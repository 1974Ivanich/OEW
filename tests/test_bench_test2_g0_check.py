"""Offline regression tests for the Test №2 Hard Gate G0 validator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO / "tools" / "bench_test2_g0_check.py"
SPEC = importlib.util.spec_from_file_location("bench_test2_g0_check", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
G0 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = G0
SPEC.loader.exec_module(G0)

SOURCE_SHA = "a" * 40
FIRMWARE = b"test2-g0-diagnostic-firmware\x00"
FIRMWARE_SHA256 = hashlib.sha256(FIRMWARE).hexdigest()


def required_build_log() -> str:
    return "\n".join(
        "arm-none-eabi-gcc -D" + name + "=" + value
        for name, value in G0.REQUIRED_DEFINES.items()
    ) + "\nBuild complete!\n"


def valid_approval() -> dict:
    return {
        "schema": G0.APPROVAL_SCHEMA,
        "gate": G0.GATE,
        "decision": "APPROVED",
        "role": "safety-owner",
        "approval_id": "HIL-G0-20260825-001",
        "approver": "Safety Owner",
        "approved_at": "2026-08-25T12:00:00Z",
        "source_sha": SOURCE_SHA,
        "firmware_sha256": FIRMWARE_SHA256,
        "approved_extra_defines": [],
        "scope": dict(G0.REQUIRED_SCOPE),
    }


def valid_manifest() -> dict:
    return {
        "schema": G0.MANIFEST_SCHEMA,
        "gate": G0.GATE,
        "target": G0.TARGET,
        "test": G0.TEST,
        "source_sha": SOURCE_SHA,
        "defines_complete": True,
        "defines": dict(G0.REQUIRED_DEFINES),
        "firmware": {
            "path": "firmware-diagnostic.bin",
            "sha256": FIRMWARE_SHA256,
        },
    }


def write_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "test2_nohv_20260825T120000Z"
    campaign.mkdir()
    (campaign / "firmware-diagnostic.bin").write_bytes(FIRMWARE)
    (campaign / "diagnostic_build.log").write_text(required_build_log(), encoding="utf-8")
    (campaign / "g0_approval.json").write_text(
        json.dumps(valid_approval()), encoding="utf-8")
    (campaign / "diagnostic_build_manifest.json").write_text(
        json.dumps(valid_manifest()), encoding="utf-8")
    return campaign


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def failing_ids(campaign: Path) -> set[str]:
    summary = G0.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    return {check["id"] for check in summary["checks"] if check["result"] == "FAIL"}


def test_valid_campaign_passes_and_is_explicitly_offline(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)

    summary = G0.validate_campaign(campaign)

    assert summary["gate"] == "HIL_TEST2_G0"
    assert summary["verdict"] == "PASS"
    assert summary["execution"] == {"mode": "OFFLINE", "hardware_access": False}
    assert all(check["result"] == "PASS" for check in summary["checks"])


def test_hs1_commissioning_release_is_required_and_not_forbidden() -> None:
    assert G0.REQUIRED_DEFINES["OEW_HS1_COMMISSIONING_RELEASE"] == "1"
    assert "OEW_HS1_COMMISSIONING_RELEASE" not in G0.FORBIDDEN_DEFINES


def test_missing_hs1_define_fails(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    manifest_path = campaign / "diagnostic_build_manifest.json"
    manifest = load(manifest_path)
    del manifest["defines"]["OEW_HS1_COMMISSIONING_RELEASE"]
    save(manifest_path, manifest)

    failed = failing_ids(campaign)

    assert "manifest.define.OEW_HS1_COMMISSIONING_RELEASE" in failed


def test_cli_writes_summary_and_returns_zero_only_for_pass(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--campaign", str(campaign)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "HARD_GATE_G0=PASS" in completed.stdout
    summary = load(campaign / "g0_check_summary.json")
    assert summary["verdict"] == "PASS"
    assert summary["gate"] == G0.GATE


@pytest.mark.parametrize(
    ("name", "mutate", "expected_check"),
    [
        (
            "missing_approval",
            lambda campaign: (campaign / "g0_approval.json").unlink(),
            "approval.present",
        ),
        (
            "rejected_approval",
            lambda campaign: _mutate_approval(campaign, "decision", "REJECTED"),
            "approval.decision",
        ),
        (
            "approval_without_physical_execution_scope",
            lambda campaign: _mutate_scope(campaign, "allows_physical_nohv_execution", False),
            "approval.scope.allows_physical_nohv_execution",
        ),
        (
            "approval_without_dc_link_ban",
            lambda campaign: _mutate_scope(campaign, "forbids_dc_link", False),
            "approval.scope.forbids_dc_link",
        ),
        (
            "source_sha_mismatch",
            lambda campaign: _mutate_approval(campaign, "source_sha", "b" * 40),
            "identity.source_sha",
        ),
        (
            "firmware_sha_mismatch",
            lambda campaign: _mutate_approval(campaign, "firmware_sha256", "b" * 64),
            "identity.firmware_sha256",
        ),
        (
            "wrong_required_define",
            lambda campaign: _mutate_define(campaign, "OEW_HOST_TEST", "0"),
            "manifest.define.OEW_HOST_TEST",
        ),
        (
            "forbidden_energise_define",
            lambda campaign: _add_define(campaign, "OEW_STAGE_A", "1"),
            "manifest.forbidden_defines",
        ),
        (
            "unapproved_extra_define",
            lambda campaign: _add_define(campaign, "OEW_DIAGNOSTIC_TRACE", "1"),
            "identity.extra_defines",
        ),
        (
            "missing_build_log_token",
            lambda campaign: (campaign / "diagnostic_build.log").write_text(
                "-DOEW_MAP_CAPTURE=1\n", encoding="utf-8"),
            "build_log.token.OEW_HOST_TEST",
        ),
        (
            "binary_path_escape",
            lambda campaign: _mutate_firmware(campaign, "path", "../other.bin"),
            "firmware.path",
        ),
        (
            "missing_binary",
            lambda campaign: (campaign / "firmware-diagnostic.bin").unlink(),
            "firmware.present",
        ),
        (
            "binary_hash_mismatch",
            lambda campaign: (campaign / "firmware-diagnostic.bin").write_bytes(b"tampered"),
            "firmware.sha256",
        ),
        (
            "malformed_manifest",
            lambda campaign: (campaign / "diagnostic_build_manifest.json").write_text(
                "{not json", encoding="utf-8"),
            "manifest.json",
        ),
    ],
)
def test_fail_closed_for_invalid_g0_evidence(
    tmp_path: Path,
    name: str,
    mutate: Callable[[Path], None],
    expected_check: str,
) -> None:
    campaign = write_campaign(tmp_path)
    mutate(campaign)

    failed = failing_ids(campaign)

    assert expected_check in failed, name


def test_extra_define_passes_only_when_explicitly_approved(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    _add_define(campaign, "OEW_DIAGNOSTIC_TRACE", "1")
    approval_path = campaign / "g0_approval.json"
    approval = load(approval_path)
    approval["approved_extra_defines"] = ["OEW_DIAGNOSTIC_TRACE"]
    save(approval_path, approval)

    summary = G0.validate_campaign(campaign)

    assert summary["verdict"] == "PASS"


def test_cli_returns_two_and_persists_fail_summary(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    _mutate_scope(campaign, "forbids_stage_a", False)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--campaign", str(campaign)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "HARD_GATE_G0=FAIL" in completed.stdout
    assert load(campaign / "g0_check_summary.json")["verdict"] == "FAIL"


def _mutate_approval(campaign: Path, key: str, value: object) -> None:
    path = campaign / "g0_approval.json"
    approval = load(path)
    approval[key] = value
    save(path, approval)


def _mutate_scope(campaign: Path, key: str, value: object) -> None:
    path = campaign / "g0_approval.json"
    approval = load(path)
    approval["scope"][key] = value
    save(path, approval)


def _mutate_define(campaign: Path, key: str, value: object) -> None:
    path = campaign / "diagnostic_build_manifest.json"
    manifest = load(path)
    manifest["defines"][key] = value
    save(path, manifest)


def _add_define(campaign: Path, key: str, value: object) -> None:
    _mutate_define(campaign, key, value)


def _mutate_firmware(campaign: Path, key: str, value: object) -> None:
    path = campaign / "diagnostic_build_manifest.json"
    manifest = load(path)
    manifest["firmware"][key] = value
    save(path, manifest)
