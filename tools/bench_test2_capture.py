#!/usr/bin/env python3
"""ПК-3: UART + sigrok evidence automation для no-HV MapCapture test №2.

Safety boundary:
* Скрипт не прошивает MCU и не включает DC-link.
* Реальный запуск требует явного подтверждения no-HV preflight, SD и sigrok.
* Скрипт не выполняет FOC/V/f/autotune/mapcap build и не отправляет `f` без
  явного opt-in после automation PASS.
* Автоматизация подтверждает только UART/sigrok transport evidence. Форма PWM
  в CSV подтверждается оператором отдельным scope verdict.
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
from types import MappingProxyType
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # Позволяет тестировать parser без pyserial.
    serial = None


APPROVED_TEST2_PROFILE_ID = 1398361684  # 0x53594E54, "SYNT"; only supported profile.
DEFAULT_SIGROK_DRIVER = "fx2lafw"
DEFAULT_SIGROK_RATE_HZ = 8_000_000
DEFAULT_SIGROK_CHANNELS = tuple(f"D{i}" for i in range(12))

# Versioned fault-detail / ADC-status values from map_capture.h and adc.h.
# Never infer VBUS cause from terminal status alone.
MAP_CAPTURE_FAULTED = 5
MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW = 7
ADC_FRAME_WINDOW_INVALID = 7
MAP_CAPTURE_TERMINAL_STATES = (3, 4, 5)  # COMPLETE, ABORTED, FAULTED

# The script supports only profiles whose acceptance contract is explicitly
# duplicated and reviewed here. Both mappings are read-only at runtime; these
# values are metadata plus the exact acceptance envelope for test №2.
PROFILE_CONTRACTS: Mapping[int, Mapping[str, int | str]] = MappingProxyType({
    APPROVED_TEST2_PROFILE_ID: MappingProxyType({
        "name": "SYNT",
        "max_abs_shunt_ma": 10000,
        "min_vbus_mv": 1000,
        "max_vbus_mv": 70000,
        "nohv_max_raw_vbus": 9,
        "expected_adc_status": ADC_FRAME_WINDOW_INVALID,
    })
})

ARM_RE = re.compile(r"@MC:ARM:cap=(?P<cap>\d+):rc=(?P<rc>-?\d+)")
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
ADC_RAW_RE = re.compile(
    r"@ADC:I1=(?P<i1>\d+):I2=(?P<i2>\d+):Ires=(?P<ires>\d+):VBUS=(?P<raw_vbus>\d+)"
)
DRAIN_RE = re.compile(r"@MC:DRAIN:records=(?P<records>\d+)")
ENC_ERR_RE = re.compile(r"(?:^|:)err=(?P<err>-?\d+)(?=$|:|\r|\n)")
CALIBRATION_OK_RE = re.compile(
    r"@ADC:CAL:offset_i1=(?P<i1>\d+):offset_i2=(?P<i2>\d+):offset_ires=(?P<ires>\d+)"
)


class BenchTestError(RuntimeError):
    """Expected validation, transport or hardware-access failure."""


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def last_match(pattern: re.Pattern[str], text: str) -> Optional[re.Match[str]]:
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def parse_status(text: str) -> Optional[dict[str, int]]:
    """Parse only the expanded mapcap status contract; legacy status fails closed."""
    match = last_match(STATUS_RE, text)
    return {key: int(value) for key, value in match.groupdict().items()} if match else None


def parse_adc_raw(text: str) -> Optional[dict[str, int]]:
    match = last_match(ADC_RAW_RE, text)
    return {key: int(value) for key, value in match.groupdict().items()} if match else None


def parse_drain_records(text: str) -> Optional[int]:
    match = last_match(DRAIN_RE, text)
    return int(match.group("records")) if match else None


def has_arm_ok(text: str) -> bool:
    match = last_match(ARM_RE, text)
    return bool(match and int(match.group("cap")) > 0 and int(match.group("rc")) == 0)


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
    preflight_adc_text: str,
    status_text: str,
    drain_text: str,
    profile_contract: Mapping[str, int | str],
) -> dict[str, Any]:
    """Return a fail-closed three-layer verdict for test №2.

    Automation PASS is possible only for the explicit VBUS_LOW path. Thus all
    -11 statuses, I1/I2/VBUS_HIGH details, missing extended fields, timeout
    consequences, and absent raw-VBUS evidence are FAIL. A terminal null frame
    cannot pass merely because its diagnostic raw VBUS is zero.
    """
    status = parse_status(status_text)
    preflight_adc = parse_adc_raw(preflight_adc_text)
    records = parse_drain_records(drain_text)
    nohv_max_raw_vbus = int(profile_contract["nohv_max_raw_vbus"])
    min_vbus_mv = int(profile_contract["min_vbus_mv"])
    max_abs_shunt_ma = int(profile_contract["max_abs_shunt_ma"])
    expected_adc_status = int(profile_contract["expected_adc_status"])
    checks = {
        "arm_rc_zero": has_arm_ok(arm_text),
        "run_rc_zero": has_run_ok(run_text),
        "preflight_adc_parsed": preflight_adc is not None,
        "preflight_raw_vbus_nohv": bool(preflight_adc and preflight_adc["raw_vbus"] <= nohv_max_raw_vbus),
        "status_parsed_extended_contract": status is not None,
        "faulted_state": bool(status and status["state"] == MAP_CAPTURE_FAULTED),
        "terminal_is_limit_exceeded": bool(status and status["term"] == -12),
        "detail_is_vbus_low": bool(status and status["detail"] == MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW),
        "terminal_adc_window_invalid": bool(status and status["adc_status"] == expected_adc_status),
        "terminal_raw_vbus_nohv": bool(status and status["raw_vbus"] <= nohv_max_raw_vbus),
        "terminal_vbus_below_profile_min": bool(status and 0 <= status["vbus_mv"] < min_vbus_mv),
        "terminal_i1_within_limit": bool(status and abs(status["i1_ma"]) <= max_abs_shunt_ma),
        "terminal_i2_within_limit": bool(status and abs(status["i2_ma"]) <= max_abs_shunt_ma),
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
        "status": status,
        "preflight_adc": preflight_adc,
        "drain_records": records,
        "expected_nohv_contract": {
            "term": -12,
            "detail": "VBUS_LOW",
            "detail_code": MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW,
            "adc_status": "WINDOW_INVALID",
            "adc_status_code": expected_adc_status,
            "max_raw_vbus": nohv_max_raw_vbus,
            "min_vbus_mv_exclusive": min_vbus_mv,
            "max_abs_shunt_ma": max_abs_shunt_ma,
            "max_vbus_mv_metadata_only": int(profile_contract["max_vbus_mv"]),
        },
    }


def find_sigrok_cli(explicit_path: Optional[str]) -> str:
    candidates: list[str] = []
    if explicit_path:
        candidates.append(explicit_path)
    if os.environ.get("SIGROK_CLI_PATH"):
        candidates.append(os.environ["SIGROK_CLI_PATH"])
    candidates.extend([
        r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe",
        str(Path(__file__).resolve().parents[1] / "tools" / "sigrok-cli" / "sigrok-cli.exe"),
    ])
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("sigrok-cli") or shutil.which("sigrok-cli.exe")
    if found:
        return found
    raise BenchTestError("sigrok-cli не найден. Укажите --sigrok-cli или SIGROK_CLI_PATH.")


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
    if not channels or any(not re.fullmatch(r"D(?:1[0-5]|[0-9])", channel) for channel in channels):
        raise argparse.ArgumentTypeError("Каналы должны быть списком D0..D15 через запятую.")
    return channels


class UartLogger:
    def __init__(self, port: str, baud: int, log_path: Path):
        if serial is None:
            raise BenchTestError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self._serial: Any = None
        self._log_file: Any = None

    def __enter__(self) -> "UartLogger":
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"port={self.port}; baud={self.baud}")
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:
            self._log_file.close()
            raise BenchTestError(f"Не удалось открыть {self.port} @ {self.baud}: {exc}") from exc
        time.sleep(0.25)
        self.read_quiet(0.5, 0.15, "BOOT")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self._serial is not None:
                self._serial.close()
        finally:
            if self._log_file is not None:
                self._log_file.close()

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def read_quiet(self, total_timeout_s: float, quiet_s: float, label: str) -> str:
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
        started_utc = utc_now()
        started = time.monotonic()
        residual = self.read_quiet(0.2, 0.05, "RX_BEFORE_COMMAND")
        if residual:
            self._log("NOTE", "Фоновые UART-данные сохранены до команды; они не удалялись.")
        self._log("TX", command)
        self._serial.write((command + "\r\n").encode("ascii"))
        self._serial.flush()
        response = self.read_quiet(total_timeout_s, quiet_s, "RX")
        return CommandResult(command, response, started_utc, round(time.monotonic() - started, 3))


def wait_for_terminal_status(uart: UartLogger, timeout_s: float, poll_s: float) -> tuple[CommandResult, list[dict[str, Any]]]:
    """Poll only within an absolute monotonic deadline, including UART latency."""
    deadline = time.monotonic() + timeout_s
    observations: list[dict[str, Any]] = []
    last: Optional[CommandResult] = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        result = uart.command("mapcap status", total_timeout_s=min(0.5, remaining), quiet_s=min(0.08, remaining))
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


def start_sigrok_capture(cli: str, driver: str, channels: list[str], rate_hz: int, duration_s: float, output_dir: Path) -> tuple[subprocess.Popen[str], list[str], Path, Path, Path, float]:
    csv_path = output_dir / "sigrok_digital.csv"
    stdout_path = output_dir / "sigrok_stdout.log"
    stderr_path = output_dir / "sigrok_stderr.log"
    command = [cli, "--driver", driver, "--config", f"samplerate={rate_to_sigrok(rate_hz)}", "--channels", ",".join(channels), "--time", str(int(duration_s * 1000)), "-O", "csv", "-o", str(csv_path)]
    stdout_file = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_file = stderr_path.open("w", encoding="utf-8", newline="\n")
    try:
        process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file, text=True)
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise
    stdout_file.close()
    stderr_file.close()
    return process, command, csv_path, stdout_path, stderr_path, time.monotonic()


def collect_sigrok_capture(process: subprocess.Popen[str], command: list[str], csv_path: Path, stdout_path: Path, stderr_path: Path, started: float, duration_s: float) -> SigrokResult:
    error: Optional[str] = None
    try:
        returncode = process.wait(timeout=duration_s + 65.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        returncode = process.returncode
        error = "sigrok-cli превысил допустимый timeout и был остановлен"
    return SigrokResult(command, utc_now(), round(time.monotonic() - started, 3), returncode, str(csv_path), str(stdout_path), str(stderr_path), csv_path.exists(), csv_path.stat().st_size if csv_path.exists() else 0, error)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_output_dir() -> Path:
    return Path("campaign_raw") / f"test2_nohv_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%SZ')}"


def require_safety_confirmations(args: argparse.Namespace) -> None:
    required = {
        "--confirm-dc-link-disconnected": args.confirm_dc_link_disconnected,
        "--confirm-pc4-zero": args.confirm_pc4_zero,
        "--confirm-sd-high": args.confirm_sd_high,
        "--confirm-sigrok-connected": args.confirm_sigrok_connected,
    }
    missing = [flag for flag, confirmed in required.items() if not confirmed]
    if missing:
        raise BenchTestError("Реальный запуск заблокирован. После физического preflight укажите: " + " ".join(missing))


def execute_test(args: argparse.Namespace) -> int:
    if args.capture_seconds <= 0 or args.capture_warmup_seconds < 0:
        raise BenchTestError("Длительность capture должна быть >0, а warmup должен быть >=0.")
    if args.terminal_timeout_seconds <= 0 or args.terminal_poll_seconds <= 0:
        raise BenchTestError("Terminal timeout и polling interval должны быть положительными.")
    profile_contract = get_profile_contract(args.profile_id)
    require_safety_confirmations(args)
    if not args.port:
        raise BenchTestError("Для реального запуска укажите --port COMx.")

    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=False)
    sigrok_cli = find_sigrok_cli(args.sigrok_cli)
    write_json(output_dir / "metadata.json", {
        "started_utc": utc_now(),
        "script": str(Path(__file__).resolve()),
        "profile_id": args.profile_id,
        "profile_contract": dict(profile_contract),
        "sigrok_cli": sigrok_cli,
        "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
    })

    commands: dict[str, CommandResult] = {}
    poll_observations: list[dict[str, Any]] = []
    sigrok_result: Optional[SigrokResult] = None
    active_sigrok: Optional[subprocess.Popen[str]] = None
    verdict: dict[str, Any] = {"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL", "failure_stage": "PRECHECK", "failure_reason": "not_started"}
    failure_stage = "PRECHECK"
    try:
        with UartLogger(args.port, args.baud, output_dir / "uart.log") as uart:
            for command, key in (("sysinfo", "sysinfo"), ("p?", "pwm_before"), ("pdump", "pdump_before"), ("a", "adc_before"), ("c", "calibration"), ("enc", "encoder"), ("mapcap status", "status_before")):
                commands[key] = uart.command(command)
            preflight_adc = parse_adc_raw(commands["adc_before"].response)
            initial_status = parse_status(commands["status_before"].response)
            if not has_calibration_ok(commands["calibration"].response):
                raise BenchTestError("Калибровка `c` не подтвердила offsets; mapcap arm запрещён.")
            if not has_encoder_ok(commands["encoder"].response):
                raise BenchTestError("`enc` не подтвердил err=0; mapcap arm запрещён.")
            if not preflight_adc or preflight_adc["raw_vbus"] > int(profile_contract["nohv_max_raw_vbus"]):
                raise BenchTestError("Preflight `a` не доказал no-HV raw VBUS по approved profile contract; mapcap arm запрещён.")
            if not initial_status or initial_status["state"] != 0 or initial_status["frames"] != 0 or initial_status["avail"] != 0:
                raise BenchTestError("Начальный mapcap status не IDLE/empty в расширенном контракте.")

            failure_stage = "ARM"
            commands["arm"] = uart.command(f"mcarm={args.profile_id}")
            if not has_arm_ok(commands["arm"].response):
                raise BenchTestError("mcarm не вернул cap>0 и rc=0; mapcap run не отправлен.")
            commands["status_armed"] = uart.command("mapcap status")
            armed = parse_status(commands["status_armed"].response)
            if not armed or armed["state"] != 1 or armed["frames"] != 0:
                raise BenchTestError("После mcarm не получено ARMED в расширенном status-контракте.")

            failure_stage = "CAPTURE_START"
            active_sigrok, sigrok_cmd, csv_path, stdout_path, stderr_path, started = start_sigrok_capture(sigrok_cli, args.sigrok_driver, args.sigrok_channels, args.sigrok_rate_hz, args.capture_seconds, output_dir)
            time.sleep(args.capture_warmup_seconds)
            failure_stage = "RUN"
            commands["run"] = uart.command("mapcap run", total_timeout_s=1.5, quiet_s=0.30)
            failure_stage = "TERMINAL_POLL"
            commands["status_terminal"], poll_observations = wait_for_terminal_status(uart, args.terminal_timeout_seconds, args.terminal_poll_seconds)
            commands["drain"] = uart.command("mapcap drain")
            commands["pwm_terminal"] = uart.command("p?")
            commands["pdump_terminal"] = uart.command("pdump")
            sigrok_result = collect_sigrok_capture(active_sigrok, sigrok_cmd, csv_path, stdout_path, stderr_path, started, args.capture_seconds)
            active_sigrok = None

            failure_stage = "VERDICT"
            verdict = evaluate_test(commands["arm"].response, commands["run"].response, commands["adc_before"].response, commands["status_terminal"].response, commands["drain"].response, profile_contract)
            verdict["sigrok_capture_returncode_zero"] = bool(sigrok_result.returncode == 0)
            verdict["sigrok_csv_exists"] = sigrok_result.csv_exists
            verdict["sigrok_csv_size_bytes"] = sigrok_result.csv_size_bytes
            if not verdict["sigrok_capture_returncode_zero"] or not verdict["sigrok_csv_exists"]:
                verdict["automation"] = "FAIL"
                verdict["scope"] = "NOT_APPLICABLE"
                verdict["final"] = "FAIL"
                verdict["failure_stage"] = "SIGROK_EVIDENCE"
                verdict["failure_reason"] = "sigrok did not return rc=0 with a CSV artifact"
            elif verdict["automation"] != "PASS":
                verdict["failure_stage"] = "VERDICT"
                verdict["failure_reason"] = "one or more no-HV UART acceptance checks failed"

            if verdict["automation"] == "PASS" and args.clear_fault_after_evidence:
                commands["fault_clear"] = uart.command("f")
                commands["pwm_after_clear"] = uart.command("p?")
                commands["pdump_after_clear"] = uart.command("pdump")
            else:
                verdict["fault_clear_skipped"] = "fault remains for manual review unless explicit opt-in follows automation PASS"
    except Exception as exc:
        verdict = {"automation": "FAIL", "scope": "NOT_APPLICABLE", "final": "FAIL", "failure_stage": failure_stage, "failure_reason": str(exc)}
    finally:
        if active_sigrok is not None and active_sigrok.poll() is None:
            active_sigrok.kill()
            try:
                active_sigrok.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    summary = {
        "finished_utc": utc_now(),
        "verdict": verdict,
        "terminal_poll": poll_observations,
        "commands": {key: asdict(value) for key, value in commands.items()},
        "sigrok": asdict(sigrok_result) if sigrok_result else None,
        "artifacts": {"output_dir": str(output_dir), "uart_log": str(output_dir / "uart.log"), "metadata": str(output_dir / "metadata.json"), "summary": str(output_dir / "summary.json")},
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps({"automation": verdict["automation"], "scope": verdict["scope"], "final": verdict["final"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if verdict["automation"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ПК-3: UART + sigrok automation для no-HV MapCapture test №2.")
    parser.add_argument("--port", help="COM-порт STM32 UART, например COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sigrok-cli")
    parser.add_argument("--sigrok-driver", default=DEFAULT_SIGROK_DRIVER)
    parser.add_argument("--sigrok-channels", type=parse_channels, default=list(DEFAULT_SIGROK_CHANNELS))
    parser.add_argument("--sigrok-rate-hz", type=int, default=DEFAULT_SIGROK_RATE_HZ)
    parser.add_argument("--capture-seconds", type=float, default=1.5)
    parser.add_argument("--capture-warmup-seconds", type=float, default=0.30)
    parser.add_argument("--terminal-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--terminal-poll-seconds", type=float, default=0.05)
    parser.add_argument("--profile-id", type=int, default=APPROVED_TEST2_PROFILE_ID)
    parser.add_argument("--clear-fault-after-evidence", action="store_true")
    parser.add_argument("--confirm-dc-link-disconnected", action="store_true")
    parser.add_argument("--confirm-pc4-zero", action="store_true")
    parser.add_argument("--confirm-sd-high", action="store_true")
    parser.add_argument("--confirm-sigrok-connected", action="store_true")
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
        profile_contract = get_profile_contract(args.profile_id)
        print(json.dumps({
            "mode": "dry-run",
            "profile_id": args.profile_id,
            "profile_contract": dict(profile_contract),
            "terminal_timeout_seconds": args.terminal_timeout_seconds,
            "terminal_poll_seconds": args.terminal_poll_seconds,
            "nohv_contract": "term=-12 + detail=VBUS_LOW + raw/vbus/current evidence",
            "verdict_layers": {"automation": "PASS|FAIL", "scope": "PENDING until CSV review", "final": "PENDING until scope PASS"},
            "uart_sequence": ["sysinfo", "p?", "pdump", "a", "c", "enc", "mapcap status", f"mcarm={args.profile_id}", "mapcap status", "mapcap run", "poll mapcap status", "mapcap drain", "p?", "pdump"],
            "safety_note": "dry-run does not access COM, sigrok or the bench",
        }, ensure_ascii=False, indent=2))
        return 0
    return None


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        utility = handle_utility_modes(args)
        return utility if utility is not None else execute_test(args)
    except BenchTestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Прервано оператором. Fault не очищался автоматически.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
