#!/usr/bin/env python3
"""Authoritative offline Test3 G0 gate: route checks + retained-build provenance.

No hardware access is performed.  A physical Test3 readiness PASS is impossible
unless both the existing route checker and the build-provenance verifier pass.
"""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from test2_test3_route_check import build_verdict
    from test3_build_provenance_check import provenance_checks
except ModuleNotFoundError:
    from tools.test2_test3_route_check import build_verdict
    from tools.test3_build_provenance_check import provenance_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline composite Test3 G0 gate")
    parser.add_argument("--test2-approval", type=Path, required=True)
    parser.add_argument("--test3-plan", type=Path, required=True)
    args = parser.parse_args(argv)

    route = build_verdict(args.test2_approval, args.test3_plan)
    provenance = provenance_checks(args.test3_plan.parent)
    route_pass = route["route_plan_valid"] == "PASS" and route["test3_g0_evidence"] == "PASS"
    provenance_pass = bool(provenance) and all(passed for _, passed, _ in provenance)

    print(f"ROUTE_G0_PASS={'PASS' if route_pass else 'FAIL'}")
    print(f"BUILD_PROVENANCE_PASS={'PASS' if provenance_pass else 'FAIL'}")
    print(f"TEST3_NOHV_COMMISSIONING_READY={'PASS' if route_pass and provenance_pass else 'BLOCKED'}")
    print("PHYSICAL_TEST3_EXECUTED=BLOCKED")
    print("STAGE_A_60V=BLOCKED")
    return 0 if route_pass and provenance_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
