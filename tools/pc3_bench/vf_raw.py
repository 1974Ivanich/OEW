#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send one command to the board and dump the raw response, minus the periodic stream.

Usage: py -3 vf_raw.py [COM4] [command] [seconds]

This is the capture tool of the PC-3 bench kit: it opens the UART, sends one
command (default `pdump`), reads for `seconds` (default 3.0) and prints
everything except the unsolicited periodic streams (`@FOC:`/`@VFLOG:`), so the
raw answer can be saved to a file and parsed by the kit's parser
(`breakdiag_parse.py`, `enc_sign.py`).

Requires pyserial.  If it is missing, the tool prints the install hint and
exits with code 3 instead of a traceback (the operator must see an instruction,
not a stack trace).
"""
import sys
import time

try:
    import serial
except ImportError:                       # pragma: no cover - environment dependent
    sys.stderr.write('pyserial не установлен: py -3 -m pip install pyserial\n')
    sys.exit(3)

port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
cmd = sys.argv[2] if len(sys.argv) > 2 else "pdump"
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

ser = serial.Serial(port, 115200, timeout=0.2)
try:
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    t0 = time.time()
    buf = bytearray()
    while time.time() - t0 < secs:
        chunk = ser.read(8192)
        if chunk:
            buf += chunk
        else:
            time.sleep(0.05)
finally:
    ser.close()

text = bytes(buf).decode("utf-8", "replace")
skip = ("@FOC:", "@VFLOG:")
for ln in text.splitlines():
    s = ln.rstrip()
    if s.strip() and not s.strip().startswith(skip):
        print(s)
