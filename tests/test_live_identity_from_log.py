"""Тесты парсера живой identity платы (TZ-02).

Проверяют: разбор реального формата mapcap identity, отказ на FAIL,
отказ на отсутствии строки, отказ на нулевых полях (fail-closed),
и совместимость вывода с --rebase-identity (все 11 полей присутствуют).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from live_identity_from_log import REQUIRED_NONZERO, parse_identity  # noqa: E402

GOOD = (
    "[2026-09-19T06:00:00+00:00] TX\nmapcap identity\n[2026-09-19T06:00:01+00:00] RX\n"
    "@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:"
    "sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0xE98FCB2C\r\n> \n"
)


def test_parses_real_identity_line() -> None:
    ident = parse_identity(GOOD)
    assert ident["board_revision"] == 7
    assert ident["pwm_frequency_hz"] == 5000          # истинные 5000, не D3-значение 294
    assert ident["timer_arr"] == 999
    assert ident["adc_trigger_id"] == 0x4F455731
    assert ident["trigger_offset_ticks"] == 0
    assert ident["deadtime_ticks"] == 192
    assert ident["adc_clock_hz"] == 42500000
    assert ident["adc_sample_cycles_x2"] == 1281
    assert ident["adc_resolution"] == 0
    assert ident["adc_config_signature"] == 0x26B9B97B
    assert ident["current_calibration_signature"] == 0xE98FCB2C


def test_all_required_fields_present_for_rebase() -> None:
    ident = parse_identity(GOOD)
    for key in REQUIRED_NONZERO:
        assert ident.get(key), f"поле {key} должно быть ненулевым"
    assert len(ident) == 11


def test_fail_line_is_rejected() -> None:
    with pytest.raises(ValueError, match="IDENTITY:FAIL"):
        parse_identity("[utc] TX\nmapcap identity\n[utc] RX\n@MAP:IDENTITY:FAIL\r\n> \n")


def test_missing_line_is_rejected() -> None:
    with pytest.raises(ValueError, match="не найдена"):
        parse_identity("@SYS:CLK=170000000:PSC=16:TCLK=10000000\r\n")


def test_zero_field_is_rejected() -> None:
    broken = GOOD.replace("acs=0x26B9B97B", "acs=0x00000000")
    with pytest.raises(ValueError, match="нулевые поля"):
        parse_identity(broken)


def test_truncated_line_is_rejected() -> None:
    truncated = GOOD.replace(":ccs=0xE98FCB2C", "")
    with pytest.raises(ValueError, match="нет полей"):
        parse_identity(truncated)


def test_garbage_value_is_rejected() -> None:
    broken = GOOD.replace("pwm=5000", "pwm=пятьтысяч")
    with pytest.raises(ValueError, match="не разобрано как число"):
        parse_identity(broken)
