#!/usr/bin/env python3
"""Fail-closed CLI pre-flight validator for physical no-HV HIL Test №2.

The tool validates G0 evidence and can make a limited set of UART observations.
It never flashes firmware and never sends mcarm, mapcap run/drain/build, f, FOC,
V/f or autotune commands.  PREFLIGHT=PASS is only permission to launch the
separate approved no-HV automation command; it is not a Test №2 PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


TOOLS_DIR = Path(__file__).resolve().parent
CAPTURE_PATH = TOOLS_DIR / "bench_test2_capture.py"
_CAPTURE_SPEC = importlib.util.spec_from_file_location("_test2_capture_contract", CAPTURE_PATH)
if _CAPTURE_SPEC is None or _CAPTURE_SPEC.loader is None:
    raise RuntimeError("Cannot load accepted Test №2 UART contract.")
_CAPTURE = importlib.util.module_from_spec(_CAPTURE_SPEC)
sys.modules[_CAPTURE_SPEC.name] = _CAPTURE
_CAPTURE_SPEC.loader.exec_module(_CAPTURE)

G0_GATE = "HIL_TEST2_G0"
REQUIRED_G0_FILES = (
    "g0_approval.json",
    "diagnostic_build_manifest.json",
    "diagnostic_build.log",
)
G0_SUMMARY_CANDIDATES = ("g0_check_summary.json", "g0/g0_check_summary.json")
SAFE_UART_COMMANDS = ("sysinfo", "p?", "pdump", "a", "c", "enc", "mapcap status")
FORBIDDEN_UART_COMMANDS = frozenset(("mcarm", "mapcap run", "mapcap drain", "mapcap build", "f"))
REQUIRED_DEFINES: Mapping[str, str] = {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
    "OEW_MAP_SYNTHETIC_PROFILE": "1",
    "OEW_HOST_TEST": "1",
}
PWM_OFF_RE = re.compile(r"(?:\bMOE\s*[=:]\s*0\b|\bdefault_deny\s*[=:]\s*1\b)", re.IGNORECASE)
ERROR_RE = re.compile(r"(?:@(?:SIM|CLI):ERR|unknown[_ ]command|error\s*:)", re.IGNORECASE)


@dataclass
class Check:
    check_id: str
    kind: str
    expected: Any
    actual: Any
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "kind": self.kind,
            "result": "PASS" if self.passed else "FAIL",
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class Recorder:
    checks: list[Check] = field(default_factory=list)

    def add(self, check_id: str, kind: str, expected: Any, actual: Any,
            passed: bool, detail: str) -> bool:
        self.checks.append(Check(check_id, kind, expected, actual, passed, detail))
        return passed

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_inside_git_worktree(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return True
    return False


def load_json(path: Path, recorder: Recorder, check_id: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        recorder.add(check_id, "OFFLINE", "valid JSON object", type(exc).__name__, False,
                     "JSON evidence is unreadable or malformed.")
        return None
    recorder.add(check_id, "OFFLINE", "JSON object", type(parsed).__name__, isinstance(parsed, dict),
                 "Evidence JSON root must be an object.")
    return parsed if isinstance(parsed, dict) else None


def resolve_g0_summary(campaign: Path, recorder: Recorder) -> Optional[Path]:
    found = [relative for relative in G0_SUMMARY_CANDIDATES if (campaign / relative).is_file()]
    invalid = [relative for relative in G0_SUMMARY_CANDIDATES
               if (campaign / relative).exists() and not (campaign / relative).is_file()]
    matched = len(found) == 1 and not invalid
    recorder.add("g0.summary.path", "OFFLINE", "exactly one regular summary", {
        "present": found, "invalid": invalid,
    }, matched, "Exactly one G0 summary location is accepted to prevent evidence ambiguity.")
    return campaign / found[0] if len(found) == 1 and not invalid else None


def validate_g0(campaign: Path, recorder: Recorder) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for relative in REQUIRED_G0_FILES:
        path = campaign / relative
        valid = path.is_file() and not path.is_symlink()
        recorder.add("g0.file." + relative, "OFFLINE", "regular file", str(path), valid,
                     "Required G0 input must be a non-symlink regular file.")
        if valid:
            evidence[relative] = sha256_file(path)
    summary_path = resolve_g0_summary(campaign, recorder)
    if summary_path is not None:
        evidence[summary_path.relative_to(campaign).as_posix()] = sha256_file(summary_path)
    else:
        return evidence

    approval = load_json(campaign / "g0_approval.json", recorder, "g0.approval.json")
    manifest = load_json(campaign / "diagnostic_build_manifest.json", recorder, "g0.manifest.json")
    summary = load_json(summary_path, recorder, "g0.summary.json")
    if summary is not None:
        recorder.add("g0.summary.gate", "OFFLINE", G0_GATE, summary.get("gate"),
                     summary.get("gate") == G0_GATE, "G0 summary must identify the Test №2 gate.")
        recorder.add("g0.summary.verdict", "OFFLINE", "PASS", summary.get("verdict"),
                     summary.get("verdict") == "PASS", "A physical pre-flight cannot proceed with G0 FAIL.")
    if approval is not None:
        recorder.add("g0.approval.decision", "OFFLINE", "APPROVED", approval.get("decision"),
                     approval.get("decision") == "APPROVED", "Safety-owner approval must be explicit.")
        scope = approval.get("scope") if isinstance(approval.get("scope"), dict) else {}
        expected_scope = {
            "target": "physical-nohv-diagnostic",
            "test": "MAPCAP_TEST2",
            "allows_oew_host_test": True,
            "allows_physical_nohv_execution": True,
            "diagnostic_only": True,
            "forbids_stage_a": True,
            "forbids_production": True,
            "forbids_dc_link": True,
            "forbids_foc": True,
            "forbids_vf": True,
            "forbids_autotune": True,
        }
        for key, expected in expected_scope.items():
            recorder.add("g0.approval.scope." + key, "OFFLINE", expected, scope.get(key),
                         scope.get(key) == expected,
                         "Approval scope must remain strictly physical no-HV diagnostic only.")
    if manifest is not None:
        recorder.add("g0.manifest.target", "OFFLINE", "physical-nohv-diagnostic", manifest.get("target"),
                     manifest.get("target") == "physical-nohv-diagnostic",
                     "Manifest target must remain no-HV diagnostic.")
        recorder.add("g0.manifest.test", "OFFLINE", "MAPCAP_TEST2", manifest.get("test"),
                     manifest.get("test") == "MAPCAP_TEST2", "Manifest test must identify Test №2.")
        recorder.add("g0.manifest.defines_complete", "OFFLINE", True, manifest.get("defines_complete"),
                     manifest.get("defines_complete") is True,
                     "Manifest must declare a complete active defines map.")
        defines = manifest.get("defines") if isinstance(manifest.get("defines"), dict) else {}
        for key, expected in REQUIRED_DEFINES.items():
            recorder.add("g0.manifest.define." + key, "OFFLINE", expected, defines.get(key),
                         defines.get(key) == expected,
                         "Required diagnostic define must match the approved build contract.")
    return evidence


def validate_operator_confirmations(args: argparse.Namespace, recorder: Recorder) -> None:
    required = {
        "dc_link_disconnected": args.confirm_dc_link_disconnected,
        "pc4_zero": args.confirm_pc4_zero,
        "sd_high": args.confirm_sd_high,
        "sigrok_connected": args.confirm_sigrok_connected,
    }
    for name, value in required.items():
        recorder.add("operator." + name, "OPERATOR", True, bool(value), bool(value),
                     "This flag is an operator attestation and does not replace DMM/scope evidence.")


def validate_campaign_boundary(campaign: Path, recorder: Recorder, was_symlink: bool) -> bool:
    valid = campaign.exists() and campaign.is_dir() and not was_symlink
    recorder.add("campaign.directory", "OFFLINE", "existing non-symlink directory", str(campaign), valid,
                 "Campaign root must exist before pre-flight results are recorded.")
    outside_git = valid and not is_inside_git_worktree(campaign)
    recorder.add("campaign.outside_git", "OFFLINE", True, outside_git, outside_git,
                 "Physical evidence campaign must be outside any Git working tree.")
    return valid and outside_git


def safe_response(response: str) -> bool:
    return bool(response.strip()) and ERROR_RE.search(response) is None


def validate_uart_responses(responses: Mapping[str, Any], recorder: Recorder,
                            kind: str) -> list[str]:
    sequence = list(responses.keys())
    recorder.add("uart.command_sequence", kind, list(SAFE_UART_COMMANDS), sequence,
                 sequence == list(SAFE_UART_COMMANDS),
                 "Only the exact observation allow-list is accepted; no control command may appear.")
    for command in sequence:
        forbidden = command in FORBIDDEN_UART_COMMANDS or command.startswith("mcarm=")
        recorder.add("uart.forbidden." + command, kind, False, forbidden, not forbidden,
                     "Pre-flight validator must never send or accept a control/energising command.")
    if sequence != list(SAFE_UART_COMMANDS):
        return sequence
    for command in SAFE_UART_COMMANDS:
        response = responses[command]
        response_ok = (safe_response(response) if isinstance(response, str)
                       else all(safe_response(item) for item in response))
        recorder.add("uart.response." + command, kind, "nonempty response without CLI error", response,
                     response_ok, "UART observation must receive a valid response.")
    pwm = (responses["p?"], responses["pdump"])
    for command, response in zip(("p?", "pdump"), pwm):
        recorder.add("uart.pwm_off." + command, kind, "MOE=0 or default_deny=1", response,
                     PWM_OFF_RE.search(response) is not None,
                     "PWM must be disabled before Test №2 automation is permitted.")
    adc_texts = responses["a"] if isinstance(responses["a"], list) else [responses["a"]]
    adc_raws = _CAPTURE.parse_adc_raws(adc_texts)
    adc_values = [raw["raw_vbus"] for raw in adc_raws] if adc_raws else []
    adc_median = _CAPTURE.median(adc_values)
    adc_max = max(adc_values) if adc_values else None
    adc_ok = bool(
        adc_raws is not None
        and adc_median is not None and adc_median <= 9
        and adc_max is not None and adc_max <= _CAPTURE.NOHV_RAW_VBUS_HARD_LIMIT
        and all(raw["i1"] < 32767 and raw["i2"] < 32767 for raw in adc_raws)
    )
    recorder.add("uart.adc", kind, "median(raw_vbus)<=9, max<=200, I1/I2 non-saturated",
                 {"samples": adc_values, "median": adc_median, "max": adc_max}, adc_ok,
                 "ADC pre-flight must prove no-HV raw VBUS (statistical) and non-saturated current channels.")
    recorder.add("uart.calibration", kind, "valid offsets/no CAL:FAIL", responses["c"],
                 _CAPTURE.has_calibration_ok(responses["c"]),
                 "Calibration must produce all offsets without a failure marker.")
    recorder.add("uart.encoder", kind, "err=0", responses["enc"],
                 _CAPTURE.has_encoder_ok(responses["enc"]),
                 "Encoder is infrastructure readiness and must report err=0 unless separately waived.")
    status = _CAPTURE.parse_status(responses["mapcap status"])
    status_ok = bool(status and status["state"] == _CAPTURE.MAP_CAPTURE_IDLE and
                     status["term"] == 0 and status["frames"] == 0 and
                     status["dropped"] == 0 and status["avail"] == 0)
    recorder.add("uart.mapcap_idle", kind, "IDLE term=0 frames=dropped=avail=0", status,
                 status_ok, "MapCapture must be empty and IDLE; stale state is a hard NO-GO.")
    return sequence


def load_transcript(path: Path, recorder: Recorder) -> Optional[dict[str, Any]]:
    payload = load_json(path, recorder, "transcript.json")
    if payload is None:
        return None
    responses = payload.get("responses") if isinstance(payload.get("responses"), dict) else payload

    def is_valid_response(value: Any) -> bool:
        # `a` may be a list of N observation responses (statistical no-HV gate);
        # every other safe command has exactly one string response.
        return isinstance(value, str) or (
            isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)
        )

    valid = isinstance(responses, dict) and all(isinstance(key, str) and is_valid_response(value)
                                                for key, value in responses.items())
    recorder.add("transcript.responses", "OFFLINE", "string command-to-response mapping",
                 type(responses).__name__, valid,
                 "Offline transcript must be a JSON object of string responses.")
    return dict(responses) if valid else None


def observe_uart(port: str, baud: int, log_path: Path,
                 vbus_samples: int) -> tuple[dict[str, Any], list[str]]:
    transport = _CAPTURE.SerialTransport(port, baud, log_path)
    responses: dict[str, Any] = {}
    try:
        transport.open()
        for command in SAFE_UART_COMMANDS:
            if command == "a":
                responses[command] = [transport.command(command).response
                                      for _ in range(vbus_samples)]
            else:
                responses[command] = transport.command(command).response
        return responses, list(transport.command_sequence)
    finally:
        transport.close()


def scan_sigrok(cli: str, output_path: Path) -> tuple[bool, str, list[str]]:
    command = [cli, "--scan"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        combined = (completed.stdout or "") + (completed.stderr or "")
        output_path.write_text(combined, encoding="utf-8", newline="\n")
        return completed.returncode == 0 and bool(combined.strip()), combined, command
    except (OSError, subprocess.SubprocessError) as exc:
        output_path.write_text(str(exc) + "\n", encoding="utf-8", newline="\n")
        return False, str(exc), command


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(path)


def run_preflight(args: argparse.Namespace) -> tuple[dict[str, Any], Optional[Path]]:
    supplied_campaign = args.campaign.expanduser()
    supplied_campaign_is_symlink = supplied_campaign.is_symlink()
    campaign = supplied_campaign.resolve()
    recorder = Recorder()
    evidence_hashes: dict[str, str] = {}
    campaign_valid = validate_campaign_boundary(campaign, recorder, supplied_campaign_is_symlink)
    if campaign_valid:
        validate_operator_confirmations(args, recorder)
        evidence_hashes = validate_g0(campaign, recorder)
    else:
        recorder.add("operator.confirmations", "OPERATOR", "campaign boundary PASS", None, False,
                     "Operator confirmations are not accepted until campaign boundary is valid.")

    mode = "OFFLINE"
    command_sequence: list[str] = []
    responses: dict[str, str] = {}
    if args.uart_transcript is not None:
        transcript = load_transcript(args.uart_transcript.resolve(), recorder)
        if transcript is not None:
            command_sequence = validate_uart_responses(transcript, recorder, "OFFLINE_UART_TRANSCRIPT")
            responses = transcript
        recorder.add("uart.physical_identity", "OFFLINE", "real --port observation", "transcript only", False,
                     "A transcript reproduces parsing but cannot prove the target currently connected to ПК-3.")
    elif args.port:
        all_confirmed = all((args.confirm_dc_link_disconnected, args.confirm_pc4_zero,
                             args.confirm_sd_high, args.confirm_sigrok_connected))
        preconditions = campaign_valid and recorder.passed and all_confirmed
        recorder.add("uart.real_mode_preconditions", "OPERATOR", True, preconditions, preconditions,
                     "Real UART access is blocked unless campaign, G0 and all no-HV attestations already pass.")
        if preconditions:
            mode = "UART"
            log_path = campaign / "preflight_uart.log"
            try:
                responses, command_sequence = observe_uart(args.port, args.baud, log_path, args.vbus_samples)
                validate_uart_responses(responses, recorder, "UART")
                evidence_hashes["preflight_uart.log"] = sha256_file(log_path)
            except Exception as exc:  # Transport errors must be retained as evidence, never raised into a false PASS.
                recorder.add("uart.transport", "UART", "successful observation allow-list", type(exc).__name__, False,
                             str(exc))
    else:
        recorder.add("uart.physical_identity", "OFFLINE", "--port <MCU_UART_VCP>", None, False,
                     "A complete pre-flight requires real UART observations; offline evidence alone is not GO.")

    if args.scan_sigrok:
        all_confirmed = all((args.confirm_dc_link_disconnected, args.confirm_pc4_zero,
                             args.confirm_sd_high, args.confirm_sigrok_connected))
        preconditions = (not args.offline) and campaign_valid and recorder.passed and all_confirmed
        recorder.add("sigrok.scan_preconditions", "OPERATOR", True, preconditions, preconditions,
                     "USB sigrok scan is blocked in offline mode or before G0/no-HV preconditions pass.")
        if preconditions:
            try:
                cli = _CAPTURE.find_sigrok_cli(args.sigrok_cli)
                ok, output, command = scan_sigrok(cli, campaign / "preflight_sigrok_scan.log")
                recorder.add("sigrok.scan", "SIGROK", "exit=0 and nonempty scan output", output, ok,
                             "This is connectivity only, not an actual Test №2 capture.")
                evidence_hashes["preflight_sigrok_scan.log"] = sha256_file(campaign / "preflight_sigrok_scan.log")
            except Exception as exc:
                recorder.add("sigrok.scan", "SIGROK", "available sigrok-cli/device", type(exc).__name__, False,
                             str(exc))
    else:
        recorder.add("sigrok.scan", "SIGROK", "--scan-sigrok completed", None, False,
                     "A complete physical pre-flight requires an explicit sigrok connectivity scan.")

    summary = {
        "schema": "test2-preflight-v1",
        "generated_at": utc_now(),
        "campaign": str(campaign),
        "mode": mode,
        "verdict": "PASS" if recorder.passed else "FAIL",
        "operator_confirmations": {
            "dc_link_disconnected": bool(args.confirm_dc_link_disconnected),
            "pc4_zero": bool(args.confirm_pc4_zero),
            "sd_high": bool(args.confirm_sd_high),
            "sigrok_connected": bool(args.confirm_sigrok_connected),
        },
        "uart": {"port": args.port, "baud": args.baud if args.port else None,
                 "command_sequence": command_sequence, "responses": responses},
        "evidence_sha256": dict(sorted(evidence_hashes.items())),
        "checks": [check.as_dict() for check in recorder.checks],
        "safety_boundary": "No mcarm/run/drain/build/f/flash/FOC/VF/autotune was issued by this tool.",
    }
    if not campaign_valid:
        return summary, None
    summary_path = campaign / "preflight_summary.json"
    write_json_atomic(summary_path, summary)
    return summary, summary_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--port", help="Actual MCU UART VCP; enables observation allow-list only.")
    mode.add_argument("--uart-transcript", type=Path,
                      help="Offline JSON mapping of safe UART commands to responses; never grants physical GO.")
    parser.add_argument("--offline", action="store_true", help="Document explicit offline intent; no physical port is opened.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--vbus-samples", type=int, default=_CAPTURE.DEFAULT_VBUS_SAMPLES,
                        help="Число сэмплов `a` для статистического no-HV гейта VBUS (default: %(default)s)")
    parser.add_argument("--scan-sigrok", action="store_true")
    parser.add_argument("--sigrok-cli")
    parser.add_argument("--confirm-dc-link-disconnected", action="store_true")
    parser.add_argument("--confirm-pc4-zero", action="store_true")
    parser.add_argument("--confirm-sd-high", action="store_true")
    parser.add_argument("--confirm-sigrok-connected", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offline and args.port:
        print("PREFLIGHT=FAIL")
        print("error=--offline cannot be combined with --port", file=sys.stderr)
        return 2
    if args.vbus_samples < 1:
        print("PREFLIGHT=FAIL")
        print("error=--vbus-samples must be >= 1", file=sys.stderr)
        return 2
    try:
        summary, summary_path = run_preflight(args)
    except (OSError, ValueError) as exc:
        print("PREFLIGHT=FAIL")
        print("error=" + str(exc), file=sys.stderr)
        return 2
    print("PREFLIGHT=" + summary["verdict"])
    print("summary=" + (str(summary_path) if summary_path is not None else "UNWRITTEN_INVALID_CAMPAIGN"))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
