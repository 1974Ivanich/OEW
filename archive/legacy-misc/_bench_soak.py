import serial, time, re

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

def send_cmd(cmd, wait=0.5):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(wait)
    return ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

print("=== SOAK TEST (~60s, no-HV) ===")
print("Опрашиваем sysinfo/p?/enc каждые 3 с; следим за счётчиками ADC и устойчивостью.")

start = time.time()
prev = None
ok = True
while time.time() - start < 60.0:
    out = send_cmd("sysinfo", 0.4)
    m = re.search(r'@SYS:CLK=(\d+):PSC=(\d+):TCLK=(\d+):PLLCFGR=0x[0-9A-F]+:OVR=(\d+):JEOS=(\d+):TO=(\d+):JQOVF=(\d+)', out)
    if m:
        cur = (int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)))
        if prev is not None and cur != prev:
            print(f"  ! счётчики изменились: было {prev}, стало {cur}")
            ok = False
        prev = cur
    # PWM state + encoder
    out2 = send_cmd("p?", 0.3)
    pwm = [l for l in out2.splitlines() if l.startswith('@PWM:')]
    pwm_off = bool(pwm) and 'CCER=0' in pwm[0]
    out3 = send_cmd("enc", 0.3)
    enc = [l for l in out3.splitlines() if l.startswith('@ENC:')]
    enc_err0 = bool(enc) and 'err=0' in enc[0]
    if not pwm_off:
        print(f"  ! PWM включился: {pwm[0] if pwm else 'нет @PWM'}")
        ok = False
    if not enc_err0:
        print(f"  ! ENC ошибка: {enc[0] if enc else 'нет @ENC'}")
        ok = False
    if int(time.time() - start) % 10 == 0:
        print(f"  t={int(time.time()-start):2d}s  PWM={pwm[0] if pwm else '??'}  ENC={enc[0] if enc else '??'}")
    time.sleep(3.0)

# Final single ADC + counters
print("\n--- final ---")
out = send_cmd("a")
print(" ", [l for l in out.splitlines() if l.startswith('@ADC:')][0] if any(l.startswith('@ADC:') for l in out.splitlines()) else out[:200])
out = send_cmd("sysinfo")
print(" ", [l for l in out.splitlines() if l.startswith('@SYS:')][0] if any(l.startswith('@SYS:') for l in out.splitlines()) else out[:200])
print(f"\nRESULT: {'PASS' if ok else 'CHECK'}")
ser.close()