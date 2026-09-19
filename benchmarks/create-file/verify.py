"""
Ассерт для promptfoo (type: python) - смоук-тест харнесса.

Задача тривиальна: агент должен создать workspace/hello.txt с одной строкой
OK-42. Никакой сборки: проверка - прямое чтение файла.

Файл ВСЕГДА удаляется в finally: при repeat: 10 прогоны идут подряд в одной
директории, и без очистки следующий прогон мог бы "пройти" на артефакте
предыдущего, даже если агент ничего не сделал.

Функция по умолчанию, которую ищет promptfoo во внешнем .py файле - get_assert.
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