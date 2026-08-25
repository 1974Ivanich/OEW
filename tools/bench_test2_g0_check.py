#!/usr/bin/env python3
"""Fail-closed offline validator for physical no-HV HIL Test №2 Hard Gate G0.

The tool reads evidence files from a campaign directory only.  It never opens a
serial device, calls sigrok, invokes a programmer, or communicates with any
physical equipment.  A PASS means the supplied *offline* approval, source,
firmware and build evidence are internally consistent.  It does not prove the
identity of a human approver or the image currently flashed on a target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


GATE = "HIL_TEST2_G0"
APPROVAL_SCHEMA = "h1-g0-approval-v1"
MANIFEST_SCHEMA = "h1-g0-diagnostic-manifest-v1"
TARGET = "physical-nohv-diagnostic"
TEST = "MAPCAP_TEST2"
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FIRMWARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_DEFINES = {
    "OEW_MAP_CAPTURE": "1",
    "OEW_MAP_L3": "1",
    "PWM_OEW_BOARD_REVISION": "7",
    "OEW_MAP_SYNTHETIC_PROFILE": "1",
    "OEW_HOST_TEST": "1",
    "OEW_HS1_COMMISSIONING_RELEASE": "1",
}

REQUIRED_SCOPE = {
    "target": TARGET,
    "test": TEST,
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

# Any such define makes the physical no-HV diagnostic scope ambiguous.  A new
# flag may be considered only through a new approved G0 contract/package.
# OEW_HS1_COMMISSIONING_RELEASE is REQUIRED (not forbidden): it only enables the
# real hardware-interlock check in PWM_HardwareInterlockHealthy (SD high + break
# configured + no BIF) and does not open DC-link / control admission / FOC/VF.
FORBIDDEN_DEFINES = {
    "OEW_ALLOW_DC_LINK",
    "OEW_ALLOW_CONTROL_ADMISSION",
    "OEW_STAGE_A",
    "OEW_FOC_ENABLE",
    "OEW_VF_ENABLE",
    "OEW_AUTOTUNE_ENABLE",
}

INPUT_NAMES = {
    "approval": "g0_approval.json",
    "manifest": "diagnostic_build_manifest.json",
    "build_log": "diagnostic_build.log",
}


@dataclass
class Check:
    """One immutable, serializable G0 check result."""

    check_id: str
    result: str
    expected: Any
    actual: Any
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "result": self.result,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class Recorder:
    checks: list[Check] = field(default_factory=list)

    def add(self, check_id: str, expected: Any, actual: Any, passed: bool,
            detail: str) -> bool:
        self.checks.append(Check(
            check_id=check_id,
            result="PASS" if passed else "FAIL",
            expected=expected,
            actual=actual,
            detail=detail,
        ))
        return passed

    @property
    def passed(self) -> bool:
        return all(check.result == "PASS" for check in self.checks)


def _value_at(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normal_sha(value: Any, pattern: re.Pattern[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    return lowered if pattern.fullmatch(lowered) else None


def _safe_campaign_file(campaign: Path, relative_path: Any) -> Optional[Path]:
    """Resolve one artifact without allowing an absolute or escaping path."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return None
    root = campaign.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _read_json(campaign: Path, filename: str, recorder: Recorder,
               check_id: str) -> Optional[dict[str, Any]]:
    path = _safe_campaign_file(campaign, filename)
    exists = path is not None and path.is_file()
    recorder.add(check_id + ".present", filename,
                 str(path) if path is not None else filename, exists,
                 "JSON evidence must be a regular file inside the campaign.")
    if not exists or path is None:
        return None
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        recorder.add(check_id + ".json", "valid JSON object", type(exc).__name__,
                     False, "JSON evidence is unreadable or malformed.")
        return None
    is_object = isinstance(decoded, dict)
    recorder.add(check_id + ".json", "JSON object", type(decoded).__name__,
                 is_object, "Evidence root must be a JSON object.")
    return decoded if is_object else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_required_metadata(approval: Mapping[str, Any], recorder: Recorder) -> None:
    recorder.add("approval.schema", APPROVAL_SCHEMA, approval.get("schema"),
                 approval.get("schema") == APPROVAL_SCHEMA,
                 "Approval schema must be explicit and versioned.")
    recorder.add("approval.gate", GATE, approval.get("gate"),
                 approval.get("gate") == GATE,
                 "Approval must apply to this Hard Gate.")
    recorder.add("approval.decision", "APPROVED", approval.get("decision"),
                 approval.get("decision") == "APPROVED",
                 "Only an explicit APPROVED decision permits the pre-flash gate.")
    recorder.add("approval.role", "safety-owner", approval.get("role"),
                 approval.get("role") == "safety-owner",
                 "Approval must be issued in the safety-owner role.")
    for field_name in ("approval_id", "approver", "approved_at"):
        value = approval.get(field_name)
        recorder.add("approval." + field_name, "non-empty string", value,
                     _text(value), "Traceable human approval metadata is required.")


