"""Software-contract regression for tools/scope_acs712_capture.py.

Runs WITHOUT hardware and WITHOUT pyusb on purpose: the pure functions must be
importable and usable on a clean checkout, because CI installs pytest + numpy
only. A module-level ``import usb`` would make these tests unrunnable there.

Covers the defects found while reviewing the first TZ revision:

  * ``unit()`` must be total - an empty or garbage scope quantity must not
    raise (the field is frequently empty on firmware V3.0.0);
  * the CSV emitter writes INTEGER millivolts, because ``parse_scope_csv``
    reads every numeric field through ``int()``;
  * ``scope_qualified`` is a real gate, not the old amplitude sniff
    ``abs(sync_mv - 2500) > 500`` that any DC level away from mid-rail passed;
  * a swapped marker/current channel must be rejected - an ACS712 output is
    itself periodic at the PWM frequency, so periodicity and frequency gates
    cannot tell it from the logic marker; only the signal span can;
  * the emitter -> map_scope_ingest contract, end to end.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import map_scope_ingest as msi   # noqa: E402
import scope_acs712_capture as SCOPE   # noqa: E402

# 500 mV/div, 1X probe, zero offset -> to_mv() yields 20 mV per code. Codes stay
# inside 1..254 so no channel is falsely reported as rail-clipped.
META = {"SCALE": "500mV", "PROBE": "1X", "OFFSET": "0"}
MV_PER_CODE = 20.0
SR = 200000.0
PWM_HZ = 5000.0
N = 4000


def _chan(mv):
    codes = np.clip(np.round(np.asarray(mv) / MV_PER_CODE), 0, 255).astype(np.int32)
    return {"codes": codes, "mv": SCOPE.to_mv(codes, META)}


def _good(pwm_hz=PWM_HZ, n=N, u_amp_mv=200.0, v_amp_mv=150.0):
    """Periodic 3.3 V marker plus two ACS712-like phase currents."""
    t = np.arange(n) / SR
    sync = np.where((t * pwm_hz) % 1.0 < 0.5, 3300.0, 500.0)
    u = 2680.0 + u_amp_mv * np.sin(2 * np.pi * pwm_hz * t)
    v = 2661.0 + v_amp_mv * np.sin(2 * np.pi * pwm_hz * t + 2.1)
    return {"sync": _chan(sync), "u": _chan(u), "v": _chan(v)}


def _dc(n=N, sync_mv=3300.0):
    return {"sync": _chan(np.full(n, sync_mv)),
            "u": _chan(np.full(n, 2680.0)),
            "v": _chan(np.full(n, 2661.0))}


def test_pure_api_works_without_pyusb(monkeypatch, tmp_path):
    """The software path must not depend on the USB transport being available."""
    monkeypatch.setattr(SCOPE, "usb", None)

    assert SCOPE.unit("(50kSa/s)") == pytest.approx(50000.0)
    assert SCOPE.to_mv(np.array([0, 1], dtype=np.int32), META)[1] == pytest.approx(20.0)

    out = tmp_path / "scope.csv"
    SCOPE.write_scope_csv(out, np.array([10]), np.full(50, 2780.0),
                          np.full(50, 2561.0), 900, 15, 1, "")
    assert out.exists()

    qualified, _checks, _notes = SCOPE.qualify(_good(), SR, PWM_HZ, 900.0, 110)
    assert qualified == 1

    # only the hardware entry point may fail, and it must fail cleanly
    with pytest.raises(SCOPE.ScopeError, match="pyusb"):
        SCOPE.Scope()
    with pytest.raises(SCOPE.ScopeError):
        SCOPE.cmd_list(None)


def test_unit_is_total():
    assert SCOPE.unit("(50kSa/s)") == pytest.approx(50000.0)
    assert SCOPE.unit("(500kSa/s)") == pytest.approx(500000.0)
    assert SCOPE.unit("10 ms") == pytest.approx(0.01)
    assert SCOPE.unit("500us") == pytest.approx(5e-4)
    assert SCOPE.unit("50.0mV") == pytest.approx(0.05)
    # must not raise: these occur in real HEAD dumps
    for bad in ("", None, "garbage", "   "):
        assert SCOPE.unit(bad) == 0.0


def test_to_mv_scaling():
    codes = np.array([0, 25, 50], dtype=np.int32)
    one_v_div = {"SCALE": "1.00 V", "PROBE": "1X", "OFFSET": "0"}
    assert SCOPE.to_mv(codes, one_v_div)[1] == pytest.approx(1000.0)
    ten_x = {"SCALE": "1.00 V", "PROBE": "10X", "OFFSET": "0"}
    assert SCOPE.to_mv(codes, ten_x)[1] == pytest.approx(10000.0)
    assert SCOPE.to_mv(codes, META)[1] == pytest.approx(500.0)


def test_qualify_accepts_valid_trace():
    qualified, checks, notes = SCOPE.qualify(_good(), SR, PWM_HZ, 900.0, 110)
    assert qualified == 1, notes
    assert all(checks.values())


def test_qualify_rejects_dc_trace_that_old_gate_passed():
    """The defect: a flat marker away from mid-rail satisfied the old sniff."""
    dc = _dc()
    old_gate = 1 if abs(float(dc["sync"]["mv"].mean()) - 2500.0) > 500.0 else 0
    assert old_gate == 1, "this DC trace is exactly what the old gate passed"

    qualified, checks, _ = SCOPE.qualify(dc, SR, PWM_HZ, 900.0, 110)
    assert qualified == 0
    assert checks["marker_is_logic_signal"] is False
    assert checks["sync_periodic"] is False


def test_qualify_rejects_swapped_marker_and_current_channel():
    """An ACS712 output is periodic at the PWM frequency too - span must decide."""
    good = _good()
    swapped = {"sync": good["u"], "u": good["sync"], "v": good["v"]}
    qualified, checks, notes = SCOPE.qualify(swapped, SR, PWM_HZ, 900.0, 110)
    assert qualified == 0
    assert checks["marker_is_logic_signal"] is False
    assert checks["current_channels_not_marker"] is False
    assert any("wiring" in n for n in notes)


def test_qualify_rejects_wrong_declared_pwm_frequency():
    _, checks, _ = SCOPE.qualify(_good(), SR, PWM_HZ * 4, 900.0, 110)
    assert checks["pwm_frequency_match"] is False


def test_qualify_rejects_clipping_and_low_margin():
    clipped = _good()
    clipped["u"]["codes"] = clipped["u"]["codes"].copy()
    clipped["u"]["codes"][100:120] = 255
    _, checks_clip, _ = SCOPE.qualify(clipped, SR, PWM_HZ, 900.0, 110)
    assert checks_clip["no_clipping"] is False

    _, checks_low, _ = SCOPE.qualify(_good(), SR, PWM_HZ, 50.0, 110)
    assert checks_low["margin_above_minimum"] is False

    _, checks_none, _ = SCOPE.qualify(_good(), SR, PWM_HZ, None, 110)
    assert checks_none["margin_above_minimum"] is False


def test_measure_margin_ticks_is_numeric():
    """Marker rises every 40 samples; the switching edge lands 5 samples later."""
    timer_hz = 170e6
    idx = np.arange(N)
    sync_mv = np.where((idx % 40) < 20, 3300.0, 500.0)
    u_mv = np.where((idx % 40) >= 5,
                    np.where((idx % 40) < 10, 3000.0, 2680.0), 2680.0)
    margin, err = SCOPE.measure_margin_ticks(
        SCOPE.to_mv(np.clip(np.round(u_mv / MV_PER_CODE), 0, 255).astype(np.int32), META),
        SCOPE.to_mv(np.clip(np.round(sync_mv / MV_PER_CODE), 0, 255).astype(np.int32), META),
        SR, timer_hz)
    assert err is None
    assert margin == pytest.approx((5.0 / SR) * timer_hz, abs=1.0)

    # a flat current channel must not yield a margin
    flat = SCOPE.to_mv(np.full(N, 134, dtype=np.int32), META)
    margin2, err2 = SCOPE.measure_margin_ticks(
        flat, SCOPE.to_mv(np.clip(np.round(sync_mv / MV_PER_CODE), 0, 255).astype(np.int32), META),
        SR, timer_hz)
    assert margin2 is None and err2


def test_emitter_writes_integer_mv_and_ingest_roundtrip(tmp_path):
    """Emitter -> real parse_scope_csv, in the repo's tmp_path convention."""
    calib_path = tmp_path / "acs712_calibration.json"
    calib_path.write_text(
        '{"vcc_mv": 5020, "sensors": {'
        '"U": {"v0_mv": 2680, "sens_mv_per_a": 100.0},'
        '"V": {"v0_mv": 2661, "sens_mv_per_a": 100.0}}}', encoding="utf-8")

    csv_path = tmp_path / "scope_region_0.csv"
    SCOPE.write_scope_csv(csv_path, np.arange(5) + 1,
                          np.full(50, 2780.0), np.full(50, 2561.0),
                          900, 15, 1, "")

    header, body = csv_path.read_text(encoding="utf-8").splitlines()[:2]
    assert header == ("pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,"
                      "blanking_ticks,scope_qualified,note")
    assert "." not in body.split(",")[1], "integer mV required: %r" % body
    assert body.split(",")[3] == "", "ref_w_mv must stay empty (KCL-derived)"

    rows = msi.parse_scope_csv(csv_path, expected_rows=5,
                               calibration=msi._load_calibration(calib_path))
    assert [(r["ref_u_ma"], r["ref_v_ma"], r["ref_w_ma"]) for r in rows] == \
        [(1000, -1000, 0)] * 5
    assert rows[0]["scope_qualified"] == 1
    assert rows[0]["margin_ticks"] == 900


