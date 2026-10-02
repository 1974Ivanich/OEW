#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sampling_qual.py — Уровень 2: квалификация ADC sampling window
(TZ_SHUNT_SAMPLING_QUALIFICATION_PC3.md, реализация спеки cd51b8e+).

Синхронизация — самим осциллографом: один 4-канальный SCREEN-кадр
  CH1/CH2 → шунты Inv1/Inv2
  CH3     → PA5 (toggle в ADC ISR при JEOS: маркер конца выборки фрейма)
  CH4     → PA4 (toggle в TIM1_UP ISR: маркер источника TRGO/UIF)
Прошивка — bench-aperture (commissioning defines + OEW_BENCH_APERTURE=1),
runtime-логика не изменена.

Режимы:
  capture  — гейт (CH1..CH4, DISPLAY ON, readback ожиданий), N≥32 кадров,
             waveform целиком в CSV + @ADC телеметрия в provenance.
  analyze  — offline из каталога capture: период PWM (PA4), фаза JEOS-маркера
             в PWM-периоде и её джиттер, ток шунта в точке маркера (интерполяция),
             средний ток, ripple pp/rms за период, сравнение Inv1/Inv2.
             Никакой подгонки фазы: геометрия следует ТОЛЬКО из PA4/PA5/waveform.

Формулировка результата (спека v2): временнАя привязка «ADC event → ISR marker»
с ISR-хвостом как измеренной константой сборки — НЕ субмикросекундный момент
выборки. Вердикт покрывает только утверждение A (timing qualification):
  A. SAMPLING_QUALIFIED / SAMPLING_SUSPECT — стабильность фазы/периода/джиттера.
Утверждение B (current reconstruction / пригодность для FOC map) этим
инструментом НЕ заявляется: поле current_reconstruction.claim = NOT_CLAIMED.

Пороги вердикта — параметры CLI, не константы. analysis_version фиксируется в
отчёте; provenance: firmware sha, scope IDN/HEAD, timebase, конфиг каналов,
capture CRC.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from shunt_cal_campaign import (  # noqa: E402
    SCOPE_CONTROL_BLOCKED,
    ScopeAdapter,
    Stm32Adapter,
    chan_meta,
    codes_to_volts,
    dataset_crc32,
    num,
    run_capability_gate,
    utc_now,
    write_json,
)

ANALYSIS_VERSION = "sampling_qual-1.0"
ALL_CHANNELS = ("CH1", "CH2", "CH3", "CH4")
MARKER_CHANNELS = ("CH3", "CH4")


# ---------------------------------------------------------------- capture
def run_capture(sc, stm32, out: Path, opts: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "sampling-qual-capture-v1",
        "started": utc_now(),
        "phase": "GATE",
        "verdict": None,
        "verdict_reason": None,
        "frames_requested": opts["frames"],
        "frames": [],
        "analysis_version": ANALYSIS_VERSION,
        "firmware_sha256": opts.get("firmware_sha256"),
        "marker_note": "PA5=ADC ISR toggle (JEOS), PA4=TIM1_UP ISR toggle (TRGO/UIF); "
                       "результат = ADC event → ISR marker, ISR-хвост фиксируется как константа",
    }
    expect = dict(opts["expect"])
    expect.setdefault("channels", ALL_CHANNELS)
    gate = run_capability_gate(sc, expect)
    report["capability_gate"] = gate
    report["scope_identity"] = {"idn": gate.get("idn"),
                                "model": (gate.get("head") or {}).get("MODEL")}
    if gate["verdict"] != "PASS":
        report["phase"] = "BLOCKED"
        report["verdict"] = SCOPE_CONTROL_BLOCKED
        report["verdict_reason"] = gate["blocked_reason"]
        report["finished"] = utc_now()
        write_json(out / "capture_report.json", report)
        print("GATE BLOCKED: %s" % gate["blocked_reason"])
        return report
    print("Capability gate: PASS (%d каналов, %d шагов)" % (
        len(expect["channels"]), len(gate["steps"])))

    head = gate["head"]
    datalen = int(head["SAMPLE"]["DATALEN"])
    dt = 10.0 * num(head.get("TIMEBASE", {}).get("SCALE")) / datalen
    report["frame_dt_s"] = dt
    metas = {ch: chan_meta(head, ch) for ch in ALL_CHANNELS}

    for k in range(opts["frames"]):
        codes = {ch: sc.waveform_codes(ch) for ch in ALL_CHANNELS}
        tel = stm32.read_adc() if stm32 is not None else None
        files = []
        for ch in ALL_CHANNELS:
            p = out / ("frame_%03d_%s.csv" % (k, ch))
            n = len(codes[ch])
            volts = codes_to_volts(codes[ch], metas[ch])
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("t_s,volts,code\n")
                for i, (c, v) in enumerate(zip(codes[ch], volts)):
                    f.write("%.9g,%.9g,%d\n" % (i * dt, v, c))
            files.append(p.name)
        report["frames"].append({
            "index": k, "timestamp": utc_now(), "files": files,
            "datalen": datalen, "stm32_telemetry": tel,
        })
        write_json(out / "capture_report.json", report)  # crash-resilient
        print("  frame %d/%d" % (k + 1, opts["frames"]))

    names = [f for fr in report["frames"] for f in fr["files"]]
    if (out / "uart_session.log").exists():
        names.append("uart_session.log")
    report["dataset_crc32"] = dataset_crc32(out, names)
    report["phase"] = "DONE"
    report["verdict"] = "CAPTURED"
    report["verdict_reason"] = "%d кадров; анализ — mode analyze" % len(report["frames"])
    report["finished"] = utc_now()
    write_json(out / "capture_report.json", report)
    print("Verdict: CAPTURED (%d кадров), dataset_crc32=%s" % (
        len(report["frames"]), report["dataset_crc32"]))
    return report


