# BM1109 OEW-6T — Daily research log — 2026-11-24

> Consolidated index of the BM1109-related engineering work discussed and/or committed on 24 November 2026. This is a research log, not a claim that every item was independently reproduced in this session. Test counts and run results are attributed to the project-owner reports or the referenced Git commits. Synthetic validation is not physical validation of BM1109.

## 0. Executive summary

Today's work covered several parallel tracks:

1. DOE_1 and DOE_2A/2B mathematical contracts and validation strategy.
2. DOE_2C dual-inverter/DC-link power accounting, source model, ESR treatment, integration guard and tests.
3. A separate offline variable-inductance three-wire-star passive-braking solver, including energy/mechanical-power audit, angle-reference handling and timestep convergence.
4. BM1109 Parameter DB v0.1 architecture.
5. NUCLEO-G474RE PWM/timer synchronization and three-channel ADC current-sensing plan.
6. Methodological review of six Habr references relevant to power electronics, motor control, timing, supervision and system integration.

The selected BM1109 motor topology remains OEW-6T: three independent phase branches U, V, W, six physical winding terminals U1/U2, V1/V2, W1/W2, no shared phase neutral, two three-phase bridges on a common DC link. No constraint iU+iV+iW=0 is to be imposed on the BM1109 OEW model.

## 1. DOE_1 — magnetic ROM identification and validation

### 1.1 Current-space sampling

The DOE_1 grid reported for this project contains 33 current points and 12 rotor-angle points (396 FEM cases):
- 1 centre point;
- 12 axis points (four each on d, q and 0 axes);
- 12 pairwise ±0.5 combinations;
- 8 three-axis ±0.5 combinations.

Do not replace this with the earlier incorrect 81-point count.

### 1.2 Angle plan and harmonic resolution

The angle plan supports electrical-period, full-mechanical-revolution and custom mechanical-span modes. It checks period closure and harmonic resolvability. Twelve samples per electrical period resolve at most the configured lower harmonics; the Nyquist boundary is guarded explicitly. A full mechanical revolution must be sized using pole-pair count, not assumed equivalent to one electrical period.

Warnings are advisory; hard sampling/aliasing violations reject the plan. Fourier fit truncation and residual reporting are intended to expose unexplained angular content.

### 1.3 Derivatives, domain and loop closure

- Application and FEM domains are distinct (Ω_op and Ω_FEM); stencils crossing the FEM boundary are incomplete rather than silently extrapolated.
- Second-order one-sided differences are used when the central symmetric stencil is unavailable.
- The two reported current-space loop paths check ∮ ψᵀdi and normalize by path magnitude; a synthetic non-conservative field is used to test sign and area scaling.
- Incomplete derivative stencils must not be represented as a complete ROM result.

### 1.4 Co-energy, flux and zero-current state

The magnetic model is based on:
```text
W' = W'(i_U, i_V, i_W, theta_m)
psi = gradient_i W'
L_inc = Hessian_i W'
T_e = partial W'/partial theta_m at fixed physical phase currents
```

The incremental inductance matrix should be symmetric (reciprocity). If co-energy is not directly available from Maxwell, reconstruct it by integrating flux linkages and test path independence. The zero-state flux consistency gate is blocking; angular ripple at zero current is diagnostic and must not erase real PM flux or slotting effects.

DOE_1 also tracks torque consistency between FEM, flux and energy methods, and sensitivity to the independent zero-sequence coordinate i0. The A/B/C classification is to be made over operating regions/trajectories, not from one isolated point.

Reported synthetic verification in earlier project notes: 5,209 synthetic ROM cases, reciprocity near machine precision, very small loop residual and energy/torque agreement. These results are software/synthetic checks only, not BM1109 FEM evidence.

## 2. DOE_2A — voltage reconstruction and resistance matrix

In dq0, the voltage equation includes resistive drop, flux derivative and rotating-frame terms:
```text
u_dq0 = R_dq0*i_dq0 + L_dq0*d(i_dq0)/dt
        + omega_e*(partial(psi_dq0)/partial(theta_e)|i + J*psi)
J*psi = (-psi_q, psi_d, 0)
R_dq0 = T*diag(R_U,R_V,R_W)*T^T
```

