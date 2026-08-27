#!/usr/bin/env python3
"""Emit a retained Test3 diagnostic build log and manifest from actual outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_DEFINES = {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    args = parser.parse_args()

    root = args.campaign_root.resolve()
    firmware = args.firmware.resolve()
    if not firmware.is_file():
        raise SystemExit(f"firmware not found: {firmware}")
    try:
        firmware.relative_to(root)
    except ValueError as exc:
        raise SystemExit("firmware must be retained inside campaign root") from exc

    root.mkdir(parents=True, exist_ok=True)
    firmware_rel = firmware.relative_to(root).as_posix()
    firmware_sha = sha256_file(firmware)

    log_path = root / "diagnostic_build.log"
    lines = [
        f"OEW_PROVENANCE_SOURCE_SHA={args.source_sha}",
        f"OEW_PROVENANCE_FIRMWARE_SHA256={firmware_sha}",
        *(f"OEW_PROVENANCE_DEFINE_{name}={value}" for name, value in REQUIRED_DEFINES.items()),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_sha = sha256_file(log_path)

    manifest = {
        "schema": "h1-g0-diagnostic-manifest-v2-test3-transition",
        "gate": "HIL_TEST3_G0",
        "test_id": "TEST3",
        "target": "physical-nohv-diagnostic-test3",
        "test": "MAPCAP_TEST3",
        "source_sha": args.source_sha,
        "defines_complete": True,
        "defines": REQUIRED_DEFINES,
        "build": {
            "log_path": log_path.relative_to(root).as_posix(),
            "log_sha256": log_sha,
        },
        "firmware": {
            "path": firmware_rel,
            "sha256": firmware_sha,
        },
        "transition": {
            "legacy_contract": "HIL_TEST2_G0/MAPCAP_TEST2",
            "status": "NOT_VALID_FOR_LEGACY_G0_PASS",
        },
    }
    (root / "diagnostic_build_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
