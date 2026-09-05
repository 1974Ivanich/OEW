# Step A Acceptance — 10 V r0p0 burst on PC-3

**Decision: PASS**

Date: 2026-09-05
Procedure: `README_BOAR_LOW_VOLTAGE_BRINGUP_PC3.md`, Step A

## Evidence package

Archive: `boar_stepa_20260905T090358Z_STEP_A.zip`
SHA-256: `75E14BBF7F6913BD6B322DA7C9C4FD1A29B25394F2E6608EF985D8FC0CDE2D0A`

### Package contents

| File | Present | Verified |
|------|---------|----------|
| g0_approval.json | yes | APPROVED, step_a_10v authorized |
| energize_ready.json | yes | all checks passed |
| pre_energize_uart.log | yes | FAULT=0, PWM off |
| logs/r0p0_10V.log | yes | 8 REC, COMPLETE, term=0, FAULT=0 |
| post_burst_10V.log | yes | FAULT=0, PWM off |
| flash_commissioning.log | yes | SHA CB2DAE1C... |
| flash_production_restore.log | yes | SHA BC7C8A17... |
| post_restore_identity.log | yes | mapcap unavailable |
| logic/pb6_sync.csv | yes | PB6 HIGH ~1.67 ms |
| logic/mapcap_burst.csv | yes | SD1/SD2 high throughout |
| scope/ | empty | see "ACS712 scope waiver" below |

## PASS criteria evaluation

| Criterion | Result |
|-----------|--------|
| ARM rc=0, RUN rc=0 | PASS |
| 8 records, COMPLETE, term=0 | PASS: 8/8 REC |
| FAULT=0 after burst | PASS |
| SD1/SD2 high throughout LA trace | PASS |
| No CC transition/trip | PASS |
| Shunt ADC nonzero, non-saturated | PASS: i1 up to 294 mA, i2 up to 255 mA |
| PWM off (CCER=0, MOE=0) | PASS |

## Shunt ADC evidence

The on-board current shunt path (STEVAL shunt resistor + OPA + STM32G474
12-bit ADC2 injected channels) recorded current in every REC frame:

- `raw_i1` values differ from zero-offset (~2040) by tens of ADC counts
- `raw_i2` values differ from zero-offset (~2069) by tens of ADC counts
- `i1_ma` up to 294 mA, `i2_ma` up to 255 mA
- No ADC saturation (raw values well within 1–4094 range)

The differential CCR vector was verified correct:

- TIM1 CCR: 625, 500, 375
- TIM8 CCR: 500, 375, 625 (cyclic shift of TIM1)

This confirms nonzero current flow through the open-winding motor phases,
which is the physical objective of the 10 V diagnostic burst.

## ACS712 scope waiver

The ACS712-20A scope evidence requirement is **waived for 10 V** based on the
following analysis:

- ACS712-20A sensitivity: 100 mV/A
- Expected current at 10 V: 200-400 mA
- Expected ACS712 deflection: 20-40 mV
- ACS712 noise floor: ~100 mV pp (measured)
- **Signal-to-noise ratio < 1**: the ACS712 cannot reliably resolve the current

The on-board shunt ADC path provides tens of ADC counts of signal above the
zero-current offset, which is a reliable and authoritative measurement.

ACS712 scope evidence becomes mandatory at 60 V, where expected current of
several amperes produces hundreds of mV deflection (SNR >> 1).

The FNIRSI-1014D waveform file (`1.wav`, SHA-256
`E29E0C272120A4EFBFF77954B094DD98809660C34A4DCBB0A6F41B329B34BCA8`) was decoded
and found to contain nonzero signals, but:

1. Trigger was CH2 rising AUTO, not PB6
2. Timebase was 20 ms/div, too slow to resolve the 1.67 ms service window
3. The 2-channel FNIRSI cannot simultaneously record PB6 + two ACS712 channels

This file is classified as **diagnostic-only** and is not formal evidence.

## LA evidence

Logic analyzer captured:

- PB6 (D2): HIGH for ~1.67 ms during the burst, consistent with 8 PWM
  periods at the configured frequency
- SD1 (D0): HIGH throughout (no STEVAL-1 fault)
- SD2 (D1): HIGH throughout (no STEVAL-2 fault)

## What this acceptance authorizes

- Step A at 10 V is formally PASS
- The differential open-winding PWM vector is confirmed working
- The shunt ADC current measurement path is confirmed working
- PB6 synchronization is confirmed working

## What this acceptance does NOT authorize

- Step B (15 V), Step C (20 V), or 60 V operation
- Full 48-point grid campaign
- Disabling BKIN or enabling OEW_SD_MONITOR_ONLY
- Using the ACS712 scope waiver at 60 V
- Treating these measurements as grid-campaign characterization data

Each next step requires a separate G0, evidence package, and review.