The orthonormal Clarke/Park transform is power invariant. Three independent physical phase currents are retained; i0 is a mathematical coordinate, not a physical common neutral.

Reported DOE_2A checks include direct UVW versus dq0 voltage reconstruction, coordinate power invariance, and resistance-matrix tests for symmetry, trace invariance and positive definiteness when all phase resistances are positive. Unequal phase resistances are supported; a single scalar R is only the equal-phase special case.

## 3. DOE_2B — two bridges and PWM feasibility

For each phase branch:
```text
u_k = Vdc*(d_k1 - d_k2)
delta_k = u_k/Vdc
d_k1 = c_k + delta_k/2
d_k2 = c_k - delta_k/2
```

The ideal duty feasibility condition is distinct from real switching accuracy:
- Level A: duty geometry and min/max duty limits.
- Level B: dead-time and minimum-pulse effects require switching model, driver timing and current polarity; until then dead-time accuracy remains NOT_EVALUATED.

The common-mode duty centre c_k affects power exchange between bridges, although total ideal motor-terminal power is independent of c_k. No c_k optimization was started today. The model must not infer dead-time-corrected voltage without the missing physical timing/polarity inputs.

## 4. DOE_2C — dual-bridge and DC-link power accounting

Detailed record: [BM1109_DOE2C_PROGRESS_2026-11-24.md](BM1109_DOE2C_PROGRESS_2026-11-24.md).

### 4.1 Distinguish phase-current sum from duty-weighted current

```text
I_phase_sum = i_U + i_V + i_W
I_c = sum(c_k*i_k)
P1 = Vdc*I_c + Pmotor/2
P2 = -Vdc*I_c + Pmotor/2
P1 - P2 = 2*Vdc*I_c
P1 + P2 = Pmotor = sum(u_k*i_k)
```

The reported 8.6603 A is the unweighted sum magnitude; 4.3301 A is the duty-weighted current at c_k=0.5. With Vdc=48 V, the bridge power difference is about 415.69 W, while the maximum individual bridge power is about 208.47 W. The original apparent mismatch came from applying the identity to the wrong current definition. The implementation reportedly added I_c_A and a per-row common-mode identity gate.

### 4.2 Thevenin source

Keep separate:
```text
P_terminal = V_source*I_source
P_series = I_source^2*R_source
P_bus = Vdc*I_source
P_terminal = P_bus + P_series   # unconstrained Thevenin source
```

The bus energy equation must use bus-side power. A current/power clamp must be represented consistently with the source law. A curtailed-power term is not automatically dissipated heat: the model must distinguish curtailed source production from a physical dissipative clamp.

### 4.3 Capacitor ESR and energy state

The reported code correction estimates capacitor current from source current less bus load current and calculates ESR heating from capacitor current, not source current. This is an averaged estimate only. It does not provide capacitor ripple RMS current, spectrum, ESL or switching-resolved ESR loss.

For an explicit series-ESR capacitor model, distinguish internal capacitor voltage V_C and terminal voltage Vdc:
```text
Vdc = V_C + ESR*I_C
E_C = 0.5*C*V_C^2
dE_C/dt = V_C*I_C = Vdc*I_C - ESR*I_C^2
```
This energy identity is a recommended follow-up gate; ensure ESR is not double-counted.

Average power-equivalent bridge currents are not switch RMS/peak currents. Empty RMS/ripple CSV fields mean NOT_EVALUATED, not zero.

### 4.4 Numerical integration

A reported explicit-Euler case used step=1 ms with R_source*C_DC=100 us and allowed the energy state to become negative. The code was reportedly changed to stop on invalid energy and mark incomplete energy-balance evaluation NOT_EVALUATED rather than PASS. Add timestep refinement/convergence checks; stability alone does not establish accuracy.

### 4.5 Reported tests and limits

Project-owner report: 71 tests passed across DOE_1/DOE_2/DOE_2C; three selftests exit 0; synthetic DOE_2C run computed 396/396 rows with PASS_2C. Inverter losses, bridge RMS/peak and capacitor ripple RMS remain NOT_EVALUATED. Synthetic parameters (48 V, 470 uF, ESR 0.01 ohm, source R 0.1 ohm, 20 kHz and 20 us step) are not BM1109 hardware parameters. DOE_2D remains deferred.

