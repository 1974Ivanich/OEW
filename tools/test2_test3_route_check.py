#!/usr/bin/env python3
"""Offline fail-closed checker for the compact Test №2 → Test №3 → map route.

The checker reads only local JSON/evidence files.  It never opens a serial port,
starts sigrok, flashes firmware, calls ST-Link, or sends a command to a board.
It can validate a *route plan* and a locally bound Test №3 G0 evidence bundle,
but it never declares physical execution, a current map, Stage A or motor start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

TEST2_SCHEMA = "oew-test2-baseline-approval-v2-evidence-bound"
TEST3_SCHEMA = "oew-test3-commissioning-plan-v2-g0-bound"
TEST3_G0_SCHEMA = "h1-g0-approval-v2-test3-transition"
TEST3_G0_GATE = "HIL_TEST3_G0"
TEST3_G0_TARGET = "physical-nohv-diagnostic-test3"
TEST3_G0_TEST = "MAPCAP_TEST3"
TEST3_MANIFEST_SCHEMA = "h1-g0-diagnostic-manifest-v2-test3-transition"
TEST3_MANIFEST_NAME = "diagnostic_build_manifest.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_TEST2_EVIDENCE = ("uart_log", "adc_samples", "summary", "build_log")
REQUIRED_TEST2_STATEMENT = "dc-link disconnected"
REQUIRED_TEST3_SCOPE = {
    "target": TEST3_G0_TARGET,
    "test": TEST3_G0_TEST,
    "allows_oew_host_test": False,
    "allows_physical_nohv_execution": False,
    "diagnostic_only": True,
    "forbids_stage_a": True,
    "forbids_production": True,
    "forbids_dc_link": True,
    "forbids_foc": True,
    "forbids_vf": True,
    "forbids_autotune": True,
}

REQUIRED_TEST3_DEFINES = {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
}

FORBIDDEN_DEFINES = {
    "OEW_ALLOW_DC_LINK",
    "OEW_ALLOW_CONTROL_ADMISSION",
    "OEW_STAGE_A",
    "OEW_FOC_ENABLE",
    "OEW_VF_ENABLE",
    "OEW_AUTOTUNE_ENABLE",
}
FORBIDDEN_PASS_CLAIMS = (
    "physical_test3_executed",
    "real_board_capture_valid",
    "stage_a_60v",
    "limited_motor_start",
)
EXPECTED_CONTEXTS = frozenset((sector, window) for sector in range(6) for window in range(2))


@dataclass(frozen=True)
class Check:
    identifier: str
    passed: bool
    expected: Any
    actual: Any
    detail: str


def add_check(checks: list[Check], identifier: str, passed: bool, expected: Any, actual: Any, detail: str) -> None:
    checks.append(Check(identifier, bool(passed), expected, actual, detail))


def load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "root must be a JSON object"
    return value, None


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_git_sha(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if GIT_SHA_RE.fullmatch(candidate) else None


def normalized_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if SHA256_RE.fullmatch(candidate) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_campaign_root(value: Any) -> Path | None:
    """Accept an existing real directory only; campaign root itself cannot be a symlink."""
    if not nonempty_text(value):
        return None
    raw = Path(str(value)).expanduser()
    try:
        if raw.is_symlink() or not raw.is_dir():
            return None
        return raw.resolve(strict=True)
    except OSError:
        return None


def safe_relative_file(root: Path | None, relative_path: Any) -> Path | None:
    """Return a regular non-symlink file contained by root; reject traversal/links."""
    if root is None or not isinstance(relative_path, str) or not relative_path.strip():
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    current = root
    try:
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def test2_checks(approval: dict[str, Any] | None, parse_error: str | None) -> list[Check]:
    checks: list[Check] = []
    add_check(checks, "R-01-test2-json", approval is not None, "parseable JSON object", parse_error or "object", "Test №2 approval must be parseable JSON.")
    if approval is None:
        return checks

    add_check(checks, "R-02-test2-schema", approval.get("schema") == TEST2_SCHEMA, TEST2_SCHEMA, approval.get("schema"), "Only the evidence-bound Test №2 approval schema is accepted.")
    add_check(checks, "R-03-test2-id", approval.get("test_id") == "TEST2", "TEST2", approval.get("test_id"), "Approval must not relabel another physical test as Test №2.")
    add_check(checks, "R-04-test2-decision", approval.get("decision") == "PASS", "PASS", approval.get("decision"), "Route plan requires explicit accepted Test №2 baseline evidence.")

    campaign = approval.get("campaign")
    root = safe_campaign_root(campaign.get("path") if isinstance(campaign, Mapping) else None)
    campaign_ok = isinstance(campaign, Mapping) and nonempty_text(campaign.get("id")) and root is not None
    add_check(checks, "R-05-test2-campaign", campaign_ok, "existing non-symlink campaign directory with id", campaign, "Evidence must remain in a real local campaign directory.")

    approval_source = normalized_git_sha(approval.get("source_sha"))
    add_check(checks, "R-06-test2-source-format", approval_source is not None, "40–64 lowercase hexadecimal Git SHA", approval.get("source_sha"), "Approval must bind to an immutable source revision.")
    source_identity = safe_relative_file(root, approval.get("source_identity_path"))
    source_file_text: str | None = None
    if source_identity is not None:
        try:
            source_file_text = source_identity.read_text(encoding="utf-8").strip().lower()
        except (OSError, UnicodeDecodeError):
            source_file_text = None
    add_check(checks, "R-07-test2-source-file", source_identity is not None and normalized_git_sha(source_file_text) is not None and source_file_text == approval_source, "campaign source identity equals approval source SHA", {"path": str(source_identity) if source_identity else None, "value": source_file_text, "approval": approval_source}, "Approval source SHA must match retained source_sha.txt bytes inside the campaign.")

    evidence = approval.get("evidence")
    evidence_mapping = evidence if isinstance(evidence, Mapping) else {}
    for name in REQUIRED_TEST2_EVIDENCE:
        item = evidence_mapping.get(name)
        expected_hash = normalized_sha256(item.get("sha256") if isinstance(item, Mapping) else None)
        path = safe_relative_file(root, item.get("path") if isinstance(item, Mapping) else None)
        actual_hash: str | None = None
        if path is not None:
            try:
                actual_hash = sha256_file(path)
            except OSError:
                actual_hash = None
        add_check(checks, f"R-08-test2-evidence-{name}", expected_hash is not None and path is not None and actual_hash == expected_hash, "existing non-symlink campaign file with matching SHA-256", {"path": str(path) if path else None, "expected": expected_hash, "actual": actual_hash}, "Evidence hash must be computed from the retained file, not merely declared in JSON.")

    review = approval.get("review")
    review_ok = isinstance(review, Mapping) and nonempty_text(review.get("reviewer")) and nonempty_text(review.get("reviewed_at")) and nonempty_text(review.get("statement"))
    add_check(checks, "R-09-test2-review", review_ok, "reviewer/reviewed_at/statement", review, "A human reviewer must explicitly accept existing Test №2 evidence.")
    statement = review.get("statement", "") if isinstance(review, Mapping) else ""
    add_check(checks, "R-10-test2-nohv-statement", REQUIRED_TEST2_STATEMENT in statement.lower(), f"statement contains '{REQUIRED_TEST2_STATEMENT}'", statement, "Approval must explicitly preserve the physical no-HV boundary.")
    return checks


def plan_checks(plan: dict[str, Any] | None, parse_error: str | None) -> list[Check]:
    checks: list[Check] = []
    add_check(checks, "R-11-test3-plan-json", plan is not None, "parseable JSON object", parse_error or "object", "Test №3 commissioning plan must be parseable JSON.")
    if plan is None:
        return checks

    add_check(checks, "R-12-test3-plan-schema", plan.get("schema") == TEST3_SCHEMA, TEST3_SCHEMA, plan.get("schema"), "Only the G0-bound Test №3 commissioning-plan schema is accepted.")
    add_check(checks, "R-13-test3-id", plan.get("test_id") == "TEST3", "TEST3", plan.get("test_id"), "No-HV commissioning plan must use the Test №3 identifier.")
    add_check(checks, "R-14-test3-plan-pending", plan.get("decision") == "PENDING", "PENDING", plan.get("decision"), "Plan cannot self-approve physical Test №3 execution.")
    plan_source = normalized_git_sha(plan.get("source_sha"))
    add_check(checks, "R-15-test3-source-format", plan_source is not None, "40–64 lowercase hexadecimal Git SHA", plan.get("source_sha"), "Plan must bind to one reviewed source revision.")

    scope = plan.get("scope")
    scope_ok = isinstance(scope, Mapping) and all(scope.get(key) == expected for key, expected in REQUIRED_TEST3_SCOPE.items())
    add_check(checks, "R-16-test3-scope", scope_ok, REQUIRED_TEST3_SCOPE, scope, "All no-HV/default-deny prohibitions must remain explicit.")

    review = plan.get("scope_timing_review")
    review_ok = isinstance(review, Mapping) and nonempty_text(review.get("reviewer")) and nonempty_text(review.get("reviewed_at")) and nonempty_text(review.get("statement"))
    add_check(checks, "R-17-test3-scope-review", review_ok, "reviewer/reviewed_at/statement", review, "One reviewed scope/timing qualification is required for the combined campaign plan.")

    contexts = plan.get("contexts")
    pairs: list[tuple[int, int]] = []
    if isinstance(contexts, list):
        for context in contexts:
            if isinstance(context, Mapping) and isinstance(context.get("sector"), int) and isinstance(context.get("window"), int):
                pairs.append((context["sector"], context["window"]))
    actual = {"declared": len(contexts) if isinstance(contexts, list) else None, "parsed": len(pairs), "unique": len(set(pairs)), "pairs": sorted(set(pairs))}
    add_check(checks, "R-18-test3-contexts", len(pairs) == 12 and len(set(pairs)) == 12 and set(pairs) == EXPECTED_CONTEXTS, "all 12 unique sector/window contexts (0..5 × 0..1)", actual, "The single Test №3 plan must review the full future characterization matrix without splitting it into separate physical tests.")

    claims = plan.get("claims", {})
    premature = {name: claims.get(name) for name in FORBIDDEN_PASS_CLAIMS if isinstance(claims, Mapping) and claims.get(name) == "PASS"}
    add_check(checks, "R-19-no-premature-claims", not premature, "no PASS claim for physical Test №3/capture/Stage A/motor start", premature or "none", "No planning input may self-authorize physical execution, characterization, DC-link or motor start.")
    return checks


def test3_g0_checks(plan_path: Path, plan: dict[str, Any] | None) -> list[Check]:
    checks: list[Check] = []
    if plan is None:
        add_check(checks, "R-20-test3-g0-plan", False, "parseable Test №3 plan", None, "G0 cannot be located without a parseable plan.")
        return checks
    g0_relative = plan.get("g0_approval_path")
    g0_path = safe_relative_file(plan_path.parent.resolve(), g0_relative)
    g0, g0_error = load_json(g0_path) if g0_path is not None else (None, "unsafe/missing g0_approval_path")
    add_check(checks, "R-20-test3-g0-present", g0 is not None, "existing non-symlink g0 approval JSON inside Test №3 campaign", {"path": str(g0_path) if g0_path else None, "error": g0_error}, "Physical Test №3 readiness requires retained approved G0 evidence.")
    if g0 is None:
        return checks

    add_check(checks, "R-21-test3-g0-schema", g0.get("schema") == TEST3_G0_SCHEMA, TEST3_G0_SCHEMA, g0.get("schema"), "G0 approval must use Test №3 transition schema.")
    add_check(checks, "R-22-test3-g0-gate", g0.get("gate") == TEST3_G0_GATE and g0.get("test_id") == "TEST3", {"gate": TEST3_G0_GATE, "test_id": "TEST3"}, {"gate": g0.get("gate"), "test_id": g0.get("test_id")}, "G0 evidence must apply to Test №3 only.")
    add_check(checks, "R-23-test3-g0-approved", g0.get("decision") == "APPROVED", "APPROVED", g0.get("decision"), "PENDING/FAIL G0 cannot make physical Test №3 ready.")
    add_check(checks, "R-24-test3-g0-review", g0.get("role") == "safety-owner" and all(nonempty_text(g0.get(key)) for key in ("approval_id", "approver", "approved_at")), "safety-owner plus approval_id/approver/approved_at", {key: g0.get(key) for key in ("role", "approval_id", "approver", "approved_at")}, "Traceable safety-owner approval is required.")

    plan_source = normalized_git_sha(plan.get("source_sha"))
    g0_source = normalized_git_sha(g0.get("source_sha"))
    add_check(checks, "R-25-test3-g0-source", plan_source is not None and plan_source == g0_source, "plan source SHA == approved G0 source SHA", {"plan": plan_source, "g0": g0_source}, "G0 cannot be transferred between source revisions.")
    scope = g0.get("scope")
    scope_ok = isinstance(scope, Mapping) and all(scope.get(key) == expected for key, expected in REQUIRED_TEST3_SCOPE.items())
    add_check(checks, "R-26-test3-g0-scope", scope_ok, REQUIRED_TEST3_SCOPE, scope, "Approved G0 must preserve the exact no-HV/default-deny scope.")

    firmware_expected = normalized_sha256(g0.get("firmware_sha256"))
    firmware_path = safe_relative_file(plan_path.parent.resolve(), g0.get("firmware_path"))
    firmware_actual: str | None = None
    if firmware_path is not None:
        try:
            firmware_actual = sha256_file(firmware_path)
        except OSError:
            firmware_actual = None
    add_check(checks, "R-27-test3-g0-firmware", firmware_expected is not None and firmware_path is not None and firmware_actual == firmware_expected, "existing non-symlink firmware file matching approved SHA-256", {"path": str(firmware_path) if firmware_path else None, "expected": firmware_expected, "actual": firmware_actual}, "Approved firmware identity must be verified from retained local binary bytes.")

    # --- provenance chain: G0 source_sha -> manifest source_sha -> exact defines
    #     -> approved_extra_defines -> retained firmware -> SHA-256 ---
    manifest_path = safe_relative_file(plan_path.parent.resolve(), TEST3_MANIFEST_NAME)
    manifest, manifest_error = load_json(manifest_path) if manifest_path is not None else (None, "missing/non-regular diagnostic_build_manifest.json")
    add_check(checks, "R-28-test3-g0-manifest", manifest is not None, "retained diagnostic_build_manifest.json in Test №3 campaign", {"path": str(manifest_path) if manifest_path else None, "error": manifest_error}, "The build manifest must be a real retained artifact, not a declaration inside G0.")
    if manifest is None:
        return checks

    add_check(checks, "R-29-test3-manifest-contract",
              manifest.get("schema") == TEST3_MANIFEST_SCHEMA
              and manifest.get("gate") == TEST3_G0_GATE
              and manifest.get("test_id") == "TEST3"
              and manifest.get("target") == TEST3_G0_TARGET
              and manifest.get("test") == TEST3_G0_TEST,
              {"schema": TEST3_MANIFEST_SCHEMA, "gate": TEST3_G0_GATE, "test_id": "TEST3", "target": TEST3_G0_TARGET, "test": TEST3_G0_TEST},
              {key: manifest.get(key) for key in ("schema", "gate", "test_id", "target", "test")},
              "Manifest must claim the same Test №3 G0 build contract.")

    manifest_source = normalized_git_sha(manifest.get("source_sha"))
    add_check(checks, "R-30-test3-manifest-source",
              manifest_source is not None and manifest_source == g0_source and g0_source == plan_source,
              "plan source SHA == G0 source SHA == manifest source SHA",
              {"plan": plan_source, "g0": g0_source, "manifest": manifest_source},
              "The retained manifest must bind to the exact same source revision as the approved G0.")

    add_check(checks, "R-31-test3-manifest-defines-complete", manifest.get("defines_complete") is True, True, manifest.get("defines_complete"), "The manifest must assert a complete list of preprocessor defines.")

    defines = manifest.get("defines")
    defines_ok = isinstance(defines, Mapping)
    add_check(checks, "R-32-test3-manifest-defines-object", defines_ok, "name-to-value JSON object", type(defines).__name__, "Defines must be a machine-readable object.")
    if defines_ok:
        for name, expected in REQUIRED_TEST3_DEFINES.items():
            actual = defines.get(name)
            add_check(checks, f"R-33-test3-manifest-define-{name}", actual == expected, expected, actual, "Required Test №3 diagnostic define must match exactly.")
        forbidden_present = sorted(name for name in FORBIDDEN_DEFINES if name in defines)
        add_check(checks, "R-34-test3-manifest-forbidden-defines", not forbidden_present, "none", forbidden_present, "Stage-A/admission/energise defines are outside the no-HV G0 scope.")
        extra_actual = sorted(name for name in defines if name not in REQUIRED_TEST3_DEFINES)
        approved_extra = g0.get("approved_extra_defines")
        valid_extra = isinstance(approved_extra, list) and all(isinstance(item, str) for item in approved_extra)
        normalized_approved = sorted(approved_extra) if valid_extra else None
        add_check(checks, "R-35-test3-manifest-extra-defines", normalized_approved is not None and normalized_approved == extra_actual, "G0 approved_extra_defines == manifest extra defines", {"approved": normalized_approved, "actual": extra_actual}, "Every additional preprocessor define must be explicitly approved in G0.")

    manifest_firmware = manifest.get("firmware")
    mfw_expected = normalized_sha256(manifest_firmware.get("sha256") if isinstance(manifest_firmware, Mapping) else None)
    mfw_relative = manifest_firmware.get("path") if isinstance(manifest_firmware, Mapping) else None
    mfw_path = safe_relative_file(plan_path.parent.resolve(), mfw_relative)
    mfw_actual: str | None = None
    if mfw_path is not None:
        try:
            mfw_actual = sha256_file(mfw_path)
        except OSError:
            mfw_actual = None
    same_firmware = g0.get("firmware_path") == mfw_relative
    add_check(checks, "R-36-test3-g0-firmware-chain",
              same_firmware and mfw_expected is not None and mfw_path is not None and mfw_actual == mfw_expected and mfw_expected == firmware_expected,
              "manifest firmware path/SHA-256 == G0 firmware == retained binary bytes",
              {"g0_path": g0.get("firmware_path"), "manifest_path": mfw_relative, "g0_sha": firmware_expected, "manifest_sha": mfw_expected, "actual": mfw_actual},
              "The provenance chain must end at the exact retained firmware binary.")
    return checks


def g0_status(checks: list[Check]) -> str:
    """Distinguish a planned/missing gate from a retained but invalid G0 artifact."""
    by_id = {check.identifier: check for check in checks}
    present = by_id.get("R-20-test3-g0-present")
    approved = by_id.get("R-23-test3-g0-approved")
    if present is None or not present.passed:
        return "BLOCKED"
    if approved is not None and approved.actual == "PENDING":
        return "BLOCKED"
    return "PASS" if checks and all(check.passed for check in checks) else "FAIL"


def input_hash(path: Path) -> str:
    try:
        return sha256_file(path)
    except OSError:
        return "UNAVAILABLE"


def build_verdict(test2_path: Path, test3_path: Path) -> dict[str, Any]:
    test2, test2_error = load_json(test2_path)
    test3, test3_error = load_json(test3_path)
    checks_test2 = test2_checks(test2, test2_error)
    checks_plan = plan_checks(test3, test3_error)
    checks_g0 = test3_g0_checks(test3_path, test3)
    test2_ok = bool(checks_test2) and all(check.passed for check in checks_test2)
    plan_ok = bool(checks_plan) and all(check.passed for check in checks_plan)
    g0_evidence_status = g0_status(checks_g0)
    route_plan_valid = test2_ok and plan_ok
    return {
        "schema": "oew-test2-test3-map-route-verdict-v2-evidence-bound",
        "inputs": {
            "test2_baseline_approval": str(test2_path),
            "test3_commissioning_plan": str(test3_path),
            "sha256": {
                "test2_baseline_approval": input_hash(test2_path),
                "test3_commissioning_plan": input_hash(test3_path),
            },
        },
        "checks": [asdict(check) for check in checks_test2 + checks_plan + checks_g0],
        "route_plan_valid": "PASS" if route_plan_valid else "FAIL",
        "test2_baseline_accepted": "PASS" if test2_ok else "FAIL",
        "test3_g0_evidence": g0_evidence_status,
        "test3_nohv_commissioning_ready": g0_evidence_status if route_plan_valid else "FAIL",
        "physical_test3_executed": "BLOCKED",
        "real_board_capture_valid": "BLOCKED",
        "stage_a_60v": "BLOCKED",
        "limited_motor_start": "BLOCKED",
        "next_gate": "Test №3 G0/pre-flight human approval" if route_plan_valid and g0_evidence_status == "BLOCKED" else "Correct retained Test №3 G0 evidence" if route_plan_valid and g0_evidence_status == "FAIL" else "Controlled Test №3 no-HV execution" if route_plan_valid else "Correct failed Test №2 evidence or Test №3 planning checks",
        "scope_note": "Offline evidence/plan validation only; no hardware interface was opened and no physical execution is authorized.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline checker of evidence-bound Test №2 → Test №3 → map route readiness.")
    parser.add_argument("--test2-approval", type=Path, required=True, help="Evidence-bound test2_baseline_approval.json")
    parser.add_argument("--test3-plan", type=Path, required=True, help="G0-bound test3_commissioning_plan.json")
    parser.add_argument("--output", type=Path, default=None, help="Optional route verdict JSON path")
    args = parser.parse_args(argv)
    verdict = build_verdict(args.test2_approval, args.test3_plan)
    destination = args.output or args.test3_plan.parent / "test2_test3_route_verdict.json"
    destination.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ROUTE_PLAN_VALID={verdict['route_plan_valid']}")
    print(f"TEST2_BASELINE_ACCEPTED={verdict['test2_baseline_accepted']}")
    print(f"TEST3_G0_EVIDENCE={verdict['test3_g0_evidence']}")
    print(f"TEST3_NOHV_COMMISSIONING_READY={verdict['test3_nohv_commissioning_ready']}")
    print("PHYSICAL_TEST3_EXECUTED=BLOCKED")
    print("REAL_BOARD_CAPTURE_VALID=BLOCKED")
    print("STAGE_A_60V=BLOCKED")
    print("LIMITED_MOTOR_START=BLOCKED")
    print(f"REPORT={destination}")
    return 0 if verdict["route_plan_valid"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
