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
    assert ids.get("HW_MOTOR_PAIRS") == "PASS"
    assert ids.get("HW_START_PATH_READY") == "PASS"
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


# ── гейт параметров двигателя: проверки обязаны кусаться ───────────────────
# Сценарий tests/target/hw_motor_params.py выносит вердикт по числам, и его
# смысл — ловить неверное состояние цели, а не проходить всегда. Поэтому каждая
# проверка гейта проверяется своим неверным состоянием мока: сначала по имени
# упавшего assert'а, потом временно — по вердикту.

def run_one_case(tmp_path: Path, case_id: str, **target_kwargs) -> tuple:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE), **target_kwargs)) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path)), "run",
                      "--connect", f"127.0.0.1:{srv.port}", "--out", str(out),
                      "--case", case_id)
    return res, out


def result_case(out: Path, case_id: str) -> dict:
    return [c for c in latest_result(out)["cases"] if c["id"] == case_id][0]


def failed_check_names(out: Path, case_id: str) -> list:
    return [c["name"] for c in result_case(out, case_id)["checks"] if not c["ok"]]


def test_pole_pairs_not_the_nameplate_is_fail(tmp_path: Path) -> None:
    """6 на цели вместо 3: это число множит электрический угол (шаг 1 паспорта)."""
    res, out = run_one_case(tmp_path, "HW_MOTOR_PAIRS",
                            symbols={"pole_pairs": 6})
    assert res.returncode == 1, res.stdout + res.stderr
    assert result_case(out, "HW_MOTOR_PAIRS")["verdict"] == "FAIL"
    assert ("FOC: пар полюсов ровно столько, сколько на шильдике"
            in failed_check_names(out, "HW_MOTOR_PAIRS"))


def test_mirror_diverged_from_foc_is_fail(tmp_path: Path) -> None:
    """FOC=3, но зеркало MotorParams разошлось — телеметрия отдаёт не то число."""
    res, out = run_one_case(tmp_path, "HW_MOTOR_PAIRS",
                            motor_params={"pole_pairs": 5})
    assert res.returncode == 1, res.stdout + res.stderr
    assert failed_check_names(out, "HW_MOTOR_PAIRS") == [
        "зеркало g_motor_params.pole_pairs совпадает с FOC"]


def test_tr_just_below_threshold_is_fail(tmp_path: Path) -> None:
    """Порог включительный: 1000 проходит, 999 — нет (src/foc_slip_policy.c:10)."""
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            motor_params={"Tr_rotor_us": 999})
    assert res.returncode == 1, res.stdout + res.stderr
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "FAIL"
    assert [c["name"] for c in case["checks"] if not c["ok"]] == [
        "Tr достигает порога прямого encoder-старта"]


def test_tr_of_the_current_parameter_file_is_fail_with_the_path_named(
        tmp_path: Path) -> None:
    """Tr=60 мкс — то, что лежит в autotune_params.json (2026-08-06).

    Гейт обязан не только покраснеть, но и назвать следствие: при таком Tr
    старт идёт через V/f с handoff, то есть кампания возможна, а прямой старт
    закрыт до измерения Rr/Lm (паспорт, шаг 4).
    """
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 1, res.stdout + res.stderr
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "FAIL"
    assert any("V/f" in note for note in case.get("notes", [])), case
