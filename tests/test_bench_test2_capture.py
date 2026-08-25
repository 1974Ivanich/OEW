"""Offline tests for tools/bench_test2_capture.py; no COM port or sigrok device."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import bench_test2_capture as capture


ARM_OK = "@MC:ARM:cap=7:rc=0\r\n> "
RUN_OK = "@MC:RUN:rc=0\r\n> "
PREFLIGHT_NOHV = "@ADC:I1=2048:I2=2048:Ires=2048:VBUS=2\r\n> "
DRAIN_ZERO = "@MC:DRAIN:records=0\r\n> "


def status(
    term: int = -12,
    detail: int = 7,
    raw_vbus: int = 2,
    vbus_mv: int = 201,
    i1_ma: int = 0,
    i2_ma: int = 0,
    adc_status: int = 7,
    frames: int = 0,
    dropped: int = 0,
    avail: int = 0,
) -> str:
    return (
        f"@MC:STATUS:state=5:term={term}:cap=7:frames={frames}:dropped={dropped}:periods=1:avail={avail}:"
        f"detail={detail}:raw_vbus={raw_vbus}:vbus_mv={vbus_mv}:i1_ma={i1_ma}:i2_ma={i2_ma}:"
        f"adc_status={adc_status}:sector=0:window=0\r\n> "
    )


def evaluate(status_text: str, drain_text: str = DRAIN_ZERO) -> dict:
    return capture.evaluate_test(
        ARM_OK,
        RUN_OK,
        PREFLIGHT_NOHV,
        status_text,
        drain_text,
        nohv_max_raw_vbus=9,
        min_vbus_mv=1000,
        max_abs_shunt_ma=10000,
    )


class CaptureParserTest(unittest.TestCase):
    def test_accepts_calibration_offsets_response(self) -> None:
        response = "@ADC:CAL:offset_i1=2039:offset_i2=2068:offset_ires=0\r\n> "
        self.assertTrue(capture.has_calibration_ok(response))

    def test_rejects_calibration_failure(self) -> None:
        response = "@ADC:CAL:FAIL:rc=-1 (Ires unqualified or ADC not converged)\r\n> "
        self.assertFalse(capture.has_calibration_ok(response))

    def test_parses_encoder_success(self) -> None:
        response = "@ENC:angle=3019:speed=0:period_us=897:pulse_us=670:err=0\r\n> "
        self.assertTrue(capture.has_encoder_ok(response))

    def test_accepts_only_explicit_vbus_low_path(self) -> None:
        result = evaluate(status())
        self.assertEqual(result["automation"], "PASS")
        self.assertEqual(result["scope"], "PENDING")
        self.assertEqual(result["final"], "PENDING")
        self.assertTrue(all(result["checks"].values()))

    def test_rejects_i1_limit(self) -> None:
        result = evaluate(status(detail=5, i1_ma=10001))
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["detail_is_vbus_low"])

    def test_rejects_i2_limit(self) -> None:
        result = evaluate(status(detail=6, i2_ma=-10001))
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["detail_is_vbus_low"])

    def test_rejects_vbus_high(self) -> None:
        result = evaluate(status(detail=8, raw_vbus=100, vbus_mv=10070))
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["detail_is_vbus_low"])

    def test_rejects_adc_status_invalid_even_when_raw_vbus_is_zero(self) -> None:
        result = evaluate(status(term=-11, detail=2, raw_vbus=0, vbus_mv=0, adc_status=8))
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["terminal_is_limit_exceeded"])

    def test_rejects_legacy_status_without_extended_evidence(self) -> None:
        legacy = "@MC:STATUS:state=5:term=-12:cap=7:frames=0:dropped=0:periods=1:avail=0\r\n> "
        result = evaluate(legacy)
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["status_parsed_extended_contract"])

    def test_rejects_missing_vbus_evidence(self) -> None:
        incomplete = status().replace(":raw_vbus=2", "")
        result = evaluate(incomplete)
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["status_parsed_extended_contract"])

    def test_rejects_records_at_zero_vbus(self) -> None:
        drain = "@MC:REC:cap=7:seq=1\r\n@MC:DRAIN:records=1\r\n> "
        result = evaluate(status(frames=1, avail=1), drain)
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["zero_drain_records"])
        self.assertFalse(result["checks"]["no_record_lines"])

    def test_rejects_timeout_terminal(self) -> None:
        result = evaluate(status(term=-9, detail=0))
        self.assertEqual(result["automation"], "FAIL")
        self.assertFalse(result["checks"]["terminal_is_limit_exceeded"])

    def test_rate_format(self) -> None:
        self.assertEqual(capture.rate_to_sigrok(8_000_000), "8m")
        self.assertEqual(capture.rate_to_sigrok(500_000), "500k")


if __name__ == "__main__":
    unittest.main()
