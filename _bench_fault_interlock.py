import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(ser, cmd, wait=0.9):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

print("=== FAULT / INTERLOCK TEST (no-HV, default-deny) ===")

# 0) Baseline FOC telemetry
print("\n--- baseline @FOC ---")
out = send_cmd(ser, "p?", 0.7)
foc = [l for l in out.splitlines() if l.startswith('@FOC:')]
print(foc[0] if foc else out[:300])
m = re.search(r"FAULT=(\d+):FAULT_R=(\d+)", out)
print("Fault baseline: " + (f"FAULT={m.group(1)} FAULT_R={m.group(2)}" if m else "not found"))

# 1) Clear fault
print("\n--- f (clear fault) ---")
out = send_cmd(ser, "f", 1.0)
print(out[:400])
print("CLEAR: " + ("PASS (latch cleared)" if "cleared" in out.lower() or "@FAULT:CLEAR" in out else "CHECK"))

# 2) Attempt FOC start (must be interlocked, PWM off)
print("\n--- 1 (FOC start attempt) ---")
out = send_cmd(ser, "1", 1.0)
print(out[:400])
print("START ATTEMPT: " + ("PASS (blocked)" if ("rc=" in out or "FAULT" in out.upper() or "blocked" in out.lower()) else "CHECK"))

# 3) PWM must remain OFF
print("\n--- p? (PWM state after attempt) ---")
out = send_cmd(ser, "p?", 0.7)
pwm = [l for l in out.splitlines() if l.startswith('@PWM:')]
print(pwm[0] if pwm else out[:300])
ccer = re.search(r"CCER=(\w+)", out)
cen_moe_ok = False
if ccer:
    ccer_val = int(ccer.group(1), 16)
    # CEN=0 (bit0 CR1), MOE=0 (bit15 BDTR) -> PWM disabled
    cr1 = re.search(r"CR1=(\w+)", out)
    bdtr = re.search(r"BDTR=(\w+)", out)
    cen = int(cr1.group(1), 16) & 1 if cr1 else None
    moe = (int(bdtr.group(1), 16) >> 15) & 1 if bdtr else None
    print(f"CEN={cen} MOE={moe}")
    cen_moe_ok = (cen == 0 and moe == 0)
print("PWM OFF: " + ("PASS" if cen_moe_ok else "CHECK"))

# 4) FOC stop (safety)
print("\n--- 0 (stop) ---")
out = send_cmd(ser, "0", 0.6)
print(out[:200])

# 5) FOC telemetry after stop (FAULT state re-check)
print("\n--- final @FOC ---")
out = send_cmd(ser, "p?", 0.7)
foc2 = [l for l in out.splitlines() if l.startswith('@FOC:')]
print(foc2[0] if foc2 else out[:300])

ser.close()
print("\n=== DONE ===")