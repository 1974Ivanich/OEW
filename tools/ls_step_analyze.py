#!/usr/bin/env python3
"""Offline LS-STANDSTILL-STEP log analyzer.

This module intentionally does not claim a motor Ls measurement.  It parses the
firmware telemetry, computes the firmware-style two-point estimate and a
first-order exponential estimate, and reports measurement consistency only.

The exponential model is important for this bench: with Rs=13 ohm and
L=800 uH, tau=L/Rs is about 61.5 us while one PWM period is 200 us, so a
single-period two-point estimate is strongly biased high.  The analyzer keeps
that distinction explicit and never proposes an mp= value.

Only Python 3.11 standard-library modules are used.
"""
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

CPU_HZ_DEFAULT = 170_000_000
TCLK_MHZ_DEFAULT = 10.0
RS_MOHM_DEFAULT = 13_000
MIN_DI_DEFAULT = 50.0
DT_TOL_DEFAULT = 0.15
PERIOD_TOL_DEFAULT = 0.05
PRE_PLATEAU_DEFAULT = 100.0
CONV_TOL_DEFAULT = 0.25
LEVELS = (5, 10, 15)
L_MIN = 500.0
L_MAX = 500_000.0

START_RE = re.compile(r"^@AT:LS:START$")
LEVEL_RE = re.compile(r"^@AT:LS:LEVEL:d=(?P<d>\d+):U_eff_mv=(?P<u>[-+]?\d+):ccr_hi=(?P<hi>\d+):ccr_lo=(?P<lo>\d+):arr=(?P<arr>\d+)$")
FRAME_RE = re.compile(r"^@AT:LS:FRAME:d=(?P<d>\d+):n=(?P<n>\d+):ph=(?P<ph>[01]):t=(?P<t>\d+):I1=(?P<i1>[-+]?\d+):I2=(?P<i2>[-+]?\d+):Idiff=(?P<id>[-+]?\d+):Vbus=(?P<v>[-+]?\d+):CCR1=(?P<c1>\d+):CCR8=(?P<c8>\d+)$")
RESULT_RE = re.compile(r"^@AT:LS:RESULT:d=(?P<d>\d+):Lstep_uH=(?P<l>[-+]?\d+(?:\\.\d+)?):n_valid=(?P<n>\d+):Rs_mOhm=(?P<rs>\d+):SEMANTICS=Lstep_not_confirmed_Ls$")
FAULT_PREFIXES = (
    "@AT:LS:ERROR:OFFSETS_NOT_VALID",
    "@AT:LS:ERROR:VBUS_LOW:",
    "@AT:LS:ERROR:ADC_ARM_FAIL",
    "@AT:LS:ERROR:PWM_ARM_FAIL:",
    "@AT:LS:ERROR:OVERCURRENT ",
    "@AT:LS:ABORTED",
    "@AT:LS:WARN:RESET_FAIL:D=",
)

def parse_log(text):
    start = False
    done = False
    crlf = "\r\n" in text
    levels = {}
    results = {}
    faults = []
    for raw in text.splitlines():
        line = raw.strip()
        if START_RE.fullmatch(line):
            start = True
            continue
        m = LEVEL_RE.fullmatch(line)
        if m:
            d = int(m.group("d"))
            levels.setdefault(d, {"d": d, "u_eff_mv": int(m.group("u")),
                                  "ccr_hi": int(m.group("hi")), "ccr_lo": int(m.group("lo")),
                                  "arr": int(m.group("arr")), "frames": []})
            continue
        m = FRAME_RE.fullmatch(line)
        if m:
            d = int(m.group("d"))
            rec = {k: int(m.group(g)) for k, g in (
                ("n","n"), ("ph","ph"), ("t","t"), ("I1","i1"), ("I2","i2"),
                ("Idiff","id"), ("Vbus","v"), ("CCR1","c1"), ("CCR8","c8"))}
            levels.setdefault(d, {"d": d, "u_eff_mv": None, "ccr_hi": None,
                                  "ccr_lo": None, "arr": None, "frames": []})["frames"].append(rec)
            continue
        m = RESULT_RE.fullmatch(line)
        if m:
            results[int(m.group("d"))] = {
                "Lstep_uH": float(m.group("l")), "n_valid": int(m.group("n")),
                "Rs_mOhm": int(m.group("rs"))
            }
            continue
        if line == "@AT:LS:DONE":
            done = True
            continue
        if any(line.startswith(p) for p in FAULT_PREFIXES):
            faults.append(line)
    return start, done, levels, results, faults, crlf

