#!/usr/bin/env python3
"""UART + sigrok evidence automation for no-HV MapCapture test №2.

The physical and software-HIL modes share the same command sequence, terminal
polling, evaluator and summary writer. A simulated result is never a physical
bench result: its summary is explicitly marked ``execution.mode=SIMULATED`` and
its final verdict is always ``SIMULATED``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Protocol

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


APPROVED_TEST3_PROFILE_ID = 1398361684  # 0x53594E54, SYNT; only supported profile
DEFAULT_SIGROK_DRIVER = "fx2lafw"
DEFAULT_SIGROK_RATE_HZ = 8_000_000
DEFAULT_SIGROK_CHANNELS = tuple(f"D{i}" for i in range(12))

# Статистический no-HV гейт канала VBUS (TZ_BENCH_TEST3_STATISTICAL_NOHV_GATE.md):
# одиночный raw_vbus на этом стенде даёт шумовые выбросы до 61 (макс. 142) при 0 В.
# Гейт по N сэмплам: медиана <= nohv_max_raw_vbus (доказывает шину < ~0.9 В),
# max <= NOHV_RAW_VBUS_HARD_LIMIT (страховка от грубых аномалий; 142 = худший
# наблюдаемый шум при DMM-доказанных 0 В, 200 = +40% запас, ~20 В шины).
DEFAULT_VBUS_SAMPLES = 20
NOHV_RAW_VBUS_HARD_LIMIT = 200

# Mirrors src/adc.c: ADC1/ADC2 shunt channels are bipolar 12-bit inputs.
# Their frame status is usable only strictly away from both rails.
ADC_RAW_SAT_LOW = 1
ADC_RAW_SAT_HIGH = 4094


def bipolar_adc_sample_is_usable(raw: int) -> bool:
    """Match firmware adc_bipolar_sample_is_usable(raw) exactly."""
    return ADC_RAW_SAT_LOW < raw < ADC_RAW_SAT_HIGH


MAP_CAPTURE_IDLE = 0

MAP_CAPTURE_ARMED = 1
MAP_CAPTURE_RUNNING = 2
MAP_CAPTURE_FAULTED = 5
MAP_CAPTURE_TERMINAL_STATES = (3, 4, 5)
MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW = 7
ADC_FRAME_WINDOW_INVALID = 7

PROFILE_CONTRACTS: Mapping[int, Mapping[str, int | str]] = MappingProxyType({
    APPROVED_TEST3_PROFILE_ID: MappingProxyType({
        "name": "SYNT",
        "max_abs_shunt_ma": 10000,
        "min_vbus_mv": 1000,
        "max_vbus_mv": 70000,
        "nohv_max_raw_vbus": 9,
        "nohv_max_raw_vbus_hard": NOHV_RAW_VBUS_HARD_LIMIT,
        "expected_adc_status": ADC_FRAME_WINDOW_INVALID,
    })
})

SIMULATION_SCENARIOS = (
    "vbus-low-valid",
    "i1-limit",
    "i2-limit",
    "vbus-high",
    "adc-invalid",
    "timeout",
    "records",
    "arm-fail",
    "run-fail",
    "sigrok-fail",
    "missing-csv",
)

ARM_RE = re.compile(
    r"@MC:ARM:cap=(?P<cap>\d+):rc=(?P<rc>-?\d+)"
    r"(?::offsets_valid=(?P<offsets_valid>[01]):inj_start_rc=(?P<inj_start_rc>0|-1|NA))?"
    r"(?=\r|\n|$)"
)
ADUMP_RE = re.compile(
    r"@ADUMP:SQR1=(?P<sqr1>0x[0-9A-Fa-f]+):CFGR=(?P<cfgr>0x[0-9A-Fa-f]+)"
    r":SMPR1=(?P<smpr1>0x[0-9A-Fa-f]+):JSQR=(?P<jsqr>0x[0-9A-Fa-f]+)"
    r":DIFSEL=(?P<difsel>0x[0-9A-Fa-f]+):CR=(?P<cr>0x[0-9A-Fa-f]+)"
    r":ISR=(?P<isr>0x[0-9A-Fa-f]+):DR=(?P<dr>0x[0-9A-Fa-f]+)"
    r":JDR1=(?P<jdr1>0x[0-9A-Fa-f]+):JDR2=(?P<jdr2>0x[0-9A-Fa-f]+)"
    r":JDR3=(?P<jdr3>0x[0-9A-Fa-f]+):JDR4=(?P<jdr4>0x[0-9A-Fa-f]+)"
    r"(?::ADC1_CR=(?P<adc1_cr>0x[0-9A-Fa-f]+):ADC1_ISR=(?P<adc1_isr>0x[0-9A-Fa-f]+))?"
    r"(?=\r|\n|$)"
)
RUN_RE = re.compile(r"@MC:RUN:rc=(?P<rc>-?\d+)")

STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)"
    r":detail=(?P<detail>-?\d+):raw_vbus=(?P<raw_vbus>\d+)"
    r":vbus_mv=(?P<vbus_mv>-?\d+):i1_ma=(?P<i1_ma>-?\d+)"
    r":i2_ma=(?P<i2_ma>-?\d+):adc_status=(?P<adc_status>-?\d+)"
    r":sector=(?P<sector>\d+):window=(?P<window>\d+)"
)
ADC_RAW_RE = re.compile(r"@ADC:I1=(?P<i1>\d+):I2=(?P<i2>\d+):Ires=(?P<ires>\d+):VBUS=(?P<raw_vbus>\d+)")
DRAIN_RE = re.compile(r"@MC:DRAIN:records=(?P<records>\d+)")
ENC_ERR_RE = re.compile(r"(?:^|:)err=(?P<err>-?\d+)(?=$|:|\r|\n)")
CALIBRATION_OK_RE = re.compile(r"@ADC:CAL:offset_i1=(?P<i1>\d+):offset_i2=(?P<i2>\d+):offset_ires=(?P<ires>\d+)")


class BenchTestError(RuntimeError):
    """Expected validation, transport or capture failure."""


@dataclass
class CommandResult:
    command: str
    response: str
    started_utc: str
    elapsed_s: float


@dataclass
class SigrokResult:
    command: list[str]
    started_utc: str
    elapsed_s: float
    returncode: Optional[int]
    csv_path: str
    stdout_path: str
    stderr_path: str
    csv_exists: bool
    csv_size_bytes: int
    error: Optional[str] = None


class CommandTransport(Protocol):
    command_sequence: list[str]

    def open(self) -> None: ...
    def close(self) -> None: ...
    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> CommandResult: ...


class CaptureBackend(Protocol):
    events: list[str]

    def start(self) -> None: ...
    def collect(self) -> SigrokResult: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def last_match(pattern: re.Pattern[str], text: str) -> Optional[re.Match[str]]:
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def parse_status(text: str) -> Optional[dict[str, int]]:
    match = last_match(STATUS_RE, text)
    return {key: int(value) for key, value in match.groupdict().items()} if match else None


def parse_adc_raw(text: str) -> Optional[dict[str, int]]:
    match = last_match(ADC_RAW_RE, text)
    return {key: int(value) for key, value in match.groupdict().items()} if match else None


def parse_adc_raws(texts: Sequence[str]) -> Optional[list[dict[str, int]]]:
    """Parse every `a` response; return None if any response is malformed.

    The statistical no-HV gate must never silently drop a sample: a malformed
    response is fail-closed for the whole observation.
    """
    parsed: list[dict[str, int]] = []
    for text in texts:
        raw = parse_adc_raw(text)
        if raw is None:
            return None
        parsed.append(raw)
    return parsed


def median(values: Sequence[int]) -> Optional[int]:
    """Integer median; None for an empty sequence. Even length -> lower median."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return ordered[(n - 1) // 2]


