# INA240A1 bench capture protocol — locked-rotor DC current

Instrument: OWON TAO3104A (V3.0.0, SN 2306027)
Probe: 1x (NOT 10x) on all three CH
Shunt: 0.1 ohm per phase
INA240A1: G = 20 V/V, VCC = 5 V -> Vref = 2.5 V -> Vout = 2.5 + 2.0*I [V]
Rotor: locked (standstill), no FOC/Vf running

## 1. Wiring

| Channel | INA240 | Shunt | Probe tip | Probe ground |
|---|---|---|---|---|
| CH1 | U-phase | Rsh_U | OUT pin | GND of INA240 U |
| CH2 | V-phase | Rsh_V | OUT pin | GND of INA240 V |
| CH3 | W-phase | Rsh_W | OUT pin | GND of INA240 W |
| CH4 | GPIO PB6 | - | - | GND |

All three INA240 GND pins must be on the same potential. Verify with multimeter before connecting scope probes.

## 2. Scope front-panel settings (set manually - no USB SET commands on V3.0.0)

| Setting | Value |
|---|---|
| CH1-CH3 scale | 500 mV/div (1x probe) |
| CH4 scale | 1 V/div (1x probe) |
| Timebase | 10 ms/div (DEPMEM = 10 kSa, 10 ms total) |
| Trigger | CH1, rising, level ~2.6 V (between 0 A and +1 A plateau) |
| Acquire mode | SAMPLE |
| Average | OFF (no averaging for this campaign) |
| Coupling | DC all channels |

## 3. STM32 setup

1. Build and flash firmware with `ci` CLI commands available.
2. Ensure FOC/Vf is stopped (PWM gates off).
3. If the firmware supports current injection, inject a constant current through two phases:
   - I_U = +1.0 A, I_V = -1.0 A, I_W = 0 A (or any value <= 1.0 A)
   - If the firmware does not support this, apply a DC current manually via a bench power supply and series resistor to one phase pair.

## 4. Capture sequence

### 4.1 Zero frame (PWM off, no current)

1. Wait for the scope to show a settled trace on all channels.
2. Verify that all three phase outputs sit near 2.5 V (code ~125 at 500 mV/div).
3. Press Run/Stop to freeze.
4. Press Save/Recall -> Type = Wave -> Format = CSV -> All Channels -> Save.
5. Rename the file to zero.csv or keep the default name.
6. Press Single (the scope stays in Single mode; no need to re-arm for the next capture unless the trace drifted).

This zero frame is used by --zero-csv in the analysis script to establish the per-channel zero level independently from the nominal 2.5 V.

### 4.2 Current frame

1. Inject the DC current (or set the firmware to inject it).
2. Wait 1-2 seconds for settling.
3. Press Run (the scope will trigger on CH1 rising edge).
4. When the trace is frozen, press Save/Recall -> Type = Wave -> Format = CSV -> All Channels -> Save.
5. The file will be named something like scope_r0p0_202610XX.csv.

## 5. Post-capture analysis

```bash
# Basic analysis (uses nominal Vref/2 as zero):
python tools/ina240_report.py --csv scope_r0p0_*.csv --vzero 2.5

# Better: use the zero frame:
python tools/ina240_report.py --csv scope_r0p0_*.csv --zero-csv zero.csv

# With selfcheck against instrument measurements:
python tools/ina240_report.py --csv scope_r0p0_*.csv --zero-csv zero.csv --strict
```

Expected outputs:
- VERDICT: REFERENCE_OK - code size <= 20% of amplitude
- KCL: PASS - residual peak <= 20% of amplitude
- Exit code 0 on success, 3 on TIMING_ONLY, 4 on strict selfcheck failure

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| All channels sit at ~2.5 V regardless of current | Probe ground on INA240 GND, not on shunt side |
| One channel inverted (KCL WARN) | Swap probe polarity or check INA240 IN+/IN- wiring |
| Code size > 20% of amplitude | Verify probe is 1x, not 10x; verify scope scale |
| Selfcheck FAIL | Verify zero frame is taken with the same scale/probe as current frame |
| Scope freezes on USB read | Do not send SET commands; use front panel only; power-cycle if frozen |

## 7. Safety notes

- INA240 common-mode range: -4 V to +80 V. At VBUS <= 60 V this is safe.
- Shunt power: P = I^2 * R = 1^2 * 0.1 = 0.1 W. Use >= 0.25 W rated shunts.
- Do NOT connect scope probe ground to the high-side of any shunt.
- All INA240 GND pins must share the same potential before connecting scope grounds.
