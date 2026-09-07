# Runbook: 60 V BOAR Grid Campaign on PC-3 (G0 v4)

Quick-reference for the operator. Full procedure: `README_BOAR_CAPTURE_PC3.md`.
Safety checklist: `SAFETY_CHECKLIST_PC3.md`.

## 1. Before going to bench

On PC-3, from a clean `origin/main`:

```powershell
cd C:\Users\MyHome\Documents\GitHub\OEW
git checkout main
git pull origin main

# Scaffold campaign directory
python tools\boar_campaign_template.py --campaign-root D:\campaign_raw\boar_60v_%date:~-4%%date:~3,2%%date:~0,2% --calibration C:\campaign_raw\accepted\acs712_calibration.json

# Fill G0 approval: copy template, set decision=APPROVED, firmware SHAs
copy docs\templates\grid_campaign_boar\g0_approval_template.json D:\campaign_raw\boar_60v_...\g0_approval.json
# Edit g0_approval.json: firmware_source_sha, firmware_sha256, signatures

# Build commissioning firmware
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1"

# Record SHA256
certutil -hashfile build\firmware.bin SHA256

# Pre-energize readiness
python tools\boar_energize_ready.py --g0-approval D:\campaign_raw\boar_60v_...\g0_approval.json --operator "NAME" --watcher "NAME" --output D:\campaign_raw\boar_60v_...\energize_ready.json
```

## 2. Flash and verify

```powershell
make flash
```

In UART terminal (115200 8N1):
```text
sysinfo          # firmware identity
p?               # PWM off
pdump            # no active output
mapcap status    # should respond (command available)
```

## 3. LOTO and energize

1. LOTO DC-link supply
2. Connect motor leads U/V/W
3. Remove LOTO (both persons agree)
4. Raise DC-link to 60 V, current limit 2 A
5. Verify `vbus_mv` ~ 60000

## 4. Step E: energize-only (30 s)

```text
# LA: trigger on falling SD1/SD2
# Wait 30 s
# In UART:
breakdiag        # expect valid=0
p?               # PWM off
```

If fault: STOP. Do not proceed.

## 5. PB6 verification (one burst)

```text
# LA: trigger on PB6 rising edge
mapcap build=1112490322
mcarm=1112490322
mapcap run
mapcap drain
mapcap status    # COMPLETE
breakdiag        # valid=0
```

Check LA: PB6 pulse ~1.67 ms. Check UART: 8 `@MC:REC` with nonzero `i1`/`i2`.

## 6. Grid capture: 48 points

For each point `r=0..11`, `p=0..3`:

```text
profile_id = 1112490322 + r*4 + p
```

### Sequence per point

```text
mapcap build=<id>
mcarm=<id>
mapcap run
mapcap drain
mapcap status
breakdiag
p?
```

### Evidence checklist per point

- [ ] 8 `@MC:REC` lines, `@MC:DRAIN:records=8`
- [ ] `status=COMPLETE`, `detail=0`, `fault=0`
- [ ] `breakdiag valid=0`
- [ ] `i1` and `i2` nonzero, `status=7` (ADC_FRAME_VALID)
- [ ] Save UART log: `region_<r>_<p>.log`
- [ ] Save LA trace: `la_region_<r>_<p>.csv`

### Profile ID table

| r | sector | window | p0 | p1 | p2 | p3 |
|---|--------|--------|-----|-----|-----|-----|
| 0 | 0 | 0 | 1112490322 | 1112490323 | 1112490324 | 1112490325 |
| 1 | 0 | 1 | 1112490326 | 1112490327 | 1112490328 | 1112490329 |
| 2 | 1 | 0 | 1112490330 | 1112490331 | 1112490332 | 1112490333 |
| 3 | 1 | 1 | 1112490334 | 1112490335 | 1112490336 | 1112490337 |
| 4 | 2 | 0 | 1112490338 | 1112490339 | 1112490340 | 1112490341 |
| 5 | 2 | 1 | 1112490342 | 1112490343 | 1112490344 | 1112490345 |
| 6 | 3 | 0 | 1112490346 | 1112490347 | 1112490348 | 1112490349 |
| 7 | 3 | 1 | 1112490350 | 1112490351 | 1112490352 | 1112490353 |
| 8 | 4 | 0 | 1112490354 | 1112490355 | 1112490356 | 1112490357 |
| 9 | 4 | 1 | 1112490358 | 1112490359 | 1112490360 | 1112490361 |
| 10 | 5 | 0 | 1112490362 | 1112490363 | 1112490364 | 1112490365 |
| 11 | 5 | 1 | 1112490366 | 1112490367 | 1112490368 | 1112490369 |

### Between regions

- Wait >= 30 s thermal settle
- Re-check DC-link 60 V, current limit active

## 7. If FAULT_N transient (sd=1,1 ce=0,0)

```text
breakdiag          # verify sd=1,1 and ce=0,0
# save output to log
breakdiag reset
breakdiag          # verify valid=0
p?                 # PWM off
# re-arm LA, continue
```

Max 5 resets per session. If sd=0 or ce!=0: STOP, do not reset.

## 8. Shutdown

```text
# Set DC-link to 0 V
# LOTO
# Wait 60 s, verify < 1 V with DMM
```

```powershell
make clean
make
make flash
```

Verify in UART: `mapcap` command unavailable (production default-deny).

## 9. Post-session validation

```powershell
python tools\verify_boar_campaign_ready.py --campaign-root D:\campaign_raw\boar_60v_... --scope-waiver
```

Expected: **READY**

Copy campaign folder to PC-2 unmodified.
