"""DOE_3 (A+B+C) для BM1109: plant-модель в dq0, линеаризация ROM, цифровой PI.

Контракт: docs/tz/TZ_DOE3_CONTROL_CONTRACT_V0_1.md (заморожен 2026-11-25).
Текущая реализация приведена к контракту; расхождения — дефекты.

A — нелинейная модель объекта из магнитной ROM, полная 3x3 (контракт §3):

    u_dq0 = R_dq0 i_dq0 + L_inc^total di_dq0/dt
            + w_e ( dpsi_total/dtheta_e|_i + J psi_total ),
    J psi = (-psi_q, psi_d, 0),  w_e = p * omega_m,
    psi_total = psi_FEM + L_sigma,end^dq0 i  (контракт §6).

B — линеаризация 3x3 около рабочей точки сетки ROM:

    A_ii = -L3^-1 ( R I + w_e ( dM/di + J L3 ) ),  M = dpsi/dtheta_e,

C — цифровой токовый контур: PI с компенсацией полюса объекта

    K_p = L w_c,  K_i = R w_c,

— diagonal approximation (контракт §8): cross-coupling в регуляторе не
учитывается, coupling_index — только диагностика.

Нулевая последовательность (контракт §2): I0 — полноценная переменная
состояния; строки с I0 != 0 легитимны и не исключаются.

Момент (контракт §5): единственный источник — co-energy derivative DOE-1
(колонка Te_coenergy в ROM). Без неё момент NOT_EVALUATED; cross-product
форма остаётся только диагностикой.

Все числа на синтетической ROM — DERIVED; аппаратная проверка NOT_EVALUATED.
"""
from __future__ import annotations

import argparse
import cmath
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_doe2 as doe2  # noqa: E402

PWM_DELAY_FACTOR = 1.5
PARAM_KEYS = ("pole_pairs", "r_phase_ohm", "f_pwm_hz", "omega_c_rad_s",
              "pm_target_deg", "j_inertia_kg_m2", "b_friction_nm_s",
              "psi_reference", "l_sigma_end_uvw_h")
REQUIRED_KEYS = ("pole_pairs", "r_phase_ohm", "f_pwm_hz",
                 "omega_c_rad_s", "pm_target_deg", "psi_reference")

OUT_HEADER = [
    "case_id", "theta_e_deg", "Id_A", "Iq_A", "I0_A",
    "Ldd_H", "Lqq_H", "L00_H", "Ldq_H", "Lqd_H", "Ld0_H", "L0d_H",
    "Lq0_H", "L0q_H", "coupling_index",
    "Te_Nm", "Te_cross_diagnostic_Nm",
    "A_dd", "A_dq", "A_d0", "A_qd", "A_qq", "A_q0", "A_0d", "A_0q", "A_00",
    "eig1_re", "eig1_im", "eig2_re", "eig2_im", "eig3_re", "eig3_im",
    "max_re_lambda", "lambda_rest_max",
    "Kp_d", "Ki_d", "Kp_q", "Ki_q",
    "T_delay_s", "PM_deg", "PM_design_deg", "GM_dB",
    "omega_c_rad_s", "omega_c_max_rad_s", "max_pole_radius",
    "status", "note",
]


def _num(value):
    return doe2._num(value)


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle)]


def row_complete(row: dict) -> bool:
    """Строка ROM полна: все поля есть и конечны; nan — отсутствующее значение."""
    for name in doe2.ROM_FIELDS:
        value = _num(row.get(name))
        if value is None or not math.isfinite(value):
            return False
    return True


def grid_index(rows: list[dict]) -> dict[tuple[int, int, int, float], dict]:
    """Ключ — утроенные нормированные координаты (шаг сетки 0.5 -> целые)."""
    table = {}
    for row in rows:
        if not row_complete(row):
            continue
        key = (round(_num(row["xd"]) * 2), round(_num(row["xq"]) * 2),
               round(_num(row["x0"]) * 2), round(_num(row["theta_e_deg"]), 3))
        table[key] = row
    return table


def grid_step(rows: list[dict]) -> float | None:
    """Шаг сетки в амперах: I = x * ir, восстанавливается из самих строк."""
    estimates = []
    for row in rows:
        i0, x0 = _num(row.get("I0_A")), _num(row.get("x0"))
        if i0 is None or x0 is None or abs(x0) < 0.5:
            continue
        estimates.append(abs(i0 / x0))
    if not estimates:
        return None
    estimates.sort()
    return estimates[len(estimates) // 2]


# ── 3x3 линейная алгебра ──────────────────────────────────────────────────

def inv3(m: list[list[float]]) -> list[list[float]] | None:
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = (a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g))
    if det == 0.0:
        return None
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def mat_vec3(m: list[list[float]], v) -> tuple[float, float, float]:
    return tuple(sum(m[r][k] * v[k] for k in range(3)) for r in range(3))


def mat_mul3(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)]


# ── момент ────────────────────────────────────────────────────────────────

