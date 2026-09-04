#!/usr/bin/env python3
"""Verify an incoming ACS712 no-HV campaign zip on ПК-2.

Extracts the archive to a temporary directory, checks SHA-256 of the zip, and
re-runs the offline fail-closed validator on the evidence package.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import acs712_nohv_validator as validator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path, help="path to campaign zip")
    parser.add_argument("--expected-sha256", help="expected SHA-256 of the zip (hex)")
    parser.add_argument(
        "--campaign-root",
        default=None,
        help="expected root folder inside the zip (default: derive from zip name)",
    )
    args = parser.parse_args()

    zip_path: Path = args.zip
    if not zip_path.is_file():
        print(f"error: zip not found: {zip_path}", file=sys.stderr)
        return 2

    actual_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"zip_sha256={actual_sha}")
    if args.expected_sha256:
        if actual_sha.lower() != args.expected_sha256.lower():
            print(f"error: SHA256 mismatch (expected {args.expected_sha256})", file=sys.stderr)
            return 2
        print("zip_sha256 OK")

    campaign_root = args.campaign_root or zip_path.stem
    with tempfile.TemporaryDirectory() as tmp:
        extract = Path(tmp) / "camp"
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract)
        campaign = extract / campaign_root
        if not campaign.is_dir():
            print(f"error: expected root folder missing: {campaign_root}", file=sys.stderr)
            return 2
        print(f"extracted files: {len([p for p in campaign.rglob('*') if p.is_file()])}")
        summary = validator.validate_campaign(campaign)
        print(f"verdict={summary['verdict']}")
        if summary["verdict"] != "PASS":
            for check in summary["checks"]:
                if check["result"] != "PASS":
                    print(json.dumps(check, ensure_ascii=False))
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
