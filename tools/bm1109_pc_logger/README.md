# BM1109 PC Logger

Read-only PC data logger for the YALU BM1109 baseline bench.

## Scope

- PZEM-004T V3.0 over USB–UART TTL (Modbus RTU).
- AD800-2S2D2-PU00CU00 over USB–RS485 (Modbus RTU), through the CU00 control module.
- One timestamped CSV row per polling cycle, with separate per-device error fields.
- Offline plots from the CSV file.

**This first version is monitoring-only.** It does not implement RUN/STOP, reset, parameter writes, or any other drive control. Do not connect a TTL UART adapter to RS485 terminals. Verify the actual PZEM board variant and the CU00 RS485 terminal markings before wiring.

## Important commissioning limitation

The PZEM-004T V3.0 profile uses the common V3 register layout (function 0x04, ten input registers from 0x0000, default address 0xF8). Confirm this against the exact meter/firmware before relying on measurements.

The AD800 register map and serial framing are deliberately **not guessed**. The supplied configuration has an empty AD800 register list and placeholder serial settings. Fill them only from the exact manual for PU00/CU00 and the actual drive settings. Until then, the logger will record the AD800 connection as unconfigured and continue logging PZEM data.

The AD800 v1.70 PU00/CU04 manual is not automatically assumed to describe the user's PU00/CU00 combination.

## Install

Python 3.10 or newer is recommended.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r tools/bm1109_pc_logger/requirements.txt
```

Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tools/bm1109_pc_logger/requirements.txt
```

## Configure

Copy `config.example.json` to `config.json` and set the actual COM/device ports.

- PZEM: USB–UART TTL adapter; common default 9600 baud, 8 data bits, no parity, 1 stop bit. Confirm board variant and address.
- AD800: USB–RS485 adapter on CU00; set baud, parity, stop bits, slave address and register definitions from the exact manual/device configuration. Keep `registers` empty until verified.

Each AD800 register entry reads one 16-bit register. For multi-register values, add a documented decoder only after confirming word order, signedness and scaling; do not infer them.

## Configuration validation

Before opening any serial port, the program validates enabled-device settings, serial framing, Modbus slave addresses, read function codes, and configured AD800 register definitions. It rejects duplicate COM-port assignments and malformed CSV column names. An empty AD800 register list remains valid as an intentionally unconfigured state; the logger records the device error and continues with the other device.

## Run

From the repository root:

```bash
python -m tools.bm1109_pc_logger.logger --config tools/bm1109_pc_logger/config.json --output data/bm1109
```

Stop with Ctrl+C. The program creates a timestamped CSV file. It will continue if one device is unavailable; errors are written to the corresponding `*_error` column. The PZEM and AD800 are polled sequentially, so timestamps represent the start/end of a cycle rather than simultaneous sampling. This is suitable for slow trend logging, not PWM or transient capture.

## Plot

```bash
python -m tools.bm1109_pc_logger.plot data/bm1109/bm1109_YYYYMMDD_HHMMSS.csv
```

PNG plots are written beside the CSV. Matplotlib is optional and installed by the requirements file.

## CSV fields

The logger stores UTC ISO-8601 timestamps, Unix epoch time, cycle duration, PZEM voltage/current/power/energy/frequency/power factor/alarm, configured AD800 register values, and separate device error strings. Missing or invalid readings are blank, never silently replaced with zero.

## Safety and validation

1. Keep the PC module read-only.
2. Confirm electrical isolation and safe USB connection arrangements for the PZEM measurement circuit. TTL logic levels do not by themselves guarantee mains isolation.
3. Confirm the PZEM is the TTL version before connecting a USB–UART adapter.
4. Confirm RS485 polarity and terminals on CU00 from the exact unit documentation.
5. Verify serial framing and slave address before polling.
6. Validate each displayed value against the device keypad/display and an independent meter under a safe, stable condition.
7. Do not use this logger as an interlock, protection device, emergency stop, or substitute for an oscilloscope/current probe/torque sensor.
8. Do not use PZEM AC input power as shaft output power.

## Current status

Initial software scaffold. Hardware communication, register-map verification, calibration and end-to-end tests remain pending on the actual bench.
