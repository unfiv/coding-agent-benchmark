#!/usr/bin/env python3
"""
Provider для promptfoo поверх `claude -p --output-format stream-json --verbose`.

ВАЖНО: подключается как file://run_claude_code.py в promptfoo (Python-провайдер),
а НЕ как exec:python3 run_claude_code.py. Причина задокументирована прямо в
доках promptfoo (custom-script, "Structured provider responses"): exec:-провайдер
ВСЕГДА берёт весь stdout как сырую строку для `output`, даже если скрипт
печатает JSON - tokenUsage/cost из него так извлечь нельзя в принципе. Python-
провайдер вызывает call_api() в процессе и берёт возвращённый dict как есть.

Пишет максимально подробный лог (аналог agent_execution.log из run_agent.py):
  - logs/claude_code_transcript.jsonl - КАЖДАЯ строка-событие от Claude Code
    verbatim, одна на строку;
  - logs/claude_code_execution.log    - человекочитаемая выжимка по каждому
    событию, плюс полная разбивка cost/usage/модели по итогам сессии.

Проверено на реальном transcript.jsonl (см. чат) - формат событий и имена
полей ниже подтверждены, а не предположены:
  - result.total_cost_usd - агрегированная стоимость по ВСЕМ моделям сессии
  - result.usage.{input_tokens,cache_creation_input_tokens,
    cache_read_input_tokens,output_tokens} - ЭТО агрегированные суммы по всей
    сессии (проверено построчным сложением промежуточных usage-блоков);
    per-message usage-поля у отдельных "assistant"-событий занижены/не
    репрезентативны - для итоговых цифр использовать только result.usage.
  - result.modelUsage - разбивка по каждой реально вызванной модели
    (Claude Code может дёргать доп. модель, например haiku, для служебных
    подзадач - это не баг парсинга, а реальное поведение).
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


def summarize_event(evt):
    """Компактная человекочитаемая строка для одного события stream-json.
    Ветки assistant/user (tool_use, tool_result) и result подтверждены на
    реальном transcript.jsonl."""
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


def run_claude(prompt_text):
    """Общая логика: запуск claude, потоковое логирование, извлечение
    итоговых метрик. Используется и call_api() (реальный путь через
    promptfoo), и __main__ (ручные смоук-тесты)."""
    log("=== Starting Claude Code headless run (stream-json) ===")

    # Модель переключается через CLAUDE_MODEL в .env, без пересборки образа.
    # По умолчанию - Sonnet 5 (заметно дешевле Opus, но всё ещё способен
    # реально чинить код, а не только гонять тулы). Для чистой отладки
    # пайплайна (без ставки на качество фикса) поставьте
    # claude-haiku-4-5-20251001 - в 5 раз дешевле Opus по всем категориям
    # (вход/выход/кэш-запись/кэш-чтение) равномерно. Для финального
    # "боевого" сравнения моделей - claude-opus-5.
    model_name = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    log(f"Using model: {model_name}")

    cmd = [
        "claude", "-p", prompt_text,
        "--model", model_name,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--allowedTools", "Read,Edit,Bash",
        # Двойная страховка от зацикливания - независимая от того, какая
        # модель выбрана. Дёшево за токен не значит дёшево по факту, если
        # модель начнёт перебирать одинаково неудачные фиксы по кругу.
        # --max-turns: жёсткий потолок на число ходов агента.
        # --max-budget-usd: жёсткий $ потолок именно на ЭТОТ прогон (это
        # ОЦЕНКА Claude Code по своим токенам, а не серверная проверка
        # биллинга Anthropic - лимит workspace в Console остаётся главным,
        # это лишь дополнительный, более быстрый предохранитель).
        "--max-turns", os.environ.get("CLAUDE_MAX_TURNS", "40"),
        "--max-budget-usd", os.environ.get("CLAUDE_MAX_BUDGET_USD", "2.00"),
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
                log(f"[unparsed line] {line[:200]}")
                continue
            log(summarize_event(evt))
            if evt.get("type") == "result":
                final_result_evt = evt
        stderr_tail = proc.stderr.read()
        returncode = proc.wait()

    log(f"Claude Code exit code: {returncode}")
    if stderr_tail:
        log(f"stderr tail:\n{stderr_tail[-1000:]}")

    cost = final_result_evt.get(
        "total_cost_usd", final_result_evt.get("cost_usd", 0.0)
    )

    usage = final_result_evt.get("usage", {}) or {}
    fresh_input = usage.get("input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    prompt_tokens = fresh_input + cache_write + cache_read

    model_usage = final_result_evt.get("modelUsage", {}) or {}
    per_model_cost = {
        model: stats.get("costUSD", 0.0) for model, stats in model_usage.items()
    }

    output_data = {
        "output": final_result_evt.get("result", ""),
        "tokenUsage": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        },
        "cost": round(cost, 6),
        "metadata": {
            "num_turns": final_result_evt.get("num_turns"),
            "duration_ms": final_result_evt.get("duration_ms"),
            "duration_api_ms": final_result_evt.get("duration_api_ms"),
            "is_error": final_result_evt.get("is_error"),
            "terminal_reason": final_result_evt.get("terminal_reason"),
            "permission_denials": final_result_evt.get("permission_denials"),
            "cache_write_tokens": cache_write,
            "cache_read_tokens": cache_read,
            "fresh_input_tokens": fresh_input,
            "per_model_cost_usd": per_model_cost,
        },
    }

    log(
        f"=== Execution Finished. Cost: ${output_data['cost']:.6f} "
        f"(models: {per_model_cost}), num_turns={final_result_evt.get('num_turns')}, "
        f"duration_ms={final_result_evt.get('duration_ms')}, "
        f"tokens prompt={prompt_tokens} (fresh={fresh_input}, "
        f"cache_write={cache_write}, cache_read={cache_read}) "
        f"completion={completion_tokens} ==="
    )
    return output_data


def call_api(prompt, options, context):
    """Точка входа для promptfoo Python file://-провайдера. СЕЙЧАС НЕ
    ИСПОЛЬЗУЕТСЯ (providers: указывает на exec:python3 run_claude_code.py) -
    Python worker pool promptfoo убивает вызов на 300000ms независимо от
    config.timeoutMs и версии (проверено на 0.122.0 и 0.123.0 дешёвым
    sleep-тестом, без единого обращения к Claude). Оставлено на случай,
    если promptfoo в будущем это починит - тогда достаточно вернуть
    providers обратно на file://run_claude_code.py."""
    return run_claude(prompt)


def _append_metrics_jsonl(output_data):
    """Свой собственный machine-readable журнал метрик - независимый от
    того, что покажет таблица promptfoo (exec:-провайдер всегда пихает
    весь stdout как сырую строку в output, structured cost/tokenUsage
    оттуда не достаёт в принципе - см. обсуждение в чате)."""
    import time as _time
    record = dict(output_data)
    record["timestamp"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open("logs/metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log(f"Failed to append metrics.jsonl: {e}")


if __name__ == "__main__":
    # exec:-провайдер promptfoo вызывает скрипт с промптом как ОТДЕЛЬНЫМ
    # позиционным аргументом argv[1] (см. docs: "Your script receives
    # three arguments: 1. prompt, 2. options (JSON), 3. context (JSON)").
    # ВАЖНО: раньше тут было " ".join(sys.argv[1:]) - это склеило бы
    # промпт с JSON'ами options/context из argv[2]/argv[3] и испортило
    # бы его. argv[1] нужно брать как есть, без join.
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

    result = run_claude(prompt_text)
    _append_metrics_jsonl(result)
    print(json.dumps(result))
    sys.exit(0)