"""Periodic angle-dependent 3x3 inductance matrix data and interpolation.

This module is data/math infrastructure only. It does not simulate currents,
compute torque, or represent a specific BM1109 winding topology.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


Matrix3 = tuple[tuple[float, float, float], ...]


def _as_matrix3(value: Sequence[Sequence[float]]) -> Matrix3:
    if len(value) != 3 or any(len(row) != 3 for row in value):
        raise ValueError("each inductance sample must be a 3x3 matrix")
    result = tuple(tuple(float(x) for x in row) for row in value)
    if not all(math.isfinite(x) for row in result for x in row):
        raise ValueError("inductance samples must contain finite values")
    scale = max(abs(x) for row in result for x in row)
    if scale == 0:
        raise ValueError("inductance matrix must be positive definite")
    tol = max(1e-15, scale * 1e-10)
    for i in range(3):
        for j in range(3):
            if abs(result[i][j] - result[j][i]) > tol:
                raise ValueError("inductance matrix must be symmetric")
    # Cholesky positive-definiteness check.
    c = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(i + 1):
            v = result[i][j] - math.fsum(c[i][k] * c[j][k] for k in range(j))
            if i == j:
                if v <= tol:
                    raise ValueError("inductance matrix must be positive definite")
                c[i][j] = math.sqrt(v)
            else:
                c[i][j] = v / c[j][j]
    return result


def _wrap(angle_deg: float) -> float:
    return angle_deg % 360.0


def salient_pmsm_labc(
    angle_deg: float, ld_h: float, lq_h: float, l0_h: float
) -> Matrix3:
    """Synthetic symmetric-PMSM Labc(theta), including explicit zero sequence.

    Ld, Lq and L0 are henries; angle is electrical degrees. L0 is required
    explicitly because it is not determined by the dq inductances.
    """
    values = (angle_deg, ld_h, lq_h, l0_h)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("angle and inductances must be finite")
    if min(ld_h, lq_h, l0_h) <= 0:
        raise ValueError("Ld, Lq and L0 must be positive")
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    lab = (
        (ld_h*c*c + lq_h*s*s, (ld_h-lq_h)*c*s),
        ((ld_h-lq_h)*c*s, ld_h*s*s + lq_h*c*c),
    )
    b = ((1.0, 0.0), (-0.5, math.sqrt(3.0)/2.0),
         (-0.5, -math.sqrt(3.0)/2.0))
    clarke = ((2.0/3.0, -1.0/3.0, -1.0/3.0),
              (0.0, 1.0/math.sqrt(3.0), -1.0/math.sqrt(3.0)))
    result = [[0.0]*3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            result[i][j] = math.fsum(
                b[i][a] * lab[a][d] * clarke[d][j]
                for a in range(2) for d in range(2)
            ) + l0_h/3.0
    matrix = tuple(tuple(row) for row in result)
    return _as_matrix3(matrix)


def salient_pmsm_labc_derivative(
    angle_deg: float, ld_h: float, lq_h: float, l0_h: float
) -> Matrix3:
    """Analytic dLabc/d(theta_e), in H/radian, for salient_pmsm_labc."""
    values = (angle_deg, ld_h, lq_h, l0_h)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("angle and inductances must be finite")
    if min(ld_h, lq_h, l0_h) <= 0:
        raise ValueError("Ld, Lq and L0 must be positive")
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    dlab = (
        (2.0*(lq_h-ld_h)*s*c, (ld_h-lq_h)*(c*c-s*s)),
        ((ld_h-lq_h)*(c*c-s*s), 2.0*(ld_h-lq_h)*s*c),
    )
    b = ((1.0, 0.0), (-0.5, math.sqrt(3.0)/2.0),
         (-0.5, -math.sqrt(3.0)/2.0))
    clarke = ((2.0/3.0, -1.0/3.0, -1.0/3.0),
              (0.0, 1.0/math.sqrt(3.0), -1.0/math.sqrt(3.0)))
    result = [[0.0]*3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            result[i][j] = math.fsum(
                b[i][a] * dlab[a][d] * clarke[d][j]
                for a in range(2) for d in range(2)
            )
    return tuple(tuple(row) for row in result)


@dataclass(frozen=True)
class PeriodicInductanceTable:
    """Periodic Labc(theta), with angles in degrees and inductances in henries.

    Angles must be strictly increasing in [0, 360), with at least three
    samples. Periodic cubic Hermite interpolation is performed componentwise.
    The constructor validates symmetry and positive definiteness at source
    samples and on a dense interior grid of every interpolation interval.
    """

    angles_deg: tuple[float, ...]
    matrices_h: tuple[Matrix3, ...]

    def __init__(self, angles_deg: Sequence[float],
                 matrices_h: Sequence[Sequence[Sequence[float]]]):
        angles = tuple(float(x) for x in angles_deg)
        if len(angles) < 3 or len(matrices_h) != len(angles):
            raise ValueError("at least three angle/matrix samples are required")
        if not all(math.isfinite(x) for x in angles):
            raise ValueError("angles must be finite")
        if angles[0] < 0 or angles[-1] >= 360:
            raise ValueError("angles must lie in [0, 360), without a duplicate 360 row")
        if any(angles[i + 1] <= angles[i] for i in range(len(angles) - 1)):
            raise ValueError("angles must be strictly increasing")
        matrices = tuple(_as_matrix3(m) for m in matrices_h)
        object.__setattr__(self, "angles_deg", angles)
        object.__setattr__(self, "matrices_h", matrices)
        # Reject cubic overshoot that would make the interpolated matrix
        # non-positive-definite between measured samples.
        for i in range(len(angles)):
            left = angles[i]
            right = angles[(i + 1) % len(angles)]
            if i == len(angles) - 1:
                right += 360.0
            for k in range(1, 33):
                theta = left + (right - left) * k / 33.0
                interpolated, _ = self._evaluate_unchecked(theta)
                _as_matrix3(interpolated)

    def _slope(self, index: int, row: int, col: int) -> float:
        n = len(self.angles_deg)
        prev_i = (index - 1) % n
        next_i = (index + 1) % n
        x_prev = self.angles_deg[prev_i]
        x_next = self.angles_deg[next_i]
        if prev_i > index:
            x_prev -= 360.0
        if next_i < index:
            x_next += 360.0
        y_prev = self.matrices_h[prev_i][row][col]
        y_next = self.matrices_h[next_i][row][col]
        return (y_next - y_prev) / (x_next - x_prev)

    def _evaluate_unchecked(self, angle_deg: float) -> tuple[Matrix3, Matrix3]:
        theta = _wrap(angle_deg)
        n = len(self.angles_deg)
        i = 0
        while i + 1 < n and self.angles_deg[i + 1] <= theta:
            i += 1
        j = (i + 1) % n
        x0 = self.angles_deg[i]
        x1 = self.angles_deg[j]
        if j == 0:
            x1 += 360.0
        x = theta
        if x < x0:
            x += 360.0
        h = x1 - x0
        t = (x - x0) / h
        h00 = 2*t**3 - 3*t**2 + 1
        h10 = t**3 - 2*t**2 + t
        h01 = -2*t**3 + 3*t**2
        h11 = t**3 - t**2
        dh00 = (6*t*t - 6*t) / h
        dh10 = 3*t*t - 4*t + 1
        dh01 = (-6*t*t + 6*t) / h
        dh11 = 3*t*t - 2*t
        values = [[0.0]*3 for _ in range(3)]
        derivs = [[0.0]*3 for _ in range(3)]
        for r in range(3):
            for c in range(3):
                y0 = self.matrices_h[i][r][c]
                y1 = self.matrices_h[j][r][c]
                m0 = self._slope(i, r, c)
                m1 = self._slope(j, r, c)
                values[r][c] = h00*y0 + h10*h*m0 + h01*y1 + h11*h*m1
                derivs[r][c] = dh00*y0 + dh10*m0 + dh01*y1 + dh11*m1
        # Preserve exact symmetry against round-off from independent paths.
        for r in range(3):
            for c in range(r):
                v = 0.5 * (values[r][c] + values[c][r])
                d = 0.5 * (derivs[r][c] + derivs[c][r])
                values[r][c] = values[c][r] = v
                derivs[r][c] = derivs[c][r] = d
        return (
            tuple(tuple(row) for row in values),
            tuple(tuple(row) for row in derivs),
        )

    def evaluate(self, angle_deg: float) -> Matrix3:
        """Return Labc(theta), with periodic wrapping."""
        if not math.isfinite(angle_deg):
            raise ValueError("angle must be finite")
        matrix, _ = self._evaluate_unchecked(angle_deg)
        _as_matrix3(matrix)
        return matrix

    def derivative_per_degree(self, angle_deg: float) -> Matrix3:
        """Return dLabc/d(theta_deg), in H/degree."""
        if not math.isfinite(angle_deg):
            raise ValueError("angle must be finite")
        matrix, derivative = self._evaluate_unchecked(angle_deg)
        _as_matrix3(matrix)
        return derivative

    def derivative_per_radian(self, angle_deg: float) -> Matrix3:
        """Return dLabc/d(theta_rad), in H/rad."""
        scale = 180.0 / math.pi
        derivative = self.derivative_per_degree(angle_deg)
        return tuple(tuple(x * scale for x in row) for row in derivative)
