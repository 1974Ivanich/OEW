"""Offline tests for tools/bench_test2_capture.py; no COM port or sigrok device required."""

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
STATUS_MINUS_11 = "@MC:STATUS:state=5:term=-11:cap=7:frames=0:dropped=0:periods=1:avail=0\r\n> "
STATUS_MINUS_12 = "@MC:STATUS:state=5:term=-12:cap=7:frames=0:dropped=0:periods=1:avail=0\r\n> "
DRAIN_ZERO = "@MC:DRAIN:records=0\r\n> "


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

    def test_accepts_nohv_adc_lower_rail_fault(self) -> None:
        result = capture.evaluate_test(ARM_OK, RUN_OK, STATUS_MINUS_11, DRAIN_ZERO)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["status"]["term"], -11)

    def test_accepts_nohv_vbus_limit_fault(self) -> None:
        result = capture.evaluate_test(ARM_OK, RUN_OK, STATUS_MINUS_12, DRAIN_ZERO)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["status"]["term"], -12)

    def test_rejects_records_at_zero_vbus(self) -> None:
        status = "@MC:STATUS:state=3:term=0:cap=7:frames=16:dropped=0:periods=16:avail=16\r\n> "
        drain = "@MC:REC:cap=7:seq=1\r\n@MC:DRAIN:records=16\r\n> "
        result = capture.evaluate_test(ARM_OK, RUN_OK, status, drain)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result["checks"]["zero_drain_records"])
        self.assertFalse(result["checks"]["no_record_lines"])

    def test_rate_format(self) -> None:
        self.assertEqual(capture.rate_to_sigrok(8_000_000), "8m")
        self.assertEqual(capture.rate_to_sigrok(500_000), "500k")


if __name__ == "__main__":
    unittest.main()
