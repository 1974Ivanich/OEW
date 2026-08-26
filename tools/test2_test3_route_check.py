#!/usr/bin/env python3
"""Offline readiness checker for the compact Test №2 → Test №3 → map route.

The tool parses two human-reviewed JSON declarations only.  It never opens a
serial port, starts sigrok, flashes firmware, talks to ST-Link, or commands a
board.  A PASS means that the *paper/evidence route* is ready for the next
reviewed gate; it never authorizes Test №3 execution, Stage A/DC-link, map load
or motor start.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TEST2_SCHEMA = "oew-test2-baseline-approval-v1"
TEST3_SCHEMA = "oew-test3-commissioning-plan-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_EVIDENCE_HASHES = ("uart_log", "adc_samples", "summary")
REQUIRED_TEST2_STATEMENT = "dc-link disconnected"
REQUIRED_TEST3_SCOPE = {
    "target": "physical-nohv-diagnostic-test3",
    "diagnostic_only": True,
    "forbids_dc_link": True,
    "forbids_stage_a": True,
    "forbids_foc": True,
    "forbids_vf": True,
    "forbids_autotune": True,
}
FORBIDDEN_PASS_CLAIMS = (
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


def is_git_sha(value: Any) -> bool:
    return isinstance(value, str) and GIT_SHA_RE.fullmatch(value) is not None


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def path_claim_is_safe(value: Any) -> bool:
    """Accept a descriptive campaign path but reject an empty or traversal-only claim."""
    if not nonempty_text(value):
        return False
    normalized = value.replace("\\", "/").strip()
    return normalized not in {".", ".."} and "/../" not in normalized


def test2_checks(approval: dict[str, Any] | None, parse_error: str | None) -> list[Check]:
    checks: list[Check] = []
    add_check(checks, "R-01-test2-json", approval is not None, "parseable object", parse_error or "object", "Test №2 approval must be parseable JSON.")
    if approval is None:
        return checks

    add_check(checks, "R-02-test2-schema", approval.get("schema") == TEST2_SCHEMA, TEST2_SCHEMA, approval.get("schema"), "Only the reviewed Test №2 baseline schema is accepted.")
    add_check(checks, "R-03-test2-id", approval.get("test_id") == "TEST2", "TEST2", approval.get("test_id"), "Approval must not relabel another physical test as Test №2.")
    add_check(checks, "R-04-test2-decision", approval.get("decision") == "PASS", "PASS", approval.get("decision"), "Route readiness requires an explicit accepted Test №2 baseline.")
    campaign = approval.get("campaign")
    add_check(checks, "R-05-test2-campaign", isinstance(campaign, dict) and nonempty_text(campaign.get("id")) and path_claim_is_safe(campaign.get("path")), "campaign id and non-empty evidence path", campaign, "The reviewed Test №2 campaign must be identifiable and locatable.")
    add_check(checks, "R-06-test2-source", is_git_sha(approval.get("source_sha")), "40–64 lowercase hexadecimal Git SHA", approval.get("source_sha"), "Test №2 approval must bind to an accepted source revision.")

    hashes = approval.get("evidence_sha256")
    evidence_ok = isinstance(hashes, dict) and all(is_sha256(hashes.get(name)) for name in REQUIRED_EVIDENCE_HASHES)
    add_check(checks, "R-07-test2-hashes", evidence_ok, list(REQUIRED_EVIDENCE_HASHES), hashes, "UART, ADC sample table and Test №2 summary must be hash-bound to the reviewer decision.")

    review = approval.get("review")
    review_ok = isinstance(review, dict) and nonempty_text(review.get("reviewer")) and nonempty_text(review.get("reviewed_at")) and nonempty_text(review.get("statement"))
    add_check(checks, "R-08-test2-review", review_ok, "reviewer/reviewed_at/statement", review, "A human reviewer must explicitly accept existing Test №2 evidence.")
    statement = review.get("statement", "") if isinstance(review, dict) else ""
    add_check(checks, "R-09-test2-nohv-statement", REQUIRED_TEST2_STATEMENT in statement.lower(), f"statement contains '{REQUIRED_TEST2_STATEMENT}'", statement, "Approval must explicitly preserve the physical no-HV boundary.")
    return checks


def forbidden_pass_claims(value: dict[str, Any]) -> dict[str, Any]:
    claims = value.get("claims", {})
    if not isinstance(claims, dict):
        return {"claims": claims}
    return {name: claims.get(name) for name in FORBIDDEN_PASS_CLAIMS if claims.get(name) == "PASS"}


def test3_checks(plan: dict[str, Any] | None, parse_error: str | None) -> list[Check]:
    checks: list[Check] = []
    add_check(checks, "R-10-test3-json", plan is not None, "parseable object", parse_error or "object", "Test №3 commissioning plan must be parseable JSON.")
    if plan is None:
        return checks

    add_check(checks, "R-11-test3-schema", plan.get("schema") == TEST3_SCHEMA, TEST3_SCHEMA, plan.get("schema"), "Only the reviewed Test №3 commissioning-plan schema is accepted.")
    add_check(checks, "R-12-test3-id", plan.get("test_id") == "TEST3", "TEST3", plan.get("test_id"), "No-HV commissioning plan must use the Test №3 identifier.")
    add_check(checks, "R-13-test3-pending", plan.get("decision") == "PENDING", "PENDING", plan.get("decision"), "This tool validates readiness; it cannot self-approve physical Test №3 execution.")
    add_check(checks, "R-14-test3-source", is_git_sha(plan.get("source_sha")), "40–64 lowercase hexadecimal Git SHA", plan.get("source_sha"), "Plan must bind to the reviewed source revision.")
    add_check(checks, "R-15-test3-g0-path", path_claim_is_safe(plan.get("g0_approval_path")), "non-empty approved G0 path", plan.get("g0_approval_path"), "The future Test №3 decision must be linked to its dedicated G0 approval file.")

    scope = plan.get("scope")
    scope_ok = isinstance(scope, dict) and all(scope.get(key) == expected for key, expected in REQUIRED_TEST3_SCOPE.items())
    add_check(checks, "R-16-test3-scope", scope_ok, REQUIRED_TEST3_SCOPE, scope, "All no-HV/default-deny prohibitions must remain explicit.")

    review = plan.get("scope_timing_review")
    review_ok = isinstance(review, dict) and nonempty_text(review.get("reviewer")) and nonempty_text(review.get("reviewed_at")) and nonempty_text(review.get("statement"))
    add_check(checks, "R-17-test3-scope-review", review_ok, "reviewer/reviewed_at/statement", review, "One reviewed scope/timing qualification is required for the combined campaign plan.")

    contexts = plan.get("contexts")
    pairs: list[tuple[int, int]] = []
    if isinstance(contexts, list):
        for context in contexts:
            if isinstance(context, dict) and isinstance(context.get("sector"), int) and isinstance(context.get("window"), int):
                pairs.append((context["sector"], context["window"]))
    actual = {"declared": len(contexts) if isinstance(contexts, list) else None, "parsed": len(pairs), "unique": len(set(pairs)), "pairs": sorted(set(pairs))}
    add_check(checks, "R-18-test3-contexts", len(pairs) == 12 and len(set(pairs)) == 12 and set(pairs) == EXPECTED_CONTEXTS, "all 12 unique sector/window contexts (0..5 × 0..1)", actual, "The single Test №3 plan must review the complete future characterization matrix without splitting it into separate physical tests.")

    claims = forbidden_pass_claims(plan)
    add_check(checks, "R-19-no-premature-claims", not claims, "no PASS claim for capture/Stage A/motor start", claims or "none", "No planning input may self-authorize characterization, DC-link or motor start.")
    return checks


def build_verdict(test2_path: Path, test3_path: Path) -> dict[str, Any]:
    test2, test2_error = load_json(test2_path)
    test3, test3_error = load_json(test3_path)
    checks = test2_checks(test2, test2_error) + test3_checks(test3, test3_error)
    route_ready = all(check.passed for check in checks)
    inputs: dict[str, str] = {}
    for label, path in (("test2_baseline_approval", test2_path), ("test3_commissioning_plan", test3_path)):
        try:
            inputs[label] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            inputs[label] = "UNAVAILABLE"
    return {
        "schema": "oew-test2-test3-map-route-verdict-v1",
        "inputs": {"test2_baseline_approval": str(test2_path), "test3_commissioning_plan": str(test3_path), "sha256": inputs},
        "checks": [asdict(check) for check in checks],
        "test2_baseline_accepted": "PASS" if all(check.passed for check in checks[:9]) else "FAIL",
        "test3_nohv_commissioning_ready": "PASS" if route_ready else "FAIL",
        "real_board_capture_valid": "BLOCKED",
        "stage_a_60v": "BLOCKED",
        "limited_motor_start": "BLOCKED",
        "route_verdict": "PASS" if route_ready else "FAIL",
        "next_gate": "Test №3 G0 and pre-flight human approval" if route_ready else "Correct failed route evidence/planning checks",
        "scope_note": "Offline readiness only; no hardware interface was opened and no physical execution is authorized.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline checker of compact Test №2 → Test №3 → map route readiness.")
    parser.add_argument("--test2-approval", type=Path, required=True, help="Reviewed test2_baseline_approval.json")
    parser.add_argument("--test3-plan", type=Path, required=True, help="Reviewed test3_commissioning_plan.json")
    parser.add_argument("--output", type=Path, default=None, help="Optional route verdict JSON path")
    args = parser.parse_args(argv)
    verdict = build_verdict(args.test2_approval, args.test3_plan)
    destination = args.output or args.test3_plan.parent / "test2_test3_route_verdict.json"
    destination.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ROUTE_VERDICT={verdict['route_verdict']}")
    print(f"TEST2_BASELINE_ACCEPTED={verdict['test2_baseline_accepted']}")
    print(f"TEST3_NOHV_COMMISSIONING_READY={verdict['test3_nohv_commissioning_ready']}")
    print("REAL_BOARD_CAPTURE_VALID=BLOCKED")
    print("STAGE_A_60V=BLOCKED")
    print("LIMITED_MOTOR_START=BLOCKED")
    print(f"REPORT={destination}")
    return 0 if verdict["route_verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