Recommended closure gates: capacitor energy/ESR identity, reverse-direction source behavior, DC-link timestep convergence, and clear distinction between source curtailment and dissipative clamp power.

## 5. Separate offline variable-inductance three-wire-star passive-braking solver

This was also committed on 24 November and must not be confused with the OEW-6T model. Its own documentation explicitly defines a three-wire star with floating neutral; it is not an OEW six-terminal model, rectifier, DC-link or hardware-control implementation.

### 5.1 Model and equations

The new offline solver uses measured open-circuit phase BEMF, angle-dependent symmetric positive-definite L_abc(theta), winding and load resistance, and an RK4 time integrator. Generator-positive phase currents flow out of the machine. A 4x4 KKT system enforces the floating-neutral constraint:
```text
L(theta)*dj/dt + omega_e*(dL/dtheta)*j + R_total*j + vn*1 = e_oc
1^T*dj/dt = 0
```

The variable-inductance speed term omega_e*(dL/dtheta)*j is essential. The solver reports mean braking torque, PM/BEMF electrical power, reluctance conversion power, copper/load loss, phase RMS/peak current, current-sum error, periodic convergence and energy residual.

### 5.2 Co-energy and power balance

The co-energy helper uses motor-positive current i_motor=-j_generator. The audit distinguishes:
- PM open-circuit-BEMF power;
- signed reluctance conversion power;
- total mechanical braking power;
- copper and external load losses.

For a periodic solution the mechanical and electrical power relations must close. A dedicated synthetic salient-rotor test constructs BEMF and L(theta) from the same rotor reference, rather than combining unrelated angle origins.

### 5.3 Angle reference and numerical convergence

The solver exposes separate BEMF and inductance electrical-angle offsets. A common shift of both tables must preserve periodic mean torque/power; a relative shift changes their physical phase relation. Offsets must come from a shared measurement/indexing reference and are not inferred automatically.

Reported tests cover common-angle-shift invariance and timestep refinement at 512/1024/2048 steps per electrical period, with a reported final torque difference below 0.2%. A later correction adds the exact magnetic-energy endpoint term to the energy audit; the documentation notes remaining time-integration/quadrature error and omitted saturation, hysteresis and core-loss states.

These are offline model/synthetic tests, not BM1109 hardware validation. This star solver is a separate research branch and must not be substituted for DOE_1/DOE_2 OEW-6T.

## 6. BM1109 Parameter DB v0.1

Agreed architecture: a versioned contract between Python DOE, Maxwell ROM, MCU, FPGA Supervisor and bench telemetry, without changing DOE_1/DOE_2 mathematics.

Three layers:
1. Canonical JSON parameter database with JSON Schema, stable ID registry, provenance, profiles and validation.
2. Maxwell ROM/model artifacts stored separately with manifest and SHA-256.
3. Generated Python/C/FPGA/bench interfaces; generated files are not manually edited.

Parameter records should carry stable uint32 ID, name, value (null means unknown), type, SI unit, status, source/provenance, conditions, uncertainty, limits and description. Derived values need formula IDs and input references. Statuses distinguish UNVERIFIED, ASSUMED, SYNTHETIC_VALIDATED, FEM_VALIDATED and BENCH_VALIDATED. Software tests must not promote a physical parameter to BENCH_VALIDATED.

Synthetic profiles must be isolated from physical BM1109 configuration. Unknown hardware limits must not silently become zero or arbitrary generated thresholds. Run manifests should pin database/schema/profile versions, tool and generator versions, git commit, model ID/hash, inputs/outputs hashes and validation states.

Proposed v0.1 sequence: schema, empty physical DB with confirmed topology only, ID registry, topology/provenance validators, profile loader, DOE adapter that preserves numerical results, immutable run manifest. Geometry/magnetic-ROM parameter population follows only when sourced.

## 7. NUCLEO-G474RE PWM/timer synchronization

Selected controller board: NUCLEO-G474RE / STM32G474RET6, LQFP64.

The reviewed STM32G4 timer routes include TIM1_TRGO to TIM8_ITR0 and TIM8_TRGO to TIM1_ITR5. These routes enable hardware synchronization experiments but do not by themselves prove the correct PWM phase or update behavior.

