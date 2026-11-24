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

The solver exposes `bemf_angle_offset_deg` and
`inductance_angle_offset_deg`. Each is added to the integration rotor angle
before querying its respective periodic table. These are coordinate-reference
corrections, not estimates: they must come from the measurement/indexing
procedure. A common shift of both tables must leave periodic mean quantities
unchanged; a relative shift changes the physical phase relation and can change
the torque. The model cannot infer the correct relative offset from BEMF and
inductance tables without a shared rotor-angle reference.

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

Important naming distinction:
- `average_em_power_w` is the PM open-circuit-BEMF power
  `mean(e_oc^T j)`, not the total mechanical braking power.
- `average_reluctance_conversion_w` is signed and may be positive or negative.
- The co-energy torque gives the independent mechanical relation
  `P_br = -mean(T_e)*omega_m = P_PM - P_rel`.
- On a periodic solution, this must equal `P_cu + P_load`.

A dedicated synthetic salient-rotor test constructs both PM BEMF and
`L_abc(theta)` from the same analytical rotor reference, then checks both
`P_br = P_PM - P_rel` and `P_br = P_cu + P_load`. This avoids the invalid
test arrangement in which unrelated BEMF and inductance angle references
could accidentally be combined.

## Validation tests

Synthetic tests check:
- constant isotropic inductance: zero reluctance conversion and closed energy
  balance;
- salient inductance: nonzero reluctance conversion and energy closure;
- consistent salient rotor: PM BEMF, variable inductance, co-energy torque and
  total mechanical/electrical power balance agree;
- zero speed: zero currents, power and torque;
- common angle-reference shift: invariant periodic average torque and power;
- time-step refinement: 512/1024/2048 steps per electrical period, with
  decreasing torque difference and a final relative difference below 0.2%.

These are numerical model tests, not BM1109 measurements. Run:
```
python -m pytest -q tests/test_bm1109_variable_l_star.py
```

## Known numerical limitations

The energy residual uses sampled powers over the final simulated period and
an exact magnetic-energy endpoint difference. It is still subject to
time-integration and quadrature error. Current-dependent inductance,
hysteresis, core loss and other parasitics require additional states/terms.
