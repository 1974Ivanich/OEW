"""Сквозные тесты HWT на цели-заглушке (мок-сервер RSP) — без железа.

Проверяется весь путь на стенде, кроме самого кремния: подключение по RSP,
идентичность цели, сверка CRC образа, исполнение сценариев из tests/target,
вердикты и отчёты. Мок отдаёт данные настоящего ELF, поэтому проверки
сценариев проходят по-настоящему, а не «потому что так настроено».
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.mock_rsp_server import MockRspServer, make_target

ROOT = Path(__file__).resolve().parents[1]
HWT = ROOT / "tools" / "hwt.py"
FIRMWARE = ROOT / "build" / "firmware.elf"

pytestmark = pytest.mark.skipif(not FIRMWARE.exists(),
                                reason="нужна сборка (make) для образа ELF")


def run_hwt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HWT), *args], cwd=str(ROOT),
                          capture_output=True, text=True, errors="replace",
                          timeout=300)


def latest_result(out: Path) -> dict:
    dirs = sorted(p for p in out.iterdir() if p.is_dir())
    return json.loads((dirs[-1] / "result.json").read_text(encoding="utf-8"))


def write_stand(tmp_path: Path, **probe) -> Path:
    cfg = tmp_path / "stand.toml"
    cfg.write_text(
        "[probe]\n"
        f"device_id = {probe.get('device_id', 0x469)}\n"
        f"flash_size_kb = {probe.get('flash_size_kb', 512)}\n"
        f"policy = \"{probe.get('policy', 'deny')}\"\n"
        "[server]\ngdb_port = 3333\n"
        "[run]\nstate = \"reset-halt\"\n"
        "[safety]\nallow_injections = false\n", encoding="utf-8")
    return cfg


def test_full_pipeline_passes_and_records_recovery(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path)), "run",
                      "--connect", f"127.0.0.1:{srv.port}", "--out", str(out))
    assert res.returncode == 0, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "PASS"
    ids = {c["id"]: c["verdict"] for c in report["cases"]}
    assert ids.get("HWT_IMAGE") == "PASS"
    assert ids.get("HWT_TARGET_IDENTITY") == "PASS"
    assert ids.get("HW_ALIVE") == "PASS"
    assert ids.get("HW_LOOP_PROGRESS") == "PASS"
    assert ids.get("HW_SAFETY_IDLE") == "PASS"
    assert report["recovery"]["ok"] is True


def test_stale_firmware_is_fail_and_scenarios_not_run(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE),
                                   corrupt_section=".text")) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path)), "run",
                      "--connect", f"127.0.0.1:{srv.port}", "--out", str(out))
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    ids = {c["id"]: c["verdict"] for c in report["cases"]}
    assert ids["HWT_IMAGE"] == "FAIL"
    assert "HW_ALIVE" not in ids, "при расхождении образа сценарии не идут"


def test_wrong_device_is_denied_by_policy(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE), device_id=0x999)) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path, device_id=0x469)),
                      "run", "--connect", f"127.0.0.1:{srv.port}",
                      "--out", str(out))
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    ids = {c["id"]: c["verdict"] for c in report["cases"]}
    assert ids["HWT_TARGET_IDENTITY"] == "FAIL"
    assert "HW_ALIVE" not in ids


def test_wrong_device_with_warn_policy_continues(tmp_path: Path) -> None:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE), device_id=0x999)) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path, device_id=0x469,
                                                  policy="warn")),
                      "run", "--connect", f"127.0.0.1:{srv.port}",
                      "--out", str(out))
    assert res.returncode == 0, res.stdout + res.stderr
    report = latest_result(out)
    assert "HW_ALIVE" in {c["id"] for c in report["cases"]}


def test_unreachable_checkpoint_is_error_not_pass(tmp_path: Path) -> None:
    scenario = tmp_path / "hw_hang.py"
    scenario.write_text(
        'from hwt_target import case\n\n'
        '@case("HW_HANG", timeout_s=3, contracts=("main",))\n'
        'def hw_hang(t):\n    t.reach("main")\n    t.check("x", 1, 1)\n',
        encoding="utf-8")
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE), hang=True)) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path)),
                      "--scenarios", str(scenario), "run",
                      "--connect", f"127.0.0.1:{srv.port}", "--out", str(out))
    assert res.returncode == 2, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "ERROR"
    hang = [c for c in report["cases"] if c["id"] == "HW_HANG"][0]
    assert hang["verdict"] == "ERROR" and hang["error"]


def test_failed_check_is_fail_not_error(tmp_path: Path) -> None:
    """Ложное ожидание даёт FAIL(1) — сценарий исполнился, проверка не сошлась."""
    scenario = tmp_path / "hw_wrong_expectation.py"
    scenario.write_text(
        'from hwt_target import case\n\n'
        'from hwt_stm32g4 import TIM1_BDTR\n\n'
        '@case("HW_WRONG", timeout_s=5, contracts=("main",))\n'
        'def hw_wrong(t):\n    t.reach("main")\n'
        '    t.check("MOE снят", t.read_u32(TIM1_BDTR) & 0x8000, 1)\n',
        encoding="utf-8")
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path)),
                      "--scenarios", str(scenario), "run",
                      "--connect", f"127.0.0.1:{srv.port}", "--out", str(out))
    assert res.returncode == 1, res.stdout + res.stderr
    wrong = [c for c in latest_result(out)["cases"]
             if c["id"] == "HW_WRONG"][0]
    assert wrong["verdict"] == "FAIL"
