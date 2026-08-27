from __future__ import annotations

import hashlib
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
FIRMWARE_BYTES = b"approved-test3-diagnostic-firmware\x00"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contexts() -> list[dict[str, int]]:
    return [{"sector": sector, "window": window} for sector in range(6) for window in range(2)]


def write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def make_test2_campaign(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    campaign = tmp_path / "test2_adc_campaign"
    (campaign / "uart").mkdir(parents=True)
    (campaign / "summary").mkdir()
    (campaign / "identity").mkdir()
    (campaign / "uart" / "test2_adc_uart.log").write_text("@SYS:OK\n", encoding="utf-8")
    (campaign / "uart" / "adc_samples.csv").write_text("sample,i1,i2\n1,2048,2048\n", encoding="utf-8")
    (campaign / "summary" / "test2_adc_summary.md").write_text("# Test №2\n", encoding="utf-8")
    (campaign / "identity" / "build.log").write_text("build PASS\n", encoding="utf-8")
    (campaign / "identity" / "source_sha.txt").write_text(SHA + "\n", encoding="utf-8")
    approval = {
        "schema": ROUTE.TEST2_SCHEMA,
        "test_id": "TEST2",
        "decision": "PASS",
        "source_sha": SHA,
        "campaign": {"id": "test2_adc_20260826T000000Z", "path": str(campaign)},
        "source_identity_path": "identity/source_sha.txt",
        "evidence": {
            "uart_log": {"path": "uart/test2_adc_uart.log", "sha256": sha256(campaign / "uart" / "test2_adc_uart.log")},
            "adc_samples": {"path": "uart/adc_samples.csv", "sha256": sha256(campaign / "uart" / "adc_samples.csv")},
            "summary": {"path": "summary/test2_adc_summary.md", "sha256": sha256(campaign / "summary" / "test2_adc_summary.md")},
            "build_log": {"path": "identity/build.log", "sha256": sha256(campaign / "identity" / "build.log")},
        },
        "review": {
            "reviewer": "safety-owner",
            "reviewed_at": "2026-08-26T00:00:00Z",
            "statement": "DC-link disconnected; default-deny ADC baseline evidence reviewed.",
        },
    }
    return campaign, approval


def make_test3_campaign(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    campaign = tmp_path / "test3_nohv_campaign"
    campaign.mkdir()
    (campaign / "firmware.bin").write_bytes(FIRMWARE_BYTES)
    manifest = {
        "schema": ROUTE.TEST3_MANIFEST_SCHEMA,
        "gate": ROUTE.TEST3_G0_GATE,
        "test_id": "TEST3",
        "target": ROUTE.TEST3_G0_TARGET,
        "test": ROUTE.TEST3_G0_TEST,
        "source_sha": SHA,
        "defines_complete": True,
        "defines": dict(ROUTE.REQUIRED_TEST3_DEFINES),
        "firmware": {"path": "firmware.bin", "sha256": sha256(campaign / "firmware.bin")},
    }
    write_json(campaign / "diagnostic_build_manifest.json", manifest)
    approval = {
        "schema": ROUTE.TEST3_G0_SCHEMA,
        "gate": ROUTE.TEST3_G0_GATE,
        "test_id": "TEST3",
        "decision": "APPROVED",
        "role": "safety-owner",
        "approval_id": "G0-20260826-001",
        "approver": "safety-owner",
        "approved_at": "2026-08-26T00:00:00Z",
        "source_sha": SHA,
        "firmware_path": "firmware.bin",
        "firmware_sha256": sha256(campaign / "firmware.bin"),
        "approved_extra_defines": [],
        "scope": dict(ROUTE.REQUIRED_TEST3_SCOPE),
    }
    write_json(campaign / "g0_approval.json", approval)
    plan = {
        "schema": ROUTE.TEST3_SCHEMA,
        "test_id": "TEST3",
        "decision": "PENDING",
        "source_sha": SHA,
        "g0_approval_path": "g0_approval.json",
        "scope": dict(ROUTE.REQUIRED_TEST3_SCOPE),
        "scope_timing_review": {
            "reviewer": "safety-owner",
            "reviewed_at": "2026-08-26T00:00:00Z",
            "statement": "One no-HV timing review covers the planned 12 contexts.",
        },
        "contexts": contexts(),
        "claims": {},
    }
    return campaign, plan


def check_result(report: dict[str, Any], identifier: str) -> bool:
    found = [check for check in report["checks"] if check["identifier"] == identifier]
    assert len(found) == 1
    return bool(found[0]["passed"])


def build(tmp_path: Path, test2_mutator: Any = None, test3_mutator: Any = None) -> tuple[dict[str, Any], Path, Path]:
    _, test2 = make_test2_campaign(tmp_path)
    test3_campaign, test3 = make_test3_campaign(tmp_path)
    if test2_mutator is not None:
        test2_mutator(test2)
    if test3_mutator is not None:
        test3_mutator(test3)
    test2_path = write_json(tmp_path / "test2_baseline_approval.json", test2)
    test3_path = write_json(test3_campaign / "test3_commissioning_plan.json", test3)
    return ROUTE.build_verdict(test2_path, test3_path), test2_path, test3_path


def test_valid_evidence_bound_route_and_g0_is_ready_but_not_executed(tmp_path: Path) -> None:
    report, _, _ = build(tmp_path)

    assert report["route_plan_valid"] == "PASS"
    assert report["test2_baseline_accepted"] == "PASS"
    assert report["test3_nohv_commissioning_ready"] == "PASS"
    assert report["physical_test3_executed"] == "BLOCKED"
    assert report["real_board_capture_valid"] == "BLOCKED"
    assert report["stage_a_60v"] == "BLOCKED"
    assert report["limited_motor_start"] == "BLOCKED"


def test_test2_tampered_uart_evidence_is_rejected(tmp_path: Path) -> None:
    campaign, approval = make_test2_campaign(tmp_path)
    (campaign / "uart" / "test2_adc_uart.log").write_text("tampered\n", encoding="utf-8")
    test3_campaign, plan = make_test3_campaign(tmp_path)
    report = ROUTE.build_verdict(write_json(tmp_path / "test2.json", approval), write_json(test3_campaign / "plan.json", plan))

    assert report["route_plan_valid"] == "FAIL"
    assert report["test2_baseline_accepted"] == "FAIL"
    assert not check_result(report, "R-08-test2-evidence-uart_log")


def test_test2_path_traversal_is_rejected(tmp_path: Path) -> None:
    def mutate(approval: dict[str, Any]) -> None:
        approval["evidence"]["summary"]["path"] = "../outside.md"

    report, _, _ = build(tmp_path, test2_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-08-test2-evidence-summary")


def test_test2_source_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    campaign, approval = make_test2_campaign(tmp_path)
    (campaign / "identity" / "source_sha.txt").write_text("b" * 40 + "\n", encoding="utf-8")
    test3_campaign, plan = make_test3_campaign(tmp_path)
    report = ROUTE.build_verdict(write_json(tmp_path / "test2.json", approval), write_json(test3_campaign / "plan.json", plan))

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-07-test2-source-file")


def test_test3_plan_requires_complete_unique_context_matrix(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["contexts"][-1] = {"sector": 5, "window": 0}

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-18-test3-contexts")


def test_test3_plan_cannot_open_dc_link(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["scope"]["forbids_dc_link"] = False

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_missing_g0_blocks_commissioning_but_keeps_valid_plan_distinct(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    (plan_path.parent / "g0_approval.json").unlink()
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["route_plan_valid"] == "PASS"
    assert report["test3_nohv_commissioning_ready"] == "BLOCKED"
    assert not check_result(report, "R-20-test3-g0-present")


def test_g0_source_or_firmware_mismatch_fails_commissioning_integrity(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    g0_path = plan_path.parent / "g0_approval.json"
    g0 = json.loads(g0_path.read_text(encoding="utf-8"))
    g0["source_sha"] = "b" * 40
    write_json(g0_path, g0)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["route_plan_valid"] == "PASS"
    assert report["test3_g0_evidence"] == "FAIL"
    assert report["test3_nohv_commissioning_ready"] == "FAIL"
    assert not check_result(report, "R-25-test3-g0-source")


def test_pending_g0_blocks_without_claiming_integrity_failure(tmp_path: Path) -> None:
    _, _, plan_path = build(tmp_path)
    g0_path = plan_path.parent / "g0_approval.json"
    g0 = json.loads(g0_path.read_text(encoding="utf-8"))
    g0["decision"] = "PENDING"
    write_json(g0_path, g0)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["route_plan_valid"] == "PASS"
    assert report["test3_g0_evidence"] == "BLOCKED"
    assert report["test3_nohv_commissioning_ready"] == "BLOCKED"


def test_premature_physical_claim_is_rejected(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["claims"] = {"physical_test3_executed": "PASS", "stage_a_60v": "PASS"}

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-19-no-premature-claims")


def test_malformed_test2_json_is_fail_closed(tmp_path: Path) -> None:
    _, plan = make_test3_campaign(tmp_path)
    plan_path = write_json(tmp_path / "test3_nohv_campaign" / "plan.json", plan)
    test2_path = tmp_path / "test2.json"
    test2_path.write_text("{not-json", encoding="utf-8")
    report = ROUTE.build_verdict(test2_path, plan_path)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-01-test2-json")


def test_cli_writes_route_report_and_preserves_physical_blocks(tmp_path: Path) -> None:
    _, test2_path, plan_path = build(tmp_path)
    output = tmp_path / "route_verdict.json"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--test2-approval", str(test2_path), "--test3-plan", str(plan_path), "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "ROUTE_PLAN_VALID=PASS" in completed.stdout
    assert "TEST3_G0_EVIDENCE=PASS" in completed.stdout
    assert "TEST3_NOHV_COMMISSIONING_READY=PASS" in completed.stdout
    assert "PHYSICAL_TEST3_EXECUTED=BLOCKED" in completed.stdout
    assert "STAGE_A_60V=BLOCKED" in completed.stdout
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["limited_motor_start"] == "BLOCKED"

def test_plan_scope_cannot_enable_host_test(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["scope"]["allows_oew_host_test"] = True

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_plan_scope_cannot_enable_physical_execution(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["scope"]["allows_physical_nohv_execution"] = True

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_plan_scope_must_forbid_production(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["scope"]["forbids_production"] = False

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_plan_scope_test_must_be_mapcap_test3(tmp_path: Path) -> None:
    def mutate(plan: dict[str, Any]) -> None:
        plan["scope"]["test"] = "MAPCAP_TEST2"

    report, _, _ = build(tmp_path, test3_mutator=mutate)

    assert report["route_plan_valid"] == "FAIL"
    assert not check_result(report, "R-16-test3-scope")


def test_g0_scope_cannot_enable_host_test(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    g0_path = plan_path.parent / "g0_approval.json"
    g0 = json.loads(g0_path.read_text(encoding="utf-8"))
    g0["scope"]["allows_oew_host_test"] = True
    write_json(g0_path, g0)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-26-test3-g0-scope")


def test_g0_scope_must_forbid_production(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    g0_path = plan_path.parent / "g0_approval.json"
    g0 = json.loads(g0_path.read_text(encoding="utf-8"))
    g0["scope"]["forbids_production"] = False
    write_json(g0_path, g0)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-26-test3-g0-scope")


def test_missing_build_manifest_blocks_g0(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    (plan_path.parent / "diagnostic_build_manifest.json").unlink()
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_nohv_commissioning_ready"] == "FAIL"
    assert not check_result(report, "R-28-test3-g0-manifest")


def test_manifest_source_mismatch_breaks_chain(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha"] = "b" * 40
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-30-test3-manifest-source")


def test_manifest_missing_required_define_rejected(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["defines"].pop("OEW_MAP_L3")
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-33-test3-manifest-define-OEW_MAP_L3")


def test_manifest_extra_define_requires_approval(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["defines"]["OEW_DIAGNOSTIC_TRACE"] = "1"
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-35-test3-manifest-extra-defines")


def test_manifest_forbidden_define_rejected(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["defines"]["OEW_ALLOW_DC_LINK"] = "1"
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-34-test3-manifest-forbidden-defines")


def test_manifest_firmware_mismatch_breaks_chain(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["firmware"]["sha256"] = "f" * 64
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-36-test3-g0-firmware-chain")


def test_manifest_must_claim_complete_defines(tmp_path: Path) -> None:
    report, _, plan_path = build(tmp_path)
    manifest_path = plan_path.parent / "diagnostic_build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["defines_complete"] = False
    write_json(manifest_path, manifest)
    report = ROUTE.build_verdict(tmp_path / "test2_baseline_approval.json", plan_path)

    assert report["test3_g0_evidence"] == "FAIL"
    assert not check_result(report, "R-31-test3-manifest-defines-complete")
