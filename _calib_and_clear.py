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

print("=== ADC OFFSET CALIBRATION + FAULT CLEAR ===")
print("(PWM is OFF, MOE=0, safe)")

print("--- c (calibrate offsets, 256 samples) ---")
out = send_cmd(ser, "c", 2.0)
m = re.search(r'@ADC:CAL:offset_i1=(\d+):offset_i2=(\d+):offset_ires=(\d+)', out)
if m:
    print(f"  offset_i1={m.group(1)} offset_i2={m.group(2)} offset_ires={m.group(3)}")
else:
    print(out[:300])

print("--- a? (offset status) ---")
print(send_cmd(ser, "a?", 0.5)[:200])

print("--- f (clear fault; expects VBUS range + currents <6A) ---")
out = send_cmd(ser, "f", 0.8)
if "fault cleared" in out:
    print("  FAULT CLEARED!")
else:
    print(out[:400])

print("--- a (single raw sample after clear) ---")
print(send_cmd(ser, "a", 0.5)[:200])

print("--- a=200 (3 raw stream samples) ---")
send_cmd(ser, "a=200", 0.3)
lines = []
for _ in range(3):
    time.sleep(0.25)
    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    lines.extend([l for l in chunk.splitlines() if l.startswith('@ADC:')])
    if len(lines) >= 3: break
for l in lines[:3]: print(" ", l)
send_cmd(ser, "a=0", 0.3)

print("--- @FOC telemetry check ---")
out = send_cmd(ser, "a", 1.0)
m = re.search(r'@FOC:I1=(-?\d+):I2=(-?\d+):Ires=(-?\d+):VBUS=(-?\d+):STATE=(\d+):SPD=(\d+):TH=(\d+):FAULT=(\d+):FAULT_R=(\d+)', out)
if m:
    fault_status = "FAULT ACTIVE" if m.group(8) == "1" else "NO FAULT"
    print(f"  I1={m.group(1)}mA I2={m.group(2)}mA Ires={m.group(3)}mA VBUS={m.group(4)}mV "
          f"STATE={m.group(5)} FAULT={m.group(8)} ({fault_status})")

ser.close()
print("=== DONE ===")