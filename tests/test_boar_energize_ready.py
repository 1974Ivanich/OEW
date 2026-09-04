"""Tests for the BOAR pre-energize readiness checklist script."""

import json
from pathlib import Path

import pytest

from tools.boar_energize_ready import build_parser, load_g0, main


def _g0_approved(extra: dict | None = None) -> dict:
    data = {
        "schema": "oew-test3-g0-approval-v1",
        "test_id": "TEST3",
        "test_name": "BOAR grid",
        "decision": "APPROVED",
        "scope": ["mapcap"],
        "profile_id": "0x424F4152",
        "board_revision": 7,
        "required_firmware_defines": {},
        "firmware_source_sha": "",
        "firmware_sha256": "",
        "calibration_path": "calibration/acs712_calibration.json",
    }
    if extra:
        data.update(extra)
    return data


def test_load_g0_rejects_pending(tmp_path: Path) -> None:
    path = tmp_path / "g0.json"
    path.write_text(json.dumps(_g0_approved({"decision": "PENDING"})),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="decision"):
        load_g0(path)


def test_load_g0_rejects_wrong_test_id(tmp_path: Path) -> None:
    path = tmp_path / "g0.json"
    path.write_text(json.dumps(_g0_approved({"test_id": "TEST2"})),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="test_id"):
        load_g0(path)


def test_load_g0_rejects_missing_mapcap_scope(tmp_path: Path) -> None:
    path = tmp_path / "g0.json"
    path.write_text(json.dumps(_g0_approved({"scope": ["vf"]})),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="mapcap"):
        load_g0(path)


def test_main_accepts_approved_with_dummy_firmware(tmp_path: Path) -> None:
    g0_path = tmp_path / "g0.json"
    g0_path.write_text(json.dumps(_g0_approved()), encoding="utf-8")

    fw = tmp_path / "firmware.bin"
    fw.write_bytes(b"dummy")

    out = tmp_path / "ready.json"

    rc = main([
        "--g0-approval", str(g0_path),
        "--operator", "Alice",
        "--watcher", "Bob",
        "--firmware-bin", str(fw),
        "--skip-build",
        "--allow-non-main",
        "--allow-dirty",
        "--assume-yes",
        "--output", str(out),
    ])
    assert rc == 0
    assert out.is_file()
    evidence = json.loads(out.read_text(encoding="utf-8"))
    assert evidence["operator"] == "Alice"
    assert evidence["watcher"] == "Bob"
    assert all(item["confirmed"] for item in evidence["safety"].values())


def test_main_rejects_mismatched_firmware_sha(tmp_path: Path) -> None:
    g0_path = tmp_path / "g0.json"
    g0_path.write_text(json.dumps(_g0_approved({
        "firmware_sha256": "0" * 64,
    })), encoding="utf-8")

    fw = tmp_path / "firmware.bin"
    fw.write_bytes(b"dummy")

    rc = main([
        "--g0-approval", str(g0_path),
        "--operator", "Alice",
        "--watcher", "Bob",
        "--firmware-bin", str(fw),
        "--skip-build",
        "--allow-non-main",
        "--allow-dirty",
        "--assume-yes",
        "--output", str(tmp_path / "ready.json"),
    ])
    assert rc == 2


def test_main_dry_run_does_not_write_file(tmp_path: Path) -> None:
    g0_path = tmp_path / "g0.json"
    g0_path.write_text(json.dumps(_g0_approved()), encoding="utf-8")

    fw = tmp_path / "firmware.bin"
    fw.write_bytes(b"dummy")

    out = tmp_path / "ready.json"
    rc = main([
        "--g0-approval", str(g0_path),
        "--operator", "Alice",
        "--watcher", "Bob",
        "--firmware-bin", str(fw),
        "--skip-build",
        "--allow-non-main",
        "--allow-dirty",
        "--assume-yes",
        "--dry-run",
        "--output", str(out),
    ])
    assert rc == 0
    assert not out.is_file()


def test_parser() -> None:
    args = build_parser().parse_args([
        "--g0-approval", "x.json",
        "--operator", "A",
        "--watcher", "B",
    ])
    assert args.operator == "A"
    assert args.watcher == "B"
