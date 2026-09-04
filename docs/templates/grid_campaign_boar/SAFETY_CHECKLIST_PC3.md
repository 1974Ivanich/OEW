# Safety checklist — BOAR grid MapCapture campaign (ПК-3)

**Classification:** energized bench work with low-voltage DC-link (60 V) and
controlled PWM pulses. This is NOT a no-HV procedure. Every step below is
mandatory unless explicitly marked [optional].

**Prerequisites before opening this checklist:**
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
- [ ] Oscilloscope CH1 = phase U (ACS712 U), CH2 = phase V (ACS712 V).
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
   - **ACS712 reference shows clear non-zero current pulse on both CH1 (U)
     and CH2 (V). A flat ~2.7 V trace means zero winding current — abort
     immediately; do not continue to the next point/region.**
6. [ ] If any anomaly: operator hits emergency stop, both move to safe state
   (section 7).
7. [ ] If burst is clean:
   ```text
   mapcap drain
   mapcap status
   ```
   - Expected: 8 `@MC:REC` lines, `@MC:DRAIN:records=8`, status COMPLETE,
     detail=0, fault=0.
8. [ ] Save UART log as `region_<r>_<p>.log`.
9. [ ] Save oscilloscope CSV as `scope_region_<r>_<p>.csv`.
10. [ ] Verify PWM is **off**:
    ```text
    p?
    pdump
    ```
11. [ ] Both sign the per-point log sheet or the timestamped terminal log.

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
- firmware reports `FAULT=1`, `state=FAULTED`, or any unexpected terminal output;
- ACS712 reference trace is flat (zero winding current) for any phase;
- operator or watcher loses sight of the bench.

After emergency stop:
1. LOTO DC-link.
2. Discharge bus capacitors.
3. Document event in evidence log.
4. Do **not** resume until root cause is reviewed and approved by G0 approver.

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
