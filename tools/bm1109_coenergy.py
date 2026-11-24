"""Co-energy torque from phase-domain flux-linkage derivatives.

Current convention: positive phase current enters the machine (motor convention).
Angles: derivatives are with respect to electrical rotor angle in radians.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CoenergyTorque:
    pm_nm: float
    reluctance_nm: float

    @property
    def total_nm(self) -> float:
        return self.pm_nm + self.reluctance_nm


def coenergy_torque_nm(
    phase_currents_a: Sequence[float],
    d_inductance_dtheta_h_per_rad: Sequence[Sequence[float]],
    d_pm_flux_dtheta_wb_per_rad: Sequence[float],
    pole_pairs: int,
) -> CoenergyTorque:
    """Return PM, reluctance and total torque in motor-positive convention.

    T_e = p * [i^T d(psi_PM)/d(theta_e)
              + 0.5 * i^T d(L)/d(theta_e) * i].

    The current vector must use positive current entering the machine.
    For generator-positive currents flowing out of the machine, negate the
    current vector before calling this function. The PM term then changes
    sign and the quadratic reluctance term remains unchanged.
    """
    if not isinstance(pole_pairs, int) or isinstance(pole_pairs, bool) or pole_pairs <= 0:
        raise ValueError("pole_pairs must be a positive integer")
    if len(phase_currents_a) != 3 or len(d_pm_flux_dtheta_wb_per_rad) != 3:
        raise ValueError("phase current and PM-flux derivative vectors must have length 3")
    if len(d_inductance_dtheta_h_per_rad) != 3 or any(
        len(row) != 3 for row in d_inductance_dtheta_h_per_rad
    ):
        raise ValueError("inductance derivative must be 3x3")

    i = tuple(float(x) for x in phase_currents_a)
    dpsi = tuple(float(x) for x in d_pm_flux_dtheta_wb_per_rad)
    dl = tuple(tuple(float(x) for x in row) for row in d_inductance_dtheta_h_per_rad)
    if not all(math.isfinite(x) for x in i + dpsi) or not all(
        math.isfinite(x) for row in dl for x in row
    ):
        raise ValueError("all inputs must be finite")
    scale = max(1.0, *(abs(x) for row in dl for x in row))
    tol = 1e-10 * scale
    if any(abs(dl[r][c] - dl[c][r]) > tol for r in range(3) for c in range(3)):
        raise ValueError("inductance derivative must be symmetric")

    pm = pole_pairs * math.fsum(i[k] * dpsi[k] for k in range(3))
    reluctance = 0.5 * pole_pairs * math.fsum(
        i[r] * dl[r][c] * i[c] for r in range(3) for c in range(3)
    )
    return CoenergyTorque(pm_nm=pm, reluctance_nm=reluctance)