def torque_cross_product(row: dict, pole_pairs: float) -> float:
    """p (psi_d i_q - psi_q i_d) — ТОЛЬКО диагностика (контракт §5):
    при i = 0 тождественно ноль, cogging невидим; не источник момента."""
    return pole_pairs * (_num(row["psi_d"]) * _num(row["Iq_A"])
                         - _num(row["psi_q"]) * _num(row["Id_A"]))


def torque_source(row: dict) -> float | None:
    """Нормативный момент — co-energy derivative, колонка Te_coenergy из DOE-1.
    Без колонки момент недоступен (NOT_EVALUATED), а не подменяется формулой."""
    value = _num(row.get("Te_coenergy"))
    if value is None or not math.isfinite(value):
        return None
    return value


# ── plant-модель 3x3 (контракт §3) ─────────────────────────────────────────

def plant_matrices(row: dict, r_phase, l_sigma_end_uvw):
    """psi_total, dpsi_total/dtheta, L_total, R_dq0 для строки ROM.

    Источники — DOE-2 (end_leakage_dq0, resistance_dq0): собственной копии
    voltage equation в DOE-3 нет (контракт §3, §7).
    """
    theta = _num(row["theta_e_deg"])
    psi = list(doe2.flux(row))
    dpsi = [_num(row["dpsi_d_dtheta_e"]), _num(row["dpsi_q_dtheta_e"]),
            _num(row["dpsi_0_dtheta_e"])]
    table = [line[:] for line in doe2.matrix(row)]
    d_end = None
    if l_sigma_end_uvw is not None:
        current = (_num(row["Id_A"]), _num(row["Iq_A"]), _num(row["I0_A"]))
        l_end, d_end = doe2.end_leakage_dq0(l_sigma_end_uvw, theta)
        for r in range(3):
            psi[r] += sum(l_end[r][j] * current[j] for j in range(3))
            dpsi[r] += sum(d_end[r][j] * current[j] for j in range(3))
            for c in range(3):
                table[r][c] += l_end[r][c]
    r_dq0 = doe2.resistance_dq0(r_phase, theta)
    return psi, dpsi, table, r_dq0


def rotation_emf(row: dict, omega_m: float, pole_pairs: float,
                 r_phase, l_sigma_end_uvw) -> tuple[float, float, float]:
    """w_e (dpsi_total/dtheta + J psi_total) — ЭДС вращения в dq0."""
    psi, dpsi, _, _ = plant_matrices(row, r_phase, l_sigma_end_uvw)
    w_e = pole_pairs * omega_m
    rotation = (dpsi[0] - psi[1], dpsi[1] + psi[0], dpsi[2])
    return tuple(w_e * value for value in rotation)


def plant_derivative(row: dict, u_dq0, omega_m: float, pole_pairs: float,
                     r_phase, l_sigma_end_uvw) -> tuple[tuple[float, float, float],
                                                        float | None]:
    """di_dq0/dt и T_e для строки ROM — полная 3x3 модель (контракт §3).

    Возвращает момент из torque_source; None — момент NOT_EVALUATED.
    """
    psi, dpsi, table, r_dq0 = plant_matrices(row, r_phase, l_sigma_end_uvw)
    inverse = inv3(table)
    if inverse is None:
        raise ValueError(f"{row.get('case_id')}: L3 вырождена")
    current = (_num(row["Id_A"]), _num(row["Iq_A"]), _num(row["I0_A"]))
    resistive = mat_vec3(r_dq0, current)
    w_e = pole_pairs * omega_m
    emf = tuple(w_e * value for value in
                (dpsi[0] - psi[1], dpsi[1] + psi[0], dpsi[2]))
    rhs = tuple(u_dq0[k] - resistive[k] - emf[k] for k in range(3))
    return mat_vec3(inverse, rhs), torque_source(row)


def _neighbour(index: dict, row: dict, dx: int, axis: int) -> dict | None:
    key = [round(_num(row["xd"]) * 2), round(_num(row["xq"]) * 2),
           round(_num(row["x0"]) * 2)]
    key[axis] += dx                       # dx в утроенных единицах (±2 = ±1.0)
    return index.get((key[0], key[1], key[2], round(_num(row["theta_e_deg"]), 3)))


def dm_di(row: dict, index: dict, ir: float) -> tuple[list[list[float]] | None, str]:
    """dM/di 3x3, M = dpsi_dq0/dtheta_e: центральная разность по всем осям."""
    columns = []
    for axis in range(3):
        plus = _neighbour(index, row, 2, axis)
        minus = _neighbour(index, row, -2, axis)
        if plus is None or minus is None:
            return None, "no_neighbours"
        delta = 2.0 * ir
        columns.append([(_num(plus["dpsi_d_dtheta_e"]) - _num(minus["dpsi_d_dtheta_e"])) / delta,
                        (_num(plus["dpsi_q_dtheta_e"]) - _num(minus["dpsi_q_dtheta_e"])) / delta,
                        (_num(plus["dpsi_0_dtheta_e"]) - _num(minus["dpsi_0_dtheta_e"])) / delta])
    return [[columns[c][r] for c in range(3)] for r in range(3)], "central"


