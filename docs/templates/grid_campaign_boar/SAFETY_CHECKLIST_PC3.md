# Safety checklist — BOAR grid MapCapture campaign (ПК-3)

**Classification:** energized bench work with low-voltage DC-link (60 V) and
controlled PWM pulses. This is NOT a no-HV procedure. Every step below is
mandatory unless explicitly marked [optional].

**Prerequisites before opening this checklist:**
- **Step A 10 V bring-up PASS** (see `docs/STEP_A_ACCEPTANCE.md`).
- **ACS712 Phase 1 no-HV checkout PASS** (calibration JSON available).
- Approved Test №3 G0 written procedure signed by operator and safety watcher.
- LOTO (lockout/tagout) plan for DC-link power supply and motor disconnect.
- Two-person rule: operator + safety watcher, both present and signed.
- Emergency stop within arm's reach of both persons.
- Fire extinguisher class C/E present and known to both.
- First aid kit accessible.
- `tools/boar_energize_ready.py` executed and produced `energize_ready.json`
  with all safety prompts confirmed.

## 0. Roles and signatures

| Role | Name | Signature | Date/UTC |
|---|---|---|---|
| Operator | ________ | ________ | ________ |
| Safety watcher | ________ | ________ | ________ |
| Approver (G0) | ________ | ________ | ________ |

> No signature → do not proceed.

## 1. Pre-session hardware setup

- [ ] Motor is mechanically secured; shaft cannot spin freely or engage loads.
- [ ] DC-link power supply set to **60 V** with current limit **≤ 2 A** (or as
  specified in approved procedure). Verify with DMM before connecting.
- [ ] DC-link positive and negative leads are fused and have detachable
  connectors for LOTO.
- [ ] STLINK/SEGKR debugger and UART cable are isolated/insulated.
- [ ] Two independent emergency stops are functional: one on the power supply,
  one on the bench within reach.
- [ ] Oscilloscope and logic analyzer are grounded to the same bench ground as
  the inverter. Use isolated differential probes if available.
- [ ] ACS712 sensors powered from a **5.0 V** external supply, not from the
  inverter J2 header. Confirm `CF ≤ 1 нФ` on sensor outputs; do **not** fit
  47 нФ capacitors.
- [ ] Oscilloscope in **SINGLE** mode, timebase **≤ 500 µs/div**.
  Recommended setup: CH1 = PB6 (trigger source), CH2 = ACS712 U or V.
  Alternative: CH1 = ACS712 U, CH2 = ACS712 V (trigger from CH1/CH2).
- [ ] Logic analyzer (fx2lafw): SD1=D0, SD2=D1, PB6=D2.
  Use D0–D3 only for trigger (D14/D15 trigger unreliable).
- [ ] UART terminal configured for **115200 8N1**, logging to file enabled.

## 2. Firmware identity and default-deny check

- [ ] Repository is on `origin/main`, working tree clean.
- [ ] Commissioning build command:
  ```powershell
  make clean
  make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1"
  ```
- [ ] Build completes without errors; note `firmware.bin` SHA256:
  `________________________________________________`
- [ ] Flash firmware with `make flash` and verify `sysinfo` response.
- [ ] Confirm PWM is **off** after boot:
  ```text
  p?
  pdump
  ```
- [ ] Confirm `mapcap` commands respond; FOC/Vf/autotune commands are still
  blocked (`control_admitted=0`).

## 3. LOTO and energize sequence

- [ ] Apply lockout/tagout to DC-link supply: key removed, tag signed.
- [ ] Verify with DMM that DC-link terminals on the inverter are **< 1 V**.
- [ ] Connect motor leads U/V/W to inverter; verify phase order.
- [ ] Remove LOTO only after both operator and watcher agree.
- [ ] Slowly raise DC-link to 60 V while watching current limit and DMM.
- [ ] Confirm `vbus_mv` reported by firmware is within 55–65 V; if not, stop and
  investigate.

