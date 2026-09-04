#!/usr/bin/env python3
"""Pre-ingest readiness check for a BOAR grid campaign folder.

Does not replace `tools/map_scope_ingest.py` validation; it only verifies that
all expected files exist, the calibration JSON is present, and placeholder
flags are cleared.  Exit 0 = ready to attempt ingest; 1 = missing/unfilled
placeholders; 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REGIONS = 12
POINTS = 4


def is_placeholder(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return True
    return "placeholder" in text.lower() or "replace this file" in text.lower()


def check_scope_csv(path: Path) -> list[str]:
    """Return list of problems with a scope CSV."""
    problems = []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(row for row in fh if not row.lstrip().startswith("#"))
            rows = list(reader)
    except Exception as exc:  # pragma: no cover - defensive
        problems.append(f"cannot parse: {exc}")
        return problems

    if len(rows) != 8:
        problems.append(f"expected 8 data rows, got {len(rows)}")
    qualified = [r for r in rows if r.get("scope_qualified") == "1"]
    if not qualified and rows:
        problems.append("scope_qualified=0 everywhere")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    args = parser.parse_args(argv)

    root: Path = args.campaign_root.resolve()
    if not root.is_dir():
        print(f"error: campaign root does not exist: {root}", file=sys.stderr)
        return 2

    logs = root / "logs"
    scope = root / "scope"
    calibration = root / "calibration" / "acs712_calibration.json"

    problems = []
    missing_log = []
    missing_scope = []
    placeholder_files = []
    for r in range(REGIONS):
        for p in range(POINTS):
            log = logs / f"region_{r}_{p}.log"
            csv_ = scope / f"scope_region_{r}_{p}.csv"
            if not log.is_file():
                missing_log.append(log.name)
            elif is_placeholder(log):
                placeholder_files.append(log.name)
            if not csv_.is_file():
                missing_scope.append(csv_.name)
            elif is_placeholder(csv_):
                placeholder_files.append(csv_.name)
            else:
                csv_problems = check_scope_csv(csv_)
                if csv_problems:
                    problems.append(f"{csv_.name}: {csv_problems[0]}")

    if missing_log:
        problems.append(f"missing logs: {len(missing_log)}")
    if missing_scope:
        problems.append(f"missing scope CSVs: {len(missing_scope)}")
    if not calibration.is_file():
        problems.append("calibration/acs712_calibration.json not found")

    if placeholder_files:
        problems.append(
            f"{len(placeholder_files)} placeholder files not yet replaced"
        )

    if problems:
        print("NOT READY:")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"READY: {root}")
    print(f"  {REGIONS * POINTS} region logs + {REGIONS * POINTS} scope CSVs present")
    print(f"  calibration: {calibration}")
    print("Next step: python tools\\map_scope_ingest.py --logs ... --scope ... --out ... --calib ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
