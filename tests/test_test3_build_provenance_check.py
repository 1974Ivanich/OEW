from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.test3_build_provenance_check import provenance_checks


def _write_campaign(tmp_path: Path) -> Path:
    root = tmp_path / "campaign"
    root.mkdir()
    firmware = root / "firmware.bin"
    firmware.write_bytes(b"diagnostic-firmware")
    firmware_sha = hashlib.sha256(firmware.read_bytes()).hexdigest()
    source_sha = "a" * 40
    lines = [
        f"OEW_PROVENANCE_SOURCE_SHA={source_sha}",
        f"OEW_PROVENANCE_FIRMWARE_SHA256={firmware_sha}",
        "OEW_PROVENANCE_DEFINE_OEW_MAP_CAPTURE=1",
        "OEW_PROVENANCE_DEFINE_OEW_MAP_L3=1",
        "OEW_PROVENANCE_DEFINE_PWM_OEW_BOARD_REVISION=7",
        "OEW_PROVENANCE_DEFINE_OEW_MAP_SYNTHETIC_PROFILE=1",
        "OEW_PROVENANCE_DEFINE_OEW_HOST_TEST=1",
        "OEW_PROVENANCE_DEFINE_OEW_HS1_COMMISSIONING_RELEASE=1",
    ]
    log = root / "build.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_sha = hashlib.sha256(log.read_bytes()).hexdigest()
    manifest = {
        "schema": "h1-g0-diagnostic-manifest-v2-test3-transition",
        "source_sha": source_sha,
        "defines_complete": True,
        "defines": {
            "OEW_MAP_CAPTURE": "1",
            "OEW_MAP_L3": "1",
            "PWM_OEW_BOARD_REVISION": "7",
            "OEW_MAP_SYNTHETIC_PROFILE": "1",
            "OEW_HOST_TEST": "1",
            "OEW_HS1_COMMISSIONING_RELEASE": "1",
        },
        "build": {"log_path": "build.log", "log_sha256": log_sha},
        "firmware": {"path": "firmware.bin", "sha256": firmware_sha},
    }
    (root / "diagnostic_build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_valid_build_provenance(tmp_path: Path) -> None:
    checks = provenance_checks(_write_campaign(tmp_path))
    assert checks and all(passed for _, passed, _ in checks)


def test_tampered_build_log_fails(tmp_path: Path) -> None:
    root = _write_campaign(tmp_path)
    (root / "build.log").write_text((root / "build.log").read_text() + "tamper\n", encoding="utf-8")
    checks = dict((name, passed) for name, passed, _ in provenance_checks(root))
    assert checks["build-log-sha256"] is False


def test_build_source_marker_mismatch_fails(tmp_path: Path) -> None:
    root = _write_campaign(tmp_path)
    log = root / "build.log"
    text = log.read_text().replace("OEW_PROVENANCE_SOURCE_SHA=" + "a" * 40, "OEW_PROVENANCE_SOURCE_SHA=" + "b" * 40)
    log.write_text(text, encoding="utf-8")
    manifest = json.loads((root / "diagnostic_build_manifest.json").read_text())
    manifest["build"]["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    (root / "diagnostic_build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    checks = dict((name, passed) for name, passed, _ in provenance_checks(root))
    assert checks["source-build-binding"] is False


def test_define_marker_mismatch_fails(tmp_path: Path) -> None:
    root = _write_campaign(tmp_path)
    log = root / "build.log"
    text = log.read_text().replace("OEW_PROVENANCE_DEFINE_OEW_MAP_L3=1", "OEW_PROVENANCE_DEFINE_OEW_MAP_L3=0")
    log.write_text(text, encoding="utf-8")
    manifest = json.loads((root / "diagnostic_build_manifest.json").read_text())
    manifest["build"]["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    (root / "diagnostic_build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    checks = dict((name, passed) for name, passed, _ in provenance_checks(root))
    assert checks["define-OEW_MAP_L3"] is False
