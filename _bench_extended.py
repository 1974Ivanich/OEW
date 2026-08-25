import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.8):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

print("=== EXTENDED BENCH TESTS (no-HV, ct-fix firmware) ===")

print("\n--- m (help/reference) ---")
out = send_cmd("m", 0.8)
print(out[:1200])

print("\n--- vdc=150 (nominal bus, expect @VDC:OK with measured VBUS) ---")
out = send_cmd("vdc=150", 0.6)
line = [l for l in out.splitlines() if l.startswith('@VDC:')]
print(line[0] if line else out[:300])

print("\n--- a=200 ADC stream stability (8 samples over ~4s) ---")
send_cmd("a=200", 0.3)
lines = []
for _ in range(8):
    time.sleep(0.5)
    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    for l in chunk.splitlines():
        if l.startswith('@ADC:') and 'I1=' in l:
            lines.append(l)
    if len(lines) >= 8:
        break
for l in lines[:8]:
    print(" ", l)
send_cmd("a=0", 0.3)

# Analyze stability
if lines:
    i1s = [int(re.search(r'I1=(\d+)', l).group(1)) for l in lines if re.search(r'I1=(\d+)', l)]
    i2s = [int(re.search(r'I2=(\d+)', l).group(1)) for l in lines if re.search(r'I2=(\d+)', l)]
    iress = [int(re.search(r'Ires=(\d+)', l).group(1)) for l in lines if re.search(r'Ires=(\d+)', l)]
    print(f"  I1 range {min(i1s)}..{max(i1s)} (spread {max(i1s)-min(i1s)})")
    print(f"  I2 range {min(i2s)}..{max(i2s)} (spread {max(i2s)-min(i2s)})")
    print(f"  Ires range {min(iress)}..{max(iress)}")
    stable = max(i1s)-min(i1s) <= 8 and max(i2s)-min(i2s) <= 8
    print("  STABILITY: " + ("PASS" if stable else "CHECK"))

print("\n--- p? / dump / pdump consistency ---")
out = send_cmd("p?")
print(" ", [l for l in out.splitlines() if l.startswith('@PWM:')][0] if any(l.startswith('@PWM:') for l in out.splitlines()) else out[:200])
out = send_cmd("dump")
print(" ", [l for l in out.splitlines() if l.startswith('@PWM:DUMP')][0] if any(l.startswith('@PWM:DUMP') for l in out.splitlines()) else out[:200])

print("\n--- vf? (V/f default-deny) ---")
out = send_cmd("vf?")
print(" ", [l for l in out.splitlines() if l.startswith('@VF:')][0] if any(l.startswith('@VF:') for l in out.splitlines()) else out[:200])

print("\n--- a? (offset status after fix) ---")
out = send_cmd("a?")
print(" ", [l for l in out.splitlines() if l.startswith('@ADC:STATUS')][0] if any(l.startswith('@ADC:STATUS') for l in out.splitlines()) else out[:200])

ser.close()
print("=== DONE ===")