def parse_arm(text: str) -> Optional[dict[str, int | str]]:
    """Parse legacy or extended ARM evidence without weakening the rc=0 gate."""
    match = last_match(ARM_RE, text)
    if match is None:
        return None
    parsed: dict[str, int | str] = {
        "cap": int(match.group("cap")),
        "rc": int(match.group("rc")),
    }
    offsets_valid = match.group("offsets_valid")
    inj_start_rc = match.group("inj_start_rc")
    if offsets_valid is not None and inj_start_rc is not None:
        parsed["offsets_valid"] = int(offsets_valid)
        parsed["inj_start_rc"] = inj_start_rc
    return parsed


def parse_adc_dump(text: str) -> Optional[dict[str, int]]:
    """Parse legacy ADC2 diagnostics plus optional paired-ADC1 CR/ISR evidence."""
    match = last_match(ADUMP_RE, text)
    if match is None:
        return None
    return {
        key: int(value, 16)
        for key, value in match.groupdict().items()
        if value is not None
    }


def parse_drain_records(text: str) -> Optional[int]:

    match = last_match(DRAIN_RE, text)
    return int(match.group("records")) if match else None


def has_arm_ok(text: str) -> bool:
    arm = parse_arm(text)
    return bool(arm and int(arm["cap"]) > 0 and int(arm["rc"]) == 0)


def has_run_ok(text: str) -> bool:
    match = last_match(RUN_RE, text)
    return bool(match and int(match.group("rc")) == 0)


def has_encoder_ok(text: str) -> bool:
    match = last_match(ENC_ERR_RE, text)
    return bool(match and int(match.group("err")) == 0)


def has_calibration_ok(text: str) -> bool:
    return "@ADC:CAL:FAIL" not in text and last_match(CALIBRATION_OK_RE, text) is not None


def get_profile_contract(profile_id: int) -> Mapping[str, int | str]:
    contract = PROFILE_CONTRACTS.get(profile_id)
    if contract is None:
        raise BenchTestError(f"Profile {profile_id} не имеет утверждённого automation contract.")
    return contract


