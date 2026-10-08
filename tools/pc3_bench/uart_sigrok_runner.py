#!/usr/bin/env python3
"""Run one UART command while capturing sigrok LA evidence.

This is the generic glue between the firmware CLI and the logic analyzer:

    python tools/pc3_bench/uart_sigrok_runner.py \
        --port COM4 --baud 115200 \
        --duration 6 --channels D0,D1,D2,D3,D4 \
        --pre "c:0.3,cv:0.3,a:0.3,sysinfo:0.3" \
        --command "al:1.0" \
        --post "al off:0.3,dump:0.3" \
        --output-dir campaign_raw/run1

The tool starts a fixed-duration LA capture in a thread, then drives the
command sequence, saves the UART log, and prints a short LA summary.

Use `--no-la` to exercise only the UART path.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.sigrok_lib import (
    SigrokCaptureResult,
    SigrokError,
    SigrokSession,
    capture_fixed,
    find_fx2lafw,
)

try:
    import serial
except ImportError as exc:  # pragma: no cover - dependency handling at runtime
    raise SystemExit("pyserial is required: py -3 -m pip install pyserial") from exc


@dataclass
class CommandStep:
    command: str
    delay_after_s: float


def parse_command_spec(spec: str) -> List[CommandStep]:
    """Parse 'cmd:delay,cmd2:delay' into steps."""
    steps: List[CommandStep] = []
    if not spec:
        return steps
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            cmd, _, delay = part.rpartition(":")
            steps.append(CommandStep(cmd.strip(), float(delay)))
        else:
            steps.append(CommandStep(part, 0.3))
    return steps


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UartRecorder:
    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self._ser: Optional[serial.Serial] = None
        self._log_file = None
        self._buffer = ""

    def open(self) -> None:
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"port={self.port}; baud={self.baud}; opened={utc_now_iso()}")
        self._ser = serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1.0)
        time.sleep(0.25)
        self._drain(quiet_s=0.15, label="BOOT")

    def close(self) -> None:
        if self._ser:
            self._drain(quiet_s=0.1, label="TRAILING")
            self._ser.close()
            self._ser = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        if self._log_file is None:
            return
        self._log_file.write(f"[{utc_now_iso()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def _drain(self, quiet_s: float, label: str) -> str:
        deadline = time.monotonic() + 2.0
        last_data = time.monotonic()
        chunks: List[bytes] = []
        while self._ser and time.monotonic() < deadline:
            data = self._ser.read(self._ser.in_waiting or 1)
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
            self._buffer += text
        return text

    def command(self, step: CommandStep, until: Optional[str] = None, timeout_s: float = 2.0) -> str:
        """Send one command, optionally waiting until a marker appears."""
        if self._ser is None:
            raise RuntimeError("UART not open")
        self._drain(quiet_s=0.05, label="RX_BEFORE")
        self._log("TX", step.command)
        self._ser.write((step.command + "\r\n").encode("ascii"))
        self._ser.flush()
        deadline = time.monotonic() + timeout_s
        response = ""
        while time.monotonic() < deadline:
            chunk = self._drain(quiet_s=0.1, label="RX")
            response += chunk
            if until and until in response:
                break
            # Give the target time to produce more output.
            time.sleep(0.05)
        # Wait the requested post-command delay.
        time.sleep(max(0.0, step.delay_after_s - (time.monotonic() - (deadline - timeout_s))))
        return response


def run_la_capture(
    output_path: Path,
    duration_s: float,
    channels: List[str],
    rate_hz: int,
    result_holder: List[Optional[SigrokCaptureResult]],
) -> None:
    """Thread target: run a fixed sigrok capture."""
    try:
        dev = find_fx2lafw()
        result_holder[0] = capture_fixed(
            output_path=output_path,
            duration_s=duration_s,
            channels=channels,
            rate_hz=rate_hz,
            driver=dev.driver,
            conn=dev.conn,
        )
    except SigrokError as exc:
        result_holder[0] = exc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UART command sequence + sigrok LA capture")
    p.add_argument("--port", default=os.environ.get("OEW_UART_PORT", "COM4"), help="UART port")
    p.add_argument("--baud", type=int, default=int(os.environ.get("OEW_UART_BAUD", "115200")))
    p.add_argument("--output-dir", type=Path, required=True, help="Directory for uart.log and la_capture.sr")
    p.add_argument("--duration", type=float, default=5.0, help="LA capture duration in seconds")
    p.add_argument("--rate", type=float, default=1_000_000, help="LA sample rate in Hz")
    p.add_argument("--channels", default="D0,D1,D2,D3,D4", help="Comma-separated LA channels")
    p.add_argument("--pre", default="", help="Pre-command sequence 'cmd:delay,...'")
    p.add_argument("--command", required=True, help="Main command to send (optionally with ':delay')")
    p.add_argument("--post", default="", help="Post-command sequence 'cmd:delay,...'")
    p.add_argument("--no-la", action="store_true", help="Skip LA capture (UART-only dry run)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    uart_log = output_dir / "uart.log"
    la_path = output_dir / "la_capture.sr"

    pre_steps = parse_command_spec(args.pre)
    main_step = parse_command_spec(args.command)[0]
    post_steps = parse_command_spec(args.post)
    channels = [ch.strip() for ch in args.channels.split(",") if ch.strip()]

    recorder = UartRecorder(args.port, args.baud, uart_log)
    recorder.open()

    la_result: List[Optional[SigrokCaptureResult]] = [None]
    la_thread: Optional[threading.Thread] = None

    if not args.no_la:
        la_thread = threading.Thread(
            target=run_la_capture,
            args=(la_path, args.duration, channels, int(args.rate), la_result),
            daemon=True,
        )
        la_thread.start()
        # Give the LA a moment to arm before we start UART commands.
        time.sleep(0.5)

    try:
        for step in pre_steps:
            recorder.command(step)

        print(f"TX main: {main_step.command}")
        recorder.command(main_step)

        for step in post_steps:
            recorder.command(step)
    finally:
        if not args.no_la and la_thread is not None:
            # Wait for the fixed LA capture to finish (with generous margin).
            la_thread.join(timeout=max(args.duration + 10.0, 10.0))
        recorder.close()

    print(f"UART log: {uart_log}")
    if not args.no_la:
        res = la_result[0]
        if isinstance(res, SigrokError):
            print(f"LA capture failed: {res}", file=sys.stderr)
            return 1
        if res is None:
            print("LA capture did not return a result", file=sys.stderr)
            return 1
        print(f"LA capture: {res.output_path} ({res.samples_captured} samples in {res.elapsed_s:.2f}s)")
        session = res.session
        for ch in channels:
            trans = session.channel_transitions(ch)
            print(f"  {ch}: {len(trans)} transitions")

    return 0


if __name__ == "__main__":
    sys.exit(main())
