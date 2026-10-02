"""Тесты оффлайн-части резервного копирования Flash (без платы)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "hwt_backup.py"


def run_backup(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=str(ROOT),
                          capture_output=True, text=True, errors="replace",
                          timeout=120)


def test_from_file_writes_bin_hex_and_sums(tmp_path: Path) -> None:
    src = tmp_path / "sample.bin"
    src.write_bytes(bytes(range(256)) * 3)
    out = tmp_path / "backup"
    res = run_backup("--from-file", str(src), "--out", str(out),
                     "--stamp", "20260101_000000")
    assert res.returncode == 0, res.stdout + res.stderr

    bin_path = out / "flash_20260101_000000.bin"
    assert bin_path.exists() and bin_path.read_bytes() == src.read_bytes()

    sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    assert digest in sums
    assert sums.count("flash_20260101_000000.bin") == 1

    if shutil.which("arm-none-eabi-objcopy"):
        hex_path = out / "flash_20260101_000000.hex"
        assert hex_path.exists()
        text = hex_path.read_text(encoding="ascii")
        assert text.startswith(":")
        assert digest in sums


def test_verify_ok_and_mismatch(tmp_path: Path) -> None:
    src = tmp_path / "sample.bin"
    src.write_bytes(b"\x01\x02\x03\x04")
    out = tmp_path / "backup"
    assert run_backup("--from-file", str(src), "--out", str(out),
                      "--stamp", "s1").returncode == 0
    bin_path = out / "flash_s1.bin"

    res = run_backup("--verify", str(bin_path), "--out", str(out))
    assert res.returncode == 0 and "OK" in res.stdout

    bin_path.write_bytes(b"\x01\x02\x03\xFF")  # подмена байта
    res = run_backup("--verify", str(bin_path), "--out", str(out))
    assert res.returncode == 1 and "РАСХОЖДЕНИЕ" in res.stdout


def test_verify_unknown_file_is_error(tmp_path: Path) -> None:
    out = tmp_path / "backup"
    assert run_backup("--from-file", str(_make_bin(tmp_path)),
                      "--out", str(out), "--stamp", "s2").returncode == 0
    other = out / "flash_s2.bin"
    other.write_bytes(b"x")
    (out / "SHA256SUMS.txt").write_text("", encoding="utf-8")
    res = run_backup("--verify", str(other), "--out", str(out))
    assert res.returncode == 2


def test_from_file_missing_is_error(tmp_path: Path) -> None:
    res = run_backup("--from-file", str(tmp_path / "нет.bin"),
                     "--out", str(tmp_path / "backup"))
    assert res.returncode == 2


def _make_bin(tmp_path: Path) -> Path:
    p = tmp_path / "seed.bin"
    p.write_bytes(b"\x00\x11\x22\x33")
    return p


@pytest.mark.parametrize("flag", ["--no-hex"])
def test_no_hex_keeps_bin_and_sums(tmp_path: Path, flag: str) -> None:
    src = _make_bin(tmp_path)
    out = tmp_path / "backup"
    res = run_backup("--from-file", str(src), "--out", str(out),
                     "--stamp", "s3", flag)
    assert res.returncode == 0
    assert (out / "flash_s3.bin").exists()
    assert not (out / "flash_s3.hex").exists()
