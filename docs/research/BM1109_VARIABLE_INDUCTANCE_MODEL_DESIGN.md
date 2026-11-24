# BM1109 variable-inductance abc model — derivation and implementation gate

## Status and scope

This document specifies the next mathematical extension after
`tools/bm1109_star_abc.py`. It is a design note, not an implemented solver and
not a source of BM1109 parameter values.

The intended topology remains a three-wire star with floating neutral. The
inductance matrix may depend on electrical rotor angle. Current dependence,
saturation, hysteresis, eddy-current loss, delta connection, OEW six-terminal
wiring, rectifier and DC-link dynamics remain out of scope for this step.

## Flux-linkage model

Use a full flux-linkage expression rather than treating inductance as a constant:

```
lambda_abc(theta, i) = L_abc(theta) * i + psi_pm_abc(theta)
```

For electrical angular velocity omega_e:

```
d(lambda)/dt = L * di/dt
             + omega_e * (dL/dtheta) * i
             + omega_e * d(psi_pm)/dtheta
```

The open-circuit measured phase BEMF constrains only the PM-flux derivative
(the exact sign depends on the declared voltage/current reference direction):

```
e_oc(theta) = -omega_e * d(psi_pm)/dtheta
```

It does not identify `L_abc(theta)` and does not include the current-dependent
reluctance-voltage term. Do not infer the variable-inductance term from open-
circuit BEMF alone.

## Floating-neutral differential-algebraic system

For the selected three-wire star, the sum of phase currents is constrained to
zero. With the chosen generator-positive reference convention, the solver must
write the phase voltage equation from a single documented sign convention and
solve the neutral potential as an algebraic unknown. In compact form the
differential part contains:

```
L(theta) * di/dt + 1 * vn
 = source_terms(theta, omega_e, i) - R * i - v_load
1^T * di/dt = 0
```

The source terms must include both the PM term derived from measured open-circuit
BEMF and the speed-induced inductance term involving
`omega_e * (dL/dtheta) * i`. The sign of these terms must be derived from the
chosen terminal-voltage/current references, not copied by analogy.

At each integration stage, solve the constrained 4x4 KKT system. The matrix
`L(theta)` must be symmetric positive definite at every evaluated angle.
For periodic data, interpolation must wrap continuously across 0/360 degrees.
A discontinuity at the wrap boundary is a model-input error.

## Torque and energy bookkeeping

The electromagnetic torque follows from magnetic co-energy. For a linear
magnetic model with PM flux linkage:

```
T_e = 0.5 * i^T * (dL/dtheta) * i
      + i^T * d(psi_pm)/dtheta
```

This expression is written in the conventional motor sign convention; when the
solver uses generator-positive currents, the reported braking-torque sign must
be transformed explicitly and tested against power flow.

The variable-inductance term is not optional: omitting it while allowing
`L(theta)` to vary violates the torque/energy relationship. Conversely,
`average_em_power / mechanical_speed` is not generally the total torque when
the source model contains only open-circuit PM BEMF and the inductance varies.

The energy audit must separately track:
- terminal/load electrical power;
- winding copper loss;
- change in stored magnetic energy;
- mechanical conversion associated with PM flux and reluctance torque;
- any explicitly modelled semiconductor or parasitic loss.

A periodic steady-state audit should close over a complete electrical period.
The audit equation and signs must be derived from the exact implemented voltage
equation and current references before code is written. Do not retain the
constant-L residual formula unchanged for variable L.

## Required input contract

1. Three phase BEMF data with angle, phase order, polarity, direction, reference
   speed, pole-pair count and measurement method.
2. `L_abc(theta)` as a periodic 3x3 matrix dataset, with units, rotor-angle
   reference, test current/bias, temperature, uncertainty and provenance.
3. Winding resistance per phase and temperature.
4. Per-phase load resistance and connection topology.
5. Declared current and voltage reference directions.
6. Optional measurement covariance/uncertainty for BEMF and inductance data.

If only `Ld` and `Lq` are available, they may be used to construct a model
under an explicitly stated sinusoidal, linear, rotor-coordinate assumption.
They do not uniquely specify an arbitrary measured phase-domain matrix,
zero-sequence inductance, slotting harmonics or saturation.

## Numerical and physical validation gates

Before accepting a variable-L solver:

- symmetry and positive definiteness of `L(theta)` over a dense angle grid;
- periodic continuity of `L` and its derivative at the wrap boundary;
- enforced `iU+iV+iW=0` at every solver stage;
- constant-L limit reproduces the current coupled-star solver;
- sinusoidal rotor model reproduces an independently derived dq reference;
- zero-current/open-circuit case has zero load current;
- phase permutation produces the corresponding permuted solution;
- energy residual converges toward zero as time-step is reduced;
- torque from co-energy agrees with mechanical/electrical power balance;
- periodic solution convergence is checked independently from time-step
  convergence.

A model that passes only the constant-L limit is not validated for saliency.

## Implementation sequence

1. Add a pure, independently tested periodic `L_abc(theta)` data container and
   derivative/interpolation routine.
2. Derive and document signs for one simple analytical salient-pole case.
3. Implement the constrained variable-L derivative only after the sign and
   energy equations are independently checked.
4. Add co-energy torque and a period-integrated energy audit.
5. Validate against constant-L and dq analytical controls.
6. Only then consider measured angle-dependent inductance data.

## Explicit exclusions

This is an offline analysis model. It must not generate PWM/gate commands,
current limits, hardware connection instructions or test authorization. No
motor parameter is to be invented to make a numerical example appear realistic.