Candidate approaches:
- one-shot trigger-mode synchronization at start, then common free-running counters;
- periodic reset-mode synchronization, only if center-aligned counter direction, update events, preload transfer and repetition counter behavior are verified.

Do not assume a periodic slave reset is harmless: it can alter center-aligned CNT/DIR and generate update events. Verify RCR, URS, UDIS, UG, ARPE, OCxPE, preload transfer, COM behavior and Break/MOE handling.

The two bridges have six CCR duty values (three per timer) and twelve gate outputs. All six CCR values must be staged as one generation and applied coherently. A missed FOC deadline must not produce a partial bridge update. Hardware Break and independent FPGA Supervisor shutdown must disable both bridges.

ADC trigger selection is not the same as the analog sampling instant. TIM1_TRGO2 and OC4REF are candidates requiring exact MMS2/event/edge validation and oscilloscope confirmation. Measure ADC-to-PWM apply latency, jitter and inter-bridge timing. Initial carrier phase shift remains 0 degrees until evidence supports another setting.

## 8. NUCLEO-G474RE ADC current sensing v0.1

Recorded proposed pin allocation:
| Phase | GPIO | ADC channel | Connector |
|---|---|---|---|
| U | PA0 | ADC1_IN1 | CN8 Arduino A0 |
| V | PA1 | ADC2_IN2 | CN8 Arduino A1 |
| W | PB1 | ADC3_IN1 | CN10 Morpho contact 23 |

Proposed acquisition: ADC1/ADC2 dual injected simultaneous pair, ADC3 independent injected conversion from the common TIM1_TRGO2 event. Do not describe all three conversions as hardware-simultaneous: ADC3 skew relative to ADC1/ADC2 must be measured.

Analog requirements: valid 0..VDDA range, bidirectional current offset, input protection, analog filtering, source-impedance/sample-time settling, per-channel offset/gain calibration and actual VREF verification. PB1 is intentionally allocated to ADC3_IN1 rather than consuming an additional ADC1 channel. This pin plan is a recorded proposal and must still be checked against the complete PWM/Break/enable/FPGA/communications pin map and board schematic.

Planned no-power-stage validation: apply a common safe analog test signal to PA0/PA1/PB1; verify dual injected ADC1/2, independent ADC3 trigger count, overruns, relative sample skew, calibration and generation coherence.

## 9. Habr methodological references reviewed

These articles are engineering-methodology references. Their specific board choices, numerical values and single-inverter assumptions are not BM1109 design parameters.

