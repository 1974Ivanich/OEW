#!/usr/bin/env python3
"""Tests for tools/acs712_nohv_validator.py.

These tests exercise the fail-closed offline validator for the ACS712 no-HV
checkout evidence package.  They never touch hardware.
"""

import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure project tools are importable without side effects.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import acs712_nohv_validator as validator


VALID_SHA = "a" * 40


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=2))


def _make_valid_campaign(root: Path) -> None:
    _write_text(root / "identity" / "source_sha.txt", VALID_SHA + " 2026-09-04T12:00:00Z\n")
    _write_text(root / "identity" / "ci_run_url.txt", "https://github.com/1974Ivanich/OEW/actions/runs/12345\n")
    _write_text(root / "identity" / "flash_verify.log", "Flash done. Verify OK.\n")
    _write_text(
        root / "identity" / "terminal_identity.log",
        "> sysinfo\nbranch=main sha=" + VALID_SHA + "\n",
    )

    _write_json(
        root / "calibration" / "acs712_calibration.json",
        {
            "vcc_mv": 5000,
            "sensors": {
                "U": {"v0_mv": 2498, "sens_mv_per_a": 100.0},
                "V": {"v0_mv": 2504, "sens_mv_per_a": 100.0},
            },
        },
    )

    scope_path = root / "scope" / "scope_zero.csv"
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    with scope_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["pulse", "ref_u_mv", "ref_v_mv", "ref_w_mv", "margin_ticks", "blanking_ticks", "scope_qualified", "note"]
        )
        writer.writerow([1, 2510, 2495, "", 110, 15, 0, "zero-current"])

    _write_text(
        root / "dmm" / "zero_current_offsets_worksheet.md",
        "| Параметр | Измерено | Ожидаемо |\n"
        "|---|---|---|\n"
        "| Vcc | 5000 | 5000 |\n"
        "| v0_U | 2498 | 2500 |\n"
        "| v0_V | 2504 | 2500 |\n"
        "| Шум U | 35 | <110 |\n"
        "| Шум V | 40 | <110 |\n",
    )

    _write_text(
        root / "observations" / "wiring_check.md",
        "| Gate | Status |\n"
        "|---|---|\n"
        "| G-01 | PASS |\n"
        "| G-02 | PASS |\n"
        "| G-03 | PASS |\n"
        "| G-04 | PASS |\n"
        "| G-05 | PASS |\n"
        "| G-06 | PASS |\n",
    )

    _write_text(
        root / "summary" / "acs712_nohv_summary.md",
        "| Domain | Verdict |\n"
        "|---|---|\n"
        "| Overall | PASS |\n",
    )


def test_valid_campaign_passes(tmp_path: Path) -> None:
    campaign = tmp_path / "valid"
    _make_valid_campaign(campaign)
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "PASS"
    assert all(check["result"] == "PASS" for check in summary["checks"])


def test_main_valid_campaign_exit_zero(tmp_path: Path) -> None:
    campaign = tmp_path / "valid"
    _make_valid_campaign(campaign)
    rc = validator.main(["--campaign", str(campaign)])
    assert rc == 0
    assert (campaign / "acs712_nohv_validation_summary.json").is_file()


def test_missing_files_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "empty"
    campaign.mkdir()
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any("file." in check["id"] and check["result"] == "FAIL" for check in summary["checks"])


def test_main_missing_files_exit_two(tmp_path: Path) -> None:
    campaign = tmp_path / "empty"
    campaign.mkdir()
    rc = validator.main(["--campaign", str(campaign)])
    assert rc == 2


def test_bad_source_sha_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_sha"
    _make_valid_campaign(campaign)
    _write_text(campaign / "identity" / "source_sha.txt", "not-a-sha\n")
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any(check["id"] == "identity.source_sha.format" and check["result"] == "FAIL" for check in summary["checks"])


def test_missing_ci_url_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_url"
    _make_valid_campaign(campaign)
    _write_text(campaign / "identity" / "ci_run_url.txt", "\n")
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any(check["id"] == "identity.ci_run_url.present" and check["result"] == "FAIL" for check in summary["checks"])


def test_flash_log_without_success_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_flash"
    _make_valid_campaign(campaign)
    _write_text(campaign / "identity" / "flash_verify.log", "Connection failed.\n")
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any(check["id"] == "identity.flash_verify.success" and check["result"] == "FAIL" for check in summary["checks"])


def test_v0_out_of_range_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_v0"
    _make_valid_campaign(campaign)
    _write_json(
        campaign / "calibration" / "acs712_calibration.json",
        {
            "vcc_mv": 5000,
            "sensors": {
                "U": {"v0_mv": 100, "sens_mv_per_a": 100.0},
                "V": {"v0_mv": 2504, "sens_mv_per_a": 100.0},
            },
        },
    )
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any("sensors.U.v0_mv.range" in check["id"] and check["result"] == "FAIL" for check in summary["checks"])


def test_sensitivity_out_of_range_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_sens"
    _make_valid_campaign(campaign)
    _write_json(
        campaign / "calibration" / "acs712_calibration.json",
        {
            "vcc_mv": 5000,
            "sensors": {
                "U": {"v0_mv": 2498, "sens_mv_per_a": 150.0},
                "V": {"v0_mv": 2504, "sens_mv_per_a": 100.0},
            },
        },
    )
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any("sensors.U.sens_mv_per_a.range" in check["id"] and check["result"] == "FAIL" for check in summary["checks"])


def test_scope_qualified_non_zero_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_scope"
    _make_valid_campaign(campaign)
    scope_path = campaign / "scope" / "scope_zero.csv"
    with scope_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["pulse", "ref_u_mv", "ref_v_mv", "ref_w_mv", "margin_ticks", "blanking_ticks", "scope_qualified", "note"]
        )
        writer.writerow([1, 2510, 2495, "", 110, 15, 1, "zero-current"])
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any("scope_qualified.zero" in check["id"] and check["result"] == "FAIL" for check in summary["checks"])


def test_summary_verdict_fail_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_summary"
    _make_valid_campaign(campaign)
    _write_text(
        campaign / "summary" / "acs712_nohv_summary.md",
        "| Domain | Verdict |\n"
        "|---|---|\n"
        "| Overall | FAIL |\n",
    )
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any(check["id"] == "summary.verdict.pass" and check["result"] == "FAIL" for check in summary["checks"])


def test_wiring_check_with_fail_fails(tmp_path: Path) -> None:
    campaign = tmp_path / "bad_wiring"
    _make_valid_campaign(campaign)
    _write_text(
        campaign / "observations" / "wiring_check.md",
        "| Gate | Status |\n"
        "|---|---|\n"
        "| G-01 | PASS |\n"
        "| G-02 | FAIL |\n"
        "| G-03 | PASS |\n"
        "| G-04 | PASS |\n"
        "| G-05 | PASS |\n"
        "| G-06 | PASS |\n",
    )
    summary = validator.validate_campaign(campaign)
    assert summary["verdict"] == "FAIL"
    assert any(check["id"] == "observations.wiring_check.gates" and check["result"] == "FAIL" for check in summary["checks"])