def l_from_flux(row: dict, index: dict, ir: float) -> tuple[list[list[float]] | None, str]:
    """Диагностическая сверка L_inc секундой по узлам сетки (3x3)."""
    columns = []
    for axis in range(3):
        plus = _neighbour(index, row, 2, axis)
        minus = _neighbour(index, row, -2, axis)
        if plus is None or minus is None:
            return None, "no_neighbours"
        delta = 2.0 * ir
        columns.append([(_num(plus["psi_d"]) - _num(minus["psi_d"])) / delta,
                        (_num(plus["psi_q"]) - _num(minus["psi_q"])) / delta,
                        (_num(plus["psi_0"]) - _num(minus["psi_0"])) / delta])
    return [[columns[c][r] for c in range(3)] for r in range(3)], "central"


def linearize_a(row: dict, index: dict, ir: float, omega_m: float,
                pole_pairs: float, r_phase, l_sigma_end_uvw,
                ) -> tuple[list[list[float]] | None, str]:
    """A = -L3^-1 ( R I + w_e (dM/di + J L3) ) — полная 3x3 линеаризация."""
    _, _, table, r_dq0 = plant_matrices(row, r_phase, l_sigma_end_uvw)
    inverse = inv3(table)
    gradient, method = dm_di(row, index, ir)
    if inverse is None or gradient is None:
        return None, method
    w_e = pole_pairs * omega_m
    j_l = [[-table[1][c] for c in range(3)], [table[0][c] for c in range(3)],
           [0.0, 0.0, 0.0]]
    inner = [[r_dq0[r][c] + w_e * (gradient[r][c] + j_l[r][c])
              for c in range(3)] for r in range(3)]
    product = mat_mul3(inverse, inner)
    return [[-product[r][c] for c in range(3)] for r in range(3)], method


def eigen3(m: list[list[float]]) -> list[complex]:
    """Собственные значения 3x3 через характеристический полином (Дюран-Кернер)."""
    trace = m[0][0] + m[1][1] + m[2][2]
    minors = ((m[0][0] * m[1][1] - m[0][1] * m[1][0])
              + (m[0][0] * m[2][2] - m[0][2] * m[2][0])
              + (m[1][1] * m[2][2] - m[1][2] * m[2][1]))
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    return poly_roots([1.0, -trace, minors, -det])


def loop_gain(omega: float, inductance: float, resistance: float,
              omega_c: float, delay: float) -> complex:
    """(K_p + K_i/s) 1/(Ls+R) e^{-s T_d}; при K_i/K_p = R/L равно w_c/s e^{-sTd}."""
    s = 1j * omega
    kp = inductance * omega_c
    ki = resistance * omega_c
    return (kp + ki / s) / (inductance * s + resistance) * cmath.exp(-s * delay)


def _crossover(inductance: float, resistance: float, omega_c: float,
               delay: float) -> float | None:
    low, high = 1e-3, 1e7
    if abs(loop_gain(low, inductance, resistance, omega_c, delay)) < 1.0:
        return None
    if abs(loop_gain(high, inductance, resistance, omega_c, delay)) > 1.0:
        return None
    for _ in range(200):
        middle = math.sqrt(low * high)
        if abs(loop_gain(middle, inductance, resistance, omega_c, delay)) > 1.0:
            low = middle
        else:
            high = middle
    return math.sqrt(low * high)


def phase_margin(inductance: float, resistance: float, omega_c: float,
                 delay: float) -> float | None:
    crossing = _crossover(inductance, resistance, omega_c, delay)
    if crossing is None:
        return None
    value = loop_gain(crossing, inductance, resistance, omega_c, delay)
    return 180.0 + math.degrees(cmath.phase(value))


def gain_margin_db(inductance: float, resistance: float, omega_c: float,
                   delay: float) -> float | None:
    """Запас по усилению: 1/|L| на частоте фазового перехода -180 град."""
    previous = None
    step = 1.02
    omega = 1e-2
    while omega < 1e7:
        value = loop_gain(omega, inductance, resistance, omega_c, delay)
        phase = math.degrees(cmath.phase(value))
        if phase <= -180.0:
            if previous is not None:
                magnitude = abs(loop_gain(previous, inductance, resistance, omega_c, delay))
                if magnitude > 0.0:
                    return -20.0 * math.log10(magnitude)
            return None
        previous = omega
        omega *= step
    return None


def poly_mul(a: list[float], b: list[float]) -> list[float]:
    result = [0.0] * (len(a) + len(b) - 1)
    for i, first in enumerate(a):
        for j, second in enumerate(b):
            result[i + j] += first * second
    return result


def poly_eval(coeffs: list[float], z: complex) -> complex:
    value = 0j
    for coefficient in coeffs:
        value = value * z + coefficient
    return value


