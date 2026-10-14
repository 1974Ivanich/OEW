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
    run = 'state = "reset-halt"\n'
    if probe.get("waivers"):
        run += f'waivers = "{probe["waivers"]}"\n'
    cfg.write_text(
        "[probe]\n"
        f"device_id = {probe.get('device_id', 0x469)}\n"
        f"flash_size_kb = {probe.get('flash_size_kb', 512)}\n"
        f"policy = \"{probe.get('policy', 'deny')}\"\n"
        "[server]\ngdb_port = 3333\n"
        "[run]\n" + run +
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

def run_one_case(tmp_path: Path, case_id: str, waivers: str | None = None,
                 **target_kwargs) -> tuple:
    out = tmp_path / "hwt"
    with MockRspServer(make_target(str(FIRMWARE), **target_kwargs)) as srv:
        res = run_hwt("--config", str(write_stand(tmp_path, waivers=waivers)),
                      "run", "--connect", f"127.0.0.1:{srv.port}",
                      "--out", str(out), "--case", case_id)
    return res, out


def no_registry(tmp_path: Path) -> str:
    """Путь, которого нет: явный обычный режим — отказов нет.

    Нужен там, где проверяется именно красный гейт: закоммиченный
    tests/target/waivers.json снимает HW_START_PATH_READY, и без такого
    явного «без реестра» тест проверял бы уже другой режим.
    """
    return (tmp_path / "нет-реестра.json").as_posix()


def campaign_registry(tmp_path: Path, **overrides) -> str:
    """Реестр с одной записью VF_CAMPAIGN (по умолчанию — как в репозитории)."""
    entry = {
        "id": "VF_CAMPAIGN",
        "case": "HW_START_PATH_READY",
        "check": "Tr достигает порога прямого encoder-старта",
        "reason": "шаг 4 паспорта не выполнен: Rr/Lm не измерены",
        "compensating": "кампания идёт open-loop V/f → handoff (FOC_STATE_STARTUP)",
        "author": "тест",
        "date": "2026-10-14",
    }
    entry.update(overrides)
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps({"waivers": [entry]}, ensure_ascii=False),
                    encoding="utf-8")
    return path.as_posix()


def result_case(out: Path, case_id: str) -> dict:
    return [c for c in latest_result(out)["cases"] if c["id"] == case_id][0]


def failed_check_names(out: Path, case_id: str) -> list:
    return [c["name"] for c in result_case(out, case_id)["checks"] if not c["ok"]]


# Вторая половина критерия шага 4 (ENC_GetError() == 0): она есть в сценарии
# и НЕ объявлена снимаемой — запись отказа не может её подменить.
ENC_CHECK = "ENC_GetError() == 0: канал энкодера исправен"


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


def test_tr_just_below_threshold_is_fail_without_a_record(tmp_path: Path) -> None:
    """Порог включительный: 1000 проходит, 999 — нет (src/foc_slip_policy.c:10).

    Без записанного отказа гейт красный; закоммиченная запись VF_CAMPAIGN
    снимает именно его, поэтому здесь реестр задан явно пустым.
    """
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            waivers=no_registry(tmp_path),
                            motor_params={"Tr_rotor_us": 999})
    assert res.returncode == 1, res.stdout + res.stderr
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "FAIL"
    assert [c["name"] for c in case["checks"] if not c["ok"]] == [
        "Tr достигает порога прямого encoder-старта"]


def test_tr_of_the_current_parameter_file_is_fail_with_the_path_named(
        tmp_path: Path) -> None:
    """Tr=60 мкс — то, что лежит в autotune_params.json (2026-08-06).

    Без записанного отказа гейт обязан не только покраснеть, но и назвать
    следствие: при таком Tr старт идёт через V/f с handoff, то есть кампания
    возможна, а прямой старт закрыт до измерения Rr/Lm (паспорт, шаг 4).
    Режим кампании (WAIVED) проверяется ниже своим тестом.
    """
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            waivers=no_registry(tmp_path),
                            motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 1, res.stdout + res.stderr
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "FAIL"
    assert any("V/f" in note for note in case.get("notes", [])), case


# ── режим кампании через V/f: записанный отказ вместо красного гейта ───────
# Запись в реестре снимает один гейт и делает это явно: прогон не красный, но
# вердикт — WAIVED, а не PASS, и отказ виден в консоли, result.json и junit.
# Проверки, которые снять нельзя (шильдиковые пары полюсов), остаются красными.

