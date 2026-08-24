from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from mapcap_uart_export import export_uart_log, parse_record_line  # noqa: E402


VALID = (
    "@MC:REC:cap=7:seq=42:raw_i1=2048:raw_i2=2050:raw_ct=0:raw_vbus=739:"
    "i1=0:i2=25:vbus=595:ccr1=2470,2500,2530:ccr8=2470,2500,2530:"
    "arr=5000:trig=3:status=7:fault=0\r\n"
)


def test_parse_record_preserves_raw_and_pwm_fields():
    record = parse_record_line(VALID, 12)
    assert record["cap"] == 7
    assert record["seq"] == 42
    assert record["raw_ct"] == 0
    assert record["ccr1"] == [2470, 2500, 2530]
    assert record["ccr8"] == [2470, 2500, 2530]
    assert record["source_line"] == 12


def test_export_ignores_non_record_uart_lines(tmp_path):
    source = tmp_path / "uart.log"
    destination = tmp_path / "raw.jsonl"
    source.write_text("boot\r\n> \r\n" + VALID + "@ADC:I1=2048\r\n", encoding="utf-8")

    assert export_uart_log(source, destination) == 1
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["seq"] == 42


def test_truncated_record_is_rejected():
    bad = VALID.replace(":fault=0", "")
    try:
        parse_record_line(bad, 9)
    except ValueError as exc:
        assert "missing fields" in str(exc)
    else:
        raise AssertionError("truncated @MC:REC must be rejected")
