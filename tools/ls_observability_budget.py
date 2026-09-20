#!/usr/bin/env python3
"""Offline information budget for the OEW Ls current-step measurement.

This tool does not measure Ls.  It evaluates whether a stated U/dt/noise
budget can distinguish the requested Ls interval.  It uses only the Python
standard library and is deliberately fail-closed when the log lacks current
noise or step parameters.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from pathlib import Path

ADC_DC_SHUNT_UV_PER_A = 63000
ONE_LSB_MA = 3300.0 / 4095.0 * 1000.0 * 1000.0 / ADC_DC_SHUNT_UV_PER_A
DEFAULT_PWM_HZ = 5000
DEFAULT_ARR = 999
DEFAULT_TRIGGER = 0x4F455731
DEFAULT_LS_MIN_UH = 500.0
DEFAULT_LS_MAX_UH = 500000.0
DEFAULT_Z = 3.0
DEFAULT_MC_TRIALS = 2000
DEFAULT_SEED = 20260920
KV_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([-+]?0x[0-9A-Fa-f]+|[-+]?\d+(?:\.\d+)?)")


def _num(value: str) -> float:
    return float(int(value, 0)) if value.lower().startswith(("0x", "-0x", "+0x")) else float(value)


def parse_log(path: Path) -> tuple[list[dict[str, float]], str]:
    frames: list[dict[str, float]] = []
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_no, line in enumerate(stream, 1):
            if not ("@AT:LS:FRAME" in line or "@LS:FRAME" in line or "@AT:LS:" in line):
                continue
            fields = {key.lower(): _num(value) for key, value in KV_RE.findall(line)}
            if any(k in fields for k in ("i1", "i2", "idc1", "idc2", "raw_i1", "raw_i2")):
                fields["_line"] = float(line_no)
                frames.append(fields)
    if not frames:
        raise ValueError("log: no LS frames with i1/i2 fields")
    return frames, "@AT:LS frame block"


def _values(frames: list[dict[str, float]], names: tuple[str, ...]) -> list[float]:
    return [f[k] for f in frames for k in names if k in f]


def _field(frames: list[dict[str, float]], names: tuple[str, ...]) -> float | None:
    vals = _values(frames, names)
    return vals[0] if vals else None


def _identity(path: str | None) -> dict:
    result = {"pwm_hz": DEFAULT_PWM_HZ, "arr": DEFAULT_ARR, "trigger": DEFAULT_TRIGGER}
    if path:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"identity: cannot read {path}: {exc}")
        if not isinstance(payload, dict):
            raise ValueError("identity: expected JSON object")
        result.update(payload)
    return result


def _pooled_within_block(frames: list[dict[str, float]], phase: float | None) -> dict | None:
    """Пул внутригрупповых SD по блокам (d, ph): шум НЕЛЬЗЯ мерить по всему логу.

    Уровни duty и фазы ph дают СИГНАЛЬНЫЙ разброс; смешение блоков завышает шум
    (на реальном `ls`-логе это давало порог 308 мА вместо ~108 мА). Для шумового пола
    берутся блоки ph=0 (нулевой вектор): там нет ступени, только шум и смещение нуля.
    """
    groups: dict[tuple, list[dict[str, float]]] = {}
    for frame in frames:
        if phase is not None and frame.get("ph") != phase:
            continue
        groups.setdefault((frame.get("d"), frame.get("ph")), []).append(frame)
    blocks, num1, den1, num2, den2 = [], 0.0, 0.0, 0.0, 0.0
    for key in sorted(groups, key=lambda k: (str(k[0]), str(k[1]))):
        items = groups[key]
        i1 = [f["i1"] for f in items if "i1" in f]
        i2 = [f["i2"] for f in items if "i2" in f]
        if len(i1) < 2 or len(i2) < 2:
            continue
        s1, s2 = statistics.stdev(i1), statistics.stdev(i2)
        blocks.append({"d": key[0], "ph": key[1], "n": min(len(i1), len(i2)),
                       "sd_i1_ma": round(s1, 9), "sd_i2_ma": round(s2, 9),
                       "effective_sd_di_ma": round(math.hypot(s1, s2), 9)})
        num1 += (len(i1) - 1) * s1 * s1
        den1 += (len(i1) - 1)
        num2 += (len(i2) - 1) * s2 * s2
        den2 += (len(i2) - 1)
    if not blocks or den1 <= 0 or den2 <= 0:
        return None
    sd_i1 = math.sqrt(num1 / den1)
    sd_i2 = math.sqrt(num2 / den2)
    return {"blocks": blocks, "sd_i1_ma": sd_i1, "sd_i2_ma": sd_i2,
            "effective_sd_di_ma": math.hypot(sd_i1, sd_i2)}


def _grid(lo: float, hi: float) -> list[float]:
    if lo <= 0 or hi < lo:
        raise ValueError("Ls window must satisfy 0 < min <= max")
    # Include endpoints and logarithmically spaced interior points.
    points = [lo * (hi / lo) ** (i / 8.0) for i in range(9)]
    points[0], points[-1] = lo, hi
    return points


def analyze(args: argparse.Namespace) -> dict:
    frames, source_block = parse_log(Path(args.log))
    if args.z <= 0 or args.mc_trials <= 0:
        raise ValueError("z and mc-trials must be positive")
    identity = _identity(args.identity)
    i1 = _values(frames, ("i1", "idc1", "raw_i1"))
    i2 = _values(frames, ("i2", "idc2", "raw_i2"))
    if len(i1) < 2 or len(i2) < 2:
        raise ValueError("log: at least two i1 and two i2 samples are required")
    block_noise = None
    if args.sd_ma is not None:
        effective_sd, sd_i1, sd_i2 = float(args.sd_ma), None, None
        noise_source, noise_reliable = "explicit_sd_override", True
    else:
        block_noise = _pooled_within_block(frames, phase=0.0)
        if block_noise is None:
            if not args.allow_ungrouped_noise:
                raise ValueError(
                    "noise: нет блоков ph=0 минимум по 2 кадра для оценки шума. "
                    "Шум по всему логу запрещён (уровни/фазы дают сигнальный разброс); "
                    "передайте --sd-ma <измеренный SD, мА> или --allow-ungrouped-noise")
            sd_i1 = statistics.stdev(i1)
            sd_i2 = statistics.stdev(i2)
            effective_sd = math.hypot(sd_i1, sd_i2)
            noise_source, noise_reliable = "ungrouped_all_frames_UNRELIABLE", False
        else:
            sd_i1 = block_noise["sd_i1_ma"]
            sd_i2 = block_noise["sd_i2_ma"]
            effective_sd = block_noise["effective_sd_di_ma"]
            noise_source, noise_reliable = "pooled_within_block_ph0", True
    vstep = args.vstep_mv
    if vstep is None:
        vstep = _field(frames, ("vstep_mv", "u_mv", "dv_mv", "vstep", "u"))
    tstep = args.tstep_us
    if tstep is None:
        tstep = _field(frames, ("tstep_us", "dt_us", "tstep", "dt"))
    if vstep is None or tstep is None or vstep <= 0 or tstep <= 0:
        raise ValueError("missing positive vstep/tstep; pass --vstep-mv and --tstep-us")
    threshold_ma = max(args.z * effective_sd, ONE_LSB_MA)
    ls_grid = _grid(args.ls_min_uh, args.ls_max_uh)
    rows = []
    for ls in ls_grid:
        delta_i = vstep * tstep / ls
        rows.append({"ls_uh": round(ls, 9), "delta_i_ma": round(delta_i, 9),
                     "threshold_ma": round(threshold_ma, 9),
                     "discriminable": bool(delta_i >= threshold_ma)})
    discriminable = [r["ls_uh"] for r in rows if r["discriminable"]]
    required_area = threshold_ma * args.ls_max_uh
    required_tstep = required_area / vstep
    target_di_at_max = vstep * tstep / args.ls_max_uh
    # Пол квантования: если даже идеальный шум не помогает, требование SD недостижимо —
    # это надо назвать, а не выдавать недостижимое число.
    quantization_limited = (target_di_at_max / args.z) < ONE_LSB_MA
    required_sd = None if quantization_limited else target_di_at_max / args.z
    # Synthetic inversion: noisy delta-I samples, with the same stated voltage/time.
    rng = random.Random(args.seed)
    estimates: list[float] = []
    sigma_di = effective_sd
    non_positive = 0
    for _ in range(args.mc_trials):
        measured_di = target_di_at_max + rng.gauss(0.0, sigma_di)
        if measured_di > 0:
            estimates.append(vstep * tstep / measured_di)
        else:
            non_positive += 1
    non_positive_fraction = non_positive / args.mc_trials
    if estimates:
        estimates.sort()
        lo = estimates[max(0, int(0.025 * len(estimates)) - 1)]
        hi = estimates[min(len(estimates) - 1, int(0.975 * len(estimates)))]
        mc = {"target_ls_uh": args.ls_max_uh, "trials": args.mc_trials,
              "seed": args.seed, "valid_estimates": len(estimates),
              "non_positive_fraction": round(non_positive_fraction, 9),
              "reliable": bool(non_positive_fraction <= args.mc_max_nonpositive
                               and len(estimates) >= 100),
              "ci95_uh": [round(lo, 9), round(hi, 9)],
              "ci95_width_uh": round(hi - lo, 9),
              "ci95_width_vs_window": round((hi - lo) / (args.ls_max_uh - args.ls_min_uh), 9)}
    else:
        mc = {"target_ls_uh": args.ls_max_uh, "trials": args.mc_trials,
              "seed": args.seed, "valid_estimates": 0,
              "non_positive_fraction": round(non_positive_fraction, 9),
              "reliable": False, "ci95_uh": None,
              "ci95_width_uh": None, "ci95_width_vs_window": None}
    if not discriminable:
        verdict = "NOT_DISCRIMINABLE_WITH_THIS_NOISE"
    elif max(discriminable) < args.ls_max_uh:
        verdict = "NOT_DISCRIMINABLE_WITH_THIS_NOISE"
    else:
        verdict = "DISCRIMINABLE"
    return {
        "noise": {"source": source_block,
                   "source_kind": noise_source,
                   "reliable": noise_reliable,
                   "line_range": [int(frames[0]["_line"]), int(frames[-1]["_line"])],
                   "samples": len(frames),
                   "sd_i1_ma": None if sd_i1 is None else round(sd_i1, 9),
                   "sd_i2_ma": None if sd_i2 is None else round(sd_i2, 9),
                   "effective_sd_di_ma": round(effective_sd, 9),
                   "one_lsb_ma": round(ONE_LSB_MA, 9),
                   "blocks": (block_noise["blocks"] if block_noise else None),
                   "identity": identity},
        "budget": {"vstep_mv": vstep, "tstep_us": tstep, "z": args.z,
                    "ls_window_uh": [args.ls_min_uh, args.ls_max_uh],
                    "table": rows,
                    "expected_dc_current_ma_at_rs": (round(vstep / args.rs_mohm * 1000.0, 9)
                                                     if args.rs_mohm and args.rs_mohm > 0 else None),
                    "dc_current_over_shunt_limit": bool(
                        args.rs_mohm and args.rs_mohm > 0
                        and vstep / args.rs_mohm * 1000.0 > 10000.0),
                    "quantization_limited": quantization_limited,
                    "required_u_times_t_mv_us": round(required_area, 9),
                    "required_tstep_us_at_given_vstep": round(required_tstep, 9),
                    "required_sd_ma_at_given_u_times_t": (None if required_sd is None
                                                          else round(required_sd, 9)),
                    "monte_carlo": mc},
        "discriminable_range_uh": ([min(discriminable), max(discriminable)] if discriminable else []),
        "verdict": verdict,
        "limitations": [
            "Budget only; no Ls value is measured or claimed.",
            "Noise is pooled WITHIN (d, ph) blocks, ph=0 for the floor; whole-log SD is refused"
            " (levels/phases carry signal spread) unless explicitly allowed.",
            "Monte-Carlo discards non-positive delta-I; non_positive_fraction is reported and"
            " 'reliable' is false when the discard rate exceeds the declared limit.",
            "The model assumes a known voltage step, duration, and additive current noise.",
            "It does not qualify phase reference, ADC absolute accuracy, map readiness, or hardware safety.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--identity")
    parser.add_argument("--rs-mohm", type=float, default=13000.0)
    parser.add_argument("--ls-min-uh", type=float, default=DEFAULT_LS_MIN_UH)
    parser.add_argument("--ls-max-uh", type=float, default=DEFAULT_LS_MAX_UH)
    parser.add_argument("--vstep-mv", type=float)
    parser.add_argument("--tstep-us", type=float)
    parser.add_argument("--z", type=float, default=DEFAULT_Z)
    parser.add_argument("--sd-ma", type=float,
                        help="явно измеренный SD дельта-тока (мА); заменяет оценку по логу")
    parser.add_argument("--allow-ungrouped-noise", action="store_true",
                        help="РАЗРЕШИТЬ оценку шума по всему логу (помечается UNRELIABLE)")
    parser.add_argument("--mc-max-nonpositive", type=float, default=0.05,
                        help="доля отброшенных неположительных оценок MC, выше которой reliable=false")
    parser.add_argument("--mc-trials", type=int, default=DEFAULT_MC_TRIALS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(args)
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        Path(args.json).write_text(text, encoding="utf-8", newline="\n")
        print(text, end="")
        return 0
    except (OSError, ValueError) as exc:
        failure = {
            "verdict": "INSUFFICIENT_INPUT",
            "error": str(exc),
            "noise": None,
            "budget": None,
            "discriminable_range_uh": [],
            "limitations": ["No budget verdict is possible until the required log and step parameters are present."],
        }
        try:
            Path(args.json).write_text(
                json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n")
        except OSError:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