def poly_roots(coeffs: list[float], iterations: int = 500) -> list[complex]:
    """Корни полинома (Дюран-Кернер); коэффициенты — от старшей степени."""
    degree = len(coeffs) - 1
    if degree < 1:
        return []
    monic = [coefficient / coeffs[0] for coefficient in coeffs]
    roots = [complex(0.4, 0.9) ** k for k in range(degree)]
    for _ in range(iterations):
        shift = 0.0
        updated = []
        for i, root in enumerate(roots):
            denominator = 1 + 0j
            for j, other in enumerate(roots):
                if i != j:
                    denominator *= (root - other)
            if denominator == 0:
                denominator = 1e-12
            new = root - poly_eval(monic, root) / denominator
            shift = max(shift, abs(new - root))
            updated.append(new)
        roots = updated
        if shift < 1e-13:
            break
    return roots


def closed_loop_poles(inductance: float, resistance: float, omega_c: float,
                      ts: float) -> list[complex]:
    """PI (Тастин) + ZOH-объект + z^-1 + Паде половины такта."""
    if resistance <= 0.0 or inductance <= 0.0:
        return []
    a = math.exp(-ts * resistance / inductance)
    b = (1.0 - a) / resistance
    kp = inductance * omega_c
    ki = resistance * omega_c
    c1 = kp + ki * ts / 2.0
    c2 = ki * ts / 2.0 - kp
    left = poly_mul(poly_mul([1.0, -1.0], [1.0, -a]), [3.0, 1.0])
    right = poly_mul([b * c1, b * c2], [1.0, 3.0])
    length = max(len(left), len(right))
    left = [0.0] * (length - len(left)) + left
    right = [0.0] * (length - len(right)) + right
    return poly_roots([l + r for l, r in zip(left, right)])


def bandwidth_ceiling(delay: float, pm_target_deg: float) -> float:
    """w_c_max = (pi/2 - PM_target) / T_d для контура w_c/s e^{-sTd}."""
    return (math.pi / 2.0 - math.radians(pm_target_deg)) / delay


def _l3(row: dict) -> list[list[float]]:
    return [[_num(row["Ldd"]), _num(row["Ldq"]), _num(row["Ld0"])],
            [_num(row["Lqd"]), _num(row["Lqq"]), _num(row["Lq0"])],
            [_num(row["L0d"]), _num(row["L0q"]), _num(row["L00"])]]


def design_point(row: dict, index: dict, ir: float, params: dict) -> dict:
    pole_pairs = params["pole_pairs"]
    resistance = params["r_phase_ohm"]
    l_end = params.get("l_sigma_end_uvw_h")
    f_pwm = params["f_pwm_hz"]
    omega_c = params["omega_c_rad_s"]
    pm_target = params["pm_target_deg"]
    ts = 1.0 / f_pwm
    delay = PWM_DELAY_FACTOR * ts

    table = _l3(row)
    if not all(math.isfinite(value) for line in table for value in line):
        return {"case_id": row.get("case_id", ""), "status": "DATA_INVALID",
                "note": "L3 содержит nan/inf — синтез невозможен"}
    inverse = inv3(table)
    out = {
        "case_id": row.get("case_id", ""),
        "theta_e_deg": _num(row["theta_e_deg"]),
        "Id_A": _num(row["Id_A"]), "Iq_A": _num(row["Iq_A"]),
        "I0_A": _num(row["I0_A"]),
        "Ldd_H": table[0][0], "Lqq_H": table[1][1], "L00_H": table[2][2],
        "Ldq_H": table[0][1], "Lqd_H": table[1][0],
        "Ld0_H": table[0][2], "L0d_H": table[2][0],
        "Lq0_H": table[1][2], "L0q_H": table[2][1],
        "T_delay_s": delay, "omega_c_rad_s": omega_c,
        "omega_c_max_rad_s": bandwidth_ceiling(delay, pm_target),
        "PM_design_deg": pm_target,
    }
    scale = max(abs(table[0][0]), abs(table[1][1]), abs(table[2][2]), 1e-12)
    out["coupling_index"] = max(abs(table[0][1]), abs(table[0][2]),
                                abs(table[1][2])) / scale
    out["Te_Nm"] = torque_source(row)
    out["Te_cross_diagnostic_Nm"] = torque_cross_product(row, pole_pairs)
    out["status"] = "OK"
    out["note"] = ""

    if inverse is None:
        out["status"] = "FAIL"
        out["note"] = "L3 вырождена"
        return out

    omega_m = params.get("omega_m_rad_s", 0.0)
    a_matrix, method = linearize_a(row, index, ir, omega_m, pole_pairs,
                                   resistance, l_end)
    if a_matrix is None:
        out["status"] = "A_UNAVAILABLE"
        out["note"] = f"dM/di не посчитана: {method}"
    else:
        eigs = sorted(eigen3(a_matrix), key=lambda z: -z.real)
        for k, root in enumerate(eigs, start=1):
            out[f"eig{k}_re"] = root.real
            out[f"eig{k}_im"] = root.imag
        out["max_re_lambda"] = max(root.real for root in eigs)
        for r in range(3):
            for c in range(3):
                out["A_" + "dq0"[r] + "dq0"[c]] = a_matrix[r][c]
    rest_matrix, _ = linearize_a(row, index, ir, 0.0, pole_pairs, resistance, l_end)
    if rest_matrix is not None:
        out["lambda_rest_max"] = max(root.real for root in eigen3(rest_matrix))

    axis_d = {"L": table[0][0], "R": resistance}
    axis_q = {"L": table[1][1], "R": resistance}
    out["Kp_d"] = axis_d["L"] * omega_c
    out["Ki_d"] = resistance * omega_c
    out["Kp_q"] = axis_q["L"] * omega_c
    out["Ki_q"] = resistance * omega_c

    margins = [phase_margin(axis["L"], axis["R"], omega_c, delay)
               for axis in (axis_d, axis_q)]
    gains = [gain_margin_db(axis["L"], axis["R"], omega_c, delay)
             for axis in (axis_d, axis_q)]
    usable = [value for value in margins if value is not None]
    out["PM_deg"] = min(usable) if usable else None
    gain_usable = [value for value in gains if value is not None]
    out["GM_dB"] = min(gain_usable) if gain_usable else None

    poles = [closed_loop_poles(axis["L"], axis["R"], omega_c, ts)
             for axis in (axis_d, axis_q)]
    radii = [max(abs(pole) for pole in group) for group in poles if group]
    out["max_pole_radius"] = max(radii) if radii else None
    if out["status"] == "OK":
        out["note"] = f"dM/di: {method}"
    return out


