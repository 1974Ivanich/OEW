# OEW SD-direct no-HV acceptance protocol — T0'–T9' rev.2

**Board:** STM32G474RE + two STEVAL-IPM20B / SLLIMM 2nd. **Topology:** SD1 (STEVAL-1 TP23/J2) → PB12/TIM1_BKIN and SD2 (STEVAL-2 TP23/J2) → PD2/TIM8_BKIN. The MCU is a read-only listener on each SD net; it must never drive SD. This protocol is a prerequisite for any energized commissioning, map capture, V/f, or FOC.

> **Safety boundary:** DC link is disconnected for every T0'–T9' test. Do not use a push-pull source on SD. Any deliberate low assertion must use an open-drain fixture or an isolated transistor to the relevant module logic ground. A second observer is mandatory for T6b'–T9'.

## 1. Required equipment and common capture plan

Use a DMM, a four-channel or higher isolated oscilloscope, an SWD probe, the default-deny image, and a separate commissioning-release image only where T3' explicitly permits it. Before every test record image SHA-256, board serials, probe ground arrangement, and DC-link voltage.

| Test point | Physical net | Expected idle | Scope use |
|---|---|---:|---|
| SC-A | SD1 / PB12 | high with STEVAL-1 aux powered | trigger on falling edge for TIM1 break |
| SC-B | SD2 / PD2 | high with STEVAL-2 aux powered | trigger on falling edge for TIM8 break |
| SC-C | TIM1 PWM representative output at J2 | static/no switching without HV | verify no unexpected command |
| SC-D | TIM8 PWM representative output at J2 | static/no switching without HV | verify no unexpected command |
| SC-E | TIM1 MOE state via SWD/register snapshot | 0 unless controlled T9' setup | hardware break evidence |
| SC-F | TIM8 MOE state via SWD/register snapshot | 0 unless controlled T9' setup | hardware break evidence |
| SC-G | NRST | high except T6b'/T7' | reset timing and boot evidence |

A PASS requires recorded scope images or register/console evidence, not only an operator statement. Stop immediately on any unexplained PWM output, SD net driven by MCU, rail backfeed, or inability to preserve default-deny after reset.

## 2. T0'–T3': topology and passive startup

| ID | Procedure | Required evidence | PASS criterion |
|---|---|---|---|
| **T0'** | Photograph revision, confirm both DC-link rails are below 1 V, install default-deny image (`OEW_HS1_COMMISSIONING_RELEASE` absent/0). | Photo; DMM values; build flags; hash. | No HV source attached; `PWM_HardwareInterlockHealthy()==false`; CEN/MOE remain low. |
| **T1'** | DMM continuity only: TP23/J2 SD1→PB12; TP23/J2 SD2→PD2. Confirm no continuity tying SD1 and SD2 together. Verify all 12 PWM wires map to their intended J2 pins and aux grounds are common only as designed. | Continuity table, wiring photo. | Each SD reaches only its intended BKIN; no short to PB4/PB5/PB11/PB13 or to a PWM net. |
| **T2'** | With both STEVAL auxiliary logic supplies absent, inspect SC-A/SC-B and request an FOC/PWM start only through normal command path. | SD levels; UART result; TIM registers. | Both SD inputs are low/undefined-safe; interlock false; PWM enable rejected; no CEN/MOE. |
| **T3'** | Apply only auxiliary 3.3 V logic supply to both modules; keep DC link disconnected. Observe SD1/SD2 high through each board R28. Repeat with default-deny image. Optionally use release image solely to observe interlock predicate. | SC-A/SC-B; IDR; BIF/B2IF=0. | Default-deny stays false. In controlled release image only, both high SD lines and clean timers permit interlock=true; neither PWM nor MOE is enabled by this observation. |

## 3. T4'–T7': self-clearing SD and reset behavior

| ID | Procedure | Required evidence | PASS criterion |
|---|---|---|---|
| **T4'** | With aux logic active and release image only, remove STEVAL-1 auxiliary supply. Capture SC-A, TIM1 SR BIF, `@HS1` counter and protection reason. Restore aux; SD1 must return high. Confirm hardware/software fault remains latched until explicit safe clear. Repeat independently for STEVAL-2/TIM8. | Two captures; pre/post SR snapshots; clear result. | Falling SD causes the matching timer break and terminal PWM stop. Returning SD high does not restore interlock/PWM. Clear while SD low is rejected; after SD high plus valid recovery sample clear is explicit and leaves PWM disabled. |
| **T5'** | Not applicable: ARM_REQ truth table is removed with interposer. Confirm PB4/PB5/PB11/PB13 are not driven/configured by this firmware. | GPIO MODER/ODR snapshot. | No firmware-owned ARM, safety-feedback, or heartbeat action remains. |
| **T6b'** | Test-only image: suppress TIM6 IWDG refresh without debug halt. Measure from last marker/refresh to SC-G NRST. Flash/reboot normal default-deny image afterward. | SC-G timing; boot log; post-reset registers. | Reset occurs near nominal 1 s within measured LSI tolerance; boot returns default-deny with CEN/MOE low and no self-rearm. |
| **T7'** | Reset Nucleo and separately remove/restore logic power while SD topology remains connected and DC link absent. | SC-C/SC-D; reset/boot logs. | PWM remains off throughout; boot never enables outputs without a new explicit valid start and release conditions. |

## 4. T8'–T9': direct SD break proof

| ID | Procedure | Required evidence | PASS criterion |
|---|---|---|---|
| **T8'** | With aux logic active, use open-drain fixture to pull **SD1 only** low. Capture SC-A and the matching timer/diagnostic data. Release fixture; verify software latch remains. Attempt clear while low (must fail); then release high and execute safe explicit clear. Repeat for **SD2 only**. | Per-channel scope capture; BIF/B2IF flags; `@HS1` counters; clear result. | SD1 affects TIM1 evidence only; SD2 affects TIM8 evidence only. Each event increments matching diagnostic counter and latches terminal protection. No clear is accepted while its SD remains low. |
| **T9'** | Specialist-only SWD test, DC link absent: set exactly one timer MOE by SWD under a controlled release image; open-drain pull its corresponding SD low. Capture falling SD and MOE becoming 0. Reset immediately after each channel test. | SC-A/SC-B; before/after BDTR/MOE register snapshots; reset log. | Hardware BKIN clears MOE without firmware scheduling. SD release does not reassert MOE. Reset returns default-deny. Never add a production MOE diagnostic write for this test. |

## 5. Acceptance decision

All T0'–T9' results must be recorded as PASS, FAIL, or BLOCKED. Any FAIL is terminal for energized work until root cause, re-test evidence, and independent reviewer signature are recorded. Passing this protocol permits only the next controlled commissioning stage; it does not authorize HV by itself. Measured current map, ADC polarity/gain/offset, dead-time/IPM loss evidence, and the normal FOC release gates remain separate prerequisites.
