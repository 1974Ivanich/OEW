"""Ожидания гейта параметров двигателя: одно число — один источник.

Гейт `tests/target/hw_motor_params.py` выносит вердикт по числам, которые
повторяются в нескольких слоях: шильдиковые пары полюсов, порог `Tr` и
смещения полей `MotorParams`. Копия, разошедшаяся с прошивкой, не падает — она
даёт правдоподобно неверный вердикт: сценарий читает не то поле или сверяет не
то число и рапортует PASS. Поэтому каждое повторение пинится здесь.

Проверяется:

* `tools/hwt_stm32g4.FOC_POLE_PAIRS_NAMEPLATE` == `#define FOC_DEFAULT_POLE_PAIRS`
  (`src/foc.c`) — число, которое умножает электрический угол;
* `tools/hwt_stm32g4.FOC_SLIP_POLICY_MIN_TR_US` ==
  `#define FOC_SLIP_POLICY_MIN_TR_US` (`src/foc_slip_policy.h`) — порог прямого
  старта;
* `tools/hwt_stm32g4.AT_MOTOR_PARAM_OFFSETS` == числа из
  `tests/autotune_params_layout_test.c`, а тот файл ставит их компилятором
  против `offsetof` настоящего заголовка (`src/autotune.h`). Здесь пинится
  равенство двух записей одного факта; за истинность отвечает компилятор;
* исправное состояние без железа — фикстура симулятора и значения мока —
  обязано объявлять те же числа, иначе прогон в CI проверял бы другой гейт,
  чем стенд.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _macro(source: str, name: str) -> int:
    text = (ROOT / source).read_text(encoding="utf-8")
    match = re.search(rf"^#define\s+{name}\s+(\d+)", text, re.MULTILINE)
    assert match, f"в {source} нет #define {name}"
    return int(match.group(1))


def test_nameplate_pole_pairs_match_the_firmware():
    from tools.hwt_stm32g4 import FOC_POLE_PAIRS_NAMEPLATE

    assert FOC_POLE_PAIRS_NAMEPLATE == _macro("src/foc.c",
                                              "FOC_DEFAULT_POLE_PAIRS")
    assert FOC_POLE_PAIRS_NAMEPLATE == 3, "шильдик: 6 полюсов = 3 пары"


def test_direct_start_threshold_matches_the_slip_policy_header():
    from tools.hwt_stm32g4 import FOC_SLIP_POLICY_MIN_TR_US

    assert FOC_SLIP_POLICY_MIN_TR_US == _macro("src/foc_slip_policy.h",
                                               "FOC_SLIP_POLICY_MIN_TR_US")


def test_encoder_error_check_reads_the_same_byte_the_firmware_reports():
    """Байт `ENC_GetError() == 0` — тот же, что читает FOC_Start.

    Имя символа — единственная связь сценария с прошивкой: расход дал бы ERROR
    по контракту на preflight, а вердикт — по чужому объекту. Поэтому имя
    берётся из `src/encoder.c` и с ним сверяется контракт сценария, а ноль
    объявлен «ошибок нет» только потому, что флаги ENC_ERR_* ненулевые.
    """
    from tools.hwt_stm32g4 import ENC_ERROR_NONE
    from tools.hwt_target import iter_cases

    source = (ROOT / "src" / "encoder.c").read_text(encoding="utf-8")
    declared = re.findall(r"static\s+volatile\s+uint8_t\s+(\w+)\s*=", source)
    getter = re.search(r"uint8_t\s+ENC_GetError\(void\)\s*\{\s*return\s+(\w+);",
                       source)
    assert getter and getter.group(1) in declared, (
        "ENC_GetError() обязана возвращать объявленный байт состояния")
    name = getter.group(1)

    assert ENC_ERROR_NONE == 0
    flags = re.findall(r"^#define\s+(ENC_ERR_\w+)\s+(0x[0-9A-Fa-f]+|\d+)",
                       (ROOT / "src" / "encoder.h").read_text(encoding="utf-8"),
                       re.MULTILINE)
    assert {"ENC_ERR_TIMEOUT", "ENC_ERR_BAD_PERIOD"} <= {n for n, _v in flags}
    assert all(int(value, 0) != ENC_ERROR_NONE for _n, value in flags), \
        "ноль должен означать «ошибок нет»: ни один флаг ENC_ERR_* не равен 0"

    cases = {m.id: m for m in iter_cases(ROOT / "tests" / "target" /
                                         "hw_motor_params.py")}
    assert name in cases["HW_START_PATH_READY"].contracts, (
        f"контракт сценария обязан называть символ {name!r} из src/encoder.c "
        "(preflight проверит его наличие в образе до платы)")
    scenario = (ROOT / "tests" / "target" /
                "hw_motor_params.py").read_text(encoding="utf-8")
    assert "byte_at_symbol(ENC_ERROR)" in scenario, (
        "сценарий обязан читать байт по символу-константе, а не по адресу "
        "числом")


def test_motor_param_offsets_match_the_build_time_contract():
    """C-файл — источник правды: он сравнивает числа с offsetof настоящего заголовка."""
    from tools.hwt_stm32g4 import AT_MOTOR_PARAM_OFFSETS

    text = (ROOT / "tests" / "autotune_params_layout_test.c").read_text(
        encoding="utf-8")
    pinned = {field: int(value) for field, value in re.findall(
        r"offsetof\(MotorParams,\s*(\w+)\)\s*==\s*(\d+)", text)}
    assert pinned, "в контракте layout нет ни одного _Static_assert"
    assert pinned == AT_MOTOR_PARAM_OFFSETS, (
        "сценарий адресует поля не теми смещениями, которые подтверждает "
        "сборка")


def test_sim_fixture_declares_the_same_expectations():
    from tools.hwt_stm32g4 import (AT_MOTOR_PARAM_OFFSETS, ENC_ERROR_NONE,
                                   FOC_POLE_PAIRS_NAMEPLATE,
                                   FOC_SLIP_POLICY_MIN_TR_US)

    fixture = json.loads((ROOT / "tests" / "target" /
                          "sim_values.json").read_text(encoding="utf-8"))
    assert fixture["sym"]["pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    assert fixture["sym"]["enc_error"] == ENC_ERROR_NONE, (
        "фикстура обязана объявлять исправный канал энкодера, иначе в CI "
        "проверялась бы другая половина критерия")
    assert "byte_at_symbol(ENC_ERROR)" in (
        (ROOT / "tests" / "target" /
         "hw_motor_params.py").read_text(encoding="utf-8"))
    fields = fixture["field"]
    assert fields["g_motor_params.pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    # ровно на пороге: так проверяется включительность сравнения (>=)
    assert fields["g_motor_params.Tr_rotor_us"] == FOC_SLIP_POLICY_MIN_TR_US

    scenario = (ROOT / "tests" / "target" /
                "hw_motor_params.py").read_text(encoding="utf-8")
    for field in AT_MOTOR_PARAM_OFFSETS:
        assert f'AT_MOTOR_PARAM_OFFSETS["{field}"]' in scenario, (
            f"сценарий должен брать смещение {field} из сверенного модуля, а не "
            "числом по месту")


def test_mock_healthy_target_declares_the_same_expectations():
    from tests.mock_rsp_server import (static_motor_param_fields,
                                       static_scenario_values)
    from tools.hwt_stm32g4 import (ENC_ERROR_NONE, FOC_POLE_PAIRS_NAMEPLATE,
                                   FOC_SLIP_POLICY_MIN_TR_US)

    assert static_scenario_values()["pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    assert static_scenario_values()["enc_error"] == ENC_ERROR_NONE, (
        "исправное состояние мока обязано объявлять канал энкодера без "
        "ошибки — как на здоровой плате")
    fields = static_motor_param_fields()
    assert fields["g_motor_params.pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    assert fields["g_motor_params.Tr_rotor_us"] == FOC_SLIP_POLICY_MIN_TR_US


def test_campaign_refusal_arms_exactly_the_waivable_check():
    """Запись VF_CAMPAIGN снимает ту проверку, которая объявлена waivable.

    Опечатка в имени проверки не даёт тихого обхода: отказ перестаёт
    применяться и гейт снова красный. Но заметить это надо здесь, а не на
    стенде, поэтому имена сверяются AST-разбором сценария. Обратно: снимаемых
    проверок в файле ровно одна — Tr; пары полюсов отказом не снимаются.
    """
    from tools.hwt_target import declared_checks, load_waivers

    registry = load_waivers(ROOT / "tests" / "target" / "waivers.json")
    declared = declared_checks(ROOT / "tests" / "target" / "hw_motor_params.py")
    waivable = [(function, name) for function, checks in declared.items()
                for name, flag in checks if flag]
    assert len(waivable) == 1 and waivable[0][0] == "hw_start_path_ready", \
        "снимаемой должна быть ровно одна проверка — Tr"
    assert [w.check for w in registry.entries] == [name for _f, name in waivable]
    assert [w.case for w in registry.entries] == ["HW_START_PATH_READY"]

    direct = declared["hw_start_path_ready"]
    assert ("ENC_GetError() == 0: канал энкодера исправен", False) in direct, (
        "вторая половина критерия шага 4 должна быть в сценарии и не "
        "объявляться снимаемой")
