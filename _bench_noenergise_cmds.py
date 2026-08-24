import serial, time

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.8):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    out = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    # keep only command responses (strip periodic @FOC noise)
    lines = []
    for l in out.splitlines():
        if l.startswith('@FOC:') or l.startswith('>'):
            continue
        lines.append(l)
    return "\n".join(lines)

for cmd in ["stats", "params", "mpapply", "dump8", "curve"]:
    print("--- " + cmd + " ---")
    print(send_cmd(cmd, 0.8)[:500])
    print()

ser.close()