1. [Habr 769004 — PWM timing](https://habr.com/ru/articles/769004/): synchronized duty updates across bridges, preload/update timing, dead-time/minimum-pulse transitions, ADC windows, PWM latency and independent hardware shutdown.
2. [Habr 727782 — Axiom](https://habr.com/ru/articles/727782/): separation of MCU FOC and independent FPGA Supervisor, fault latching, bridge shutdown, diagnostics, current/DC-link/temperature sensing and position feedback. Do not transfer Axiom ratings/topology.
3. [Habr 925660 — MC80 / firmware engineering](https://habr.com/ru/articles/925660/): generated parameter interfaces, diagnostics, RTOS separation, WCET/jitter/latency measurement and pre-fault ring buffers. Do not transfer RA8M1/ThreadX numerical configuration.
4. [Habr 429734 — DIY electric paramotor](https://habr.com/ru/articles/429734/): system-level optimization of motor, inverter, source, cabling and cooling; specific article ratings/material values are not BM1109 parameters.
5. [Habr 1023940 — hardware timer synchronization](https://habr.com/ru/articles/1023940/): Master-Slave TRGO/ITR synchronization, separate motor electrical angle/carrier phase/ADC sample timing, synchronized updates and Break/restart. STM32F407 examples do not prove STM32G474 configuration.
6. [Habr 401873 — open motor-control module](https://habr.com/ru/articles/401873/): gate-charge/driver-current/thermal limits on PWM frequency; three current channels, offset/calibration, analog filtering/settling, DC-link ripple RMS/ESR/thermal life and fault monitoring. Do not transfer TIDA-00901/DRV8305 ratings or its ordinary three-phase topology. The referenced GitHub project was not independently reviewed through the connector.

## 10. Decisions and non-decisions

### Confirmed project-level decisions
- BM1109 remains OEW-6T: three independent phase branches and six physical ends; no shared phase neutral.
- Do not impose iU+iV+iW=0 on the OEW model.
- Preserve the orthonormal dq0 transform and independent i0 coordinate.
- Keep DOE_1/DOE_2 mathematical contracts separate from synthetic validation profiles.
- Initial PWM carrier phase shift is 0 degrees pending evidence.
- ADC sensing proposal uses PA0/PA1/PB1 with ADC1/ADC2 dual injected and ADC3 independent injected.
- Keep DOE_2D deferred until DOE_2C model-consistency gates are closed.

### Not established / not authorized by this log
- No physical BM1109 FEM or bench validation is claimed.
- No real PWM frequency, current limit, voltage limit, thermal limit or capacitor rating is inferred from Habr examples or synthetic tests.
- No RMS/ripple/peak bridge-current capability is claimed from average-power-equivalent currents.
- No final complete GPIO pin map is claimed.
- No G9 passport wording or status was changed here.
- The variable-L floating-neutral star solver is not an OEW-6T solver.
- No claim that DOE_2D or c_k optimization has begun.

## 11. Suggested next actions

1. Close DOE_2C capacitor internal-energy/ESR identity, reverse-power source behavior and timestep convergence.
2. Clarify source current-limiter semantics (curtailed source production versus dissipative clamp).
3. Keep RMS/peak/ripple quantities NOT_EVALUATED until switching/ripple modeling exists.
4. Complete the NUCLEO-G474RE GPIO conflict matrix against PWM, Break, enable, FPGA Supervisor and telemetry.
5. Validate TIM1/TIM8 synchronization and ADC sample timing on hardware with power stage disabled.
6. Continue Parameter DB v0.1 without inventing physical values.
7. Only then resume DOE_2D.

## 12. Related GitHub records from 24 November

- [Variable-L star solver implementation](https://github.com/1974Ivanich/OEW/commit/83b3de7bf2b9e66cf39d834a489f3befa8d574f2)
- [Variable-L star solver tests](https://github.com/1974Ivanich/OEW/commit/8eac4d519d2f7410df403747558cd6fbb6cb5251)
- [Variable-L star solver model specification](https://github.com/1974Ivanich/OEW/commit/7025ca580db151d60f23613e01807b4859cf0c33)
- [Salient rotor mechanical power-balance test](https://github.com/1974Ivanich/OEW/commit/d4c207f938f6346a57d30c9ad540034cb5d6a120)
- [PM and braking power definitions](https://github.com/1974Ivanich/OEW/commit/a7c5ee59809a1ed3fe4b98a5b6e8fea84dfdb07a)
- [Signed reluctance conversion test correction](https://github.com/1974Ivanich/OEW/commit/5cadb3c9ed89dff488342c0284765bdc8ae3b013)
- [Electrical angle reference parameters](https://github.com/1974Ivanich/OEW/commit/e67f44b22449ccc4af1af988a06a5fdfb27276c6)
- [Angle-reference and timestep-convergence tests](https://github.com/1974Ivanich/OEW/commit/b398950ffdeb5183ae0020444955c5b3691a1214)
- [Rotor angle and convergence documentation](https://github.com/1974Ivanich/OEW/commit/fd3b6894d2455a91675f6818d7aba21dc7c36be2)
- [Exact magnetic endpoint-energy audit fix](https://github.com/1974Ivanich/OEW/commit/69000f18edb093c0b38d8defbe24d8ccc605f3aa)
- [Endpoint-energy audit documentation](https://github.com/1974Ivanich/OEW/commit/0ff027f125549efc9c7a9f38f9d01d9fcf236c41)
- [DOE_2C daily progress record](https://github.com/1974Ivanich/OEW/commit/66972fdef35847bddf3a9ff394272683b60a9303)

This daily log is an index and consolidated record. Detailed implementation truth remains in source code, tests and the individual technical records linked above.

---
Date: 2026-11-24
Repository: 1974Ivanich/OEW
Branch: main
