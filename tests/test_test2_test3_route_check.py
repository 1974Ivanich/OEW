from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "test2_test3_route_check.py"
SPEC = importlib.util.spec_from_file_location("test2_test3_route_check", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ROUTE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ROUTE
SPEC.loader.exec_module(ROUTE)

SHA = "a" * 40
HASH = "b" * 64


def contexts() -> list[dict[str, int]]:
    return [{"sector": sector, "window": window} for sector in range(6) for window in range(2)]


def valid_test2() -> dict[str, Any]:
    return {
        "schema": ROUTE.TEST2_SCHEMA,
        "test_id": "TEST2",
        "decision": "PASS",
        "source_sha": SHA,
        "campaign": {"id": "test2_adc_20260826T000000Z", "path": "D:/campaign_raw/test2_adc_20260826T000000Z"},
        "evidence_sha256": {"uart_log": HASH, "adc_samples": HASH, "summary": HASH},
        "review": {
            "reviewer": "safety-owner",
            "reviewed_at": "2026-08-26T00:00:00Z",
            "statement": "DC-link disconnected; default-deny ADC baseline evidence reviewed.",
        },
    }


def valid_test3() -> dict[str, Any]:
    return {
        "schema": ROUTE.TEST3_SCHEMA,
        "test_id": "TEST3",
        "decision": "PENDING",
        "source_sha": SHA,
        "g0_approval_path": "D:/campaign_raw/test3_nohv_20260826T000000Z/g0_approval.json",
        "scope": dict(ROUTE.REQUIRED_TEST3_SCOPE),
        "scope_timing_review": {
            "reviewer": "safety-owner",
            "reviewed_at": "2026-08-26T00:00:00Z",
            "statement": "One no-HV timing review covers the planned 12 contexts.",
        },
        "contexts": contexts(),
        "claims": {},
    }


def write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def check_result(report: dict[str, Any], identifier: str) -> bool:
    found = [check for check in report["checks"] if check["identifier"] == identifier]
    assert len(found) == 1
    return bool(found[0]["passed"])


def build(tmp_path: Path, test2: Any | None = None, test3: Any | None = None) -> dict[str, Any]:
    test2_path = write_json(tmp_path / "test2_baseline_approval.json", valid_test2() if test2 is None else test2)
    test3_path = write_json(tmp_path / "test3_commissioning_plan.json", valid_test3() if test3 is None else test3)
    return ROUTE.build_verdict(test2_path, test3_path)


def test_valid_route_is_ready_but_cannot_authorize_capture_or_start(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["route_verdict"] == "PASS"
    assert report["test2_baseline_accepted"] == "PASS"
    assert report["test3_nohv_commissioning_ready"] == "PASS"
    assert report["real_board_capture_valid"] == "BLOCKED"
    assert report["stage_a_60v"] == "BLOCKED"
    assert report["limited_motor_start"] == "BLOCKED"


def test_test2_without_human_review_cannot_be_reused(tmp_path: Path) -> None:
    approval = valid_test2()
    approval["review"]["reviewer"] = ""
    report = build(tmp_path, test2=approval)

    assert report["route_verdict"] == "FAIL"
    assert report["test2_baseline_accepted"] == "FAIL"
    assert not check_result(report, "R-08-test2-review")


def test_test2_nonpass_is_fail_closed(tmp_path: Path) -> None:
    approval = valid_test2()
    approval["decision"] = "PENDING"
    report = build(tmp_path, test2=approval)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-04-test2-decision")


def test_test3_plan_requires_pending_not_self_approval(tmp_path: Path) -> None:
    plan = valid_test3()
    plan["decision"] = "PASS"
    report = build(tmp_path, test3=plan)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-13-test3-pending")


def test_test3_plan_requires_complete_unique_context_matrix(tmp_path: Path) -> None:
    plan = valid_test3()
    plan["contexts"][-1] = {"sector": 5, "window": 0}
    report = build(tmp_path, test3=plan)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-18-test3-contexts")


def test_test3_scope_cannot_open_dc_link(tmp_path: Path) -> None:
    plan = valid_test3()
    plan["scope"]["forbids_dc_link"] = False
    report = build(tmp_path, test3=plan)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_premature_capture_or_stage_a_claim_is_rejected(tmp_path: Path) -> None:
    plan = valid_test3()
    plan["claims"] = {"real_board_capture_valid": "PASS", "stage_a_60v": "PASS"}
    report = build(tmp_path, test3=plan)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-19-no-premature-claims")


def test_malformed_json_is_fail_closed(tmp_path: Path) -> None:
    test2_path = tmp_path / "test2_baseline_approval.json"
    test2_path.write_text("{not-json", encoding="utf-8")
    test3_path = write_json(tmp_path / "test3_commissioning_plan.json", valid_test3())
    report = ROUTE.build_verdict(test2_path, test3_path)

    assert report["route_verdict"] == "FAIL"
    assert not check_result(report, "R-01-test2-json")


def test_cli_writes_route_report_and_preserves_blocks(tmp_path: Path) -> None:
    test2_path = write_json(tmp_path / "test2_baseline_approval.json", valid_test2())
    test3_path = write_json(tmp_path / "test3_commissioning_plan.json", valid_test3())
    output = tmp_path / "route_verdict.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--test2-approval", str(test2_path), "--test3-plan", str(test3_path), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "ROUTE_VERDICT=PASS" in completed.stdout
    assert "REAL_BOARD_CAPTURE_VALID=BLOCKED" in completed.stdout
    assert "STAGE_A_60V=BLOCKED" in completed.stdout
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["limited_motor_start"] == "BLOCKED"
