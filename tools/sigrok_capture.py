#!/usr/bin/env python3
"""Command-line wrapper around tools/sigrok_lib.py for LA capture.

Examples:

    # 5 s capture of D0-D4 at 1 MHz, saved as .sr
    python tools/sigrok_capture.py -o build/la/pc3_al.sr -t 5 -r 1e6 -c D0,D1,D2,D3,D4

    # Rolling capture (sequential snapshots) for a long/unknown event
    python tools/sigrok_capture.py -o campaign_raw/run1/la -t 30 --rolling -s 8

    # Analyze an existing .sr file
    python tools/sigrok_capture.py -i existing.sr --analyze
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

# Add repository root to path so this file can be run standalone.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.sigrok_lib import (
    SigrokCaptureResult,
    SigrokError,
    SigrokRollingCapture,
    SigrokRollingResult,
    SigrokSession,
    capture_fixed,
    find_fx2lafw,
    scan_devices,
)


def parse_channels(value: str) -> List[str]:
    return [ch.strip() for ch in value.split(",") if ch.strip()]


def arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Capture/analyze sigrok logic-analyzer sessions")
    p.add_argument("-o", "--output", type=Path, default=None, help="Output .sr file or directory for rolling mode")
    p.add_argument("-i", "--input", type=Path, default=None, help="Existing .sr file to analyze")
    p.add_argument("-t", "--time", type=float, default=None, help="Capture duration in seconds")
    p.add_argument("-r", "--rate", type=float, default=float(os.environ.get("SIGROK_RATE_HZ", 1_000_000)), help="Sample rate in Hz")
    p.add_argument("-c", "--channels", type=parse_channels, default=None, help="Comma-separated channels, e.g. D0,D1,D4")
    p.add_argument("-d", "--driver", default=os.environ.get("SIGROK_DRIVER", ""), help="sigrok driver (default: fx2lafw or demo)")
    p.add_argument("--conn", default=os.environ.get("SIGROK_CONN", ""), help="Device connection string")
    p.add_argument("--rolling", action="store_true", help="Use repeated snapshots instead of one capture")
    p.add_argument("-s", "--snapshot-duration", type=float, default=8.0, help="Snapshot length in rolling mode (seconds)")
    p.add_argument("--analyze", action="store_true", help="Print transition summary after capture or for -i input")
    p.add_argument("--list-devices", action="store_true", help="List detected sigrok devices and exit")
    return p


def list_devices_and_exit() -> None:
    devices = scan_devices()
    if not devices:
        print("No sigrok devices detected.")
        sys.exit(1)
    for dev in devices:
        conn = f":conn={dev.conn}" if dev.conn else ""
        print(f"{dev.driver}{conn} -> {dev.channel_count} channels: {' '.join(dev.channels)}")
    sys.exit(0)


def print_session_summary(session: SigrokSession, channels: Optional[List[str]] = None) -> None:
    if channels is None:
        channels = session.channels
    print(f"path:          {session.path}")
    print(f"samplerate:    {session.samplerate} Hz")
    print(f"duration:      {session.duration:.6f} s")
    print(f"samples:       {session.sample_count}")
    print(f"unitsize:      {session.unitsize} bytes")
    print(f"channels:      {', '.join(session.channels)}")
    for ch in channels:
        if ch not in session.channel_to_bit:
            print(f"  {ch}: not present")
            continue
        trans = session.channel_transitions(ch)
        print(f"  {ch}: {len(trans)} transitions")
        if trans:
            print(f"       first={trans[0]}, last={trans[-1]}")


def main() -> int:
    args = arg_parser().parse_args()

    if args.list_devices:
        list_devices_and_exit()

    if args.input:
        try:
            session = SigrokSession.load(args.input)
        except SigrokError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print_session_summary(session, args.channels)
        return 0

    if args.output is None or args.time is None:
        arg_parser().print_help()
        return 2

    channels = args.channels
    if channels is None:
        try:
            dev = find_fx2lafw()
            channels = list(dev.channels)
        except SigrokError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    rate_hz = int(args.rate)
    duration_s = args.time

    try:
        if args.rolling:
            output_dir = args.output
            output_dir.mkdir(parents=True, exist_ok=True)
            cap = SigrokRollingCapture(
                snapshot_dir=output_dir,
                snapshot_s=args.snapshot_duration,
                channels=channels,
                rate_hz=rate_hz,
                driver=args.driver or "fx2lafw",
                conn=args.conn,
            )
            print(f"Rolling capture started: {output_dir}")
            print("Press Ctrl+C to stop...")
            try:
                cap.start()
            except KeyboardInterrupt:
                print("\nStopping...")
            cap.stop()
            print(f"Snapshots saved: {len(cap.paths)}")
            if args.analyze and cap.paths:
                for path in cap.paths:
                    print_session_summary(SigrokSession.load(path), channels)
        else:
            result = capture_fixed(
                output_path=args.output,
                duration_s=duration_s,
                channels=channels,
                rate_hz=rate_hz,
                driver=args.driver or "fx2lafw",
                conn=args.conn,
            )
            print(f"Captured {result.samples_captured} samples in {result.elapsed_s:.2f}s -> {result.output_path}")
            if args.analyze:
                print_session_summary(result.session, channels)
    except SigrokError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Capture interrupted.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
