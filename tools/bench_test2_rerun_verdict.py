#!/usr/bin/env python3
"""Offline-only terminal verdict for a repeated physical no-HV MapCapture run.

The tool reads saved campaign evidence only.  It never opens COM/USB/ST-Link/
sigrok, flashes firmware, or emits MCU commands.  A PASS proves that the
attested physical evidence bundle is internally consistent with the existing
no-HV terminal UART contract.  It never authorizes Stage A or a DC-link test.
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
MAX_RAW_VBUS_HARD = 200
MIN_VBUS_MV = 0
MAX_VBUS_MV_EXCLUSIVE = 1000
MAX_ABS_SHUNT_MA = 10_000
# Must mirror src/adc.c adc_bipolar_sample_is_usable() for I1/I2.
ADC_RAW_SAT_LOW = 1
ADC_RAW_SAT_HIGH = 4094
APPROVED_PROFILE_ID = 1398361684

SUMMARY_NAME = "summary.json"
UART_LOG_NAME = "uart.log"
METADATA_NAME = "metadata.json"
ATTESTATION_NAME = "physical_run_attestation.json"
OUTPUT_NAME = "rerun_terminal_verdict.json"
ATTESTATION_SCHEMA = "oew-physical-nohv-attestation-v1"

STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)"
    r":detail=(?P<detail>-?\d+):raw_vbus=(?P<raw_vbus>\d+)"
    r":vbus_mv=(?P<vbus_mv>-?\d+):i1_ma=(?P<i1_ma>-?\d+)"
    r":i2_ma=(?P<i2_ma>-?\d+):adc_status=(?P<adc_status>-?\d+)"
    r":sector=(?P<sector>\d+):window=(?P<window>\d+)"
)
ADC_RAW_RE = re.compile(
    r"@ADC:I1=(?P<i1>\d+):I2=(?P<i2>\d+):Ires=(?P<ires>\d+):VBUS=(?P<raw_vbus>\d+)"
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
STATUS_FIELDS = (
    "state", "term", "cap", "frames", "dropped", "periods", "avail", "detail",
    "raw_vbus", "vbus_mv", "i1_ma", "i2_ma", "adc_status", "sector", "window",
)
RAW_ADC_FIELDS = ("i1", "i2", "ires", "raw_vbus")


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


def median(values: list[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def bipolar_adc_sample_is_usable(raw: int) -> bool:
    """Match firmware adc_bipolar_sample_is_usable(raw) exactly."""
    return ADC_RAW_SAT_LOW < raw < ADC_RAW_SAT_HIGH


def parse_statuses(text: str) -> list[dict[str, int]]:
    return [{key: int(value) for key, value in match.groupdict().items()} for match in STATUS_RE.finditer(text)]


def parse_adc_raws(text: str) -> list[dict[str, int]]:
    return [{key: int(value) for key, value in match.groupdict().items()} for match in ADC_RAW_RE.finditer(text)]


def status_projection(status: Any) -> Optional[dict[str, int]]:
    if not isinstance(status, dict):
        return None
    try:
        return {field: int(status[field]) for field in STATUS_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None


def raw_projection(raw: Any) -> Optional[dict[str, int]]:
    if not isinstance(raw, dict):
        return None
    try:
        return {field: int(raw[field]) for field in RAW_ADC_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None


def is_accepted_status(status: Any) -> bool:
    projected = status_projection(status)
    if projected is None:
        return False
    return (
        projected["state"] == EXPECTED_STATE
        and projected["term"] == EXPECTED_TERM
        and projected["detail"] == EXPECTED_DETAIL
        and projected["adc_status"] == EXPECTED_ADC_STATUS
        and projected["raw_vbus"] <= MAX_RAW_VBUS
        and MIN_VBUS_MV <= projected["vbus_mv"] < MAX_VBUS_MV_EXCLUSIVE
        and abs(projected["i1_ma"]) <= MAX_ABS_SHUNT_MA
        and abs(projected["i2_ma"]) <= MAX_ABS_SHUNT_MA
        and projected["frames"] == 0
        and projected["dropped"] == 0
        and projected["avail"] == 0
    )


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


def resolve_inside_campaign(campaign: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value:
        return None
    try:
        resolved = Path(value).resolve()
        resolved.relative_to(campaign)
    except (OSError, ValueError):
        return None
    return resolved


def expected_sample_count(metadata: Any) -> Optional[int]:
    if not isinstance(metadata, dict):
        return None
    arguments = metadata.get("arguments")
    if not isinstance(arguments, dict):
        return None
    value = arguments.get("vbus_samples")
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 1 else None


def validate_attestation(
    campaign: Path,
    attestation: Any,
    summary_path: Path,
    uart_path: Path,
    metadata_path: Path,
    sigrok_csv_path: Optional[Path],
) -> tuple[bool, dict[str, Any]]:
    """Validate a human physical-observation attestation that binds raw files.

    This is a chain-of-custody statement, not cryptographic proof that a named
    person or device is genuine.  It prevents a text-only synthetic pair from
    self-declaring PHYSICAL merely by omitting simulation markers.
    """
    evidence = attestation.get("evidence") if isinstance(attestation, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}
    sigrok_evidence = evidence.get("sigrok_csv") if isinstance(evidence, dict) else None
    sigrok_evidence = sigrok_evidence if isinstance(sigrok_evidence, dict) else {}
    actual = {
        "schema": attestation.get("schema") if isinstance(attestation, dict) else None,
        "role": attestation.get("role") if isinstance(attestation, dict) else None,
        "operator": attestation.get("operator") if isinstance(attestation, dict) else None,
        "observed_at": attestation.get("observed_at") if isinstance(attestation, dict) else None,
        "statement": attestation.get("statement") if isinstance(attestation, dict) else None,
        "summary_sha256": evidence.get("summary_sha256"),
        "uart_log_sha256": evidence.get("uart_log_sha256"),
        "metadata_sha256": evidence.get("metadata_sha256"),
        "sigrok_csv_path": sigrok_evidence.get("path"),
        "sigrok_csv_sha256": sigrok_evidence.get("sha256"),
    }
    expected = {
        "schema": ATTESTATION_SCHEMA,
        "role": "bench-operator",
        "summary_sha256": sha256_file(summary_path),
        "uart_log_sha256": sha256_file(uart_path),
        "metadata_sha256": sha256_file(metadata_path),
        "sigrok_csv_path": str(sigrok_csv_path) if sigrok_csv_path else None,
        "sigrok_csv_sha256": sha256_file(sigrok_csv_path) if sigrok_csv_path else None,
    }
    result = (
        actual["schema"] == expected["schema"]
        and actual["role"] == expected["role"]
        and isinstance(actual["operator"], str) and bool(actual["operator"].strip())
        and isinstance(actual["observed_at"], str) and bool(actual["observed_at"].strip())
        and isinstance(actual["statement"], str) and bool(actual["statement"].strip())
        and actual["summary_sha256"] == expected["summary_sha256"]
        and actual["uart_log_sha256"] == expected["uart_log_sha256"]
        and actual["metadata_sha256"] == expected["metadata_sha256"]
        and actual["sigrok_csv_path"] == expected["sigrok_csv_path"]
        and actual["sigrok_csv_sha256"] == expected["sigrok_csv_sha256"]
    )
    return result, {"expected": expected, "actual": actual}


def build_verdict(campaign: Path) -> dict[str, Any]:
    """Validate saved evidence only and return a complete machine-readable report."""
    summary_path = campaign / SUMMARY_NAME
    uart_path = campaign / UART_LOG_NAME
    metadata_path = campaign / METADATA_NAME
    attestation_path = campaign / ATTESTATION_NAME
    checks: list[Check] = []

    summary, summary_error = loaded_json(summary_path) if summary_path.is_file() else (None, "файл отсутствует")
    metadata, metadata_error = loaded_json(metadata_path) if metadata_path.is_file() else (None, "файл отсутствует")
    attestation, attestation_error = loaded_json(attestation_path) if attestation_path.is_file() else (None, "файл отсутствует")
    try:
        uart_text = uart_path.read_text(encoding="utf-8")
        uart_error: Optional[str] = None
    except (OSError, UnicodeDecodeError) as exc:
        uart_text, uart_error = "", str(exc)

    check(checks, "RV-00-summary-json", summary is not None, "parseable JSON object", summary_error or "OK", "summary.json обязателен")
    check(checks, "RV-00-uart-log", uart_error is None and bool(uart_text), "non-empty UTF-8 UART log", uart_error or len(uart_text), "uart.log обязателен и не может быть пустым")
    check(checks, "RV-00-metadata-json", metadata is not None, "parseable JSON object", metadata_error or "OK", "metadata.json из capture automation обязателен")
    check(checks, "RV-00-physical-attestation", attestation is not None, "parseable physical_run_attestation.json", attestation_error or "OK", "ручная chain-of-custody attestation обязательна")

    verdict = summary.get("verdict") if isinstance(summary, dict) else None
    execution = summary.get("execution") if isinstance(summary, dict) else None
    execution = execution if isinstance(execution, dict) else {}
    metadata_execution = metadata.get("execution") if isinstance(metadata, dict) else None
    metadata_execution = metadata_execution if isinstance(metadata_execution, dict) else {}
    mode = execution.get("mode")
    metadata_mode = metadata_execution.get("mode")
    check(checks, "RV-01-physical-execution", mode == "PHYSICAL" and metadata_mode == "PHYSICAL", {"summary": "PHYSICAL", "metadata": "PHYSICAL"}, {"summary": mode, "metadata": metadata_mode}, "summary и metadata должны независимо declare PHYSICAL")

    metadata_profile = metadata.get("profile_id") if isinstance(metadata, dict) else None
    check(checks, "RV-01-approved-profile", metadata_profile == APPROVED_PROFILE_ID, APPROVED_PROFILE_ID, metadata_profile, "metadata должен привязывать capture к approved read-only profile")

    automation = verdict.get("automation") if isinstance(verdict, dict) else None
    scope = verdict.get("scope") if isinstance(verdict, dict) else None
    final = verdict.get("final") if isinstance(verdict, dict) else None
    check(checks, "RV-02-automation", automation == "PASS", "PASS", automation, "summary должен сохранить PASS существующего UART evaluator")
    check(checks, "RV-03-scope-final", (scope, final) in (("PENDING", "PENDING"), ("PASS", "PASS")), "PENDING/PENDING or PASS/PASS", {"scope": scope, "final": final}, "parser не оценивает scope, но отклоняет противоречивую физическую пару")

    evaluator_checks = verdict.get("checks") if isinstance(verdict, dict) else None
    evaluator_checks = evaluator_checks if isinstance(evaluator_checks, dict) else {}
    missing_or_false = [name for name in REQUIRED_EVALUATOR_CHECKS if evaluator_checks.get(name) is not True]
    check(checks, "RV-04-summary-evaluator-claims", not missing_or_false, "all required no-HV checks=true", missing_or_false, "summary claims сохраняются как проверяемая декларация, а не как единственное доказательство")

    raw_samples = parse_adc_raws(uart_text)
    sample_count = expected_sample_count(metadata)
    raw_vbus_values = [sample["raw_vbus"] for sample in raw_samples]
    raw_vbus_median = median(raw_vbus_values)
    raw_vbus_max = max(raw_vbus_values) if raw_vbus_values else None
    raw_checks = {
        "declared_samples": sample_count,
        "observed_samples": len(raw_samples),
        "raw_vbus_median": raw_vbus_median,
        "raw_vbus_max": raw_vbus_max,
        "i1_not_saturated": bool(raw_samples and all(bipolar_adc_sample_is_usable(sample["i1"]) for sample in raw_samples)),
        "i2_not_saturated": bool(raw_samples and all(bipolar_adc_sample_is_usable(sample["i2"]) for sample in raw_samples)),
    }
    recomputed_raw_pass = (
        sample_count is not None
        and len(raw_samples) == sample_count
        and raw_vbus_median is not None and raw_vbus_median <= MAX_RAW_VBUS
        and raw_vbus_max is not None and raw_vbus_max <= MAX_RAW_VBUS_HARD
        and raw_checks["i1_not_saturated"]
        and raw_checks["i2_not_saturated"]
    )
    check(checks, "RV-04b-uart-preflight-recomputed", recomputed_raw_pass, {"sample_count": "metadata.arguments.vbus_samples", "median_max": MAX_RAW_VBUS, "hard_max": MAX_RAW_VBUS_HARD, "i1_i2": "not saturated"}, raw_checks, "preflight raw ADC conditions пересчитаны непосредственно из uart.log")

    summary_raw_samples = verdict.get("preflight_adc") if isinstance(verdict, dict) else None
    summary_raw_projection = [raw_projection(item) for item in summary_raw_samples] if isinstance(summary_raw_samples, list) else None
    check(checks, "RV-04c-summary-uart-preflight-match", summary_raw_projection == raw_samples and None not in (summary_raw_projection or []), "identical ordered I1/I2/Ires/VBUS sample sequence", {"summary_samples": summary_raw_projection, "uart_samples": raw_samples}, "summary preflight rows должны совпадать с raw UART evidence")

    summary_status = verdict.get("status") if isinstance(verdict, dict) else None
    check(checks, "RV-05-summary-terminal", is_accepted_status(summary_status), "state=5 term=-12 detail=7 adc_status=7 with no-HV limits", status_projection(summary_status), "summary terminal status должен соответствовать immutable no-HV contract")

    sigrok_info = summary.get("sigrok") if isinstance(summary, dict) else None
    sigrok_info = sigrok_info if isinstance(sigrok_info, dict) else {}
    sigrok_csv_path = resolve_inside_campaign(campaign, sigrok_info.get("csv_path"))
    sigrok_size = sigrok_csv_path.stat().st_size if sigrok_csv_path and sigrok_csv_path.is_file() else None
    sigrok_rc = verdict.get("sigrok_capture_returncode_zero") if isinstance(verdict, dict) else None
    sigrok_csv = verdict.get("sigrok_csv_exists") if isinstance(verdict, dict) else None
    sigrok_valid = (
        sigrok_rc is True and sigrok_csv is True and sigrok_csv_path is not None
        and sigrok_csv_path.is_file() and sigrok_size is not None and sigrok_size > 0
        and sigrok_info.get("csv_exists") is True and sigrok_info.get("csv_size_bytes") == sigrok_size
    )
    check(checks, "RV-06-sigrok-artifact", sigrok_valid, "successful non-empty CSV inside campaign matching summary size", {"returncode_zero": sigrok_rc, "summary_csv_exists": sigrok_csv, "path": str(sigrok_csv_path) if sigrok_csv_path else None, "actual_size": sigrok_size, "summary_size": sigrok_info.get("csv_size_bytes")}, "parser проверяет presence/size binding; waveform scope acceptance остаётся human review")

    attestation_valid, attestation_detail = validate_attestation(campaign, attestation, summary_path, uart_path, metadata_path, sigrok_csv_path)
    check(checks, "RV-06b-human-attestation", attestation_valid, attestation_detail["expected"], attestation_detail["actual"], "attestation привязывает exact evidence hashes к именованному bench-operator; она не является криптографическим доказательством личности")

    arm_before_run, arm_run_actual = accepted_arm_before_run(uart_text)
    check(checks, "RV-07-uart-arm-before-run", arm_before_run, "accepted ARM before accepted RUN", arm_run_actual, "accepted ARM имеет cap>0/rc=0; accepted RUN имеет rc=0")

    statuses = parse_statuses(uart_text)
    terminal_statuses = [status for status in statuses if status["state"] in (3, 4, 5)]
    check(checks, "RV-08-single-terminal-status", len(terminal_statuses) == 1, "exactly one terminal STATUS (state 3/4/5)", terminal_statuses, "ранний terminal/fault STATUS не может быть скрыт поздней правильной строкой")
    uart_terminal = terminal_statuses[0] if len(terminal_statuses) == 1 else None
    check(checks, "RV-08-uart-terminal-contract", is_accepted_status(uart_terminal), "state=5 term=-12 detail=7 adc_status=7 with no-HV limits", status_projection(uart_terminal), "единственный terminal STATUS UART должен быть accepted terminal evidence")
    check(checks, "RV-08-summary-uart-full-match", status_projection(summary_status) == status_projection(uart_terminal) and status_projection(summary_status) is not None, "identical complete STATUS projections", {"summary": status_projection(summary_status), "uart": status_projection(uart_terminal)}, "сравниваются все parsed STATUS fields, включая cap/periods/sector/window")

    drains = [int(match.group("records")) for match in DRAIN_RE.finditer(uart_text)]
    check(checks, "RV-09-uart-drain-records", bool(drains) and drains[-1] == 0 and "@MC:REC:" not in uart_text, "last drain=0 and no @MC:REC", {"drains": drains, "record_marker": "@MC:REC:" in uart_text}, "record evidence противоречит no-HV no-record terminal contract")
    check(checks, "RV-10-no-simulation-markers", "mode=SIMULATED" not in uart_text and "@SIM:" not in uart_text, "no simulation markers", {"mode_simulated": "mode=SIMULATED" in uart_text, "sim_marker": "@SIM:" in uart_text}, "absence of markers is only a supplementary negative check; RV-06b is required provenance")

    terminal_verdict = "PASS" if all(item.result for item in checks) else "FAIL"
    return {
        "schema_version": 2,
        "validated_utc": utc_now(),
        "campaign": str(campaign),
        "inputs": {
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
            "uart_log": {"path": str(uart_path), "sha256": sha256_file(uart_path)},
            "metadata": {"path": str(metadata_path), "sha256": sha256_file(metadata_path)},
            "physical_attestation": {"path": str(attestation_path), "sha256": sha256_file(attestation_path)},
            "sigrok_csv": {"path": str(sigrok_csv_path) if sigrok_csv_path else None, "sha256": sha256_file(sigrok_csv_path) if sigrok_csv_path else None},
        },
        "checks": [asdict(item) for item in checks],
        "summary_terminal_status": status_projection(summary_status),
        "uart_terminal_status": status_projection(uart_terminal),
        "terminal_status_count": len(terminal_statuses),
        "terminal_verdict": terminal_verdict,
        "stage_a_60v": "BLOCKED",
        "safety_boundary": "This offline terminal-evidence verdict never authorizes Stage A, DC-link, FOC, V/f, autotune, map build, or a physical test.",
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline fail-closed verdict for repeated no-HV summary.json + uart.log.")
    parser.add_argument("--campaign", type=Path, required=True, help="Каталог кампании с summary.json, uart.log, metadata.json и physical_run_attestation.json")
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
