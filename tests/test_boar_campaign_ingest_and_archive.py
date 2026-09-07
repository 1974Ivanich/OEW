"""Tests for BOAR campaign ingest wrapper and deterministic archive."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.boar_campaign_archive import _sha256_file, main as archive_main
from tools.boar_campaign_template import main as template_main


@pytest.fixture
def ready_campaign(tmp_path: Path) -> Path:
    """A campaign with placeholder files replaced by minimal valid content."""
    calib = tmp_path / "calib.json"
    calib.write_text('{"vcc_mv": 5020}', encoding="utf-8")
    campaign = tmp_path / "boar_ready"
    template_main(["--campaign-root", str(campaign), "--calibration", str(calib)])

    # Fill logs and LA traces with non-placeholder text.
    log_text = "@MC:REC:dummy\n@MC:DRAIN:records=8\n"
    la_text = "D0,D1,D2\n1,1,1\n"
    for log in (campaign / "logs").glob("*.log"):
        log.write_text(log_text, encoding="utf-8")
    for la in (campaign / "la").glob("*.csv"):
        la.write_text(la_text, encoding="utf-8")
    return campaign


def test_ingest_wrapper_rejects_unready(tmp_path: Path) -> None:
    campaign = tmp_path / "not_ready"
    template_main(["--campaign-root", str(campaign), "--no-stubs"])
    (campaign / "logs").mkdir(exist_ok=True)
    (campaign / "la").mkdir(exist_ok=True)
    (campaign / "scope").mkdir(exist_ok=True)
    (campaign / "calibration").mkdir(exist_ok=True)

    script = Path(__file__).resolve().parent.parent / "tools" / "boar_campaign_ingest.py"
    res = subprocess.run(
        [sys.executable, str(script), "--campaign-root", str(campaign)],
        text=True,
        capture_output=True,
    )
    assert res.returncode != 0
    assert "calibration" in res.stderr or "not found" in res.stderr.lower()


def test_archive_requires_manifest_and_samples(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "campaign").mkdir()
    rc = archive_main(["--campaign-root", str(empty)])
    assert rc == 2


def test_archive_is_deterministic(ready_campaign: Path, tmp_path: Path) -> None:
    # Archive only requires campaign/manifest.json + samples.jsonl to exist.
    (ready_campaign / "campaign").mkdir(exist_ok=True)
    (ready_campaign / "campaign" / "manifest.json").write_text("{}", encoding="utf-8")
    (ready_campaign / "campaign" / "samples.jsonl").write_text("\n", encoding="utf-8")

    zip1 = tmp_path / "a.zip"
    zip2 = tmp_path / "b.zip"

    rc = archive_main(["--campaign-root", str(ready_campaign), "--out", str(zip1)])
    assert rc == 0

    # Copy campaign and archive again; content match => deterministic archive.
    campaign2 = tmp_path / "copy"
    shutil.copytree(ready_campaign, campaign2)
    rc = archive_main(["--campaign-root", str(campaign2), "--out", str(zip2)])
    assert rc == 0

    assert zip1.read_bytes() == zip2.read_bytes()
    assert _sha256_file(zip1) == _sha256_file(zip2)

    receipt = zip1.with_suffix(".receipt.json")
    assert receipt.is_file()
    data = receipt.read_text(encoding="utf-8")
    assert _sha256_file(zip1) in data
