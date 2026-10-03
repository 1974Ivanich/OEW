#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send one command to the board and dump the raw response, minus the periodic stream.

Usage: py -3 vf_raw.py [COM4] [command...] [seconds]
       многословные команды принимаются и в кавычках, и без них:
       py -3 vf_raw.py COM4 "breakdiag reset" 3   ==   py -3 vf_raw.py COM4 breakdiag reset 3

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
# Хвост разбирается терпимо: последний аргумент — секунды, только если это число.
# Иначе `breakdiag reset` принимался за «команда + секунды» и тул падал трейсбеком
# (на плату ничего не уходило — безопасно, но evidence терялся).
rest = list(sys.argv[2:])
secs = 3.0
if rest:
    try:
        secs = float(rest[-1])
        rest.pop()
    except ValueError:
        secs = 3.0
cmd = " ".join(rest) if rest else "pdump"

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
