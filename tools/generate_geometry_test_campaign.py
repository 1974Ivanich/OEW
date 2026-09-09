#!/usr/bin/env python3
"""Generate a deterministic synthetic geometry-test campaign fixture.

This fixture is for software regression only. It is not BOAR measurement
 evidence and must not be used for safety approval or hardware map loading.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SECTORS = 6
WINDOWS = 2
ARR = 5000
MID = (ARR + 1) // 2


def ccr_from_q15(value: int) -> int:
    return MID + round(value * MID / 32768)


def phases(sector: int, mod: int) -> tuple[int, int, int]:
    a = mod // 3
    b = mod - a
    return {
        0: (mod, -a, -b),
        1: (mod, -b, -a),
        2: (-a, mod, -b),
        3: (-b, mod, -a),
        4: (-a, -b, mod),
        5: (-b, -a, mod),
    }[sector]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: generate_geometry_test_campaign.py OUTPUT_DIR", file=sys.stderr)
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format_version": 1,
        "campaign_id": "synthetic-geometry-regression-001",
        "board_revision": 7,
        "pwm_frequency_hz": 20000,
        "timer_arr": ARR,
        "adc_trigger_id": 1329944369,
        "trigger_offset_ticks": 0,
        "deadtime_ticks": 85,
        "adc_clock_hz": 42500000,
        "adc_sample_cycles_x2": 1281,
        "adc_resolution": 0,
        "adc_config_signature": 287454020,
        "current_calibration_signature": 1432778632,
        "characterization_id": 16909061,
        "dataset_crc32": 2712847317,
        "tool_build_id": 539363361,
        "qualification_revision": 3,
        "solver_revision": 4,
        "certifier_revision": 3,
        "phase_a": 0,
        "phase_b": 1,
        "startup": {"sector": 0, "window": 0, "hold_cycles": 25,
                     "mu": 7000, "mv": -2333, "mw": -4667},
        "qualifications": {
            "accumulator": {"min_samples": 8, "mad_limit_ma": 1000,
                             "kcl_limit_ma": 100, "min_margin_ticks": 1},
            "solver": {"min_samples": 8, "holdout_samples": 2,
                       "residual_rms_limit_ma": 1000,
                       "residual_max_limit_ma": 2000, "bias_limit_ma": 1000,
                       "holdout_rms_limit_ma": 1000, "kcl_rms_limit_ma": 100,
                       "max_condition_ratio": 100000,
                       "min_abs_determinant": 1, "min_abs_diagonal": 100},
            "region": {"min_valid_cells": 4, "guard_q15": 0,
                        "min_margin_ticks": 3, "use_geometry": 1,
                        "geometry_window0_min_mod_q15": 6000,
                        "geometry_window0_max_mod_q15": 10000,
                        "geometry_window1_min_mod_q15": 10000,
                        "geometry_window1_max_mod_q15": 14000},
        },
    }
    samples = []
    seq = 1
    for sector in range(SECTORS):
        for window, mods in enumerate((
                (7000, 7400, 8000, 8500, 8800, 9000, 9200, 9500),
                (10500, 11000, 11500, 12000, 12500, 13000, 13500, 13800))):
            for index, mod in enumerate(mods):
                mu, mv, mw = phases(sector, mod)
                x = 1000 + index * 1000
                y = 2000 + index * 500
                samples.append({
                    "seq": index + 1, "sector": sector, "window": window,
                    "ccr1": ccr_from_q15(mu), "ccr2": ccr_from_q15(mv),
                    "ccr3": ccr_from_q15(mw), "arr": ARR,
                    "raw_idc1": 2050 + index, "raw_idc2": 2060 + index,
                    "raw_ct": 2048, "raw_vbus": 2900,
                    "idc1_ma": x, "idc2_ma": y, "ict_ma": 0,
                    "vbus_mv": 12000, "ref_u_ma": 2 * x + y,
                    "ref_v_ma": -x + 3 * y, "ref_w_ma": -x - 4 * y,
                    "margin_ticks": 5, "blanking_ticks": 2,
                    "adc_settled": 1, "scope_qualified": 1,
                    "timestamp_cycles": 100000 + seq,
                })
                seq += 1
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "samples.jsonl").write_text("\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")
    print(f"generated {out} with {len(samples)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
