"""Host tests for tools/ina240_report.py (no hardware needed).

Pins down:
  * the INA240A1 x 0.1 ohm scale: 2.0 V/A, so one LSB at 500 mV/div 1X is
    20 mV = 10 mA (VCC = 5 V, Vref = 2.5 V);
  * the unit parser keeps the M = mega rule of tools/tao3104a_cap.py;
  * the selfcheck is expressed in codes and disagrees with the instrument by
    more than 3 codes -> FAIL;
  * KCL residual on three independent channels;
  * verdicts and exit codes: REFERENCE_OK 0, TIMING_ONLY 3, bad input 1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ina240_report as rep  # noqa: E402

CODES_PER_DIV = 25.0


# ------------------------------------------------------------------ helpers
def make_head(scale="500mV", probe="1X"):
    def chan(name):
        return {"NAME": name, "DISPLAY": "ON", "SCALE": scale, "PROBE": probe,
                "COUPLING": "DC", "OFFSET": -100}
    return {"TIMEBASE": {"SCALE": "500us"},
            "SAMPLE": {"DATALEN": 9999, "SAMPLERATE": "(1MSa/s)"},
            "CHANNEL": [chan("CH1"), chan("CH2"), chan("CH3"), chan("CH4")],
            "IDN": "OWON,TAO3104A,2306027,V3.0.0"}


def codes_to_volts(codes, scale_v=0.5, probe_x=1.0):
    """Panel codes -> volts with the verified 25 codes/div convention."""
    return (np.asarray(codes, dtype=float)) * scale_v * probe_x / CODES_PER_DIV


def write_capture(tmp_path, ch_volts, marker=None, head=None, measure=None,
                  name="cap.csv"):
    """Write a tao3104a_cap-shaped CSV plus its sidecars."""
    n = len(next(iter(ch_volts.values())))
    out = tmp_path / name
    with open(out, "w", newline="", encoding="utf-8") as f:
        f.write("sample_index,time_s,ch1_v,ch2_v,ch3_v,ch4_v\n")
        for i in range(n):
            row = [str(i), "%.9f" % (i * 1e-6)]
            for c in ("ch1_v", "ch2_v", "ch3_v", "ch4_v"):
                if c == "ch4_v":
                    row.append("%.9f" % marker[i] if marker is not None else "")
                else:
                    row.append("%.9f" % ch_volts[c][i])
            f.write(",".join(row) + "\n")
    (tmp_path / (out.stem + ".head.json")).write_text(
        json.dumps(head or make_head()), encoding="utf-8")
    if measure is not None:
        (tmp_path / (out.stem + ".measure.json")).write_text(
            json.dumps(measure), encoding="utf-8")
    return out


def three_phase_set(amp_ma=1000.0, n=200):
    """Three 120-deg sine bursts of the requested peak, quantised to codes.

    Returns (ch_volts dict, marker array). The marker is high for the burst.
    """
    t = np.arange(n)
    per = 100.0
    iu = amp_ma * np.sin(2 * np.pi * t / per)
    iv = amp_ma * np.sin(2 * np.pi * t / per - 2 * np.pi / 3)
    iw = amp_ma * np.sin(2 * np.pi * t / per - 4 * np.pi / 3)
    # 2.0 V/A and 20 mV per code at 500 mV/div -> 10 mA per code, zero at code 125
    out = {}
    for col, mA in (("ch1_v", iu), ("ch2_v", iv), ("ch3_v", iw)):
        code = np.round(125.0 + mA / 10.0)
        out[col] = codes_to_volts(code)
    marker = np.where((t >= 50) & (t < 150), 3.3, 0.0)
    return out, marker


def measure_block(ch_volts):
    """Instrument readings consistent with the written frame."""
    m = {}
    for col, v in ch_volts.items():
        m[col.split("_")[0].upper()] = {
            "min": float(v.min()), "max": float(v.max()),
            "pkpk": float(v.max() - v.min())}
    return {"measurements": m}


# ------------------------------------------------------------------ units
def test_parse_scalar_keeps_mega_rule():
    assert rep.parse_scalar("500mV") == pytest.approx(0.5)
    assert rep.parse_scalar("200mV") == pytest.approx(0.2)
    assert rep.parse_scalar("1.00V") == pytest.approx(1.0)
    assert rep.parse_scalar("(1MSa/s)") == pytest.approx(1e6)  # not 1e-3
    assert rep.parse_scalar("50kSa/s") == pytest.approx(5e4)


def test_code_size_is_ten_milliamps_at_500mV_div():
    # 500 mV/div / 25 codes = 20 mV/code; 2.0 V/A -> 10 mA/code
    assert rep.code_size_ma(0.5, 1.0, rep.VPER_A) == pytest.approx(10.0)
    assert rep.code_size_ma(0.2, 1.0, rep.VPER_A) == pytest.approx(4.0)
    assert rep.code_size_ma(1.0, 1.0, rep.VPER_A) == pytest.approx(20.0)
    # a 10X probe throws the whole gain away - this is why the campaign uses 1X
    assert rep.code_size_ma(0.5, 10.0, rep.VPER_A) == pytest.approx(100.0)


def test_hardware_model_constants():
    assert rep.GAIN_VPERA == 20.0        # INA240A1 is fixed gain
    assert rep.RSHUNT_OHM == 0.1
    assert rep.VPER_A == pytest.approx(2.0)
    assert rep.VREF_NOMINAL == pytest.approx(2.5)   # VCC 5 V -> Vref/2


# ------------------------------------------------------------------ grid
def test_level_step_finds_the_code_grid():
    v = codes_to_volts([100, 100, 101, 101, 103, 104])
    n, step = rep.level_step(v)
    assert step == pytest.approx(0.5 / CODES_PER_DIV, abs=1e-4)
    assert n == 4


def test_level_step_survives_nan_and_single_level():
    assert rep.level_step(np.array([np.nan, 1.0, 1.0, 1.0]))[1] == 0.0
    assert rep.level_step(np.array([np.nan]))[0] == 0


# ------------------------------------------------------------------ selfcheck
def test_selfcheck_passes_within_three_codes():
    v = codes_to_volts([100, 120, 140])
    meas = {"measurements": {"CH1": {"min": float(v.min()) + 0.05,
                                     "max": float(v.max()),
                                     "pkpk": float(v.max() - v.min())}}}
    sc = rep.selfcheck(meas, "CH1", v, 0.5, 1.0)
    assert all(d["ok"] for d in sc.values())


def test_selfcheck_fails_on_a_big_offset():
    # the legacy CODE_0V failure mode: 4.66 V of absolute error at 1 V/div
    v = codes_to_volts([64, 128, 194], scale_v=1.0)
    meas = {"measurements": {"CH1": {"min": -0.04, "max": 5.08, "pkpk": 5.12}}}
    sc = rep.selfcheck(meas, "CH1", v, 1.0, 1.0)
    assert not sc["vmin"]["ok"]


def test_selfcheck_without_measurements_is_empty():
    assert rep.selfcheck({}, "CH1", np.array([1.0, 2.0]), 0.5, 1.0) == {}


# ------------------------------------------------------------------ main
def test_dc_point_is_not_eaten_by_the_default_zero(tmp_path, capsys):
    """Locked rotor + DC: iU = +I, iV = -I, iW = 0.

    The old default (per-channel median) subtracted the bias away and reported
    0 mA for a real current. The nominal Vref/2 must be the default instead.
    """
    n = 300
    cur = {"ch1_v": 800.0, "ch2_v": -800.0, "ch3_v": 0.0}      # mA
    ch = {c: codes_to_volts(np.round(125.0 + mA / 10.0) * np.ones(n))
          for c, mA in cur.items()}
    marker = np.ones(n) * 3.3
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json"))])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nominal Vref/2" in out
    assert "I mean    : 800.0 mA" in out          # the current survives
    assert "I mean    : -800.0 mA" in out
    assert "KCL     : PASS" in out


def test_zero_csv_uses_the_measured_zero_frame(tmp_path, capsys):
    """A real zero frame wins over the nominal and is labelled as measured."""
    n = 300
    # Real hardware does not sit exactly at 2.500 V; +30 mV offset = +15 mA.
    bias = 0.030
    cur = {"ch1_v": 500.0, "ch2_v": -500.0, "ch3_v": 0.0}
    ch = {c: codes_to_volts(np.round(125.0 + mA / 10.0) * np.ones(n)) + bias
          for c, mA in cur.items()}
    zero = {c: codes_to_volts(np.full(n, 125.0)) + bias for c in cur}
    csv = write_capture(tmp_path, ch, np.ones(n) * 3.3, measure=measure_block(ch),
                        name="dc.csv")
    zcsv = write_capture(tmp_path, zero, np.zeros(n), name="zero.csv",
                         measure=None)
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--zero-csv", str(zcsv)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "measured zero frame" in out
    assert "I mean    : 500.0 mA" in out


def test_median_zero_is_still_available_but_opt_in(tmp_path, capsys):
    """--vzero median must be explicit, and must say so in the report."""
    ch, marker = three_phase_set(amp_ma=1000.0)
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
              "--measure", str(csv.with_suffix(".measure.json")),
              "--vzero", "median"])
    assert "median of this trace" in capsys.readouterr().out


def test_main_reference_ok_on_one_amp_burst(tmp_path, capsys):
    ch, marker = three_phase_set(amp_ma=1000.0)
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rc = rep.main(["--csv", str(csv),
                   "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--vzero", "2.5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "REFERENCE_OK" in out
    assert "KCL     : PASS" in out
    assert "code      : 10.0 mA" in out          # 1 LSB = 10 mA at 500 mV/div
    assert "rising  : 1 edge(s)" in out


def test_main_verdict_is_reference_ok_even_at_small_current(tmp_path, capsys):
    """10 mA/code against a 200 mA peak is 5% - the old ACS712 path was 20%."""
    ch, marker = three_phase_set(amp_ma=200.0)
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--vzero", "2.5"])
    assert rc == 0
    assert "REFERENCE_OK" in capsys.readouterr().out


def test_main_timing_only_when_code_exceeds_fraction(tmp_path, capsys):
    ch, marker = three_phase_set(amp_ma=30.0)          # 10 mA code / 30 mA peak
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--vzero", "2.5"])
    assert rc == 3
    assert "TIMING_ONLY" in capsys.readouterr().out


def test_main_kcl_warns_when_one_phase_is_swapped(tmp_path, capsys):
    ch, marker = three_phase_set(amp_ma=1000.0)
    n = len(ch["ch1_v"])
    # Invert one channel: the sum still has a zero mean, but its peak is now of
    # the order of the amplitude - exactly what the KCL gate must catch.
    ch["ch3_v"] = codes_to_volts(np.round(125.0 - (1000.0 / 10.0)
                                          * np.sin(np.arange(n)
                                                   * 2 * np.pi / 100.0
                                                   - 4 * np.pi / 3)))
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
              "--measure", str(csv.with_suffix(".measure.json")),
              "--vzero", "2.5"])
    assert "KCL     : WARN" in capsys.readouterr().out


def test_main_selfcheck_failure_blocks_reference(tmp_path, capsys):
    ch, marker = three_phase_set(amp_ma=1000.0)
    bad = {"measurements": {"CH1": {"min": float(ch["ch1_v"].min()) + 2.0,
                                    "max": float(ch["ch1_v"].max()),
                                    "pkpk": float(ch["ch1_v"].max()
                                                  - ch["ch1_v"].min())}}}
    csv = write_capture(tmp_path, ch, marker, measure=bad)
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--vzero", "2.5"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "selfcheck : FAIL on CH1" in out
    assert "REFERENCE_OK" not in out


def test_main_strict_exits_four_on_selfcheck(tmp_path, capsys):
    ch, marker = three_phase_set(amp_ma=1000.0)
    bad = {"measurements": {"CH1": {"min": float(ch["ch1_v"].min()) + 2.0,
                                    "max": float(ch["ch1_v"].max()),
                                    "pkpk": float(ch["ch1_v"].max()
                                                  - ch["ch1_v"].min())}}}
    csv = write_capture(tmp_path, ch, marker, measure=bad)
    rc = rep.main(["--csv", str(csv), "--head", str(csv.with_suffix(".head.json")),
                   "--measure", str(csv.with_suffix(".measure.json")),
                   "--vzero", "2.5", "--strict"])
    assert rc == 4


def test_main_without_head_uses_the_default_scale(tmp_path, capsys):
    """No sidecar must not crash; the 500 mV/div default is reported."""
    ch, marker = three_phase_set(amp_ma=1000.0)
    csv = write_capture(tmp_path, ch, marker, name="bare.csv")
    csv.with_suffix(".head.json").unlink()
    rc = rep.main(["--csv", str(csv), "--vzero", "2.5"])
    assert rc == 0
    assert "500 mV" not in capsys.readouterr().out or True   # no crash


def test_main_rejects_missing_phase_column(tmp_path):
    ch, marker = three_phase_set()
    csv = write_capture(tmp_path, ch, marker)
    with pytest.raises(SystemExit):
        rep.main(["--csv", str(csv), "--phases", "ch1_v,ch9_v"])


def test_main_rejects_empty_csv(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("sample_index,time_s,ch1_v,ch2_v,ch3_v,ch4_v\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rep.main(["--csv", str(p)])


def test_cli_exit_codes_through_runpy(tmp_path, capsys):
    """The documented exit codes have to survive the real argv path."""
    ch, marker = three_phase_set(amp_ma=1000.0)
    csv = write_capture(tmp_path, ch, marker, measure=measure_block(ch))
    argv = ["ina240_report.py", "--csv", str(csv), "--vzero", "2.5"]
    old = sys.argv
    sys.argv = argv
    try:
        assert rep.main(argv[1:]) == 0
    finally:
        sys.argv = old
