"""Регрессия D3: частота ШИМ в identity карты (двойной учёт PSC).

Стендовый дефект 15.09.2026: `cap_pwm_frequency_hz()` брала `tclk` из
`PWM_GetSysInfo()` (там это уже частота СЧЁТЧИКА, т.е. `get_tim_ck_int() /
(PSC + 1)`) и повторно делила на `(PSC + 1)`. На стендовых числах
(PSC=16, ARR=999, 170 МГц SYSCLK -> счётчик 10 МГц) истинная частота 5000 Гц,
а формула отдавала 294 Гц. Значение уезжало в identity карты, в манифест
кампании (`BOAR_PWM_HZ`) и в evidence.

Тест держит три вещи вместе, чтобы дефект не вернулся молча:

  * арифметика: 10e6/(2*(999+1)) == 5000, а вариант с (PSC+1) == 294;
  * константы: `MAP_CAPTURE_BOARD_PWM_HZ` (firmware), `BOAR_PWM_HZ` (ingest)
    и `MAP_CAPTURE_SYNTHETIC_PWM_HZ` (host) равны 5000 и не расходятся;
  * исходник `cap_pwm_frequency_hz()` не содержит множителя `(psc + 1u)` в
    знаменателе (текстовый guard против повторного внесения двойного деления).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import map_scope_ingest as msi  # noqa: E402

PROFILES_C = _ROOT / "src" / "map_capture_profiles.c"
PORT_C = _ROOT / "src" / "map_capture_port.c"

BENCH_TCLK_HZ = 10_000_000          # 170 МГц / (PSC=16 + 1)
BENCH_ARR = 999


def _c_define(macro: str, path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"#define\s+%s\s+(\d+)u?" % re.escape(macro), text)
    assert match, "не найден #define %s в %s" % (macro, path)
    return int(match.group(1))


def _pwm_frequency_hz(tclk_hz: int, arr: int, psc: int) -> int:
    """Текущая формула прошивки: период = 2*(ARR+1) тактов счётчика."""
    del psc  # намеренно не участвует: tclk уже поделён на (PSC + 1)
    denominator = 2 * (arr + 1)
    return (tclk_hz + denominator // 2) // denominator


def _pwm_frequency_hz_with_double_psc(tclk_hz: int, arr: int, psc: int) -> int:
    """Дефектный вариант D3 (для доказательства, что он даёт 294)."""
    denominator = 2 * (psc + 1) * (arr + 1)
    return (tclk_hz + denominator // 2) // denominator


def test_arithmetic_matches_bench_numbers() -> None:
    assert _pwm_frequency_hz(BENCH_TCLK_HZ, BENCH_ARR, 16) == 5000
    assert _pwm_frequency_hz_with_double_psc(BENCH_TCLK_HZ, BENCH_ARR, 16) == 294


def test_firmware_constant_matches_true_frequency() -> None:
    assert _c_define("MAP_CAPTURE_BOARD_PWM_HZ", PROFILES_C) == 5000


def test_synthetic_constant_agrees_with_board() -> None:
    assert _c_define("MAP_CAPTURE_SYNTHETIC_PWM_HZ", PROFILES_C) == 5000


def test_ingest_constant_matches_firmware() -> None:
    assert msi.BOAR_PWM_HZ == 5000
    assert msi.BOAR_PWM_HZ == _c_define("MAP_CAPTURE_BOARD_PWM_HZ", PROFILES_C)


def test_cap_pwm_frequency_has_no_double_psc() -> None:
    body = PORT_C.read_text(encoding="utf-8", errors="replace")
    start = body.index("static uint32_t cap_pwm_frequency_hz(void)")
    end = body.index("\n}", start)
    function = body[start:end]
    assert "denominator = 2ULL * (uint64_t)(arr + 1u);" in function
    assert not re.search(r"denominator\s*=\s*[^;]*\(psc\s*\+\s*1u\)", function), (
        "в знаменателе снова учитывается (PSC + 1): tclk уже содержит это деление")
