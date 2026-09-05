# BOAR low-voltage diagnostic bring-up — ПК-3

## 0. Decision and scope

This procedure is for classifying `PROTECT_FAULT_HARDWARE_BREAK` observed after
the first differential `r0p0` burst on 2026-09-04. It is **not** a grid campaign
and its output must not be used as map characterization evidence.

Approved scope:

- one `r0p0` burst (`profile_id=1112490322`, 8 PWM periods);
- begin at 10 V DC-link;
- 15 V and then 20 V are permitted only as separately approved escalation
  steps after a clean lower-voltage burst;
- hardware BKIN remains enabled; `OEW_SD_MONITOR_ONLY` is forbidden;
- no FOC, V/f, autotune, repeated burst, or full grid campaign;
- any `FAULT_R=18`, source CC transition/trip, unexpected motion/noise,
  temperature rise, smoke/smell, clipped trace, or loss of observation ends the
  session immediately.

**Coordination decision:** first bring-up uses the current reviewed `main`
without first-break ISR modifications. PB12/SD1 and PD2/SD2 are captured directly
by the logic analyzer, which preserves the physical fault source independently
of firmware timing. If the break repeats and the external trace is inconclusive,
stop; create a separate ISR-diagnostics specification before another attempt.

## 1. Required people and evidence

Two people must be present throughout energized work:

| Role | Name | Signature | UTC |
|---|---|---|---|
| Operator | | | |
| Safety watcher | | | |
| G0 approver | | | |

Required files in a new folder `boar_bringup_<UTC>/`:

```text
g0_approval.json
energize_ready.json
pre_energize_uart.log
scope_r0p0_<voltage>V.csv or native scope capture
logic_r0p0_<voltage>V.csv or native logic capture
supply_observation_<voltage>V.md
logs/r0p0_<voltage>V.log
post_burst_<voltage>V.log
flash_commissioning.log
flash_production_restore.log
post_restore_identity.log
BRINGUP_REPORT.md
```

Do not reuse either previous BLOCKED campaign directory.

## 2. Instrument setup — de-energized and LOTO applied

- [ ] DC-link disconnected, LOTO applied, DMM at inverter terminals < 1 V.
- [ ] Motor mechanically secured; open-winding phase connections and phase
  order independently checked.
- [ ] ACS712 U/V series wiring and continuity independently checked.
- [ ] Oscilloscope (if used): CH1 = PB6 or ACS712 U, CH2 = ACS712 U or V,
  trigger = CH1 (PB6) or CH1/CH2 (ACS712), SINGLE mode, timebase <= 500 us/div.
  See "Evidence tiers" below for when scope is required vs. optional.
- [ ] If scope is armed: pre-trigger and post-trigger capture cover at least
  2 ms before and 5 ms after the PB6 falling edge.
- [ ] Logic analyzer channels: PB12/SD1, PD2/SD2, PB6. On the validated
  fx2lafw setup use SD1=D0, SD2=D1 and PB6=D2: hardware triggers were verified
  only on low channels D0–D3; D14/D15 trigger specifications did not fire
  reliably.
- [ ] For the **energize-only stage**, logic capture is already running before
  supply enable and triggers on falling edge of **either SD1 or SD2** (or uses
  a continuous/ring capture). PB6 cannot trigger this stage because PWM is off.
- [ ] For the later burst stage, PB6 may be used as trigger; capture includes at
  least 2 ms before and 5 ms after PB6 falling edge, with a sample rate
  sufficient to resolve short SD pulses.
- [ ] Camera or watcher can see both STEVAL fault LEDs and the supply display.
- [ ] Supply current/CC indication can be recorded during the burst.
- [ ] Emergency stop is accessible to both people.

## 3. Firmware identity

Use the latest green `origin/main`; do not hard-code an earlier SHA:

```powershell
git fetch origin
git checkout --detach origin/main
git status --short
git rev-parse HEAD
```

`git status --short` must show no tracked modifications. Record the source SHA.
Build commissioning firmware without `OEW_SD_MONITOR_ONLY`:

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1"
Get-FileHash build\firmware.bin -Algorithm SHA256
make flash
```

Record build/flash output. Run and log:

```text
sysinfo
p?
pdump
breakdiag
```

Expected: PWM off, `FAULT=0`, mapcap available. Verify the G0 JSON contains the
exact source and firmware SHA-256.

Run readiness only after G0 is signed:

```powershell
python tools\boar_energize_ready.py `
  --g0-approval "D:\campaign_raw\boar_bringup_<UTC>\g0_approval.json" `
  --operator "<operator>" `
  --watcher "<watcher>" `
  --firmware-bin build\firmware.bin `
  --skip-build `
  --output "D:\campaign_raw\boar_bringup_<UTC>\energize_ready.json"
