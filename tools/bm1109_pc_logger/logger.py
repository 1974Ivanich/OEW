"""Read-only PZEM-004T V3.0 and AD800 Modbus RTU data logger."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import serial
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit("Missing dependency: install with 'pip install -r tools/bm1109_pc_logger/requirements.txt'") from exc

LOG = logging.getLogger("bm1109_logger")


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc & 0xFFFF


def modbus_frame(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + struct.pack("<H", crc)


def check_crc(frame: bytes) -> None:
    if len(frame) < 4:
        raise ValueError("Modbus frame too short")
    expected = crc16_modbus(frame[:-2])
    received = struct.unpack("<H", frame[-2:])[0]
    if expected != received:
        raise ValueError(f"Modbus CRC mismatch: received 0x{received:04X}, expected 0x{expected:04X}")


def read_modbus_registers(port: serial.Serial, address: int, function: int,
                          start: int, count: int) -> list[int]:
    if not 1 <= address <= 247 and address != 248:
        raise ValueError(f"Invalid Modbus address: {address}")
    if function not in (3, 4):
        raise ValueError("Only read holding/input registers (functions 0x03/0x04) are supported")
    if not 1 <= count <= 125 or not 0 <= start <= 0xFFFF:
        raise ValueError("Invalid register range")

    request = modbus_frame(struct.pack(">BBHH", address, function, start, count))
    port.reset_input_buffer()
    port.write(request)
    port.flush()

    header = port.read(3)
    if len(header) != 3:
        raise TimeoutError("Timeout waiting for Modbus response header")
    resp_addr, resp_func, byte_count = header
    if resp_addr != address:
        raise ValueError(f"Unexpected Modbus address {resp_addr}, expected {address}")
    if resp_func == (function | 0x80):
        code = port.read(1)
        tail = port.read(2)
        if len(code + tail) != 3:
            raise TimeoutError("Incomplete Modbus exception response")
        frame = header + code + tail
        check_crc(frame)
        raise RuntimeError(f"Modbus exception code 0x{code[0]:02X}")
    if resp_func != function:
        raise ValueError(f"Unexpected Modbus function 0x{resp_func:02X}")
    if byte_count != count * 2:
        raise ValueError(f"Unexpected response byte count {byte_count}, expected {count * 2}")
    tail = port.read(byte_count + 2)
    if len(tail) != byte_count + 2:
        raise TimeoutError("Timeout waiting for Modbus register data")
    frame = header + tail
    check_crc(frame)
    return list(struct.unpack(">" + "H" * count, frame[3:-2]))


def serial_from_config(cfg: dict[str, Any]) -> serial.Serial:
    required = ("port", "baudrate", "bytesize", "parity", "stopbits", "timeout_s")
    missing = [key for key in required if cfg.get(key) is None]
    if missing:
        raise ValueError("serial settings not configured: " + ", ".join(missing))
    return serial.Serial(
        port=str(cfg["port"]),
        baudrate=int(cfg["baudrate"]),
        bytesize=int(cfg["bytesize"]),
        parity=str(cfg["parity"]).upper(),
        stopbits=float(cfg["stopbits"]),
        timeout=float(cfg["timeout_s"]),
        write_timeout=float(cfg["timeout_s"]),
    )


def read_pzem(cfg: dict[str, Any]) -> dict[str, Any]:
    """Read common PZEM-004T V3.0 ten-register input block.

    Confirm this register map and address against the exact meter before use.
    """
    with serial_from_config(cfg) as port:
        regs = read_modbus_registers(port, int(cfg.get("slave_address", 248)), 4, 0, 10)
    current_raw = regs[1] | (regs[2] << 16)
    power_raw = regs[3] | (regs[4] << 16)
    energy_raw = regs[5] | (regs[6] << 16)
    return {
        "pzem_voltage_v": regs[0] / 10.0,
        "pzem_current_a": current_raw / 1000.0,
        "pzem_active_power_w": power_raw / 10.0,
        "pzem_energy_kwh": energy_raw / 1000.0,
        "pzem_frequency_hz": regs[7] / 10.0,
        "pzem_power_factor": regs[8] / 100.0,
        "pzem_alarm": regs[9],
    }


def read_ad800(cfg: dict[str, Any]) -> dict[str, Any]:
    """Read only explicitly configured single 16-bit AD800 registers."""
    registers = cfg.get("registers") or []
    if not registers:
        raise ValueError("AD800 register map is empty; configure only verified addresses/scales")
    result: dict[str, Any] = {}
    with serial_from_config(cfg) as port:
        address = int(cfg["slave_address"])
        function = int(cfg["function"])
        for item in registers:
            name = str(item["name"])
            reg = int(item["address"])
            raw = read_modbus_registers(port, address, function, reg, 1)[0]
            if item.get("signed", False) and raw >= 0x8000:
                raw -= 0x10000
            result["ad800_" + name] = raw * float(item.get("scale", 1.0))
    return result


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        cfg = json.load(handle)
    if float(cfg.get("poll_interval_s", 0)) <= 0:
        raise ValueError("poll_interval_s must be greater than zero")
    return cfg


def make_output_path(cfg: dict[str, Any], override: str | None) -> Path:
    directory = Path(override or cfg.get("output_dir", "data/bm1109"))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return directory / f"bm1109_{stamp}.csv"


def run(config: dict[str, Any], output: Path) -> None:
    fixed_fields = [
        "timestamp_utc", "epoch_s", "cycle_duration_s",
        "pzem_voltage_v", "pzem_current_a", "pzem_active_power_w",
        "pzem_energy_kwh", "pzem_frequency_hz", "pzem_power_factor",
        "pzem_alarm", "pzem_error", "ad800_error",
    ]
    ad_names = ["ad800_" + str(x["name"]) for x in config.get("ad800", {}).get("registers", [])]
    fields = fixed_fields[:-2] + ad_names + fixed_fields[-2:]
    LOG.info("Writing CSV: %s", output)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        handle.flush()
        try:
            while True:
                cycle_start = time.monotonic()
                row: dict[str, Any] = {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "epoch_s": time.time(),
                    "pzem_error": "",
                    "ad800_error": "",
                }
                pzem_cfg = config.get("pzem", {})
                if pzem_cfg.get("enabled", False):
                    try:
                        row.update(read_pzem(pzem_cfg))
                    except Exception as exc:  # log per-device failure and continue
                        row["pzem_error"] = f"{type(exc).__name__}: {exc}"
                        LOG.warning("PZEM read failed: %s", row["pzem_error"])
                else:
                    row["pzem_error"] = "disabled"

                ad_cfg = config.get("ad800", {})
                if ad_cfg.get("enabled", False):
                    try:
                        row.update(read_ad800(ad_cfg))
                    except Exception as exc:
                        row["ad800_error"] = f"{type(exc).__name__}: {exc}"
                        LOG.warning("AD800 read failed: %s", row["ad800_error"])
                else:
                    row["ad800_error"] = "disabled"

                row["cycle_duration_s"] = round(time.monotonic() - cycle_start, 6)
                writer.writerow(row)
                handle.flush()
                LOG.info("cycle %.3fs | PZEM=%s | AD800=%s",
                         row["cycle_duration_s"],
                         "OK" if not row["pzem_error"] else "ERR",
                         "OK" if not row["ad800_error"] else "ERR")
                delay = float(config["poll_interval_s"]) - row["cycle_duration_s"]
                if delay > 0:
                    time.sleep(delay)
        except KeyboardInterrupt:
            LOG.info("Stopped by user (Ctrl+C). CSV closed cleanly.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only BM1109 PZEM + AD800 CSV logger")
    parser.add_argument("--config", required=True, type=Path, help="JSON configuration file")
    parser.add_argument("--output", help="CSV output directory (overrides output_dir)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        cfg = load_config(args.config)
        output = make_output_path(cfg, args.output)
        run(cfg, output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOG.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
