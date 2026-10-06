import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

ENC_RE = re.compile(r"@ENC:angle=(\d+):speed=(-?\d+):period_us=(\d+):pulse_us=(\d+):err=(\d+)")

def read_enc():
    ser.reset_input_buffer()
    ser.write(b"enc\r\n")
    deadline = time.time() + 0.5
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(ser.in_waiting)
        if chunk:
            buf += chunk
            if b"@ENC:" in buf and (b"> " in buf or b"@ENC:" in buf.split(b"@ENC:")[-1][-40:]):
                pass
        if b"> " in buf:
            break
    text = buf.decode('utf-8', errors='ignore')
    for line in text.splitlines():
        m = ENC_RE.search(line)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(5))
    return None

print("=== ENCODER MANUAL SPIN TEST ===")
print("POKRUTITE VAL V OBE STORONY (CW i CCW) v techenie 25 s...")
print("angle(0..16383), speed[+]=CW, speed[-]=CCW, err")
print("-" * 60)

start = time.time()
last_angle = None
last_speed = None
min_speed = 0
max_speed = 0
angle_min = 16383
angle_max = 0
err_ok = True

while time.time() - start < 25.0:
    r = read_enc()
    if r is None:
        continue
    angle, speed, period_us, err = r
    if err != 0:
        err_ok = False
        print(f"  !! ERR={err}")
    if speed < min_speed: min_speed = speed
    if speed > max_speed: max_speed = speed
    if angle < angle_min: angle_min = angle
    if angle > angle_max: angle_max = angle
    if angle != last_angle or speed != last_speed:
        arrow = "" if speed == 0 else ("CW+" if speed > 0 else "CCW-")
        print(f"  angle={angle:5d}  speed={speed:+6d}  period={period_us:4d}us  err={err}  {arrow}")
        last_angle = angle
        last_speed = speed
    time.sleep(0.05)

print("-" * 60)
print(f"SUMMARY: angle range {angle_min}..{angle_max} (span={angle_max-angle_min}), "
      f"speed range {min_speed}..{max_speed}, err_ok={err_ok}")
print("PASS: angle changed & both speed signs seen" if (angle_max-angle_min > 50 and min_speed < 0 and max_speed > 0)
      else "CHECK: need angle change and both speed signs")
ser.close()