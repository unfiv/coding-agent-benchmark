"""
Ассерт для promptfoo (type: python) - независимая проверка фикса агента.

Почему Python, а не inline JS: promptfoo выполняет `type: javascript`
через eval() в контексте без доступа к Node builtins - `require('child_process')`
там бросает ReferenceError ДО входа в try/catch, то есть проверка не
выполняется вообще, а eval молча падает как "ошибка ассерта". Подтверждено
разбором grading_result в promptfoo.db за прогон eval-E6p-2026-09-13T08:29:20:
"Custom function threw error: require is not defined".

Функция по умолчанию, которую ищет promptfoo во внешнем .py файле - get_assert.
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