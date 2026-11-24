# BM1109 OEW-6T — DOE_2C revision report — 2026-11-24

> Update reported by the project owner after the earlier DOE_2C progress record. This document records the reported implementation and test results; it does not claim that this session independently ran the local tests or inspected the local source tree.

## Scope of the reported revision

The project owner reports a full rewrite of `tools/bm1109_doe2c.py`, corresponding test updates in `tests/test_bm1109_doe2c.py`, and a G9 update in `docs/BM1109_CONFIG_PASSPORT.md`. The local source files were not available through the connected GitHub repository at the time this report was prepared, so this commit records the report only; it does not contain those source-file changes.

## 1. Phase-current sum naming

The former `I_common_mode_A` field is renamed `I_phase_sum_A`. A separate column `I_zero_sequence_A = I_phase_sum_A/sqrt(3)` is added. The duty-weighted current `I_c_A = sum(c_k*i_k)` remains distinct.

`P_common_mode_W` is explicitly defined as `Vdc*I_c`. The identity `P1-P2 = Vdc*I_phase_sum` applies only when every `c_k=0.5`; the general identity is `P1-P2=2*Vdc*I_c`.

Reported synthetic run:
- `I_phase_sum_A`: extrema approximately ±8.660254 A.
- `I_zero_sequence_A`: extrema ±5.0 A.
- `I_c_A`: extrema approximately ±4.330127 A.
- `P_common_mode_W`: extrema approximately ±207.846 W.
- `P1-P2`: extrema approximately ±415.692 W.
- Worst reported relative identity residual: `3.55e-14`.

## 2. Source curtailment semantics

`P_clamp` is renamed `P_source_curtailed` and means unavailable/curtailed source power:
```text
P_source_curtailed = I_s*(V_src - V_DC - I_s*R_s)
P_terminal = P_bus + I_s^2*R_s + P_source_curtailed
```

This term is not asserted to be heat dissipation. A dissipative clamp would need a separate physical model and separate quantity such as `P_clamp_dissipated`.

A dedicated `source_power_reverse_direction` gate checks:
- `sign(P_terminal) = sign(I_s)`;
- `sign(P_bus) = sign(I_s)`;
- `P_series >= 0).

The 396-row synthetic run reportedly had `absorbing_states=0`, so reverse flow did not occur in that dataset. A unit test `test_reverse_direction_signs_are_consistent` reportedly exercises an absorbing case at `Vdc=50 V`, `Vsrc=48 V`, with `P_src<0`, `P_series>0` and gate PASS.

## 3. Explicit capacitor internal voltage and ESR

The reported model uses an ideal capacitance in series with ESR:
```text
V_dc_terminal = V_cap_internal + ESR*I_cap
E_cap = 0.5*C*V_cap_internal^2
dE_cap/dt = V_cap_internal*I_cap
           = V_dc_terminal*I_cap - I_cap^2*ESR
