"""Тесты конвейера HWT без железа: предел времени, вердикты, отчёты, контракты.

Проверяется именно конвейер (симуляция, DDTT-6.7): сверка CRC образа,
формирование result.json/junit.xml, коды выхода и то, что ERROR не становится
PASS. Поведение реального железа эти тесты не подтверждают.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HWT = ROOT / "tools" / "hwt.py"
FIRMWARE = ROOT / "build" / "firmware.elf"

pytestmark = pytest.mark.skipif(not FIRMWARE.exists(),
                                reason="нужна сборка (make) для образа ELF")


def run_hwt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HWT), *args], cwd=str(ROOT),
                          capture_output=True, text=True, errors="replace",
                          timeout=300)


def latest_report(out: Path) -> dict:
    dirs = sorted(p for p in out.iterdir() if p.is_dir())
    assert dirs, f"нет отчётов в {out}"
    return json.loads((dirs[-1] / "result.json").read_text(encoding="utf-8"))


def test_list_and_preflight_pass(tmp_path: Path) -> None:
    res = run_hwt("list")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "HW_ALIVE" in res.stdout and "HW_SAFETY_IDLE" in res.stdout

    res = run_hwt("preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "CRC образа" in res.stdout and "OK" in res.stdout


def test_preflight_fails_on_missing_contract(tmp_path: Path) -> None:
    scenario = tmp_path / "hw_bad_contract.py"
    scenario.write_text(
        'from hwt_target import case\n\n\n'
        '@case("HW_BAD", contracts=("нет_такого_символа_в_образе",))\n'
        'def hw_bad(t):\n    t.check("x", 1, 1)\n', encoding="utf-8")
    res = run_hwt("--scenarios", str(scenario), "preflight")
    assert res.returncode == 2, res.stdout + res.stderr
    assert "контракт не выполнен" in res.stdout


def test_simulated_run_passes_and_is_marked(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--out", str(out))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "СИМУЛЯЦИЯ" in res.stdout
    report = latest_report(out)
    assert report["simulated"] is True
    assert report["verdict"] == "PASS"
    assert report["image_crc32"]
    assert {c["section"] for c in report["image_sections"]} >= {".text"}
    ids = [c["id"] for c in report["cases"]]
    assert "HWT_IMAGE" in ids and "HW_SAFETY_IDLE" in ids


def test_simulated_corrupt_image_is_fail_not_pass(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--sim-corrupt", ".text", "--out", str(out))
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "FAIL"
    image = [c for c in report["cases"] if c["id"] == "HWT_IMAGE"][0]
    assert image["verdict"] == "FAIL"
    bad = [c for c in image["checks"] if not c["ok"]]
    assert bad and ".text" in bad[0]["name"]


def test_simulated_hang_is_error(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--sim-hang", "CLI_ProcessLine",
                  "--case", "HW_ALIVE", "--out", str(out))
    assert res.returncode == 2, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "ERROR"
    alive = [c for c in report["cases"] if c["id"] == "HW_ALIVE"][0]
    assert alive["verdict"] == "ERROR" and alive["error"]


def test_junit_written_and_parseable(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET
    out = tmp_path / "hwt"
    assert run_hwt("run", "--sim", "--out", str(out)).returncode == 0
    dirs = sorted(p for p in out.iterdir() if p.is_dir())
    xml_path = dirs[-1] / "junit.xml"
    root = ET.parse(xml_path).getroot()
    assert root.tag == "testsuite"
    assert int(root.attrib["tests"]) >= 4
    assert root.attrib["errors"] == "0"


def test_no_hardware_means_error_not_pass(tmp_path: Path) -> None:
    """Без инструментов запуск на стенде обязан дать ERROR (2), не PASS."""
    out = tmp_path / "hwt"
    res = subprocess.run(
        [sys.executable, str(HWT), "--config", str(tmp_path / "нет.toml"),
         "run", "--out", str(out)], cwd=str(ROOT), capture_output=True,
        text=True, errors="replace", timeout=120)
    assert res.returncode == 2
