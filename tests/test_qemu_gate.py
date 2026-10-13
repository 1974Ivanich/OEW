"""Поведение гейта QEMU в Makefile (цель `test-qemu`).

Гейт различает четыре случая, и каждый — это отдельный код возврата:

1. инструмента нет, режим необязательный  -> ПРОПУСК, rc=0 (не FAIL);
2. инструмента нет, `QEMU_REQUIRED=1`      -> ОШИБКА, rc!=0;
3. `QEMU` пуст и `QEMU_REQUIRED=1`         -> ОШИБКА, rc!=0. Это ровно случай
   CI (`QEMU=$(command -v qemu-system-arm)` ничего не нашёл);
4. инструмент есть, но прогон не даёт `ALL PASS` -> FAIL, rc!=0.

Случай 3 — регрессия на реальный дефект: `command -v` без аргумента
завершается успехом, поэтому без проверки непустоты гейт считал инструмент
найденным и пропускал случай 3 через `$(QEMU) -M ...`, то есть падал с
`-M: command not found` вместо явной диагностики.

Проверки намеренно опираются на код возврата и на ASCII-маркеры вывода:
сообщения гейта содержат кириллицу, а её кодировка зависит от консоли.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MAKE = shutil.which("make")
ARM_GCC = shutil.which("arm-none-eabi-gcc")

pytestmark = [
    pytest.mark.skipif(MAKE is None, reason="make не найден в PATH"),
    pytest.mark.skipif(
        ARM_GCC is None,
        reason="нет arm-none-eabi-gcc: цель test-qemu не соберёт предпосылки",
    ),
]

# Заведомо несуществующий инструмент: так результат не зависит от того,
# установлен ли qemu-system-arm на машине прогона.
NO_SUCH_TOOL = "qemu-definitely-not-installed-xyz"


def run_gate(qemu: str, required: str) -> subprocess.CompletedProcess:
    """Выполнить `make test-qemu` с явно заданным QEMU и QEMU_REQUIRED.

    Обе переменные задаются всегда: иначе значение, пришедшее из MAKEFLAGS
    родительского make, изменило бы смысл сценария. MAKEFLAGS из окружения
    снимается по той же причине.
    """
    env = dict(os.environ)
    env.pop("MAKEFLAGS", None)
    env.pop("MFLAGS", None)
    return subprocess.run(
        [MAKE, "test-qemu", f"QEMU={qemu}", f"QEMU_REQUIRED={required}"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=300,
    )


def output(res: subprocess.CompletedProcess) -> str:
    return res.stdout + res.stderr


def test_missing_tool_is_skip_not_failure() -> None:
    res = run_gate(NO_SUCH_TOOL, "0")
    assert res.returncode == 0, output(res)
    # Гейт сработал сам, а не отдал управление отсутствующей команде.
    assert "command not found" not in output(res), output(res)


def test_missing_tool_in_required_mode_is_error() -> None:
    res = run_gate(NO_SUCH_TOOL, "1")
    assert res.returncode != 0, output(res)
    assert "command not found" not in output(res), output(res)


def test_empty_qemu_in_required_mode_is_error() -> None:
    """Регрессия: пустой QEMU — точная форма CI-вызова."""
    res = run_gate("", "1")
    assert res.returncode != 0, output(res)
    # Без проверки непустоты здесь было `-M: command not found`.
    assert "command not found" not in output(res), output(res)


def test_present_tool_that_does_not_pass_is_failure() -> None:
    """Инструмент есть: гейт обязан пропустить прогон и вернуть его вердикт."""
    res = run_gate("true", "0")
    assert res.returncode != 0, output(res)
    assert "ALL PASS" in output(res), output(res)
