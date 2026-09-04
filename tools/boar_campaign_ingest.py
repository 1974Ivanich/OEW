#!/usr/bin/env python3
"""Run the full BOAR grid campaign ingest pipeline on ПК-2.

Workflow:
  1. Pre-check the campaign with verify_boar_campaign_ready.py.
  2. Run map_scope_ingest.py to produce manifest.json + samples.jsonl.
  3. Optionally run the host pipeline artifact generator.

This script never touches hardware; it only validates and processes evidence
that was already captured on ПК-3. Exit 0 = ingest succeeded and produced
`campaign/manifest.json` + `campaign/samples.jsonl`.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root",
        type=Path,
        required=True,
        help="campaign directory with logs/, scope/, calibration/acs712_calibration.json",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="also run the host pipeline artifact generator (map-artifact-cli)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="working directory for the pipeline step (default: campaign-root/../work)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="skip pre-check (not recommended)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.campaign_root.resolve()

    logs = root / "logs"
    scope = root / "scope"
    calibration = root / "calibration" / "acs712_calibration.json"
    out = root / "campaign"

    if not logs.is_dir() or not scope.is_dir():
        print(f"error: {root} must contain logs/ and scope/ directories",
              file=sys.stderr)
        return 2
    if not calibration.is_file():
        print(f"error: calibration not found: {calibration}", file=sys.stderr)
        return 2

    if not args.skip_verify:
        verify_script = Path(__file__).with_name("verify_boar_campaign_ready.py")
        print(f"--- pre-check: {verify_script.name} ---")
        res = subprocess.run(
            [sys.executable, str(verify_script), "--campaign-root", str(root)],
            text=True,
        )
        if res.returncode != 0:
            print("error: campaign not ready, aborting ingest", file=sys.stderr)
            return 1
        print("pre-check OK")

    ingest_script = Path(__file__).with_name("map_scope_ingest.py")
    cmd = [
        sys.executable, str(ingest_script),
        "--logs", str(logs),
        "--scope", str(scope),
        "--out", str(out),
        "--calib", str(calibration),
    ]
    if args.pipeline:
        cmd.append("--pipeline")
        if args.work_dir:
            cmd.extend(["--work-dir", str(args.work_dir)])

    print(f"--- ingest: {ingest_script.name} ---")
    res = subprocess.run(cmd, text=True)
    if res.returncode != 0:
        return res.returncode

    manifest = out / "manifest.json"
    samples = out / "samples.jsonl"
    if not manifest.is_file() or not samples.is_file():
        print("error: ingest did not produce manifest.json/samples.jsonl",
              file=sys.stderr)
        return 1

    print("--- ingest output ---")
    print(f"  manifest: {manifest}  sha256={sha256_file(manifest)}")
    print(f"  samples : {samples}  sha256={sha256_file(samples)}")
    if args.pipeline:
        pipeline_bin = out / "pipeline" / "oew_map_v2.bin"
        if pipeline_bin.is_file():
            print(f"  pipeline artifact: {pipeline_bin}  "
                  f"size={pipeline_bin.stat().st_size} B")
    print("--- BOAR ingest OK ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