def evaluate_test(
    arm_text: str,
    run_text: str,
    preflight_adc_texts: Sequence[str],
    status_text: str,
    drain_text: str,
    profile_contract: Mapping[str, int | str],
) -> dict[str, Any]:
    """Fail closed unless terminal evidence is explicit VBUS_LOW for SYNT.

    Pre-flight no-HV proof for `a` is statistical (TZ_BENCH_TEST3_STATISTICAL_NOHV_GATE.md):
    median(raw_vbus) <= nohv_max_raw_vbus proves bus < ~0.9 V, and
    max(raw_vbus) <= nohv_max_raw_vbus_hard guards against gross anomalies.
    """
    arm = parse_arm(arm_text)

    status = parse_status(status_text)
    preflight_raws = parse_adc_raws(list(preflight_adc_texts))
    records = parse_drain_records(drain_text)

    nohv_raw_max = int(profile_contract["nohv_max_raw_vbus"])
    nohv_raw_hard = int(profile_contract["nohv_max_raw_vbus_hard"])
    min_vbus_mv = int(profile_contract["min_vbus_mv"])
    max_shunt_ma = int(profile_contract["max_abs_shunt_ma"])
    expected_adc_status = int(profile_contract["expected_adc_status"])
    raw_vbus_values = [raw["raw_vbus"] for raw in preflight_raws] if preflight_raws else []
    raw_vbus_median = median(raw_vbus_values)
    raw_vbus_max = max(raw_vbus_values) if raw_vbus_values else None
    checks = {
        "arm_rc_zero": has_arm_ok(arm_text),
        "run_rc_zero": has_run_ok(run_text),
        "preflight_adc_parsed": bool(preflight_raws and len(preflight_raws) >= 1),
        "preflight_raw_vbus_median_nohv": bool(raw_vbus_median is not None and raw_vbus_median <= nohv_raw_max),
        "preflight_raw_vbus_max_hard": bool(raw_vbus_max is not None and raw_vbus_max <= nohv_raw_hard),
        "preflight_i1_not_saturated": bool(preflight_raws and all(bipolar_adc_sample_is_usable(raw["i1"]) for raw in preflight_raws)),
        "preflight_i2_not_saturated": bool(preflight_raws and all(bipolar_adc_sample_is_usable(raw["i2"]) for raw in preflight_raws)),

        "status_parsed_extended_contract": status is not None,
        "faulted_state": bool(status and status["state"] == MAP_CAPTURE_FAULTED),
        "terminal_is_limit_exceeded": bool(status and status["term"] == -12),
        "detail_is_vbus_low": bool(status and status["detail"] == MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW),
        "terminal_adc_window_invalid": bool(status and status["adc_status"] == expected_adc_status),
        "terminal_raw_vbus_nohv": bool(status and status["raw_vbus"] <= nohv_raw_max),
        "terminal_vbus_below_profile_min": bool(status and 0 <= status["vbus_mv"] < min_vbus_mv),
        "terminal_i1_within_limit": bool(status and abs(status["i1_ma"]) <= max_shunt_ma),
        "terminal_i2_within_limit": bool(status and abs(status["i2_ma"]) <= max_shunt_ma),
        "zero_frames": bool(status and status["frames"] == 0),
        "zero_dropped": bool(status and status["dropped"] == 0),
        "zero_available": bool(status and status["avail"] == 0),
        "drain_parsed": records is not None,
        "zero_drain_records": records == 0,
        "no_record_lines": "@MC:REC:" not in drain_text,
    }
    automation = "PASS" if all(checks.values()) else "FAIL"
    return {
        "automation": automation,
        "scope": "PENDING" if automation == "PASS" else "NOT_APPLICABLE",
        "final": "PENDING" if automation == "PASS" else "FAIL",
        "checks": checks,
        "arm": arm,
        "status": status,
        "preflight_adc": preflight_raws,
        "preflight_raw_vbus_samples": raw_vbus_values,
        "preflight_raw_vbus_median": raw_vbus_median,
        "preflight_raw_vbus_max": raw_vbus_max,

        "drain_records": records,
        "expected_nohv_contract": {
            "term": -12,
            "detail": "VBUS_LOW",
            "detail_code": MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW,
            "adc_status": "WINDOW_INVALID",
            "adc_status_code": expected_adc_status,
            "max_raw_vbus": nohv_raw_max,
            "max_raw_vbus_hard": nohv_raw_hard,
            "min_vbus_mv_exclusive": min_vbus_mv,
            "max_abs_shunt_ma": max_shunt_ma,
            "max_vbus_mv_metadata_only": int(profile_contract["max_vbus_mv"]),
        },
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_output_dir(simulated: bool) -> Path:
    kind = "test2_sim" if simulated else "test2_nohv"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3] + "Z"

    return Path("campaign_raw") / f"{kind}_{stamp}"


