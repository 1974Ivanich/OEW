# BM1109 offline braking model — first implementation

## Scope

The first executable slice is `tools/bm1109_braking.py`. It implements a time-domain
three-phase **independent phase-RL** model driven by one measured electrical period
of phase BEMF. It supports periodic linear interpolation of the measured waveform,
speed scaling for a linear magnetic model, RK4 integration to periodic steady state,
and calculation of average electromagnetic braking torque, phase RMS/peak currents,
copper loss and external resistor loss.

This is not yet the diode-bridge model described in the research specification.
It is also not an OEW model and does not represent the two inverter bridges, their
DC-link paths, mutual winding coupling, zero-sequence paths, saturation or magnetic
position dependence of inductance. Do not interpret its outputs as hardware limits
or permission to energize the motor.

## Input CSV

Use one electrical period, uniformly sampled from 0 degrees inclusive to 360 degrees
exclusive. The required header is:

```csv
angle_deg,e_u_v,e_v_v,e_w_v
```

The three voltage columns are phase-to-neutral BEMF in volts, with consistent phase
order and polarity. The table must have a uniform angle grid starting at 0 degrees.
Do not append a duplicate 360-degree row. Supply the mechanical speed at which the
BEMF was measured and the pole-pair count separately when constructing `BemfTable`.

Example import:

```python
from tools.bm1109_braking import load_bemf_csv, simulate_phase_rl

bemf = load_bemf_csv(
    "measured_bemf.csv",
    reference_rpm=MEASURED_RPM,
    pole_pairs=POLE_PAIRS,
)

result = simulate_phase_rl(
    bemf,
    rpm=TARGET_RPM,
    winding_resistance_ohm=(R_U, R_V, R_W),
    load_resistance_ohm=(R_LOAD_U, R_LOAD_V, R_LOAD_W),
    phase_inductance_h=L_PHASE,
)

if not result.converged:
    raise RuntimeError("periodic steady state was not reached")

print(result.average_braking_torque_nm)
print(result.phase_rms_current_a, result.phase_peak_current_a)
print(result.average_em_power_w, result.copper_loss_w, result.load_loss_w)
```

All parameter names use SI units. Winding resistance is per phase and must match
the same winding connection used for the BEMF measurement. Load resistance is an
external series resistor in each phase branch. The model assumes one scalar phase
inductance and no mutual coupling; use it only when that approximation is justified.

## Analytical control

`sinusoidal_analytical_torque(...)` computes the balanced-sinusoidal fundamental
reference for the (L_d=L_q) case. The unit test compares the time-domain result
against this reference. A mismatch should be resolved before extending the solver
to measured harmonic BEMF waveforms.

## Tests

Run the focused tests from the repository root:

```bash
python -m pytest -q tests/test_bm1109_braking_model.py
```

The implementation has not yet been exercised against BM1109 measurement files:
no measured BEMF/R/L dataset is embedded or inferred here. The diode-bridge MNA
solver, DC-link dynamics, uncertainty propagation, CSV result export and OEW
network model remain subsequent tasks.
