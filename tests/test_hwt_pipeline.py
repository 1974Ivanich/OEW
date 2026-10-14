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
    res = run_hwt("run", "--sim", "--sim-hang", "UART_ReadLine",
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


# ── записанный отказ в конвейере: WAIVED — не PASS, ERROR не снимается ───

def _low_tr_fixture(tmp_path: Path, enc_error: int = 0) -> Path:
    """Значения симулятора, где Tr ниже порога прямого старта (как на плате).

    `enc_error` позволяет вывести канал энкодера из строя отдельно от Tr:
    вторая половина критерия шага 4 проверяется своим значением, а не общим
    состоянием фикстуры.
    """
    values = json.loads((ROOT / "tests" / "target" / "sim_values.json")
                        .read_text(encoding="utf-8"))
    values["field"]["g_motor_params.Tr_rotor_us"] = 60
    values["sym"]["enc_error"] = enc_error
    path = tmp_path / "sim_values_low_tr.json"
    path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return path


def _campaign_registry(tmp_path: Path, **overrides) -> Path:
    entry = {"id": "VF_CAMPAIGN", "case": "HW_START_PATH_READY",
             "check": "Tr достигает порога прямого encoder-старта",
             "reason": "шаг 4 паспорта не выполнен: Rr/Lm не измерены",
             "compensating": "кампания идёт open-loop V/f → handoff",
             "author": "тест", "date": "2026-10-14"}
    entry.update(overrides)
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps({"waivers": [entry]}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_simulated_refusal_waives_a_gate_but_never_calls_it_pass(
        tmp_path: Path) -> None:
    """Tr=60: с записью VF_CAMPAIGN — WAIVED(0), без записи — FAIL(1)."""
    fixture = _low_tr_fixture(tmp_path)
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--sim-values", str(fixture),
                  "--waivers", str(_campaign_registry(tmp_path)), "--case",
                  "HW_START_PATH_READY", "--out", str(out))
    assert res.returncode == 0, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "WAIVED"
    assert report["waivers"]["applied"][0]["id"] == "VF_CAMPAIGN"

    out2 = tmp_path / "hwt2"
    res2 = run_hwt("run", "--sim", "--sim-values", str(fixture), "--waivers",
                   str(tmp_path / "нет-реестра.json"), "--case",
                   "HW_START_PATH_READY", "--out", str(out2))
    assert res2.returncode == 1, res2.stdout + res2.stderr
    assert latest_report(out2)["verdict"] == "FAIL"


def test_simulated_error_is_not_waived_by_a_record(tmp_path: Path) -> None:
    """Незакрытая точка останова — ERROR; отказ не снимает ERROR."""
    fixture = _low_tr_fixture(tmp_path)
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--sim-values", str(fixture),
                  "--sim-hang", "UART_ReadLine", "--waivers",
                  str(_campaign_registry(tmp_path)), "--case",
                  "HW_START_PATH_READY", "--out", str(out))
    assert res.returncode == 2, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "ERROR"
    assert report["waivers"]["applied"] == []
    assert "ERROR" in report["waivers"]["not_applied"][0]["why"]


def test_simulated_encoder_error_is_not_waived_by_the_campaign_record(
        tmp_path: Path) -> None:
    """Ошибка канала энкодера — FAIL и с записью VF_CAMPAIGN: не снимается."""
    fixture = _low_tr_fixture(tmp_path, enc_error=2)
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--sim-values", str(fixture),
                  "--waivers", str(_campaign_registry(tmp_path)), "--case",
                  "HW_START_PATH_READY", "--out", str(out))
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "FAIL"
    case = [c for c in report["cases"] if c["id"] == "HW_START_PATH_READY"][0]
    enc = [c for c in case["checks"]
           if c["name"] == "ENC_GetError() == 0: канал энкодера исправен"][0]
    assert enc["ok"] is False and "waiver" not in enc
    assert report["waivers"]["applied"][0]["check"] == \
        "Tr достигает порога прямого encoder-старта"


def test_broken_registry_stops_the_run_before_the_target(tmp_path: Path) -> None:
    """Битая запись — ERROR(2) с отчётом, а не «отказов нет» и не запуск."""
    broken = tmp_path / "waivers.json"
    broken.write_text('{"waivers": [{"id": "X", "case": "HW_ALIVE"}]}',
                      encoding="utf-8")
    out = tmp_path / "hwt"
    res = run_hwt("run", "--sim", "--waivers", str(broken), "--out", str(out))
    assert res.returncode == 2, res.stdout + res.stderr
    report = latest_report(out)
    assert report["verdict"] == "ERROR"
    assert report["cases"][0]["id"] == "HWT_WAIVERS"

    res = run_hwt("preflight", "--waivers", str(broken))
    assert res.returncode == 2, res.stdout + res.stderr
    assert "реестр отказов" in res.stdout


def test_preflight_names_the_committed_refusal(tmp_path: Path) -> None:
    res = run_hwt("preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "отказы" in res.stdout and "1 записей" in res.stdout


def test_preflight_warns_when_the_recorded_scenario_is_not_in_this_run(
        tmp_path: Path) -> None:
    """Прогон с другим набором сценариев — предупреждение, не ошибка."""
    res = run_hwt("--scenarios", str(ROOT / "tests" / "target" /
                                      "hw_identity.py"), "preflight")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "не будет применена" in res.stdout
