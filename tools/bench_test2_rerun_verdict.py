#!/usr/bin/env python3
"""Offline-only terminal verdict for a repeated physical no-HV MapCapture run.

The tool reads campaign ``summary.json`` and continuous ``uart.log`` only.  It
never opens COM/USB/ST-Link/sigrok, flashes firmware, or emits MCU commands.
A PASS proves consistency with the existing no-HV terminal UART contract; it
never authorizes Stage A or a DC-link test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


EXPECTED_STATE = 5
EXPECTED_TERM = -12
EXPECTED_DETAIL = 7
EXPECTED_ADC_STATUS = 7
MAX_RAW_VBUS = 9
MIN_VBUS_MV = 0
MAX_VBUS_MV_EXCLUSIVE = 1000
MAX_ABS_SHUNT_MA = 10_000

SUMMARY_NAME = "summary.json"
UART_LOG_NAME = "uart.log"
OUTPUT_NAME = "rerun_terminal_verdict.json"

STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)"
    r":detail=(?P<detail>-?\d+):raw_vbus=(?P<raw_vbus>\d+)"
    r":vbus_mv=(?P<vbus_mv>-?\d+):i1_ma=(?P<i1_ma>-?\d+)"
    r":i2_ma=(?P<i2_ma>-?\d+):adc_status=(?P<adc_status>-?\d+)"
    r":sector=(?P<sector>\d+):window=(?P<window>\d+)"
)
ARM_RE = re.compile(
    r"@MC:ARM:cap=(?P<cap>\d+):rc=(?P<rc>-?\d+)"
    r"(?::offsets_valid=(?P<offsets_valid>[01]):inj_start_rc=(?P<inj_start_rc>0|-1|NA))?"
    r"(?=\r|\n|$)"
)
RUN_RE = re.compile(r"@MC:RUN:rc=(?P<rc>-?\d+)")
DRAIN_RE = re.compile(r"@MC:DRAIN:records=(?P<records>\d+)")

REQUIRED_EVALUATOR_CHECKS = (
    "arm_rc_zero",
    "run_rc_zero",
    "preflight_adc_parsed",
    "preflight_raw_vbus_median_nohv",
    "preflight_raw_vbus_max_hard",
    "preflight_i1_not_saturated",
    "preflight_i2_not_saturated",
    "status_parsed_extended_contract",
    "faulted_state",
    "terminal_is_limit_exceeded",
    "detail_is_vbus_low",
    "terminal_adc_window_invalid",
    "terminal_raw_vbus_nohv",
    "terminal_vbus_below_profile_min",
    "terminal_i1_within_limit",
    "terminal_i2_within_limit",
    "zero_frames",
    "zero_dropped",
    "zero_available",
    "drain_parsed",
    "zero_drain_records",
    "no_record_lines",
)


@dataclass(frozen=True)
class Check:
    """One auditable fail-closed validation decision."""

    id: str
    result: bool
    expected: Any
    actual: Any
    detail: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_statuses(text: str) -> list[dict[str, int]]:
    return [
        {key: int(value) for key, value in match.groupdict().items()}
        for match in STATUS_RE.finditer(text)
    ]


def parse_last_status(text: str) -> Optional[dict[str, int]]:
    statuses = parse_statuses(text)
    return statuses[-1] if statuses else None


def is_accepted_status(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    required = {
        "state", "term", "frames", "dropped", "avail", "detail", "raw_vbus",
        "vbus_mv", "i1_ma", "i2_ma", "adc_status",
    }
    if not required.issubset(status):
        return False
    try:
        return (
            int(status["state"]) == EXPECTED_STATE
            and int(status["term"]) == EXPECTED_TERM
            and int(status["detail"]) == EXPECTED_DETAIL
            and int(status["adc_status"]) == EXPECTED_ADC_STATUS
            and int(status["raw_vbus"]) <= MAX_RAW_VBUS
            and MIN_VBUS_MV <= int(status["vbus_mv"]) < MAX_VBUS_MV_EXCLUSIVE
            and abs(int(status["i1_ma"])) <= MAX_ABS_SHUNT_MA
            and abs(int(status["i2_ma"])) <= MAX_ABS_SHUNT_MA
            and int(status["frames"]) == 0
            and int(status["dropped"]) == 0
            and int(status["avail"]) == 0
        )
    except (TypeError, ValueError):
        return False


def status_projection(status: Any) -> Optional[dict[str, int]]:
    if not isinstance(status, dict):
        return None
    fields = (
        "state", "term", "frames", "dropped", "avail", "detail", "raw_vbus",
        "vbus_mv", "i1_ma", "i2_ma", "adc_status",
    )
    try:
        return {field: int(status[field]) for field in fields}
    except (KeyError, TypeError, ValueError):
        return None


def loaded_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "корень JSON должен быть object"
    return value, None


def check(checks: list[Check], identifier: str, result: bool, expected: Any, actual: Any, detail: str) -> None:
    checks.append(Check(identifier, bool(result), expected, actual, detail))


def accepted_arm_before_run(uart_text: str) -> tuple[bool, dict[str, int]]:
    accepted_arm_offsets = [
        match.start() for match in ARM_RE.finditer(uart_text)
        if int(match.group("cap")) > 0 and int(match.group("rc")) == 0
    ]
    accepted_run_offsets = [
        match.start() for match in RUN_RE.finditer(uart_text)
        if int(match.group("rc")) == 0
    ]
    result = any(arm_offset < run_offset for arm_offset in accepted_arm_offsets for run_offset in accepted_run_offsets)
    return result, {"accepted_arm_count": len(accepted_arm_offsets), "accepted_run_count": len(accepted_run_offsets)}


def build_verdict(campaign: Path) -> dict[str, Any]:
    """Validate only saved evidence and return a full machine-readable report."""
    summary_path = campaign / SUMMARY_NAME
    uart_path = campaign / UART_LOG_NAME
    checks: list[Check] = []
    summary, summary_error = loaded_json(summary_path) if summary_path.is_file() else (None, "файл отсутствует")
    try:
        uart_text = uart_path.read_text(encoding="utf-8")
        uart_error: Optional[str] = None
    except (OSError, UnicodeDecodeError) as exc:
        uart_text, uart_error = "", str(exc)

    check(checks, "RV-00-summary-json", summary is not None, "parseable JSON object", summary_error or "OK", "summary.json обязателен")
    check(checks, "RV-00-uart-log", uart_error is None and bool(uart_text), "non-empty UTF-8 UART log", uart_error or len(uart_text), "uart.log обязателен и не может быть пустым")

    verdict = summary.get("verdict") if summary else None
    execution = summary.get("execution") if summary else None
    mode = execution.get("mode") if isinstance(execution, dict) else None
    check(checks, "RV-01-physical-execution", mode == "PHYSICAL", "PHYSICAL", mode, "simulation или неизвестный mode не являются physical evidence")

    automation = verdict.get("automation") if isinstance(verdict, dict) else None
    scope = verdict.get("scope") if isinstance(verdict, dict) else None
    final = verdict.get("final") if isinstance(verdict, dict) else None
    check(checks, "RV-02-automation", automation == "PASS", "PASS", automation, "summary должен сохранить PASS существующего UART evaluator")
    check(checks, "RV-03-scope-final", (scope, final) in (("PENDING", "PENDING"), ("PASS", "PASS")), "PENDING/PENDING or PASS/PASS", {"scope": scope, "final": final}, "offline parser не оценивает ручной scope, но отклоняет противоречивую физическую пару")

    evaluator_checks = verdict.get("checks") if isinstance(verdict, dict) else None
    evaluator_checks = evaluator_checks if isinstance(evaluator_checks, dict) else {}
    missing_or_false = [name for name in REQUIRED_EVALUATOR_CHECKS if evaluator_checks.get(name) is not True]
    check(checks, "RV-04-evaluator-checks", not missing_or_false, "all required no-HV checks=true", missing_or_false, "никакой существующий fail-closed evaluator check не может быть пропущен или false")

    summary_status = verdict.get("status") if isinstance(verdict, dict) else None
    check(checks, "RV-05-summary-terminal", is_accepted_status(summary_status), "state=5 term=-12 detail=7 adc_status=7 with no-HV limits", status_projection(summary_status), "summary terminal status должен соответствовать immutable no-HV contract")

    sigrok_rc = verdict.get("sigrok_capture_returncode_zero") if isinstance(verdict, dict) else None
    sigrok_csv = verdict.get("sigrok_csv_exists") if isinstance(verdict, dict) else None
    check(checks, "RV-06-sigrok-declaration", sigrok_rc is True and sigrok_csv is True, {"returncode_zero": True, "csv_exists": True}, {"returncode_zero": sigrok_rc, "csv_exists": sigrok_csv}, "summary обязан декларативно подтвердить успешный capture и CSV")

    arm_before_run, arm_run_actual = accepted_arm_before_run(uart_text)
    check(checks, "RV-07-uart-arm-before-run", arm_before_run, "accepted ARM before accepted RUN", arm_run_actual, "accepted ARM имеет cap>0/rc=0; accepted RUN имеет rc=0")

    uart_terminal = parse_last_status(uart_text)
    check(checks, "RV-08-uart-terminal-contract", is_accepted_status(uart_terminal), "state=5 term=-12 detail=7 adc_status=7 with no-HV limits", status_projection(uart_terminal), "последняя extended status строка UART должна быть accepted terminal evidence")
    check(checks, "RV-08-summary-uart-match", status_projection(summary_status) == status_projection(uart_terminal) and status_projection(summary_status) is not None, "identical terminal status projections", {"summary": status_projection(summary_status), "uart": status_projection(uart_terminal)}, "summary и непрерывный UART log не могут расходиться")

    drains = [int(match.group("records")) for match in DRAIN_RE.finditer(uart_text)]
    check(checks, "RV-09-uart-drain-records", bool(drains) and drains[-1] == 0 and "@MC:REC:" not in uart_text, "last drain=0 and no @MC:REC", {"drains": drains, "record_marker": "@MC:REC:" in uart_text}, "record evidence противоречит no-HV no-record terminal contract")
    check(checks, "RV-10-physical-provenance", "mode=SIMULATED" not in uart_text and "@SIM:" not in uart_text, "no simulation markers", {"mode_simulated": "mode=SIMULATED" in uart_text, "sim_marker": "@SIM:" in uart_text}, "simulated UART evidence не является физическим прогоном")

    terminal_verdict = "PASS" if all(item.result for item in checks) else "FAIL"
    return {
        "schema_version": 1,
        "validated_utc": utc_now(),
        "campaign": str(campaign),
        "inputs": {
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
            "uart_log": {"path": str(uart_path), "sha256": sha256_file(uart_path)},
        },
        "checks": [asdict(item) for item in checks],
        "summary_terminal_status": status_projection(summary_status),
        "uart_terminal_status": status_projection(uart_terminal),
        "terminal_verdict": terminal_verdict,
        "stage_a_60v": "BLOCKED",
        "safety_boundary": "This offline terminal-evidence verdict never authorizes Stage A, DC-link, FOC, V/f, autotune, map build, or a physical test.",
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline fail-closed verdict for repeated no-HV summary.json + uart.log.")
    parser.add_argument("--campaign", type=Path, required=True, help="Каталог кампании с summary.json и uart.log")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    campaign = args.campaign.resolve()
    if not campaign.is_dir():
        print("TERMINAL_VERDICT=FAIL stage_a_60v=BLOCKED reason=campaign_directory_missing")
        return 2
    report = build_verdict(campaign)
    write_json(campaign / OUTPUT_NAME, report)
    print(f"TERMINAL_VERDICT={report['terminal_verdict']} stage_a_60v=BLOCKED report={campaign / OUTPUT_NAME}")
    return 0 if report["terminal_verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
