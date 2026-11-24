# BM1109 phase-domain co-energy torque helper

## Status

Implemented as a pure offline calculation helper:
- `tools/bm1109_coenergy.py`
- `tests/test_bm1109_coenergy.py`

No current integration, OEW topology, power-stage model, or BM1109 parameter
identification is included.

## Contract

The helper evaluates, in motor-positive convention,

```
Te = p * [i_abc^T * d(psi_PM,abc)/d(theta_e)
          + 0.5 * i_abc^T * d(L_abc)/d(theta_e) * i_abc]
```

Inputs:
- phase currents positive into the machine, A;
- symmetric `dL_abc/dtheta_e`, H/rad;
- `dpsi_PM,abc/dtheta_e`, Wb/rad;
- positive integer pole-pair count.

For generator-positive currents flowing out of the machine, negate all three
currents before calling the helper. This flips the PM contribution and leaves
the quadratic reluctance contribution unchanged. Rotor-angle direction and
PM-flux polarity must still match the input data contract.

## Analytical salient-PMSM test

The synthetic reference uses:
- Ld = 4 mH, Lq = 7 mH, L0 = 5 mH;
- PM flux linkage = 0.05 Wb;
- p = 4;
- id = 2 A, iq = 3 A;
- electrical angle = 37 degrees.

Expected:
- PM torque = +0.900 N m;
- reluctance torque = -0.108 N m;
- total motor-convention torque = +0.792 N m.

These are synthetic test values, not BM1109 measurements. The test evaluates
the phase-domain derivatives and checks agreement with the independently
expressed dq formula. It also verifies the generator-current sign rule and
rejects malformed inputs.

Run:
```
python -m pytest -q tests/test_bm1109_coenergy.py
```

## Limits

The helper is not yet connected to the variable-inductance current solver.
It assumes a linear magnetic model and a known PM-flux derivative. Open-circuit
BEMF can supply the PM derivative only after its polarity and voltage
reference are reconciled with the chosen current convention. It does not infer
the reluctance torque from open-circuit BEMF alone.