def _p2_n2_status(rom_path: Path | None) -> str | None:
    """Вердикт P2-N2 из summary.json рядом с ROM (наследование, контракт §9)."""
    if rom_path is None:
        return None
    candidate = rom_path.parent / "summary.json"
    if not candidate.exists():
        return None
    try:
        summary = json.loads(candidate.read_text(encoding="utf-8"))
        return summary.get("poc2_nonlinear", {}).get("P2_N2", {}).get("status")
    except (json.JSONDecodeError, OSError):
        return None


def analyse(rows: list[dict], params: dict, rom_path: Path | None = None,
            ) -> tuple[list[dict], dict]:
    missing = [name for name in REQUIRED_KEYS
               if params.get(name) is None]
    index = grid_index(rows)
    ir = grid_step(rows)
    gates: dict[str, str] = {}
    notes: dict[str, str] = {}

    complete = [row for row in rows if row_complete(row)]
    gates["rom_fields_complete"] = "PASS" if len(complete) == len(rows) and rows else "FAIL"
    missing_rows = len(rows) - len(complete)
    notes["rom_fields_complete"] = (
        f"{len(complete)}/{len(rows)} строк с конечными L/psi/dpsi"
        + (f"; {missing_rows} строк с nan — в синтез не попали" if missing_rows else ""))

    if ir is None:
        gates["grid_step"] = "NOT_EVALUATED"
        notes["grid_step"] = "шаг сетки ir не восстановлен (нет строк с x0 != 0)"
    else:
        gates["grid_step"] = "PASS"
        notes["grid_step"] = f"ir = {ir:g} A"

    if missing:
        gates["params"] = "NOT_EVALUATED"
        notes["params"] = "нет параметров: " + ", ".join(missing)
        return [], {"status": "NOT_EVALUATED", "gates": gates, "notes": notes}
    gates["params"] = "PASS"

    # контракт §4: psi_reference обязан быть ABSOLUTE
    if params.get("psi_reference") == "ABSOLUTE":
        gates["psi_reference_absolute"] = "PASS"
        notes["psi_reference_absolute"] = "заявлен оператором в params"
    else:
        gates["psi_reference_absolute"] = "NOT_EVALUATED"
        notes["psi_reference_absolute"] = (
            f"psi_reference = {params.get('psi_reference')!r}; без ABSOLUTE "
            "относительный flux неотличим — контракт §4")

    # контракт §6: L_sigma,end обязателен для plant/voltage-пути
    l_end = params.get("l_sigma_end_uvw_h")
    leakage_ok = (isinstance(l_end, list) and len(l_end) == 3
                  and all(isinstance(line, list) and len(line) == 3 for line in l_end))
    if leakage_ok:
        symmetric = all(math.isclose(l_end[r][c], l_end[c][r], rel_tol=0.0, abs_tol=1e-15)
                        for r in range(3) for c in range(3))
        if symmetric:
            gates["leakage_contract"] = "PASS"
            notes["leakage_contract"] = "L_sigma,end^UVW 3x3 симметрична, задана явно"
        else:
            gates["leakage_contract"] = "FAIL"
            notes["leakage_contract"] = "L_sigma,end^UVW несимметрична — contract failure"
    else:
        gates["leakage_contract"] = "NOT_EVALUATED"
        notes["leakage_contract"] = "нет l_sigma_end_uvw_h — не неявный ноль (контракт §6)"

    # контракт §5: момент из co-energy колонки DOE-1
    te_rows = sum(1 for row in complete if torque_source(row) is not None)
    if te_rows == len(complete) and complete:
        gates["torque_source"] = "PASS"
        notes["torque_source"] = f"Te_coenergy в {te_rows} строках"
    elif te_rows:
        gates["torque_source"] = "NOT_EVALUATED"
        notes["torque_source"] = f"Te_coenergy только в {te_rows}/{len(complete)} строк"
    else:
        gates["torque_source"] = "NOT_EVALUATED"
        notes["torque_source"] = ("в ROM нет Te_coenergy; cross-product — диагностика, "
                                  "не источник (контракт §5)")

    points = []
    for row in complete:
        points.append(design_point(row, index, ir or 1.0, params))

    failed = [point for point in points if point["status"] == "FAIL"]
    with_a = [point for point in points if point["status"] == "OK"]
    usable = points

    gates["point_status"] = "PASS" if not failed else "FAIL"
    notes["point_status"] = (f"OK: {len(with_a)}, A_UNAVAILABLE: "
                             f"{len(points) - len(with_a) - len(failed)}, "
                             f"FAIL: {len(failed)}")

    # контракт §9: reciprocity наследуется от P2-N2, а не произвольный порог
    n2 = _p2_n2_status(rom_path)
    if n2 == "PASS":
        gates["reciprocity_contract"] = "PASS"
    elif n2 == "FAIL":
        gates["reciprocity_contract"] = "FAIL"
    else:
        gates["reciprocity_contract"] = "NOT_EVALUATED"
    notes["reciprocity_contract"] = (
        f"P2-N2 из summary.json ROM: {n2 or 'нет данных'}; собственного порога нет (§9)")

    # positive definiteness — до отдельного критерия только диагностика (§9)
    min_eig = math.inf
    for row in complete:
        table = _l3(row)
        sym = [[(table[r][c] + table[c][r]) / 2.0 for c in range(3)] for r in range(3)]
        try:
            min_eig = min(min_eig, min(root.real for root in eigen3(sym)))
        except Exception:
            pass
    gates["l_positive_definite"] = "DIAGNOSTIC"
    notes["l_positive_definite"] = (f"min Re eig симметричной части L3 = {min_eig:.3e}; "
                                    "нормативного критерия пока нет (контракт §9)")

    # §8: diagonal approximation — DIAGNOSTIC, coupling не учтён в регуляторе
    couplings = [point["coupling_index"] for point in usable
                 if point.get("coupling_index") is not None]
    gates["diagonal_approximation"] = "DIAGNOSTIC"
    notes["diagonal_approximation"] = (
        f"max coupling_index = {max(couplings) if couplings else 0.0:.3e}; "
        "Kp/Ki per-axis, cross-coupling в регуляторе не учтён (контракт §8)")

    # §2/§9: реальная проверка представления I0 в данных вместо тавтологии
    if ir:
        worst_i0 = 0.0
        for row in complete:
            expected = _num(row["x0"]) * ir
            worst_i0 = max(worst_i0, abs(_num(row["I0_A"]) - expected) / ir)
        gates["i0_data_consistency"] = "PASS" if worst_i0 <= 1e-6 else "FAIL"
        notes["i0_data_consistency"] = (
            f"max |I0 - x0*ir|/ir = {worst_i0:.2e}; строки с I0 != 0 легитимны "
            "и в синтез входят (контракт §2)")
    else:
        gates["i0_data_consistency"] = "NOT_EVALUATED"

    # сверка L_inc секундой по узлам — ДИАГНОСТИКА, как прежде
    worst_rel = 0.0
    checked = 0
    if ir:
        for row in complete:
            fresh, _ = l_from_flux(row, index, ir)
            if fresh is None:
                continue
            table = _l3(row)
            scale = max(abs(table[0][0]), abs(table[1][1]), 1e-12)
            error = max(abs(fresh[r][c] - table[r][c]) for r in range(3) for c in range(3))
            worst_rel = max(worst_rel, error / scale)
            checked += 1
    gates["l_inc_secant_diagnostic"] = "INFO"
    notes["l_inc_secant_diagnostic"] = (
        f"{checked} точек: max |dL|/|L| = {worst_rel:.2e}; порог не применяется"
        if checked else "нет соседних узлов для секунды")

    ts = 1.0 / params["f_pwm_hz"]
    delay = PWM_DELAY_FACTOR * ts
    omega_c = params["omega_c_rad_s"]
    pm_target = params["pm_target_deg"]
    identity = 90.0 - math.degrees(omega_c * delay)
    numeric = [point["PM_deg"] for point in usable if point.get("PM_deg") is not None]
    if numeric:
        worst_pm = max(abs(value - identity) for value in numeric)
        gates["phase_margin_identity"] = "PASS" if worst_pm <= 0.05 else "FAIL"
        notes["phase_margin_identity"] = (
            f"PM_числ vs 90-w_c T_d: max |d| = {worst_pm:.3f} град")
        gates["phase_margin"] = "PASS" if min(numeric) >= pm_target else "FAIL"
        notes["phase_margin"] = (f"PM = {min(numeric):.1f} град "
                                 f"(цель {pm_target:g} град)")
    else:
        gates["phase_margin_identity"] = "NOT_EVALUATED"
        gates["phase_margin"] = "NOT_EVALUATED"

    ceiling = bandwidth_ceiling(delay, pm_target)
    gates["bandwidth_ceiling"] = "PASS" if omega_c <= ceiling else "FAIL"
    notes["bandwidth_ceiling"] = (
        f"w_c = {omega_c:g} рад/с <= w_c_max = {ceiling:.1f} рад/с "
        f"({ceiling / (2 * math.pi):.1f} Гц)")

    radii = [point["max_pole_radius"] for point in usable
             if point.get("max_pole_radius") is not None]
    if radii:
        gates["discrete_stability"] = "PASS" if max(radii) < 1.0 else "FAIL"
        notes["discrete_stability"] = f"max |z| = {max(radii):.4f} (нужно < 1)"
    else:
        gates["discrete_stability"] = "NOT_EVALUATED"

    rest = [point["lambda_rest_max"] for point in with_a
            if point.get("lambda_rest_max") is not None]
    if rest:
        gates["a_eigen_stable_at_rest"] = "PASS" if max(rest) < 0.0 else "FAIL"
        notes["a_eigen_stable_at_rest"] = (
            f"max Re lambda (w_m = 0) = {max(rest):.3e} 1/s; ожидается < 0")
    else:
        gates["a_eigen_stable_at_rest"] = "NOT_EVALUATED"
        notes["a_eigen_stable_at_rest"] = "A не посчитана: нет соседних узлов ROM"

    if params.get("j_inertia_kg_m2") and params.get("b_friction_nm_s"):
        inertia = params["j_inertia_kg_m2"]
        friction = params["b_friction_nm_s"]
        tau_mech = inertia / friction if friction > 0 else None
        gates["speed_loop_mechanics"] = "PASS" if tau_mech else "NOT_EVALUATED"
        notes["speed_loop_mechanics"] = (
            "tau_mech = J/B = %.4f с; правило w_speed <= w_c/10" % tau_mech if tau_mech
            else "B = 0: механическая постоянная не определена")
    else:
        gates["speed_loop_mechanics"] = "NOT_EVALUATED"
        notes["speed_loop_mechanics"] = "нет J и B — численный синтез speed loop не выполнен"

    gates["hardware_validation"] = "NOT_EVALUATED"
    notes["hardware_validation"] = "ROM синтетическая; target readback и стенд не выполнялись"

    blocking = [name for name, value in gates.items() if value == "FAIL"]
    contract_missing = [name for name in ("psi_reference_absolute", "leakage_contract",
                                          "torque_source", "reciprocity_contract")
                        if gates.get(name) == "NOT_EVALUATED"]
    if blocking:
        status = "FAIL_3"
    elif contract_missing:
        status = "NOT_EVALUATED"
    else:
        status = "PASS_3"
    summary = {
        "status": status,
        "gates": gates,
        "blocking_gates": blocking,
        "contract_missing": contract_missing,
        "notes": notes,
        "params": {name: params.get(name) for name in PARAM_KEYS},
        "rows": len(points),
        "ok": len(with_a),
        "delay_s": delay,
        "pwm_period_s": ts,
        "delay_factor": PWM_DELAY_FACTOR,
        "omega_c_max_rad_s": ceiling,
        "model_note": ("полная dq0 3x3 plant-модель по TZ_DOE3_CONTROL_CONTRACT_V0_1; "
                       "I0 — переменная состояния, строки не исключаются"),
        "data_note": "синтетическая ROM: числа DERIVED",
        "speed_loop": "NOT_EVALUATED без J, B",
        "remaining": ["аппаратный readback G-IF", "реальная ROM (Maxwell)", "MTPA/FW"],
    }
    return points, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUT_HEADER)
        for row in rows:
            values = []
            for name in OUT_HEADER:
                value = row.get(name)
                values.append("" if value is None else value)
            writer.writerow(values)


