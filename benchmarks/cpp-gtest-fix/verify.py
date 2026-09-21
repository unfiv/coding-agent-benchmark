"""
Promptfoo assertion (type: python) - independent verification of the agent's fix.

Why Python and not inline JS: promptfoo runs `type: javascript` through
eval() in a context without access to Node builtins - `require('child_process')`
there throws a ReferenceError BEFORE entering try/catch, so the check is not
executed at all, and eval silently fails as an "assertion error". Confirmed by
inspecting grading_result in promptfoo.db for the run eval-E6p-2026-09-13T08:29:20:
"Custom function threw error: require is not defined".

The default function promptfoo looks for in an external .py file is get_assert.
"""
import subprocess

CWD = "workspace/googletest"


def _run(cmd):
    return subprocess.run(
        cmd, shell=True, check=True, capture_output=True, text=True, encoding="utf-8"
    )


def get_assert(output, context):
    try:
        _run(f"cmake --build {CWD}/build")

        _run(
            f"g++ -std=c++17 -I {CWD}/googletest/include -I {CWD}/googletest "
            f"checks/overflow_regression_test.cc "
            f"-L {CWD}/build/lib -lgtest -lgtest_main -lpthread "
            f"-o /tmp/overflow_regression_test"
        )
        _run("/tmp/overflow_regression_test")

        _run(f"ctest --test-dir {CWD}/build --output-on-failure")

        return {
            "pass": True,
            "score": 1.0,
            "reason": "build OK, overflow_regression_test 4/4, full ctest suite passed",
        }
    except subprocess.CalledProcessError as e:
        tail = ((e.stdout or "") + (e.stderr or "")).strip().split("\n")
        tail = "\n".join(tail[-30:])
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"CHECK FAILED (command exit {e.returncode}):\n{tail}",
        }
    finally:
        try:
            subprocess.run(
                f"git -C {CWD} checkout . && git -C {CWD} clean -fd -e build",
                shell=True,
                capture_output=True,
            )
        except Exception:
            pass