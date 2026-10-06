import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.3):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

print("=== ENCODER PERIOD JITTER + TELEMETRY PULSE (no-HV) ===")

# 1) Encoder period jitter: 100 samples
print("--- encoder period jitter (100 samples) ---")
periods = []
pulses = []
for i in range(100):
    out = send_cmd("enc", 0.05)
    m = re.search(r'period_us=(\d+):pulse_us=(\d+):err=(\d+)', out)
    if m:
        periods.append(int(m.group(1)))
        pulses.append(int(m.group(2)))
periods.sort()
pulses.sort()
if periods:
    pmed = periods[len(periods)//2]
    spread = periods[-1] - periods[0]
    print(f"  period_us: n={len(periods)} min={periods[0]} med={pmed} max={periods[-1]} spread={spread}")
    print("  JITTER: " + ("PASS (spread<=3)" if spread <= 3 else f"CHECK (spread={spread})"))
else:
    print("  no encoder data")

if pulses:
    pmed_p = pulses[len(pulses)//2]
    print(f"  pulse_us: min={pulses[0]} med={pmed_p} max={pulses[-1]}")

# 2) Telemetry pulse: count @FOC lines over fixed window
print("\n--- @FOC telemetry pulse (5s window) ---")
send_cmd("", 0.2)
ser.reset_input_buffer()
t0 = time.time()
count = 0
window = 5.0
while time.time() - t0 < window:
    chunk = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    count += chunk.count('@FOC:')
    time.sleep(0.05)
freq = count / window
print(f"  @FOC lines in {window:.0f}s: {count} -> {freq:.1f} Hz")
print("  TELEMETRY: " + ("PASS (5-15 Hz, не забивает UART)" if 5 <= freq <= 15 else f"CHECK ({freq:.1f} Hz)"))

# 3) UART responsiveness during telemetry background
print("\n--- UART responsiveness during background @FOC ---")
t0 = time.time()
ser.reset_input_buffer()
ser.write(b"p?\r\n")
time.sleep(0.3)
out = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
resp_time = time.time() - t0
has_pwm = '@PWM:' in out
print(f"  p? response in {resp_time:.2f}s, @PWM present={has_pwm}")
print("  RESPONSIVENESS: " + ("PASS" if has_pwm and resp_time < 0.5 else "CHECK"))

ser.close()
print("=== DONE ===")