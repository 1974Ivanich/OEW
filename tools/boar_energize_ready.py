#!/usr/bin/env python3
"""Step-by-step readiness checklist before energizing the BOAR capture session.

This script runs on ПК-3 and verifies:
  1. Approved Test №3 G0 JSON is present and decision == APPROVED.
  2. Git working tree is clean and on origin/main (or a detached HEAD that
     matches origin/main).
  3. Commissioning firmware builds (unless --skip-build).
  4. Built firmware SHA-256 matches the G0 approval, if the approval records
     one.
  5. Safety checklist items are confirmed by operator and safety watcher.

It writes a machine-readable `energize_ready.json` evidence file.  It does not
flash firmware, close relays, or energize hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_G0_FIELDS = (
    "schema", "test_id", "decision", "scope", "profile_id",
    "board_revision", "required_firmware_defines",
)

SAFETY_PROMPTS = {
    "motor_secured": "Motor is mechanically secured; shaft cannot spin or engage loads.",
    "dc_link_loto": "DC-link supply LOTO locks are applied and keys removed before energize.",
    "emergency_stop": "Emergency stop is within arm's reach of both operator and watcher.",
    "current_limit_2a": "DC-link supply is set to 60 V with current limit <= 2 A.",
    "acs712_wiring": "ACS712 sensors are wired CH1=U, CH2=V, powered from external 5.0 V, CF <= 1 nF.",
    "two_person_present": "Both operator and safety watcher are present and have read the procedure.",
    "extinguisher": "Fire extinguisher class C/E is present and known to both persons.",
    "first_aid": "First aid kit is accessible.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--g0-approval", type=Path, required=True,
        help="path to signed Test №3 G0 approval JSON",
    )
    parser.add_argument(
        "--operator", required=True,
        help="name of the operator",
    )
    parser.add_argument(
        "--watcher", required=True,
        help="name of the safety watcher",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("energize_ready.json"),
        help="output evidence JSON (default: ./energize_ready.json)",
    )
    parser.add_argument(
        "--assume-yes", action="store_true",
        help="record all safety prompts as YES without interactive input (for tests only)",
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="skip commissioning firmware build check",
    )
    parser.add_argument(
        "--firmware-bin", type=Path, default=None,
        help="use existing firmware.bin instead of building",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run all checks but do not write the output file",
    )
    parser.add_argument(
        "--allow-non-main", action="store_true",
        help="allow branches other than main (use only for tests/dev)",
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="allow uncommitted changes in src/, Makefile, .ioc (use only for tests/dev)",
    )
    return parser


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_g0(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_G0_FIELDS if f not in data]
    if missing:
        raise ValueError(f"G0 approval missing fields: {', '.join(missing)}")
    if data.get("test_id") != "TEST3":
        raise ValueError(f"G0 approval test_id={data.get('test_id')!r}, expected TEST3")
    if data.get("decision") != "APPROVED":
        raise ValueError(f"G0 decision={data.get('decision')!r}, expected APPROVED")
    if "mapcap" not in str(data.get("scope", [])).lower():
        raise ValueError("G0 scope does not mention mapcap")
    return data


def git_state() -> dict[str, Any]:
    head = _run(["git", "rev-parse", "HEAD"])
    if head.returncode != 0:
        raise RuntimeError("not a git repository")
    sha = head.stdout.strip()

    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch_name = branch.stdout.strip()

    # Check that HEAD matches origin/main if branch is not main.
    origin_main = _run(["git", "rev-parse", "origin/main"])
    origin_sha = origin_main.stdout.strip() if origin_main.returncode == 0 else None

    status = _run(["git", "status", "--porcelain", "--", "src/", "Makefile", "OEW_Motor.ioc"])
    dirty_files = [line.strip() for line in status.stdout.splitlines() if line.strip()]

    return {
        "sha": sha,
        "branch": branch_name,
        "origin_main_sha": origin_sha,
        "on_main_or_detached_origin": branch_name == "main" or sha == origin_sha,
        "dirty_files": dirty_files,
    }


def build_firmware() -> Path:
    cmd = [
        "make", "clean",
        ";",
        "make",
        "EXTRA_CFLAGS=-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 "
        "-DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1",
    ]
    # Use shell=True so ';' works as a command separator in the default shell.
    res = subprocess.run(" ".join(cmd), shell=True)
    if res.returncode != 0:
        raise RuntimeError("commissioning firmware build failed")
    fw = Path("build/firmware.bin")
    if not fw.is_file():
        raise RuntimeError("build/firmware.bin not found after build")
    return fw


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"[assume-yes] {prompt}: YES")
        return True
    while True:
        answer = input(f"{prompt} [yes/no]: ").strip().lower()
        if answer in ("yes", "y"):
            return True
        if answer in ("no", "n"):
            return False
        print("Please answer yes or no.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        g0 = load_g0(args.g0_approval)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: G0 approval invalid: {exc}", file=sys.stderr)
        return 2

    print(f"G0 approval: {args.g0_approval} — APPROVED for {g0['test_id']}")

    try:
        git = git_state()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if git["dirty_files"] and not args.allow_dirty:
        print("error: uncommitted changes in src/, Makefile, or OEW_Motor.ioc:",
              file=sys.stderr)
        for line in git["dirty_files"]:
            print(f"  {line}", file=sys.stderr)
        return 2

    if not git["on_main_or_detached_origin"] and not args.allow_non_main:
        print(f"error: HEAD {git['sha'][:8]} is not main and does not match origin/main",
              file=sys.stderr)
        return 2

    print(f"Git: {git['branch']} @ {git['sha']}")

    if not args.skip_build:
        try:
            if args.firmware_bin:
                fw = args.firmware_bin.resolve()
                if not fw.is_file():
                    raise RuntimeError(f"--firmware-bin not found: {fw}")
            else:
                fw = build_firmware()
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        fw = args.firmware_bin or Path("build/firmware.bin")
        if not fw.is_file():
            print(f"error: --skip-build but firmware not found: {fw}", file=sys.stderr)
            return 2

    fw_sha = _sha256_file(fw)
    print(f"Firmware: {fw} sha256={fw_sha}")

    expected_fw_sha = g0.get("firmware_sha256", "")
    if expected_fw_sha and fw_sha.lower() != expected_fw_sha.lower():
        print(f"error: firmware SHA256 mismatch (G0 expects {expected_fw_sha})",
              file=sys.stderr)
        return 2

    expected_src_sha = g0.get("firmware_source_sha", "")
    if expected_src_sha and git["sha"].lower() != expected_src_sha.lower():
        print(f"error: source SHA mismatch (G0 expects {expected_src_sha})",
              file=sys.stderr)
        return 2

    print("\n--- Safety checklist ---")
    safety: dict[str, Any] = {}
    all_yes = True
    for key, prompt in SAFETY_PROMPTS.items():
        yes = confirm(prompt, args.assume_yes)
        safety[key] = {"confirmed": yes, "utc": datetime.now(timezone.utc).isoformat()}
        if not yes:
            all_yes = False
            print(f"  {key}: NOT CONFIRMED")

    if not all_yes:
        print("error: not all safety items confirmed", file=sys.stderr)
        return 1

    evidence = {
        "schema": "oew-boar-energize-ready-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "operator": args.operator,
        "watcher": args.watcher,
        "g0_approval": str(args.g0_approval),
        "git": git,
        "firmware": {
            "path": str(fw),
            "sha256": fw_sha,
            "built": not args.skip_build,
        },
        "safety": safety,
    }

    if not args.dry_run:
        args.output.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nEvidence written: {args.output}")
    else:
        print("\nDry run complete; evidence not written.")

    print("--- READY for energize ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
