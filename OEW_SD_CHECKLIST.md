# OEW SD-direct acceptance checklist (simplified protection)

**Board:** STM32G474RE + two STEVAL-IPM20B. **Topology:** SD1 → PB12/TIM1_BKIN,
SD2 → PD2/TIM8_BKIN (MCU is a read-only listener; never drives SD).

**Protection model (simplified 2026-08):** hardware BKIN removes timer MOE on
SD=0 (works even with a hung CPU, no software involved). The break ISR latches
the central PROTECT fault (`PROTECT_FAULT_HARDWARE_BREAK`) and performs the
terminal stop. Recovery is explicit: SD high + `f` (PROTECT_RequestClear),
then a fresh explicit start. No self-rearm — returning SD alone never restores
PWM; MOE is only rewritten by PWM_Enable.

> Safety boundary: DC link disconnected for every test. Any deliberate low
> assertion on SD must use an open-drain fixture (transistor), never a
> push-pull driver. All tests are no-HV.

## Checklist

| ID | Procedure | Evidence | PASS criterion |
|---|---|---|---|
| **T1** | Build default-deny (`OEW_HS1_COMMISSIONING_RELEASE=0`), flash, verify hash. DC-link rails < 1 V. Apply aux 3.3 V to both modules. | build hash; DMM values | `PWM_HardwareInterlockHealthy()==false`; `p?` → MOE=0; PWM start (`1`) rejected with `rc=-4`; SD1/SD2 high (IDR/`p?`/probe) |
| **T2** | With aux active and default-deny image, pull **SD1 only** low via open-drain fixture, then release. | UART session log; `p?` snapshots | TIM1 break: `BDTR.MOE=0`, CEN off; fault latched (`FAULT_R=HARDWARE_BREAK`); SD1 high again does NOT restore PWM; `f` while SD low is rejected; after SD high + `f` fault clears, PWM stays off until explicit start |
| **T3** | Repeat T2 for **SD2 only** (TIM8). | UART session log; `p?` snapshots | Same as T2, TIM8 evidence |
| **T4** | Reset Nucleo (NRST / power cycle) with SD topology connected, DC link absent. | boot log; `p?` | PWM off throughout; boot never enables outputs without a new explicit valid start; default-deny preserved |

A PASS requires the recorded evidence (UART log + register snapshots), not an
operator statement. Any FAIL is terminal for energized work until root cause
and re-test evidence are recorded.

Passing this checklist permits only the next controlled commissioning stage;
it does not authorize HV by itself. Measured current map, ADC calibration and
the normal FOC release gates remain separate prerequisites.