## 3a. Step E — energize-only verification (mandatory before first burst)

- [ ] Start logic capture **before** supply enable: SD1=D0, SD2=D1, PB6=D2.
  Trigger on falling edge of SD1 or SD2 (or continuous/ring capture).
- [ ] Observe 60 V supply for at least 30 s with no MapCapture command.
- [ ] Verify: `FAULT=0`, `breakdiag valid=0`, SD1 and SD2 remain HIGH, PWM off.
- [ ] If fault or SD-low event: disable supply, LOTO, run `breakdiag`, save
  trace. **BLOCKED** — do not proceed.
- [ ] Save energize-only logic trace.

## 3b. PB6 / DSO5202P EXT TRIG verification (de-energized MapCapture with DC-link on)

- [ ] Hantek DSO5202P configured:
  - CH1 = ACS712 U, DC coupling, 100 mV/div, vertical offset ~2.5 V
  - CH2 = ACS712 V, DC coupling, 100 mV/div, vertical offset ~2.5 V
  - EXT TRIG = PB6, SINGLE mode, rising edge, level 1.5 V
  - Timebase = 500 µs/div, horizontal position ~10 % from left
  - Bandwidth limit = 20 MHz (if available)
- [ ] (Optional) Hantek PC software connected via USB device port:
  - driver installed, Hantek Scope (MSScope) running on ПК-3
  - SINGLE trigger mode verified in PC software
  - if PC software does not work with SINGLE: use USB flash save (method A)
- [ ] LA armed: SD1=D0, SD2=D1, PB6=D2; trigger on PB6 rising edge.
- [ ] Run one de-energized MapCapture point to verify EXT TRIG fires and scope captures waveform.
- [ ] Confirm PB6 pulse on LA (D2) and scope waveform present.
- [ ] Confirm `mapcap status` → COMPLETE, `breakdiag valid=0`.
- [ ] If EXT TRIG does not fire or waveform missing: **BLOCKED** — do not proceed to grid capture.

## 4. Per-session (per region/point) procedure

Repeat for each `r = 0..11`, `p = 0..3`:

1. [ ] Compute profile ID: `1112490322 + r*4 + p`.
2. [ ] Operator announces "Region N point P, ID <number>".
3. [ ] Safety watcher confirms ID matches the runbook and no faults are active.
4. [ ] Operator arms and runs capture:
   ```text
   mapcap build=<profile_id>
   mcarm=<profile_id>
   mapcap run
   ```
5. [ ] During the short burst both persons watch for:
   - unexpected motor movement or noise;
   - overcurrent trip on the supply;
   - abnormal heating or smell;
   - oscilloscope clipping/saturating signals.
   - **ACS712 reference on DSO5202P CH1 (U) and CH2 (V) shows clear
     non-zero current pulse synchronized by EXT TRIG PB6. A flat ~2.5 V
     trace means zero winding current — abort immediately; do not continue.**
6. [ ] If any anomaly: operator hits emergency stop, both move to safe state
   (section 7).
7. [ ] If burst is clean:
   ```text
   mapcap drain
   mapcap status
   breakdiag
   ```
   - Expected: 8 `@MC:REC` lines, `@MC:DRAIN:records=8`, status COMPLETE,
     detail=0, fault=0.
   - Expected: `breakdiag valid=0` (no break event captured).
   - If `breakdiag valid=1`: **BLOCKED** — fault occurred, do not continue.
8. [ ] Verify shunt ADC records show nonzero, non-saturated current
   (`i1_ma` and `i2_ma` distinguishable from zero-offset).
9. [ ] Save UART log as `region_<r>_<p>.log`.
10. [ ] Save oscilloscope CSV as `scope_region_<r>_<p>.csv`.
11. [ ] Verify PWM is **off**:
    ```text
    p?
    pdump
    ```
12. [ ] Both sign the per-point log sheet or the timestamped terminal log.

