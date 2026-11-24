# BM1109 coupled abc star model

## Scope
This second solver slice models a three-wire star winding with floating neutral,
series phase loads and a constant symmetric positive-definite 3x3 phase inductance
matrix. The neutral potential is solved at every RK4 derivative stage. The original
independent phase-RL solver remains the baseline.

This is not a delta, OEW, two-inverter, rectifier or DC-link model. Position- and
current-dependent inductance, saturation and zero-sequence paths are not included.

## Equations
With generator-positive current:
```
Labc * di/dt + vn * [1,1,1]^T = eabc - Rtotal * i
[1,1,1] * di/dt = 0
```
At every derivative evaluation, solve:
```
[ Labc  1 ] [di/dt] = [eabc - Rtotal*i]
[ 1^T   0 ] [ vn  ]   [       0        ]
```
This enforces iU+iV+iW=0 during integration, rather than correcting currents
afterward. Labc is checked for symmetry and positive definiteness.

For a symmetric matrix with diagonal Ls and equal mutual term M, the balanced
zero-sum subspace sees Ls-M; the zero-sequence eigenvalue is Ls+2M.

## Tests
Run from repository root:
```
python -m pytest -q tests/test_bm1109_star_abc.py tests/test_bm1109_braking_model.py
```
Tests cover balanced phase symmetry, energy balance, agreement with the independent
phase baseline in the balanced subspace, and rejection of invalid matrices.
A committed test is not evidence that it has been executed; record actual output.

## Limits
No angle/current-dependent inductance, delta topology, neutral conductor, diode
bridge, DC-link dynamics, PWM switch, semiconductor losses or six-terminal OEW
network. No BM1109 measurement data or identified parameters are embedded.