def test_float_mv_is_rejected_by_ingest(tmp_path):
    """The old emitter defect (float formatting) must fail loudly downstream."""
    calib_path = tmp_path / "calib.json"
    calib_path.write_text('{"vcc_mv": 5020, "sensors": {'
                          '"U": {"v0_mv": 2680}, "V": {"v0_mv": 2661}}}',
                          encoding="utf-8")
    csv_path = tmp_path / "float.csv"
    csv_path.write_text(
        "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,"
        "scope_qualified,note\n1,2780.0000,2561.0000,,900,15,1,\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="не int"):
        msi.parse_scope_csv(csv_path, expected_rows=1,
                            calibration=msi._load_calibration(calib_path))


def test_cli_requires_pwm_hz_and_rejects_duplicate_channels(tmp_path):
    """No silent default for the PWM frequency; channels must be distinct."""
    import subprocess

    script = str(_TOOLS / "scope_acs712_capture.py")
    no_pwm = subprocess.run([sys.executable, script, "--capture"],
                            capture_output=True, text=True)
    assert no_pwm.returncode == 2
    assert "pwm-hz is required" in no_pwm.stdout

    dup = subprocess.run([sys.executable, script, "--capture", "--pwm-hz", "5000",
                          "--sync-ch", "CH1", "--u-ch", "CH1", "--v-ch", "CH3"],
                         capture_output=True, text=True)
    assert dup.returncode == 2
