#!/usr/bin/env python3
"""
Итоговая точка эксперимента: сводка в консоль + logs/summary.{json,md}.

Зачем отдельным шагом: exec:-провайдер promptfoo запускается новым процессом
на каждый тест-кейс, поэтому агрегирующая сводка обязана идти ПОСЛЕ
`promptfoo eval`. Сравнение моделей делает сам promptfoo (несколько записей
в providers: с разными label/config в promptfooconfig.yaml) - этот скрипт
просто читает его результат и добавляет то, что exec:-провайдер физически
не может отдать promptfoo структурно: cost/tokens/причину остановки.

Единственный вход - JSON, который `promptfoo eval -o <path>` уже написал.
Каждая строка результата содержит response.output - это ровно тот JSON,
который наш run_claude_code.py напечатал в stdout, включая metadata.label
для группировки. Отдельный metrics.jsonl НЕ читается - он лишь durability-
копия на диске, отчёту не нужна, пока results.json на месте.

Использование:
    python3 bench_report.py                      # logs/results.json
    python3 bench_report.py logs/results.json
    python3 bench_report.py logs/results.json --baseline claude-sonnet-5
"""
import argparse
import json
import os
import sys

COMPLETED = "COMPLETED"
STEP_LIMIT = "STEP_LIMIT"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
PROVIDER_ERROR = "PROVIDER_ERROR"
HARNESS_ERROR = "HARNESS_ERROR"
TASK_FAILED = "TASK_FAILED"  # агент закончил сам, но verify.py не прошёл

LIMIT_REASONS = (STEP_LIMIT, BUDGET_EXCEEDED)
ERROR_REASONS = (PROVIDER_ERROR, HARNESS_ERROR)

EXIT_OK, EXIT_TASK_FAILED, EXIT_BUDGET, EXIT_STEPS, EXIT_PROVIDER, EXIT_HARNESS = range(6)

# Выше этой доли незавершённых прогонов сравнение моделей недостоверно.
INCOMPLETE_UNRELIABLE = 0.20
W = 72


def c(code, s):
    return s if os.environ.get("NO_COLOR") or not sys.stdout.isatty() else f"\033[{code}m{s}\033[0m"


RED = lambda s: c("31;1", s)
GREEN = lambda s: c("32;1", s)
YELLOW = lambda s: c("33;1", s)
DIM = lambda s: c("2", s)