def test_recorded_refusal_makes_the_run_waived_not_pass(tmp_path: Path) -> None:
    """Tr=60 с записью VF_CAMPAIGN: прямой старт закрыт, кампания — не красная."""
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            waivers=campaign_registry(tmp_path),
                            motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 0, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "WAIVED", "снятый гейт — не PASS"
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "WAIVED"
    assert [c["name"] for c in case["checks"] if not c["ok"]] == [
        "Tr достигает порога прямого encoder-старта"]
    bad = case["checks"][0]
    assert bad["actual"] is False and bad["ok"] is False
    assert bad["waivable"] is True
    assert bad["waiver"]["id"] == "VF_CAMPAIGN" and bad["waiver"]["reason"]
    assert any("V/f" in note for note in case["notes"])
    assert report["waivers"]["applied"][0]["id"] == "VF_CAMPAIGN"
    assert report["waiver_registry"]["exists"] is True
    assert "WAIVED" in res.stdout and "[отказ]" in res.stdout
    assert "вердикт WAIVED" in res.stdout
    junit = (sorted(p for p in out.iterdir() if p.is_dir())[-1] /
             "junit.xml").read_text(encoding="utf-8")
    assert 'skipped="1"' in junit and "<skipped" in junit


def test_encoder_error_keeps_the_run_red_even_with_a_refusal(
        tmp_path: Path) -> None:
    """Отказ снимает половину Tr, но не половину энкодера: вердикт — FAIL.

    Запись VF_CAMPAIGN адресована проверке Tr (единственной waivable), поэтому
    ошибка канала энкодера не прикрывается снятой половиной: код 1, вердикт
    FAIL, снятая проверка остаётся в отчёте, а половина энкодера — красная.
    """
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            waivers=campaign_registry(tmp_path),
                            symbols={"enc_error": 0x02},
                            motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    case = result_case(out, "HW_START_PATH_READY")
    assert case["verdict"] == "FAIL"
    assert sorted(failed_check_names(out, "HW_START_PATH_READY")) == sorted([
        "Tr достигает порога прямого encoder-старта", ENC_CHECK])
    enc = [c for c in case["checks"] if c["name"] == ENC_CHECK][0]
    assert enc["actual"] == 0x02 and enc["ok"] is False
    assert "waiver" not in enc, "половина энкодера снимаемой быть не должна"
    assert report["waivers"]["applied"][0]["check"] == \
        "Tr достигает порога прямого encoder-старта"
    assert report["waivers"]["not_applied"] == []


def test_refusal_addressed_to_the_encoder_half_is_not_applied(
        tmp_path: Path) -> None:
    """Запись на половину энкодера — «не объявлена waivable», а не снятие."""
    res, out = run_one_case(
        tmp_path, "HW_START_PATH_READY",
        waivers=campaign_registry(tmp_path, check=ENC_CHECK),
        symbols={"enc_error": 1}, motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    assert report["waivers"]["applied"] == []
    why = report["waivers"]["not_applied"][0]["why"]
    assert "waivable" in why, why


def test_refusal_that_is_not_needed_is_reported(tmp_path: Path) -> None:
    """Tr=1000: проверка проходит сама — запись становится ненужной."""
    res, out = run_one_case(tmp_path, "HW_START_PATH_READY",
                            waivers=campaign_registry(tmp_path),
                            motor_params={"Tr_rotor_us": 1000})
    assert res.returncode == 0, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "PASS"
    assert report["waivers"]["applied"] == []
    why = report["waivers"]["not_applied"][0]["why"]
    assert "не понадобился" in why, why
    assert "не применён" in res.stdout


def test_refusal_cannot_lift_pole_pairs(tmp_path: Path) -> None:
    """p=6 остаётся FAIL даже с записью: проверка не объявлена снимаемой."""
    res, out = run_one_case(
        tmp_path, "HW_MOTOR_PAIRS",
        waivers=campaign_registry(
            tmp_path, case="HW_MOTOR_PAIRS",
            check="FOC: пар полюсов ровно столько, сколько на шильдике"),
        symbols={"pole_pairs": 6})
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    assert result_case(out, "HW_MOTOR_PAIRS")["verdict"] == "FAIL"
    assert report["waivers"]["applied"] == []
    why = report["waivers"]["not_applied"][0]["why"]
    assert "waivable" in why, why


def test_typo_in_the_recorded_check_name_leaves_the_gate_red(tmp_path: Path) -> None:
    """Опечатка в имени проверки — не снятие, а красный гейт и запись об этом."""
    res, out = run_one_case(
        tmp_path, "HW_START_PATH_READY",
        waivers=campaign_registry(tmp_path, check="Tr достигла порога прямого "
                                                   "encoder-старта"),
        motor_params={"Tr_rotor_us": 60})
    assert res.returncode == 1, res.stdout + res.stderr
    report = latest_result(out)
    assert report["verdict"] == "FAIL"
    assert report["waivers"]["not_applied"][0]["why"] == \
        "в сценарии нет проверки с таким именем"
