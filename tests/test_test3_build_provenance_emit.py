from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.test3_build_provenance_emit import main as emit_main
from tools.test3_build_provenance_check import provenance_checks


def test_emitted_provenance_binds_actual_binary(tmp_path: Path, monkeypatch):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"test3-firmware-bytes")
    campaign = tmp_path / "campaign"
    source_sha = "a" * 40
    monkeypatch.setattr(
        "sys.argv",
        ["test3_build_provenance_emit.py", "--campaign-root", str(campaign), "--source-sha", source_sha, "--firmware", str(firmware)],
    )
    # The emitter requires the firmware to be retained inside campaign root.
    retained = campaign / "firmware.bin"
    campaign.mkdir()
    retained.write_bytes(firmware.read_bytes())
    monkeypatch.setattr("sys.argv", ["test3_build_provenance_emit.py", "--campaign-root", str(campaign), "--source-sha", source_sha, "--firmware", str(retained)])
    assert emit_main() == 0
    checks = provenance_checks(campaign)
    assert checks and all(passed for _, passed, _ in checks)

    manifest = json.loads((campaign / "diagnostic_build_manifest.json").read_text())
    assert manifest["firmware"]["sha256"] == hashlib.sha256(retained.read_bytes()).hexdigest()


def test_emitted_provenance_fails_after_binary_tamper(tmp_path: Path, monkeypatch):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    firmware = campaign / "firmware.bin"
    firmware.write_bytes(b"original")
    monkeypatch.setattr("sys.argv", ["emit", "--campaign-root", str(campaign), "--source-sha", "a" * 40, "--firmware", str(firmware)])
    emit_main()
    firmware.write_bytes(b"tampered")
    checks = provenance_checks(campaign)
    assert any(name == "firmware-bytes-binding" and not passed for name, passed, _ in checks)