def rate_to_sigrok(rate_hz: int) -> str:
    if rate_hz <= 0:
        raise BenchTestError("Частота sigrok должна быть положительной.")
    if rate_hz % 1_000_000 == 0:
        return f"{rate_hz // 1_000_000}m"
    if rate_hz % 1_000 == 0:
        return f"{rate_hz // 1_000}k"
    return str(rate_hz)


def parse_channels(value: str) -> list[str]:
    channels = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not channels or any(not re.fullmatch(r"D(?:1[0-5]|[0-9])", ch) for ch in channels):
        raise argparse.ArgumentTypeError("Каналы должны быть списком D0..D15 через запятую.")
    return channels


class SerialTransport:
    """Physical UART transport. This backend is never constructed in simulation."""

    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self._serial: Any = None
        self._log_file: Any = None

    def open(self) -> None:
        if serial is None:
            raise BenchTestError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=PHYSICAL; port={self.port}; baud={self.baud}")
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:
            self.close()
            raise BenchTestError(f"Не удалось открыть {self.port} @ {self.baud}: {exc}") from exc
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

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> CommandResult:
        started_utc, started = utc_now(), time.monotonic()
        residual = self._read_quiet(0.2, 0.05, "RX_BEFORE_COMMAND")
        if residual:
            self._log("NOTE", "Фоновые UART-данные сохранены до команды; они не удалялись.")
        self.command_sequence.append(command)
        self._log("TX", command)
        self._serial.write((command + "\r\n").encode("ascii"))
        self._serial.flush()
        response = self._read_quiet(total_timeout_s, quiet_s, "RX")
        return CommandResult(command, response, started_utc, round(time.monotonic() - started, 3))


def _status_line(
    state: int, term: int, detail: int = 0, raw_vbus: int = 2, vbus_mv: int = 201,
    i1_ma: int = 0, i2_ma: int = 0, adc_status: int = ADC_FRAME_WINDOW_INVALID,
    frames: int = 0, dropped: int = 0, avail: int = 0,
) -> str:
    return (
        f"@MC:STATUS:state={state}:term={term}:cap=7:frames={frames}:dropped={dropped}:periods=1:avail={avail}:"
        f"detail={detail}:raw_vbus={raw_vbus}:vbus_mv={vbus_mv}:i1_ma={i1_ma}:i2_ma={i2_ma}:"
        f"adc_status={adc_status}:sector=0:window=0\r\n> "
    )


class SimulatedTransport:
    """Deterministic UART responder; it cannot open a COM port or hardware device."""

    def __init__(self, scenario: str, log_path: Path) -> None:
        if scenario not in SIMULATION_SCENARIOS:
            raise BenchTestError(f"Unknown simulation scenario: {scenario}")
        self.scenario = scenario
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self._phase = "IDLE"
        self._log_file: Any = None

    def open(self) -> None:
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=SIMULATED; scenario={self.scenario}; no COM/ST-Link/STEVAL/DC-link")
        self._log("BOOT", "@SIM:BOOT:deterministic-test2-simulation\r\n> ")

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def _terminal_status(self) -> str:
        if self.scenario == "i1-limit":
            return _status_line(MAP_CAPTURE_FAULTED, -12, detail=5, i1_ma=10001)
        if self.scenario == "i2-limit":
            return _status_line(MAP_CAPTURE_FAULTED, -12, detail=6, i2_ma=-10001)
        if self.scenario == "vbus-high":
            return _status_line(MAP_CAPTURE_FAULTED, -12, detail=8, raw_vbus=100, vbus_mv=70000)
        if self.scenario == "adc-invalid":
            return _status_line(MAP_CAPTURE_FAULTED, -11, detail=2, raw_vbus=0, vbus_mv=0, adc_status=8)
        if self.scenario == "records":
            return _status_line(MAP_CAPTURE_FAULTED, -12, detail=7, frames=1, avail=1)
        return _status_line(MAP_CAPTURE_FAULTED, -12, detail=7)

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> CommandResult:
        del total_timeout_s, quiet_s
        self.command_sequence.append(command)
        self._log("TX", command)
        if command == "sysinfo":
            response = "@SIM:SYSINFO:mode=SIMULATED\r\n> "
        elif command in ("p?", "pdump"):
            response = "@SIM:PWM:default_deny=1\r\n> "
        elif command == "a":
            response = "@ADC:I1=2048:I2=2048:Ires=2048:VBUS=2\r\n> "
        elif command == "c":
            response = "@ADC:CAL:offset_i1=2048:offset_i2=2048:offset_ires=2048\r\n> "
        elif command == "enc":
            response = "@ENC:angle=0:speed=0:period_us=897:pulse_us=670:err=0\r\n> "
        elif command.startswith("mcarm="):
            if self.scenario == "arm-fail":
                response = "@MC:ARM:cap=0:rc=-4\r\n> "
            else:
                self._phase = "ARMED"
                response = "@MC:ARM:cap=7:rc=0:offsets_valid=1:inj_start_rc=0\r\n> "

        elif command == "mapcap run":
            if self.scenario == "run-fail":
                response = "@MC:RUN:rc=-7\r\n> "
            else:
                self._phase = "RUNNING"
                response = "@MC:RUN:rc=0\r\n> "
        elif command == "mapcap status":
            if self._phase == "IDLE":
                response = _status_line(MAP_CAPTURE_IDLE, 0)
            elif self._phase == "ARMED":
                response = _status_line(MAP_CAPTURE_ARMED, 0)
            elif self.scenario == "timeout":
                response = _status_line(MAP_CAPTURE_RUNNING, 0)
            else:
                self._phase = "TERMINAL"
                response = self._terminal_status()
        elif command == "mapcap drain":
            response = "@MC:REC:cap=7:seq=1\r\n@MC:DRAIN:records=1\r\n> " if self.scenario == "records" else "@MC:DRAIN:records=0\r\n> "
        elif command == "f":
            response = "@SIM:FAULT:CLEAR:unexpected\r\n> "
        else:
            response = "@SIM:ERR:unknown_command\r\n> "
        self._log("RX", response)
        return CommandResult(command, response, utc_now(), 0.0)


