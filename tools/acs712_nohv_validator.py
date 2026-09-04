#!/usr/bin/env python3
"""Fail-closed offline validator for the ACS712 no-HV checkout evidence package.

The tool reads the campaign directory produced on ПК-3 and checks that every
required piece of evidence exists, is well-formed, and satisfies the de-energized
bounds.  It never opens a serial port, programs a target, or issues any command
that could energize hardware.

Exit codes:
    0  -- evidence package passes all checks.
    2  -- one or more checks failed (fail-closed).
    3  -- runtime/IO error during validation.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCHEMA = "acs712-nohv-check-v1"

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)

REQUIRED_FILES = {
    "identity/source_sha.txt",
    "identity/ci_run_url.txt",
    "identity/flash_verify.log",
    "identity/terminal_identity.log",
    "dmm/zero_current_offsets_worksheet.md",
    "calibration/acs712_calibration.json",
    "scope/scope_zero.csv",
    "observations/wiring_check.md",
    "summary/acs712_nohv_summary.md",
}

REQUIRED_CALIBRATION_FIELDS = [
    ("vcc_mv", (int, float)),
    ("sensors", (dict,)),
    ("sensors.U", (dict,)),
    ("sensors.U.v0_mv", (int, float)),
    ("sensors.U.sens_mv_per_a", (int, float)),
    ("sensors.V", (dict,)),
    ("sensors.V.v0_mv", (int, float)),
    ("sensors.V.sens_mv_per_a", (int, float)),
]

WORKSHEET_ROWS = ("Vcc", "v0_U", "v0_V", "Шум U", "Шум V")
GATE_IDS = ("G-01", "G-02", "G-03", "G-04", "G-05", "G-06")


@dataclass(frozen=True)
class Check:
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

    def add(
        self,
        check_id: str,
        expected: Any,
        actual: Any,
        passed: bool,
        detail: str,
    ) -> bool:
        self.checks.append(
            Check(
                check_id=check_id,
                result="PASS" if passed else "FAIL",
                expected=expected,
                actual=actual,
                detail=detail,
            )
        )
        return passed

    @property
    def passed(self) -> bool:
        return all(check.result == "PASS" for check in self.checks)


def _safe_path(campaign: Path, relative_path: str) -> Optional[Path]:
    """Resolve a relative path strictly inside the campaign directory."""
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


def _read_text(campaign: Path, relative: str) -> Optional[str]:
    path = _safe_path(campaign, relative)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _check_file_tree(campaign: Path, recorder: Recorder) -> None:
    for rel in sorted(REQUIRED_FILES):
        path = _safe_path(campaign, rel)
        exists = path is not None and path.is_file()
        recorder.add(
            f"file.{rel}",
            "present",
            str(path) if path is not None else rel,
            exists,
            "Required evidence file must exist inside the campaign directory.",
        )


def _check_identity(campaign: Path, recorder: Recorder) -> None:
    source_sha_text = _read_text(campaign, "identity/source_sha.txt")
    if source_sha_text is not None:
        first_line = source_sha_text.splitlines()[0].strip() if source_sha_text.strip() else ""
    else:
        first_line = ""
    sha_match = SHA_RE.search(first_line)
    sha_candidate = sha_match.group(0) if sha_match else first_line
    sha_ok = bool(sha_match and len(sha_candidate) == 40)
    recorder.add(
        "identity.source_sha.format",
        "40 hex source SHA",
        sha_candidate,
        sha_ok,
        "source_sha.txt must contain the exact source revision used for the build.",
    )

    url_text = _read_text(campaign, "identity/ci_run_url.txt")
    url_ok = bool(url_text and url_text.strip().startswith(("http://", "https://")))
    recorder.add(
        "identity.ci_run_url.present",
        "https? URL",
        url_text.strip() if url_text else None,
        url_ok,
        "ci_run_url.txt must contain the green CI run URL for the source SHA.",
    )

    flash_text = _read_text(campaign, "identity/flash_verify.log")
    flash_ok = bool(
        flash_text
        and flash_text.strip()
        and any(
            token in flash_text.lower()
            for token in ("verify ok", "flash done", "success", "program ok")
        )
    )
    recorder.add(
        "identity.flash_verify.success",
        "flash success marker",
        "present" if flash_text else None,
        flash_ok,
        "flash_verify.log must show that the firmware was written and verified.",
    )

    term_text = _read_text(campaign, "identity/terminal_identity.log")
    term_ok = bool(term_text and "sysinfo" in term_text.lower())
    recorder.add(
        "identity.terminal_identity.sysinfo",
        "sysinfo in terminal log",
        "present" if term_text else None,
        term_ok,
        "terminal_identity.log must contain the sysinfo response used for G-05/G-06.",
    )


def _get_nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _check_calibration(campaign: Path, recorder: Recorder) -> Optional[dict[str, Any]]:
    path = _safe_path(campaign, "calibration/acs712_calibration.json")
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        recorder.add(
            "calibration.json.valid",
            "valid JSON object",
            type(exc).__name__,
            False,
            "acs712_calibration.json must be readable JSON.",
        )
        return None

    is_object = isinstance(data, dict)
    recorder.add(
        "calibration.json.object",
        "JSON object",
        type(data).__name__,
        is_object,
        "Calibration root must be a JSON object.",
    )
    if not is_object:
        return None

    for dotted, types in REQUIRED_CALIBRATION_FIELDS:
        value = _get_nested(data, *dotted.split("."))
        recorder.add(
            f"calibration.{dotted}.type",
            types[0].__name__,
            type(value).__name__,
            isinstance(value, types),
            f"Calibration field {dotted} is required and must be numeric.",
        )

    vcc = data.get("vcc_mv")
    vcc_ok = isinstance(vcc, (int, float)) and 4500 <= vcc <= 5500
    recorder.add(
        "calibration.vcc_mv.range",
        "4500 <= vcc_mv <= 5500",
        vcc,
        vcc_ok,
        "Sensor supply must be close to 5.0 V.",
    )
    if not vcc_ok or not isinstance(vcc, (int, float)):
        return data

    half_vcc = vcc / 2.0
    for phase in ("U", "V"):
        v0 = _get_nested(data, "sensors", phase, "v0_mv")
        v0_ok = isinstance(v0, (int, float)) and (half_vcc - 200) <= v0 <= (half_vcc + 200)
        recorder.add(
            f"calibration.sensors.{phase}.v0_mv.range",
            f"{half_vcc - 200:.1f} <= v0_mv <= {half_vcc + 200:.1f}",
            v0,
            v0_ok,
            "Zero-current output must be near Vcc/2.",
        )

        sens = _get_nested(data, "sensors", phase, "sens_mv_per_a")
        sens_ok = isinstance(sens, (int, float)) and 80 <= sens <= 120
        recorder.add(
            f"calibration.sensors.{phase}.sens_mv_per_a.range",
            "80 <= sens_mv_per_a <= 120",
            sens,
            sens_ok,
            "ACS712-20A nominal sensitivity is 100 mV/A; allow measurement tolerance.",
        )

    return data


def _check_scope_csv(campaign: Path, recorder: Recorder) -> None:
    path = _safe_path(campaign, "scope/scope_zero.csv")
    if path is None or not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        recorder.add(
            "scope.csv.readable",
            "readable CSV",
            type(exc).__name__,
            False,
            "scope_zero.csv could not be read.",
        )
        return

    lines = text.splitlines()
    header_line = next((line for line in lines if not line.startswith("#")), None)
    expected_header = (
        "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,scope_qualified,note"
    )
    recorder.add(
        "scope.csv.header",
        expected_header,
        header_line,
        header_line == expected_header,
        "Scope CSV header must match the approved template exactly.",
    )
    if header_line is None:
        return

    reader = csv.DictReader([header_line] + [line for line in lines if not line.startswith("#") and line != header_line])
    rows = list(reader)
    recorder.add(
        "scope.csv.rows.count",
        ">= 1 data row",
        len(rows),
        len(rows) >= 1,
        "Scope CSV must contain at least one measurement row.",
    )

    numeric_cols = ("ref_u_mv", "ref_v_mv", "margin_ticks", "blanking_ticks")
    for idx, row in enumerate(rows, start=1):
        for col in numeric_cols:
            val = row.get(col)
            try:
                float(val)  # type: ignore[arg-type]
                ok = True
            except (TypeError, ValueError):
                ok = False
            recorder.add(
                f"scope.csv.row{idx}.{col}.numeric",
                "numeric",
                val,
                ok,
                f"Row {idx} column {col} must be numeric.",
            )

        ref_w = row.get("ref_w_mv")
        ref_w_ok = ref_w is None or str(ref_w).strip() == ""
        recorder.add(
            f"scope.csv.row{idx}.ref_w_mv.empty",
            "empty",
            ref_w,
            ref_w_ok,
            f"Row {idx}: ref_w_mv must be empty because W is derived by KCL.",
        )

        qualified = row.get("scope_qualified")
        recorder.add(
            f"scope.csv.row{idx}.scope_qualified.zero",
            "0",
            qualified,
            str(qualified).strip() == "0",
            f"Row {idx}: scope_qualified must be 0 for a zero-current/no-aperture record.",
        )


def _check_worksheet(campaign: Path, recorder: Recorder) -> None:
    text = _read_text(campaign, "dmm/zero_current_offsets_worksheet.md")
    if text is None:
        return
    for marker in WORKSHEET_ROWS:
        # Look for a table row that starts with the marker and contains a measured value.
        pattern = re.compile(
            rf"^\|\s*{re.escape(marker)}[^|]*\|\s*(\S[^|]*)\s*\|",
            re.MULTILINE | re.IGNORECASE,
        )
        match = pattern.search(text)
        value = match.group(1).strip() if match else None
        try:
            numeric = float(value) if value else None  # type: ignore[arg-type]
            ok = numeric is not None
        except (TypeError, ValueError):
            numeric = value
            ok = False
        recorder.add(
            f"worksheet.{marker}.measured",
            "numeric measured value",
            value,
            ok,
            f"Worksheet must contain a measured numeric value for {marker}.",
        )


def _check_wiring_and_summary(campaign: Path, recorder: Recorder) -> None:
    wiring = _read_text(campaign, "observations/wiring_check.md")
    wiring_ok = False
    if wiring:
        has_all_gates = all(gid in wiring for gid in GATE_IDS)
        has_fail = "FAIL" in wiring.upper() or "| FAIL |" in wiring.upper()
        wiring_ok = has_all_gates and not has_fail
    recorder.add(
        "observations.wiring_check.gates",
        "G-01..G-06 present, no FAIL",
        "present" if wiring else None,
        wiring_ok,
        "wiring_check.md must reference all hard gates and must not contain a FAIL verdict.",
    )

    summary = _read_text(campaign, "summary/acs712_nohv_summary.md")
    summary_ok = False
    if summary:
        # Accept either an explicit Verdict/Overall PASS table row.
        has_pass = bool(
            re.search(r"^\|\s*(Verdict|Overall)\s*\|\s*PASS\s*\|", summary, re.MULTILINE | re.IGNORECASE)
        )
        has_fail = bool(
            re.search(r"^\|\s*(Verdict|Overall)\s*\|\s*FAIL\s*\|", summary, re.MULTILINE | re.IGNORECASE)
        )
        summary_ok = has_pass and not has_fail
    recorder.add(
        "summary.verdict.pass",
        "| Verdict | PASS |",
        "present" if summary else None,
        summary_ok,
        "acs712_nohv_summary.md must contain an explicit PASS verdict.",
    )


def validate_campaign(campaign: Path) -> dict[str, Any]:
    """Validate the campaign directory and return a JSON-serializable summary."""
    recorder = Recorder()
    campaign = campaign.resolve()
    recorder.add(
        "campaign.directory",
        "existing directory",
        str(campaign),
        campaign.is_dir(),
        "Campaign root must exist before validation.",
    )
    if not campaign.is_dir():
        return {
            "schema": SCHEMA,
            "verdict": "FAIL",
            "campaign": str(campaign),
            "checks": [check.as_dict() for check in recorder.checks],
        }

    _check_file_tree(campaign, recorder)
    _check_identity(campaign, recorder)
    _check_calibration(campaign, recorder)
    _check_scope_csv(campaign, recorder)
    _check_worksheet(campaign, recorder)
    _check_wiring_and_summary(campaign, recorder)

    return {
        "schema": SCHEMA,
        "verdict": "PASS" if recorder.passed else "FAIL",
        "campaign": str(campaign),
        "checks": [check.as_dict() for check in recorder.checks],
    }


def _write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    payload = dict(summary)
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign",
        required=True,
        type=Path,
        help="directory containing the ACS712 no-HV checkout evidence",
    )
    parser.add_argument(
        "--output",
        default="acs712_nohv_validation_summary.json",
        help="relative summary path inside campaign (default: %(default)s)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    campaign = args.campaign.resolve()
    summary = validate_campaign(campaign)
    try:
        output_path = _safe_path(campaign, args.output)
        if output_path is None:
            raise ValueError("--output must be a relative path inside --campaign")
        _write_summary(output_path, summary)
    except (OSError, ValueError) as exc:
        print("ACS712_NOHV=FAIL")
        print("summary_write_error=" + str(exc), file=sys.stderr)
        return 3

    print("ACS712_NOHV=" + summary["verdict"])
    print("summary=" + str(output_path))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
