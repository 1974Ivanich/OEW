#!/usr/bin/env python3
"""ПК-3: автоматизация test №2 MapCapture no-HV.

Скрипт предназначен только для временного диагностического образа test №2.
Он собирает полный UART-лог, управляет ограниченным цифровым захватом через
sigrok-cli и формирует machine-readable summary.json.

Safety boundary:
* Скрипт не прошивает МК и не подаёт питание на DC-link.
* Запуск реального сценария требует трёх явных подтверждений оператора.
* При любом нецелевом ответе UART скрипт НЕ отправляет `f` и оставляет fault
  для ручной проверки. `--clear-fault-after-evidence` является явным opt-in.
* Скрипт не выполняет FOC/V/f/autotune, mapcap build или любые energise-команды.
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
from typing import Any, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # Позволяет запускать --dry-run/юнит-тесты без pyserial.
    serial = None


DEFAULT_PROFILE_ID = 1398361684  # 0x53594E54, "SYNT"
DEFAULT_SIGROK_DRIVER = "fx2lafw"
DEFAULT_SIGROK_RATE_HZ = 8_000_000
DEFAULT_SIGROK_CHANNELS = tuple(f"D{i}" for i in range(12))

ARM_RE = re.compile(r"@MC:ARM:cap=(?P<cap>\d+):rc=(?P<rc>-?\d+)")
RUN_RE = re.compile(r"@MC:RUN:rc=(?P<rc>-?\d+)")
STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)"
)
DRAIN_RE = re.compile(r"@MC:DRAIN:records=(?P<records>\d+)")
ENC_ERR_RE = re.compile(r"(?:^|:)err=(?P<err>-?\d+)(?=$|:|\r|\n)")
CALIBRATION_OK_RE = re.compile(
    r"@ADC:CAL:offset_i1=(?P<i1>\d+):offset_i2=(?P<i2>\d+):offset_ires=(?P<ires>\d+)"
)


class BenchTestError(RuntimeError):
    """Expected validation or hardware-access failure."""


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


def find_sigrok_cli(explicit_path: Optional[str]) -> str:
    """Use the same lookup order as nucleo_debug_tool.py."""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    env_path = os.environ.get("SIGROK_CLI_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.append(r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe")
    repo_path = Path(__file__).resolve().parents[1] / "tools" / "sigrok-cli" / "sigrok-cli.exe"
    candidates.append(str(repo_path))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which("sigrok-cli") or shutil.which("sigrok-cli.exe")
    if found:
        return found
    raise BenchTestError(
        "sigrok-cli не найден. Укажите --sigrok-cli или SIGROK_CLI_PATH, "
        "либо установите sigrok-cli."
    )


def rate_to_sigrok(rate_hz: int) -> str:
    if rate_hz <= 0:
        raise BenchTestError("Частота sigrok должна быть положительной.")
    if rate_hz % 1_000_000 == 0:
        return f"{rate_hz // 1_000_000}m"
    if rate_hz % 1_000 == 0:
        return f"{rate_hz // 1_000}k"
    return str(rate_hz)


def last_match(pattern: re.Pattern[str], text: str) -> Optional[re.Match[str]]:
    matches = list(pattern.finditer(text))
    return matches[-1] if matches else None


def parse_status(text: str) -> Optional[dict[str, int]]:
    match = last_match(STATUS_RE, text)
    if not match:
        return None
    return {key: int(value) for key, value in match.groupdict().items()}


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


def evaluate_test(arm_text: str, run_text: str, status_text: str, drain_text: str) -> dict[str, Any]:
    """Return deterministic verdict for the no-HV target behaviour.

    At PC4=0 only terminal -11 (ADC lower rail) or -12 (VBUS lower limit) is
    acceptable. The script intentionally treats every other terminal condition
    as a failure requiring investigation rather than trying to recover.
    """
    status = parse_status(status_text)
    records = parse_drain_records(drain_text)
    checks = {
        "arm_rc_zero": has_arm_ok(arm_text),
        "run_rc_zero": has_run_ok(run_text),
        "status_parsed": status is not None,
        "faulted_state": bool(status and status["state"] == 5),
        "expected_terminal": bool(status and status["term"] in (-11, -12)),
        "zero_frames": bool(status and status["frames"] == 0),
        "zero_dropped": bool(status and status["dropped"] == 0),
        "zero_available": bool(status and status["avail"] == 0),
        "drain_parsed": records is not None,
        "zero_drain_records": records == 0,
        "no_record_lines": "@MC:REC:" not in drain_text,
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "status": status,
        "drain_records": records,
        "expected_terminals": [-11, -12],
    }


class UartLogger:
    def __init__(self, port: str, baud: int, log_path: Path, timeout_s: float):
        if serial is None:
            raise BenchTestError("Модуль pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        self._port = port
        self._baud = baud
        self._timeout_s = timeout_s
        self._log_path = log_path
        self._serial: Any = None
        self._log_file: Any = None

    def __enter__(self) -> "UartLogger":
        self._log_file = self._log_path.open("w", encoding="utf-8", newline="\n")
        self._write_log("META", f"port={self._port}; baud={self._baud}")
        try:
            self._serial = serial.Serial(self._port, self._baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:
            self._log_file.close()
            raise BenchTestError(f"Не удалось открыть {self._port} @ {self._baud}: {exc}") from exc
        time.sleep(0.25)
        self.read_quiet(total_timeout_s=0.5, quiet_s=0.15, label="BOOT")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self._serial is not None:
                self._serial.close()
        finally:
            if self._log_file is not None:
                self._log_file.close()

    def _write_log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n")
        self._log_file.write(text)
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def read_quiet(self, total_timeout_s: float, quiet_s: float, label: str) -> str:
        deadline = time.monotonic() + total_timeout_s
        last_data_at = time.monotonic()
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            waiting = self._serial.in_waiting
            data = self._serial.read(waiting or 1)
            if data:
                chunks.append(data)
                last_data_at = time.monotonic()
            elif chunks and (time.monotonic() - last_data_at) >= quiet_s:
                break
            else:
                time.sleep(0.02)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if text:
            self._write_log(label, text)
        return text

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> CommandResult:
        started = utc_now()
        started_monotonic = time.monotonic()
        # Не очищаем буфер: фоновые сообщения тоже являются evidence.
        residual = self.read_quiet(total_timeout_s=0.2, quiet_s=0.05, label="RX_BEFORE_COMMAND")
        if residual:
            self._write_log("NOTE", "Фоновые UART-данные сохранены до команды; они не удалялись.")
        payload = (command + "\r\n").encode("ascii")
        self._write_log("TX", command)
        self._serial.write(payload)
        self._serial.flush()
        response = self.read_quiet(total_timeout_s=total_timeout_s, quiet_s=quiet_s, label="RX")
        return CommandResult(
            command=command,
            response=response,
            started_utc=started,
            elapsed_s=round(time.monotonic() - started_monotonic, 3),
        )


def start_sigrok_capture(
    sigrok_cli: str,
    driver: str,
    channels: list[str],
    rate_hz: int,
    duration_s: float,
    output_dir: Path,
) -> tuple[subprocess.Popen[str], list[str], Path, Path, Path, float]:
    csv_path = output_dir / "sigrok_digital.csv"
    stdout_path = output_dir / "sigrok_stdout.log"
    stderr_path = output_dir / "sigrok_stderr.log"
    cmd = [
        sigrok_cli,
        "--driver", driver,
        "--config", f"samplerate={rate_to_sigrok(rate_hz)}",
        "--channels", ",".join(channels),
        "--time", str(int(duration_s * 1000)),
        "-O", "csv",
        "-o", str(csv_path),
    ]
    stdout_file = stdout_path.open("w", encoding="utf-8", newline="\n")
    stderr_file = stderr_path.open("w", encoding="utf-8", newline="\n")
    try:
        process = subprocess.Popen(cmd, stdout=stdout_file, stderr=stderr_file, text=True)
    except Exception:
        stdout_file.close()
        stderr_file.close()
        raise
    # Закрываем родительские file descriptors: дочерний процесс уже владеет ими.
    stdout_file.close()
    stderr_file.close()
    return process, cmd, csv_path, stdout_path, stderr_path, time.monotonic()


def collect_sigrok_capture(
    process: subprocess.Popen[str],
    cmd: list[str],
    csv_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    started_monotonic: float,
    duration_s: float,
) -> SigrokResult:
    error: Optional[str] = None
    returncode: Optional[int]
    try:
        returncode = process.wait(timeout=duration_s + 65.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        returncode = process.returncode
        error = "sigrok-cli превысил допустимый timeout и был остановлен"
    return SigrokResult(
        command=cmd,
        started_utc=utc_now(),
        elapsed_s=round(time.monotonic() - started_monotonic, 3),
        returncode=returncode,
        csv_path=str(csv_path),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        csv_exists=csv_path.exists(),
        csv_size_bytes=csv_path.stat().st_size if csv_path.exists() else 0,
        error=error,
    )


def parse_channels(value: str) -> list[str]:
    channels = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not channels or any(not re.fullmatch(r"D(?:1[0-5]|[0-9])", channel) for channel in channels):
        raise argparse.ArgumentTypeError("Каналы должны быть списком D0..D15 через запятую.")
    return channels


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ПК-3: UART + sigrok automation для no-HV MapCapture test №2."
    )
    parser.add_argument("--port", help="COM-порт STM32, например COM4")
    parser.add_argument("--baud", type=int, default=115200, help="UART baud rate (default: 115200)")
    parser.add_argument("--output-dir", type=Path, help="Папка evidence; по умолчанию campaign_raw/test2_nohv_<UTC>")
    parser.add_argument("--sigrok-cli", help="Явный путь к sigrok-cli.exe")
    parser.add_argument("--sigrok-driver", default=DEFAULT_SIGROK_DRIVER, help="sigrok driver (default: fx2lafw)")
    parser.add_argument("--sigrok-channels", type=parse_channels, default=list(DEFAULT_SIGROK_CHANNELS), help="CSV цифровых каналов, default D0..D11")
    parser.add_argument("--sigrok-rate-hz", type=int, default=DEFAULT_SIGROK_RATE_HZ, help="Частота логического захвата (default: 8000000)")
    parser.add_argument("--capture-seconds", type=float, default=1.5, help="Длительность sigrok-capture (default: 1.5)")
    parser.add_argument("--capture-warmup-seconds", type=float, default=0.30, help="Задержка после запуска sigrok перед mapcap run (default: 0.30)")
    parser.add_argument("--profile-id", type=int, default=DEFAULT_PROFILE_ID, help="MapCapture profile ID (default: SYNT)")
    parser.add_argument("--clear-fault-after-evidence", action="store_true", help="После PASS отправить f и зафиксировать p?/pdump; по умолчанию fault не очищается")
    parser.add_argument("--confirm-dc-link-disconnected", action="store_true", help="Подтверждаю: обе DC-link шины измерены <1 V и отсоединены")
    parser.add_argument("--confirm-pc4-zero", action="store_true", help="Подтверждаю: на PC4 не подан внешний VBUS/имитатор")
    parser.add_argument("--confirm-sd-high", action="store_true", help="Подтверждаю: SD1 и SD2 проверены high")
    parser.add_argument("--dry-run", action="store_true", help="Проверить план и пути без UART/sigrok/оборудования")
    parser.add_argument("--list-ports", action="store_true", help="Показать доступные COM-порты и завершить работу")
    parser.add_argument("--scan-sigrok", action="store_true", help="Выполнить sigrok --scan и завершить работу")
    return parser


def require_safety_confirmations(args: argparse.Namespace) -> None:
    missing = []
    if not args.confirm_dc_link_disconnected:
        missing.append("--confirm-dc-link-disconnected")
    if not args.confirm_pc4_zero:
        missing.append("--confirm-pc4-zero")
    if not args.confirm_sd_high:
        missing.append("--confirm-sd-high")
    if missing:
        raise BenchTestError(
            "Реальный запуск заблокирован. После физического preflight укажите: " + " ".join(missing)
        )


def default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    return Path("campaign_raw") / f"test2_nohv_{stamp}"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def execute_test(args: argparse.Namespace) -> int:
    if args.capture_seconds <= 0 or args.capture_warmup_seconds < 0:
        raise BenchTestError("Длительность capture должна быть >0, а warmup должен быть >=0.")
    require_safety_confirmations(args)
    if not args.port:
        raise BenchTestError("Для реального запуска укажите --port COMx.")

    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=False)
    sigrok_cli = find_sigrok_cli(args.sigrok_cli)
    metadata = {
        "started_utc": utc_now(),
        "script": str(Path(__file__).resolve()),
        "profile_id": args.profile_id,
        "safety_confirmations": {
            "dc_link_disconnected": args.confirm_dc_link_disconnected,
            "pc4_zero": args.confirm_pc4_zero,
            "sd_high": args.confirm_sd_high,
        },
        "arguments": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in vars(args).items()
        },
        "sigrok_cli": sigrok_cli,
    }
    write_json(output_dir / "metadata.json", metadata)

    command_results: dict[str, CommandResult] = {}
    sigrok_result: Optional[SigrokResult] = None
    active_sigrok_process: Optional[subprocess.Popen[str]] = None
    verdict: dict[str, Any] = {"verdict": "FAIL", "checks": {}, "reason": "not_started"}

    try:
        with UartLogger(args.port, args.baud, output_dir / "uart.log", timeout_s=0.05) as uart:
            # Preflight responses are evidence. The script only hard-gates c/enc/initial status.
            for command, key in (("sysinfo", "sysinfo"), ("p?", "pwm_before"), ("pdump", "pdump_before"), ("a", "adc_before"), ("c", "calibration"), ("enc", "encoder"), ("mapcap status", "status_before")):
                command_results[key] = uart.command(command)

            if not has_calibration_ok(command_results["calibration"].response):
                raise BenchTestError("Калибровка `c` не вернула строку offsets без @ADC:CAL:FAIL; mapcap arm запрещён.")
            if not has_encoder_ok(command_results["encoder"].response):
                raise BenchTestError("`enc` не подтвердил err=0; тест остановлен до arm.")
            initial_status = parse_status(command_results["status_before"].response)
            if not initial_status or initial_status["state"] != 0 or initial_status["frames"] != 0 or initial_status["avail"] != 0:
                raise BenchTestError("Начальный mapcap status не IDLE/empty; test №2 не запускается.")

            arm_command = f"mcarm={args.profile_id}"
            command_results["arm"] = uart.command(arm_command)
            if not has_arm_ok(command_results["arm"].response):
                raise BenchTestError("mcarm не вернул cap>0 и rc=0; mapcap run не отправлен.")
            command_results["status_armed"] = uart.command("mapcap status")
            armed_status = parse_status(command_results["status_armed"].response)
            if not armed_status or armed_status["state"] != 1 or armed_status["frames"] != 0:
                raise BenchTestError("После mcarm не получено ожидаемое состояние ARMED; run не отправлен.")

            active_sigrok_process, cmd, csv_path, stdout_path, stderr_path, capture_started = start_sigrok_capture(
                sigrok_cli=sigrok_cli,
                driver=args.sigrok_driver,
                channels=args.sigrok_channels,
                rate_hz=args.sigrok_rate_hz,
                duration_s=args.capture_seconds,
                output_dir=output_dir,
            )
            time.sleep(args.capture_warmup_seconds)
            command_results["run"] = uart.command("mapcap run", total_timeout_s=1.5, quiet_s=0.30)
            # Waiting lets the bounded service path terminate before evidence commands.
            time.sleep(1.0)
            command_results["status_terminal"] = uart.command("mapcap status")
            command_results["drain"] = uart.command("mapcap drain")
            command_results["pwm_terminal"] = uart.command("p?")
            command_results["pdump_terminal"] = uart.command("pdump")

            sigrok_result = collect_sigrok_capture(
                active_sigrok_process, cmd, csv_path, stdout_path, stderr_path, capture_started, args.capture_seconds
            )
            active_sigrok_process = None
            verdict = evaluate_test(
                command_results["arm"].response,
                command_results["run"].response,
                command_results["status_terminal"].response,
                command_results["drain"].response,
            )
            verdict["sigrok_capture_returncode_zero"] = bool(sigrok_result.returncode == 0)
            verdict["sigrok_csv_exists"] = sigrok_result.csv_exists
            verdict["sigrok_csv_size_bytes"] = sigrok_result.csv_size_bytes
            verdict["manual_pwm_evidence_required"] = True
            if not verdict["sigrok_capture_returncode_zero"] or not verdict["sigrok_csv_exists"]:
                verdict["verdict"] = "FAIL"

            if verdict["verdict"] == "PASS" and args.clear_fault_after_evidence:
                command_results["fault_clear"] = uart.command("f")
                command_results["pwm_after_clear"] = uart.command("p?")
                command_results["pdump_after_clear"] = uart.command("pdump")
            elif verdict["verdict"] != "PASS":
                verdict["fault_clear_skipped"] = "target verdict not reached; fault remains for manual review"
            else:
                verdict["fault_clear_skipped"] = "opt-in flag not supplied; fault remains for manual review"
    except Exception as exc:
        verdict = {
            "verdict": "FAIL",
            "reason": str(exc),
            "manual_pwm_evidence_required": True,
        }
    finally:
        if active_sigrok_process is not None and active_sigrok_process.poll() is None:
            active_sigrok_process.kill()
            try:
                active_sigrok_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    summary = {
        "finished_utc": utc_now(),
        "verdict": verdict,
        "commands": {key: asdict(value) for key, value in command_results.items()},
        "sigrok": asdict(sigrok_result) if sigrok_result else None,
        "artifacts": {
            "output_dir": str(output_dir),
            "uart_log": str(output_dir / "uart.log"),
            "metadata": str(output_dir / "metadata.json"),
            "summary": str(output_dir / "summary.json"),
        },
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps({"verdict": verdict.get("verdict"), "output_dir": str(output_dir), "summary": str(output_dir / "summary.json")}, ensure_ascii=False))
    return 0 if verdict.get("verdict") == "PASS" else 1


def handle_utility_modes(args: argparse.Namespace) -> Optional[int]:
    if args.list_ports:
        if serial is None:
            raise BenchTestError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            print("COM-порты не найдены.")
        for item in ports:
            print(f"{item.device}\t{item.description}\t{item.hwid}")
        return 0
    if args.scan_sigrok:
        cli = find_sigrok_cli(args.sigrok_cli)
        result = subprocess.run([cli, "--driver", args.sigrok_driver, "--scan"], text=True)
        return result.returncode
    if args.dry_run:
        output_dir = args.output_dir or default_output_dir()
        plan = {
            "mode": "dry-run",
            "profile_id": args.profile_id,
            "sigrok_driver": args.sigrok_driver,
            "sigrok_channels": args.sigrok_channels,
            "sigrok_rate": rate_to_sigrok(args.sigrok_rate_hz),
            "capture_seconds": args.capture_seconds,
            "output_dir": str(output_dir),
            "uart_sequence": ["sysinfo", "p?", "pdump", "a", "c", "enc", "mapcap status", f"mcarm={args.profile_id}", "mapcap status", "mapcap run", "mapcap status", "mapcap drain", "p?", "pdump"],
            "safety_note": "dry-run не обращается к COM, sigrok или стенду",
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        utility_result = handle_utility_modes(args)
        if utility_result is not None:
            return utility_result
        return execute_test(args)
    except BenchTestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Прервано оператором. Fault не очищался автоматически.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
