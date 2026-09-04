# Test report — 2026-09-04 (ПК-2)

Post-merge regression test run after accepting the ACS712 no-HV checkout package and signature-filler tooling.

## Environment

- **PC:** ПК-2
- **Repository:** `C:\Users\MyHome\Documents\GitHub\OEW`
- **Branch under test:** `ai2/pc3-checkout-discrepancy` (already merged into `main`)
- **Toolchain:** `arm-none-eabi-gcc` from `C:\ST\STM32CubeCLT_1.22.0\GNU-tools-for-STM32\bin`
- **Python:** 3.13
- **QEMU:** not installed on this Windows host (`qemu-system-arm: not found`)

## Commands and results

### 1. Python tests

```powershell
python -m pytest tests -q
```

Result: **356 passed in 8.96s**.

### 2. Production default-deny build

```powershell
make clean
make
```

Result: **PASS**. Firmware size:

```text
   text    data     bss     dec     hex filename
  68884     516   11472   80872   13be8 build/firmware.elf
```

### 3. Commissioning build (6 required defines)

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1 -DOEW_HS1_COMMISSIONING_RELEASE=1"
```

Result: **PASS**. Firmware size:

```text
   text    data     bss     dec     hex filename
  76572     516   12616   89704   15e68 build/firmware.elf
```

### 4. Hosted C tests

```powershell
make test-hosted
```

Result: **ALL PASS**. Summary of hosted checks:

| Test suite | Checks | Failures |
|---|---|---|
| Auto-Tune math | 25 | 0 |
| V/f start | 14 | 0 |
| Observer / PLL / Flux Weakening | 12 | 0 |
| UART | 10 | 0 |
| Telemetry budget | 13 | 0 |
| Encoder | 12 | 0 |
| CLI golden snapshots | 107 | 0 |
| FOC math (hosted) | 19 | 0 |
| V/f control (hosted) | 32 | 0 |
| CORDIC modulus | several | 0 |
| Voltage manager | 27 | 0 |
| ADC ISR decisions | PASS | 0 |
| FOC handoff gate | PASS | 0 |
| ADC frame dual | PASS | 0 |
| ADC sample time | PASS | 0 |
| Current reconstruct | PASS | 0 |
| PWM HS-1 replacement | PASS | 0 |
| PWM SD monitor-only | PASS | 0 |
| PWM bench aperture | PASS | 0 |
| PWM break init | PASS | 0 |
| FOC start gate | PASS | 0 |
| SD direct interlock | PASS | 0 |
| SD direct latch/clear | PASS | 0 |
| SD direct no-self-rearm | PASS | 0 |
| Frame-aware protection | PASS | 0 |
| Measured map selector | PASS | 0 |
| Map capture service path | PASS | 0 |
| Map capture port boundary | PASS | 0 |
| Map builder | PASS | 0 |
| Map measurement accumulator | PASS | 0 |
| Map solver / certifier | PASS | 0 |
| ADC production dispatch | PASS | 0 |
| Map candidate / commissioning | PASS | 0 |

### 5. Full `make test` (hosted + QEMU + pytest)

```powershell
make test
```

Result: **FAIL at QEMU stage**. Hosted tests and pytest completed successfully, but the QEMU FOC test could not run because `qemu-system-arm` is not installed on ПК-2:

```text
--- FOC math (QEMU) ---
c:/st/stm32cubeclt_1.22.0/make/bin/sh: qemu-system-arm: not found
QEMU FOC test failed
make: *** [Makefile:169: test-qemu] Error 1
```

This is expected for the local Windows environment; the QEMU tests are intended to run in CI on `ubuntu-latest`.

## Conclusion

- All Python tests pass.
- Both production and commissioning firmware builds pass.
- All hosted C tests pass.
- QEMU tests are **not executed** on ПК-2 due to missing `qemu-system-arm`; they remain the responsibility of CI.

No code regressions were introduced by the accepted ACS712 no-HV checkout package.
