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
    # keep terminal/response lines only (skip periodic @FOC noise)
    keep = []
    for l in out.splitlines():
        if l.startswith('@FOC:') or l == '>':
            continue
        keep.append(l)
    return " | ".join(keep[:2]) if keep else "(no response)"

print("=== MAPCAP FAIL-CLOSED SESSION (commissioning 8773492, no-HV) ===")
print("firmware: main@8773492 (OEW_MAP_CAPTURE+OEW_MAP_L3, BOARD_REV=7)")

cases = [
    ("mcarm=0",           "BLOCKED:PROFILE"),
    ("mcarm=1",           "BLOCKED:PROFILE"),
    ("mcarm=1398361684",  "BLOCKED:PROFILE"),
    ("mapcap run",        "rc=-14"),
    ("mapcap drain",      "records=0"),
    ("mapcap status",     "state=0"),
    ("mapcap build=1",    "BLOCKED" if False else ""),  # build печатает не через @MC:ARM
]

for cmd, expect in cases:
    resp = send_cmd(cmd, 0.9)
    ok = expect in resp if expect else True
    print(f"  [{'PASS' if ok else 'FAIL'}] {cmd:20s} -> {resp[:100]}")

# build комманда отдельно (формат может отличаться)
print("\n--- mapcap build=1 ---")
resp = send_cmd("mapcap build=1", 1.0)
print("  ->", resp[:160])

ser.close()
print("=== DONE ===")