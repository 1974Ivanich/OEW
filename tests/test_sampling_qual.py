"""Host tests for tools/sampling_qual.py (Level 2, no hardware).

Synthetic 4-channel frames with KNOWN geometry (no curve fitting):
  * PA4 toggle: 2 edges per PWM period, known period -> f_pwm;
  * PA5 toggle: rising edge at known phase in period, per-frame drift
    -> phase jitter (analytic);
  * CH1/CH2: DC + triangular PWM ripple of known pp;
  * current at marker computed by interpolation == analytic value (tolerance
    of one screen sample).
Verdict paths: QUALIFIED / SUSPECT (jitter, period, error frames), no phase
fitting anywhere; A/B split pinned (current_reconstruction.claim ==
NOT_CLAIMED). Gate: CH3/CH4 absent/off -> SCOPE_CONTROL_BLOCKED. Provenance
emit/check --define (OEW_BENCH_APERTURE=1) round-trip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import sampling_qual as sq  # noqa: E402
from shunt_cal_campaign import SCOPE_CONTROL_BLOCKED  # noqa: E402

IDN = "OWON,TAO3104A,2306027,V3.0.0"


# ---------------------------------------------------------------- fixtures
def make_head(datalen=1520):
    chans = []
    for name, scale in (("CH1", "100mV"), ("CH2", "100mV"),
                        ("CH3", "1V"), ("CH4", "1V")):
        chans.append({"NAME": name, "DISPLAY": "ON", "SCALE": scale,
                      "PROBE": "1X", "COUPLING": "DC", "OFFSET": -100,
                      "INVERSE": "OFF"})
    return {"TIMEBASE": {"SCALE": "500us"},
            "SAMPLE": {"DATALEN": datalen, "FULLSCREEN": datalen},
            "CHANNEL": chans, "IDN": IDN, "MODEL": "510401102"}


def synth_frame(datalen=1520, t_pwm_n=304.0, phase0=0.30, phase_drift=0.0,
                ripple_pp_v=0.020, dc_v=0.033, marker_phase=None):
    """Один кадр: PA4 тогглится 2 раза за t_pwm_n сэмплов; PA5 — rising фронт
    раз за период в фазе phase0 (+phase_drift*кадр не здесь — дрифт даёт call site);
    CH1/CH2 — DC + треугольный ripple, синхронный с PWM."""
    def tri(i, period):
        x = (i % period) / period
        return 2.0 * x if x < 0.5 else 2.0 * (1.0 - x)

    ch4, ch3 = [], []
    half = t_pwm_n / 2.0
    mp = (marker_phase if marker_phase is not None else phase0) * t_pwm_n
    for i in range(datalen):
        # PA4: скважность 50% от period t_pwm_n -> период toggle = t_pwm_n
        ch4.append(1.0 if (i % t_pwm_n) < half else 0.0)
        # PA5: узкий импульс в фазе mp каждые t_pwm_n
        ph = (i % t_pwm_n)
        ch3.append(1.0 if abs(ph - mp) < 2 else 0.0)
    # zero-mean треугольный ripple: (tri-0.5) in [-0.5, 0.5] -> pp = ripple_pp_v
    ch1 = [dc_v + ripple_pp_v * (tri(i, t_pwm_n) - 0.5)
           for i in range(datalen)]
    ch2 = [dc_v * 1.05 + ripple_pp_v * (tri(i + 37, t_pwm_n) - 0.5)
           for i in range(datalen)]
    return {"CH1": ch1, "CH2": ch2, "CH3": ch3, "CH4": ch4}


class FakeScope:
    def __init__(self, frames, head=None, fail=None):
        self.frames = frames
        self.head_data = head or make_head()
        self.fail = fail or {}
        self.k = 0

    def identify(self):
        return IDN

    def head(self):
        if self.fail.get("head"):
            raise RuntimeError("bulk failed")
        return self.head_data

    def waveform_codes(self, ch):
        if self.fail.get("wave_" + ch):
            raise RuntimeError("wave failed")
        codes = self.frames[self.k % len(self.frames)][ch]
        if ch == "CH4":                      # конец раунда из 4 каналов -> следующий кадр
            self.k += 1
        # volts->codes обратно: в тесте подаём вольты как «коды» с OFFSET=-100,
        # 100mV/div: v = (c+100-305.5)*0.004 -> c = v/0.004 + 205.5
        meta = next(c for c in self.head_data["CHANNEL"] if c["NAME"] == ch)
        scale = {"100mV": 0.004, "1V": 0.04}[meta["SCALE"]]
        return [int(round(v / scale + 205.5)) for v in codes]

    def close(self):
        pass


class NoStm:
    def read_adc(self, wait_s=1.5):
        return None

    def close(self):
        pass


def capture_dir(tmp_path, frames, head=None):
    d = tmp_path / "cap"
    rep = sq.run_capture(FakeScope(frames, head), NoStm(), d,
                         {"expect": {}, "frames": len(frames),
                          "firmware_sha256": "abc"})
    assert rep["verdict"] == "CAPTURED"
    return d


# ---------------------------------------------------------------- gate CH3/CH4
def test_gate_blocks_when_marker_channels_absent(tmp_path):
    head = make_head()
    head["CHANNEL"] = head["CHANNEL"][:2]          # только CH1/CH2
    sc = FakeScope([synth_frame()], head)
    rep = sq.run_capture(sc, NoStm(), tmp_path / "out",
                         {"expect": {}, "frames": 1})
    assert rep["verdict"] == SCOPE_CONTROL_BLOCKED
    assert rep["verdict_reason"] == "CH3_absent_in_head"


def test_gate_blocks_when_marker_display_off(tmp_path):
    head = make_head()
    for c in head["CHANNEL"]:
        if c["NAME"] == "CH4":
            c["DISPLAY"] = "OFF"
    rep = sq.run_capture(FakeScope([synth_frame()], head), NoStm(),
                         tmp_path / "out", {"expect": {}, "frames": 1})
    assert rep["verdict"] == SCOPE_CONTROL_BLOCKED
    assert rep["verdict_reason"] == "CH4_display_off"


def test_gate_all_zero_marker_blocks(tmp_path):
    frames = synth_frame()
    frames["CH4"] = [0.0] * len(frames["CH4"])     # мёртвый маркер TRGO
    # waveform all-zero проверяется на КОДАХ; нулевые вольты -> код 206, не 0,
    # поэтому вместо этого ломаем длину: len != DATALEN
    frames_bad = synth_frame()
    frames_bad["CH4"] = [0.0] * 100
    sc = FakeScope([frames_bad], make_head())
    rep = sq.run_capture(sc, NoStm(), tmp_path / "out", {"expect": {}, "frames": 1})
    assert rep["verdict"] == SCOPE_CONTROL_BLOCKED
    assert rep["verdict_reason"] == "CH4_waveform_len_mismatch"


# ---------------------------------------------------------------- analyze
def test_analyze_known_geometry_qualified(tmp_path):
    # 5 кадров: фаза маркера дрейфует на 0.0002 доли периода за кадр (~0.7 мкс)
    frames = [synth_frame(phase0=0.30 + 0.0002 * k) for k in range(5)]
    d = capture_dir(tmp_path, frames)
    rep = sq.run_analyze(d, {"rshunt": 0.03,
                             "max_phase_jitter_s": 3.3e-6,
                             "max_phase_drift_s": 3.3e-6,
                             "max_period_jitter_pct": 0.5,
                             "max_error_frames_pct": 5.0})
    assert rep["verdict"] == "SAMPLING_QUALIFIED", rep["verdict_reason"]
    tq = rep["timing_qualification"]
    # период: 304 сэмпла * 3.2895 us = 1.0 ms -> 1000 Hz
    assert tq["f_pwm_hz_mean"] == pytest.approx(1000.0, rel=0.02)
    assert tq["phase_jitter_s_max"] < 3.3e-6
    assert tq["phase_crossframe_drift_s"] < 3.3e-6
    assert rep["frames_with_errors"] == 0
    # A/B: B сознательно не заявлен
    assert rep["current_reconstruction"]["claim"] == "NOT_CLAIMED"


def test_analyze_frame_current_at_marker_and_ripple(tmp_path):
    v = synth_frame(dc_v=0.033, ripple_pp_v=0.020)
    volts = {"CH1": v["CH1"], "CH2": v["CH2"], "CH3": v["CH3"], "CH4": v["CH4"]}
    res = sq.analyze_frame(volts, 3.289473684210527e-6, 0.03)
    assert "error" not in res
    assert res["pwm"]["f_pwm_hz"] == pytest.approx(1000.0, rel=0.01)
    # DC ток: 33 мВ / 0.03 Ом = 1100 мА; ток в маркере — внутри DC±ripple
    assert res["ch1"]["i_mean_ma"] == pytest.approx(1100.0, abs=1e-6)
    assert abs(res["ch1"]["i_at_marker_ma_mean"] - 1100.0) <= 0.020 / 0.03 / 2 * 1000 + 1
    # ripple pp: 20 мВ / 0.03 = 666.7 мА
    assert res["ch1"]["ripple_pp_ma_mean"] == pytest.approx(666.67, rel=0.05)
    assert res["ch2"]["ripple_pp_ma_mean"] == pytest.approx(666.67, rel=0.05)


def test_analyze_suspect_on_phase_jitter(tmp_path):
    # межкадровый дрейф фазы 0.10 доли периода за кадр (~100 мкс) -> SUSPECT
    frames = [synth_frame(phase0=0.30 + 0.10 * k) for k in range(4)]
    d = capture_dir(tmp_path, frames)
    rep = sq.run_analyze(d, {"rshunt": 0.03, "max_phase_jitter_s": 3.3e-6,
                             "max_phase_drift_s": 3.3e-6,
                             "max_period_jitter_pct": 0.5,
                             "max_error_frames_pct": 5.0})
    assert rep["verdict"] == "SAMPLING_SUSPECT"
    assert "phase_crossframe_drift" in rep["verdict_reason"]


def test_analyze_suspect_on_1sample_drift(tmp_path):
    # дрейф маркера на 1 сэмпл между кадрами = 3.29 мкс > порога 3.0e-6
    frames = [synth_frame(phase0=0.30),
              synth_frame(phase0=0.30 + 1.0 / 304.0)]
    d = capture_dir(tmp_path, frames)
    rep = sq.run_analyze(d, {"rshunt": 0.03, "max_phase_jitter_s": 3.0e-6,
                             "max_phase_drift_s": 3.0e-6,
                             "max_period_jitter_pct": 0.5,
                             "max_error_frames_pct": 5.0})
    assert rep["verdict"] == "SAMPLING_SUSPECT"
    assert "phase_crossframe_drift" in rep["verdict_reason"]


def test_analyze_suspect_on_missing_marker(tmp_path):
    frames = synth_frame()
    frames["CH3"] = [0.0] * len(frames["CH3"])     # нет фронтов PA5
    d = capture_dir(tmp_path, [frames])
    rep = sq.run_analyze(d, {"rshunt": 0.03, "max_phase_jitter_s": 3.3e-6,
                             "max_phase_drift_s": 3.3e-6,
                             "max_period_jitter_pct": 0.5,
                             "max_error_frames_pct": 5.0})
    assert rep["verdict"] == "SAMPLING_SUSPECT"
    assert rep["frames_with_errors"] == 1


def test_analyze_requires_captured(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SystemExit):
        sq.run_analyze(d, {"rshunt": 0.03, "max_phase_jitter_s": 1e-6,
                           "max_period_jitter_pct": 0.5,
                           "max_error_frames_pct": 5.0})


def test_interpolation_no_extrapolation():
    v = [1.0, 2.0, 3.0]
    assert sq.interp_at(v, 0) == 1.0
    assert sq.interp_at(v, 1.5) == 2.5
    assert sq.interp_at(v, 10) == 3.0
    assert sq.interp_at(v, -5) == 1.0


# ---------------------------------------------------------------- provenance
def test_provenance_emit_check_with_extra_define(tmp_path):
    import test3_build_provenance_emit as emit
    import test3_build_provenance_check as check

    fw = tmp_path / "build-ba" / "firmware.bin"
    fw.parent.mkdir(parents=True)
    fw.write_bytes(b"\x00\x01\x02FW")
    rc = emit.main_safe if hasattr(emit, "main_safe") else None
    # emit.main() читает sys.argv — используем подмену
    import sys as _sys
    old = _sys.argv
    _sys.argv = ["emit", "--campaign-root", str(fw.parent),
                 "--source-sha", "a" * 40,
                 "--firmware", str(fw),
                 "--define", "OEW_BENCH_APERTURE=1"]
    try:
        assert emit.main() == 0
    finally:
        _sys.argv = old
    man = json.loads((fw.parent / "diagnostic_build_manifest.json").read_text())
    assert man["defines"]["OEW_BENCH_APERTURE"] == "1"
    assert man["defines"]["OEW_HS1_COMMISSIONING_RELEASE"] == "1"
    # check без --define: базовые проверки ок (extra define не обязателен)
    checks = check.provenance_checks(fw.parent)
    assert all(p for _, p, _ in checks)
    # check с --define: тоже ок
    checks = check.provenance_checks(fw.parent, {"OEW_BENCH_APERTURE": "1"})
    assert all(p for _, p, _ in checks)
    # а с неверным значением — падает именно define-проверка
    checks = check.provenance_checks(fw.parent, {"OEW_BENCH_APERTURE": "0"})
    bad = [c for c in checks if c[0] == "define-OEW_BENCH_APERTURE"]
    assert bad and not bad[0][1]


def test_capture_report_schema_and_crc(tmp_path):
    d = capture_dir(tmp_path, [synth_frame(), synth_frame()])
    rep = json.loads((d / "capture_report.json").read_text("utf-8"))
    assert rep["schema"] == "sampling-qual-capture-v1"
    assert rep["dataset_crc32"]
    assert len(rep["frames"]) == 2
    assert len(rep["frames"][0]["files"]) == 4
    assert rep["marker_note"].startswith("PA5=")
