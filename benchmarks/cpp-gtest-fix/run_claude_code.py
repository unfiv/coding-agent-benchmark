#!/usr/bin/env python3
"""
Provider for promptfoo on top of `claude -p --output-format stream-json --verbose`.

IMPORTANT: hooked up as exec:python3 run_claude_code.py, NOT file://
(see promptfoo docs, custom-script, "Structured provider responses") -
promptfoo's Python worker pool kills a file:// call at 300000ms regardless
of config.timeoutMs (confirmed on 0.122.0 and 0.123.0).

promptfoo ITSELF compares the models: multiple entries under providers: with
different label/config, all pointing at this same script (exec: identical),
and promptfoo runs the whole matrix and folds it into its own table. No
external orchestration/loops around promptfoo are needed - see the example
in promptfooconfig.yaml.

Model and limits come from the per-provider config (argv[2], which promptfoo
passes to the script as JSON - see custom-script docs, "options"), with a
fallback to environment variables for manual smoke tests from the terminal.

Writes to logs/ (flat, no per-model/per-run subdirectories - the breakdown
by model happens at the report stage, via the metadata.label field in each
record):
  - logs/metrics.jsonl              - machine-readable log, 1 line per call;
  - logs/claude_code_execution.log  - human-readable digest;
  - logs/transcripts/<run_id>.jsonl - EVERY Claude Code event verbatim,
    one file per call (run_id is unique, so different calls don't get mixed
    up even in the shared log).

Verified against a real transcript.jsonl - the event format and field names
below are confirmed, not assumed:
  - result.total_cost_usd - aggregated cost across ALL models in the session
  - result.usage.{input_tokens,cache_creation_input_tokens,
    cache_read_input_tokens,output_tokens} - aggregated totals for the whole
    session (per-message usage on individual "assistant" events is
    understated - use only result.usage for the total)
  - result.modelUsage - breakdown per model actually invoked
"""
import sys
import os
import json
import time
import uuid
import subprocess
import logging

# --- Run termination reasons. Keep in sync with bench_report.py. ---
COMPLETED = "COMPLETED"              # agent decided by itself that it's done
STEP_LIMIT = "STEP_LIMIT"            # hit --max-turns
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"  # hit --max-budget-usd
PROVIDER_ERROR = "PROVIDER_ERROR"    # non-zero exit / claude crash
HARNESS_ERROR = "HARNESS_ERROR"      # bug in the harness itself

# Claude Code cuts off the run slightly before hitting the exact budget ceiling.
BUDGET_HIT_RATIO = 0.97

os.makedirs("logs/transcripts", exist_ok=True)
logging.basicConfig(
    filename="logs/claude_code_execution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def log(msg):
    sys.stderr.write(f"{msg}\n")
    logging.info(msg)


def summarize_event(evt):
    """Compact, human-readable line for a single stream-json event."""
    etype = evt.get("type", "?")
    subtype = evt.get("subtype")

    if etype == "assistant":
        msg = evt.get("message", {})
        parts = []
        for block in msg.get("content", []) if isinstance(msg, dict) else []:
            if block.get("type") == "text":
                parts.append(block["text"][:200])
            elif block.get("type") == "tool_use":
                parts.append(f"tool_use: {block.get('name')} args={json.dumps(block.get('input'))[:300]}")
        return f"[assistant] {' | '.join(parts) if parts else '(empty)'}"

    if etype == "user":
        msg = evt.get("message", {})
        parts = []
        for block in msg.get("content", []) if isinstance(msg, dict) else []:
            if block.get("type") == "tool_result":
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content)
                parts.append(f"tool_result: {text[:300]}")
        return f"[tool_result] {' | '.join(parts) if parts else '(empty)'}"

    if etype == "system" and subtype == "api_retry":
        return f"[retry] attempt={evt.get('attempt')} status={evt.get('error_status')} reason={evt.get('error')}"

    if etype == "result":
        return f"[result] subtype={subtype} is_error={evt.get('is_error')} num_turns={evt.get('num_turns')}"

    return f"[{etype}/{subtype}] {json.dumps(evt)[:200]}"


def classify_termination(result_evt, returncode, num_turns, cost, max_turns, max_budget_usd):
    """Reason the agent stopped. terminal_reason is a field on Claude Code's
    result event confirmed by real data (value "max_turns" observed in
    production) and is more authoritative than subtype - checked first.
    subtype and the numeric fallback cover the case where terminal_reason
    is absent."""
    if not result_evt:
        return PROVIDER_ERROR if returncode != 0 else HARNESS_ERROR

    reason = (result_evt.get("terminal_reason") or "").lower()
    if "max_turns" in reason or "turn" in reason:
        return STEP_LIMIT
    if "budget" in reason or "cost" in reason:
        return BUDGET_EXCEEDED

    subtype = (result_evt.get("subtype") or "").lower()
    if subtype == "success" and not result_evt.get("is_error"):
        return COMPLETED
    if "budget" in subtype or "cost" in subtype:
        return BUDGET_EXCEEDED
    if "max_turns" in subtype or "turn" in subtype:
        return STEP_LIMIT

    if max_budget_usd and cost >= max_budget_usd * BUDGET_HIT_RATIO:
        return BUDGET_EXCEEDED
    if num_turns and max_turns and num_turns >= max_turns:
        return STEP_LIMIT
    if result_evt.get("is_error") or returncode != 0:
        return PROVIDER_ERROR
    return COMPLETED


