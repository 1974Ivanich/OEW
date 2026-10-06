import serial, time

PORT = "COM4"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.2)
time.sleep(0.7)

for cmd in ["a?", "dump", "vf?"]:
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.8)
    out = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
    print("--- " + cmd + " ---")
    print(out[:400])
    print()

ser.close()