def load_rows(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    node = data.get("results", data)
    if isinstance(node, dict):
        node = node.get("results", [])
    if not isinstance(node, list):
        raise ValueError(f"неожиданная форма results.json: {type(node)}")

    rows = []
    for pf in node:
        resp = pf.get("response") or {}
        raw = resp.get("output")
        payload = {}
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
        meta = payload.get("metadata") or {}
        grading = pf.get("gradingResult") or {}
        passed = grading.get("pass")
        if passed is None:
            passed = pf.get("success")
        reason = grading.get("reason") or pf.get("error") or ""

        if not meta:
            # response.output не распарсился как наш JSON - провайдер упал
            # раньше, чем успел его напечатать. Это харнесс-ошибка, а не
            # проваленная задача.
            termination = HARNESS_ERROR
            label = "unknown"
        else:
            termination = meta.get("termination") or HARNESS_ERROR
            label = meta.get("label") or meta.get("model") or "unknown"

        rows.append({
            "label": label,
            "solved": bool(passed),
            "verdict_reason": str(reason),
            "termination": termination,
            "cost": payload.get("cost") or 0.0,
            "tokens_total": (payload.get("tokenUsage") or {}).get("total") or 0,
            "num_turns": meta.get("num_turns"),
            "max_turns": meta.get("max_turns"),
            "max_budget_usd": meta.get("max_budget_usd"),
            "wall_ms": meta.get("wall_ms") or 0,
            "episodes": meta.get("episodes") or 1,
            "transcript": meta.get("transcript"),
        })
    return rows


def aggregate(rows):
    n = len(rows)
    solved = [r for r in rows if r["solved"]]
    limit_hit = [r for r in rows if r["termination"] in LIMIT_REASONS]
    errors = [r for r in rows if r["termination"] in ERROR_REASONS]
    unsolved = [r for r in rows if not r["solved"]]

    by_reason = {}
    for r in unsolved:
        reason = TASK_FAILED if r["termination"] == COMPLETED else r["termination"]
        by_reason[reason] = by_reason.get(reason, 0) + 1

    turns = [r["num_turns"] for r in rows if isinstance(r["num_turns"], (int, float))]
    wall_ms = [r["wall_ms"] for r in rows if isinstance(r["wall_ms"], (int, float)) and r["wall_ms"] > 0]
    multi_episode = sum(1 for r in rows if (r.get("episodes") or 1) > 1)
    cost_total = sum(r["cost"] for r in rows)
    cost_solved = sum(r["cost"] for r in solved)
    tokens_solved = sum(r["tokens_total"] for r in solved)

    return {
        "n": n,
        "solved": len(solved),
        "solve_rate": len(solved) / n if n else 0.0,
        "limit_hit": len(limit_hit),
        "errors": len(errors),
        "unsolved_by_reason": by_reason,
        "cost_total": cost_total,
        "cost_per_solved": (cost_solved / len(solved)) if solved else None,
        "tokens_per_solved": (tokens_solved / len(solved)) if solved else None,
        "turns_avg": (sum(turns) / len(turns)) if turns else 0.0,
        "turns_max": max(turns) if turns else 0,
        "wall_avg_ms": (sum(wall_ms) / len(wall_ms)) if wall_ms else 0,
        "wall_max_ms": max(wall_ms) if wall_ms else 0,
        "multi_episode": multi_episode,
        "max_turns": rows[0]["max_turns"] if rows else None,
        "max_budget_usd": rows[0]["max_budget_usd"] if rows else None,
        "reliable": (len(limit_hit) / n if n else 0) <= INCOMPLETE_UNRELIABLE,
    }


def exit_code(rows, min_solve_rate, solve_rate):
    if not rows:
        return EXIT_HARNESS
    reasons = {r["termination"] for r in rows}
    if HARNESS_ERROR in reasons:
        return EXIT_HARNESS
    if PROVIDER_ERROR in reasons:
        return EXIT_PROVIDER
    if BUDGET_EXCEEDED in reasons:
        return EXIT_BUDGET
    if STEP_LIMIT in reasons:
        return EXIT_STEPS
    if solve_rate < min_solve_rate:
        return EXIT_TASK_FAILED
    return EXIT_OK


def fmt_tokens(t):
    return "0" if not t else (f"{t/1000:.1f}k" if t < 1_000_000 else f"{t/1_000_000:.2f}M")


def fmt_ms(ms):
    s = (ms or 0) / 1000
    return f"{int(s//60)}m {s%60:.0f}s" if s >= 60 else f"{s:.1f}s"


def print_model_block(label, rows, agg):
    print("═" * W)
    print(f" MODEL  {label}   ·  runs={agg['n']}")
    print("─" * W)
    if agg["n"] == 0:
        print(RED(" STATUS   NO DATA"))
        print("═" * W)
        return

    limit_reasons = [k for k in (BUDGET_EXCEEDED, STEP_LIMIT) if agg["unsolved_by_reason"].get(k)]
    if agg["errors"]:
        bad = sorted({r["termination"] for r in rows if r["termination"] in ERROR_REASONS})
        status = RED("FAILED — " + ", ".join(bad))
    elif limit_reasons:
        status = YELLOW("INCOMPLETE — " + ", ".join(limit_reasons))
    elif agg["solved"] == agg["n"]:
        status = GREEN("OK — all tasks solved")
    else:
        status = YELLOW("COMPLETE — but not all solved")
    print(f" STATUS   {status}")
    print("─" * W)

    print(f" solved      {agg['solved']}/{agg['n']}   ({agg['solve_rate']*100:.1f}%)")
    if agg["unsolved_by_reason"]:
        detail = ", ".join(f"{k.lower()} {v}" for k, v in sorted(agg["unsolved_by_reason"].items()))
        print(f" unsolved    {agg['n'] - agg['solved']}/{agg['n']}   ← {detail}")

    max_turns = agg['max_turns'] if agg['max_turns'] is not None else "n/a"
    turns_line = f" turns       avg {agg['turns_avg']:.1f}   max {agg['turns_max']}   limit {max_turns}"
    if agg["unsolved_by_reason"].get(STEP_LIMIT):
        turns_line += "   " + YELLOW("← hit")
    print(turns_line)

    if agg["wall_avg_ms"]:
        wall_line = f" wall        avg {fmt_ms(agg['wall_avg_ms'])}   max {fmt_ms(agg['wall_max_ms'])}"
        if agg["multi_episode"]:
            # Claude Code может уводить долгую bash-команду в background и
            # "просыпаться" по уведомлению - несколько type:result в одном
            # вызове. num_turns/wall тут суммированы по всем эпизодам, но
            # если что-то в отчёте не сходится - смотреть transcript целиком,
            # а не последний result внутри него.
            wall_line += "   " + DIM(f"({agg['multi_episode']}/{agg['n']} runs had background wait/wakeup)")
        print(wall_line)

    budget = f"${agg['max_budget_usd']}" if agg['max_budget_usd'] is not None else "n/a"
    cost_line = f" cost        ${agg['cost_total']:.4f} total   budget {budget}/run"
    if agg["unsolved_by_reason"].get(BUDGET_EXCEEDED):
        cost_line += "   " + YELLOW("← hit")
    print(cost_line)

    if agg["cost_per_solved"] is not None:
        print(f" per solved  ${agg['cost_per_solved']:.4f}   {fmt_tokens(agg['tokens_per_solved'])} tokens")
    else:
        print(" per solved  " + DIM("n/a — ни одной решённой задачи"))
    print("═" * W)


def print_failures(rows):
    bad = [r for r in rows if not r["solved"]]
    if not bad:
        return
    print()
    print(" НЕ РЕШЕНО:")
    for r in bad:
        reason = TASK_FAILED if r["termination"] == COMPLETED else r["termination"]
        head = f"  · [{r['label']}] {reason}"
        if r["termination"] == STEP_LIMIT:
            head += f" (turns {r['num_turns']}/{r['max_turns']})"
        elif r["termination"] == BUDGET_EXCEEDED:
            head += f" (${r['cost']:.4f}/${r['max_budget_usd']})"
        print(head)
        for line in [l for l in (r["verdict_reason"] or "").splitlines() if l.strip()][:3]:
            print(DIM(f"      {line[:100]}"))
        if r["transcript"]:
            print(DIM(f"      {r['transcript']}"))


def print_comparison(per_label, baseline):
    labels = list(per_label)
    if len(labels) < 2:
        return
    if baseline not in per_label:
        baseline = labels[0]

    print()
    print("═" * W)
    print(" СРАВНЕНИЕ ПРОВАЙДЕРОВ")
    print("─" * W)
    print(f" {'provider':<22}{'solved':>8}{'limit':>7}{'$/solved':>11}{'tok/solved':>12}{'Δ':>6}")
    print("─" * W)

    base = per_label[baseline]
    order = sorted(labels, key=lambda m: (-per_label[m]["solve_rate"],
                                          per_label[m]["cost_per_solved"] or 9e9))
    for m in order:
        a = per_label[m]
        name = m + (" *" if m == baseline else "")
        cps = f"${a['cost_per_solved']:.4f}" if a["cost_per_solved"] is not None else "—"
        tps = fmt_tokens(a["tokens_per_solved"]) if a["tokens_per_solved"] else "—"
        solved = f"{a['solved']}/{a['n']}"
        line = f" {name:<22}{solved:>8}{a['limit_hit']:>7}{cps:>11}{tps:>12}"
        if m != baseline:
            d = a["solved"] - base["solved"]
            line += f"{d:+d}".rjust(6) if d else "     ="
        else:
            line += "      "
        if not a["reliable"]:
            line += YELLOW("  ← ненадёжно")
        print(line)
    print("─" * W)
    print(DIM(" * baseline. $/solved и tok/solved нормированы на решённые задачи -"))
    print(DIM(" иначе модель, которая рано сдаётся, выглядела бы самой дешёвой."))
    if any(a["n"] < 5 for a in per_label.values()):
        print(YELLOW(" n < 5, репитов нет — разброс между прогонами перекрывает разницу"))
        print(YELLOW(" между моделями. Это анекдот, не замер."))
    print("═" * W)


def render_markdown(rows, per_label, code):
    out = [f"# Benchmark run", "", f"Exit code: `{code}`.", "",
           "| provider | solved | limit hit | $/solved | tok/solved |",
           "|---|---|---|---|---|"]
    for m, a in per_label.items():
        cps = f"${a['cost_per_solved']:.4f}" if a["cost_per_solved"] is not None else "—"
        tps = fmt_tokens(a["tokens_per_solved"]) if a["tokens_per_solved"] else "—"
        out.append(f"| {m} | {a['solved']}/{a['n']} | {a['limit_hit']} | {cps} | {tps} |")
    bad = [r for r in rows if not r["solved"]]
    if bad:
        out += ["", "## Не решено", ""]
        for r in bad:
            reason = TASK_FAILED if r["termination"] == COMPLETED else r["termination"]
            out.append(f"- **{r['label']}** — {reason}, turns {r['num_turns']}/{r['max_turns']}, ${r['cost']:.4f}")
            tail = [l for l in (r["verdict_reason"] or "").splitlines() if l.strip()][:5]
            if tail:
                out += ["", "  ```"] + [f"  {l[:200]}" for l in tail] + ["  ```"]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_path", nargs="?", default="logs/results.json")
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--min-solve-rate", type=float, default=1.0)
    args = ap.parse_args()

    if not os.path.exists(args.results_path):
        print(RED(f"Нет файла: {args.results_path}"), file=sys.stderr)
        return EXIT_HARNESS

    rows = load_rows(args.results_path)
    if not rows:
        print(RED("promptfoo не вернул ни одного результата"), file=sys.stderr)
        return EXIT_HARNESS

    print()
    per_label = {}
    for label in sorted({r["label"] for r in rows}):
        label_rows = [r for r in rows if r["label"] == label]
        agg = aggregate(label_rows)
        per_label[label] = agg
        print_model_block(label, label_rows, agg)
        print_failures(label_rows)

    print_comparison(per_label, args.baseline or next(iter(per_label)))

    solved = sum(1 for r in rows if r["solved"])
    solve_rate = solved / len(rows)
    code = exit_code(rows, args.min_solve_rate, solve_rate)

    summary = {"totals": {"n": len(rows), "solved": solved, "solve_rate": solve_rate},
               "models": per_label, "exit_code": code}
    out_dir = os.path.dirname(args.results_path) or "."
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(rows, per_label, code))

    print()
    print(f" artifacts  {out_dir}/summary.json, {out_dir}/summary.md")
    print(f" exit code  {code}")
    print()
    return code


if __name__ == "__main__":
    sys.exit(main())