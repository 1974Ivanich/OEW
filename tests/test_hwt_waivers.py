"""Записанные отказы (waivers): реестр обязан быть точным, иначе он не значит ничего.

Разбор реестра — место, где «случайный обход» гейта ловится до платы:
неполная запись, дубль, маска вместо имени, опечатка в имени поля или
не-ISO дата — это ERROR, а не «отказов нет» и не «проверка снята».

Сами вердикты (WAIVED против FAIL/PASS) проверяются на стенде-заглушке
(``tests/test_hwt_on_target.py``) и на симуляторе (``tests/test_hwt_pipeline.py``),
а связка «запись ↔ waivable-проверка» — в ``tests/test_hwt_motor_param_gate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.hwt_target import ScenarioError, declared_checks, iter_cases, load_waivers

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "tests" / "target" / "hw_motor_params.py"
COMMITTED = ROOT / "tests" / "target" / "waivers.json"

VALID = {
    "id": "VF_CAMPAIGN",
    "case": "HW_START_PATH_READY",
    "check": "Tr достигает порога прямого encoder-старта",
    "reason": "шаг 4 паспорта не выполнен: Rr/Lm не измерены",
    "compensating": "кампания идёт open-loop V/f → handoff",
    "author": "оператор",
    "date": "2026-10-14",
}


def write_registry(tmp_path: Path, *entries: dict, top: dict | None = None) -> Path:
    data: dict = {"waivers": list(entries)}
    data.update(top or {})
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def test_missing_file_is_the_normal_mode_not_an_error(tmp_path: Path) -> None:
    reg = load_waivers(tmp_path / "нет.json")
    assert reg.entries == [] and reg.exists is False and reg.sha256 == ""


def test_valid_registry_is_read_and_hashed(tmp_path: Path) -> None:
    reg = load_waivers(write_registry(tmp_path, VALID))
    assert reg.exists is True and len(reg.sha256) == 64
    assert [w.id for w in reg.entries] == ["VF_CAMPAIGN"]
    assert reg.entries[0].compensating == VALID["compensating"]
    assert reg.for_case("HW_ALIVE") == []
    assert reg.for_case("HW_START_PATH_READY") == reg.entries


@pytest.mark.parametrize("change, expected", [
    ({"reason": None}, "reason"),
    ({"reason": "   "}, "reason"),
    ({"compensating": ""}, "compensating"),
    ({"author": None}, "author"),
    ({"id": 7}, "id"),
    ({"check": ""}, "check"),
    ({"check": "Tr достигает порога*"}, "маски"),
    ({"case": "HW_*"}, "маски"),
    ({"date": "14.10.2026"}, "date"),
    ({"date": "2026-02-30"}, "date"),
])
def test_incomplete_or_vague_entry_is_an_error(tmp_path: Path, change: dict,
                                               expected: str) -> None:
    entry = dict(VALID)
    entry.update(change)
    with pytest.raises(ScenarioError) as exc:
        load_waivers(write_registry(tmp_path, entry))
    assert expected in str(exc.value)


def test_typo_in_a_field_name_is_an_error(tmp_path: Path) -> None:
    """`resaon` вместо `reason` не должен стать записью «без причины»."""
    entry = dict(VALID)
    entry["resaon"] = entry.pop("reason")
    with pytest.raises(ScenarioError) as exc:
        load_waivers(write_registry(tmp_path, entry))
    assert "неизвестные поля" in str(exc.value)


def test_duplicates_are_rejected(tmp_path: Path) -> None:
    second = dict(VALID, id="OTHER")
    with pytest.raises(ScenarioError) as exc:
        load_waivers(write_registry(tmp_path, VALID, second))
    assert "уже снята" in str(exc.value)

    other = dict(VALID, check="другая проверка")
    with pytest.raises(ScenarioError) as exc:
        load_waivers(write_registry(tmp_path, VALID, other))
    assert "повторяется" in str(exc.value)


@pytest.mark.parametrize("raw, expected", [
    ([], "объект"),
    ("{}", "нет ключа"),
    ('{"waivers": {}}', "списком записей"),
    ('{"не_waivers": []}', "верхнего уровня"),
    ('{"waivers": [], "waive_all": true}', "верхнего уровня"),
    ('{"waivers": ["не объект"]}', "запись должна быть объектом"),
    ("не json", "JSON"),
])
def test_broken_file_structure_is_an_error(tmp_path: Path, raw, expected: str) -> None:
    path = tmp_path / "waivers.json"
    path.write_text(raw if isinstance(raw, str) else json.dumps(raw),
                    encoding="utf-8")
    with pytest.raises(ScenarioError) as exc:
        load_waivers(path)
    assert expected in str(exc.value)


def test_comment_keys_are_allowed(tmp_path: Path) -> None:
    path = write_registry(tmp_path, VALID, top={"_комментарий": "почему так"})
    assert len(load_waivers(path).entries) == 1


def test_committed_registry_names_the_vf_campaign() -> None:
    """Запись ссылается на сценарий, который действительно есть (AST-разбор)."""
    cases = {m.id for m in iter_cases(SCENARIO)}
    reg = load_waivers(COMMITTED)
    assert reg.exists is True
    assert [(w.id, w.case) for w in reg.entries] == \
        [("VF_CAMPAIGN", "HW_START_PATH_READY")]
    assert reg.entries[0].case in cases


def test_declared_checks_sees_the_literal_waivable_flag() -> None:
    declared = declared_checks(SCENARIO)
    direct = declared["hw_start_path_ready"]
    assert ("Tr достигает порога прямого encoder-старта", True) in direct
    assert ("ENC_GetError() == 0: канал энкодера исправен", False) in direct, \
        "вторая половина критерия шага 4 не должна быть снимаемой"
    assert all(not waivable for _name, waivable
               in declared["hw_motor_pairs"]), \
        "пары полюсов не должны быть снимаемыми"
