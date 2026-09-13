#!/usr/bin/env python3
"""
Wrapper над `claude -p --output-format stream-json --verbose` для promptfoo.

Пишет максимально подробный лог (аналог agent_execution.log из run_agent.py):
  - logs/claude_code_transcript.jsonl - КАЖДАЯ событие-строка от Claude Code
    verbatim, одна на строку, ничего не теряется;
  - logs/claude_code_execution.log    - человекочитаемая выжимка по каждому
    событию (какой тул вызван, с какими аргументами, что вернул) - в том же
    духе, что и лог вашего собственного ReAct-цикла.

В stdout уходит РОВНО ОДНА финальная JSON-строка {output, tokenUsage, cost} -
это то, что ожидает exec:-провайдер promptfoo. Всё остальное - в stderr/файлы,
чтобы не сломать парсинг на стороне promptfoo.
"""
import sys
import os
import json
import subprocess
import logging

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/claude_code_execution.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
TRANSCRIPT_PATH = "logs/claude_code_transcript.jsonl"


def log(msg):
    sys.stderr.write(f"{msg}\n")
    logging.info(msg)


def read_prompt():
    prompt_text = ""
    if not sys.stdin.isatty():
        raw_stdin = sys.stdin.read().strip()
        if raw_stdin:
            try:
                data = json.loads(raw_stdin)
                prompt_text = data.get("prompt", raw_stdin)
            except json.JSONDecodeError:
                prompt_text = raw_stdin
    if not prompt_text and len(sys.argv) > 1:
        prompt_text = " ".join(sys.argv[1:]).strip()
    if not prompt_text:
        prompt_text = "Fix the task"
    return prompt_text


def summarize_event(evt):
    """Компактная человекочитаемая строка для одного событие stream-json.
    Формы событий у Claude Code различаются между версиями CLI - здесь
    защитный best-effort разбор, не исчерпывающий список типов."""
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
        return f"[result] subtype={subtype} is_error={evt.get('is_error')}"

    return f"[{etype}/{subtype}] {json.dumps(evt)[:200]}"


def main():
    prompt_text = read_prompt()
    log("=== Starting Claude Code headless run (stream-json) ===")

    cmd = [
        "claude", "-p", prompt_text,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--allowedTools", "Read,Edit,Bash",
    ]
    if os.environ.get("MCP_CONFIG_PATH"):
        cmd += ["--mcp-config", os.environ["MCP_CONFIG_PATH"]]

    log(f"Executing: {' '.join(cmd[:3])} ...")

    final_result_evt = {}
    with open(TRANSCRIPT_PATH, "a", encoding="utf-8") as transcript, \
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
                log(f"[unparsed line] {line[:5000]}")
                continue
            log(summarize_event(evt))
            if evt.get("type") == "result":
                final_result_evt = evt
        stderr_tail = proc.stderr.read()
        returncode = proc.wait()

    log(f"Claude Code exit code: {returncode}")
    if stderr_tail:
        log(f"stderr tail:\n{stderr_tail[-5000:]}")

    # Поле со стоимостью переименовывали между версиями CLI (встречались и
    # cost_usd, и total_cost_usd) - проверяю оба, чтобы не тихо получить 0.
    cost = final_result_evt.get(
        "total_cost_usd", final_result_evt.get("cost_usd", 0.0)
    )
    usage = final_result_evt.get("usage", {}) or {}
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)

    output_data = {
        "output": final_result_evt.get("result", ""),
        "tokenUsage": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        },
        "cost": round(cost, 6),
    }

    log(
        f"=== Execution Finished. Cost: ${output_data['cost']:.6f}, "
        f"num_turns={final_result_evt.get('num_turns')}, "
        f"duration_ms={final_result_evt.get('duration_ms')} ==="
    )
    print(json.dumps(output_data))
    sys.exit(0)


if __name__ == "__main__":
    main()