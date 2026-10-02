"""Host tests for tools/bench_inject_check.py (offline: no port, no energy).

Pins down the published copy of the PC-3 bench package (request f3ebb36):
  * parsing of the live `@CI:ZERO` line, the dump/META shape and the verdict
    math (design scale, the disputed 4.5x case, SNR discipline, point gates);
  * the OFFSET-AWARE no-HV gate: `a` answers RAW COUNTS, so raw counts are
    never compared against a mV threshold (5000 mV is about 50 counts), the
    measured `cv` offset must be subtracted (a silent offset = 0 is forbidden)
    and the residual gate (median <= 9 / max <= 200 counts) must fire on a bus
    that is not bled;
  * fail-closed behaviour: no `@ADC:CV:OK` (firmware refusal rc=-2, ADC busy,
    old image) -> verdict FAIL and nothing else is sent to the bench - verified
    by the recorded command log of the stub.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import bench_inject_check as inj  # noqa: E402

# ---- canned replies, shapes byte-for-byte from the live PC-3 bench ----------
ZERO_LIVE = ("@CI:ZERO:samples=256:raw_i1_avg=2038:raw_i2_avg=2067:raw_i1_min=2031:"
             "raw_i1_max=2046:raw_i2_min=2038:raw_i2_max=2111:dt_ms=4:vbus_mv=62353"
             "\r\n> ")
ZERO_BLED = ("@CI:ZERO:samples=256:raw_i1_avg=2038:raw_i2_avg=2067:raw_i1_min=2037:"
             "raw_i1_max=2039:raw_i2_min=2066:raw_i2_max=2068:dt_ms=4:vbus_mv=0"
             "\r\n> ")
META = ("@PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0xE0:CR2=0x20:CCER=0x00000000"
        "\r\n> ")
META8 = ("@PWM:DUMP8:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0xE0:CR2=0x20:"
         "CCER=0x00000000\r\n> ")
BRK = "@BRK:valid=0:cause=0:count=0\r\n> "
SYSINFO = "@SYS:build=inject-check:adc_offsets_valid=1\r\n> "
STATUS = "@FOC:t=1:state=idle:em_stop2=0:FAULT=0\r\n"
CAL = "@ADC:CAL:offset_i1=2038:offset_i2=2067:offset_ires=2048\r\n> "
CV_OK = "@ADC:CV:OK:offset_vbus=22 (raw at 0 V)\r\n> "
CV_FAIL = ("@ADC:CV:FAIL:rc=-2 (0=OK -1=ADC busy/not converged -2=VBUS not at "
           "zero)\r\n> ")
CV_UNSUPPORTED = "err: cv unsupported\r\n> "


def adc_line(vbus):
    return "@ADC:I1=2038:I2=2067:Ires=2048:VBUS=%d\r\n> " % vbus


class StubBench:
    """Same surface as the real Bench, answered from a canned reply list."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.log = []
        self.ser = self                     # main() closes b.ser in `finally`

    def cmd(self, line, timeout=3.0):
        self.sent.append(line)
        if not self.replies:
            raise AssertionError("stub ran out of replies at %r (sent=%r)"
                                 % (line, self.sent))
        return self.replies.pop(0)

    def read_for(self, seconds, markers=()):
        return STATUS                        # @FOC status is polled, not a cmd

    def foc_lines(self, text):
        return [l.strip() for l in text.replace("\r", "\n").split("\n")
                if l.strip().startswith("@FOC:t=") and "em_stop2=" in l]

    def say(self, text):
        self.log.append(text)

    def close(self):
        pass


def run_preflight(monkeypatch, replies, tmp_path):
    """Run the tool with a stubbed bench, preflight-only, then return (rc, stub)."""
    stub = StubBench(replies)
    monkeypatch.setattr(inj, "Bench", lambda: stub)
    monkeypatch.setattr(sys, "argv", [
        "bench_inject_check.py", "--preflight-only", "--windows=1",
        "--log=%s" % (tmp_path / "inject_log.txt"),
    ])
    rc = inj.main()
    return rc, stub


