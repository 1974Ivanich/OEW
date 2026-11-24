# Periodic BM1109 inductance-table infrastructure

## Status
Implemented as a data/interpolation utility only:
- `tools/bm1109_inductance.py`
- `tests/test_bm1109_inductance.py`

It does not yet feed the coupled-star current solver. It does not calculate
electromagnetic torque or claim any BM1109 inductance values.

## Input
A periodic sequence of angle samples in degrees in [0, 360), each paired with
a symmetric positive-definite 3x3 matrix in henries. A duplicated 360-degree
row is rejected; the 360-degree endpoint is represented by periodic wrap to
the zero-degree sample.

## Interpolation
Each matrix component uses periodic cubic Hermite interpolation. Tangents are
estimated from adjacent periodic samples. This provides a C1-periodic matrix
curve, including continuity of the first derivative across the 0/360 boundary.
The derivative is exposed both per degree and per radian.

The constructor checks symmetry and positive definiteness at source samples
and at 32 interior points in each segment. This is a finite numerical screen,
not a formal proof that every point of every cubic segment is positive definite.

## Validation
Tests cover constant-matrix invariance, zero derivative, periodic value and
derivative at the wrap boundary, and invalid source matrices/angle grids.
Run:
```
python -m pytest -q tests/test_bm1109_inductance.py
```
Do not claim tests passed unless an execution result is available.

## Limits
No current solver coupling, no angle-dependent PM flux linkage, no co-energy
torque, no energy audit for variable L, no measured BM1109 data, and no OEW,
delta, rectifier or DC-link topology.
