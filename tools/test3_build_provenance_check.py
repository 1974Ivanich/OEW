#!/usr/bin/env python3
"""Offline Test3 build provenance verifier.

This checker binds a retained build log to the diagnostic manifest and firmware
without touching hardware.  The build log must explicitly contain machine-
readable identity markers emitted by the build wrapper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_DEFINES = {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
}
MARKER_RE = re.compile(r"^OEW_PROVENANCE_([A-Z0-9_]+)=(.*)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_ok(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value.strip().lower()) is not None


def git_sha_ok(value: Any) -> bool:
    return isinstance(value, str) and GIT_SHA_RE.fullmatch(value.strip().lower()) is not None


def safe_file(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    p = Path(value)
    if p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        return None
    cur = root
    try:
        for part in p.parts:
            cur = cur / part
            if cur.is_symlink():
                return None
        resolved = cur.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def provenance_checks(campaign_root: Path) -> list[tuple[str, bool, str]]:
    manifest_path = campaign_root / "diagnostic_build_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return [("manifest-present", False, "retained diagnostic_build_manifest.json is required")]
    manifest = load(manifest_path)
    build = manifest.get("build")
    if not isinstance(build, Mapping):
        return [("build-object", False, "manifest.build object is required")]

    log_path = safe_file(campaign_root, build.get("log_path"))
    checks: list[tuple[str, bool, str]] = []
    checks.append(("build-log-path", log_path is not None, "build.log must be a retained regular file inside campaign root"))
    if log_path is None:
        return checks

    declared_log_sha = str(build.get("log_sha256", "")).lower()
    actual_log_sha = sha256_file(log_path)
    checks.append(("build-log-sha256", sha256_ok(declared_log_sha) and declared_log_sha == actual_log_sha, "manifest build.log SHA-256 must match retained bytes"))

    markers: dict[str, str] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        match = MARKER_RE.match(line.strip())
        if match:
            markers[match.group(1)] = match.group(2).strip()

    source_sha = str(manifest.get("source_sha", "")).lower()
    checks.append(("source-build-binding", git_sha_ok(source_sha) and markers.get("SOURCE_SHA", "").lower() == source_sha, "build log SOURCE_SHA must equal manifest source_sha"))

    firmware = manifest.get("firmware")
    firmware_path = safe_file(campaign_root, firmware.get("path") if isinstance(firmware, Mapping) else None)
    firmware_sha = str(firmware.get("sha256", "")).lower() if isinstance(firmware, Mapping) else ""
    actual_firmware_sha = sha256_file(firmware_path) if firmware_path else ""
    checks.append(("firmware-bytes-binding", firmware_path is not None and sha256_ok(firmware_sha) and actual_firmware_sha == firmware_sha, "manifest firmware SHA must match retained binary bytes"))
    checks.append(("firmware-build-binding", firmware_path is not None and markers.get("FIRMWARE_SHA256", "").lower() == firmware_sha, "build log FIRMWARE_SHA256 must equal manifest firmware SHA"))

    defines = manifest.get("defines")
    define_markers = {k[len("DEFINE_"):]: v for k, v in markers.items() if k.startswith("DEFINE_")}
    checks.append(("defines-complete", manifest.get("defines_complete") is True and isinstance(defines, Mapping), "manifest must contain a complete defines object"))
    if isinstance(defines, Mapping):
        for name, expected in REQUIRED_DEFINES.items():
            checks.append((f"define-{name}", defines.get(name) == expected and define_markers.get(name) == expected, "manifest and build-log define must match exactly"))
        actual_names = set(str(k) for k in defines)
        marker_names = set(define_markers)
        checks.append(("defines-log-complete", actual_names == marker_names, "build log must enumerate exactly the manifest define names"))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Test3 retained-build provenance checker")
    parser.add_argument("campaign_root", type=Path)
    args = parser.parse_args(argv)
    checks = provenance_checks(args.campaign_root)
    for name, passed, detail in checks:
        print(f"{name}={'PASS' if passed else 'FAIL'}: {detail}")
    return 0 if checks and all(passed for _, passed, _ in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
