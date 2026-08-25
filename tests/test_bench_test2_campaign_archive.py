"""Offline regression tests for physical Test №2 campaign archiving."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO / "tools" / "bench_test2_campaign_archive.py"
SPEC = importlib.util.spec_from_file_location("bench_test2_campaign_archive", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ARCHIVE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ARCHIVE
SPEC.loader.exec_module(ARCHIVE)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def valid_g0_summary() -> dict:
    return {
        "schema": "h1-g0-check-summary-v1",
        "gate": "HIL_TEST2_G0",
        "verdict": "PASS",
        "checks": [],
    }


def write_campaign(tmp_path: Path, name: str = "test2_nohv_20260825T120000Z") -> Path:
    campaign = tmp_path / name
    campaign.mkdir()
    write_json(campaign / "g0_approval.json", {
        "gate": "HIL_TEST2_G0", "decision": "APPROVED", "approval_id": "G0-001",
    })
    write_json(campaign / "diagnostic_build_manifest.json", {
        "source_sha": "a" * 40, "firmware_sha256": "b" * 64,
    })
    (campaign / "diagnostic_build.log").write_text(
        "-DOEW_MAP_CAPTURE=1\n-DOEW_HOST_TEST=1\n", encoding="utf-8")
    write_json(campaign / "g0_check_summary.json", valid_g0_summary())
    write_json(campaign / "metadata.json", {"execution": {"mode": "PHYSICAL"}})
    write_json(campaign / "summary.json", {
        "verdict": {"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL"},
    })
    (campaign / "uart.log").write_text("boot\n@MC:STATUS:state=5\n", encoding="utf-8")
    (campaign / "sigrok_digital.csv").write_text("time,D0\n0,0\n", encoding="utf-8")
    (campaign / "sigrok_stdout.log").write_text("capture completed\n", encoding="utf-8")
    (campaign / "sigrok_stderr.log").write_text("", encoding="utf-8")
    (campaign / "dmm" / "readings.txt").parent.mkdir(parents=True)
    (campaign / "dmm" / "readings.txt").write_text("INV1=0.01V; INV2=0.02V\n", encoding="utf-8")
    return campaign


def archive(campaign: Path, output: Path) -> tuple[dict, Path, Path]:
    return ARCHIVE.archive_campaign(campaign, output)


def failed_ids(receipt: dict) -> set[str]:
    return {item["id"] for item in receipt["checks"] if item["result"] == "FAIL"}


def test_complete_campaign_archives_and_verifies_even_when_test_verdict_is_fail(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    original_summary = (campaign / "summary.json").read_bytes()
    original_uart = (campaign / "uart.log").read_bytes()
    receipt, zip_path, receipt_path = archive(campaign, tmp_path / "archives")

    assert receipt["verdict"] == "PASS"
    assert zip_path.is_file()
    assert receipt_path.is_file()
    assert not (campaign / "campaign_manifest.json").exists()
    assert (campaign / "summary.json").read_bytes() == original_summary
    assert (campaign / "uart.log").read_bytes() == original_uart
    verified = ARCHIVE.verify_archive(zip_path, receipt_path)
    assert verified["verdict"] == "PASS"
    assert receipt["archive_sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()


def test_same_campaign_bytes_produce_identical_archives(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    receipt_a, zip_a, _ = archive(campaign, tmp_path / "archive_a")
    receipt_b, zip_b, _ = archive(campaign, tmp_path / "archive_b")

    assert receipt_a["verdict"] == receipt_b["verdict"] == "PASS"
    assert zip_a.read_bytes() == zip_b.read_bytes()
    assert receipt_a["archive_sha256"] == receipt_b["archive_sha256"]


@pytest.mark.parametrize(
    ("name", "mutate", "expected_check"),
    [
        (
            "missing_uart",
            lambda campaign: (campaign / "uart.log").unlink(),
            "required.uart_log",
        ),
        (
            "missing_sigrok_csv",
            lambda campaign: (campaign / "sigrok_digital.csv").unlink(),
            "required.sigrok_csv",
        ),
        (
            "ambiguous_g0_summary",
            lambda campaign: write_json(campaign / "g0" / "g0_check_summary.json", valid_g0_summary()),
            "required.g0_summary",
        ),
        (
            "g0_fail",
            lambda campaign: _set_g0_verdict(campaign, "FAIL"),
            "g0.verdict",
        ),
        (
            "wrong_g0_gate",
            lambda campaign: _set_g0_gate(campaign, "OTHER_GATE"),
            "g0.gate",
        ),
        (
            "malformed_g0_json",
            lambda campaign: (campaign / "g0_check_summary.json").write_text("{bad", encoding="utf-8"),
            "g0.summary.json",
        ),
    ],
)
def test_evidence_failures_produce_recovery_archive_and_fail_receipt(
    tmp_path: Path,
    name: str,
    mutate: Callable[[Path], None],
    expected_check: str,
) -> None:
    campaign = write_campaign(tmp_path)
    mutate(campaign)

    receipt, zip_path, receipt_path = archive(campaign, tmp_path / "archives")

    assert receipt["verdict"] == "FAIL", name
    assert expected_check in failed_ids(receipt)
    assert zip_path.is_file()
    assert receipt_path.is_file()
    assert ARCHIVE.verify_archive(zip_path, receipt_path)["verdict"] == "FAIL"


def test_output_must_be_disjoint_from_campaign(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)

    with pytest.raises(ValueError, match="disjoint"):
        archive(campaign, campaign / "archives")


def test_existing_archive_or_receipt_cannot_be_overwritten(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    output = tmp_path / "archives"
    _, zip_path, receipt_path = archive(campaign, output)
    archive_bytes = zip_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()

    with pytest.raises(ValueError, match="already exists"):
        archive(campaign, output)

    assert zip_path.read_bytes() == archive_bytes
    assert receipt_path.read_bytes() == receipt_bytes


def test_symlink_evidence_is_rejected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    original = campaign / "uart.log"
    linked = campaign / "linked_uart.log"
    try:
        os.symlink(original, linked)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable in this test environment")

    receipt, _, _ = archive(campaign, tmp_path / "archives")

    assert receipt["verdict"] == "FAIL"
    assert "tree.file.linked_uart.log" in failed_ids(receipt)


def test_archive_tampering_is_detected_by_receipt_hash(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    _, zip_path, receipt_path = archive(campaign, tmp_path / "archives")
    with zip_path.open("ab") as target:
        target.write(b"tamper")

    result = ARCHIVE.verify_archive(zip_path, receipt_path)

    assert result["verdict"] == "FAIL"
    assert "archive.sha256" in {item["id"] for item in result["checks"] if item["result"] == "FAIL"}


def test_receipt_tampering_is_detected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    _, zip_path, receipt_path = archive(campaign, tmp_path / "archives")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["internal_manifest_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = ARCHIVE.verify_archive(zip_path, receipt_path)

    assert result["verdict"] == "FAIL"
    assert "receipt.internal_manifest_sha256" in {
        item["id"] for item in result["checks"] if item["result"] == "FAIL"
    }


def test_cli_archive_and_verify_return_zero_for_accepted_campaign(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    output = tmp_path / "archives"
    archived = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "archive", "--campaign", str(campaign),
         "--output-dir", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    zip_path = output / (campaign.name + ".evidence.zip")
    receipt_path = output / (campaign.name + ".archive_receipt.json")
    verified = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "verify", "--archive", str(zip_path),
         "--receipt", str(receipt_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert archived.returncode == 0
    assert "ARCHIVE=PASS" in archived.stdout
    assert verified.returncode == 0
    assert "ARCHIVE_VERIFY=PASS" in verified.stdout


def _set_g0_verdict(campaign: Path, verdict: str) -> None:
    path = campaign / "g0_check_summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["verdict"] = verdict
    write_json(path, payload)


def _set_g0_gate(campaign: Path, gate: str) -> None:
    path = campaign / "g0_check_summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["gate"] = gate
    write_json(path, payload)
