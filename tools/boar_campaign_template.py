#!/usr/bin/env python3
"""Scaffold an empty BOAR grid campaign package for PC-3.

Creates the directory tree expected by `tools/map_scope_ingest.py`:
48 UART region logs (region_<r>_<p>.log) and 48 scope CSVs
(scope_region_<r>_<p>.csv), one per (region=0..11, point=0..3).

Optionally copies the ACS712 calibration JSON from the accepted Phase-1
package.  Stubs are clearly marked as placeholders and must be replaced with
real evidence before ingest.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REGIONS = 12
POINTS = 4

CSV_HEADER = (
    "# ACS712-20A scope reference: CH1=phase U, CH2=phase V; "
    "raw oscilloscope mV at ADC sample point\n"
    "# Placeholder: replace with measured values. "
    "scope_qualified=1 is only valid after aperture check.\n"
    "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,"
    "scope_qualified,note\n"
    "1,2500,2500,,110,15,0,placeholder\n"
    "2,2500,2500,,110,15,0,placeholder\n"
    "3,2500,2500,,110,15,0,placeholder\n"
    "4,2500,2500,,110,15,0,placeholder\n"
    "5,2500,2500,,110,15,0,placeholder\n"
    "6,2500,2500,,110,15,0,placeholder\n"
    "7,2500,2500,,110,15,0,placeholder\n"
    "8,2500,2500,,110,15,0,placeholder\n"
)

LOG_HEADER = (
    "# Placeholder UART log for BOAR grid capture.\n"
    "# Replace this file with the actual session log containing "
    "8 @MC:REC lines and one @MC:DRAIN:records=8 line.\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root",
        type=Path,
        required=True,
        help="output campaign directory, e.g. D:\\campaign_raw\\boar_20260905T120000Z",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="path to accepted acs712_calibration.json to copy into campaign",
    )
    parser.add_argument(
        "--no-stubs",
        dest="stubs",
        action="store_false",
        default=True,
        help="only create directories, do not write placeholder files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.campaign_root.resolve()
    logs = root / "logs"
    scope = root / "scope"
    calibration = root / "calibration"

    root.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    scope.mkdir(exist_ok=True)
    calibration.mkdir(exist_ok=True)

    if args.stubs:
        for r in range(REGIONS):
            for p in range(POINTS):
                (logs / f"region_{r}_{p}.log").write_text(
                    LOG_HEADER, encoding="utf-8"
                )
                (scope / f"scope_region_{r}_{p}.csv").write_text(
                    CSV_HEADER, encoding="utf-8"
                )

    if args.calibration:
        if not args.calibration.is_file():
            print(f"error: calibration file not found: {args.calibration}",
                  file=sys.stderr)
            return 2
        shutil.copy2(args.calibration, calibration / "acs712_calibration.json")
        print(f"copied calibration -> {calibration / 'acs712_calibration.json'}")

    print(f"campaign scaffold created: {root}")
    print(f"  logs : {len(list(logs.glob('*')))} files (expected {REGIONS * POINTS})")
    print(f"  scope: {len(list(scope.glob('*')))} files (expected {REGIONS * POINTS})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
