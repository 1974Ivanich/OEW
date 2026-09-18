#!/usr/bin/env python3
"""M-OPT-0 no-HV baseline campaign automation for the bench PC (ПК-3).

=== HONESTY CONTRACT (read before changing) ===============================
M-OPT-0 records the behaviour of the *existing* board map M0 on the physical
bench, with the DC-link physically disconnected. This tool only records what
the firmware actually emits on UART and only reports verdicts it can prove:

  * raw UART bytes are written verbatim into per-run logs; nothing is injected,
    re-ordered, prettified or reconstructed;
  * ADC/VBUS numbers are never synthesised — the no-HV gate is computed from the
    real ``a`` responses of the run;
  * a run is ``PASS`` only when the no-HV gate AND the identity evidence
    (``run_id``, ``map_id``, ``map_crc32``) are present in the real UART stream
    and the firmware/source SHAs are recorded;
  * when the firmware does not emit that identity telemetry, the run is
    ``BLOCKED_MISSING_IDENTITY`` and the campaign can never reach ``PASS`` —
    ``--identity-source folder`` only documents that the run id comes from the
    artifact folder; it does NOT substitute for firmware evidence.

The hosted counterpart of this file is ``tests/m_opt_0_nohv_test.c``; it proves
the fail-closed admission/terminal logic and is explicitly NOT this baseline.

Usage (PowerShell on ПК-3):
    py -3 tools\\mopt0_capture.py list-ports
    py -3 tools\\mopt0_capture.py run --port COM15 --campaign D:\\campaign_raw\\mopt0_<UTC> `
        --firmware-bin build\\firmware.bin --confirm-dc-link-disconnected `
        --confirm-pc4-zero --confirm-sd-high
    py -3 tools\\mopt0_capture.py verify --campaign D:\\campaign_raw\\mopt0_<UTC>
    py -3 tools\\mopt0_capture.py run --simulate identity-absent `
        --campaign D:\\campaign_raw\\mopt0_sim_absent --firmware-bin build\\firmware.bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - exercised only without pyserial
    serial = None


# ── Statistical no-HV VBUS contract (TZ_BENCH_TEST2_STATISTICAL_NOHV_GATE.md) ──
DEFAULT_VBUS_SAMPLES = 20
NOHV_RAW_VBUS_MEDIAN_MAX = 9
NOHV_RAW_VBUS_HARD_LIMIT = 200

# src/adc.c: bipolar 12-bit shunt channels are usable strictly away from rails.
ADC_RAW_SAT_LOW = 1
ADC_RAW_SAT_HIGH = 4094

DEFAULT_RUNS = 5
DEFAULT_RUN_PREFIX = "M0-R"
IDENTITY_SOURCES = ("firmware", "folder")
# The bench firmware contract (ai5/m-opt-0-telemetry): `run=<id>` (1..23 chars,
# [A-Za-z0-9_.-]) answers `@RUN:ID=<id>`; `@FOC` then carries
# run_id/map_id/map_crc32 for every sample. `{run_id}` is substituted per run.
DEFAULT_RUN_ID_COMMAND = "run={run_id}"

ADC_RAW_RE = re.compile(
    r"@ADC:I1=(?P<i1>\d+):I2=(?P<i2>\d+):Ires=(?P<ires>\d+):VBUS=(?P<raw_vbus>\d+)")
CAL_RE = re.compile(r"@ADC:CAL:offset_i1=(?P<i1>\d+):offset_i2=(?P<i2>\d+)")
CAL_FAIL = "@ADC:CAL:FAIL"
ENC_ERR_RE = re.compile(r"(?:^|:)err=(?P<err>-?\d+)(?=$|:|[\r\n])")
STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)")
DEFAULT_DENY_RE = re.compile(r"default_deny=(?P<v>[01])")
# `sysinfo` exposes the transport counters once the @FOC budget package is in the
# image: uart_drp (packets dropped) and uart_trunc (packets rejected as
# oversized). Absent on older images -> reported as NOT_REPORTED, not as a pass.
UART_DRP_RE = re.compile(r"uart_drp=(?P<v>\d+)")
UART_TRUNC_RE = re.compile(r"uart_trunc=(?P<v>\d+)")
MOE_RE = re.compile(r"(?:^|[:\s])MOE=(?P<v>[01])(?=$|[:\s\r\n])")
SYSINFO_RE = re.compile(r"@SYSINFO:(?P<body>[^\r\n]*)")

# Identity telemetry the physical baseline MUST carry. Field spellings follow
# the accepted @FOC/@RUN contract; both "@RUN:ID=<run>" and "run_id=<run>" are
# accepted so a firmware-side rename does not silently block the campaign.
RUN_ID_PATTERNS = (
    re.compile(r"@RUN:ID=(?P<v>[A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:^|[:\s])run_id=(?P<v>[A-Za-z0-9_.\-]+)"),
)
MAP_ID_PATTERNS = (
    re.compile(r"(?:^|[:\s])map_id=(?P<v>[A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:^|[:\s])map=(?P<v>[A-Za-z0-9_.\-]+)"),
)
MAP_CRC_PATTERNS = (
    re.compile(r"map_crc32=(?P<v>(?:0x)?[0-9A-Fa-f]{1,8})"),
    re.compile(r"map_crc=(?P<v>(?:0x)?[0-9A-Fa-f]{1,8})"),
)

LOG_INTEGRITY_MARKERS = ("line overflow", "@UART:TRUNC", "@UART:DROP")


class MoptError(RuntimeError):
    """Expected validation, transport or evidence failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def median(values: Sequence[int]) -> Optional[int]:
    """Integer median; lower median for even length; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


# ── Pure evidence parsers / evaluators (unit tested) ───────────────────────

def parse_adc_raws(texts: Sequence[str]) -> Optional[list[dict[str, int]]]:
    """Parse every ``a`` response; None when any response is malformed.

    A malformed sample is fail-closed for the whole observation: the statistical
    no-HV gate must never silently drop a sample.
    """
    parsed: list[dict[str, int]] = []
    for text in texts:
        matches = list(ADC_RAW_RE.finditer(text))
        if not matches:
            return None
        parsed.append({k: int(v) for k, v in matches[-1].groupdict().items()})
    return parsed


def evaluate_no_hv_gate(samples: Optional[Sequence[dict[str, int]]]) -> dict[str, Any]:
    """Statistical no-HV proof: median ≤ 9 counts, hard cap 200, rails clear."""
    if not samples:
        return {
            "verdict": "FAIL", "samples": 0, "raw_vbus_median": None,
            "raw_vbus_max": None,
            "reason": "no parseable ADC samples",
        }
    raw_vbus = [sample["raw_vbus"] for sample in samples]
    median_vbus = median(raw_vbus)
    max_vbus = max(raw_vbus)
    i1_usable = all(ADC_RAW_SAT_LOW < sample["i1"] < ADC_RAW_SAT_HIGH for sample in samples)
    i2_usable = all(ADC_RAW_SAT_LOW < sample["i2"] < ADC_RAW_SAT_HIGH for sample in samples)
    checks = {
        "raw_vbus_median_nohv": median_vbus is not None and median_vbus <= NOHV_RAW_VBUS_MEDIAN_MAX,
        "raw_vbus_max_hard_limit": max_vbus <= NOHV_RAW_VBUS_HARD_LIMIT,
        "i1_away_from_rails": i1_usable,
        "i2_away_from_rails": i2_usable,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "verdict": "PASS" if not failed else "FAIL",
        "samples": len(samples),
        "raw_vbus_median": median_vbus,
        "raw_vbus_max": max_vbus,
        "raw_vbus_min": min(raw_vbus),
        "contract": {
            "median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
            "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT,
            "adc_sat_low": ADC_RAW_SAT_LOW,
            "adc_sat_high": ADC_RAW_SAT_HIGH,
        },
        "checks": checks,
        "failed_checks": failed,
        "reason": None if not failed else "no-HV gate failed: " + ", ".join(failed),
    }


def extract_identity(text: str) -> dict[str, Any]:
    """Extract the firmware-emitted identity fields from a raw run log."""
    def first(patterns: Sequence[re.Pattern[str]]) -> Optional[str]:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group("v")
        return None

    identity = {
        "run_id": first(RUN_ID_PATTERNS),
        "map_id": first(MAP_ID_PATTERNS),
        "map_crc32": first(MAP_CRC_PATTERNS),
    }
    identity["missing"] = sorted(key for key, value in identity.items() if not value)
    identity["present"] = not identity["missing"]
    return identity


LOG_MARKER_RE = re.compile(r"^\[[^\]]+\]\s+(?P<label>[A-Z_]+)\s*$")

# Command → semantic evidence key used by evaluate_run(). `a` samples are
# numbered individually so the offline verifier can recompute the gate.
COMMAND_EVIDENCE_KEYS = {
    "sysinfo": "sysinfo",
    "p?": "pwm_before",
    "pdump": "pdump_before",
    "c": "calibration",
    "enc": "encoder",
    "mapcap status": "status_before",
}
RUN_ID_COMMAND_PREFIX = "run="


def evidence_key_for_command(command: str) -> Optional[str]:
    """Map an exact command to its semantic evidence key (None = not evidence)."""
    if command.startswith(RUN_ID_COMMAND_PREFIX):
        return "run_id_set"
    return COMMAND_EVIDENCE_KEYS.get(command)


def rebuild_responses_from_log(text: str) -> dict[str, str]:
    """Rebuild the command→response mapping from a raw timestamped UART log.

    The transport writes ``[<utc>] TX`` / ``[<utc>] RX`` marker lines with the
    payload on the following line(s), so the pairing is recoverable byte-exactly
    from the retained log — the offline verifier needs no side channel.
    """
    responses: dict[str, str] = {}
    state: dict[str, Any] = {"label": None, "command": None, "buffer": []}
    adc_counter = [0]

    def flush() -> None:
        label = state["label"]
        buffer = state["buffer"]
        if label == "TX" and buffer:
            state["command"] = "".join(buffer).strip()
        elif label == "RX" and buffer and state["command"] is not None:
            payload = "".join(buffer)
            command = state["command"]
            if command == "a":
                responses[f"adc_{adc_counter[0]:03d}"] = payload
                adc_counter[0] += 1
            else:
                key = evidence_key_for_command(command)
                if key is not None:
                    responses[key] = responses.get(key, "") + payload
        state["label"] = None
        state["buffer"] = []

    for line in text.splitlines(keepends=True):
        match = LOG_MARKER_RE.match(line.rstrip("\r\n"))
        if match:
            flush()
            state["label"] = match.group("label")
            continue
        if state["label"] is not None:
            state["buffer"].append(line)
    flush()
    return responses


def check_log_integrity(text: str, run_id: str) -> list[str]:
    """Detect evidence-loss markers and mismatched run labels in a raw log."""
    issues: list[str] = []
    if not text.strip():
        issues.append("log is empty")
    for marker in LOG_INTEGRITY_MARKERS:
        if marker in text:
            issues.append(f"evidence-loss marker present: {marker}")
    for match in RUN_ID_PATTERNS[0].finditer(text):
        if match.group("v") != run_id:
            issues.append(f"foreign run id in log: {match.group('v')}")
            break
    return issues


def evaluate_run(
    run_id: str,
    log_text: str,
    responses: dict[str, Any],
    firmware_sha256: Optional[str],
    identity_source: str = "firmware",
) -> dict[str, Any]:
    """Per-run verdict. Fail-closed on every missing piece of real evidence."""
    samples = parse_adc_raws([responses[key] for key in sorted(responses) if key.startswith("adc_")])
    gate = evaluate_no_hv_gate(samples)
    identity = extract_identity(log_text)
    if identity_source == "folder" and not identity["run_id"]:
        # Documented fallback: the run id is the artifact folder name, and the
        # summary must show that the firmware did not carry it.
        identity = dict(identity)
        identity["folder_run_id"] = run_id
        identity["run_id_origin"] = "folder"
    else:
        identity = dict(identity)
        identity["run_id_origin"] = "firmware" if identity["run_id"] else None

    calibration_ok = CAL_FAIL not in responses.get("calibration", "") and bool(
        CAL_RE.search(responses.get("calibration", "")))
    encoder = last_int(ENC_ERR_RE, responses.get("encoder", ""), "err")
    status = parse_status(responses.get("status_before", ""))
    pwm_before = responses.get("pwm_before", "") + responses.get("pdump_before", "")
    default_deny_match = DEFAULT_DENY_RE.search(pwm_before)
    moe_match = MOE_RE.search(pwm_before)
    integrity = check_log_integrity(log_text, run_id)
    trunc_match = last_int_match(UART_TRUNC_RE, responses.get("sysinfo", ""), "v")
    drp_match = last_int_match(UART_DRP_RE, responses.get("sysinfo", ""), "v")
    uart_trunc_reported = trunc_match is not None
    # The operator's run-id command must be acknowledged by the firmware itself
    # (`@RUN:ID=<run_id>`) in the DIRECT response to that command: a stale echo
    # elsewhere in the log must never satisfy the identity gate.
    run_id_acked = f"@RUN:ID={run_id}" in responses.get("run_id_set", "")

    checks = {
        "log_integrity": not integrity,
        "calibration_ok": calibration_ok,
        "encoder_err_zero": encoder == 0,
        "default_deny_hold": bool(default_deny_match and default_deny_match.group("v") == "1"),
        "moe_low": bool(moe_match and moe_match.group("v") == "0"),
        "mapcap_idle_empty_before": bool(
            status and status["state"] == 0 and status["term"] == 0 and
            status["frames"] == 0 and status["dropped"] == 0 and status["avail"] == 0),
        "capture_rows_absent": "@MC:REC:" not in log_text,
        "no_hv_gate": gate["verdict"] == "PASS",
        # A non-zero truncation counter means an @FOC packet was rejected as
        # oversized: the evidence stream has a hole, so the run cannot pass.
        "uart_truncation_zero": trunc_match is None or trunc_match == 0,
        "firmware_sha_present": bool(firmware_sha256),
        "run_id_ack": run_id_acked,
        "identity_run_id": bool(identity.get("run_id") or identity.get("folder_run_id")),
        "identity_map_id": bool(identity.get("map_id")),
        "identity_map_crc32": bool(identity.get("map_crc32")),
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    if not failed:
        verdict = "PASS"
    elif any(name in failed for name in
             ("identity_run_id", "identity_map_id", "identity_map_crc32", "run_id_ack")):
        verdict = "BLOCKED_MISSING_IDENTITY"
    else:
        verdict = "FAIL"

    origin_note = None
    if identity.get("run_id_origin") == "folder":
        origin_note = (
            "run_id taken from the artifact folder because the firmware stream "
            "carries no @RUN:ID/run_id token; this does NOT satisfy the "
            "firmware-identity requirement")

    return {
        "run_id": run_id,
        "verdict": verdict,
        "checks": checks,
        "failed_checks": failed,
        "no_hv_gate": gate,
        "identity": identity,
        "identity_source": identity_source,
        "identity_note": origin_note,
        "log_integrity_issues": integrity,
        "uart_health": {
            "uart_trunc": trunc_match,
            "uart_drp": drp_match,
            "reported": uart_trunc_reported,
            "note": None if uart_trunc_reported else
                    "image does not report uart_trunc/uart_drp (budget package absent)",
        },
        "firmware_sha256": firmware_sha256,
        "reasons": [gate["reason"]] if gate["reason"] else [],
    }


def last_int_match(pattern: re.Pattern[str], text: str, group: str) -> Optional[int]:
    """Last integer match of `pattern` in `text`, or None when absent."""
    matches = list(pattern.finditer(text))
    return int(matches[-1].group(group)) if matches else None


def parse_status(text: str) -> Optional[dict[str, int]]:
    matches = list(STATUS_RE.finditer(text))
    return {k: int(v) for k, v in matches[-1].groupdict().items()} if matches else None


def last_int(pattern: re.Pattern[str], text: str, key: str) -> Optional[int]:
    matches = list(pattern.finditer(text))
    return int(matches[-1].group(key)) if matches else None


def campaign_verdict(
    runs: Sequence[dict[str, Any]],
    firmware_sha256: Optional[str],
    source_sha: Optional[str],
) -> dict[str, Any]:
    """Campaign verdict: PASS only for complete, identity-proven, no-HV runs."""
    ids = [run["run_id"] for run in runs]
    all_pass = bool(runs) and all(run["verdict"] == "PASS" for run in runs)
    any_identity_blocked = any(run["verdict"] == "BLOCKED_MISSING_IDENTITY" for run in runs)
    map_ids = {run.get("identity", {}).get("map_id") for run in runs}
    map_crcs = {run.get("identity", {}).get("map_crc32") for run in runs}
    checks = {
        "run_count_nonzero": bool(runs),
        "run_ids_unique": len(set(ids)) == len(ids),
        "all_runs_pass": all_pass,
        "firmware_sha_present": bool(firmware_sha256),
        "source_sha_present": bool(source_sha),
        # One baseline campaign must describe ONE map: five runs with different
        # map ids or CRCs are five different measurements, not a baseline.
        "map_id_identical": len(map_ids) == 1 and None not in map_ids,
        "map_crc32_identical": len(map_crcs) == 1 and None not in map_crcs,
    }
    if all(checks.values()):
        verdict = "PASS"
    elif any_identity_blocked:
        verdict = "INCOMPLETE_IDENTITY"
    elif (not checks["all_runs_pass"] or not checks["run_ids_unique"]
          or not checks["run_count_nonzero"] or not checks["map_id_identical"]
          or not checks["map_crc32_identical"]):
        verdict = "FAIL"
    else:
        verdict = "INCOMPLETE_PROVENANCE"
    return {
        "verdict": verdict,
        "checks": checks,
        "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "run_ids": ids,
        "map_id": next(iter(map_ids)) if len(map_ids) == 1 else sorted(map_ids, key=str),
        "map_crc32": next(iter(map_crcs)) if len(map_crcs) == 1 else sorted(map_crcs, key=str),
        "runs_total": len(runs),
        "runs_pass": sum(1 for run in runs if run["verdict"] == "PASS"),
        "mopt0_complete": verdict == "PASS",
        "note": (
            "PASS here means: 5 independent no-HV runs with firmware-side identity "
            "evidence recorded. It is NOT a Stage-A, DC-link, FOC or "
            "characterization permit."
            if verdict == "PASS" else
            "campaign is not a valid M-OPT-0 baseline; evidence is retained"),
    }


# ── Transports ────────────────────────────────────────────────────────────

class SerialTransport:
    """Physical UART transport; never constructed in simulation."""

    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self.responses: dict[str, str] = {}
        self._serial: Any = None
        self._log_file: Any = None

    def open(self) -> None:
        if serial is None:
            raise MoptError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=PHYSICAL; port={self.port}; baud={self.baud}; started={utc_now()}")
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.close()
            raise MoptError(f"Не удалось открыть {self.port} @ {self.baud}: {exc}") from exc
        time.sleep(0.25)
        self._read_quiet(0.5, 0.15, "BOOT")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def _read_quiet(self, total_timeout_s: float, quiet_s: float, label: str) -> str:
        deadline = time.monotonic() + total_timeout_s
        last_data = time.monotonic()
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            data = self._serial.read(self._serial.in_waiting or 1)
            if data:
                chunks.append(data)
                last_data = time.monotonic()
            elif chunks and time.monotonic() - last_data >= quiet_s:
                break
            else:
                time.sleep(0.02)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if text:
            self._log(label, text)
        return text

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> str:
        self.command_sequence.append(command)
        self._log("TX", command)
        self._serial.write((command + "\r\n").encode("ascii"))
        self._serial.flush()
        return self._read_quiet(total_timeout_s, quiet_s, "RX")


class SimulatedTransport:
    """Deterministic offline transport: proves orchestration, never hardware.

    ``identity-present`` models a firmware build that carries the identity
    telemetry; ``identity-absent`` models the current main (no @RUN:ID/run_id/
    map_id/map_crc32) so the fail-closed path can be exercised.
    """

    SCENARIOS = ("identity-present", "identity-absent", "nohv-violated", "run-id-rejected")

    def __init__(self, scenario: str, log_path: Path, run_id: str) -> None:
        if scenario not in self.SCENARIOS:
            raise MoptError(f"Unknown simulation scenario: {scenario}")
        self.scenario = scenario
        self.run_id = run_id
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self.responses: dict[str, str] = {}
        self._log_file: Any = None

    def open(self) -> None:
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=SIMULATED; scenario={self.scenario}; no COM/ST-Link/DC-link")
        # No boot-time identity echo: real firmware answers the run-id command,
        # so identity evidence must come from that response and the @FOC stream.
        self._log("BOOT", "@SIM:BOOT:deterministic-mopt0-simulation\r\n")

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> str:
        del total_timeout_s, quiet_s
        self.command_sequence.append(command)
        self._log("TX", command)
        raw_vbus = 120 if self.scenario == "nohv-violated" else 2
        identity_enabled = self.scenario != "identity-absent"
        # The firmware contract puts identity on the periodic @FOC line, not on
        # the @ADC sample response.
        identity_line = (
            f"@FOC:t=1000:run_id={self.run_id}:map_id=M0:map_crc32=1A2B3C4D:"
            f"Id=0:Iq=0:VBUS=201:STATE=0:SPD=0:TH=0:ADC_STATUS=7:FAULT=0:FAULT_R=0:"
            "FAIL=0:RUN=0:em_stop1=1:em_stop2=1\r\n" if identity_enabled else "")
        if command.startswith("run="):
            if self.scenario == "run-id-rejected":
                response = "err: run id must be 1..23 chars [A-Za-z0-9_.-]\r\n> "
            else:
                response = f"@RUN:ID={command[4:]}\r\n> "
        elif command == "sysinfo":
            response = "@SYSINFO:board=OEW-G474-REV7:fw=1.0\r\n"
        elif command == "p?":
            response = "@PWM:default_deny=1:MOE=0:CEN=0\r\n"
        elif command == "pdump":
            response = "@PWMD:TIM1:CR1=0x0000:BDTR=0x0000:MOE=0\r\n"
        elif command == "a":
            response = f"@ADC:I1=2048:I2=2048:Ires=2048:VBUS={raw_vbus}\r\n" + identity_line
        elif command == "c":
            response = "@ADC:CAL:offset_i1=2048:offset_i2=2048:offset_ires=2048\r\n"
        elif command == "enc":
            response = "@ENC:angle=0:speed=0:period_us=897:pulse_us=670:err=0\r\n"
        elif command == "mapcap status":
            response = ("@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0"
                        ":detail=0:raw_vbus=2:vbus_mv=201:i1_ma=0:i2_ma=0:adc_status=7:"
                        "sector=0:window=0\r\n")
        elif command == "f":
            response = "@SIM:FAULT:CLEAR:unexpected\r\n"
        else:
            response = "@SIM:ERR:unknown_command\r\n"
        self.responses[command] = response
        self._log("RX", response)
        return response


# ── Run / campaign execution ──────────────────────────────────────────────

def collect_run_evidence(
    transport: Any,
    vbus_samples: int,
    run_id: str,
    run_id_command: str = DEFAULT_RUN_ID_COMMAND,
) -> dict[str, str]:
    """The single ordered evidence sequence shared by both transports.

    The run-id command comes FIRST: without a firmware-acknowledged identifier
    every later sample would be unattributable, so the rest of the sequence is
    pointless. Its command text is recorded verbatim in the log.
    """
    responses: dict[str, str] = {}
    responses["run_id_set"] = transport.command(
        run_id_command.replace("{run_id}", run_id))
    responses["sysinfo"] = transport.command("sysinfo")
    responses["pwm_before"] = transport.command("p?")
    responses["pdump_before"] = transport.command("pdump")
    for index in range(vbus_samples):
        responses[f"adc_{index:03d}"] = transport.command("a")
    responses["calibration"] = transport.command("c")
    responses["encoder"] = transport.command("enc")
    responses["status_before"] = transport.command("mapcap status")
    return responses


def execute_run(
    run_id: str,
    transport: Any,
    run_dir: Path,
    firmware_sha256: Optional[str],
    identity_source: str,
    vbus_samples: int,
    run_id_command: str = DEFAULT_RUN_ID_COMMAND,
) -> dict[str, Any]:
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MoptError(f"Папка прогона уже существует, evidence неизменяем: {run_dir}") from exc
    transport.open()
    try:
        responses = collect_run_evidence(transport, vbus_samples, run_id, run_id_command)
    finally:
        transport.close()
    log_text = transport.log_path.read_text(encoding="utf-8", errors="replace")
    verdict = evaluate_run(run_id, log_text, responses, firmware_sha256, identity_source)
    verdict["artifacts"] = {
        "run_dir": str(run_dir),
        "uart_log": str(transport.log_path),
        "command_sequence": list(transport.command_sequence),
    }
    write_json(run_dir / f"{run_id}.json", verdict)
    return verdict


def resolve_source_sha(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def default_campaign_dir(simulated: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    kind = "mopt0_sim" if simulated else "mopt0_nohv"
    return Path("campaign_raw") / f"{kind}_{stamp}Z"


def _execute_run(args: argparse.Namespace) -> int:
    if args.runs < 1:
        raise MoptError("--runs должен быть >= 1.")
    if args.vbus_samples < 1:
        raise MoptError("--vbus-samples должен быть >= 1.")
    if args.identity_source not in IDENTITY_SOURCES:
        raise MoptError(f"--identity-source: ожидалось одно из {IDENTITY_SOURCES}.")

    firmware_sha256 = None
    if args.firmware_bin:
        firmware_path = Path(args.firmware_bin)
        if not firmware_path.is_file():
            raise MoptError(f"--firmware-bin не найден: {firmware_path}")
        firmware_sha256 = sha256_file(firmware_path)
    source_sha = resolve_source_sha(args.source_sha)

    simulated = bool(args.simulate)
    if not simulated:
        missing = [flag for flag, present in (
            ("--confirm-dc-link-disconnected", args.confirm_dc_link_disconnected),
            ("--confirm-pc4-zero", args.confirm_pc4_zero),
            ("--confirm-sd-high", args.confirm_sd_high),
        ) if not present]
        if missing:
            raise MoptError("Реальный запуск заблокирован. После физического preflight укажите: "
                            + " ".join(missing))
        if not args.port:
            raise MoptError("Для реального запуска укажите --port COMx (см. `list-ports`).")

    campaign_dir = Path(args.campaign) if args.campaign else default_campaign_dir(simulated)
    try:
        campaign_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MoptError(
            f"Папка кампании уже существует, evidence неизменяем: {campaign_dir}") from exc

    write_json(campaign_dir / "metadata.json", {
        "started_utc": utc_now(),
        "execution": {"mode": "SIMULATED" if simulated else "PHYSICAL",
                      "scenario": args.simulate},
        "run_prefix": args.run_prefix,
        "runs_requested": args.runs,
        "vbus_samples": args.vbus_samples,
        "identity_source": args.identity_source,
        "firmware_sha256": firmware_sha256,
        "source_sha": source_sha,
        "no_hv_contract": {
            "median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
            "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT,
            "adc_sat_low": ADC_RAW_SAT_LOW,
            "adc_sat_high": ADC_RAW_SAT_HIGH,
        },
        "arguments": {key: str(value) for key, value in vars(args).items()},
    })

    runs: list[dict[str, Any]] = []
    for index in range(1, args.runs + 1):
        run_id = f"{args.run_prefix}{index}"
        run_dir = campaign_dir / run_id
        if simulated:
            transport: Any = SimulatedTransport(args.simulate, run_dir / "uart.log", run_id)
        else:
            transport = SerialTransport(args.port, args.baud, run_dir / "uart.log")
        try:
            verdict = execute_run(run_id, transport, run_dir, firmware_sha256,
                                  args.identity_source, args.vbus_samples,
                                  args.run_id_command)
        except MoptError as exc:
            verdict = {"run_id": run_id, "verdict": "FAIL", "reasons": [str(exc)],
                       "checks": {}, "failed_checks": ["transport"], "no_hv_gate": None,
                       "identity": extract_identity(""), "identity_source": args.identity_source}
        runs.append(verdict)
        print(json.dumps({"run_id": run_id, "verdict": verdict["verdict"]},
                         ensure_ascii=False))

    campaign = campaign_verdict(runs, firmware_sha256, source_sha)
    if simulated:
        campaign["verdict"] = "SIMULATED" if campaign["verdict"] == "PASS" else campaign["verdict"]
        campaign["note"] = ("simulation proves orchestration only; it is never a physical "
                            "M-OPT-0 baseline")
        campaign["mopt0_complete"] = False

    summary = {
        "finished_utc": utc_now(),
        "execution": {"mode": "SIMULATED" if simulated else "PHYSICAL", "scenario": args.simulate},
        "campaign": campaign,
        "runs": runs,
        "artifacts": {"campaign_dir": str(campaign_dir),
                      "metadata": str(campaign_dir / "metadata.json"),
                      "summary": str(campaign_dir / "summary.json")},
    }
    write_json(campaign_dir / "summary.json", summary)
    print(json.dumps({"mode": summary["execution"]["mode"],
                      "campaign": campaign["verdict"],
                      "runs": campaign["runs_total"],
                      "runs_pass": campaign["runs_pass"],
                      "campaign_dir": str(campaign_dir)}, ensure_ascii=False))
    return 0 if campaign["verdict"] in ("PASS", "SIMULATED") else 1


def _execute_verify(args: argparse.Namespace) -> int:
    campaign_dir = Path(args.campaign)
    metadata_path = campaign_dir / "metadata.json"
    if not metadata_path.is_file():
        raise MoptError(f"Нет metadata.json в {campaign_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    firmware_sha256 = metadata.get("firmware_sha256")
    source_sha = metadata.get("source_sha")
    identity_source = metadata.get("identity_source", "firmware")
    vbus_samples = int(metadata.get("vbus_samples", DEFAULT_VBUS_SAMPLES))
    runs_requested = int(metadata.get("runs_requested", DEFAULT_RUNS))

    if firmware_sha256 and args.firmware_bin:
        actual = sha256_file(Path(args.firmware_bin))
        if actual != firmware_sha256:
            raise MoptError("firmware.bin не совпадает с metadata.json (SHA-256)")

    runs: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for index in range(1, runs_requested + 1):
        run_id = f"{metadata.get('run_prefix', DEFAULT_RUN_PREFIX)}{index}"
        run_dir = campaign_dir / run_id
        log_path = run_dir / "uart.log"
        if not log_path.is_file():
            mismatches.append(f"{run_id}: uart.log отсутствует")
            continue
        run_text = log_path.read_text(encoding="utf-8", errors="replace")
        responses = rebuild_responses_from_log(run_text)
        adc_chunks = [value for key, value in responses.items() if key.startswith("adc_")]
        verdict = evaluate_run(run_id, run_text, responses, firmware_sha256, identity_source)
        verdict["verified_offline"] = True
        runs.append(verdict)
        stored_path = run_dir / f"{run_id}.json"
        if stored_path.is_file():
            stored = json.loads(stored_path.read_text(encoding="utf-8"))
            if stored.get("verdict") != verdict["verdict"]:
                mismatches.append(
                    f"{run_id}: stored verdict {stored.get('verdict')} != recomputed {verdict['verdict']}")
        if adc_chunks and len(adc_chunks) < vbus_samples:
            mismatches.append(f"{run_id}: {len(adc_chunks)} ADC samples < {vbus_samples}")

    campaign = campaign_verdict(runs, firmware_sha256, source_sha)
    mode = metadata.get("execution", {}).get("mode", "PHYSICAL")
    verify_verdict = "MISMATCH" if mismatches else campaign["verdict"]
    if verify_verdict == "PASS" and mode != "PHYSICAL":
        # A simulated campaign can never be verified as a physical baseline.
        verify_verdict = "SIMULATED"
    report = {
        "verified_utc": utc_now(),
        "campaign_dir": str(campaign_dir),
        "mode": mode,
        "campaign": campaign,
        "runs": runs,
        "mismatches": mismatches,
        "verify_verdict": verify_verdict,
        "note": ("offline re-verification of a simulated campaign proves the checker and "
                 "the log format only; it is not a physical M-OPT-0 baseline"
                 if verify_verdict == "SIMULATED" else None),
    }
    write_json(campaign_dir / "verify_report.json", report)
    print(json.dumps({"campaign": campaign["verdict"], "verify": verify_verdict,
                      "mismatches": mismatches}, ensure_ascii=False))
    return 0 if verify_verdict == "PASS" else 1


def handle_utility_modes(args: argparse.Namespace) -> Optional[int]:
    if args.list_ports:
        if serial is None:
            raise MoptError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        for item in serial.tools.list_ports.comports():
            print(f"{item.device}\t{item.description}\t{item.hwid}")
        return 0
    if args.dry_run:
        print(json.dumps({
            "mode": "dry-run",
            "runs": args.runs,
            "run_ids": [f"{args.run_prefix}{i}" for i in range(1, args.runs + 1)],
            "no_hv_contract": {"median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
                               "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT},
            "requires_firmware_identity": ["run_id", "map_id", "map_crc32"],
            "note": "dry-run does not touch COM, ST-Link, sigrok or the bench",
        }, ensure_ascii=False, indent=2))
        return 0
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M-OPT-0 no-HV baseline campaign (5 runs) для стенда ПК-3.")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (("run", "собрать кампанию M0-R1..R5"),
                            ("verify", "перепроверить сохранённую кампанию офлайн")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--campaign", type=Path,
                          help="папка кампании (создаётся; для verify — существующая)")
        item.add_argument("--firmware-bin", help="путь к build/firmware.bin для SHA-256")
        item.add_argument("--source-sha", help="git SHA источника (иначе git rev-parse HEAD)")
        item.add_argument("--runs", type=int, default=DEFAULT_RUNS)
        item.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
        item.add_argument("--port", help="COM-порт UART MCU, например COM15")
        item.add_argument("--baud", type=int, default=115200)
        item.add_argument("--vbus-samples", type=int, default=DEFAULT_VBUS_SAMPLES)
        item.add_argument("--run-id-command", default=DEFAULT_RUN_ID_COMMAND,
                          help="команда установки run id; {run_id} подставляется "
                               "(default: %(default)s)")
        item.add_argument("--identity-source", choices=list(IDENTITY_SOURCES), default="firmware")
        item.add_argument("--confirm-dc-link-disconnected", action="store_true")
        item.add_argument("--confirm-pc4-zero", action="store_true")
        item.add_argument("--confirm-sd-high", action="store_true")
        item.add_argument("--simulate", choices=list(SimulatedTransport.SCENARIOS),
                          help="офлайн-прогон оркестрации; оборудование не используется")
        item.add_argument("--list-ports", action="store_true")
        item.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        utility = handle_utility_modes(args)
        if utility is not None:
            return utility
        if args.command == "run":
            return _execute_run(args)
        return _execute_verify(args)
    except MoptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Прервано оператором. Fault не очищался автоматически.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