def pwm_period_us(arr, tclk_mhz):
    return 2.0 * (arr + 1) / tclk_mhz

def pair_analysis(level, result, cpu_hz, tclk_mhz, rs_mohm, min_di, dt_tol):
    frames = sorted((f for f in level["frames"] if f["ph"] == 1), key=lambda x: x["n"])
    period = pwm_period_us(level["arr"], tclk_mhz) if level["arr"] is not None else None
    pairs = []
    for a, b in zip(frames, frames[1:]):
        dt = (b["t"] - a["t"]) / (cpu_hz / 1e6)
        di = b["Idiff"] - a["Idiff"]
        imid = (a["Idiff"] + b["Idiff"]) / 2.0
        item = {"n": a["n"], "dt_us": dt, "dI_ma": di, "i_mid_ma": imid,
                "dIdt_ma_us": (di / dt if dt else None), "L_uH": None,
                "accepted": False, "reason": None}
        if period is None or dt < period * (1.0 - dt_tol) or dt > 2000.0:
            item["reason"] = "REJECT:DT_OUT_OF_ENVELOPE"
        elif abs(di) < min_di:
            item["reason"] = "REJECT:DI_TOO_SMALL"
        elif di <= 0:
            item["reason"] = "REJECT:DI_NONPOSITIVE"
        elif level["u_eff_mv"] is None:
            item["reason"] = "REJECT:NO_LEVEL_LINE"
        else:
            l = (level["u_eff_mv"] - imid * rs_mohm / 1000.0) * dt / di
            item["L_uH"] = l
            if l <= 0:
                item["reason"] = "REJECT:L_NONPOSITIVE"
            else:
                item["accepted"] = True
        pairs.append(item)
    accepted = [p for p in pairs if p["accepted"]]
    lin = [p["L_uH"] for p in accepted]
    fw_threshold = [p for p in accepted if p["dI_ma"] >= 20.0]
    fw_vals = [p["L_uH"] for p in fw_threshold]
    fw_median = statistics.median(fw_vals) if fw_vals else None
    all_median = statistics.median(lin) if lin else None
    spread = ((max(lin) - min(lin)) / all_median * 100.0) if lin and all_median else None
    return pairs, accepted, {
        "firmware_median": fw_median, "all_median": all_median,
        "min": min(lin) if lin else None, "max": max(lin) if lin else None,
        "spread_pct": spread, "period_us": period
    }

def fit_tau(level, rs_mohm):
    frames = sorted((f for f in level["frames"] if f["ph"] == 1), key=lambda x: x["n"])
    if len(frames) < 3 or level["u_eff_mv"] is None or rs_mohm <= 0:
        return None
    t0 = frames[0]["t"] / 1.0
    rss = level["u_eff_mv"] / (rs_mohm / 1000.0)
    xs = [((f["t"] - t0) / 170.0) for f in frames]  # 170 CPU ticks/us
    ys = [float(f["Idiff"]) for f in frames]
    # General CPU clock conversion is applied by caller after fit if needed.
    return rss, xs, ys