```

The old shortcut `0.5*C*V_dc_terminal^2` is no longer used as capacitor stored energy.

Reported output columns:
- `V_dc_terminal_V`
- `V_cap_internal_V`
- `I_cap_A`
- `P_cap_esr_W`
- `P_cap_power_W = V_cap_internal*I_cap`
- `E_cap_J`

The `capacitor_energy_esr_identity` gate checks the instantaneous identity. Reported worst relative residual: `2.2e-16`.

The `integration_energy_residual` gate compares `delta(E_cap)` against the Simpson-integrated terminal input minus ESR dissipation. Reported residual: `9.1e-6`, threshold `1e-3`. This is a numerical quadrature/energy-identity consistency check, not an independent validation of the physical source/load model.

`I_cap_ripple_rms_A` remains unavailable. Average capacitor current must not be represented as ripple RMS current.

## 4. Integration method and step-related gates

For a frozen load, the linear RC part is reportedly solved with the exact exponential:
```text
dv_cap/dt = (V_ss - v_cap)/((R_s+ESR)*C)
```

Reported gates:
- `integration_stability`: checks `dt/((R_s+ESR)*C) <= 1`; reported ratio 0.387, PASS.
- `integration_step_accuracy`: one frozen-load step versus eight substeps; reported difference `2.96e-16`. This verifies the exact frozen-load exponential update, not convergence of a fully coupled time-varying bridge/load trajectory.
- `integration_energy_residual`: Simpson-integrated energy identity as above.
- `integration_stopped_at_zero_energy`: a stop at `E_cap<=0` yields NOT_EVALUATED, not PASS.

The source-current-clipped branch remains explicit and is documented as constant-current behavior.

**Important interpretation:** The reported one-step/eight-substep agreement is an algebraic check for the frozen-load linear subproblem. It is not a substitute for timestep refinement of the complete coupled trajectory with time-varying bridge power. A follow-up should compare (for example) 20, 10, 5 and 2.5 microsecond steps on the same trajectory and compare peak Vdc, capacitor current, stored energy and integrated residual.

## 5. Resistance matrix

The reported DOE_2A test continues to check positive definiteness by Sylvester's criterion and trace invariance:
```text
R_dq0 = T*R_UVW*T^T
tr(R_dq0) = R_U + R_V + R_W
```
For orthogonal T, the eigenvalues are the three phase resistances. The test is reported as `test_r_dq0_is_positive_definite_for_positive_phase_resistance`.

## 6. G9 passport statement

The project owner reports that `docs/BM1109_CONFIG_PASSPORT.md` was updated so the available winding ends are recorded as:

```text
6: U1/U2, V1/V2, W1/W2; no common neutral; three independent phase branches
```

with status “confirmed (G9)”. Geometry and magnetic parameters remain a separate open item B1.2. The owner reports that passport sections §1/§2 were not rewritten.

This topology statement is consistent with the BM1109 OEW-6T design contract: three independent phase branches, six physical winding terminals and no shared phase neutral. It does not establish geometry, magnetic parameters or measured electrical characteristics.

## 7. Reported test/run results

Project-owner report:
- `py -m pytest tests/test_bm1109_doe1.py tests/test_bm1109_doe2.py tests/test_bm1109_doe2c.py`: 75 passed.
- Selftest for all three tools: exit 0.
- `bm1109_doe2c.py run --voltages out/doe2_lim/doe2_voltage.csv --params out/bus_params_synthetic.json`: 396/396 rows computed, `PASS_2C`, exit 0, `sequence_steps=396`.
- 17 gates reportedly PASS except `inverter_loss_model=NOT_EVALUATED`.

These results are reported, not independently reproduced in this documentation commit.

## 8. Remaining limitations

Reported synthetic bus parameters: 48 V, 470 microfarad, ESR 0.01 ohm, source resistance 0.1 ohm, timestep 20 microseconds. They are not physical BM1109 parameters.

Still not evaluated:
- bridge RMS and peak currents;
- capacitor ripple RMS current;
- inverter switching/conduction losses;
- ESL;
- dissipative clamp model;
- optimization of `c_k`.

The reported `I_DC_*` values are average power-equivalent currents. `PASS_2C` means internal mathematical/accounting consistency for the tested synthetic model, not hardware suitability, capacitor lifetime, thermal safety or acceleration-trajectory validation. DOE_2D has not started.

## 9. Source changes and repository status

The owner identified these local files as modified:
- `tools/bm1109_doe2c.py`
- `tests/test_bm1109_doe2c.py`
- `docs/BM1109_CONFIG_PASSPORT.md`

At report preparation time, these three paths were not retrievable from the connected `main` branch via GitHub Contents API (404). Therefore this commit intentionally does **not** claim to commit or push those local changes. To commit the actual source and passport changes, their contents or a patch must be made available to the repository workflow.

Related prior record: [BM1109 DOE_2C progress 2026-11-24](BM1109_DOE2C_PROGRESS_2026-11-24.md).

---
Date: 2026-11-24
Repository: 1974Ivanich/OEW
Branch: main
