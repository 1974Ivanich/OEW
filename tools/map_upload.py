#!/usr/bin/env python3
"""Upload oew_map_v2.bin to firmware via UART mapload command.

Usage:
    python tools/map_upload.py --port COM5 --bin oew_map_v2.bin
    python tools/map_upload.py --port COM5 --bin oew_map_v2.bin --verify

TZ_MAP_UPLOAD_AND_ADMISSION §2.3
"""
import argparse
import sys
import time

OEW_CURRENT_MAP_WIRE_SIZE = 497


def main():
    parser = argparse.ArgumentParser(description="Upload OEW map artifact via UART")
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM5)")
    parser.add_argument("--bin", required=True, help="Path to oew_map_v2.bin (497 bytes)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    parser.add_argument("--verify", action="store_true",
                        help="After load, attempt FOC start/stop smoke test")
    args = parser.parse_args()

    try:
        import serial  # type: ignore
    except ImportError:
        print("ERROR: pyserial not installed.  pip install pyserial", file=sys.stderr)
        return 1

    # Read artifact
    with open(args.bin, "rb") as f:
        data = f.read()
    if len(data) != OEW_CURRENT_MAP_WIRE_SIZE:
        print(f"ERROR: expected {OEW_CURRENT_MAP_WIRE_SIZE} bytes, got {len(data)}",
              file=sys.stderr)
        return 1

    hex_payload = data.hex()
    assert len(hex_payload) == OEW_CURRENT_MAP_WIRE_SIZE * 2

    command = f"mapload {hex_payload}\n"

    # Open serial
    ser = serial.Serial(args.port, args.baud, timeout=0.1)
    ser.reset_input_buffer()

    print(f"Sending mapload ({len(hex_payload)} hex chars) to {args.port}...")
    ser.write(command.encode("ascii"))
    ser.flush()

    # Wait for response (up to 5 seconds)
    deadline = time.monotonic() + 5.0
    response = ""
    while time.monotonic() < deadline:
        chunk = ser.read(1024)
        if chunk:
            response += chunk.decode("ascii", errors="replace")
            if "@MAP:LOAD:OK" in response or "@MAP:LOAD:FAIL" in response:
                break
        time.sleep(0.05)

    print(f"Response: {response.strip()}")

    if "@MAP:LOAD:OK" in response:
        print("Map loaded successfully.")
        rc = 0
    elif "@MAP:LOAD:FAIL" in response:
        print("Map load FAILED.", file=sys.stderr)
        rc = 1
    else:
        print("Timeout: no response from firmware.", file=sys.stderr)
        rc = 1

    # Optional --verify: FOC start/stop smoke test
    if args.verify and rc == 0:
        print("Sending FOC start (1)...")
        ser.write(b"1\n")
        ser.flush()
        deadline = time.monotonic() + 5.0
        foc_response = ""
        while time.monotonic() < deadline:
            chunk = ser.read(1024)
            if chunk:
                foc_response += chunk.decode("ascii", errors="replace")
                if "FOC started" in foc_response or "rc=" in foc_response:
                    break
            time.sleep(0.05)

        print(f"FOC response: {foc_response.strip()}")

        # Best-effort FOC stop regardless of outcome
        try:
            ser.write(b"0\n")
            ser.flush()
            time.sleep(0.5)
        except Exception:
            pass

        if "rc=-2" in foc_response:
            print("VERIFY FAILED: map_unverified (rc=-2).", file=sys.stderr)
            rc = 1
        elif "FOC started" in foc_response or "rc=0" in foc_response:
            print("VERIFY OK: FOC started successfully.")
        else:
            print(f"VERIFY: unexpected FOC response.", file=sys.stderr)
            rc = 1

    ser.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