def _row(**kwargs) -> dict:
    base = {"case_id": "S0", "xd": 0, "xq": 0, "x0": 0, "theta_e_deg": 0.0,
            "Id_A": 0.0, "Iq_A": 0.0, "I0_A": 0.0,
            "Ldd": 2e-4, "Ldq": 1e-5, "Lqd": 1e-5, "Lqq": 3.2e-4,
            "Ld0": 0.0, "Lq0": 0.0, "L0d": 0.0, "L0q": 0.0, "L00": 8e-5,
            "psi_d": 0.0, "psi_q": 0.0, "psi_0": 0.0,
            "dpsi_d_dtheta_e": 0.0, "dpsi_q_dtheta_e": 0.0, "dpsi_0_dtheta_e": 0.0}
    base.update(kwargs)
    return base


def selftest() -> int:
    """Проверка формул без чтения файлов: plant 3x3, PM, потолок, полюса."""
    checks: list[tuple[str, bool, str]] = []
    params = {"pole_pairs": 3, "r_phase_ohm": 0.1, "f_pwm_hz": 5000.0,
              "omega_c_rad_s": 300.0, "pm_target_deg": 45.0,
              "psi_reference": "ABSOLUTE",
              "l_sigma_end_uvw_h": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]}
    ts = 1.0 / params["f_pwm_hz"]
    delay = PWM_DELAY_FACTOR * ts

    row = _row(Iq_A=10.0, psi_d=0.02, Te_coenergy=0.6)
    checks.append(("torque из Te_coenergy, не cross-product",
                   torque_source(row) == 0.6,
                   f"T_e = {torque_source(row)} Н·м"))
    checks.append(("cross-product — диагностика 0.6",
                   abs(torque_cross_product(row, 3) - 0.6) < 1e-12,
                   f"T_cross = {torque_cross_product(row, 3):.6f}"))

    rest = _row()
    derivative, _ = plant_derivative(rest, (0.0, 0.0, 0.0), 0.0, 3, 0.1,
                                     params["l_sigma_end_uvw_h"])
    checks.append(("zero current/speed -> di/dt = 0 (3 компонента)",
                   all(abs(value) < 1e-12 for value in derivative),
                   f"di/dt = {derivative}"))

    # i0 = 0 не аксиома: ток нулевой последовательности идёт через L00
    row0 = _row(I0_A=2.0)
    derivative, _ = plant_derivative(row0, (0.0, 0.0, 1.0), 0.0, 3, 0.1,
                                     params["l_sigma_end_uvw_h"])
    expected0 = (1.0 - 0.1 * 2.0) / 8e-5
    checks.append(("I0 — переменная состояния: di0/dt = (u0 - R i0)/L00",
                   abs(derivative[2] - expected0) < 1e-6,
                   f"di0/dt = {derivative[2]:.3f}, ожидание {expected0:.3f}"))

    pm = phase_margin(2e-4, 0.1, 300.0, delay)
    expected = 90.0 - math.degrees(300.0 * delay)
    checks.append(("PM identity 90 - w_c T_d",
                   pm is not None and abs(pm - expected) < 0.05,
                   f"PM = {pm:.4f} град, ожидание {expected:.4f}"))

    ceiling = bandwidth_ceiling(delay, 45.0)
    checks.append(("ceiling = (pi/2 - PM)/T_d",
                   abs(ceiling - (math.pi / 2 - math.radians(45.0)) / delay) < 1e-12,
                   f"w_c_max = {ceiling:.1f} рад/с"))

    stable = closed_loop_poles(2e-4, 0.1, 300.0, ts)
    unstable = closed_loop_poles(2e-4, 0.1, 20000.0, ts)
    stable_radius = max(abs(pole) for pole in stable) if stable else math.inf
    unstable_radius = max(abs(pole) for pole in unstable) if unstable else 0.0
    checks.append(("discrete loop stable at w_c = 300",
                   stable_radius < 1.0, f"max|z| = {stable_radius:.4f}"))
    checks.append(("discrete loop unstable at w_c = 20000",
                   unstable_radius > 1.0, f"max|z| = {unstable_radius:.4f}"))

    # eigen3 сохраняет комплексные пары
    oscillatory = [[-1.0, 10.0, 0.0], [-10.0, -1.0, 0.0], [0.0, 0.0, -2.0]]
    roots = eigen3(oscillatory)
    has_imag = any(abs(root.imag) > 1.0 for root in roots)
    checks.append(("eigen3: комплексная пара не теряет imag",
                   has_imag and len(roots) == 3,
                   f"eig = {[(round(z.real, 2), round(z.imag, 2)) for z in roots]}"))

    index = {(0, 0, 0, 0.0): row}
    gradient, method = dm_di(row, index, 5.0)
    checks.append(("dM/di needs neighbours -> None", gradient is None,
                   f"method = {method}"))

    points, summary = analyse([row], params)
    checks.append(("analyse: полный контрактный вход -> не FAIL",
                   summary["status"] != "FAIL_3",
                   f"status = {summary['status']}, missing = {summary.get('contract_missing')}"))

    no_contract = dict(params)
    del no_contract["l_sigma_end_uvw_h"]
    _, summary2 = analyse([row], no_contract)
    checks.append(("без L_sigma,end -> NOT_EVALUATED, не PASS",
                   summary2["status"] == "NOT_EVALUATED",
                   f"status = {summary2['status']}"))

    failed = 0
    for name, ok, detail in checks:
        print(("OK   " if ok else "FAIL ") + name + " | " + detail)
        failed += 0 if ok else 1
    print(f"selftest: {len(checks) - failed}/{len(checks)} OK")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DOE_3: plant, linearization, digital PI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest")
    design = sub.add_parser("design")
    design.add_argument("--rom", required=True)
    design.add_argument("--params", required=True)
    design.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()

    rom_path = Path(args.rom)
    rows = load_rows(rom_path)
    with Path(args.params).open(encoding="utf-8") as handle:
        params = json.load(handle)
    points, summary = analyse(rows, params, rom_path=rom_path)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(outdir / "ctrl_points.csv", points)
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"rows: {summary['rows']} ok: {summary['ok']}")
    for name, value in summary["gates"].items():
        print(f"  {name}: {value}")
    for name, value in summary["notes"].items():
        print(f"    {name}: {value}")
    print(f"status: {summary['status']}; out: {outdir}")
    return 0 if summary["status"] == "PASS_3" else 1


if __name__ == "__main__":
    raise SystemExit(main())
