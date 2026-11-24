"""Offline passive-braking model for a three-phase PMSM/BLDC phase-RL load.

This is deliberately NOT an OEW or diode-bridge model. Each phase is represented
by an independent series R-L branch driven by a measured phase BEMF waveform.
All voltages are phase quantities; current is positive in the generator direction.
Only the Python standard library is required.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_TWO_PI = 2.0 * math.pi
_PHASES = ("u", "v", "w")


@dataclass(frozen=True)
class BemfTable:
    """One electrical period of phase BEMF, sampled uniformly from 0 degrees."""

    angle_deg: tuple[float, ...]
    phase_v: tuple[tuple[float, float, float], ...]
    reference_rpm: float
    pole_pairs: int

    def __post_init__(self) -> None:
        if len(self.angle_deg) < 3 or len(self.angle_deg) != len(self.phase_v):
            raise ValueError("BEMF table needs at least 3 matching angle/voltage rows")
        if self.reference_rpm <= 0 or self.pole_pairs <= 0:
            raise ValueError("reference_rpm and pole_pairs must be positive")
        if abs(self.angle_deg[0]) > 1e-9:
            raise ValueError("angle table must start at 0 electrical degrees")
        step = 360.0 / len(self.angle_deg)
        for i, angle in enumerate(self.angle_deg):
            if not math.isfinite(angle) or abs(angle - i * step) > 1e-6:
                raise ValueError("angles must be uniformly spaced over [0, 360)")
            if len(self.phase_v[i]) != 3 or not all(math.isfinite(x) for x in self.phase_v[i]):
                raise ValueError("each BEMF row must contain three finite phase voltages")

    @property
    def samples_per_period(self) -> int:
        return len(self.angle_deg)

    def at_angle(self, angle_deg: float, rpm: float) -> tuple[float, float, float]:
        """Periodic linear interpolation, scaled by speed for a linear magnetic model."""
        if rpm < 0 or not math.isfinite(rpm):
            raise ValueError("rpm must be finite and non-negative")
        if rpm == 0:
            return (0.0, 0.0, 0.0)
        x = (angle_deg % 360.0) * self.samples_per_period / 360.0
        i0 = int(math.floor(x)) % self.samples_per_period
        i1 = (i0 + 1) % self.samples_per_period
        frac = x - math.floor(x)
        scale = rpm / self.reference_rpm
        return tuple(
            (self.phase_v[i0][p] + frac * (self.phase_v[i1][p] - self.phase_v[i0][p])) * scale
            for p in range(3)
        )


@dataclass(frozen=True)
class BrakingResult:
    rpm: float
    average_braking_torque_nm: float
    average_em_power_w: float
    phase_rms_current_a: tuple[float, float, float]
    phase_peak_current_a: tuple[float, float, float]
    copper_loss_w: float
    load_loss_w: float
    periodic_error_a: float
    periods_simulated: int
    converged: bool


def load_bemf_csv(path: str | Path, reference_rpm: float, pole_pairs: int) -> BemfTable:
    """Read CSV columns: angle_deg,e_u_v,e_v_v,e_w_v (one period, no 360 row)."""
    angles: list[float] = []
    rows: list[tuple[float, float, float]] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"angle_deg", "e_u_v", "e_v_v", "e_w_v"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV must contain angle_deg,e_u_v,e_v_v,e_w_v columns")
        for row in reader:
            try:
                angles.append(float(row["angle_deg"]))
                rows.append((float(row["e_u_v"]), float(row["e_v_v"]), float(row["e_w_v"])))
            except (TypeError, ValueError) as exc:
                raise ValueError("CSV contains a missing or non-numeric value") from exc
    return BemfTable(tuple(angles), tuple(rows), reference_rpm, pole_pairs)


def _rk4_step(current: float, t: float, dt: float, rpm: float,
              table: BemfTable, phase: int, r_total: float, inductance_h: float) -> float:
    omega_e = table.pole_pairs * _TWO_PI * rpm / 60.0
    period = _TWO_PI / omega_e
    def deriv(time_s: float, current_a: float) -> float:
        angle = 360.0 * (time_s % period) / period
        emf = table.at_angle(angle, rpm)[phase]
        return (emf - r_total * current_a) / inductance_h
    k1 = deriv(t, current)
    k2 = deriv(t + dt / 2, current + dt * k1 / 2)
    k3 = deriv(t + dt / 2, current + dt * k2 / 2)
    k4 = deriv(t + dt, current + dt * k3)
    return current + dt * (k1 + 2*k2 + 2*k3 + k4) / 6


def simulate_phase_rl(
    table: BemfTable,
    rpm: float,
    winding_resistance_ohm: Sequence[float],
    load_resistance_ohm: Sequence[float],
    phase_inductance_h: float,
    *,
    steps_per_period: int | None = None,
    max_periods: int = 80,
    periodic_tolerance_a: float = 1e-7,
) -> BrakingResult:
    """Integrate three independent phase R-L branches to periodic steady state.

    Winding resistance is per phase and must correspond to the BEMF connection.
    Load resistance is an external resistor in series with each phase.
    This approximation assumes no mutual coupling and no zero-sequence path.
    """
    if rpm < 0 or not math.isfinite(rpm):
        raise ValueError("rpm must be finite and non-negative")
    if len(winding_resistance_ohm) != 3 or len(load_resistance_ohm) != 3:
        raise ValueError("exactly three winding and load resistances are required")
    if any((not math.isfinite(x) or x < 0) for x in winding_resistance_ohm):
        raise ValueError("winding resistances must be finite and non-negative")
    if any((not math.isfinite(x) or x < 0) for x in load_resistance_ohm):
        raise ValueError("load resistances must be finite and non-negative")
    if not math.isfinite(phase_inductance_h) or phase_inductance_h <= 0:
        raise ValueError("phase_inductance_h must be finite and positive")
    if max_periods < 2 or periodic_tolerance_a <= 0:
        raise ValueError("max_periods must be >= 2 and tolerance must be positive")

    if rpm == 0:
        return BrakingResult(0.0, 0.0, 0.0, (0.0,)*3, (0.0,)*3,
                             0.0, 0.0, 0.0, 0, True)

    omega_m = _TWO_PI * rpm / 60.0
    omega_e = table.pole_pairs * omega_m
    period = _TWO_PI / omega_e
    nsteps = steps_per_period or max(256, table.samples_per_period * 8)
    if nsteps < table.samples_per_period:
        raise ValueError("steps_per_period must be >= number of BEMF samples")
    dt = period / nsteps
    r_total = [winding_resistance_ohm[i] + load_resistance_ohm[i] for i in range(3)]
    if any(r <= 0 for r in r_total):
        raise ValueError("each phase needs positive total series resistance")

    currents = [0.0, 0.0, 0.0]
    last_period: list[list[float]] = []
    periodic_error = math.inf
    converged = False
    used_periods = 0
    for period_index in range(max_periods):
        start = currents[:]
        samples = [[], [], []]
        for k in range(nsteps):
            t = k * dt
            for phase in range(3):
                currents[phase] = _rk4_step(
                    currents[phase], t, dt, rpm, table, phase,
                    r_total[phase], phase_inductance_h
                )
                samples[phase].append(currents[phase])
        periodic_error = max(abs(currents[i] - start[i]) for i in range(3))
        used_periods = period_index + 1
        last_period = samples
        if periodic_error <= periodic_tolerance_a:
            converged = True
            break

    # Integrate power over the final electrical period using matching time samples.
    p_em = p_cu = p_load = 0.0
    sum_i2 = [0.0, 0.0, 0.0]
    peak_i = [0.0, 0.0, 0.0]
    for k in range(nsteps):
        t = k * dt
        angle = 360.0 * t / period
        emf = table.at_angle(angle, rpm)
        for phase in range(3):
            current = last_period[phase][k]
            p_em += emf[phase] * current
            p_cu += winding_resistance_ohm[phase] * current * current
            p_load += load_resistance_ohm[phase] * current * current
            sum_i2[phase] += current * current
            peak_i[phase] = max(peak_i[phase], abs(current))
    p_em *= dt / period
    p_cu *= dt / period
    p_load *= dt / period
    rms = tuple(math.sqrt(x / nsteps) for x in sum_i2)
    torque = p_em / omega_m
    return BrakingResult(rpm, torque, p_em, rms, tuple(peak_i),
                         p_cu, p_load, periodic_error, used_periods, converged)


def sinusoidal_analytical_torque(
    table: BemfTable, rpm: float, winding_resistance_ohm: float,
    load_resistance_ohm: float, phase_inductance_h: float
) -> float:
    """Analytical balanced-sine check; requires Ld=Lq represented by phase L."""
    if rpm <= 0:
        return 0.0
    if winding_resistance_ohm < 0 or load_resistance_ohm < 0 or phase_inductance_h <= 0:
        raise ValueError("invalid R/L parameter")
    # Fundamental phase peak from a discrete Fourier projection of phase U.
    n = table.samples_per_period
    e_peak_ref = 2.0 / n * sum(table.phase_v[k][0] *
                              math.sin(_TWO_PI * k / n) for k in range(n))
    e_peak = abs(e_peak_ref) * rpm / table.reference_rpm
    omega_e = table.pole_pairs * _TWO_PI * rpm / 60.0
    r_total = winding_resistance_ohm + load_resistance_ohm
    p_em = 1.5 * e_peak * e_peak * r_total / (
        r_total * r_total + (omega_e * phase_inductance_h) ** 2
    )
    return p_em / (_TWO_PI * rpm / 60.0)
