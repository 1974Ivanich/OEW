# BM1109 variable-L abc passive-braking solver

## Status and scope

Implemented as an offline numerical model:
- `tools/bm1109_variable_l_star.py`
- `tests/test_bm1109_variable_l_star.py`

The model is a three-wire star with floating neutral, per-phase series winding
and load resistance, measured open-circuit phase BEMF, and periodic
angle-dependent symmetric positive-definite `L_abc(theta)`. It is not an OEW
six-terminal model, diode bridge, rectifier, DC-link, saturation, or hardware
control model.

## Sign convention and equation

Phase currents `j_abc` are positive out of the machine (generator-positive).
Open-circuit BEMF `e_oc` is the measured phase terminal voltage at zero current.
The phase equation is

```
L(theta) * dj/dt + omega_e * dL/dtheta * j
 + R_total * j + vn * 1 = e_oc
1^T * dj/dt = 0
```

At each RK4 stage, a 4x4 KKT system enforces the floating-neutral constraint.
The term `omega_e * dL/dtheta * j` is mandatory for variable inductance.

For this polarity, `e_oc = omega_e * d(psi_PM)/d(theta_e)`.
The co-energy helper uses motor-positive current, therefore it receives
`i_motor=-j_generator`:
```
T_e = p * [-j^T d(psi_PM)/dtheta_e
           + 0.5 * j^T dL/dtheta_e * j]
```
Positive braking torque for positive rotation is `-average(T_e)`.

## Energy audit

Multiplying the phase equation by `j^T` gives
```
e_oc^T j = P_cu + P_load + d(0.5*j^T*L*j)/dt
           + 0.5*omega_e*j^T*(dL/dtheta_e)*j
```
The final term is the reluctance mechanical-conversion power. Over a
converged periodic orbit, the mean stored-energy derivative is zero. The
reported residual is
```
P_em - P_cu - P_load - P_reluctance_conversion
```
and should converge toward zero with time-step refinement and periodic
convergence. The result also reports peak current-sum error and periodic
endpoint mismatch.

## Validation tests

Synthetic tests check:
- constant isotropic inductance: zero reluctance conversion and closed energy
  balance;
- salient inductance: nonzero reluctance conversion and energy closure;
- zero speed: zero currents, power and torque.

These are numerical model tests, not BM1109 measurements. Run:
```
python -m pytest -q tests/test_bm1109_variable_l_star.py
```

## Known numerical limitation

The energy residual is sampled over the final simulated period. The stored
magnetic energy endpoint difference is not explicitly added to the reported
residual; for a converged periodic orbit it should be negligible. Before using
non-periodic transient records, add the exact endpoint term
`(Wmag_end-Wmag_start)/period` to the audit. Current-dependent inductance,
hysteresis, core loss and other parasitics require additional states/terms.
