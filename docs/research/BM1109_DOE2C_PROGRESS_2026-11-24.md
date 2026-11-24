# BM1109 OEW-6T — DOE_2C progress record (2026-11-24)

> This record captures the engineering results reported for 24 November 2026. The code changes and test runs below were reported by the project owner; this GitHub documentation commit does not independently reproduce or certify those runs.

## Scope

DOE_2C DC-link and dual-inverter power accounting, following DOE_2A/DOE_2B. DOE_1 and DOE_2A/2B mathematics were not intentionally changed. DOE_2D has not started.

## 1. Bridge common-mode accounting

The apparent mismatch between 8.66 A and approximately 208 W was a naming/interpretation issue, not a power-model defect.

Two different quantities must remain distinct:

- `I_phase_sum_A = i_U + i_V + i_W`: unweighted sum of the three independent OEW branch currents.
- `I_c_A = Σ(c_k i_k)`: duty-weighted current that determines the inter-bridge common-mode power exchange.

For the bridge definitions used by DOE_2C:

```text
P1 = Vdc * I_c + Pmotor / 2
P2 = -Vdc * I_c + Pmotor / 2
P1 - P2 = 2 * Vdc * I_c
P1 + P2 = Pmotor = Σ(u_k * i_k)
```

At the reported synthetic operating point with Vdc = 48 V and c_k = 0.5:

- max |I_phase_sum| = 8.6603 A
- max |I_c| = 4.3301 A
- max |P1 - P2| = 415.69 W
- max |P1| = 208.47 W

The reported per-row common-mode identity residual was 3.55e-14 relative. The implementation reportedly adds `I_c_A` and a `bridge_power_common_mode` gate. Keep the unweighted phase-current sum and duty-weighted current separately named; do not call both simply “common-mode current”.

## 2. Thevenin source power accounting

The source-side terminal power and the power delivered to the DC bus are distinct:

```text
P_source_terminal = V_source * I_source
P_source_series   = I_source^2 * R_source
P_bus             = Vdc * I_source
P_source_terminal = P_bus + P_source_series   # unclipped Thevenin source
```

The reported implementation now uses bus-side power in the DC-link energy equation, separately records terminal power and series-resistance dissipation, and adds a `source_power_accounting` gate.

Current limiting must not be represented by clipping power while retaining an inconsistent unconstrained Thevenin current law. The reported implementation introduces an explicit curtailed/clamp-power accounting term and checks the terminal-to-bus power identity. Its physical interpretation depends on whether the limiter actually dissipates excess power or merely curtails source production; these cases must not be conflated. In the reported 396-row synthetic run, no clipping occurred and P_clamp was zero.

## 3. Capacitor ESR and current definitions

The reported correction calculates capacitor ESR heating from capacitor current rather than source current:

```text
I_cap = I_source - (P_DC_total + P_aux) / Vdc
P_cap_esr = I_cap^2 * ESR
```

This is an averaged estimate based on bus power. It is not capacitor ripple RMS current and does not replace a switching-ripple model. The exact DC-link state formulation must still distinguish capacitor internal voltage/energy from terminal voltage when a series ESR is modeled explicitly.

The CSV reportedly contains blank/not-evaluated fields for:
- `I_bridge_rms_1/2_A`
- `I_cap_ripple_rms_A`

Blank means NOT_EVALUATED, never zero. The reported `I_DC_*` values are average power-equivalent currents (`P_DC/Vdc`), not switch peak, RMS, or capacitor ripple currents.

## 4. Integration guard

A numerical failure was reported for explicit Euler with step = 1 ms and source time constant R_source*C_DC = 100 us: the computed DC-link energy could become negative while integration continued.

The reported change stops integration when the energy state becomes invalid and marks the energy-balance gate NOT_EVALUATED rather than PASS for an incomplete integration. A timestep-convergence check and a more explicit capacitor/ESR state model remain recommended before treating the DC-link transient as numerically closed.

For the linear first-order source RC subsystem, explicit Euler's stability condition is 0 < step < 2*R_source*C_DC; stability alone does not guarantee accuracy.

## 5. R_dq0 resistance matrix

For an orthonormal frame transform T and positive phase resistances:

```text
R_dq0 = T * diag(R_U, R_V, R_W) * T^T
```

The matrix is symmetric positive definite and has eigenvalues {R_U, R_V, R_W}; its trace is R_U + R_V + R_W. The project report says tests were added for positive definiteness (Sylvester criterion), symmetry, and trace invariance over 40 randomized resistance/angle cases.

## 6. Reported software verification

The project owner reported:

- 71 pytest tests passed across DOE_1, DOE_2, and DOE_2C.
- Three selftests completed with exit code 0.
- DOE_2C run over 396 synthetic rows: `rows=396`, `computed=396`, `status=PASS_2C`, exit code 0.
- Reported passing gates: bridge power identity, bridge common-mode identity, bridge 1/2 power limits, DC current limit, DC energy balance, DC overvoltage, source power accounting, and source reverse power.
- Inverter loss model: NOT_EVALUATED.

These are reported software/synthetic validation results, not independent reproduction and not physical validation of BM1109.

## 7. Synthetic-only limitations and open work

The reported run used synthetic bus parameters: 48 V, 470 uF, ESR 0.01 ohm, source resistance 0.1 ohm, 20 kHz and 20 us step. These are not BM1109 hardware parameters.

Not yet evaluated or physically established:

- inverter switching/conduction losses;
- bridge RMS and peak currents;
- capacitor ripple RMS current and ripple spectrum;
- capacitor ESL and detailed ESR frequency/temperature dependence;
- validated limiter/source dynamics and its physical dissipation;
- real phase resistances versus temperature;
- actual DC-link voltage, current, thermal and protection limits;
- optimization of common-mode duty offsets c_k;
- physical motor acceleration trajectory.

The 396 independent operating points and their integration are a code/model exercise, not a continuous motor acceleration trajectory.

## 8. Follow-up gates before DOE_2D

Recommended next closure items:

1. `capacitor_energy_esr_identity`: use an explicit capacitor internal voltage/state and verify energy flow plus ESR dissipation without double counting.
2. `source_power_reverse_direction`: verify sign conventions and source behavior for reverse power/regeneration.
3. `dc_link_step_convergence`: compare results at step and step/2; do not infer accuracy from nonnegative energy alone.
4. Clarify whether the current-limit term is curtailed source power or dissipated clamp power.
5. Preserve RMS/peak/ripple quantities as NOT_EVALUATED until a switching/ripple model computes them.

DOE_2D should remain deferred until these DOE_2C model-consistency items are resolved.

## 9. Project configuration / passport

No BM1109 configuration passport or G9 status was changed by this record. The reported G9 discrepancy (six accessible winding ends documented in OEW_BRANCH.md versus “assumption” wording in the passport) remains a separate documentation decision and must not be silently changed here.

---
Record date: 2026-11-24
