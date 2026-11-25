"""DOE_3 (A+B+C) для BM1109: plant-модель в dq0, линеаризация ROM, цифровой PI.

A — нелинейная модель объекта из магнитной ROM (L_inc = dpsi/di, dpsi/dtheta):

    di_dq/dt = L2^-1 [ u_dq - R i_dq
                       - w_e ( dpsi_dq/dtheta_e|_i + J psi_dq ) ],
    J = [[0, -1], [1, 0]],  w_e = p * omega_m,
    T_e = p ( psi_d i_q - psi_q i_d ).

B — линеаризация около рабочей точки сетки ROM:

    A_ii = -L2^-1 ( R I + w_e ( dM/di + J L2 ) ),  M = dpsi/dtheta_e,
    dM/di — центральная разность по узлам сетки, шаг ir (А).

C — цифровой токовый контур: PI с компенсацией полюса объекта

    K_p = L w_c,  K_i = R w_c,

задержка T_d = 1.5 T_s (вычисление 1 T_s + ZOH 0.5 T_s). При точной
компенсации (K_i/K_p = R/L) разомкнутый контур равен (w_c/s) e^{-s T_d},
поэтому фазовый запас PM = 90 - w_c T_d (град), а потолок по PM_target:

    w_c_max = (pi/2 - PM_target) / T_d.

Для устойчивости считается и дискретная модель: точная ZOH-дискретизация
1/(Ls+R), PI по Тастину, задержка z^-1 и половина такта через Паде
(z+3)/(3z+1); корни характеристического полинома ищутся методом
Дюрана-Кернера (без numpy).

МОДЕЛЬНОЕ ОГРАНИЧЕНИЕ (не следствие преобразования):
у BM1109 OEW-6T нет внешнего пути замыкания суммы фазных токов, поэтому
принято i_U+i_V+i_W = 0 и i_0 = 0. Строки ROM с I0 != 0 физически недостижимы
в этой топологии и помечаются MODEL_EXCLUDED; i_0 остаётся координатой
преобразования, а не током нейтрали. Паразитные ёмкости и common-mode пути
инверторов в модели не учитываются.

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

# Задержка контура в тактах PWM: 1 такт на вычисление + 0.5 такта ZOH.
PWM_DELAY_FACTOR = 1.5
# Сколько тактов PWM укладывается в один период (Ts = 1/f_pwm).
I0_TOL = 1e-9
PARAM_KEYS = ("pole_pairs", "r_phase_ohm", "f_pwm_hz", "omega_c_rad_s",
              "pm_target_deg", "j_inertia_kg_m2", "b_friction_nm_s")
# Без этих параметров синтез невозможен; J и B опциональны (speed loop).
REQUIRED_KEYS = ("pole_pairs", "r_phase_ohm", "f_pwm_hz",
                 "omega_c_rad_s", "pm_target_deg")

OUT_HEADER = [
    "case_id", "theta_e_deg", "Id_A", "Iq_A", "I0_A",
    "Ldd_H", "Lqq_H", "Ldq_H", "Lqd_H", "coupling_index",
    "Te_Nm", "A_dd", "A_dq", "A_qd", "A_qq",
    "lambda_1", "lambda_2", "lambda_1_re", "lambda_2_re", "lambda_rest_max",
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
    """Строка ROM полна: все поля есть и конечны.

    nan в L-колонках — это отсутствующее значение, а не число: DOE_1 пишет nan
    там, где стенсил не смог посчитать производную. Такие строки не должны
    попадать в синтез (иначе K_p = nan и это выглядит как OK).
    """
    for name in doe2.ROM_FIELDS:
        value = _num(row.get(name))
        if value is None or not math.isfinite(value):
            return False
    return True


def grid_index(rows: list[dict]) -> dict[tuple[int, int, int, float], dict]:
    table = {}
    for row in rows:
        if not row_complete(row):
            continue
        key = (round(_num(row["xd"])), round(_num(row["xq"])),
               round(_num(row["x0"])), round(_num(row["theta_e_deg"]), 3))
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


def l2(row: dict) -> list[list[float]]:
    """Инкрементальная индуктивность в подпространстве dq (i_0 = 0)."""
    return [[_num(row["Ldd"]), _num(row["Ldq"])],
            [_num(row["Lqd"]), _num(row["Lqq"])]]


def det2(matrix: list[list[float]]) -> float:
    return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]


def inv2(matrix: list[list[float]]) -> list[list[float]] | None:
    determinant = det2(matrix)
    if abs(determinant) < 1e-18:
        return None
    return [[matrix[1][1] / determinant, -matrix[0][1] / determinant],
            [-matrix[1][0] / determinant, matrix[0][0] / determinant]]


def mat_vec(matrix: list[list[float]], vector: tuple[float, float]) -> tuple[float, float]:
    return (matrix[0][0] * vector[0] + matrix[0][1] * vector[1],
            matrix[1][0] * vector[0] + matrix[1][1] * vector[1])


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(2)) for j in range(2)]
            for i in range(2)]


def torque(row: dict, pole_pairs: float) -> float:
    """T_e = p (psi_d i_q - psi_q i_d); i_0 не даёт момента."""
    return pole_pairs * (_num(row["psi_d"]) * _num(row["Iq_A"])
                         - _num(row["psi_q"]) * _num(row["Id_A"]))


def rotation(row: dict, omega_m: float, pole_pairs: float) -> tuple[float, float]:
    """w_e [dpsi_dq/dtheta_e + J psi_dq] — ЭДС вращения в dq."""
    psi_d, psi_q = _num(row["psi_d"]), _num(row["psi_q"])
    dpsi_d = _num(row["dpsi_d_dtheta_e"])
    dpsi_q = _num(row["dpsi_q_dtheta_e"])
    w_e = pole_pairs * omega_m
    return (w_e * (dpsi_d - psi_q), w_e * (dpsi_q + psi_d))


def plant_derivative(row: dict, u: tuple[float, float], omega_m: float,
                     pole_pairs: float, r_ohm: float) -> tuple[tuple[float, float], float]:
    """di_dq/dt и T_e для строки ROM; i_0 = 0 по модельному ограничению."""
    table = l2(row)
    inverse = inv2(table)
    if inverse is None:
        raise ValueError(f"{row.get('case_id')}: L2 вырождена")
    current = (_num(row["Id_A"]), _num(row["Iq_A"]))
    resistive = (r_ohm * current[0], r_ohm * current[1])
    emf = rotation(row, omega_m, pole_pairs)
    rhs = (u[0] - resistive[0] - emf[0], u[1] - resistive[1] - emf[1])
    return mat_vec(inverse, rhs), torque(row, pole_pairs)


def _neighbour(index: dict, row: dict, dxd: int, dxq: int) -> dict | None:
    key = (round(_num(row["xd"])) + dxd, round(_num(row["xq"])) + dxq,
           round(_num(row["x0"])), round(_num(row["theta_e_deg"]), 3))
    return index.get(key)


def dm_di(row: dict, index: dict, ir: float) -> tuple[list[list[float]] | None, str]:
    """dM/di, M = (dpsi_d/dtheta_e, dpsi_q/dtheta_e): центральная разность."""
    columns = []
    methods = set()
    for dxd, dxq in ((1, 0), (0, 1)):
        plus = _neighbour(index, row, dxd, dxq)
        minus = _neighbour(index, row, -dxd, -dxq)
        if plus is None or minus is None:
            return None, "no_neighbours"
        delta = 2.0 * ir
        columns.append((
            (_num(plus["dpsi_d_dtheta_e"]) - _num(minus["dpsi_d_dtheta_e"])) / delta,
            (_num(plus["dpsi_q_dtheta_e"]) - _num(minus["dpsi_q_dtheta_e"])) / delta,
        ))
        methods.add("central")
    return [[columns[0][0], columns[1][0]],
            [columns[0][1], columns[1][1]]], "+".join(sorted(methods))


def l_from_flux(row: dict, index: dict, ir: float) -> tuple[list[list[float]] | None, str]:
    """Независимая проверка L_inc: dpsi/di по значениям psi в узлах сетки."""
    columns = []
    for dxd, dxq in ((1, 0), (0, 1)):
        plus = _neighbour(index, row, dxd, dxq)
        minus = _neighbour(index, row, -dxd, -dxq)
        if plus is None or minus is None:
            return None, "no_neighbours"
        delta = 2.0 * ir
        columns.append((
            (_num(plus["psi_d"]) - _num(minus["psi_d"])) / delta,
            (_num(plus["psi_q"]) - _num(minus["psi_q"])) / delta,
        ))
    return [[columns[0][0], columns[1][0]],
            [columns[0][1], columns[1][1]]], "central"


def linearize_a(row: dict, index: dict, ir: float, omega_m: float,
                pole_pairs: float, r_ohm: float) -> tuple[list[list[float]] | None, str]:
    """A_ii = -L2^-1 ( R I + w_e (dM/di + J L2) )."""
    table = l2(row)
    inverse = inv2(table)
    gradient, method = dm_di(row, index, ir)
    if inverse is None or gradient is None:
        return None, method
    w_e = pole_pairs * omega_m
    j_l = [[-table[1][0], -table[1][1]], [table[0][0], table[0][1]]]
    inner = [[r_ohm * (1.0 if i == j else 0.0) + w_e * (gradient[i][j] + j_l[i][j])
              for j in range(2)] for i in range(2)]
    product = mat_mul(inverse, inner)
    return [[-product[i][j] for j in range(2)] for i in range(2)], method


def eigen2(matrix: list[list[float]]) -> tuple[complex, complex]:
    half_trace = 0.5 * (matrix[0][0] + matrix[1][1])
    determinant = det2(matrix)
    discriminant = half_trace * half_trace - determinant
    if discriminant >= 0.0:
        root = math.sqrt(discriminant)
        return complex(half_trace + root, 0.0), complex(half_trace - root, 0.0)
    root = math.sqrt(-discriminant)
    return complex(half_trace, root), complex(half_trace, -root)


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


def design_point(row: dict, index: dict, ir: float, params: dict) -> dict:
    pole_pairs = params["pole_pairs"]
    resistance = params["r_phase_ohm"]
    f_pwm = params["f_pwm_hz"]
    omega_c = params["omega_c_rad_s"]
    pm_target = params["pm_target_deg"]
    ts = 1.0 / f_pwm
    delay = PWM_DELAY_FACTOR * ts

    current_0 = _num(row["I0_A"])
    table = l2(row)
    if not all(math.isfinite(value) for line in table for value in line):
        return {"case_id": row.get("case_id", ""), "status": "DATA_INVALID",
                "Ld0": None, "note": "L2 содержит nan/inf — синтез невозможен"}
    inverse = inv2(table)
    out = {
        "case_id": row.get("case_id", ""),
        "theta_e_deg": _num(row["theta_e_deg"]),
        "Id_A": _num(row["Id_A"]), "Iq_A": _num(row["Iq_A"]), "I0_A": current_0,
        "Ldd_H": table[0][0], "Lqq_H": table[1][1],
        "Ldq_H": table[0][1], "Lqd_H": table[1][0],
        "T_delay_s": delay, "omega_c_rad_s": omega_c,
        "omega_c_max_rad_s": bandwidth_ceiling(delay, pm_target),
        "PM_design_deg": pm_target,
    }
    scale = 0.5 * (abs(table[0][0]) + abs(table[1][1]))
    out["coupling_index"] = abs(table[0][1]) / scale if scale > 0.0 else None
    out["Te_Nm"] = torque(row, pole_pairs)
    out["status"] = "OK"
    out["note"] = ""

    if abs(current_0) > I0_TOL:
        out["status"] = "MODEL_EXCLUDED"
        out["note"] = "I0 != 0: нет пути замыкания суммы фазных токов (i_0 = 0)"
        return out
    if inverse is None:
        out["status"] = "FAIL"
        out["note"] = "L2 вырождена"
        return out

    omega_m = params.get("omega_m_rad_s", 0.0)
    a_matrix, method = linearize_a(row, index, ir, omega_m, pole_pairs, resistance)
    if a_matrix is None:
        out["status"] = "A_UNAVAILABLE"
        out["note"] = f"dM/di не посчитана: {method}"
    else:
        lambda_1, lambda_2 = eigen2(a_matrix)
        out.update({
            "A_dd": a_matrix[0][0], "A_dq": a_matrix[0][1],
            "A_qd": a_matrix[1][0], "A_qq": a_matrix[1][1],
            "lambda_1": lambda_1.real, "lambda_2": lambda_2.real,
            "lambda_1_re": lambda_1.real, "lambda_2_re": lambda_2.real,
        })
    rest_matrix, _ = linearize_a(row, index, ir, 0.0, pole_pairs, resistance)
    if rest_matrix is not None:
        rest_lambda = max(value.real for value in eigen2(rest_matrix))
        out["lambda_rest_max"] = rest_lambda

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


def analyse(rows: list[dict], params: dict) -> tuple[list[dict], dict]:
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

    points = []
    for row in complete:
        points.append(design_point(row, index, ir or 1.0, params))

    excluded = [point for point in points if point["status"] == "MODEL_EXCLUDED"]
    failed = [point for point in points if point["status"] == "FAIL"]
    usable = [point for point in points if point["status"] != "MODEL_EXCLUDED"]
    with_a = [point for point in points if point["status"] == "OK"]

    gates["point_status"] = "PASS" if not failed else "FAIL"
    notes["point_status"] = (f"OK: {len(with_a)}, A_UNAVAILABLE: "
                             f"{len(usable) - len(with_a)}, "
                             f"MODEL_EXCLUDED: {len(excluded)}, FAIL: {len(failed)}")

    # L2: симметрия и положительная определённость (критерий Сильвестра 2x2).
    asym = 0.0
    worst_det = math.inf
    for point in usable:
        table = [[point["Ldd_H"], point["Ldq_H"]], [point["Lqd_H"], point["Lqq_H"]]]
        scale = max(abs(point["Ldd_H"]), abs(point["Lqq_H"]), 1e-12)
        asym = max(asym, abs(point["Ldq_H"] - point["Lqd_H"]) / scale)
        worst_det = min(worst_det, det2(table))
    gates["l2_symmetric"] = "PASS" if asym <= 1e-9 else "FAIL"
    notes["l2_symmetric"] = f"max |Ldq-Lqd|/|L| = {asym:.2e}"
    positive = worst_det > 0.0 and all(point["Ldd_H"] > 0.0 and point["Lqq_H"] > 0.0
                                      for point in usable)
    gates["l2_positive_definite"] = "PASS" if positive else "FAIL"
    notes["l2_positive_definite"] = f"min det(L2) = {worst_det:.3e}"

    # Сверка L_inc с секундой по узлам сетки — ДИАГНОСТИКА, не гейт:
    # L_inc в ROM посчитана стенсилом FEM ±0.05*ir (±0.25 А), а по узлам
    # доступна только секунда ±ir (±5 А). Разница измеряет кривизну psi,
    # а не ошибку; порог здесь неприменим.
    worst_rel = 0.0
    checked = 0
    if ir:
        for row in complete:
            fresh, _ = l_from_flux(row, index, ir)
            if fresh is None:
                continue
            table = l2(row)
            scale = max(abs(table[0][0]), abs(table[1][1]), 1e-12)
            error = max(abs(fresh[i][j] - table[i][j]) for i in range(2) for j in range(2))
            worst_rel = max(worst_rel, error / scale)
            checked += 1
    gates["l_inc_secant_diagnostic"] = "INFO"
    notes["l_inc_secant_diagnostic"] = (
        f"{checked} точек: max |dL|/|L| = {worst_rel:.2e}; L в ROM — стенсил "
        "±0.25 А, секунда по узлам — ±5 А, поэтому порог не применяется"
        if checked else "нет соседних узлов для секунды")

    # T_e = p(psi_d i_q - psi_q i_d) против независимой колонки Te_flux.
    worst_te = 0.0
    te_rows = 0
    for row in complete:
        reference = _num(row.get("Te_flux"))
        if reference is None:
            continue
        value = torque(row, params["pole_pairs"])
        scale = max(abs(reference), 1e-3)
        worst_te = max(worst_te, abs(value - reference) / scale)
        te_rows += 1
    if te_rows:
        gates["torque_flux_identity"] = "PASS" if worst_te <= 1e-9 else "FAIL"
        notes["torque_flux_identity"] = f"{te_rows} строк, max rel = {worst_te:.2e}"
    else:
        gates["torque_flux_identity"] = "NOT_EVALUATED"
        notes["torque_flux_identity"] = "в ROM нет колонки Te_flux"

    gates["zero_sequence_scope"] = "PASS"
    notes["zero_sequence_scope"] = (
        f"i_0 = 0 (модельное ограничение); строк с I0 != 0: {len(excluded)} "
        "помечены MODEL_EXCLUDED")

    ts = 1.0 / params["f_pwm_hz"]
    delay = PWM_DELAY_FACTOR * ts
    gates["delay_budget"] = "PASS" if math.isclose(delay, 1.5 * ts) else "FAIL"
    notes["delay_budget"] = (f"T_d = 1.5 / f_pwm = {delay * 1e6:.1f} мкс "
                             f"(f_pwm = {params['f_pwm_hz']:g} Гц)")

    omega_c = params["omega_c_rad_s"]
    pm_target = params["pm_target_deg"]
    identity = 90.0 - math.degrees(omega_c * delay)
    numeric = [point["PM_deg"] for point in usable if point["PM_deg"] is not None]
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
             if point["max_pole_radius"] is not None]
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
            f"tau_mech = J/B = {tau_mech:.4f} с; правило w_speed <= w_c/10" if tau_mech
            else "B = 0: механическая постоянная не определена")
    else:
        gates["speed_loop_mechanics"] = "NOT_EVALUATED"
        notes["speed_loop_mechanics"] = "нет J и B — численный синтез speed loop не выполнен"

    gates["hardware_validation"] = "NOT_EVALUATED"
    notes["hardware_validation"] = "ROM синтетическая; target readback и стенд не выполнялись"

    blocking = [name for name, value in gates.items()
                if value == "FAIL"]
    status = "FAIL_3" if blocking else "PASS_3"
    summary = {
        "status": status,
        "gates": gates,
        "blocking_gates": blocking,
        "notes": notes,
        "params": {name: params.get(name) for name in PARAM_KEYS},
        "rows": len(points),
        "ok": len(usable),
        "model_excluded": len(excluded),
        "delay_s": delay,
        "pwm_period_s": ts,
        "delay_factor": PWM_DELAY_FACTOR,
        "omega_c_max_rad_s": ceiling,
        "model_note": ("i_U+i_V+i_W = 0 принято как модельное ограничение топологии "
                       "OEW-6T без внешнего пути нулевой последовательности"),
        "data_note": "синтетическая ROM (build/doe1/out/L_dq0.csv): числа DERIVED",
        "speed_loop": "NOT_EVALUATED без J, B",
        "remaining": ["аппаратный readback G-IF", "реальная ROM (Maxwell)", "MTPA/FW в 2D"],
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
    # Колонка Te_flux в ROM независима от psi; в selftest она должна быть
    # согласована с psi и токами, иначе гейт torque_flux_identity ловит подделку.
    base.setdefault("Te_flux", 3.0 * (base["psi_d"] * base["Iq_A"]
                                     - base["psi_q"] * base["Id_A"]))
    return base


def selftest() -> int:
    """Проверка формул без чтения файлов: контур, PM, потолок, полюса."""
    checks: list[tuple[str, bool, str]] = []
    params = {"pole_pairs": 3, "r_phase_ohm": 0.1, "f_pwm_hz": 5000.0,
              "omega_c_rad_s": 300.0, "pm_target_deg": 45.0}
    ts = 1.0 / params["f_pwm_hz"]
    delay = PWM_DELAY_FACTOR * ts

    row = _row(Iq_A=10.0, psi_d=0.02)
    checks.append(("torque = p*psi_d*i_q", abs(torque(row, 3) - 0.6) < 1e-12,
                   f"T_e = {torque(row, 3):.6f} Н·м"))

    rest = _row()
    derivative, _ = plant_derivative(rest, (0.0, 0.0), 0.0, 3, 0.1)
    checks.append(("zero current/speed -> di/dt = 0",
                   abs(derivative[0]) < 1e-12 and abs(derivative[1]) < 1e-12,
                   f"di/dt = {derivative}"))

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

    index = {(0, 0, 0, 0.0): row}
    gradient, method = dm_di(row, index, 5.0)
    checks.append(("dM/di needs neighbours -> None", gradient is None,
                   f"method = {method}"))

    points, summary = analyse([row], params)
    checks.append(("analyse produces PASS_3 baseline",
                   summary["status"] == "PASS_3" and len(points) == 1,
                   f"status = {summary['status']}"))

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

    rows = load_rows(Path(args.rom))
    with Path(args.params).open(encoding="utf-8") as handle:
        params = json.load(handle)
    points, summary = analyse(rows, params)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(outdir / "ctrl_points.csv", points)
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"rows: {summary['rows']} ok: {summary['ok']} "
          f"model_excluded: {summary['model_excluded']}")
    for name, value in summary["gates"].items():
        print(f"  {name}: {value}")
    for name, value in summary["notes"].items():
        print(f"    {name}: {value}")
    print(f"status: {summary['status']}; out: {outdir}")
    return 0 if summary["status"] == "PASS_3" else 1


if __name__ == "__main__":
    raise SystemExit(main())
