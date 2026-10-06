import serial, time, re

PORT = "COM4"
BAUD = 115200

def send_cmd(ser, cmd, wait=1.0):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

print("=== ADC RAW DIAGNOSTICS (fail-closed, no PWM) ===")

# Step 1: raw ADC stream (a=200 enables @ADC:I1=raw:I2=raw:Ires=raw:VBUS=raw)
print("--- a=200 (raw ADC stream, 200ms period) ---")
out = send_cmd(ser, "a=200", 0.5)
# collect 3-4 raw lines
lines = []
for _ in range(4):
    time.sleep(0.25)
    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    lines.extend([l for l in chunk.splitlines() if l.startswith('@ADC:')])
    if len(lines) >= 3:
        break
for l in lines[:4]:
    print(l)
print()

# Step 2: stop stream
print("--- a=0 (stop stream) ---")
print(send_cmd(ser, "a=0", 0.5)[:200])
print()

# Step 3: dumpa — ADC calibration/offsets if available
print("--- dumpa (ADC info) ---")
print(send_cmd(ser, "dumpa", 0.5)[:500])
print()

# Step 4: sysinfo — clocks
print("--- sysinfo ---")
print(send_cmd(ser, "sysinfo", 0.5)[:300])
print()

ser.close()
print("=== DONE ===")