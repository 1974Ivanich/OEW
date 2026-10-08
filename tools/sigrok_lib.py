#!/usr/bin/env python3
"""Reusable sigrok-cli automation helpers for the OEW bench.

Design goals:
* detect the fx2lafw (or demo) LA via sigrok-cli;
* run fixed-time captures to .sr (srzip) files;
* parse .sr files without extra deps (only stdlib);
* find edges and correlate channels with UART/telemetry time base.

The module never touches the motor controller directly — it only drives
sigrok-cli and inspects captured sessions.
"""

from __future__ import annotations

import array
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Allow overriding the CLI path from environment (useful on Linux or non-default installs).
SIGROK_CLI = os.environ.get(
    "SIGROK_CLI",
    r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe",
)

DEFAULT_DRIVER = "fx2lafw"
DEMO_DRIVER = "demo"

# fx2lafw chunk size observed in generated .sr files (bytes).  Matches the chunk
# size sigrok-cli uses when writing srzip.
_SRZIP_CHUNK = 4 * 1024 * 1024


class SigrokError(RuntimeError):
    """Raised when sigrok-cli reports an error or the capture is unusable."""


class SigrokNotFound(RuntimeError):
    """Raised when sigrok-cli is not installed or not on PATH."""


# -----------------------------------------------------------------------------
# device discovery
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SigrokDevice:
    driver: str
    conn: str
    channels: Tuple[str, ...]

    @property
    def channel_count(self) -> int:
        return len(self.channels)


def find_sigrok_cli() -> Path:
    """Locate the sigrok-cli executable."""
    path = Path(SIGROK_CLI)
    if path.exists():
        return path
    alt = shutil.which("sigrok-cli")
    if alt:
        return Path(alt)
    raise SigrokNotFound(f"sigrok-cli not found at {SIGROK_CLI} or on PATH")


def _parse_scan_line(line: str) -> Optional[SigrokDevice]:
    # Example:
    # demo - Demo device with 13 channels: D0 D1 D2 D3 D4 D5 D6 D7 A0 A1 A2 A3 A4
    # fx2lafw:conn=1.4 - Cypress FX2 [S/N: Cypress FX2] with 16 channels: D0 D1 ...
    m = re.match(r"^([a-zA-Z0-9_\-]+)(?::conn=([^ ]+))?\s+-.*with (\d+) channels:\s+(.+)$", line)
    if not m:
        return None
    driver = m.group(1)
    conn = m.group(2) or ""
    channels = tuple(ch.strip() for ch in m.group(4).strip().split() if ch.strip())
    return SigrokDevice(driver, conn, channels)


