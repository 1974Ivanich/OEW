"""Тесты переносимого верификатора манифеста.

Ключевой кейс: манифест, записанный с CRLF (как первый возврат ПК-3), должен проверяться
без ложного отказа — и, при явном запросе, нормализоваться в LF с повторной проверкой.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_manifest import detect_endings, read_manifest, verify  # noqa: E402

TOOL = ROOT / "tools" / "verify_manifest.py"


def make_folder(tmp: Path, *, eol: str = "lf", separator: str = "  ", marker: str = "") -> Path:
    folder = tmp / "return"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uart_identity.log").write_text("лог\n", encoding="utf-8")
    (folder / "SESSION_META.json").write_text('{"a": 1}\n', encoding="utf-8")
    digest = hashlib.sha256((folder / "uart_identity.log").read_bytes()).hexdigest()
    digest2 = hashlib.sha256((folder / "SESSION_META.json").read_bytes()).hexdigest()
    newline = "\r\n" if eol == "crlf" else "\n"
    text = (f"{digest}{separator}{marker}uart_identity.log{newline}"
            f"{digest2}{separator}{marker}SESSION_META.json{newline}")
    (folder / "SHA256SUMS.txt").write_bytes(text.encode("utf-8"))
    return folder


def run(folder: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), str(folder), *extra],
                          capture_output=True, text=True)


def test_crlf_manifest_verifies(tmp_path: Path) -> None:
    """Манифест с CRLF не должен давать ложный FAIL (реальный случай ПК-3)."""
    folder = make_folder(tmp_path, eol="crlf")
    assert detect_endings(folder / "SHA256SUMS.txt") == "crlf"
    proc = run(folder)
    assert proc.returncode == 0, proc.stdout
    assert "PASS" in proc.stdout
    assert "переводы строк: crlf" in proc.stdout


def test_lf_manifest_verifies(tmp_path: Path) -> None:
    folder = make_folder(tmp_path, eol="lf")
    proc = run(folder)
    assert proc.returncode == 0
    assert "переводы строк: lf" in proc.stdout


@pytest.mark.parametrize("separator,marker", [("  ", ""), ("  ", "*"), (" ", ""), ("\t", "")])
def test_entry_formats(tmp_path: Path, separator: str, marker: str) -> None:
    folder = make_folder(tmp_path, separator=separator, marker=marker)
    assert run(folder).returncode == 0


def test_missing_and_mismatched_and_untracked(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    (folder / "uart_identity.log").write_text("подменено\n", encoding="utf-8")
    (folder / "лишний.txt").write_text("не в манифесте\n", encoding="utf-8")
    report = verify(folder, folder / "SHA256SUMS.txt")
    assert report["mismatched"] == ["uart_identity.log"]
    assert report["untracked"] == ["лишний.txt"]
    assert report["ok"] is False
    proc = run(folder)
    assert proc.returncode == 1
    assert "FAIL" in proc.stdout and "не покрыто манифестом" in proc.stdout


def test_all_three_class_of_defects_are_reported(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    (folder / "uart_identity.log").unlink()
    (folder / "SESSION_META.json").write_text("{}", encoding="utf-8")
    (folder / "лишний.txt").write_text("x", encoding="utf-8")
    report = verify(folder, folder / "SHA256SUMS.txt")
    assert report["missing"] == ["uart_identity.log"]
    assert report["mismatched"] == ["SESSION_META.json"]
    assert report["untracked"] == ["лишний.txt"]


def test_normalize_lf_rewrites_and_reverifies(tmp_path: Path) -> None:
    folder = make_folder(tmp_path, eol="crlf")
    proc = run(folder, "--normalize-lf")
    assert proc.returncode == 0, proc.stdout
    assert "LF" in proc.stdout and "crlf -> lf" in proc.stdout
    raw = (folder / "SHA256SUMS.txt").read_bytes()
    assert b"\r" not in raw
    assert run(folder).returncode == 0
    assert read_manifest(folder / "SHA256SUMS.txt")[0][1] == "uart_identity.log"


def test_normalize_refused_on_broken_manifest(tmp_path: Path) -> None:
    """Сломанный манифест не «лечим» нормализацией — это дефект содержимого."""
    folder = make_folder(tmp_path, eol="crlf")
    (folder / "uart_identity.log").write_text("подменено\n", encoding="utf-8")
    before = (folder / "SHA256SUMS.txt").read_bytes()
    proc = run(folder, "--normalize-lf")
    assert proc.returncode == 1
    assert "нормализация отклонена" in proc.stdout
    assert (folder / "SHA256SUMS.txt").read_bytes() == before


def test_mixed_endings_detected(tmp_path: Path) -> None:
    folder = make_folder(tmp_path, eol="lf")
    path = folder / "SHA256SUMS.txt"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n", 1))
    assert detect_endings(path) == "mixed"
    assert run(folder).returncode == 0


def test_json_report_and_missing_manifest(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    out = tmp_path / "report.json"
    proc = run(folder, "--json", str(out))
    assert proc.returncode == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["entries"] == 2

    (folder / "SHA256SUMS.txt").unlink()
    proc = run(folder)
    assert proc.returncode == 2
    assert "BLOCKED" in proc.stdout


def test_explicit_manifest_name(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    (folder / "SHA256SUMS.txt").rename(folder / "RETURN_SHA256.txt")
    assert run(folder).returncode == 0
    assert run(folder, "--manifest", "RETURN_SHA256.txt").returncode == 0
    assert run(folder, "--manifest", "nope.txt").returncode == 2