def exponential_fit(level, rs_mohm, cpu_hz):
    frames = sorted((f for f in level["frames"] if f["ph"] == 1), key=lambda x: x["n"])
    if len(frames) < 3 or level["u_eff_mv"] is None or rs_mohm <= 0:
        return None
    iss = level["u_eff_mv"] / (rs_mohm / 1000.0)
    t0 = frames[0]["t"]
    xs = [(f["t"] - t0) / (cpu_hz / 1e6) for f in frames]
    ys = [float(f["Idiff"]) for f in frames]
    if iss <= 0:
        return None
    def sse(log_tau):
        tau = math.exp(log_tau)
        return sum((y - iss * (1.0 - math.exp(-x / tau))) ** 2 for x, y in zip(xs, ys))
    lo, hi = math.log(max(1e-6, max(xs) / 1000.0)), math.log(max(1.0, max(xs) * 1000.0 + 1.0))
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = hi - gr * (hi - lo); d = lo + gr * (hi - lo)
    fc, fd = sse(c), sse(d)
    for _ in range(100):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo); fc = sse(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo); fd = sse(d)
    tau = math.exp((lo + hi) / 2.0)
    residual = math.sqrt(sse(math.log(tau)) / len(xs))
    return {"L_fit_uH": tau * rs_mohm / 1000.0, "tau_us": tau,
            "i_ss_ma": iss, "n_points": len(xs), "residual_rms_ma": residual}

def level_checks(level, pairs, accepted, pre_plateau, result, rs_mohm):
    checks = {}
    pre = [f["Idiff"] for f in level["frames"] if f["ph"] == 0]
    step = sorted((f for f in level["frames"] if f["ph"] == 1), key=lambda x: x["n"])
    if pre:
        trend = abs(pre[-1] - pre[0])
        plateau_ok = max(abs(x) for x in pre) <= pre_plateau and trend <= pre_plateau
        pre_max = max(abs(x) for x in pre)
        threshold = 10.0 * pre_max
        front_ok = any(abs(f["Idiff"]) >= threshold for f in step) if step else False
        checks["SHAPE"] = "PASS" if plateau_ok and front_ok else "FAIL"
    else:
        checks["SHAPE"] = "FAIL"
    signs = [p["dI_ma"] for p in accepted]
    checks["SIGN"] = "PASS" if signs and all(x > 0 for x in signs) else "FAIL"
    return checks