def _parse_options(argv):
    """argv[2] - JSON with the provider config from promptfooconfig.yaml
    (options.config). Absent on manual runs from the terminal - falls back
    to env in that case."""
    if len(argv) > 2 and argv[2]:
        try:
            return json.loads(argv[2]).get("config") or {}
        except json.JSONDecodeError:
            log(f"[warn] argv[2] не распарсился как JSON: {argv[2][:200]}")
    return {}


def run_claude(prompt_text, cfg):
    model = cfg.get("model") or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    max_turns = int(cfg.get("maxTurns") or os.environ.get("CLAUDE_MAX_TURNS", "40"))
    max_budget_usd = float(cfg.get("maxBudgetUsd") or os.environ.get("CLAUDE_MAX_BUDGET_USD", "2.00"))
    # label - the grouping key in the report (multiple providers with the
    # same model but different limits can also be told apart by setting
    # this explicitly in config).
    label = cfg.get("label") or model

    run_id = time.strftime("%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    started = time.time()
    log(f"=== Starting Claude Code headless run run_id={run_id} label={label} ===")
    log(f"Using model: {model} (max_turns={max_turns}, max_budget=${max_budget_usd})")

    cmd = [
        "claude", "-p", prompt_text,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        # allowedTools removed: the model runs as a black box with no
        # toolset restriction, and it seems to have no effect together with
        # --dangerously-skip-permissions anyway (the real transcript showed
        # the system got the full Claude Code tool list, including
        # Task/Monitor/Cron*, despite the explicit restriction).
        # Model-independent double safeguard against looping: cheap per
        # token doesn't mean cheap overall if the model starts cycling
        # through equally unsuccessful fixes.
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
    ]
    if os.environ.get("MCP_CONFIG_PATH"):
        cmd += ["--mcp-config", os.environ["MCP_CONFIG_PATH"]]

    log(f"Executing: {' '.join(cmd[:3])} ...")

    transcript_path = f"logs/transcripts/{run_id}.jsonl"
    final_result_evt = {}
    result_events = []  # ALL type:result events for this call - see below
    warned_budget = False
    returncode = -1
    harness_error = None
    stderr_tail = ""

    try:
        with open(transcript_path, "w", encoding="utf-8") as transcript, \
             subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1) as proc:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                transcript.write(line + "\n")
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    log(f"[unparsed line] {line[:200]}")
                    continue
                log(summarize_event(evt))

                if not warned_budget and evt.get("type") == "assistant":
                    spent = evt.get("total_cost_usd") or evt.get("cost_usd")
                    if isinstance(spent, (int, float)) and max_budget_usd and spent >= max_budget_usd * 0.8:
                        warned_budget = True
                        log(f"[WARN] budget 80% reached: ${spent:.4f} of ${max_budget_usd:.2f}")

                if evt.get("type") == "result":
                    # Claude Code can push a long bash command to the
                    # background (Task/Monitor/ScheduleWakeup from the tool
                    # list) and "wake up" on a notification - that's ONE
                    # session (shared session_id), but MULTIPLE type:result
                    # events in the stream (confirmed on a real transcript:
                    # 4 result events, shared session_id, num_turns of each
                    # being 47/4/5/1 - those are turns of THEIR OWN episode,
                    # not a running total). total_cost_usd and usage are
                    # ALREADY cumulative on their own (each subsequent event
                    # carries the running total) - for those, the last event
                    # is the correct grand total. So here we only accumulate
                    # what is NOT self-cumulative: num_turns and duration_ms.
                    result_events.append(evt)
                    final_result_evt = evt
            stderr_tail = proc.stderr.read()
            returncode = proc.wait()
    except FileNotFoundError as e:
        harness_error = f"claude CLI not found: {e}"
        log(f"[ERROR] {harness_error}")
    except Exception as e:  # noqa: BLE001 - харнесс не должен падать молча
        harness_error = f"{type(e).__name__}: {e}"
        log(f"[ERROR] harness failure: {harness_error}")

    log(f"Claude Code exit code: {returncode}")
    if stderr_tail:
        log(f"stderr tail:\n{stderr_tail[-1000:]}")

    episodes = len(result_events)
    # Sum of turns/active time across all episodes - otherwise, for a
    # session that fell asleep waiting on a background build and woke up
    # with a short "all done" episode, the report would show 1 turn instead
    # of the real dozens.
    num_turns = sum((e.get("num_turns") or 0) for e in result_events) or None
    active_duration_ms = sum((e.get("duration_ms") or 0) for e in result_events) or None

    cost = final_result_evt.get("total_cost_usd", final_result_evt.get("cost_usd", 0.0)) or 0.0
    usage = final_result_evt.get("usage", {}) or {}
    fresh_input = usage.get("input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    prompt_tokens = fresh_input + cache_write + cache_read

    model_usage = final_result_evt.get("modelUsage", {}) or {}
    per_model_cost = {m: s.get("costUSD", 0.0) for m, s in model_usage.items()}

    if harness_error:
        termination = HARNESS_ERROR
    else:
        termination = classify_termination(final_result_evt, returncode, num_turns,
                                            cost, max_turns, max_budget_usd)

    if episodes > 1:
        log(f"[INFO] multi-episode session: {episodes} episodes "
            f"(background wait/wakeup), num_turns summed to {num_turns}")

    if termination == STEP_LIMIT:
        log(f"[WARN] STEP LIMIT HIT: turns {num_turns}/{max_turns}")
    elif termination == BUDGET_EXCEEDED:
        log(f"[WARN] BUDGET EXCEEDED: ${cost:.4f}/${max_budget_usd:.2f}")
    elif termination in (PROVIDER_ERROR, HARNESS_ERROR):
        log(f"[ERROR] {termination}: rc={returncode} err={harness_error or stderr_tail[-200:]}")

    output_data = {
        "output": final_result_evt.get("result", ""),
        "tokenUsage": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        },
        "cost": round(cost, 6),
        "metadata": {
            "run_id": run_id,
            "label": label,
            "model": model,
            "termination": termination,
            "result_subtype": final_result_evt.get("subtype"),
            "terminal_reason": final_result_evt.get("terminal_reason"),
            "harness_error": harness_error,
            "exit_code": returncode,
            "num_turns": num_turns,
            "max_turns": max_turns,
            "max_budget_usd": max_budget_usd,
            "duration_ms": active_duration_ms,
            "episodes": episodes,
            "wall_ms": int((time.time() - started) * 1000),
            "cache_write_tokens": cache_write,
            "cache_read_tokens": cache_read,
            "fresh_input_tokens": fresh_input,
            "per_model_cost_usd": per_model_cost,
            "transcript": transcript_path,
        },
    }

    log(f"=== Execution Finished. termination={termination} cost=${output_data['cost']:.6f} "
        f"num_turns={num_turns}/{max_turns} tokens={prompt_tokens + completion_tokens} ===")
    return output_data


