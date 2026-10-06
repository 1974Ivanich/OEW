import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.5):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

def first_line(out, prefix):
    for l in out.splitlines():
        if l.startswith(prefix):
            return l
    return None

print("=== FINAL CHECK AFTER CONFIG COMMANDS (no-HV) ===")

# Restore defaults: dt=1500 (default), pp=6 (default from stats)
print("--- restore defaults ---")
out = send_cmd("dt=1500", 0.4)
print(" ", out.splitlines()[0][:80] if out.splitlines() else "")
out = send_cmd("pp=6", 0.4)
print(" ", out.splitlines()[0][:80] if out.splitlines() else "")

# Full checklist probes
checks = {}

out = send_cmd("sysinfo")
checks["sysinfo"] = first_line(out, "@SYS:")
out = send_cmd("p?")
checks["p?"] = first_line(out, "@PWM:")
out = send_cmd("a")
checks["a"] = first_line(out, "@ADC:")
out = send_cmd("enc")
checks["enc"] = first_line(out, "@ENC:")
out = send_cmd("pdump")
checks["pdump"] = first_line(out, "@PWM:FULL:")
out = send_cmd("a?")
checks["a?"] = first_line(out, "@ADC:STATUS:")
out = send_cmd("vf?")
checks["vf?"] = first_line(out, "@VF:")

for k, v in checks.items():
    print(f"\n  [{k}]")
    print("   ", v if v else "(no response)")

# Assertions
print("\n--- assertions ---")
ok = True
def assert_good(expr, name):
    global ok
    if not expr:
        ok = False
        print(f"  FAIL: {name}")
    else:
        print(f"  PASS: {name}")

assert_good(checks["sysinfo"] and "CLK=170000000" in checks["sysinfo"], "sysinfo clock 170 MHz")
assert_good(checks["p?"] and "CCER=0" in checks["p?"] and "CR1=224" in checks["p?"], "PWM off (CCER=0, CR1=224)")
assert_good(checks["a"] and re.search(r'I1=(203[0-9]|204[0-9]):I2=(20[5-8][0-9])', checks["a"]), "ADC I1 ~2040, I2 ~2050-2090")
assert_good(checks["enc"] and "err=0" in checks["enc"], "ENC err=0")
assert_good(checks["pdump"] and "CCER=0x00000000" in checks["pdump"], "pdump both timers CCER=0")
assert_good(checks["a?"] and "offset_i1=2039" in checks["a?"], "offset_i1=2039 retained")
assert_good(checks["vf?"] and "target=0" in checks["vf?"], "V/f default-deny")

# FOC start must remain blocked
out = send_cmd("1")
blocked = "rc=-2" in out or "map unverified" in out or "FAULT" in out.upper()
assert_good(blocked, "FOC start blocked")

print(f"\nRESULT: {'ALL PASS' if ok else 'CHECK'}")
ser.close()