def _check_scope(approval: Mapping[str, Any], recorder: Recorder) -> None:
    scope = approval.get("scope")
    is_object = isinstance(scope, Mapping)
    recorder.add("approval.scope.object", "JSON object", type(scope).__name__,
                 is_object, "Scope must be machine-readable.")
    if not is_object:
        return
    for field_name, expected in REQUIRED_SCOPE.items():
        actual = scope.get(field_name)
        recorder.add("approval.scope." + field_name, expected, actual,
                     actual == expected,
                     "Physical no-HV execution scope must be exact and fail-closed.")


def _check_source_and_firmware_binding(
        approval: Mapping[str, Any], manifest: Mapping[str, Any],
        recorder: Recorder) -> tuple[Optional[str], Optional[str], Optional[Mapping[str, Any]]]:
    approval_source = _normal_sha(approval.get("source_sha"), SOURCE_SHA_RE)
    manifest_source = _normal_sha(manifest.get("source_sha"), SOURCE_SHA_RE)
    recorder.add("approval.source_sha.format", "40 lowercase hex", approval.get("source_sha"),
                 approval_source is not None, "Approval must bind to an immutable source SHA.")
    recorder.add("manifest.source_sha.format", "40 lowercase hex", manifest.get("source_sha"),
                 manifest_source is not None, "Manifest must identify its exact source SHA.")
    recorder.add("identity.source_sha", "approval source SHA == manifest source SHA",
                 {"approval": approval_source, "manifest": manifest_source},
                 approval_source is not None and approval_source == manifest_source,
                 "G0 cannot be transferred between source revisions.")

    firmware = manifest.get("firmware")
    recorder.add("manifest.firmware.object", "JSON object", type(firmware).__name__,
                 isinstance(firmware, Mapping),
                 "Manifest must identify one firmware artifact.")
    if not isinstance(firmware, Mapping):
        return approval_source, None, None

    approval_firmware = _normal_sha(approval.get("firmware_sha256"), FIRMWARE_SHA256_RE)
    manifest_firmware = _normal_sha(firmware.get("sha256"), FIRMWARE_SHA256_RE)
    recorder.add("approval.firmware_sha256.format", "64 lowercase hex",
                 approval.get("firmware_sha256"), approval_firmware is not None,
                 "Approval must bind to the exact firmware bytes.")
    recorder.add("manifest.firmware_sha256.format", "64 lowercase hex",
                 firmware.get("sha256"), manifest_firmware is not None,
                 "Manifest must provide a SHA-256 for the binary.")
    recorder.add("identity.firmware_sha256", "approval firmware SHA-256 == manifest firmware SHA-256",
                 {"approval": approval_firmware, "manifest": manifest_firmware},
                 approval_firmware is not None and approval_firmware == manifest_firmware,
                 "G0 cannot be transferred between firmware binaries.")
    return approval_source, manifest_firmware, firmware


def _check_manifest_contract(manifest: Mapping[str, Any], approval: Mapping[str, Any],
                             recorder: Recorder) -> None:
    recorder.add("manifest.schema", MANIFEST_SCHEMA, manifest.get("schema"),
                 manifest.get("schema") == MANIFEST_SCHEMA,
                 "Manifest schema must be explicit and versioned.")
    recorder.add("manifest.gate", GATE, manifest.get("gate"),
                 manifest.get("gate") == GATE,
                 "Manifest must claim the same G0 contract.")
    recorder.add("manifest.target", TARGET, manifest.get("target"),
                 manifest.get("target") == TARGET,
                 "Manifest target must be physical no-HV diagnostic only.")
    recorder.add("manifest.test", TEST, manifest.get("test"),
                 manifest.get("test") == TEST,
                 "Manifest must be limited to MapCapture Test №2.")
    recorder.add("manifest.defines_complete", True, manifest.get("defines_complete"),
                 manifest.get("defines_complete") is True,
                 "The manifest must assert a complete list of preprocessor defines.")

    defines = manifest.get("defines")
    recorder.add("manifest.defines.object", "JSON object", type(defines).__name__,
                 isinstance(defines, Mapping),
                 "Defines must be represented as a name-to-value object.")
    if not isinstance(defines, Mapping):
        return
    for name, expected in REQUIRED_DEFINES.items():
        actual = defines.get(name)
        recorder.add("manifest.define." + name, expected, actual, actual == expected,
                     "Required diagnostic define must match exactly.")
    forbidden_present = sorted(name for name in FORBIDDEN_DEFINES if name in defines)
    recorder.add("manifest.forbidden_defines", "none", forbidden_present,
                 not forbidden_present,
                 "Stage-A, admission or energise defines are outside the no-HV G0 scope.")

    extra_actual = sorted(name for name in defines if name not in REQUIRED_DEFINES)
    approved_extra = approval.get("approved_extra_defines", [])
    valid_extra_list = isinstance(approved_extra, list) and all(
        isinstance(item, str) for item in approved_extra)
    normalized_approved = sorted(approved_extra) if valid_extra_list else None
    recorder.add("approval.approved_extra_defines", "list of define names", approved_extra,
                 valid_extra_list,
                 "Extra defines require explicit enumeration in the approval.")
    recorder.add("identity.extra_defines", normalized_approved, extra_actual,
                 normalized_approved is not None and normalized_approved == extra_actual,
                 "The approval must bind to every additional preprocessor define.")