def analyze(text, path="<stdin>", cpu_hz=CPU_HZ_DEFAULT, tclk_mhz=TCLK_MHZ_DEFAULT,
            rs_mohm=RS_MOHM_DEFAULT, min_di=MIN_DI_DEFAULT, dt_tol=DT_TOL_DEFAULT,
            period_tol=PERIOD_TOL_DEFAULT, pre_plateau=PRE_PLATEAU_DEFAULT,
            conv_tol=CONV_TOL_DEFAULT):
    start, done, levels, results, faults, crlf = parse_log(text)
    if not start:
        return {"tool":"ls_step_analyze","schema":"tz-ls-step-analyze-1",
                "data_integrity":{"start":False,"done":done,"fault_lines":faults,"levels_seen":sorted(levels)},
                "verdict":"UNREADABLE","reasons":["VERDICT=UNREADABLE: no @AT:LS:START"], "_exit":2}
    out_levels = []
    level_medians, fit_medians, conds = [], [], []
    for d in LEVELS:
        lev = levels.get(d)
        if not lev:
            out_levels.append({"d":d,"status":"INCOMPLETE"})
            continue
        r = results.get(d, {})
        rs = r.get("Rs_mOhm", rs_mohm)
        pairs, accepted, stats = pair_analysis(lev, r, cpu_hz, tclk_mhz, rs, min_di, dt_tol)
        fit = exponential_fit(lev, rs, cpu_hz)
        checks = level_checks(lev, pairs, accepted, pre_plateau, r, rs)
        meddt = statistics.median([p["dt_us"] for p in pairs if p["reason"] != "REJECT:DT_OUT_OF_ENVELOPE"]) if pairs else None
        cond = (fit["tau_us"] / meddt) if fit and meddt else None
        if stats["all_median"] is not None: level_medians.append(stats["all_median"])
        if fit: fit_medians.append(fit["L_fit_uH"])
        if cond is not None: conds.append(cond)
        mismatch = None
        if stats["firmware_median"] is not None and "Lstep_uH" in r and r["Lstep_uH"]:
            mismatch = abs(stats["firmware_median"] - r["Lstep_uH"]) / r["Lstep_uH"] * 100.0
        out_levels.append({
            "d":d,"u_eff_mv":lev["u_eff_mv"],"arr":lev["arr"],
            "pwm_period_us":stats["period_us"],"n_frames":len(lev["frames"]),
            "firmware":{"Lstep_uH":r.get("Lstep_uH"),"n_valid":r.get("n_valid"),
                        "median_lin_uH":stats["firmware_median"],"mismatch_pct":mismatch},
            "all_pairs":{"median_lin_uH":stats["all_median"],"min_uH":stats["min"],
                         "max_uH":stats["max"],"spread_pct":stats["spread_pct"],
                         "n_accepted":len(accepted),"n_rejected":len(pairs)-len(accepted)},
            "fit":fit,"cond":cond,"pairs":pairs,"checks":checks
        })
    period_values = [p["dt_us"] for lev in out_levels for p in lev.get("pairs",[]) if p["reason"] != "REJECT:DT_OUT_OF_ENVELOPE"]
    expected_periods = [lev["pwm_period_us"] for lev in out_levels if lev.get("pwm_period_us")]
    med_dt = statistics.median(period_values) if period_values else None
    model_period = statistics.median(expected_periods) if expected_periods else None
    checks = {}
    checks["PWM_PERIOD"] = "PASS" if med_dt is not None and model_period and abs(med_dt-model_period)/model_period <= period_tol else "FAIL:PERIOD_MODEL_MISMATCH"
    sign_values = [p["dI_ma"] for lev in out_levels for p in lev.get("pairs",[]) if p["accepted"]]
    checks["SIGN"] = "PASS" if sign_values and all(x > 0 for x in sign_values) else "FAIL"
    shape_values = [lev.get("checks",{}).get("SHAPE") for lev in out_levels]
    checks["SHAPE"] = "PASS" if len(shape_values)==3 and all(x=="PASS" for x in shape_values) else "FAIL"
    checks["CONVERGENCE"] = "PASS"
    if len(level_medians) == 3:
        avg = statistics.mean(level_medians)
        if avg <= 0 or max(abs(x-y) for x in level_medians for y in level_medians) / avg > conv_tol:
            checks["CONVERGENCE"] = "FAIL"
    else:
        checks["CONVERGENCE"] = "FAIL"
    checks["PLAUSIBILITY"] = "PASS"
    if len(fit_medians) != 3 or any(x < L_MIN or x > L_MAX for x in fit_medians):
        checks["PLAUSIBILITY"] = "FAIL:OUT_OF_RANGE"
    checks["CONDITIONING"] = "PASS"
    if len(conds) != 3 or any(c < 0.5 for c in conds):
        checks["CONDITIONING"] = "FAIL:INSUFFICIENT_TRANSIENT"
    reasons = []
    if any(f.startswith("@AT:LS:WARN:RESET_FAIL:") for f in faults):
        reasons.append("WARN:RESET_FAIL")
    if any(lev.get("firmware",{}).get("mismatch_pct") is not None and lev["firmware"]["mismatch_pct"] > 1.0 for lev in out_levels):
        reasons.append("WARN:FIRMWARE_MISMATCH")
    if checks["PWM_PERIOD"] != "PASS": reasons.append("PWM_PERIOD: FAIL:PERIOD_MODEL_MISMATCH")
    if checks["SIGN"] != "PASS": reasons.append("SIGN: FAIL")
    if checks["SHAPE"] != "PASS": reasons.append("SHAPE: FAIL")
    if checks["CONVERGENCE"] != "PASS": reasons.append("CONVERGENCE: FAIL")
    if checks["PLAUSIBILITY"] != "PASS": reasons.append("PLAUSIBILITY: FAIL:OUT_OF_RANGE")
    if checks["CONDITIONING"] != "PASS": reasons.append("CONDITIONING: FAIL:INSUFFICIENT_TRANSIENT")
    if faults:
        reasons.insert(0, "INCOMPLETE: " + faults[0])
    complete = done and not faults and all(lev.get("status") != "INCOMPLETE" for lev in out_levels) and all(
        lev.get("all_pairs",{}).get("n_accepted",0) > 0 and lev.get("fit") for lev in out_levels)
    verdict = "USABLE" if complete and all(v=="PASS" for v in checks.values()) else "UNUSABLE"
    if not done or faults:
        verdict = "INCOMPLETE"
    return {"tool":"ls_step_analyze","schema":"tz-ls-step-analyze-1",
            "env":{"cpu_hz":cpu_hz,"tclk_mhz":tclk_mhz,"rs_mohm":rs_mohm,
                   "min_di_ma":min_di,"dt_tol":dt_tol,"period_tol":period_tol,
                   "pre_plateau_ma":pre_plateau,"conv_tol":conv_tol},
            "levels":out_levels,
            "checks":checks,
            "data_integrity":{"start":True,"done":done,"fault_lines":faults,
                              "levels_seen":sorted(levels)},
            "verdict":verdict,"reasons":reasons,"_exit":0 if verdict=="USABLE" else 1}

