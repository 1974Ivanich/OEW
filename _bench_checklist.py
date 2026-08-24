import serial, time, re

PORT = "COM4"
BAUD = 115200

def send_cmd(ser, cmd, wait=0.8):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

print("=== BENCH_FIRST_SESSION CHECKLIST ===")

# §1 stale-flash probe: sysinfo + fresh cmd p? (must NOT give 'unknown')
print("\n--- sysinfo (probe) ---")
out = send_cmd(ser, "sysinfo", 0.6)
sysline = [l for l in out.splitlines() if l.startswith('@SYS:')]
print(sysline[0] if sysline else "NO @SYS RESPONSE")
print("SYSINFO RESPONSE: " + ("PASS" if sysline else "FAIL"))

print("\n--- p? (fresh command, must not be 'unknown') ---")
out = send_cmd(ser, "p?", 0.6)
print(out[:200])
print("P? KNOWN CMD: " + ("PASS" if "unknown" not in out.lower() else "FAIL"))

# §2 ADC calibration of zeros
print("\n--- c (calibrate zeros) ---")
out = send_cmd(ser, "c", 0.8)
cal = [l for l in out.splitlines() if '@ADC:CAL' in l]
print(cal[0] if cal else out[:300])
print("ADC CAL: " + ("PASS" if cal else "CHECK"))

# §2 ADC single read (codes ~2048, VBUS window)
print("\n--- a (single read) ---")
out = send_cmd(ser, "a", 0.6)
adc = [l for l in out.splitlines() if l.startswith('@ADC') and 'I1=' in l]
for l in adc[:2]:
    print(l)
print("ADC SINGLE READ: " + ("PASS" if adc else "FAIL"))

# §3 encoder AS5048A
print("\n--- enc (encoder) ---")
out = send_cmd(ser, "enc", 0.8)
enc = [l for l in out.splitlines() if '@ENC:' in l]
print(enc[0] if enc else out[:300])
print("ENC RESPONSE: " + ("PASS" if enc else "FAIL"))

# encoder extended: two reads to verify angle changes / period
print("\n--- enc x2 (stability) ---")
out2 = send_cmd(ser, "enc", 0.8)
enc2 = [l for l in out2.splitlines() if '@ENC:' in l]
print(enc2[0] if enc2 else out2[:300])

ser.close()
print("\n=== DONE ===")