# ---------------------------------------------------------------- analysis
def find_edges(codes: Sequence[float], min_amp: float = 0.5):
    """Фронты маркера: пересечение середины размаха. Возвращает ([(индекс, rising)], размах).
    min_amp — в тех же единицах, что вход (анализ работает в вольтах; маркеры
    PA4/PA5 дают rail-to-rail ~3.3 В, порог 0.5 В отсекает шум)."""
    lo, hi = min(codes), max(codes)
    amp = hi - lo
    if amp < min_amp:
        return [], amp
    mid = (lo + hi) / 2.0
    edges = []
    state = codes[0] > mid
    for i, c in enumerate(codes):
        s = c > mid
        if s != state:
            edges.append((i, s))   # s=True — восходящий фронт
            state = s
    return edges, amp


def interp_at(volts: Sequence[float], idx: float) -> float:
    """Линейная интерполяция volts в дробном индексе (без экстраполяции)."""
    if idx <= 0:
        return volts[0]
    if idx >= len(volts) - 1:
        return volts[-1]
    i = int(idx)
    frac = idx - i
    return volts[i] * (1.0 - frac) + volts[i + 1] * frac


def analyze_frame(frame_volts: dict, dt: float, rshunt: float) -> dict:
    """Геометрия одного кадра. Только данные, без подгонки.

    Период PWM — по ВОСХОДЯЩИМ фронтам PA4 (toggle даёт 2 фронта за период;
    соседние фронты — это полпериода при 50% скважности). Фаза маркера PA5 —
    тоже по восходящим фронтам (у импульса маркера два фронта за период)."""
    res = {}
    e4, amp4 = find_edges(frame_volts["CH4"])
    rising4 = [i for (i, r) in e4 if r]
    if len(rising4) < 2:
        return {"error": "pa4_marker_no_edges", "pa4_amp_codes_volts": amp4}
    periods = [(b - a) * dt for a, b in zip(rising4, rising4[1:])]
    t_pwm = statistics.fmean(periods)
    res["pwm"] = {"period_s": t_pwm, "f_pwm_hz": 1.0 / t_pwm,
                  "period_jitter_pct": 100.0 * (max(periods) - min(periods)) / t_pwm,
                  "edges": len(rising4)}
    e3, amp3 = find_edges(frame_volts["CH3"])
    rising3 = [i for (i, r) in e3 if r]
    if not rising3:
        res["error"] = "pa5_marker_no_edges"
        res["pa5_amp_codes_volts"] = amp3
        return res
    # фаза JEOS-маркера относительно опорного восходящего фронта PA4 (mod T_pwm)
    t0 = rising4[0] * dt
    phases = [(((i * dt) - t0) % t_pwm) / t_pwm for i in rising3]
    res["sample_marker"] = {
        "edges": len(rising3),
        "phase_in_period_mean": statistics.fmean(phases),
        "phase_in_period_min": min(phases),
        "phase_in_period_max": max(phases),
        "phase_jitter_s": (max(phases) - min(phases)) * t_pwm,
        "note": "ADC event → ISR marker (PA5); ISR-хвост включён в фазу",
    }
    # ток в точке маркера (интерполяция), средний ток, ripple за полный период
    for tag, ch in (("ch1", "CH1"), ("ch2", "CH2")):
        mv = [v * 1000.0 for v in frame_volts[ch]]
        i_mean_ma = statistics.fmean(mv) / rshunt
        at = []
        for p in phases:
            idx = ((t0 + p * t_pwm) / dt) % len(mv)
            at.append(interp_at(mv, idx) / rshunt)
        ripple = []
        for a, b in zip(rising4, rising4[1:]):
            seg = mv[a:b]
            if len(seg) > 1:
                ripple.append((max(seg) - min(seg)) / rshunt)
        res[tag] = {
            "i_mean_ma": i_mean_ma,
            "i_at_marker_ma_mean": statistics.fmean(at),
            "i_at_marker_ma_min": min(at),
            "i_at_marker_ma_max": max(at),
            "ripple_pp_ma_mean": statistics.fmean(ripple) if ripple else None,
        }
    return res


