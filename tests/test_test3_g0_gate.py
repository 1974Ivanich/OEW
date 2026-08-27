from __future__ import annotations

from pathlib import Path

from tools.test3_g0_gate import main


def test_composite_gate_is_fail_closed_without_evidence(tmp_path: Path) -> None:
    test2 = tmp_path / "test2.json"
    test3 = tmp_path / "test3.json"
    test2.write_text("{}", encoding="utf-8")
    test3.write_text("{}", encoding="utf-8")
    assert main(["--test2-approval", str(test2), "--test3-plan", str(test3)]) == 2
