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
    from tools.hwt_stm32g4 import (AT_MOTOR_PARAM_OFFSETS,
                                   FOC_POLE_PAIRS_NAMEPLATE,
                                   FOC_SLIP_POLICY_MIN_TR_US)

    fixture = json.loads((ROOT / "tests" / "target" /
                          "sim_values.json").read_text(encoding="utf-8"))
    assert fixture["sym"]["pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
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
    from tools.hwt_stm32g4 import (FOC_POLE_PAIRS_NAMEPLATE,
                                   FOC_SLIP_POLICY_MIN_TR_US)

    assert static_scenario_values()["pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    fields = static_motor_param_fields()
    assert fields["g_motor_params.pole_pairs"] == FOC_POLE_PAIRS_NAMEPLATE
    assert fields["g_motor_params.Tr_rotor_us"] == FOC_SLIP_POLICY_MIN_TR_US