def run_analyze(cap_dir: Path, opts: dict) -> dict:
    cap_path = cap_dir / "capture_report.json"
    if not cap_path.exists():
        raise SystemExit("%s не найден — сначала режим capture" % cap_path)
    cap = __import__("json").loads(cap_path.read_text(encoding="utf-8"))
    if cap.get("verdict") != "CAPTURED":
        raise SystemExit("capture_report.json: verdict=%s — анализировать нечего"
                         % cap.get("verdict"))
    dt = float(cap["frame_dt_s"])
    per_frame = []
    for fr in cap["frames"]:
        volts = {}
        ok = True
        for ch in ALL_CHANNELS:
            path = cap_dir / ("frame_%03d_%s.csv" % (fr["index"], ch))
            if not path.exists():
                ok = False
                break
            rows = path.read_text(encoding="utf-8").strip().splitlines()[1:]
            volts[ch] = [float(r.split(",")[1]) for r in rows]
        if not ok:
            per_frame.append({"index": fr["index"], "error": "missing_files"})
            continue
        per_frame.append({"index": fr["index"],
                          **analyze_frame(volts, dt, opts["rshunt"])})
    good = [f for f in per_frame if "error" not in f]
    errors = [f for f in per_frame if "error" in f]

    report = {
        "schema": "sampling-qual-analysis-v1",
        "analysis_version": ANALYSIS_VERSION,
        "generated": utc_now(),
        "capture_dir": str(cap_dir),
        "frames_analyzed": len(good),
        "frames_with_errors": len(errors),
        "frame_errors": [f for f in errors],
        "thresholds": {k: opts[k] for k in
                       ("max_phase_jitter_s", "max_phase_drift_s",
                        "max_period_jitter_pct", "max_error_frames_pct")},
        "firmware_sha256": opts.get("firmware_sha256") or cap.get("firmware_sha256"),
        "scope_identity": cap.get("scope_identity"),
        "rshunt_ohm": opts["rshunt"],
        "timing_qualification": {},   # утверждение A
        "current_reconstruction": {   # утверждение B — сознательно НЕ заявляется
            "claim": "NOT_CLAIMED",
            "reason": "A/B разделение: нужен sample timing + PWM state/sector "
                      "+ фактический shunt current + reconstruction/map — отдельный gate",
        },
        "verdict": None,
        "verdict_reason": None,
    }
    if not good:
        report["verdict"] = "SAMPLING_SUSPECT"
        report["verdict_reason"] = "нет ни одного разбираемого кадра"
        return report

    jit = [f["sample_marker"]["phase_jitter_s"] for f in good]
    pj = [f["pwm"]["period_jitter_pct"] for f in good]
    fpwm = [f["pwm"]["f_pwm_hz"] for f in good]
    err_pct = 100.0 * len(errors) / len(per_frame)
    # межкадровая стабильность фазы: дрейф среднего фаз между кадрами
    ph_means = [f["sample_marker"]["phase_in_period_mean"] for f in good]
    t_pwms = [f["pwm"]["period_s"] for f in good]
    drift_s = (max(ph_means) - min(ph_means)) * statistics.fmean(t_pwms)
    report["timing_qualification"] = {
        "claim": "A: ADC sampling event стабилен в ожидаемой части PWM-периода",
        "f_pwm_hz_mean": statistics.fmean(fpwm),
        "phase_jitter_s_max": max(jit),
        "phase_crossframe_drift_s": drift_s,
        "period_jitter_pct_max": max(pj),
        "error_frames_pct": err_pct,
        "marker_note": "фаза включает ISR-хвост PA5; подгонка фазы не выполнялась",
    }
    reasons = []
    if max(jit) > opts["max_phase_jitter_s"]:
        reasons.append("phase_jitter_s_max=%.3g > %.3g" % (
            max(jit), opts["max_phase_jitter_s"]))
    if drift_s > opts["max_phase_drift_s"]:
        reasons.append("phase_crossframe_drift_s=%.3g > %.3g" % (
            drift_s, opts["max_phase_drift_s"]))
    if max(pj) > opts["max_period_jitter_pct"]:
        reasons.append("period_jitter_pct_max=%.3g > %.3g" % (
            max(pj), opts["max_period_jitter_pct"]))
    if err_pct > opts["max_error_frames_pct"]:
        reasons.append("error_frames_pct=%.1f > %.1f" % (
            err_pct, opts["max_error_frames_pct"]))
    if reasons:
        report["verdict"] = "SAMPLING_SUSPECT"
        report["verdict_reason"] = "; ".join(reasons)
    else:
        report["verdict"] = "SAMPLING_QUALIFIED"
        report["verdict_reason"] = ("джиттер фазы/периода и ошибки кадров в порогах "
                                    "(%d кадров)" % len(good))
    return report


