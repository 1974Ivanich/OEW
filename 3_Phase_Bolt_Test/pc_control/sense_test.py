#!/usr/bin/env python3
"""Simple console tool for STEVAL-IPM20B current sense test project.

No GUI, no plots — just raw UART I/O for debugging current sensors.

Usage:
    python sense_test.py COM12

Commands (type and press Enter):
    adc            read raw ADC once
    adccont        continuous ADC print
    adcstop        stop continuous print
    calib          calibrate zero-current offsets
    i              compute/print currents
    dc A 850       DC test phase A with duty offset 850
    stop           stop DC test
    scaleA 0.00035  set scale factor
    help           command list
    q              quit
"""
import sys
import threading
import time
import serial


def reader(ser, stop_event):
    while not stop_event.is_set():
        try:
            line = ser.readline().decode('ascii', errors='ignore').strip()
            if line:
                print(line)
        except Exception as e:
            print("[read error]", e)
            break


def main(port):
    ser = serial.Serial(port, 115200, timeout=0.1)
    stop_event = threading.Event()
    t = threading.Thread(target=reader, args=(ser, stop_event), daemon=True)
    t.start()

    print("Connected to", port)
    print("Type 'help' for commands, 'q' to quit.\n")

    try:
        while True:
            cmd = input("> ").strip()
            if not cmd:
                continue
            if cmd.lower() in ('q', 'quit', 'exit'):
                break
            ser.write((cmd + "\r").encode('ascii'))
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        t.join(timeout=0.5)
        ser.close()
        print("Closed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sense_test.py COM_PORT")
        sys.exit(1)
    main(sys.argv[1])