class RealSigrokCapture:
    def __init__(self, cli: str, driver: str, channels: list[str], rate_hz: int, duration_s: float, output_dir: Path) -> None:
        self.cli, self.driver, self.channels = cli, driver, channels
        self.rate_hz, self.duration_s, self.output_dir = rate_hz, duration_s, output_dir
        self.events: list[str] = []
        self._process: Optional[subprocess.Popen[str]] = None
        self._command: list[str] = []
        self._csv = output_dir / "sigrok_digital.csv"
        self._stdout = output_dir / "sigrok_stdout.log"
        self._stderr = output_dir / "sigrok_stderr.log"
        self._started = 0.0

    def start(self) -> None:
        self._command = [self.cli, "--driver", self.driver, "--config", f"samplerate={rate_to_sigrok(self.rate_hz)}", "--channels", ",".join(self.channels), "--time", str(int(self.duration_s * 1000)), "-O", "csv", "-o", str(self._csv)]
        stdout = self._stdout.open("w", encoding="utf-8", newline="\n")
        stderr = self._stderr.open("w", encoding="utf-8", newline="\n")
        try:
            self._process = subprocess.Popen(self._command, stdout=stdout, stderr=stderr, text=True)
        finally:
            stdout.close()
            stderr.close()
        self._started = time.monotonic()
        self.events.append("sigrok:start")

    def collect(self) -> SigrokResult:
        if self._process is None:
            raise BenchTestError("sigrok collect without start")
        error: Optional[str] = None
        try:
            returncode = self._process.wait(timeout=self.duration_s + 65.0)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
            returncode = self._process.returncode
            error = "sigrok-cli timeout"
        self.events.append("sigrok:collect")
        return SigrokResult(self._command, utc_now(), round(time.monotonic() - self._started, 3), returncode, str(self._csv), str(self._stdout), str(self._stderr), self._csv.exists(), self._csv.stat().st_size if self._csv.exists() else 0, error)


class SimulatedSigrokCapture:
    """Deterministic evidence backend. It never invokes sigrok-cli or USB devices."""

    def __init__(self, scenario: str, output_dir: Path) -> None:
        self.scenario = scenario
        self.output_dir = output_dir
        self.events: list[str] = []
        self._csv = output_dir / "sigrok.csv"
        self._stdout = output_dir / "sigrok.stdout"
        self._stderr = output_dir / "sigrok.stderr"
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        self._stdout.write_text("SIMULATED sigrok start\n", encoding="utf-8")
        self._stderr.write_text("", encoding="utf-8")
        self.events.append("sigrok:start")

    def collect(self) -> SigrokResult:
        self.events.append("sigrok:collect")
        if not self._stdout.exists():
            self._stdout.write_text("SIMULATED sigrok not started because execution stopped early\n", encoding="utf-8")
        if not self._stderr.exists():
            self._stderr.write_text("", encoding="utf-8")
        if self.scenario != "missing-csv":
            # no-HV deterministic trace: all PWM channels remain inactive.
            self._csv.write_text("time,D0,D1,D2\n0.000000,0,0,0\n0.001000,0,0,0\n", encoding="utf-8")
        returncode = 1 if self.scenario == "sigrok-fail" else 0
        if self.scenario == "sigrok-fail":
            self._stderr.write_text("SIMULATED sigrok failure\n", encoding="utf-8")
        return SigrokResult(["SIMULATED_SIGROK", self.scenario], utc_now(), round(time.monotonic() - self._started, 3), returncode, str(self._csv), str(self._stdout), str(self._stderr), self._csv.exists(), self._csv.stat().st_size if self._csv.exists() else 0, "simulated capture failure" if returncode else None)