# ---- [1] parsing ------------------------------------------------------------

def test_ci_zero_line_parsing():
    avg, lo, hi, vbus = inj.ci_window(StubBench([ZERO_LIVE]), 0, 1)
    assert avg["i1"] == [2038] and avg["i2"] == [2067]
    assert lo["i1"] == [2031] and hi["i2"] == [2111]
    assert vbus == [62353]          # firmware offset-aware mV


def test_hexfield_on_live_dump_shape():
    assert META.count("CCER") == 1
    assert inj.hexfield(META, "CCER") == 0
    assert inj.hexfield(META, "BDTR") == 0x1CC0
    assert (inj.hexfield(META, "BDTR") & 0x8000) == 0     # MOE clear


def test_median_handles_odd_and_even_lists():
    assert inj.median([5, 1, 3]) == 3
    assert inj.median([1, 2, 3, 4]) == 2.5
    assert inj.median([]) == 0.0


def test_int_prefix_parses_the_live_cv_line_with_trailing_comment():
    # The firmware sends `@ADC:CV:OK:offset_vbus=22 (raw at 0 V)` (src/cli.c),
    # so the captured field is "22 (raw at 0 V)" - never a bare isdigit().
    assert inj.tagged_field(CV_OK, "@ADC:CV:OK:", "offset_vbus") == "22 (raw at 0 V)"
    assert inj.int_prefix("22 (raw at 0 V)") == 22
    assert inj.int_prefix("0") == 0
    assert inj.int_prefix("-2") == -2
    assert inj.int_prefix("") is None and inj.int_prefix("n/a") is None


# ---- [2] units: raw counts are NOT millivolts -------------------------------

def test_raw_counts_are_never_compared_against_a_mv_threshold():
    # 1 count = 100.73 mV of bus voltage; 5000 mV is about 50 counts, so a
    # "VBUS < 5000" test on a raw count (the pre-fix procedure text) would let a
    # ~5 V live bus through as "bled".
    assert abs(inj.VBUS_MV_PER_COUNT - 100.73) < 0.01
    assert inj.vbus_mv_from_raw(49, 0) < inj.VBUS_MAX_MV      # 4936 mV
    assert inj.vbus_mv_from_raw(50, 0) >= inj.VBUS_MAX_MV     # 5036 mV = 50 counts
    assert inj.vbus_mv_from_raw(50, 0) == 5036


def test_offset_is_subtracted_not_assumed_zero():
    # Bled bus: raw sits at the measured offset (22 counts on this bench).
    assert inj.vbus_mv_from_raw(22, 22) == 0
    # Live bus 4.0 V (raw 62): 6245 mV if the offset were ignored, 4029 mV when
    # it is subtracted - both are refused (the residual gate catches 40 counts,
    # the mV gate catches 50 counts at 5.0 V).
    assert inj.vbus_mv_from_raw(62, 22) == 4029
    assert inj.vbus_mv_from_raw(62, 0) == 6245
    assert inj.vbus_mv_from_raw(72, 22) == 5036
    assert inj.vbus_mv_from_raw(0, 22) == 0                   # clamped below zero


# ---- [3] verdict math -------------------------------------------------------

def test_scale_math_78_counts_per_amp():
    # +1.000 A-equivalent -> +78.18 counts -> +1000 mA-eq -> slope 1.000
    assert abs(inj.COUNTS_PER_A - 78.18) < 0.01
    slope = 78.18 * inj.MA_PER_COUNT / 1000.0
    assert abs(slope - 1.0) * 100.0 < inj.DEFAULT_TOL_PCT
    # the Kelvin-point stimulus is 30 mV/A, the pin sees 63 mV/A after x2.1
    assert inj.SHUNT_MV_PER_A == 30.0 and inj.MV_PER_A == 63.0


