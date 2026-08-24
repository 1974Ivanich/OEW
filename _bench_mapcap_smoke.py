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

def first_lines(out, prefixes, n=1):
    hits = []
    for l in out.splitlines():
        if any(l.startswith(p) for p in prefixes):
            hits.append(l)
            if len(hits) >= n:
                break
    return hits

print("=== MAPCAP SMOKE (commissioning build, no-HV) ===")

# mapcap status — должен ответить (команда скомпилирована)
out = send_cmd("mapcap status", 1.0)
hits = first_lines(out, ["@MC:STATUS"], 1)
print("mapcap status:", hits[0] if hits else out[:200])

# mapcap drain — пустой (records=0), должен дать @MC:DRAIN
out = send_cmd("mapcap drain", 0.8)
hits = first_lines(out, ["@MC:DRAIN", "@MC:REC"], 1)
print("mapcap drain:", hits[0] if hits else out[:200])

# mapcap run — без арма, ожидаем rc != 0 или блокировку
out = send_cmd("mapcap run", 0.8)
hits = first_lines(out, ["@MC:RUN", "@MC:ARM"], 1)
print("mapcap run:", hits[0] if hits else out[:200])

# mcarm — без профиля, ожидаем BLOCKED:PROFILE
out = send_cmd("mcarm=0", 0.8)
hits = first_lines(out, ["@MC:ARM"], 1)
print("mcarm=0:", hits[0] if hits else out[:200])

ser.close()
print("=== DONE ===")