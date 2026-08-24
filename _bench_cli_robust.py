import serial, time

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_raw(cmd, wait=0.4):
    ser.reset_input_buffer()
    ser.write(cmd.encode('utf-8', errors='ignore') + b"\r\n")
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

print("=== CLI ROBUSTNESS TEST (no-HV) ===")

tests = [
    ("empty string", ""),
    ("unknown cmd", "qwerty12345"),
    ("unexpected chars", "!@#$%^&*()~`"),
    ("long line (200 chars)", "A" * 200),
    ("very long token", "p" * 500),
    ("partial 'p='", "p=100"),
    ("partial 'a='", "a=9999"),
    ("partial 'vdc='", "vdc=0"),
    ("negative", "-1"),
    ("trailing space", "sysinfo "),
    ("CR inside", "p?\n1"),
]

def alive_check():
    """After each probe, confirm CLI still responds to a real command."""
    ser.reset_input_buffer()
    ser.write(b"sysinfo\r\n")
    time.sleep(0.5)
    out = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    return '@SYS:' in out

ok = True
for name, payload in tests:
    resp = send_raw(payload, 0.3)
    alive = alive_check()
    # print first non-@FOC line
    lines = [l for l in resp.splitlines() if not l.startswith('@FOC:') and l.strip()]
    short = " | ".join(lines[:2])[:120] if lines else "(no output)"
    status = "PASS" if alive else "FAIL(no response)"
    if not alive:
        ok = False
    print(f"  [{status}] {name}: {short!r}")

# final sanity
print("\n--- final sanity ---")
out = send_raw("p?", 0.5)
pwm = [l for l in out.splitlines() if l.startswith('@PWM:')]
print(" ", pwm[0] if pwm else out[:150])
print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
ser.close()