def wait_for_terminal_status(
    transport: CommandTransport,
    timeout_s: float,
    poll_s: float,
    before_command: Optional[Callable[[str], None]] = None,
) -> tuple[CommandResult, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_s
    observations: list[dict[str, Any]] = []
    last: Optional[CommandResult] = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if before_command is not None:
            before_command("mapcap status")
        result = transport.command("mapcap status", total_timeout_s=min(0.5, remaining), quiet_s=min(0.08, remaining))
        status = parse_status(result.response)
        observations.append({"elapsed_s": result.elapsed_s, "status": status})
        last = result
        if status and status["state"] in MAP_CAPTURE_TERMINAL_STATES:
            return result, observations
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_s, remaining))
    if last is None:
        raise BenchTestError("Не получен ответ mapcap status до absolute terminal timeout.")
    raise BenchTestError(f"MapCapture не достиг terminal state за абсолютные {timeout_s:.2f} с; последний={parse_status(last.response)}")


def find_sigrok_cli(explicit_path: Optional[str]) -> str:
    candidates = [explicit_path, os.environ.get("SIGROK_CLI_PATH"), r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe", str(Path(__file__).resolve().parents[1] / "tools" / "sigrok-cli" / "sigrok-cli.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which("sigrok-cli") or shutil.which("sigrok-cli.exe")
    if found:
        return found
    raise BenchTestError("sigrok-cli не найден. Укажите --sigrok-cli или SIGROK_CLI_PATH.")


def require_safety_confirmations(args: argparse.Namespace) -> None:
    required = {
        "--confirm-dc-link-disconnected": args.confirm_dc_link_disconnected,
        "--confirm-pc4-zero": args.confirm_pc4_zero,
        "--confirm-sd-high": args.confirm_sd_high,
        "--confirm-sigrok-connected": args.confirm_sigrok_connected,
    }
    missing = [flag for flag, present in required.items() if not present]
    if missing:
        raise BenchTestError("Реальный запуск заблокирован. После физического preflight укажите: " + " ".join(missing))


def _run_pipeline(
    args: argparse.Namespace,
    transport: CommandTransport,
    capture: CaptureBackend,
    output_dir: Path,
    execution_mode: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], Optional[SigrokResult], dict[str, CommandResult]]:
    """Shared production execution/evaluator pipeline for physical and simulated backends."""
    contract = get_profile_contract(args.profile_id)
    commands: dict[str, CommandResult] = {}
    poll: list[dict[str, Any]] = []
    capture_result: Optional[SigrokResult] = None
    orchestration_events: list[str] = []

    def send(command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> CommandResult:
        orchestration_events.append(f"uart:{command}")
        return transport.command(command, total_timeout_s=total_timeout_s, quiet_s=quiet_s)

    stage = "PRECHECK"
    verdict: dict[str, Any] = {"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL", "failure_stage": stage, "failure_reason": "not_started"}
    transport.open()
    try:
        for command, key in (("sysinfo", "sysinfo"), ("p?", "pwm_before"), ("pdump", "pdump_before")):
            commands[key] = send(command)
        commands["adc_before"] = [send("a") for _ in range(args.vbus_samples)]
        for command, key in (("c", "calibration"), ("enc", "encoder"), ("mapcap status", "status_before")):
            commands[key] = send(command)
        preflight = parse_adc_raws([cr.response for cr in commands["adc_before"]])
        initial = parse_status(commands["status_before"].response)
        if not has_calibration_ok(commands["calibration"].response):
            raise BenchTestError("Калибровка `c` не подтвердила offsets; mapcap arm запрещён.")
        if not has_encoder_ok(commands["encoder"].response):
            raise BenchTestError("`enc` не подтвердил err=0; mapcap arm запрещён.")
        preflight_values = [raw["raw_vbus"] for raw in preflight] if preflight else []
        preflight_median = median(preflight_values)
        preflight_max = max(preflight_values) if preflight_values else None
        if (preflight is None or preflight_median is None
                or preflight_median > int(contract["nohv_max_raw_vbus"])
                or preflight_max is None
                or preflight_max > int(contract["nohv_max_raw_vbus_hard"])):
            raise BenchTestError("Preflight `a` (N сэмплов) не доказал no-HV raw VBUS по статистическому контракту; mapcap arm запрещён.")
        if not initial or initial["state"] != MAP_CAPTURE_IDLE or initial["frames"] != 0 or initial["avail"] != 0:
            raise BenchTestError("Начальный mapcap status не IDLE/empty в расширенном контракте.")

        stage = "ARM"
        commands["arm"] = send(f"mcarm={args.profile_id}")
        if not has_arm_ok(commands["arm"].response):
            raise BenchTestError("mcarm не вернул cap>0 и rc=0; mapcap run не отправлен.")
        commands["status_armed"] = send("mapcap status")
        armed = parse_status(commands["status_armed"].response)
        if not armed or armed["state"] != MAP_CAPTURE_ARMED or armed["frames"] != 0:
            raise BenchTestError("После mcarm не получено ARMED в расширенном status-контракте.")

        stage = "CAPTURE_START"
        orchestration_events.append("capture:start")
        capture.start()  # Must precede mapcap run in both modes.
        if execution_mode == "PHYSICAL":
            time.sleep(args.capture_warmup_seconds)
        stage = "RUN"
        commands["run"] = send("mapcap run", total_timeout_s=1.5, quiet_s=0.30)
        if not has_run_ok(commands["run"].response):
            raise BenchTestError("mapcap run не вернул rc=0; terminal polling запрещён.")
        stage = "TERMINAL_POLL"
        commands["status_terminal"], poll = wait_for_terminal_status(transport, args.terminal_timeout_seconds, args.terminal_poll_seconds, before_command=lambda command: orchestration_events.append(f"uart:{command}"))
        commands["drain"] = send("mapcap drain")
        commands["pwm_terminal"] = send("p?")
        commands["pdump_terminal"] = send("pdump")
        orchestration_events.append("capture:collect")
        capture_result = capture.collect()

        stage = "VERDICT"
        verdict = evaluate_test(commands["arm"].response, commands["run"].response, [cr.response for cr in commands["adc_before"]], commands["status_terminal"].response, commands["drain"].response, contract)
        verdict["sigrok_capture_returncode_zero"] = bool(capture_result.returncode == 0)
        verdict["sigrok_csv_exists"] = capture_result.csv_exists
        verdict["sigrok_csv_size_bytes"] = capture_result.csv_size_bytes
        if not verdict["sigrok_capture_returncode_zero"] or not verdict["sigrok_csv_exists"]:
            verdict.update({"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL", "failure_stage": "SIGROK_EVIDENCE", "failure_reason": "sigrok did not return rc=0 with a CSV artifact"})
        elif verdict["automation"] != "PASS":
            verdict.update({"failure_stage": "VERDICT", "failure_reason": "one or more no-HV UART acceptance checks failed"})
        if execution_mode == "PHYSICAL" and verdict["automation"] == "PASS" and args.clear_fault_after_evidence:
            commands["fault_clear"] = send("f")
            commands["pwm_after_clear"] = send("p?")
            commands["pdump_after_clear"] = send("pdump")
    except Exception as exc:
        verdict = {"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL", "failure_stage": stage, "failure_reason": str(exc)}
    finally:
        transport.close()
        # A software-HIL failure before capture start still writes deterministic
        # simulated evidence, preserving the same evidence-shape for audit.
        if execution_mode == "SIMULATED" and capture_result is None:
            orchestration_events.append("capture:collect")
            capture_result = capture.collect()

    if execution_mode == "SIMULATED":
        # Simulation may prove orchestration only; it never claims physical scope/final PASS.
        verdict["scope"] = "NOT_APPLICABLE"
        verdict["final"] = "SIMULATED"
        verdict["fault_clear_skipped"] = "simulation never emits physical fault-clear command"
    elif verdict["automation"] == "PASS" and args.clear_fault_after_evidence:
        verdict["fault_clear_skipped"] = "physical fault clear executed after evidence"
    else:
        verdict["fault_clear_skipped"] = "fault remains for manual review unless explicit physical post-evidence operation is approved"

    verdict["command_sequence"] = list(transport.command_sequence)
    verdict["capture_events"] = list(capture.events)
    verdict["orchestration_events"] = orchestration_events
    return verdict, poll, capture_result, commands


def _execute(args: argparse.Namespace, simulated: bool) -> int:
    if args.capture_seconds <= 0 or args.capture_warmup_seconds < 0:
        raise BenchTestError("Длительность capture должна быть >0, а warmup должен быть >=0.")
    if args.terminal_timeout_seconds <= 0 or args.terminal_poll_seconds <= 0:
        raise BenchTestError("Terminal timeout и polling interval должны быть положительными.")
    if args.vbus_samples < 1:
        raise BenchTestError("Число сэмплов VBUS должно быть >=1.")
    contract = get_profile_contract(args.profile_id)
    if simulated:
        if not args.simulate_sigrok:
            raise BenchTestError("Software-HIL требует --simulate-sigrok вместе с --simulate-uart.")
        output_dir = args.output_dir or default_output_dir(True)
        transport: CommandTransport = SimulatedTransport(args.simulate_uart, output_dir / "uart.log")
        capture: CaptureBackend = SimulatedSigrokCapture(args.simulate_uart, output_dir)
        mode = "SIMULATED"
    else:
        if args.simulate_sigrok:
            raise BenchTestError("--simulate-sigrok допустим только вместе с --simulate-uart.")
        require_safety_confirmations(args)
        if not args.port:
            raise BenchTestError("Для реального запуска укажите --port COMx.")
        output_dir = args.output_dir or default_output_dir(False)
        transport = SerialTransport(args.port, args.baud, output_dir / "uart.log")
        capture = RealSigrokCapture(find_sigrok_cli(args.sigrok_cli), args.sigrok_driver, args.sigrok_channels, args.sigrok_rate_hz, args.capture_seconds, output_dir)
        mode = "PHYSICAL"
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "metadata.json", {
        "started_utc": utc_now(), "execution": {"mode": mode, "scenario": args.simulate_uart if simulated else None},
        "profile_id": args.profile_id, "profile_contract": dict(contract),
        "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
    })
    verdict, poll, capture_result, commands = _run_pipeline(args, transport, capture, output_dir, mode)
    summary = {
        "finished_utc": utc_now(),
        "execution": {"mode": mode, "scenario": args.simulate_uart if simulated else None},
        "verdict": verdict,
        "terminal_poll": poll,
        "commands": {"sequence": transport.command_sequence, "responses": {
            key: ([asdict(item) for item in value] if isinstance(value, list) else asdict(value))
            for key, value in commands.items()}},
        "capture_events": capture.events,
        "orchestration_events": verdict["orchestration_events"],
        "sigrok": asdict(capture_result) if capture_result else None,
        "artifacts": {"output_dir": str(output_dir), "uart_log": str(output_dir / "uart.log"), "metadata": str(output_dir / "metadata.json"), "summary": str(output_dir / "summary.json")},
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps({"mode": mode, "automation": verdict["automation"], "scope": verdict["scope"], "final": verdict["final"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if verdict["automation"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UART + sigrok automation для no-HV MapCapture test №2.")
    parser.add_argument("--port", help="Физический COM-порт STM32 UART, например COM15")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sigrok-cli")
    parser.add_argument("--sigrok-driver", default=DEFAULT_SIGROK_DRIVER)
    parser.add_argument("--sigrok-channels", type=parse_channels, default=list(DEFAULT_SIGROK_CHANNELS))
    parser.add_argument("--sigrok-rate-hz", type=int, default=DEFAULT_SIGROK_RATE_HZ)
    parser.add_argument("--capture-seconds", type=float, default=1.5)
    # fx2lafw (Cypress FX2) device limit is ~160 ms of capture regardless of rate
    # ("Device only sent N samples"). The PWM service burst must fire within the
    # first 160 ms, so the warmup before `mapcap run` must stay small (0.05 s
    # verified on the bench, 25.08.2026). See TZ_BENCH_TEST3_SIGROK_WARMUP.md.
    parser.add_argument("--capture-warmup-seconds", type=float, default=0.05)
    parser.add_argument("--terminal-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--terminal-poll-seconds", type=float, default=0.05)
    parser.add_argument("--profile-id", type=int, default=APPROVED_TEST3_PROFILE_ID)
    parser.add_argument("--vbus-samples", type=int, default=DEFAULT_VBUS_SAMPLES,
                        help="Число сэмплов `a` для статистического no-HV гейта VBUS (default: %(default)s)")
    parser.add_argument("--clear-fault-after-evidence", action="store_true")
    parser.add_argument("--confirm-dc-link-disconnected", action="store_true")
    parser.add_argument("--confirm-pc4-zero", action="store_true")
    parser.add_argument("--confirm-sd-high", action="store_true")
    parser.add_argument("--confirm-sigrok-connected", action="store_true")
    parser.add_argument("--simulate-uart", choices=SIMULATION_SCENARIOS, help="Software-HIL scenario; never opens COM or hardware")
    parser.add_argument("--simulate-sigrok", action="store_true", help="Use deterministic simulated sigrok evidence; required with --simulate-uart")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--scan-sigrok", action="store_true")
    return parser


def handle_utility_modes(args: argparse.Namespace) -> Optional[int]:
    if args.list_ports:
        if serial is None:
            raise BenchTestError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        for item in serial.tools.list_ports.comports():
            print(f"{item.device}\t{item.description}\t{item.hwid}")
        return 0
    if args.scan_sigrok:
        return subprocess.run([find_sigrok_cli(args.sigrok_cli), "--driver", args.sigrok_driver, "--scan"], text=True).returncode
    if args.dry_run:
        contract = get_profile_contract(args.profile_id)
        print(json.dumps({"mode": "dry-run", "profile_id": args.profile_id, "profile_contract": dict(contract), "terminal_timeout_seconds": args.terminal_timeout_seconds, "terminal_poll_seconds": args.terminal_poll_seconds, "execution_paths": {"physical": "SerialTransport + RealSigrokCapture", "simulated": "SimulatedTransport + SimulatedSigrokCapture"}, "safety_note": "dry-run does not access COM, ST-Link, sigrok or the bench"}, ensure_ascii=False, indent=2))
        return 0
    return None


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        utility = handle_utility_modes(args)
        if utility is not None:
            return utility
        return _execute(args, simulated=bool(args.simulate_uart))
    except BenchTestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Прервано оператором. Fault не очищался автоматически.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
