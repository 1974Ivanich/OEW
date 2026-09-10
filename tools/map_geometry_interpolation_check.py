#!/usr/bin/env python3
"""Check reconstruction error when a row-centre M is applied at region edges.

Input JSON:
{
  "residual_rms_limit_ma": 50,
  "rows": [
    {
      "sector": 0, "window": 0,
      "center_samples": [{"idc1": 100, "idc2": 50, "refa": 250, "refb": 50}, ...],
      "edge_samples": [{"idc1": ..., "idc2": ..., "refa": ..., "refb": ...}, ...]
    }
  ]
}

The centre M is fitted with the same no-intercept y=Mx model used by the
firmware-side characterization contract. Edge error is measured against the
independent phase-current reference. This is an analysis gate, not a claim
that sparse centre data proves interpolation safety: real campaign edge
samples are required for a safety acceptance.
"""

import argparse
import json
import math
import sys


def fit_m(samples):
    s00 = sum(float(s["idc1"]) ** 2 for s in samples)
    s01 = sum(float(s["idc1"]) * float(s["idc2"]) for s in samples)
    s11 = sum(float(s["idc2"]) ** 2 for s in samples)
    t0a = sum(float(s["idc1"]) * float(s["refa"]) for s in samples)
    t1a = sum(float(s["idc2"]) * float(s["refa"]) for s in samples)
    t0b = sum(float(s["idc1"]) * float(s["refb"]) for s in samples)
    t1b = sum(float(s["idc2"]) * float(s["refb"]) for s in samples)
    det = s00 * s11 - s01 * s01
    if abs(det) < 1e-12:
        raise ValueError("centre excitation is singular")
    return (
        (t0a * s11 - t1a * s01) / det,
        (t1a * s00 - t0a * s01) / det,
        (t0b * s11 - t1b * s01) / det,
        (t1b * s00 - t0b * s01) / det,
    )


def check_row(row, limit):
    m = fit_m(row["center_samples"])
    errors = []
    for sample in row["edge_samples"]:
        x0 = float(sample["idc1"])
        x1 = float(sample["idc2"])
        pred_a = m[0] * x0 + m[1] * x1
        pred_b = m[2] * x0 + m[3] * x1
        errors.extend((pred_a - float(sample["refa"]), pred_b - float(sample["refb"])))
    rms = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else float("inf")
    return m, rms, rms <= limit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="JSON interpolation-analysis input")
    args = parser.parse_args()
    with open(args.input, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    limit = float(data["residual_rms_limit_ma"])
    rows = data["rows"]
    if not rows:
        raise ValueError("rows must not be empty")
    failed = False
    for row in rows:
        m, rms, ok = check_row(row, limit)
        print("row %d/%d M=[%.6f %.6f; %.6f %.6f] edge_rms_ma=%.3f %s" %
              (row["sector"], row["window"], m[0], m[1], m[2], m[3], rms,
               "PASS" if ok else "FAIL"))
        failed |= not ok
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        print("map_geometry_interpolation_check: %s" % exc, file=sys.stderr)
        sys.exit(2)
