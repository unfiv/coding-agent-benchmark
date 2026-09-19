#!/usr/bin/env python3
"""
Provider для promptfoo поверх `claude -p --output-format stream-json --verbose`.

ВАЖНО: подключается как exec:python3 run_claude_code.py, а НЕ file://
(см. docs promptfoo, custom-script, "Structured provider responses") -
Python worker pool promptfoo убивает file:// вызов на 300000ms независимо от
config.timeoutMs (проверено на 0.122.0 и 0.123.0).

Сравнение моделей делает САМ promptfoo: несколько записей в providers: с
разными label/config, все указывают на этот же скрипт (exec: одинаковый),
promptfoo сам гоняет весь матрикс и сводит его в свою табличку. Никакой
внешней оркестрации/циклов вокруг promptfoo не требуется - см. пример в
promptfooconfig.yaml.

Модель и лимиты берутся из per-provider config (argv[2], который promptfoo
передаёт скрипту как JSON - см. custom-script docs, "options"), с фолбэком
на переменные окружения для ручных смоук-тестов из терминала.

Пишет в logs/ (плоско, без подкаталогов на модель/прогон - разбивка по
модели делается на этапе отчёта, по полю metadata.label в каждой записи):
  - logs/metrics.jsonl              - машиночитаемый журнал, 1 строка на вызов;
  - logs/claude_code_execution.log  - человекочитаемая выжимка;
  - logs/transcripts/<run_id>.jsonl - КАЖДОЕ событие Claude Code verbatim,
    один файл на вызов (run_id уникален, так что разные вызовы не путаются
    даже в общем логе).

Проверено на реальном transcript.jsonl - формат событий и имена полей ниже
подтверждены, а не предположены:
  - result.total_cost_usd - агрегированная стоимость по ВСЕМ моделям сессии
  - result.usage.{input_tokens,cache_creation_input_tokens,
    cache_read_input_tokens,output_tokens} - агрегированные суммы по всей
    сессии (per-message usage у отдельных "assistant"-событий занижены -
    для итога использовать только result.usage)
  - result.modelUsage - разбивка по каждой реально вызванной модели
"""
import sys
import os
import json
import time
import uuid
import subprocess
import logging

# --- Причины завершения прогона. Держать синхронным с bench_report.py. ---
COMPLETED = "COMPLETED"              # агент сам решил, что закончил
STEP_LIMIT = "STEP_LIMIT"            # упёрлись в --max-turns
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"  # упёрлись в --max-budget-usd
PROVIDER_ERROR = "PROVIDER_ERROR"    # ненулевой exit / краш claude
HARNESS_ERROR = "HARNESS_ERROR"      # баг в самом харнессе

# Claude Code режет прогон, не доходя ровно до потолка бюджета.
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
    """Компактная человекочитаемая строка для одного события stream-json."""
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
    """Причина остановки агента. subtype 'success' и 'error_max_turns' видены
    в реальных transcript'ах; вариант для бюджета ещё не попадался, поэтому
    сначала subtype, потом численный фолбэк по turns/cost против лимитов.
    Сырой subtype всегда кладём в metadata, чтобы мэппинг можно было
    уточнить по факту, не переигрывая прогоны."""
    if not result_evt:
        return PROVIDER_ERROR if returncode != 0 else HARNESS_ERROR

    subtype = (result_evt.get("subtype") or "").lower()
    if subtype == "success":
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
    """argv[2] - JSON с provider-конфигом из promptfooconfig.yaml (options.config).
    Отсутствует при ручном запуске из терминала - тогда фолбэк на env."""
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
    # label - ключ группировки в отчёте (несколько providers с одной моделью,
    # но разными лимитами, тоже можно различить, задав его явно в config).
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
        # allowedTools убран: модель работает как чёрный ящик без ограничения
        # тулсета, плюс похоже не действует вместе с --dangerously-skip-permissions
        # (в реальном транскрипте система получила полный список тулов Claude
        # Code, включая Task/Monitor/Cron*, несмотря на явное ограничение).
        # Двойная страховка от зацикливания, независимая от модели: дёшево
        # за токен не значит дёшево по факту, если модель начнёт перебирать
        # одинаково неудачные фиксы по кругу.
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
    ]
    if os.environ.get("MCP_CONFIG_PATH"):
        cmd += ["--mcp-config", os.environ["MCP_CONFIG_PATH"]]

    log(f"Executing: {' '.join(cmd[:3])} ...")

    transcript_path = f"logs/transcripts/{run_id}.jsonl"
    final_result_evt = {}
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

    cost = final_result_evt.get("total_cost_usd", final_result_evt.get("cost_usd", 0.0)) or 0.0
    usage = final_result_evt.get("usage", {}) or {}
    fresh_input = usage.get("input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    prompt_tokens = fresh_input + cache_write + cache_read

    model_usage = final_result_evt.get("modelUsage", {}) or {}
    per_model_cost = {m: s.get("costUSD", 0.0) for m, s in model_usage.items()}

    num_turns = final_result_evt.get("num_turns")
    if harness_error:
        termination = HARNESS_ERROR
    else:
        termination = classify_termination(final_result_evt, returncode, num_turns,
                                            cost, max_turns, max_budget_usd)

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
            "harness_error": harness_error,
            "exit_code": returncode,
            "num_turns": num_turns,
            "max_turns": max_turns,
            "max_budget_usd": max_budget_usd,
            "duration_ms": final_result_evt.get("duration_ms"),
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
    """Свой machine-readable журнал - независимый от того, что покажет
    таблица promptfoo (exec:-провайдер всегда пихает весь stdout как сырую
    строку в output, structured cost/tokenUsage оттуда promptfoo не
    достаёт). Отчёт (bench_report.py) в первую очередь читает это же самое
    JSON из results.json (там оно продублировано verbatim в response.output),
    так что metrics.jsonl - это durability-копия на случай, если результат
    promptfoo потеряется, а не обязательный вход отчёта."""
    record = dict(output_data)
    record["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with open("logs/metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"Failed to append metrics.jsonl: {e}")


if __name__ == "__main__":
    # exec:-провайдер вызывает скрипт как: <script> <prompt> <options_json> <context_json>
    # (см. custom-script docs). argv[1] - промпт, argv[2] - наш per-provider
    # config (модель/лимиты), argv[3] - context (не используется).
    if len(sys.argv) > 1:
        prompt_text = sys.argv[1]
    else:
        # Ручной режим для смоук-тестов из терминала:
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
    # stdout - ТОЛЬКО этот JSON: promptfoo кладёт его целиком в response.output
    # как строку, и bench_report.py парсит его обратно оттуда. Любой лишний
    # print сюда ломает отчёт.
    print(json.dumps(result, ensure_ascii=False))
    # Выходим ВСЕГДА нулём: ненулевой код заставит promptfoo пометить прогон
    # как provider error и НЕ выполнить ассерт verify.py - потеряем вердикт.
    sys.exit(0)