def _append_metrics_jsonl(output_data):
    """Our own machine-readable log - independent of whatever promptfoo's
    table shows (the exec: provider always shoves the whole stdout as a raw
    string into output, promptfoo doesn't extract structured cost/tokenUsage
    from it). The report (bench_report.py) primarily reads this same JSON
    from results.json (it's duplicated there verbatim in response.output),
    so metrics.jsonl is a durability copy in case promptfoo's result gets
    lost, not a required input for the report."""
    record = dict(output_data)
    record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with open("logs/metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"Failed to append metrics.jsonl: {e}")


if __name__ == "__main__":
    # The exec: provider invokes the script as:
    # <script> <prompt> <options_json> <context_json>
    # (see custom-script docs). argv[1] is the prompt, argv[2] is our
    # per-provider config (model/limits), argv[3] is context (unused).
    if len(sys.argv) > 1:
        prompt_text = sys.argv[1]
    else:
        # Manual mode for smoke tests from the terminal:
        #   echo 'list files' | python3 run_claude_code.py
        prompt_text = ""
        if not sys.stdin.isatty():
            raw_stdin = sys.stdin.read().strip()
            if raw_stdin:
                try:
                    data = json.loads(raw_stdin)
                    prompt_text = data.get("prompt", raw_stdin)
                except json.JSONDecodeError:
                    prompt_text = raw_stdin
        if not prompt_text:
            prompt_text = "Fix the task"

    cfg = _parse_options(sys.argv)
    result = run_claude(prompt_text, cfg)
    _append_metrics_jsonl(result)
    # stdout - ONLY this JSON: promptfoo puts it whole into response.output
    # as a string, and bench_report.py parses it back out of there. Any
    # extra print here breaks the report.
    print(json.dumps(result, ensure_ascii=False))
    # ALWAYS exit zero: a non-zero code makes promptfoo mark the run as a
    # provider error and skip running verify.py's assertion - we'd lose the
    # verdict.
    sys.exit(0)