def render(report, path):
    lines = ["=== LS-STANDSTILL-STEP analyzer ===",
             f"log: {path}"]
    di = report.get("data_integrity",{})
    lines.append(f"start={'yes' if di.get('start') else 'no'} done={'yes' if di.get('done') else 'no'} faults={len(di.get('fault_lines',[]))}")
    for lev in report.get("levels",[]):
        if "all_pairs" not in lev:
            lines.append(f"level d={lev['d']}% INCOMPLETE")
            continue
        lines.append(f"level d={lev['d']}% U_eff={lev['u_eff_mv']} mV arr={lev['arr']} period={lev['pwm_period_us']:.1f} us frames={lev['n_frames']}")
        fw=lev["firmware"]; ap=lev["all_pairs"]; fit=lev["fit"]
        lines.append(f"  firmware: Lstep={fw['Lstep_uH']} uH n_valid={fw['n_valid']} median_lin={fw['median_lin_uH']}")
        lines.append(f"  all_pairs: median={ap['median_lin_uH']} min={ap['min_uH']} max={ap['max_uH']} accepted={ap['n_accepted']} rejected={ap['n_rejected']}")
        lines.append(f"  fit: L_fit={fit['L_fit_uH']:.2f} uH tau={fit['tau_us']:.2f} us I_ss={fit['i_ss_ma']:.2f} mA points={fit['n_points']} rms={fit['residual_rms_ma']:.2f} mA")
        lines.append(f"  conditioning: {lev['cond']:.3f}  SIGN={lev['checks']['SIGN']} SHAPE={lev['checks']['SHAPE']}")
        for p in lev["pairs"]:
            if not p["accepted"]:
                lines.append(f"  rejected: n={p['n']} dt={p['dt_us']:.2f} us {p['reason']}")
    for k,v in report.get("checks",{}).items(): lines.append(f"{k}: {v}")
    lines.append(f"VERDICT: {report.get('verdict')}")
    for r in report.get("reasons",[]): lines.append(f"  - {r}")
    return "\n".join(lines)

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--json")
    ap.add_argument("--cpu-hz",type=float,default=CPU_HZ_DEFAULT)
    ap.add_argument("--tclk-mhz",type=float,default=TCLK_MHZ_DEFAULT)
    ap.add_argument("--rs-mohm",type=float,default=RS_MOHM_DEFAULT)
    ap.add_argument("--min-di-ma",type=float,default=MIN_DI_DEFAULT)
    ap.add_argument("--dt-tol",type=float,default=DT_TOL_DEFAULT)
    ap.add_argument("--period-tol",type=float,default=PERIOD_TOL_DEFAULT)
    ap.add_argument("--pre-plateau-ma",type=float,default=PRE_PLATEAU_DEFAULT)
    ap.add_argument("--conv-tol",type=float,default=CONV_TOL_DEFAULT)
    ns=ap.parse_args(argv)
    try:
        data=Path(ns.log).read_text(encoding="utf-8",errors="replace")
        report=analyze(data,ns.log,ns.cpu_hz,ns.tclk_mhz,ns.rs_mohm,ns.min_di_ma,ns.dt_tol,ns.period_tol,ns.pre_plateau_ma,ns.conv_tol)
        if ns.json:
            payload=dict(report); payload.pop("_exit",None)
            Path(ns.json).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
        print(render(report,ns.log))
        return report["_exit"]
    except (OSError, ValueError, re.error) as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