```

## 4. Step E — energize-only classification at 10 V

This stage contains no MapCapture command and must pass before any burst.
The first 2026-09-05 run classified the firmware path as TIM8 BKIN/PD2/SD2
(`src=TIM8`, `sr=1,81`, PWM/capture idle), but did not capture the external SD2
waveform. Subsequent energize-only runs were clean. The accepted CLEAN3 run
used a validated armed trigger for 150 s and observed `FAULT=0`,
`breakdiag valid=0`, SD2 NO_EVENT. This satisfies Step E for a separately
approved single 10 V Step A burst; it does not authorize 15/20/60 V or grid.

1. [ ] Set supply output off and voltage to 10.0 V. Set the current limit from
   the signed G0; do not exceed it.
2. [ ] Start logic capture before supply enable, triggering on falling edge of
   SD1 or SD2 (or continuous/ring capture). Record PB6 as a third channel.
   Merely detecting the fx2lafw device is not sufficient: save sigrok startup
   stdout/stderr and have both operator and watcher confirm acquisition is
   actively armed. If acquisition is not running, do not enable DC-link.
3. [ ] Assign one person to watch/record supply current and CC/CV indication;
   the other operates UART. Both must be able to hit emergency stop.
4. [ ] Remove LOTO only after verbal confirmation from both people.
5. [ ] Enable supply; observe the entire ramp/inrush interval.
6. [ ] If any fault or SD-low event occurs: disable supply, LOTO, run and save
   `breakdiag`, save the external trace and mark BLOCKED. Do not run
   `breakdiag reset`, clear the central fault, retry, or proceed to a burst.
7. [ ] If clean, confirm 9–11 V at the inverter, `FAULT=0`, PWM off and SD1/SD2
   continuously high. Save the energize-only trace.

## 5. Step A — one burst at 10 V

Only after Step E PASS and a new signed G0 with
`escalation.authorize_step_a_10v=true`. Keep `allow_15v=false` and
`allow_20v=false`:

1. [ ] Use a firmware containing MapCapture PB6 sync. With DC-link off,
   validate PB6=D2 using a de-energized MapCapture run, then arm scope and logic
   capture on the PB6 rising edge (`D2=r`). PB6 is high from immediately before
   service-PWM start until after physical PWM disable on every terminal path.
   Save the arming stdout/stderr.
2. [ ] Confirm supply remains at 10 V within G0 limits, `FAULT=0`, PWM off,
   SD1/SD2 high and source not in CC.
3. [ ] Execute exactly once:

   ```text
   mapcap build=1112490322
   mcarm=1112490322
   mapcap run
   mapcap drain
   mapcap status
   p?
   pdump
   ```

4. [ ] Disable supply immediately after post-burst checks. Apply LOTO and verify
   DC-link < 1 V.
5. [ ] Save all UART, scope, logic, supply and LED observations before deciding
   on escalation.

### Step A PASS criteria

All are required:

- `ARM rc=0`, `RUN rc=0`, 8 records, COMPLETE/`term=0`;
- `FAULT=0` after the burst;
- SD1 and SD2 remain high throughout the entire logic trace;
- no source CC transition/trip and no unexpected mechanical/thermal behavior;
- shunt ADC records (`i1_ma`, `i2_ma`) show nonzero, non-saturated current
  distinguishable from the zero-current offset;
- PWM returns off (`CCER=0`, `MOE=0`).

Any failed or missing criterion means **BLOCKED**. Do not escalate.

### Evidence tiers by voltage

| Voltage | Shunt ADC (REC) | LA (PB6+SD) | ACS712 scope | Status |
|---------|-----------------|-------------|-------------- |--------|
| 10–20 V | **required** | **required** | optional (diagnostic) | ACS712-20A SNR < 1 at < 0.5 A; shunt ADC is authoritative |
| 60 V | **required** | **required** | **required** | Independent reference layer for grid-campaign characterization |

**Rationale:** ACS712-20A sensitivity is 100 mV/A. At 10 V DC-link the expected
phase current is 200–400 mA, producing 20–40 mV ACS712 deflection against
~100 mV pp noise floor. The on-board current shunt + OPA + 12-bit ADC path
resolves the same current at tens of ADC counts above offset, providing a
reliable measurement. ACS712 scope becomes meaningful at >= 1 A (>= 100 mV
deflection), which is expected at 60 V. Scope evidence remains mandatory for
the full grid campaign at 60 V.

## 6. Optional escalation — 15 V, then 20 V maximum

Escalation is not automatic. After each clean step, both operator and watcher
must review and sign the evidence, and the G0 must explicitly authorize the
next voltage.

- Step B: repeat Step E and Step A once at 15 V (acceptable measured range
  14–16 V).
- Step C: repeat Step E and Step A once at 20 V (acceptable measured range
  19–21 V).

Never jump directly from 10 V to 20 V. Never exceed 20 V under this procedure.
Each voltage step gets separate logs and traces. A fault or missing trace ends
the session; do not clear and retry during the same energized session.

## 7. Classification matrix

| Observation | Classification/action |
|---|---|
| SD1 low, SD2 high | STEVAL-1 fault path; BLOCKED, inspect first inverter |
| SD1 high, SD2 low | STEVAL-2 fault path; BLOCKED, inspect second inverter |
| SD1 and SD2 low | Common supply/wiring or simultaneous protection; BLOCKED |
| SD pulse + current spike/CC/trip | Real overcurrent/desat likely; BLOCKED |
| SD pulse without current change, clean shunt ADC (or ACS712 at 60 V) | Spurious/transient fault likely; BLOCKED, still do not disable BKIN |
| `FAULT_R=18` but no SD pulse captured | Instrumentation inconclusive; BLOCKED, specify first-break ISR diagnostics |
| No fault, valid shunt ADC current trace | Bring-up step PASS; does not authorize 60 V/full campaign |

## 8. Mandatory shutdown and restore

Regardless of result:

1. Supply off, leads disconnected, LOTO applied.
2. Wait at least 60 s; DMM confirms DC-link < 1 V.
3. Flash production default-deny:

   ```powershell
   make clean
   make
   Get-FileHash build\firmware.bin -Algorithm SHA256
   make flash
   ```

4. Verify `mcarm`/`mapcap` are unavailable, `FAULT=0`, PWM off.
5. Archive the new folder and report SHA-256.

A clean 10/15/20 V result only resolves diagnostic bring-up. A separate review
and approval are required before any return to 60 V or the 48-point campaign.
