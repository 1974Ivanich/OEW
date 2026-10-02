"""Тесты HWT: метаданные сценариев, вердикты, отчёты, запрет инъекций."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

from tools.hwt_target import (CaseMeta, Check, ScenarioError, Target,
                              TargetError, VERDICT_ERROR, VERDICT_FAIL,
                              VERDICT_PASS, exit_code_for, iter_cases, junit_xml,
                              run_case)

GOOD_SCENARIO = '''
from hwt_target import case

CHECKPOINT = "CLI_ProcessLine"


@case("HW_X", timeout_s=15, labels=("smoke",), contracts=(CHECKPOINT, "main"))
def hw_x(t):
    t.check("живой", 1, 1)
'''

COMPUTED_CONTRACT = '''
from hwt_target import case

SYMS = ["main"]
CHECKPOINT = "CLI_ProcessLine"


@case("HW_Y", contracts=(CHECKPOINT, SYMS[0] + "_x"))
def hw_y(t):
    t.check("x", 1, 1)
'''


class FakeBackend:
    """Минимум, чтобы прогнать Target без железа."""

    def __init__(self, values: dict | None = None, symbol: str | None = None):
        self.values = values or {}
        self.symbol = symbol
        self.cleared = 0
        self.breakpoints: List[str] = []

    def target_info(self) -> dict:
        return {"fake": True}

    def reset_halt(self) -> None:
        pass

    def break_at(self, symbol: str) -> None:
        if self.symbol is not None and symbol != self.symbol:
            raise TargetError(f"нет символа {symbol}")
        self.breakpoints.append(symbol)

    def clear_breakpoints(self) -> None:
        self.cleared += 1

    def continue_for(self, timeout_s: float, what: str) -> bool:
        return True

    def stop_reason(self) -> str:
        return "fake"

    def evaluate(self, expr: str) -> Any:
        if expr in self.values:
            return self.values[expr]
        raise TargetError(f"нет значения {expr}")

    def read_memory(self, addr: int, size: int) -> bytes:
        raise TargetError("нет памяти")

    def force_return(self, value: int) -> None:
        pass

    def write_memory(self, addr: int, data: bytes) -> None:
        pass

    def set_value(self, expr: str, value: Any) -> None:
        if expr not in self.values:
            raise TargetError(f"нет значения {expr}")
        self.values[expr] = value


def _meta(case_id: str = "HW_X", contracts: tuple = ()) -> CaseMeta:
    return CaseMeta(id=case_id, function="hw_x", timeout_s=10.0,
                    contracts=contracts)


def test_iter_cases_reads_literals_without_executing(tmp_path: Path) -> None:
    path = tmp_path / "hw_ok.py"
    path.write_text(GOOD_SCENARIO, encoding="utf-8")
    cases = iter_cases(path)
    assert len(cases) == 1
    meta = cases[0]
    assert meta.id == "HW_X" and meta.timeout_s == 15
    assert meta.contracts == ("CLI_ProcessLine", "main")
    assert meta.labels == ("smoke",)
    assert meta.line > 1


def test_iter_cases_rejects_computed_metadata(tmp_path: Path) -> None:
    path = tmp_path / "hw_bad.py"
    path.write_text(COMPUTED_CONTRACT, encoding="utf-8")
    with pytest.raises(ScenarioError):
        iter_cases(path)


def test_case_pass_and_fail() -> None:
    ok = run_case(_meta(), lambda t: t.check("a", 1, 1), Target(FakeBackend(),
                 _meta()))
    assert ok.verdict == VERDICT_PASS and ok.exit_code == 0

    bad = run_case(_meta(), lambda t: t.check("a", 2, 1), Target(FakeBackend(),
                    _meta()))
    assert bad.verdict == VERDICT_FAIL and bad.exit_code == 1
    assert bad.checks[0].ok is False


def test_target_error_is_error_not_fail() -> None:
    def scenario(t: Target) -> None:
        t.check("a", 1, 1)
        t.value("недоступное")

    res = run_case(_meta(), scenario, Target(FakeBackend(), _meta()))
    assert res.verdict == VERDICT_ERROR and res.exit_code == 2
    assert "недоступное" in res.error


def test_scenario_without_checks_is_error() -> None:
    res = run_case(_meta(), lambda t: None, Target(FakeBackend(), _meta()))
    assert res.verdict == VERDICT_ERROR


def test_injections_denied_by_default() -> None:
    target = Target(FakeBackend({"x": 1}), _meta())
    with pytest.raises(TargetError):
        target.set_value("x", 0)
    with pytest.raises(TargetError):
        target.force_return(0)


def test_injection_recorded_with_before_after() -> None:
    target = Target(FakeBackend({"x": 5}), _meta(), allow_injections=True)
    target.set_value("x", 7)
    assert target.checks[-1].injected
    assert target.checks[-1].before == 5
    assert target.notes and "инъекция" in target.notes[0]


def test_check_tolerance() -> None:
    target = Target(FakeBackend(), _meta())
    assert target.check("близко", 100.4, 100.0, tol=1.0)
    assert not target.check("далеко", 102.0, 100.0, tol=1.0)


def test_exit_code_prefers_error() -> None:
    ok = run_case(_meta("A"), lambda t: t.check("a", 1, 1), Target(
        FakeBackend(), _meta("A")))
    fail = run_case(_meta("B"), lambda t: t.check("a", 2, 1), Target(
        FakeBackend(), _meta("B")))
    err = run_case(_meta("C"), lambda t: None, Target(FakeBackend(),
                                                      _meta("C")))
    assert exit_code_for([ok]) == 0
    assert exit_code_for([ok, fail]) == 1
    assert exit_code_for([ok, fail, err]) == 2


def test_junit_xml_reflects_verdicts() -> None:
    fail = run_case(_meta("HW_BAD"), lambda t: t.check("v", 2, 1),
                    Target(FakeBackend(), _meta("HW_BAD")))
    err = run_case(_meta("HW_ERR"), lambda t: None, Target(FakeBackend(),
                                                           _meta("HW_ERR")))
    xml = junit_xml([fail, err], name="hwt-test")
    assert 'tests="2"' in xml and 'failures="1"' in xml and 'errors="1"' in xml
    assert "<failure" in xml and "<error" in xml
    assert "HW_BAD" in xml and "HW_ERR" in xml


def test_reach_requires_declared_symbol() -> None:
    target = Target(FakeBackend(symbol="main"), _meta())
    target.reach("main")
    with pytest.raises(TargetError):
        target.reach("другой")