def _check_binary(campaign: Path, firmware: Optional[Mapping[str, Any]],
                  expected_hash: Optional[str], recorder: Recorder) -> None:
    if firmware is None:
        recorder.add("firmware.path", "safe file inside campaign", None, False,
                     "Binary cannot be located without a firmware manifest object.")
        return
    relative_path = firmware.get("path")
    path = _safe_campaign_file(campaign, relative_path)
    path_ok = path is not None
    recorder.add("firmware.path", "relative path inside campaign", relative_path,
                 path_ok, "Absolute paths and path traversal are rejected.")
    exists = path_ok and path is not None and path.is_file()
    recorder.add("firmware.present", "existing regular file", str(path) if path else None,
                 exists, "The approved binary must be retained in the campaign.")
    if not exists or path is None:
        return
    try:
        actual_hash = _sha256(path)
    except OSError as exc:
        recorder.add("firmware.sha256", expected_hash, type(exc).__name__, False,
                     "Firmware binary could not be hashed.")
        return
    recorder.add("firmware.sha256", expected_hash, actual_hash,
                 expected_hash is not None and actual_hash == expected_hash,
                 "Actual firmware bytes must match the approval and manifest SHA-256.")


def _check_build_log(campaign: Path, recorder: Recorder) -> None:
    path = _safe_campaign_file(campaign, INPUT_NAMES["build_log"])
    exists = path is not None and path.is_file()
    recorder.add("build_log.present", INPUT_NAMES["build_log"],
                 str(path) if path else None, exists,
                 "The diagnostic build log is mandatory evidence.")
    if not exists or path is None:
        return
    try:
        log = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        recorder.add("build_log.read", "readable text", type(exc).__name__, False,
                     "Build log could not be read.")
        return
    for name, value in REQUIRED_DEFINES.items():
        token = "-D" + name + "=" + value
        recorder.add("build_log.token." + name, token, token if token in log else None,
                     token in log,
                     "Build log must record the exact required compiler define token.")


def validate_campaign(campaign: Path) -> dict[str, Any]:
    """Validate campaign evidence and return a JSON-serializable summary."""
    recorder = Recorder()
    campaign = campaign.resolve()
    recorder.add("campaign.directory", "existing directory", str(campaign),
                 campaign.is_dir(), "Campaign root must exist before validation.")
    approval = _read_json(campaign, INPUT_NAMES["approval"], recorder, "approval")
    manifest = _read_json(campaign, INPUT_NAMES["manifest"], recorder, "manifest")

    firmware: Optional[Mapping[str, Any]] = None
    expected_firmware_hash: Optional[str] = None
    if approval is not None:
        _check_required_metadata(approval, recorder)
        _check_scope(approval, recorder)
    else:
        recorder.add("approval.contract", "valid approval", None, False,
                     "Approval contract cannot be checked because approval JSON is invalid.")

    if manifest is not None:
        _check_manifest_contract(manifest, approval if approval is not None else {}, recorder)
    else:
        recorder.add("manifest.contract", "valid manifest", None, False,
                     "Manifest contract cannot be checked because manifest JSON is invalid.")

    if approval is not None and manifest is not None:
        _, expected_firmware_hash, firmware = _check_source_and_firmware_binding(
            approval, manifest, recorder)
    else:
        recorder.add("identity.contract", "valid approval and manifest", None, False,
                     "Source and firmware identity cannot be checked without both JSON artifacts.")

    _check_binary(campaign, firmware, expected_firmware_hash, recorder)
    _check_build_log(campaign, recorder)

    verdict = "PASS" if recorder.passed else "FAIL"
    return {
        "schema": "h1-g0-check-summary-v1",
        "gate": GATE,
        "verdict": verdict,
        "execution": {"mode": "OFFLINE", "hardware_access": False},
        "campaign": str(campaign),
        "inputs": INPUT_NAMES,
        "checks": [check.as_dict() for check in recorder.checks],
    }


def _safe_output_path(campaign: Path, requested: str) -> Path:
    output = _safe_campaign_file(campaign, requested)
    if output is None:
        raise ValueError("--output must be a relative path inside --campaign")
    if output.name in INPUT_NAMES.values():
        raise ValueError("--output must not overwrite a required input artifact")
    return output


def _write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    payload = dict(summary)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path,
                        help="directory containing G0 offline evidence")
    parser.add_argument("--output", default="g0_check_summary.json",
                        help="relative summary path inside campaign (default: %(default)s)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    campaign = args.campaign.resolve()
    summary = validate_campaign(campaign)
    try:
        output = _safe_output_path(campaign, args.output)
        _write_summary(output, summary)
    except (OSError, ValueError) as exc:
        print("HARD_GATE_G0=FAIL")
        print("summary_write_error=" + str(exc), file=sys.stderr)
        return 2

    print("HARD_GATE_G0=" + summary["verdict"])
    print("summary=" + str(output))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