## 5. Between regions

- [ ] Allow inverter and ACS712 sensors to thermally settle (≥ 30 s).
- [ ] Re-check DC-link voltage remains at 60 V and current limit is active.
- [ ] If supply tripped or fault latched, stop, investigate, reset only per
  approved procedure.

## 6. Post-session shutdown

- [ ] Run final `p?` / `pdump`; confirm PWM off and no latched fault.
- [ ] Set DC-link supply to 0 V and disconnect leads.
- [ ] Apply LOTO to DC-link supply; key removed, tag signed.
- [ ] Wait ≥ 60 s for DC-link capacitors to discharge; verify with DMM < 1 V.
- [ ] Flash **production default-deny** firmware:
  ```powershell
  make clean
  make
  make flash
  ```
- [ ] Verify `sysinfo`, confirm `mapcap` / FOC / Vf / autotune commands are
  blocked.

## 7. Emergency/abnormal stop

If ANY of the following occurs, **immediately** hit emergency stop and follow
LOTO:

- motor rotation, vibration, or audible noise;
- DC-link voltage > 65 V or < 55 V;
- supply current > current limit or tripped;
- inverter or wiring temperature > 50 °C or any smoke/smell;
- oscilloscope shows saturated / clipped waveforms;
- firmware reports `FAULT=1` with `breakdiag sd=0,x` or `sd=x,0` (real SD low);
- ACS712 reference trace is flat (zero winding current) for any phase;
- operator or watcher loses sight of the bench.

After emergency stop:
1. LOTO DC-link.
2. Discharge bus capacitors.
3. Document event in evidence log.
4. Do **not** resume until root cause is reviewed and approved by G0 approver.

## 7a. Transient STEVAL FAULT_N break — reset allowed

If `FAULT=1` appears but breakdiag shows the **transient** pattern:

- `breakdiag sd=1,1` (both SD HIGH at ISR entry)
- `breakdiag ce=0,0` (CCER=0, PWM was off)
- no supply CC/trip, no motion/noise/heating

This is a known STEVAL-IPM20B behavior (sporadic sub-microsecond FAULT_N
transients at idle, observed on both SD1/TIM1 and SD2/TIM8 at 10 V and 60 V).

**Procedure:**

1. [ ] Run `breakdiag` — verify sd=1,1 and ce=0,0.
2. [ ] Save breakdiag output to evidence log.
3. [ ] Run `breakdiag reset`.
4. [ ] Verify `FAULT=0`, PWM off (`p?`).
5. [ ] Re-arm LA and scope.
6. [ ] Continue with the same or next grid point.

**Limit: maximum 5 transient resets per session.** If exceeded, session is
BLOCKED regardless of sd/CCER state.

## 8. Evidence integrity

- [ ] All 48 `region_<r>_<p>.log` files present.
- [ ] All 48 `scope_region_<r>_<p>.csv` files present.
- [ ] `calibration/acs712_calibration.json` is from the accepted Phase-1 package.
- [ ] Run on ПК-3:
  ```powershell
  python tools\verify_boar_campaign_ready.py --campaign-root D:\campaign_raw\boar_...
  ```
  Result: **READY**.
- [ ] Copy campaign folder to ПК-2 (or shared drive); do **not** modify files
  after copy.
- [ ] Record final DC-link voltage, current limit, and any deviations.

## 9. Final sign-off

| Checkpoint | Operator | Watcher |
|---|---|---|
| All 48 logs captured and verified | [ ] | [ ] |
| No emergency stop triggered | [ ] | [ ] |
| DC-link de-energized and LOTO applied | [ ] | [ ] |
| Production default-deny firmware restored | [ ] | [ ] |
| Evidence copied to ПК-2 unmodified | [ ] | [ ] |

Operator signature: _______________  Date/UTC: _______________

Safety watcher signature: _______________  Date/UTC: _______________
