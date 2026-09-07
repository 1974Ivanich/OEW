"""Tests for BOAR campaign scaffolding and pre-ingest checks."""

import json
from pathlib import Path

import pytest

from tools.boar_campaign_template import main as template_main
from tools.verify_boar_campaign_ready import main as verify_main


def test_scaffold_creates_all_files(tmp_path: Path) -> None:
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({"vcc_mv": 5000}), encoding="utf-8")
    campaign = tmp_path / "boar_campaign"

    rc = template_main(["--campaign-root", str(campaign), "--calibration", str(calib)])
    assert rc == 0
    assert (campaign / "logs").is_dir()
    assert (campaign / "la").is_dir()
    assert (campaign / "calibration" / "acs712_calibration.json").is_file()
    assert len(list((campaign / "logs").glob("*"))) == 48
    assert len(list((campaign / "la").glob("*"))) == 48
    assert (campaign / "logs" / "region_11_3.log").is_file()
    assert (campaign / "la" / "la_region_11_3.csv").is_file()


def test_scaffold_with_scope(tmp_path: Path) -> None:
    campaign = tmp_path / "boar_campaign"
    rc = template_main(["--campaign-root", str(campaign), "--with-scope"])
    assert rc == 0
    assert (campaign / "scope").is_dir()
    assert len(list((campaign / "scope").glob("*"))) == 48
    assert (campaign / "scope" / "scope_region_11_3.csv").is_file()


def test_verify_rejects_placeholders(tmp_path: Path) -> None:
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({"vcc_mv": 5000}), encoding="utf-8")
    campaign = tmp_path / "boar_campaign"
    template_main(["--campaign-root", str(campaign), "--calibration", str(calib)])

    rc = verify_main(["--campaign-root", str(campaign), "--scope-waiver"])
    assert rc == 1


def _filled_scope_csv() -> str:
    lines = [
        "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,scope_qualified,note",
    ]
    for i in range(1, 9):
        lines.append(f"{i},2510,2495,,110,15,1,ok")
    return "\n".join(lines) + "\n"


def test_verify_passes_when_files_filled(tmp_path: Path) -> None:
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({"vcc_mv": 5000}), encoding="utf-8")
    campaign = tmp_path / "boar_campaign"
    template_main(["--campaign-root", str(campaign), "--calibration", str(calib)])

    for log in (campaign / "logs").glob("*.log"):
        log.write_text("@MC:REC:dummy\n@MC:DRAIN:records=8\n", encoding="utf-8")
    for la in (campaign / "la").glob("*.csv"):
        la.write_text("D0,D1,D2\n1,1,1\n", encoding="utf-8")

    rc = verify_main(["--campaign-root", str(campaign), "--scope-waiver"])
    assert rc == 0


def test_verify_passes_with_scope(tmp_path: Path) -> None:
    """Without --scope-waiver, scope CSVs are required."""
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({"vcc_mv": 5000}), encoding="utf-8")
    campaign = tmp_path / "boar_campaign"
    template_main(["--campaign-root", str(campaign), "--calibration", str(calib),
                    "--with-scope"])

    for log in (campaign / "logs").glob("*.log"):
        log.write_text("@MC:REC:dummy\n@MC:DRAIN:records=8\n", encoding="utf-8")
    for la in (campaign / "la").glob("*.csv"):
        la.write_text("D0,D1,D2\n1,1,1\n", encoding="utf-8")
    for csv in (campaign / "scope").glob("*.csv"):
        csv.write_text(_filled_scope_csv(), encoding="utf-8")

    rc = verify_main(["--campaign-root", str(campaign)])
    assert rc == 0


def test_verify_rejects_missing_calibration(tmp_path: Path) -> None:
    campaign = tmp_path / "boar_campaign"
    template_main(["--campaign-root", str(campaign), "--no-stubs"])

    (campaign / "logs" / "region_0_0.log").write_text("ok", encoding="utf-8")
    (campaign / "la" / "la_region_0_0.csv").write_text("ok", encoding="utf-8")

    rc = verify_main(["--campaign-root", str(campaign), "--scope-waiver"])
    assert rc == 1


def test_verify_rejects_missing_files(tmp_path: Path) -> None:
    campaign = tmp_path / "boar_campaign"
    campaign.mkdir()
    (campaign / "logs").mkdir()
    (campaign / "la").mkdir()
    (campaign / "scope").mkdir()
    (campaign / "calibration").mkdir()

    rc = verify_main(["--campaign-root", str(campaign), "--scope-waiver"])
    assert rc == 1
