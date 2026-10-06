import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.4):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

def pwm_is_off():
    out = send_cmd("p?", 0.3)
    m = re.search(r'@PWM:CR1=(\w+):CCER=(\w+)', out)
    if not m:
        return False
    cr1 = int(m.group(1), 16)
    ccer = int(m.group(2), 16)
    return (cr1 & 0x1) == 0 and (ccer & 0xFF) == 0  # CEN=0, все CCxE=0

print("=== UART STRESS + CONFIG SAFETY (no-HV) ===")

# 1) Config commands (should be accepted, not energise)
for cmd, expect in [
    ("dt=1000", "@PWM:DT=1000 ns"),
    ("pp=2", "pole_pairs=2"),
    ("vfk=20,50", "V/f params"),
    ("s=500", "speed=500 rpm"),
    ("i=100,200", "@I:OK"),
]:
    out = send_cmd(cmd, 0.5)
    m = re.search(fr'{re.escape(expect)}', out)
    alive = '@SYS' not in out  # not required; just check no crash
    # validate via follow-up
    follow = send_cmd("p?", 0.3)
    pwm_ok = 'CR1=224' in follow and 'CCER=0' in follow
    print(f"  [{ 'PASS' if (m or expect in out) and pwm_ok else 'FAIL' }] {cmd} -> {out.splitlines()[0][:80] if out.splitlines() else '(no out)'} | PWM off={pwm_ok}")

# 2) UART stress: burst of 50 commands rapidly
print("\n--- stress: 50 команд за ~5 c ---")
send_cmd("", 0.2)
t0 = time.time()
errors = 0
for i in range(50):
    ser.reset_input_buffer()
    ser.write(b"p?\r\n")
    time.sleep(0.1)
    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    if '@PWM' not in chunk:
        errors += 1
        if errors <= 3:
            print(f"  ! пакет {i}: нет ответа, chunk={chunk[:80]!r}")
    # occasionally interleave a real command
    if i == 25:
        send_cmd("enc", 0.3)
elapsed = time.time() - t0
print(f"  elapsed={elapsed:.1f}s, потерь={errors}/50")

# 3) Final safety state
print("\n--- final safety state ---")
out = send_cmd("p?")
print("  PWM:", [l for l in out.splitlines() if l.startswith('@PWM:')][0])
out = send_cmd("sysinfo")
print("  SYS:", [l for l in out.splitlines() if l.startswith('@SYS:')][0])
out = send_cmd("enc")
print("  ENC:", [l for l in out.splitlines() if l.startswith('@ENC:')][0])

print(f"\nRESULT: {'PASS' if errors == 0 and pwm_is_off() else 'CHECK'}")
ser.close()