# ---------------------------------------------------------------- CLI
def build_argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Уровень 2: sampling qualification")
    sub = p.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("capture", help="гейт + N 4-канальных кадров")
    c.add_argument("--out", required=True)
    c.add_argument("--frames", type=int, default=32)
    c.add_argument("--port", default=None)
    c.add_argument("--baud", type=int, default=115200)
    c.add_argument("--expect-scale-ch1", default=None)
    c.add_argument("--expect-scale-ch2", default=None)
    c.add_argument("--expect-scale-ch3", default=None)
    c.add_argument("--expect-scale-ch4", default=None)
    c.add_argument("--expect-probe-ch1", default=None)
    c.add_argument("--expect-probe-ch2", default=None)
    c.add_argument("--expect-probe-ch3", default=None)
    c.add_argument("--expect-probe-ch4", default=None)
    c.add_argument("--scale-tolerance-pct", type=float, default=15.0)
    c.add_argument("--firmware-sha256", default=None)
    a = sub.add_parser("analyze", help="offline-анализ каталога capture")
    a.add_argument("capture_dir")
    a.add_argument("--rshunt", type=float, default=0.03)
    a.add_argument("--max-phase-jitter-s", type=float, default=3.3e-6,
                   help="default: один SCREEN-сэмпл при 500 мкс/дел")
    a.add_argument("--max-phase-drift-s", type=float, default=3.3e-6,
                   help="межкадровый дрейф среднего фазы маркера")
    a.add_argument("--max-period-jitter-pct", type=float, default=0.5)
    a.add_argument("--max-error-frames-pct", type=float, default=5.0)
    a.add_argument("--firmware-sha256", default=None)
    a.add_argument("--out", default=None, help="куда писать analysis_report.json")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argparse().parse_args(argv)
    if args.mode == "capture":
        expect = {k[len("expect_"):]: v for k, v in vars(args).items()
                  if k.startswith("expect_") and v}
        expect["scale_tolerance_pct"] = args.scale_tolerance_pct
        opts = {"expect": expect, "frames": args.frames,
                "firmware_sha256": args.firmware_sha256}
        out = Path(args.out)
        try:
            sc = ScopeAdapter()
        except Exception as e:  # noqa: BLE001
            print("SCOPE_CONTROL_BLOCKED: TAO3104A недоступен (%s)" % e)
            report = {"schema": "sampling-qual-capture-v1", "phase": "BLOCKED",
                      "verdict": SCOPE_CONTROL_BLOCKED,
                      "verdict_reason": "scope_open_failed",
                      "capability_gate": {"verdict": SCOPE_CONTROL_BLOCKED,
                                          "steps": [{"step": "usb_open", "ok": False,
                                                     "detail": str(e)}]},
                      "frames": [], "started": utc_now(), "finished": utc_now()}
            out.mkdir(parents=True, exist_ok=True)
            write_json(out / "capture_report.json", report)
            return 3
        stm32 = None
        try:
            if args.port:
                stm32 = Stm32Adapter(args.port, args.baud, out / "uart_session.log")
            report = run_capture(sc, stm32, out, opts)
        finally:
            try:
                sc.close()
            except Exception:  # noqa: BLE001
                pass
            if stm32 is not None:
                stm32.close()
        return 0 if report["verdict"] == "CAPTURED" else 3
    if args.mode == "analyze":
        opts = {"rshunt": args.rshunt,
                "max_phase_jitter_s": args.max_phase_jitter_s,
                "max_phase_drift_s": args.max_phase_drift_s,
                "max_period_jitter_pct": args.max_period_jitter_pct,
                "max_error_frames_pct": args.max_error_frames_pct,
                "firmware_sha256": args.firmware_sha256}
        report = run_analyze(Path(args.capture_dir), opts)
        out = Path(args.out) if args.out else Path(args.capture_dir)
        write_json(out / "analysis_report.json", report)
        print("Verdict: %s (%s)" % (report["verdict"], report["verdict_reason"]))
        tq = report["timing_qualification"]
        if tq:
            print("f_pwm=%.1f Гц; jitter фазы max=%.3g с; период jitter max=%.3g%%; "
                  "ошибок %.1f%%" % (tq["f_pwm_hz_mean"], tq["phase_jitter_s_max"],
                                     tq["period_jitter_pct_max"],
                                     tq["error_frames_pct"]))
        print("current_reconstruction: %s" %
              report["current_reconstruction"]["claim"])
        return 0 if report["verdict"] == "SAMPLING_QUALIFIED" else 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
