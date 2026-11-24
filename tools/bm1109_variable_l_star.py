"""Offline variable-inductance abc solver for a three-wire floating-neutral star.

Generator-positive phase currents flow out of the machine. Open-circuit phase
BEMF is the measured terminal voltage at zero current. The model is not OEW,
not a rectifier, and not a hardware-control routine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from tools.bm1109_braking import BemfTable
from tools.bm1109_inductance import PeriodicInductanceTable
from tools.bm1109_coenergy import coenergy_torque_nm

_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class VariableLResult:
    rpm: float
    average_braking_torque_nm: float
    average_em_power_w: float
    average_reluctance_conversion_w: float
    copper_loss_w: float
    load_loss_w: float
    phase_rms_current_a: tuple[float, float, float]
    phase_peak_current_a: tuple[float, float, float]
    current_sum_peak_a: float
    periodic_error_a: float
    periods_simulated: int
    converged: bool
    energy_balance_error_w: float
    energy_balance_error_pct: float
    torque_from_coenergy_nm: float


def _solve(a: Sequence[Sequence[float]], b: Sequence[float]) -> tuple[float, ...]:
    n = len(b)
    m = [list(map(float, a[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(m[row][col]))
        if abs(m[pivot][col]) < 1e-20:
            raise ValueError("singular constrained inductance system")
        m[col], m[pivot] = m[pivot], m[col]
        scale = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = m[row][col]
            for j in range(col, n + 1):
                m[row][j] -= factor * m[col][j]
    return tuple(m[i][n] for i in range(n))


def _matvec(a, x):
    return tuple(math.fsum(a[i][j] * x[j] for j in range(3)) for i in range(3))


def _dot(a, b):
    return math.fsum(a[i] * b[i] for i in range(3))


def _derivative(current, emf, resistance, inductance, dldtheta, omega_e):
    # Generator-positive convention:
    # L di/dt + omega_e*(dL/dtheta)i + R i + vn*1 = e_oc.
    speed_term = _matvec(dldtheta, current)
    rhs = tuple(emf[k] - resistance[k] * current[k]
                - omega_e * speed_term[k] for k in range(3))
    kkt = [list(inductance[k]) + [1.0] for k in range(3)]
    kkt.append([1.0, 1.0, 1.0, 0.0])
    result = _solve(kkt, rhs + (0.0,))
    return result[:3]


def _rk4(current, t, dt, period, rpm, omega_e, bemf, ltable, resistance,
         bemf_angle_offset_deg, inductance_angle_offset_deg):
    def f(time_s, state):
        rotor_angle = 360.0 * (time_s % period) / period
        bemf_angle = rotor_angle + bemf_angle_offset_deg
        inductance_angle = rotor_angle + inductance_angle_offset_deg
        return _derivative(
            state, bemf.at_angle(bemf_angle, rpm), resistance,
            ltable.evaluate(inductance_angle),
            ltable.derivative_per_radian(inductance_angle), omega_e
        )
    k1 = f(t, current)
    k2 = f(t + dt/2, tuple(current[i] + dt*k1[i]/2 for i in range(3)))
    k3 = f(t + dt/2, tuple(current[i] + dt*k2[i]/2 for i in range(3)))
    k4 = f(t + dt, tuple(current[i] + dt*k3[i] for i in range(3)))
    return tuple(current[i] + dt*(k1[i]+2*k2[i]+2*k3[i]+k4[i])/6
                 for i in range(3))


def simulate_variable_l_star(
    bemf: BemfTable,
    inductance: PeriodicInductanceTable,
    rpm: float,
    winding_resistance_ohm: Sequence[float],
    load_resistance_ohm: Sequence[float],
    *,
    steps_per_period: int | None = None,
    max_periods: int = 120,
    periodic_tolerance_a: float = 1e-7,
    bemf_angle_offset_deg: float = 0.0,
    inductance_angle_offset_deg: float = 0.0,
) -> VariableLResult:
    """Solve variable-L three-wire star; currents are positive out of machine.

    BEMF is scaled linearly with speed by BemfTable. L(theta) is independent
    of speed and current. This excludes saturation and hysteresis.
    """
    if not math.isfinite(rpm) or rpm < 0:
        raise ValueError("rpm must be finite and non-negative")
    if not math.isfinite(bemf_angle_offset_deg) or not math.isfinite(inductance_angle_offset_deg):
        raise ValueError("electrical angle offsets must be finite")
    if len(winding_resistance_ohm) != 3 or len(load_resistance_ohm) != 3:
        raise ValueError("exactly three phase resistances are required")
    values = tuple(winding_resistance_ohm) + tuple(load_resistance_ohm)
    if any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("resistances must be finite and non-negative")
    if any(winding_resistance_ohm[i] + load_resistance_ohm[i] <= 0
           for i in range(3)):
        raise ValueError("each phase needs positive total series resistance")
    if (not isinstance(max_periods, int) or isinstance(max_periods, bool)
            or max_periods < 2 or not math.isfinite(periodic_tolerance_a)
            or periodic_tolerance_a <= 0):
        raise ValueError("invalid periodic convergence settings")
    if rpm == 0:
        return VariableLResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            (0.0,)*3, (0.0,)*3, 0.0, 0.0, 0, True, 0.0, 0.0, 0.0)

    omega_m = _TWO_PI * rpm / 60.0
    omega_e = bemf.pole_pairs * omega_m
    period = _TWO_PI / omega_e
    n = steps_per_period or max(1024, bemf.samples_per_period * 16)
    if not isinstance(n, int) or isinstance(n, bool) or n < bemf.samples_per_period:
        raise ValueError("steps_per_period must be an integer >= BEMF sample count")
    dt = period / n
    resistance = tuple(winding_resistance_ohm[i] + load_resistance_ohm[i]
                       for i in range(3))
    current = (0.0, 0.0, 0.0)
    final_samples = []
    error = math.inf
    converged = False
    used = 0
    for period_index in range(max_periods):
        start = current
        samples = []
        for k in range(n):
            samples.append(current)
            current = _rk4(current, k*dt, dt, period, rpm, omega_e,
                           bemf, inductance, resistance,
                           bemf_angle_offset_deg, inductance_angle_offset_deg)
        error = max(abs(current[i]-start[i]) for i in range(3))
        final_samples = samples
        used = period_index + 1
        if error <= periodic_tolerance_a:
            converged = True
            break

    pem = pcu = pload = prel = torque_sum = 0.0
    sum_i2 = [0.0]*3
    peak_i = [0.0]*3
    current_sum_peak = 0.0
    for k, state in enumerate(final_samples):
        angle = 360.0*k/n
        emf = bemf.at_angle(angle + bemf_angle_offset_deg, rpm)
        dl = inductance.derivative_per_radian(angle + inductance_angle_offset_deg)
        dpsi = tuple(x/omega_e for x in emf)
        # Convert generator-positive current to motor-positive for co-energy.
        torque = coenergy_torque_nm(tuple(-x for x in state), dl, dpsi,
                                    bemf.pole_pairs)
        torque_sum += torque.total_nm
        pem += _dot(emf, state)
        pcu += math.fsum(winding_resistance_ohm[i]*state[i]**2 for i in range(3))
        pload += math.fsum(load_resistance_ohm[i]*state[i]**2 for i in range(3))
        prel += 0.5*omega_e*_dot(state, _matvec(dl, state))
        current_sum_peak = max(current_sum_peak, abs(math.fsum(state)))
        for i in range(3):
            sum_i2[i] += state[i]**2
            peak_i[i] = max(peak_i[i], abs(state[i]))

    factor = dt/period
    pem *= factor
    pcu *= factor
    pload *= factor
    prel *= factor
    torque_avg = torque_sum/n
    rms = tuple(math.sqrt(x/n) for x in sum_i2)
    # Over a converged periodic orbit, average d(0.5*i^T L i)/dt is zero.
    # The finite-period endpoint mismatch is reported via periodic_error_a.
    energy_error = pem - pcu - pload - prel
    pct = 100.0*energy_error/pem if abs(pem) > 1e-15 else 0.0
    braking_torque = -torque_avg
    return VariableLResult(rpm, braking_torque, pem, prel, pcu, pload,
        rms, tuple(peak_i), current_sum_peak, error, used, converged,
        energy_error, pct, torque_avg)