def test_disputed_45x_case_is_caught():
    slope = 351 * inj.MA_PER_COUNT / 1000.0
    assert abs(slope - 1.0) * 100.0 > inj.DEFAULT_TOL_PCT
    assert slope > 4.0


def test_snr_discipline_blocks_a_two_count_move():
    assert 2.0 / max(1, 1) < inj.SNR_MIN


def test_point_gates():
    for amps, want in ((3.5, False), (0.01, False), (0.25, True), (-1.0, True)):
        assert (inj.MIN_POINT_A <= abs(amps) <= inj.ENVELOPE_A) is want


# ---- [4] offset-aware no-HV gate, end-to-end on a stub bench ----------------

def test_preflight_bled_bus_with_measured_offset_passes(monkeypatch, tmp_path):
    rc, stub = run_preflight(monkeypatch, [
        META, META8, BRK, adc_line(22), CV_OK, SYSINFO, adc_line(22), CAL,
        ZERO_BLED,
    ], tmp_path)
    assert stub.replies == []                    # every planned step was served
    assert stub.sent == ["dump", "dump8", "breakdiag", "a", "cv", "sysinfo",
                         "a", "c", "ci 0"]
    assert rc == 0
    assert any("VBUS offset=22 counts" in l for l in stub.log)
    assert any("vbus_mv (offset-aware): 0" in l for l in stub.log)
    assert any("preflight-only" in l for l in stub.log)


def test_preflight_refuses_a_live_bus_by_residual(monkeypatch, tmp_path):
    # raw 72 counts = the measured 22-count offset + 50 counts of real bus
    # (~5.0 V): the residual gate must fire, and nothing may be injected.
    rc, stub = run_preflight(monkeypatch, [
        META, META8, BRK, adc_line(72), CV_OK, SYSINFO, adc_line(72),
    ], tmp_path)
    assert rc == 1
    assert stub.sent == ["dump", "dump8", "breakdiag", "a", "cv", "sysinfo", "a"]
    assert "c" not in stub.sent and "ci 0" not in stub.sent
    assert any("VBUS is NOT bled" in l for l in stub.log)


def test_preflight_refuses_a_live_bus_reported_by_firmware_vbus_mv(
        monkeypatch, tmp_path):
    # Independent cross-check: `ci 0` carries the firmware's own offset-aware mV.
    rc, stub = run_preflight(monkeypatch, [
        META, META8, BRK, adc_line(22), CV_OK, SYSINFO, adc_line(22), CAL,
        ZERO_LIVE,
    ], tmp_path)
    assert rc == 1
    assert any("reports VBUS 62353 mV" in l for l in stub.log)


def test_preflight_is_fail_closed_without_cv_ok(monkeypatch, tmp_path):
    # firmware refuses (rc=-2: the input is not at 0 V) - a silent offset = 0 is
    # forbidden, so the bled DC-link cannot be proven and nothing else is sent.
    rc, stub = run_preflight(monkeypatch, [
        META, META8, BRK, adc_line(22), CV_FAIL, SYSINFO,
    ], tmp_path)
    assert rc == 1
    assert stub.sent == ["dump", "dump8", "breakdiag", "a", "cv", "sysinfo"]
    assert any("no @ADC:CV:OK" in l for l in stub.log)


def test_preflight_is_fail_closed_when_cv_is_unsupported(monkeypatch, tmp_path):
    rc, stub = run_preflight(monkeypatch, [
        META, META8, BRK, adc_line(22), CV_UNSUPPORTED, SYSINFO,
    ], tmp_path)
    assert rc == 1
    assert stub.sent == ["dump", "dump8", "breakdiag", "a", "cv", "sysinfo"]
    assert any("the image has no 'cv'" in l for l in stub.log)


if __name__ == "__main__":      # py -3 tests/test_bench_inject_check.py
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