def scan_devices() -> List[SigrokDevice]:
    """Return the list of sigrok-detectable devices."""
    cli = find_sigrok_cli()
    proc = subprocess.run(
        [str(cli), "--scan"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    # sigrok-cli prints driver errors on stderr; ignore them.
    devices = []
    for line in proc.stdout.splitlines():
        dev = _parse_scan_line(line)
        if dev:
            devices.append(dev)
    return devices


def find_fx2lafw() -> SigrokDevice:
    """Find the first fx2lafw LA, or fall back to the demo device for tests."""
    for dev in scan_devices():
        if dev.driver == DEFAULT_DRIVER:
            return dev
    demo = next((d for d in scan_devices() if d.driver == DEMO_DRIVER), None)
    if demo:
        return demo
    raise SigrokError("No fx2lafw logic analyzer found (and no demo device either)")


# -----------------------------------------------------------------------------
# .sr (srzip) parsing
# -----------------------------------------------------------------------------

_SAMPLERATE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(Hz|kHz|MHz|GHz)$", re.IGNORECASE)


def _parse_samplerate(value: str) -> int:
    value = value.strip()
    m = _SAMPLERATE_RE.match(value)
    if not m:
        # Try plain integer.
        try:
            return int(value)
        except ValueError as exc:
            raise SigrokError(f"Cannot parse samplerate: {value!r}") from exc
    num = float(m.group(1))
    unit = m.group(2).lower()
    mult = {"hz": 1, "khz": 1_000, "mhz": 1_000_000, "ghz": 1_000_000_000}[unit]
    return int(num * mult)


@dataclass(frozen=True)
class SigrokSession:
    """In-memory representation of an srzip capture."""

    path: Path
    samplerate: int
    unitsize: int
    channel_to_bit: Dict[str, int]
    raw: bytes  # concatenated logic-1-* bytes
    metadata_text: str

    @property
    def sample_count(self) -> int:
        return len(self.raw) // self.unitsize

    @property
    def duration(self) -> float:
        return self.sample_count / self.samplerate

    @property
    def channels(self) -> List[str]:
        return list(self.channel_to_bit.keys())

    @classmethod
    def load(cls, path: Path | str) -> "SigrokSession":
        path = Path(path)
        with zipfile.ZipFile(path, "r") as zf:
            try:
                meta_bytes = zf.read("metadata")
            except KeyError as exc:
                raise SigrokError(f"{path}: missing metadata file") from exc
            metadata = meta_bytes.decode("utf-8", errors="replace")

        channel_to_bit: Dict[str, int] = {}
        samplerate: Optional[int] = None
        unitsize: Optional[int] = None
        capturefile: Optional[str] = None

        for line in metadata.splitlines():
            line = line.strip()
            if not line or line.startswith("["):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key == "samplerate":
                samplerate = _parse_samplerate(value)
            elif key == "unitsize":
                unitsize = int(value)
            elif key == "capturefile":
                capturefile = value
            elif key.startswith("probe") and value:
                try:
                    bit = int(key.replace("probe", "")) - 1
                except ValueError:
                    continue
                channel_to_bit[value] = bit

        if samplerate is None:
            raise SigrokError("samplerate not found in metadata")
        if unitsize is None:
            raise SigrokError("unitsize not found in metadata")
        if capturefile is None:
            raise SigrokError("capturefile not found in metadata")

        with zipfile.ZipFile(path, "r") as zf:
            chunks = []
            for i in range(1, 9999):
                name = f"{capturefile}-{i}"
                if name not in zf.namelist():
                    break
                chunks.append(zf.read(name))
            raw = b"".join(chunks)

        return cls(path, samplerate, unitsize, channel_to_bit, raw, metadata)

    def samples(self) -> array.array:
        """Return all samples as an array of little-endian unsigned integers."""
        typecode = {1: "B", 2: "H"}.get(self.unitsize)
        if typecode is None:
            raise SigrokError(f"Unsupported unitsize {self.unitsize}")
        return array.array(typecode, self.raw)

    def channel_mask(self, channels: Sequence[str]) -> int:
        mask = 0
        for ch in channels:
            if ch not in self.channel_to_bit:
                raise SigrokError(f"Channel {ch!r} not in session; available: {list(self.channel_to_bit)}")
            mask |= 1 << self.channel_to_bit[ch]
        return mask

    def transitions(
        self,
        channels: Sequence[str],
        start_sample: int = 0,
        end_sample: Optional[int] = None,
    ) -> List[Tuple[float, Dict[str, int]]]:
        """Return a list of (time_s, {channel: value}) for every change on any channel."""
        mask = self.channel_mask(channels)
        typecode = {1: "B", 2: "H"}.get(self.unitsize)
        if typecode is None:
            raise SigrokError(f"Unsupported unitsize {self.unitsize}")
        size = self.unitsize
        end_sample = end_sample if end_sample is not None else len(self.raw) // size
        data = self.samples()

        prev = None
        results: List[Tuple[float, Dict[str, int]]] = []
        t0 = start_sample / self.samplerate
        for offset in range(start_sample, end_sample):
            value = data[offset] & mask
            if value != prev:
                results.append((t0 + (offset - start_sample) / self.samplerate, {ch: (value >> self.channel_to_bit[ch]) & 1 for ch in channels}))
                prev = value
        return results

    def channel_transitions(self, channel: str) -> List[Tuple[float, int]]:
        """Return (time_s, value) transitions for a single channel."""
        if channel not in self.channel_to_bit:
            raise SigrokError(f"Channel {channel!r} not in session; available: {list(self.channel_to_bit)}")
        bit = self.channel_to_bit[channel]
        typecode = {1: "B", 2: "H"}.get(self.unitsize)
        if typecode is None:
            raise SigrokError(f"Unsupported unitsize {self.unitsize}")
        data = array.array(typecode, self.raw)
        prev = None
        results: List[Tuple[float, int]] = []
        for offset, sample in enumerate(data):
            val = (sample >> bit) & 1
            if val != prev:
                results.append((offset / self.samplerate, val))
                prev = val
        return results

    def first_edge(
        self,
        channel: str,
        edge: str = "falling",
        start_s: float = 0.0,
        end_s: Optional[float] = None,
    ) -> Optional[Tuple[float, int]]:
        """Find the first falling/rising edge on a channel within a time window."""
        trans = self.channel_transitions(channel)
        end_s = end_s if end_s is not None else self.duration
        prev_val = None
        for t, val in trans:
            if t < start_s:
                prev_val = val
                continue
            if t > end_s:
                break
            if prev_val is not None:
                if edge == "falling" and prev_val == 1 and val == 0:
                    return (t, val)
                if edge == "rising" and prev_val == 0 and val == 1:
                    return (t, val)
            prev_val = val
        return None

    def to_sr(self, dst: Path | str) -> Path:
        """Write this session back to a new .sr file (e.g. after trimming)."""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Re-split raw into standard chunks.
        chunks = [
            self.raw[i : i + _SRZIP_CHUNK]
            for i in range(0, len(self.raw), _SRZIP_CHUNK)
        ]
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata", self.metadata_text.encode("utf-8"))
            for i, chunk in enumerate(chunks, start=1):
                zf.writestr(f"logic-1-{i}", chunk)
        return dst


# -----------------------------------------------------------------------------
# capture helpers
# -----------------------------------------------------------------------------

@dataclass
class SigrokCaptureResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    output_path: Path
    elapsed_s: float
    samples_captured: int

    @property
    def session(self) -> SigrokSession:
        return SigrokSession.load(self.output_path)


def _build_channel_arg(channels: Sequence[str]) -> List[str]:
    return ["--channels", ",".join(ch.strip() for ch in channels)]


def _rate_arg(rate_hz: int) -> str:
    if rate_hz % 1_000_000 == 0:
        return f"{rate_hz // 1_000_000}m"
    if rate_hz % 1_000 == 0:
        return f"{rate_hz // 1_000}k"
    return str(rate_hz)


def capture_fixed(
    output_path: Path | str,
    duration_s: float,
    channels: Sequence[str],
    rate_hz: int = 1_000_000,
    driver: str = DEFAULT_DRIVER,
    conn: str = "",
) -> SigrokCaptureResult:
    """Run a single fixed-duration sigrok-cli capture to an .sr file."""
    cli = find_sigrok_cli()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    driver_spec = driver
    if conn:
        driver_spec = f"{driver}:conn={conn}"

    cmd: List[str] = [
        str(cli),
        "-d", driver_spec,
        "-c", f"samplerate={_rate_arg(rate_hz)}",
        *_build_channel_arg(channels),
        "--time", str(int(duration_s * 1000)),
        "-O", "srzip",
        "-o", str(output_path),
    ]

    start = time.monotonic()
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - start

    # sigrok-cli returns 0 even if the device sent fewer samples, but prints a warning.
    if "Device only sent" in proc.stderr:
        raise SigrokError(
            f"sigrok capture truncated: {proc.stderr.strip()}\nCommand: {' '.join(cmd)}"
        )
    if proc.returncode != 0:
        raise SigrokError(
            f"sigrok-cli failed (rc={proc.returncode}): {proc.stderr}\nCommand: {' '.join(cmd)}"
        )

    if not output_path.exists():
        raise SigrokError(f"sigrok-cli did not create {output_path}")

    session = SigrokSession.load(output_path)
    return SigrokCaptureResult(
        command=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        output_path=output_path,
        elapsed_s=elapsed,
        samples_captured=session.sample_count,
    )


# -----------------------------------------------------------------------------
# rolling capture for events longer than one reliable chunk
# -----------------------------------------------------------------------------

@dataclass
class SigrokRollingResult:
    snapshot_paths: List[Path]
    elapsed_s: float


class SigrokRollingCapture:
    """Capture an event of unknown/variable length by taking repeated snapshots.

    Because fx2lafw cannot stream raw samples continuously via sigrok-cli, we take
    sequential fixed-duration snapshots.  There is a small blind window between
    snapshots (driver re-arm overhead).  Use a single long `capture_fixed` call
    when the event fits into one capture.
    """

    def __init__(
        self,
        snapshot_dir: Path | str,
        snapshot_s: float,
        channels: Sequence[str],
        rate_hz: int = 1_000_000,
        driver: str = DEFAULT_DRIVER,
        conn: str = "",
    ) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_s = snapshot_s
        self.channels = tuple(channels)
        self.rate_hz = rate_hz
        self.driver = driver
        self.conn = conn
        self._stop = False
        self._paths: List[Path] = []

    def start(self) -> None:
        """Blocking loop that records snapshots until stop() is called."""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._stop = False
        self._paths = []
        idx = 0
        while not self._stop:
            path = self.snapshot_dir / f"snap_{idx:04d}.sr"
            try:
                capture_fixed(path, self.snapshot_s, self.channels, self.rate_hz, self.driver, self.conn)
                self._paths.append(path)
            except SigrokError as exc:
                # Log and stop on error so we do not spam a disconnected device.
                print(f"[SigrokRollingCapture] snapshot {idx} failed: {exc}", file=sys.stderr)
                break
            idx += 1

    def stop(self) -> None:
        self._stop = True

    @property
    def paths(self) -> List[Path]:
        return list(self._paths)


# -----------------------------------------------------------------------------
# simple self-test
# -----------------------------------------------------------------------------

def _selftest() -> int:
    """Verify parsing and capture with the demo driver if no fx2lafw is present."""
    try:
        devices = scan_devices()
    except SigrokNotFound as exc:
        print(f"SKIP: {exc}")
        return 77

    if not devices:
        print("SKIP: no sigrok devices found")
        return 77

    dev = next((d for d in devices if d.driver == DEFAULT_DRIVER), None)
    if dev is None:
        dev = next((d for d in devices if d.driver == DEMO_DRIVER), None)
    if dev is None:
        print("SKIP: neither fx2lafw nor demo device found")
        return 77

    print(f"Using device: {dev}")
    channels = list(dev.channels[:5])
    output = Path("build/sigrok_selftest.sr")
    try:
        res = capture_fixed(output, duration_s=0.2, channels=channels, rate_hz=1_000_000, driver=dev.driver, conn=dev.conn)
    except SigrokError as exc:
        print(f"SKIP: capture failed: {exc}")
        return 77

    print(f"Captured {res.samples_captured} samples in {res.elapsed_s:.2f}s")
    session = res.session
    print(f"Duration: {session.duration:.3f}s, channels: {session.channels}")
    for ch in channels:
        trans = session.channel_transitions(ch)
        print(f"  {ch}: {len(trans)} transitions")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
