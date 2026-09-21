"""
Promptfoo assertion (type: python) - smoke test of the harness.

The task is trivial: the agent must create workspace/hello.txt with a single
line, OK-42. No build: the check is a direct read of the file.

The file is ALWAYS deleted in finally: with repeat: 10 the runs go back to back in
the same directory, and without cleanup the next run could "pass" on the
previous run's artifact even if the agent did nothing.

The default function promptfoo looks for in an external .py file is get_assert.
"""
from pathlib import Path

TARGET = Path("workspace/hello.txt")
EXPECTED = "OK-42"


def get_assert(output, context):
    try:
        if not TARGET.exists():
            return {"pass": False, "score": 0.0, "reason": f"file not created: {TARGET}"}
        content = TARGET.read_text(encoding="utf-8").strip()
        if content != EXPECTED:
            return {
                "pass": False,
                "score": 0.0,
                "reason": f"wrong content: expected {EXPECTED!r}, got {content[:200]!r}",
            }
        return {"pass": True, "score": 1.0, "reason": f"{TARGET} == {EXPECTED!r}"}
    finally:
        try:
            TARGET.unlink(missing_ok=True)
        except Exception:
            pass