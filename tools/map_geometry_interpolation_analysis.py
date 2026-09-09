#!/usr/bin/env python3
"""Analyze reconstruction error near modulation-window boundaries.

This tool intentionally reports BLOCKED when real edge evidence is absent. It
never turns a lack of edge samples into PASS. The current implementation uses
actual campaign reference currents as the empirical edge target and fits the
center reconstruction matrix from samples nearest the row's modulation median.
It is a qualification harness, not a replacement for the production solver.

Usage:
  python tools/map_geometry_interpolation_analysis.py CAMPAIGN [REPORT.json]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

SECTORS = 6
WINDOWS = 2
PHASE_NAMES = ("ref_u_ma", "ref_v_ma", "ref_w_ma")


def q15_from_ccr(ccr: int, arr: int) -> int:
    if arr <= 0:
        raise ValueError("arr must be positive")
    mid = (arr + 1) // 2
    num = (ccr - mid) * 32768
    value = (num + mid // 2) // mid if num >= 0 else (num - mid // 2) // mid
    return max(-32768, min(32767, value))


def modulation_q15(sample: dict) -> int:
    values = [q15_from_ccr(sample[f"ccr{i}"], sample["arr"]) for i in (1, 2, 3)]
    return max(abs(v) for v in values)


def solve_2x2(rows: list[tuple[int, int, int, int]]) -> tuple[float, float]:
    # rows: x1, x2, target_a, target_b; solve each target independently.
    s11 = sum(x1 * x1 for x1, _, _, _ in rows)
    s12 = sum(x1 * x2 for x1, x2, _, _ in rows)
    s22 = sum(x2 * x2 for _, x2, _, _ in rows)
    det = s11 * s22 - s12 * s12
    if det == 0:
        raise ValueError("singular center model")
    b1 = (sum(x1 * ya for x1, _, ya, _ in rows) * s22 -
          sum(x2 * ya for _, x2, ya, _ in rows) * s12) / det
    b2 = (sum(x2 * ya for _, x2, ya, _ in rows) * s11 -
          sum(x1 * ya for x1, _, ya, _ in rows) * s12) / det
    c1 = (sum(x1 * yb for x1, _, _, yb in rows) * s22 -
          sum(x2 * yb for _, x2, _, yb in rows) * s12) / det
    c2 = (sum(x2 * yb for _, x2, _, yb in rows) * s11 -
          sum(x1 * yb for x1, _, _, yb in rows) * s12) / det
    return (b1, b2, c1, c2)


def analyze_row(samples: list[dict], phase_a: int, phase_b: int, window: int,
                rms_limit: int, max_limit: int) -> dict:
    if not samples:
        return {"status": "BLOCKED", "reason": "no campaign samples for row"}
    enriched = [(modulation_q15(s), s) for s in samples]
    enriched.sort(key=lambda item: item[0])
    mods = [m for m, _ in enriched]
    window_min, window_max = ((6000, 10000) if window == 0 else (10000, 14000))
    outside = [mod for mod in mods if not (
        window_min <= mod < window_max if window == 0
        else window_min <= mod <= window_max)]
    if outside:
        return {
            "status": "FAIL",
            "reason": "observed samples fall outside deterministic amplitude window",
            "sample_count": len(samples),
            "window_min_q15": window_min,
            "window_max_q15": window_max,
            "outside_mod_q15": outside,
        }
    median = mods[len(mods) // 2]
    center_count = max(2, len(enriched) // 3)
    center = sorted(enriched, key=lambda item: abs(item[0] - median))[:center_count]
    if len(center) < 2:
        return {"status": "BLOCKED", "reason": "fewer than 2 center samples"}

    rows = []
    for _, s in center:
        rows.append((int(s["idc1_ma"]), int(s["idc2_ma"]),
                     int(s[PHASE_NAMES[phase_a]]), int(s[PHASE_NAMES[phase_b]])))
    try:
        m00, m01, m10, m11 = solve_2x2(rows)
    except ValueError as exc:
        return {"status": "BLOCKED", "reason": str(exc), "sample_count": len(samples)}

    edge_count = max(2, len(enriched) // 4)
    edges = enriched[:edge_count] + enriched[-edge_count:]
    errors = []
    for mod, s in edges:
        pred_a = (m00 * s["idc1_ma"] + m01 * s["idc2_ma"])
        pred_b = (m10 * s["idc1_ma"] + m11 * s["idc2_ma"])
        actual_a = s[PHASE_NAMES[phase_a]]
        actual_b = s[PHASE_NAMES[phase_b]]
        errors.extend((pred_a - actual_a, pred_b - actual_b))

    rms = math.sqrt(sum(error * error for error in errors) / len(errors))
    maximum = max(abs(error) for error in errors)
    return {
        "status": "PASS" if rms <= rms_limit and maximum <= max_limit else "FAIL",
        "sample_count": len(samples),
        "center_sample_count": len(center),
        "edge_sample_count": len(edges),
        "mod_min_q15": min(mods),
        "mod_center_q15": median,
        "mod_max_q15": max(mods),
        "edge_rms_ma": round(rms, 3),
        "edge_max_abs_ma": round(maximum, 3),
        "residual_rms_limit_ma": rms_limit,
        "residual_max_limit_ma": max_limit,
        "model": [round(value, 9) for value in (m00, m01, m10, m11)],
    }


def analyze_campaign(campaign: Path) -> dict:
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    samples = [json.loads(line) for line in
               (campaign / "samples.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    q = manifest["qualifications"]["solver"]
    rows: dict[tuple[int, int], list[dict]] = {}
    for sample in samples:
        key = (sample["sector"], sample["window"])
        rows.setdefault(key, []).append(sample)

    report_rows = {}
    blocked = []
    failed = []
    for sector in range(SECTORS):
        for window in range(WINDOWS):
            key = (sector, window)
            result = analyze_row(rows.get(key, []), manifest["phase_a"],
                                 manifest["phase_b"], window,
                                 q["residual_rms_limit_ma"],
                                 q["residual_max_limit_ma"])
            report_rows[f"{sector}/{window}"] = result
            if result["status"] == "BLOCKED":
                blocked.append(f"{sector}/{window}")
            elif result["status"] == "FAIL":
                failed.append(f"{sector}/{window}")

    status = "PASS" if not blocked and not failed else ("BLOCKED" if blocked else "FAIL")
    return {
        "status": status,
        "campaign": str(campaign),
        "method": "center-nearest-median OLS, empirical actual reference currents at lower/upper observed edges",
        "edge_evidence_required": True,
        "blocked_rows": blocked,
        "failed_rows": failed,
        "rows": report_rows,
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: map_geometry_interpolation_analysis.py CAMPAIGN [REPORT.json]", file=sys.stderr)
        return 2
    try:
        report = analyze_campaign(Path(argv[1]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"analysis error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if len(argv) == 3:
        Path(argv[2]).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
