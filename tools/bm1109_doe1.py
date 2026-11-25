#!/usr/bin/env python3
"""BM1109 DOE: воспроизводимая процедура идентификации магнитной модели (dq0).

Зачем: получить из FEM не отдельные таблицы T_e, psi и L_dq0, а ОДИН набор
расчётов (I_U, I_V, I_W, theta) -> (psi_U, psi_V, psi_W, T_e), из которого
программно получаются psi_dq0, L_dq0, угловые производные и проверки качества.

Соглашения (менять нельзя по отдельности — они связаны):

* Преобразование Кларк/Парк ОРТОНОРМАЛЬНОЕ (power-invariant):
      i_a = sqrt(2/3)(iU - iV/2 - iW/2)
      i_b = (iV - iW)/sqrt(2)
      i_0 = (iU + iV + iW)/sqrt(3)
      i_d =  i_a cos + i_b sin,  i_q = -i_a sin + i_b cos
  При нём iU^2+iV^2+iW^2 = i_a^2+i_b^2+i_0^2, поэтому сфера
  x_d^2+x_q^2+x_0^2 <= 1 действительно означает равный RMS-токовый ресурс,
  а момент в dq равен T = p(psi_d i_q - psi_q i_d) БЕЗ множителя 3/2.
  Смесь амплитудно-инвариантного Кларка (2/3) с i_0 = sum/sqrt(3) ломает и
  сферу, и момент — так делать нельзя.

* DOE_1A: сетка {-1,-0.5,0,0.5,1}^3 внутри сферы даёт 33 точки
  (центр 1 + оси 12 + две координаты 12 + три координаты 8), а НЕ 81.
  33*12 = 396 базовых case, а не 972. Группы A..H — см. group_of().

* Угловая сетка задаётся ПЛАНОМ (AnglePlan): число точек, механический
  диапазон и число пар полюсов. Разрешимая гармоника считается как
  N/2 - 1 по точкам на магнитный период; если план её не разрешает,
  grid отказывается работать, а не выдаёт «спектр» из алиасов.
  Полный оборот при p = 3 и 12 точках — это 4 отсчёта на электрический
  период, то есть мусор: для 6-й гармоники нужно >= 42 точек на оборот.

* L = dpsi/di берётся разностями по току в самой точке, а не по соседям
  грубой сетки 0.5*I_r. Порядок: центральная разность, а если сосед
  выходит за Omega_FEM — односторонняя второго порядка
  (-3f0 + 4f1 - f2)/(2h). Факт подмены схемы пишется в notes строки.

* Omega_op (|x| <= 1) и Omega_FEM (по умолчанию |x| <= 1.1) — разные
  области. Узлы вне Omega_op помечаются outside_app_sphere и в ROM как
  рабочие точки не попадают.

* Абсолютные psi из FEM имеют произвольную аддитивную постоянную
  (калибровка обмотки). L считается по РАЗНОСТЯМ psi при одном theta,
  поэтому постоянная сокращается. T_flux, psi_dq0 и W' калибровочно
  НЕ инвариантны — это указывается в summary.

* Угловая производная dpsi/dtheta по сетке НЕ считается конечной разностью:
  при 30° эл. она обнуляет 6-ю гармонику, при 15° занижает её в 1.57 раза.
  По умолчанию — спектральное дифференцирование по ряду Фурье.

* Момент проверяется двумя путями: T_fem (Maxwell) и T_energy = dW'/dtheta_m
  при ФИКСИРОВАННЫХ I_U,I_V,I_W. Второй требует отдельных case (kind=torque)
  с поворотом theta при том же физическом токе: на базовой dq-сетке при
  фиксированных i_dq0 величина W' от theta не зависит, и наивная разность
  по углу даёт нулевой момент.

* Консервативность проверяется замыканием контура в токовом пространстве
  при фиксированном theta: ∮psi·di = 0 (kind=loop). Ненулевой интеграл —
  признак несогласованной ROM, а не «шума одного элемента L».

Подкоманды:

  grid     -> CSV точек (base / stencil / torque / loop) + manifest.json
  process  -> L_dq0.csv + summary.json с гейтами качества
  selftest -> прогон на аналитической модели (без FEM)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ── константы сетки ──────────────────────────────────────────────────────

GRID_VALUES = (-1.0, -0.5, 0.0, 0.5, 1.0)
APP_RADIUS = 1.0          # Omega_op: область применения ROM
FEM_RADIUS = 1.1          # Omega_FEM: где разрешены узлы производных
DEFAULT_DELTAS = (0.05, 0.025)
DEFAULT_ANGLE_STEP = 30.0
TORQUE_STATES = ((0.5, 0.5, 0.0), (0.0, 1.0, 0.0), (0.4, 0.0, 0.5))
TORQUE_THETA_DEG = 15.0
TORQUE_H_DEG = 5.0
LOOP_PATHS = (((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (0.5, 0.5, 0.0), (0.0, 0.0, 0.0)),
              ((0.0, 0.0, 0.0), (0.0, 0.5, 0.5), (0.5, 0.5, 0.5), (0.0, 0.0, 0.0)))
LOOP_STEPS = 4
LOOP_ANGLES = (0.0, 90.0)

CASES_HEADER = [
    "case_id", "kind", "group", "seq", "xd", "xq", "x0",
    "Id_A", "Iq_A", "I0_A", "IU_A", "IV_A", "IW_A",
    "theta_e_deg", "theta_m_deg", "delta_x", "axis", "sign",
    "method", "step_mult", "norm_x", "outside_app_sphere",
]

L_HEADER = [
    "case_id", "group", "xd", "xq", "x0", "theta_e_deg", "Id_A", "Iq_A", "I0_A",
    "Ldd", "Ldq", "Ld0", "Lqd", "Lqq", "Lq0", "L0d", "L0q", "L00",
    "eps_L", "eps_dq", "eps_d0", "eps_q0", "sym_rel",
    "psi_d", "psi_q", "psi_0",
    "dpsi_d_dtheta_e", "dpsi_q_dtheta_e", "dpsi_0_dtheta_e",
    "Te_fem", "Te_flux", "dTe_di0", "S0n", "dTe_di0_rel",
    "psi_d_W", "psi_q_W", "psi_0_W", "eps_psi", "angular_res",
    "scenario", "notes", "harmonics_resolved",
    "stencil_d", "stencil_q", "stencil_0",
]

DOE2_HEADER = [
    "case_id", "theta_m_deg", "omega_m_rad_s", "Vdc_V",
    "Id_A", "Iq_A", "I0_A", "IU_A", "IV_A", "IW_A",
    "uU_required_V", "uV_required_V", "uW_required_V",
    "duty_U1", "duty_V1", "duty_W1", "duty_U2", "duty_V2", "duty_W2",
    "voltage_margin", "current_margin", "pwm_feasible", "note",
]

SQ23 = math.sqrt(2.0 / 3.0)
SQ13 = 1.0 / math.sqrt(3.0)
SQ12 = 1.0 / math.sqrt(2.0)


# ── преобразования ───────────────────────────────────────────────────────

def clarke(iu: float, iv: float, iw: float) -> tuple[float, float, float]:
    """UVW -> alpha,beta,0 (ортонормальное, норма сохраняется)."""
    return (SQ23 * (iu - 0.5 * iv - 0.5 * iw),
            SQ12 * (iv - iw),
            SQ13 * (iu + iv + iw))


def inv_clarke(ia: float, ib: float, iz: float) -> tuple[float, float, float]:
    return (SQ23 * ia + SQ13 * iz,
            -0.5 * SQ23 * ia + SQ12 * ib + SQ13 * iz,
            -0.5 * SQ23 * ia - SQ12 * ib + SQ13 * iz)


def park(ia: float, ib: float, theta: float) -> tuple[float, float]:
    c, s = math.cos(theta), math.sin(theta)
    return (ia * c + ib * s, -ia * s + ib * c)


def inv_park(id_: float, iq: float, theta: float) -> tuple[float, float]:
    c, s = math.cos(theta), math.sin(theta)
    return (id_ * c - iq * s, id_ * s + iq * c)


def uvw_to_dq0(iu: float, iv: float, iw: float, theta: float) -> tuple[float, float, float]:
    ia, ib, iz = clarke(iu, iv, iw)
    id_, iq = park(ia, ib, theta)
    return (id_, iq, iz)


def dq0_to_uvw(id_: float, iq: float, iz: float, theta: float) -> tuple[float, float, float]:
    ia, ib = inv_park(id_, iq, theta)
    return inv_clarke(ia, ib, iz)


def base_points(radius: float = APP_RADIUS) -> list[tuple[float, float, float]]:
    """Точки сетки {-1,-0.5,0,0.5,1}^3 внутри сферы. Их 33, не 81."""
    points = [(a, b, c)
              for a in GRID_VALUES
              for b in GRID_VALUES
              for c in GRID_VALUES
              if a * a + b * b + c * c <= radius + 1e-12]
    points.sort(key=lambda p: (round(p[0] ** 2 + p[1] ** 2 + p[2] ** 2, 12),
                               p[0], p[1], p[2]))
    return points


def group_of(xd: float, xq: float, x0: float) -> str:
    """Группа A..H по числу и составу ненулевых координат."""
    nonzero = [name for name, value in (("d", xd), ("q", xq), ("0", x0)) if value != 0.0]
    if not nonzero:
        return "A"
    if len(nonzero) == 1:
        return {"d": "B", "q": "C", "0": "D"}[nonzero[0]]
    if len(nonzero) == 2:
        pair = set(nonzero)
        if pair == {"d", "q"}:
            return "E"
        return "F" if pair == {"d", "0"} else "G"
    return "H"


# ── угловой план ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AnglePlan:
    """Сетка по углу: points отсчётов на span_mech_deg механических градусов.

    Разрешимость по Найквисту — жёсткий гейт (issues), запас margin —
    только предупреждение (warnings): математика и практическая точность
    восстановления амплитуды/фазы — разные требования.
    """

    points: int
    span_mech_deg: float
    pole_pairs: float
    max_harmonic: int
    margin: float = 2.0

    @property
    def magnetic_period_mech_deg(self) -> float:
        return 360.0 / self.pole_pairs

    @property
    def samples_per_period(self) -> float:
        return self.points * self.magnetic_period_mech_deg / self.span_mech_deg

    @property
    def harmonics_resolved(self) -> int:
        return max(int(round(self.samples_per_period)) // 2 - 1, 0)

    @property
    def span_e_deg(self) -> float:
        return self.pole_pairs * self.span_mech_deg

    def angles_e_deg(self) -> list[float]:
        return [self.span_e_deg * index / self.points for index in range(self.points)]

    def required_points(self, harmonic: int | None = None) -> int:
        """Нижняя граница по Найквисту: N >= 2*(n+1) отсчётов на период."""
        harmonic = self.max_harmonic if harmonic is None else harmonic
        needed = 2.0 * (harmonic + 1) * self.span_mech_deg / self.magnetic_period_mech_deg
        return int(math.ceil(needed - 1e-9))

    @property
    def recommended_points(self) -> int:
        """Запас margin по числу точек: 2x от Найквиста для 6-й гармоники = 24."""
        return int(math.ceil(self.margin * self.required_points()))

    def warnings(self) -> list[str]:
        if self.points >= self.recommended_points:
            return []
        return [f"{self.points} точек достаточно для различения гармоник, но "
                f"меньше рекомендуемых {self.recommended_points} "
                f"(запас x{self.margin:g}): амплитуда и фаза на шумном FEM "
                f"восстанавливаются хуже"]

    def issues(self) -> list[str]:
        problems: list[str] = []
        samples = self.samples_per_period
        if abs(samples - round(samples)) > 1e-6:
            problems.append(
                f"span {self.span_mech_deg:g}° не кратен магнитному периоду "
                f"{self.magnetic_period_mech_deg:g}°: замыкание сетки разорвано")
        if samples < 2 * self.max_harmonic + 2:
            problems.append(
                f"{self.points} точек дают {samples:g} отсчётов на магнитный период "
                f"(разрешимо до {self.harmonics_resolved}-й гармоники), "
                f"а запрошена {self.max_harmonic}-я: минимум "
                f"{self.required_points()} точек, с запасом "
                f"{2 * self.required_points()} точек")
        return problems


def electrical_period_plan(pole_pairs: float,
                           points: int = 12,
                           max_harmonic: int | None = None,
                           margin: float = 2.0) -> AnglePlan:
    return AnglePlan(points, 360.0 / pole_pairs, pole_pairs,
                     points // 2 - 1 if max_harmonic is None else max_harmonic, margin)


def full_revolution_plan(pole_pairs: float, points: int = 12,
                         max_harmonic: int | None = None,
                         margin: float = 2.0) -> AnglePlan:
    """Диагностический полный оборот: период повторяемости не предполагается."""
    return AnglePlan(points, 360.0, pole_pairs,
                     max(points // 2 - 1, 1) if max_harmonic is None else max_harmonic,
                     margin)


# ── точки ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str                     # base | stencil | torque | loop
    group: str
    xd: float
    xq: float
    x0: float
    theta_e_deg: float
    pole_pairs: float
    delta: float = 0.0
    axis: str = ""
    sign: int = 0
    method: str = ""              # central | fwd2 | bwd2 (для stencil)
    step_mult: int = 1            # во сколько delta стоит узел
    seq: int = 0                  # порядок точки внутри контура

    @property
    def theta_m_deg(self) -> float:
        return self.theta_e_deg / self.pole_pairs

    @property
    def norm2(self) -> float:
        return self.xd ** 2 + self.xq ** 2 + self.x0 ** 2

    def currents(self, ir: float) -> tuple[float, float, float]:
        return dq0_to_uvw(self.xd * ir, self.xq * ir, self.x0 * ir,
                          math.radians(self.theta_e_deg))

    def row(self, ir: float) -> dict:
        iu, iv, iw = self.currents(ir)
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "group": self.group,
            "seq": self.seq,
            "xd": f"{self.xd:.9g}",
            "xq": f"{self.xq:.9g}",
            "x0": f"{self.x0:.9g}",
            "Id_A": f"{self.xd * ir:.12g}",
            "Iq_A": f"{self.xq * ir:.12g}",
            "I0_A": f"{self.x0 * ir:.12g}",
            "IU_A": f"{iu:.12g}",
            "IV_A": f"{iv:.12g}",
            "IW_A": f"{iw:.12g}",
            "theta_e_deg": f"{self.theta_e_deg:.9g}",
            "theta_m_deg": f"{self.theta_m_deg:.9g}",
            "delta_x": f"{self.delta:.9g}",
            "axis": self.axis,
            "sign": self.sign,
            "method": self.method,
            "step_mult": self.step_mult,
            "norm_x": f"{math.sqrt(self.norm2):.9g}",
            "outside_app_sphere": int(self.norm2 > APP_RADIUS + 1e-12),
        }


def select_stencil_points(points, mode: str):
    if mode == "none":
        return []
    if mode == "axes":
        return [p for p in points if sum(1 for c in p if c != 0.0) <= 1]
    if mode == "groups":
        # первая derivative-выборка: центр + все точки групп E,F,G,H
        return [p for p in points if group_of(*p) in ("A", "E", "F", "G", "H")]
    return list(points)


def build_cases(ir: float, pole_pairs: float,
                angle_step_deg: float = DEFAULT_ANGLE_STEP,
                deltas=DEFAULT_DELTAS,
                stencil: str = "axes",
                fem_radius: float = FEM_RADIUS,
                with_torque: bool = True,
                with_loops: bool = True,
                plan: AnglePlan | None = None,
                loop_steps: int = LOOP_STEPS) -> list[Case]:
    if plan is None:
        points_per_period = int(round(360.0 / angle_step_deg))
        plan = electrical_period_plan(pole_pairs, points_per_period)
    angles = plan.angles_e_deg()
    points = base_points()
    cases: list[Case] = []

    for index, (xd, xq, x0) in enumerate(points):
        for theta in angles:
            cases.append(Case(f"B{index:03d}_T{int(round(theta)):03d}", "base",
                              group_of(xd, xq, x0), xd, xq, x0, theta, pole_pairs))

    selected = select_stencil_points(points, stencil)
    for index, (xd, xq, x0) in enumerate(points):
        if (xd, xq, x0) not in selected:
            continue
        for axis, slot in (("d", 0), ("q", 1), ("0", 2)):
            for delta in deltas:
                def inside(offset: float) -> bool:
                    coords = [xd, xq, x0]
                    coords[slot] += offset
                    return sum(value * value for value in coords) <= fem_radius ** 2 + 1e-12

                def add(offset: float, method: str, mult: int, sign: int) -> None:
                    coords = [xd, xq, x0]
                    coords[slot] += offset
                    for theta in angles:
                        cases.append(Case(
                            f"S{index:03d}_{axis}{'p' if sign > 0 else 'm'}_M{mult}"
                            f"_D{int(round(delta * 1000)):03d}_T{int(round(theta)):03d}",
                            "stencil", "", coords[0], coords[1], coords[2],
                            theta, pole_pairs, delta=delta, axis=axis, sign=sign,
                            method=method, step_mult=mult))

                if inside(delta) and inside(-delta):
                    add(+delta, "central", 1, +1)
                    add(-delta, "central", 1, -1)
                elif inside(delta) and inside(2.0 * delta):
                    add(+delta, "fwd2", 1, +1)
                    add(2.0 * delta, "fwd2", 2, +1)
                elif inside(-delta) and inside(-2.0 * delta):
                    add(-delta, "bwd2", 1, -1)
                    add(-2.0 * delta, "bwd2", 2, -1)

    if with_torque:
        for group_index, state in enumerate(TORQUE_STATES, start=1):
            theta0 = TORQUE_THETA_DEG
            base = tuple(value * ir for value in state)
            uvw = dq0_to_uvw(base[0], base[1], base[2], math.radians(theta0))
            for offset in (-TORQUE_H_DEG, 0.0, TORQUE_H_DEG):
                theta = theta0 + offset
                id_, iq, i0 = uvw_to_dq0(uvw[0], uvw[1], uvw[2], math.radians(theta))
                cases.append(Case(
                    f"TQ{group_index}_{'m' if offset < 0 else 'p' if offset > 0 else '0'}",
                    "torque", f"TQ{group_index}", id_ / ir, iq / ir, i0 / ir,
                    theta, pole_pairs))

    if with_loops:
        for loop_index, path in enumerate(LOOP_PATHS, start=1):
            for theta in LOOP_ANGLES:
                seq = 0
                for start, end in zip(path, path[1:]):
                    samples = 0 if seq == 0 else 1   # первая вершина ребра уже добавлена
                    for step in range(samples, loop_steps + 1):
                        fraction = step / loop_steps
                        coords = [start[i] + fraction * (end[i] - start[i])
                                  for i in range(3)]
                        cases.append(Case(
                            f"LP{loop_index}_T{int(round(theta)):03d}_{seq:02d}",
                            "loop", f"LP{loop_index}", coords[0], coords[1], coords[2],
                            theta, pole_pairs, seq=seq))
                        seq += 1
    return cases


def case_counts(cases: list[Case]) -> dict:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.kind] = counts.get(case.kind, 0) + 1
    return counts


def group_counts(cases: list[Case]) -> dict:
    counts: dict[str, int] = {}
    for case in cases:
        if case.kind == "base":
            counts[case.group] = counts.get(case.group, 0) + 1
    return dict(sorted(counts.items()))


def write_cases_csv(path: Path, cases: list[Case], ir: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CASES_HEADER)
        writer.writeheader()
        for case in cases:
            writer.writerow(case.row(ir))


# ── спектральное дифференцирование по углу ───────────────────────────────

def fourier_derivative(values: list[float], theta0_deg: float,
                       step_deg: float, *, start_deg: float) -> tuple[float, int]:
    """dpsi/dtheta_e (на эл. радиан) через МНК-ряд Фурье.

    values[k] — отсчёт при theta_e = start_deg + k*step_deg. Ряд периодичен по
    фактическому охвату span = N*step_deg, который обязан быть кратен 360° эл.:
    один эл. период (span = 360°) или полный оборот (span = p*360°). Гармоника
    индекса h соответствует эл. порядку nu = h/n_rev, n_rev = span/360°.
    Незамкнутый охват (span не кратен 360° эл.) — ValueError, а не оценка.

    Возвращает (производная в theta0, наибольшая целая эл. гармоника).
    Число различимых гармоник индекса = N/2 - 1: при 12 точках на эл. период
    это 5, поэтому 6-я гармоника в 12-точечной сетке не видна вообще.
    """
    count = len(values)
    if count < 4:
        raise ValueError("нужно не менее 4 угловых точек")
    if not step_deg > 0.0:
        raise ValueError("шаг по углу должен быть положительным")
    cycles = count * step_deg / 360.0
    n_rev = int(round(cycles))
    if n_rev < 1 or abs(cycles - n_rev) > 1e-6:
        raise ValueError(f"охват {count * step_deg:g}° эл. не кратен 360° эл.: "
                         f"ряд Фурье на незамкнутой сетке не определён")
    max_index = count // 2 - 1
    theta = math.radians(theta0_deg - start_deg)
    total = 0.0
    for harmonic in range(1, max_index + 1):
        cos_part = 0.0
        sin_part = 0.0
        for index, value in enumerate(values):
            phase = 2.0 * math.pi * harmonic * index / count
            cos_part += value * math.cos(phase)
            sin_part += value * math.sin(phase)
        cos_part *= 2.0 / count
        sin_part *= 2.0 / count
        order = harmonic / n_rev
        total += order * (-cos_part * math.sin(order * theta)
                          + sin_part * math.cos(order * theta))
    return total, max_index // n_rev


# ── метрики матрицы ──────────────────────────────────────────────────────

MATRIX_KEYS = ("dd", "dq", "d0", "qd", "qq", "q0", "0d", "0q", "00")


def fourier_fit_residual(values: list[float], scale: float = 1.0) -> float:
    """Достаточность гармонической модели: остаток ряда, урезанного по гармоникам.

    Обычный МНК-ряд с N/2-1 гармониками почти интерполирует выборку и даёт
    остаток ~0 по построению. Поэтому ряд урезается до N/2-2: если в сигнале
    есть гармоника выше, остаток это покажет. Остаток НЕ ловит гармонику,
    точно совпадающую с Найквистом (она неотличима от постоянной) — для этого
    есть гейт разрешимости плана.
    """
    count = len(values)
    if count < 8:
        return float("nan")
    keep = max(1, count // 2 - 2)
    mean = sum(values) / count
    predicted = [mean] * count
    for harmonic in range(1, keep + 1):
        cos_part = 2.0 / count * sum(
            values[index] * math.cos(2.0 * math.pi * harmonic * index / count)
            for index in range(count))
        sin_part = 2.0 / count * sum(
            values[index] * math.sin(2.0 * math.pi * harmonic * index / count)
            for index in range(count))
        for index in range(count):
            phase = 2.0 * math.pi * harmonic * index / count
            predicted[index] += cos_part * math.cos(phase) + sin_part * math.sin(phase)
    error = sum((predicted[index] - values[index]) ** 2 for index in range(count))
    spread = sum((values[index] - mean) ** 2 for index in range(count))
    if spread <= 0.0:
        return 0.0
    # Ряда без углового сигнала: постоянная модель объясняет его точно, остаток = 0.
    # Гармонику ровно на Найквисте такой остаток не видит — её ловит гейт плана.
    # scale — масштаб самой величины (|psi|), а не ряда: иначе нулевая
    # компонента с шумом 1e-19 «объявляет себя сигналом» и даёт остаток O(1)
    if math.sqrt(spread / count) <= 1e-9 * max(abs(scale), 1e-30):
        return 0.0
    return math.sqrt(error / spread)


def symmetry_metrics(matrix: dict[str, float], floor: float) -> dict:
    def rel(a: str, b: str) -> float:
        first, second = matrix.get(a), matrix.get(b)
        if first is None or second is None:
            return float("nan")
        scale = max(abs(first), abs(second), floor)
        return abs(first - second) / scale

    diag = max(abs(matrix.get("dd", 0.0)), abs(matrix.get("qq", 0.0)),
               abs(matrix.get("00", 0.0)), floor)
    asym = 0.0
    sym = 0.0
    for first, second in (("dd", "dd"), ("dq", "qd"), ("d0", "0d"),
                          ("qq", "qq"), ("q0", "0q"), ("00", "00")):
        left, right = matrix.get(first, 0.0), matrix.get(second, 0.0)
        asym += (left - right) ** 2
        sym += left * left + right * right
    return {
        "eps_dq": rel("dq", "qd"),
        "eps_d0": rel("d0", "0d"),
        "eps_q0": rel("q0", "0q"),
        "sym_rel": math.sqrt(asym / sym) if sym > 0.0 else 0.0,
        "scale": diag,
    }


def convergence_metric(first: dict[str, float], second: dict[str, float],
                       floor: float) -> float:
    worst = 0.0
    for key in MATRIX_KEYS:
        if key not in first or key not in second:
            continue
        scale = max(abs(first[key]), floor)
        worst = max(worst, abs(second[key] - first[key]) / scale)
    return worst


# ── обработка результатов FEM ────────────────────────────────────────────

def _number(row: dict, name: str) -> float | None:
    value = row.get(name)
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    # missing-value convention: nan/inf — не валидное число, а отсутствие;
    # конечность проверяется здесь, а не только None (TZ_PHASE4 §11.3)
    return parsed if math.isfinite(parsed) else None


def _key(case: Case) -> tuple:
    return (round(case.xd, 6), round(case.xq, 6), round(case.x0, 6),
            round(case.theta_e_deg, 6))


def _flux(row: dict, theta_e_deg: float) -> tuple[float, float, float] | None:
    iu = _number(row, "psi_U")
    iv = _number(row, "psi_V")
    iw = _number(row, "psi_W")
    if iu is None or iv is None or iw is None:
        return None
    return uvw_to_dq0(iu, iv, iw, math.radians(theta_e_deg))


def _case_at(cases_by_key: dict, center: Case, slot: int, offset: float) -> Case | None:
    coords = [center.xd, center.xq, center.x0]
    coords[slot] += offset
    return cases_by_key.get((round(coords[0], 6), round(coords[1], 6),
                             round(coords[2], 6), round(center.theta_e_deg, 6)))


def _axis_derivative(sample, center_value: tuple, delta: float,
                     ir: float) -> tuple[tuple | None, str | None]:
    """Производная по одной оси: центральная, иначе односторонняя 2-го порядка.

    sample(offset) -> tuple значений или None, если узла нет. center_value —
    значение в самой точке (нужно обеим односторонним схемам).
    """
    plus, minus = sample(delta), sample(-delta)
    if plus is not None and minus is not None:
        return tuple((p - m) / (2.0 * delta * ir)
                     for p, m in zip(plus, minus)), "central"
    second_plus, second_minus = sample(2.0 * delta), sample(-2.0 * delta)
    if plus is not None and second_plus is not None:
        return tuple((-3.0 * c + 4.0 * p - q) / (2.0 * delta * ir)
                     for c, p, q in zip(center_value, plus, second_plus)), "fwd2"
    if minus is not None and second_minus is not None:
        return tuple((3.0 * c - 4.0 * m + q) / (2.0 * delta * ir)
                     for c, m, q in zip(center_value, minus, second_minus)), "bwd2"
    return None, None


def l_matrix(cases_by_key: dict, results: dict[str, dict], center: Case,
             delta: float, ir: float,
             center_flux: tuple[float, float, float],
             axis_methods: dict[str, str] | None = None
             ) -> tuple[dict[str, float] | None, set[str]]:
    """L[a][b] = dpsi_a/di_b. Центральная разность, на границе Omega_FEM — односторонняя.

    axis_methods (если передан) получает схему по каждой оси: {"d": "central", ...}.
    """
    matrix: dict[str, float] = {}
    methods: set[str] = set()

    for axis, slot, names in (("d", 0, ("dd", "qd", "0d")),
                              ("q", 1, ("dq", "qq", "0q")),
                              ("0", 2, ("d0", "q0", "00"))):
        def sample(offset: float, slot: int = slot):
            case = _case_at(cases_by_key, center, slot, offset)
            return _flux(results.get(case.case_id, {}), center.theta_e_deg) if case else None

        derivative, method = _axis_derivative(sample, center_flux, delta, ir)
        if derivative is None:
            return None, methods
        methods.add(method)
        if axis_methods is not None:
            axis_methods[axis] = method
        for component, name in enumerate(names):
            matrix[name] = derivative[component]
    return matrix, methods


def energy_gradient(cases_by_key: dict, results: dict[str, dict], center: Case,
                    delta: float, ir: float, center_energy: float
                    ) -> tuple[tuple[float, float, float] | None, set[str]]:
    """dW'/di по той же схеме, что и psi: проверка psi = dW'/dI на данных FEM."""
    gradient: list[float] = []
    methods: set[str] = set()

    for slot in (0, 1, 2):
        def sample(offset: float, slot: int = slot):
            case = _case_at(cases_by_key, center, slot, offset)
            if case is None:
                return None
            value = _number(results.get(case.case_id, {}), "W_prime")
            return None if value is None else (value,)

        derivative, method = _axis_derivative(sample, (center_energy,), delta, ir)
        if derivative is None:
            return None, methods
        gradient.append(derivative[0])
        methods.add(method)
    return (gradient[0], gradient[1], gradient[2]), methods


def loop_closure(cases: list[Case], results: dict[str, dict],
                 ir: float, steps: int = LOOP_STEPS) -> list[dict]:
    """∮psi·di по замкнутому контуру при фиксированном theta (должно быть 0)."""
    groups: dict[tuple[str, float], list[Case]] = {}
    for case in cases:
        if case.kind == "loop":
            groups.setdefault((case.group, case.theta_e_deg), []).append(case)
    loops: list[dict] = []
    for (group, theta), members in sorted(groups.items()):
        members.sort(key=lambda case: case.seq)
        if len(members) < 4:
            continue
        flux = [_flux(results.get(case.case_id, {}), theta) for case in members]
        if any(item is None for item in flux):
            loops.append({"loop": group, "theta_e_deg": theta, "status": "incomplete",
                          "W_loop_mJ": None, "rel": None})
            continue
        total = 0.0
        variation = 0.0
        for index in range(len(members) - 1):
            first, second = members[index], members[index + 1]
            step = ((second.xd - first.xd) * ir, (second.xq - first.xq) * ir,
                    (second.x0 - first.x0) * ir)
            mid = [(a + b) / 2.0 for a, b in zip(flux[index], flux[index + 1])]
            total += sum(m * s for m, s in zip(mid, step))
            variation += sum(abs(m) * abs(s) for m, s in zip(mid, step))
        loops.append({
            "loop": group, "theta_e_deg": theta, "status": "ok",
            "W_loop_mJ": total * 1e3,
            "W_variation_mJ": variation * 1e3,
            "rel": abs(total) / variation if variation > 0.0 else None,
            "steps_per_edge": steps,
        })
    return loops


def _status(value: float | None, threshold: float) -> str:
    if value is None or math.isnan(value):
        return "NOT_RUN"
    return "PASS" if value <= threshold else "FAIL"


CROSS_ORACLE_EPS_REL = 1e-6   # ε: |oracle| ниже — сравнение абсолютное
ORACLE_HARMONIC = 6           # гармоника кросс-терма c6·i_q·cos 6θ


def _harmonics_gate(harmonics: int, required: int = ORACLE_HARMONIC) -> dict:
    """Разрешимость углового плана (N/2-1 >= 6), а не физика."""
    return {"status": "PASS" if harmonics >= required else "NOT_VERIFIED",
            "harmonics_resolved": harmonics, "required": required}


def _cross_symmetry_gate(cross: dict, threshold: float) -> dict:
    """Гейт ∂_θL ≈ ∂_iM; оба нуля — не FAIL, а отсутствие кросс-терма."""
    records = cross.get("records") or []
    if not records:
        return {"status": "NOT_EVALUATED", "note": cross.get("note")}
    magnitude = max(max(item["norm_L_theta"], item["norm_M_i"]) for item in records)
    if magnitude <= 1e-12:
        return {"status": "PASS", "worst_rel": 0.0, "abs_max": 0.0,
                "note": "обе ветви ~0: кросс-терм в модели отсутствует"}
    # Относительный критерий — только там, где величина значима: на узлах,
    # где обе ветви ~1e-17, отношение измеряет шум, а не ошибку.
    floor = 1e-6 * magnitude
    significant = [item for item in records
                   if max(item["norm_L_theta"], item["norm_M_i"]) > floor]
    if not significant:
        return {"status": "PASS", "worst_rel": 0.0,
                "abs_max": cross["abs_max"], "rms": cross["rms"],
                "nodes": cross["nodes"],
                "note": "все узлы ниже 1e-6 от масштаба: обе ветви ~0"}
    worst_item = max(significant, key=lambda item: item["rel"])
    worst = worst_item["rel"]
    return {"status": "PASS" if worst <= threshold else "FAIL",
            "worst_rel": worst, "worst_case": worst_item["case_id"],
            "abs_max": cross["abs_max"], "rms": cross["rms"],
            "nodes": cross["nodes"], "significant_nodes": len(significant)}


def _cross_oracle_gate(cross: dict, c6: float | None, count_angles: int) -> dict:
    """Сверка с аналитическим oracle -6·c6·sin 6θ (только если он вообще разрешим)."""
    records = cross.get("records") or []
    harmonics = max(count_angles // 2 - 1, 0)
    if not c6:
        return {"status": "NOT_VERIFIED", "harmonics_resolved": harmonics,
                "note": "c6 = 0: oracle тождественно 0, проверять нечего"}
    if harmonics < ORACLE_HARMONIC:
        peak = cross.get("oracle_max") or 0.0
        ratio = (max(abs(item["measured_Mq_iq"]) for item in records) / peak
                 if records and peak > 0.0 else None)
        return {"status": "NOT_VERIFIED", "harmonics_resolved": harmonics,
                "alias_ratio": ratio,
                "note": "6-я гармоника не разрешена (N/2-1 < 6): предел схемы, не FAIL"}
    if not records:
        return {"status": "NOT_EVALUATED", "harmonics_resolved": harmonics}
    peak = cross["oracle_max"]
    # Семантика
    #   |oracle| < eps  ⇒ абсолютный допуск eps,
    # а не относительная ошибка около нуля: на S2 половина узлов имеет
    # sin 6θ == 0, и деление на ~0 давало бы шум вместо ошибки.
    eps = CROSS_ORACLE_EPS_REL * peak
    worst = 0.0
    relative_nodes = 0
    absolute_nodes = 0
    for item in records:
        oracle = item["oracle_Mq_iq"]
        measured_item = item["measured_Mq_iq"]
        if abs(oracle) < eps:
            absolute_nodes += 1
            error = abs(measured_item - oracle) / eps if eps > 0.0 else 0.0
        else:
            relative_nodes += 1
            error = abs(measured_item - oracle) / abs(oracle)
        worst = max(worst, error)
    return {"status": "PASS" if worst <= 0.02 else "FAIL",
            "worst_rel": worst, "harmonics_resolved": harmonics,
            "nodes": len(records), "relative_nodes": relative_nodes,
            "absolute_nodes": absolute_nodes, "eps_abs": eps,
            "abs_max": cross["abs_max"], "rms": cross["rms"]}


def _cross_derivative_metrics(rows: list[dict], ir: float, angles: list[float],
                             step_deg: float, c6: float | None = None
                             ) -> dict:
    """∂_θL против ∂_iM и oracle для synthetic-члена c6·i_q·cos 6θ.

    Два независимых маршрута к одной величине:
      ψ → ∂_iψ = L → ∂_θL   и   ψ → ∂_θψ = M → ∂_iM.
    Для гладкой магнитной модели они совпадают (∂_θL = ∂_iM).

    Сравнение — по подматрице dq при i_0 = 0 (model constraint топологии OEW);
    ось 0 не входит. Метрики трёх уровней: relative, absolute, rms —
    relative на величине ~1e-18 бессмысленна, это уже проверено.
    """
    index: dict[tuple, dict] = {}
    for row in rows:
        if math.isnan(row.get("Ldd", float("nan"))):
            continue
        key = (round(row["xd"], 6), round(row["xq"], 6), round(row["x0"], 6),
               round(row["theta_e_deg"], 6))
        index[key] = row
    components = ("dd", "dq", "qd", "qq")
    records: list[dict] = []
    oracle_max = 0.0
    for key, row in index.items():
        xd, xq, x0, theta = key
        if abs(x0) > 1e-12:
            continue
        d_l: dict[str, float] = {}
        complete = True
        for name in components:
            series = []
            for angle in angles:
                neighbour = index.get((xd, xq, x0, round(angle, 6)))
                if neighbour is None:
                    complete = False
                    break
                series.append(neighbour["L" + name])
            if not complete or len(series) < 4:
                complete = False
                break
            try:
                d_l[name], _ = fourier_derivative(series, theta, step_deg,
                                                  start_deg=angles[0])
            except ValueError:
                complete = False
                break
        if not complete:
            continue

        def slope(dxd: float, dxq: float, component: int) -> float | None:
            """∂M_component/∂i по центральной разности соседних узлов (±0.5 сетки)."""
            plus = index.get((round(xd + dxd, 6), round(xq + dxq, 6), x0, theta))
            minus = index.get((round(xd - dxd, 6), round(xq - dxq, 6), x0, theta))
            if plus is None or minus is None:
                return None
            field = "dpsi_d_dtheta_e" if component == 0 else "dpsi_q_dtheta_e"
            return (plus[field] - minus[field]) / ir

        d_m = [[slope(0.5, 0.0, 0), slope(0.0, 0.5, 0)],
               [slope(0.5, 0.0, 1), slope(0.0, 0.5, 1)]]
        if any(value is None for line in d_m for value in line):
            continue
        a = [[d_l["dd"], d_l["dq"]], [d_l["qd"], d_l["qq"]]]
        diff = [[a[i][j] - d_m[i][j] for j in range(2)] for i in range(2)]
        norm_a = math.sqrt(sum(value * value for line in a for value in line))
        norm_b = math.sqrt(sum(value * value for line in d_m for value in line))
        norm_d = math.sqrt(sum(value * value for line in diff for value in line))
        scale = max(norm_a, norm_b)
        oracle = (-6.0 * (c6 or 0.0) * math.sin(6.0 * math.radians(theta)))
        oracle_max = max(oracle_max, abs(oracle))
        records.append({
            "case_id": row["case_id"], "theta_e_deg": theta,
            "norm_L_theta": norm_a, "norm_M_i": norm_b,
            "rel": (norm_d / scale) if scale > 0.0 else 0.0,
            "abs_max": max(abs(value) for line in diff for value in line),
            "rms": math.sqrt(sum(value * value for line in diff for value in line) / 4.0),
            "measured_Mq_iq": d_m[1][1], "oracle_Mq_iq": oracle,
        })

    if not records:
        return {"records": [], "note": "нет узлов с полным набором соседей"}
    worst = max(records, key=lambda item: item["rel"])
    abs_max = max(item["abs_max"] for item in records)
    rms = math.sqrt(sum(item["rms"] ** 2 for item in records) / len(records))
    return {
        "records": records,
        "nodes": len(records),
        "worst_rel": worst["rel"],
        "worst_case": worst["case_id"],
        "abs_max": abs_max,
        "rms": rms,
        "oracle_max": oracle_max,
    }


def analyse(cases: list[Case], results: dict[str, dict], ir: float,
            pole_pairs: float,
            deltas=DEFAULT_DELTAS,
            scenario_te_rel: float = 0.01,
            scenario_l_rel: float = 0.01,
            gate_reciprocity: float = 0.01,
            gate_eps_l: float = 0.05,
            gate_loop: float = 0.02,
            gate_fit: float = 0.05,
            gate_cross: float = 0.05,
            c6: float | None = None,
            loop_steps: int = LOOP_STEPS,
            psi_floor: float | None = None,
            bh_range_status: str | None = None,
            psi_delta_floor: float | None = None) -> tuple[list[dict], dict]:
    """Построчный L_dq0 + метрики + гейты качества + классификация области.

    Сценарий ставится не точке, а области (по распределению S0n):
      C — есть точки с практически значимой dT/di0;
      B — dT/di0 мала, но L_d0/L_q0 заметны (i0 меняет магнитное состояние);
      A — ни того, ни другого.
    B НЕ запрещён теоретически: на плоскости i_q = 0 чувствительность равна
    -p*L_q0*i_d, то есть может быть и нулевой, и ненулевой.
    """
    cases_by_key = {_key(case): case for case in cases if case.kind in ("base", "stencil")}
    base_cases = [case for case in cases if case.kind == "base"]
    angles = sorted({case.theta_e_deg for case in base_cases})
    step = angles[1] - angles[0] if len(angles) > 1 else DEFAULT_ANGLE_STEP

    diag_scale = 1.0
    for case in base_cases[:4]:
        flux = _flux(results.get(case.case_id, {}), case.theta_e_deg)
        if flux:
            diag_scale = max(diag_scale, abs(flux[0]) / max(abs(case.xd) * ir, 1e-9))
    floor = 1e-3 * diag_scale

    rows: list[dict] = []
    scenarios: dict[str, int] = {}
    eps_l_values: list[float] = []
    reciprocity: dict[str, list[float]] = {"eps_dq": [], "eps_d0": [], "eps_q0": []}
    incomplete: list[str] = []
    one_sided: list[str] = []
    field_errors: list[float] = []
    field_abs: list[float] = []
    fit_errors: list[float] = []

    for center in base_cases:
        result = results.get(center.case_id)
        if result is None:
            incomplete.append(center.case_id)
            continue
        flux = _flux(result, center.theta_e_deg)
        if flux is None:
            incomplete.append(center.case_id)
            continue

        notes: list[str] = []
        matrices: dict[float, dict[str, float] | None] = {}
        methods: set[str] = set()
        primary_axes: dict[str, str] = {}
        for delta in deltas:
            matrix, used = l_matrix(cases_by_key, results, center, delta, ir, flux,
                                    primary_axes if delta == deltas[0] else None)
            matrices[delta] = matrix
            methods |= used
        if methods - {"central"}:
            notes.append("one_sided_stencil:" + ",".join(sorted(methods - {"central"})))
            one_sided.append(center.case_id)
        primary = matrices[deltas[0]]
        if primary is None:
            notes.append("derivative_stencil_incomplete")
        metrics = symmetry_metrics(primary, floor) if primary else {}
        eps_l = float("nan")
        if primary is not None and len(deltas) > 1 and matrices[deltas[1]] is not None:
            eps_l = convergence_metric(primary, matrices[deltas[1]], floor)
            eps_l_values.append(eps_l)
        for name in reciprocity:
            value = metrics.get(name, float("nan"))
            if not math.isnan(value):
                reciprocity[name].append(value)

        # P0: psi == dW'/dI на тех же узлах — отдельная проверка от момента
        energy = _number(result, "W_prime")
        psi_from_w = None
        eps_psi = float("nan")
        if energy is not None:
            psi_from_w, _ = energy_gradient(cases_by_key, results, center,
                                            deltas[0], ir, energy)
        if psi_from_w is not None:
            psi_scale = max(abs(flux[0]), abs(flux[1]), abs(flux[2]), 1e-9)
            worst_abs = max(abs(a - b) for a, b in zip(psi_from_w, flux))
            eps_psi = worst_abs / psi_scale
            field_errors.append(eps_psi)
            field_abs.append(worst_abs)

        series = {0: [], 1: [], 2: []}
        missing_angle = False
        for angle in angles:
            neighbour = cases_by_key.get((round(center.xd, 6), round(center.xq, 6),
                                          round(center.x0, 6), round(angle, 6)))
            neighbour_flux = (_flux(results.get(neighbour.case_id, {}), angle)
                              if neighbour else None)
            if neighbour_flux is None:
                missing_angle = True
                break
            for component in range(3):
                series[component].append(neighbour_flux[component])
        dpsi = [float("nan")] * 3
        max_harmonic = 0
        if not missing_angle and len(series[0]) >= 4:
            try:
                for component in range(3):
                    dpsi[component], max_harmonic = fourier_derivative(
                        series[component], center.theta_e_deg, step,
                        start_deg=angles[0])
            except ValueError:
                dpsi = [float("nan")] * 3
                max_harmonic = 0
                notes.append("angular_span_not_periodic")
        else:
            notes.append("angular_series_incomplete")

        angular_cv = float("nan")
        if not missing_angle and len(series[0]) >= 8:
            angular_scale = max(abs(value) for value in flux)
            angular_cv = max(fourier_fit_residual(series[component], angular_scale)
                             for component in range(3))
            if not math.isnan(angular_cv):
                fit_errors.append(angular_cv)

        te_fem = _number(result, "Te")
        id_, iq, i0 = center.xd * ir, center.xq * ir, center.x0 * ir
        te_flux = (pole_pairs * (flux[0] * iq - flux[1] * id_)
                   if te_fem is not None else float("nan"))

        dte_di0 = float("nan")
        dte_di0_rel = float("nan")
        zero_plus = cases_by_key.get((round(center.xd, 6), round(center.xq, 6),
                                      round(center.x0 + deltas[0], 6),
                                      round(center.theta_e_deg, 6)))
        zero_minus = cases_by_key.get((round(center.xd, 6), round(center.xq, 6),
                                       round(center.x0 - deltas[0], 6),
                                       round(center.theta_e_deg, 6)))
        if zero_plus and zero_minus:
            te_plus = _number(results.get(zero_plus.case_id, {}), "Te")
            te_minus = _number(results.get(zero_minus.case_id, {}), "Te")
            if te_plus is not None and te_minus is not None:
                dte_di0 = (te_plus - te_minus) / (2.0 * deltas[0] * ir)
                dte_di0_rel = abs(dte_di0) * ir / max(abs(te_fem or 0.0), floor)

        l_cross = (max(abs(primary.get("d0", 0.0)), abs(primary.get("q0", 0.0)))
                   if primary else float("nan"))
        l_rel = (l_cross / max(abs(primary.get("dd", 0.0)), abs(primary.get("qq", 0.0)),
                               floor) if primary else float("nan"))

        if primary is None or te_fem is None or math.isnan(dte_di0_rel):
            scenario = "incomplete"
        elif dte_di0_rel > scenario_te_rel:
            scenario = "C"
        elif l_rel > scenario_l_rel:
            scenario = "B"
        else:
            scenario = "A"
        scenarios[scenario] = scenarios.get(scenario, 0) + 1

        row = {
            "case_id": center.case_id,
            "group": center.group,
            "xd": center.xd, "xq": center.xq, "x0": center.x0,
            "theta_e_deg": center.theta_e_deg,
            "Id_A": id_, "Iq_A": iq, "I0_A": i0,
            "psi_d": flux[0], "psi_q": flux[1], "psi_0": flux[2],
            "dpsi_d_dtheta_e": dpsi[0], "dpsi_q_dtheta_e": dpsi[1],
            "dpsi_0_dtheta_e": dpsi[2],
            "Te_fem": te_fem if te_fem is not None else float("nan"),
            "Te_flux": te_flux,
            "dTe_di0": dte_di0,
            "S0n": dte_di0 * ir,
            "dTe_di0_rel": dte_di0_rel,
            "psi_d_W": psi_from_w[0] if psi_from_w else float("nan"),
            "psi_q_W": psi_from_w[1] if psi_from_w else float("nan"),
            "psi_0_W": psi_from_w[2] if psi_from_w else float("nan"),
            "eps_psi": eps_psi,
            "angular_res": angular_cv,
            "eps_L": eps_l,
            "sym_rel": metrics.get("sym_rel", float("nan")),
            "eps_dq": metrics.get("eps_dq", float("nan")),
            "eps_d0": metrics.get("eps_d0", float("nan")),
            "eps_q0": metrics.get("eps_q0", float("nan")),
            "scenario": scenario,
            "notes": ";".join(notes),
            "harmonics_resolved": max_harmonic,
            "stencil_d": primary_axes.get("d"), "stencil_q": primary_axes.get("q"),
            "stencil_0": primary_axes.get("0"),
        }
        for key in MATRIX_KEYS:
            row["L" + key] = primary.get(key, float("nan")) if primary else float("nan")
        rows.append(row)

    cross = _cross_derivative_metrics(rows, ir, angles, step, c6)

    torque_checks = _torque_checks(cases, results, pole_pairs, ir)
    loops = loop_closure(cases, results, ir, loop_steps)

    def median(values: list[float]) -> float | None:
        clean = sorted(value for value in values if not math.isnan(value))
        if not clean:
            return None
        middle = len(clean) // 2
        return clean[middle] if len(clean) % 2 else 0.5 * (clean[middle - 1] + clean[middle])

    reciprocity_median = {name: median(values) for name, values in reciprocity.items()}
    worst_reciprocity = max((value for value in reciprocity_median.values()
                             if value is not None), default=None)
    worst_loop = max((item["rel"] for item in loops
                      if item.get("rel") is not None), default=None)

    max_norm_error = 0.0
    max_roundtrip_error = 0.0
    for case in base_cases[:64]:
        iu, iv, iw = case.currents(ir)
        max_norm_error = max(max_norm_error,
                             abs(iu * iu + iv * iv + iw * iw
                                 - (case.norm2 * ir * ir)))
        back = uvw_to_dq0(iu, iv, iw, math.radians(case.theta_e_deg))
        max_roundtrip_error = max(max_roundtrip_error,
                                  max(abs(a - b * ir) for a, b in
                                      zip(back, (case.xd, case.xq, case.x0))))

    complete = sum(1 for row in rows if not math.isnan(row["Ldd"]))
    fraction = complete / len(base_cases) if base_cases else 0.0
    field_median = median(field_errors)
    fit_worst = max(fit_errors, default=None)
    regions = _region_summary(rows, scenario_te_rel, scenario_l_rel)
    flux_scale = max([max(abs(value) for value in _flux(results.get(case.case_id, {}),
                                                       case.theta_e_deg) or (0.0,))
                      for case in base_cases] + [1e-15])
    zero_state = _zero_state(cases_by_key, results, deltas, ir, angles, flux_scale,
                             psi_floor)
    increment = field_increment_consistency(rows, psi_delta_floor)
    poc2_nonlinear = {
        "field_increment_consistency": increment,
        "P2_N1": p2_n1_gate(rows, deltas[0] * ir, increment, bh_range_status,
                            expected_nodes=len(base_cases)),
        "P2_N2": p2_n2_gate(rows, deltas[0] * ir, increment,
                            expected_nodes=len(base_cases)),
        "P2_N3": p2_n3_gate(rows, 0.5 * ir, increment),
    }
    field_worst = max(field_abs, default=None)
    field_gate = {"median_eps_psi_node": field_median,
                  "max_eps_psi_node": max(field_errors, default=None),
                  "eps_psi_limit": EPS_PSI, "psi_floor": psi_floor,
                  "psi_scale": flux_scale,
                  "note": "eps_psi = max_k,nodes |psi - dW'/di| / max(psi_scale, "
                          "psi_floor) (контракт POC-5); разности не видят "
                          "постоянного сдвига psi — его проверяет zero_state"}
    if field_worst is None:
        field_gate.update({"status": "NOT_RUN", "eps_psi": None})
    elif psi_floor is None:
        field_gate.update({"status": "NOT_EVALUATED", "eps_psi": None,
                           "blocking": ["psi_floor"]})
    else:
        eps_field = field_worst / max(flux_scale, psi_floor)
        field_gate.update({"status": "PASS" if eps_field <= EPS_PSI else "FAIL",
                           "eps_psi": eps_field})

    gates = {
        "derivative_coverage": {
            "status": ("PASS" if fraction >= 0.99
                       else "PARTIAL" if fraction > 0 else "NOT_RUN"),
            "complete": complete, "points": len(base_cases), "fraction": fraction},
        "transform_norm": {"status": _status(max_norm_error, 1e-9 * ir * ir),
                           "max_abs_error_A2": max_norm_error},
        "transform_roundtrip": {"status": _status(max_roundtrip_error, 1e-9 * ir),
                                "max_abs_error_A": max_roundtrip_error},
        "reciprocity": {"status": _status(worst_reciprocity, gate_reciprocity),
                        "worst_median": worst_reciprocity},
        "derivative_convergence": {
            "status": _status(median(eps_l_values), gate_eps_l),
            "median_eps_L": median(eps_l_values)},
        "loop_closure": {"status": _status(worst_loop, gate_loop), "worst_rel": worst_loop},
        "field_consistency": field_gate,
        "angular_fit": {"status": _status(fit_worst, gate_fit), "worst_cv": fit_worst},
        "angular_harmonics_resolved": _harmonics_gate(max(len(angles) // 2 - 1, 0)),
        "cross_derivative_symmetry": _cross_symmetry_gate(cross, gate_cross),
        "cross_derivative_oracle": _cross_oracle_gate(cross, c6, len(angles)),
        "zero_state_energy_consistency": dict(zero_state["energy_consistency"]),
        "zero_state_angular_ripple": dict(zero_state["angular_ripple"]),
    }

    # Диагностические гейты не блокируют READY: угловая пульсация потока при
    # нулевом токе физична (пазы, гармоники PM, T_cog), это предупреждение.
    statuses = {name: gate["status"] for name, gate in gates.items()
                if name not in DIAGNOSTIC_GATES}
    if "FAIL" in statuses.values():
        rom_status = "FAIL"
    elif all(status == "PASS" for status in statuses.values()):
        rom_status = "READY"
    else:
        rom_status = "INCOMPLETE"
    blocking = [name for name, status in statuses.items() if status != "PASS"]
    # Две независимые оси: C_ROM (stencil coverage) и physics coverage.
    # C_ROM = 1.0 не даёт PASS по кросс-производным ни автоматически, ни косвенно.
    physics_names = ("angular_harmonics_resolved", "cross_derivative_oracle",
                     "cross_derivative_symmetry")
    physics_gates = {name: gates[name]["status"] for name in physics_names}
    if "FAIL" in physics_gates.values():
        physics_status = "FAIL"
    elif all(status == "PASS" for status in physics_gates.values()):
        physics_status = "PASS"
    else:
        physics_status = "NOT_VERIFIED"
    physics_coverage = {
        "status": physics_status,
        "gates": physics_gates,
        "stencil_gate": gates["derivative_coverage"]["status"],
        "note": "C_ROM и physics coverage — независимые оси: 100% полноты ROM "
                "не означает проверенных кросс-производных",
    }
    warnings = [name for name in DIAGNOSTIC_GATES
                if gates[name]["status"] not in ("PASS", "NOT_RUN")]

    summary = {
        "points": len(base_points()),
        "coverage": {"complete": complete, "points": len(base_cases),
                     "fraction": fraction},
        "C_ROM": fraction,
        "physics_coverage": physics_coverage,
        "poc2_nonlinear": poc2_nonlinear,
        "cross_derivative": {key: value for key, value in cross.items()
                             if key != "records"},
        "groups": group_counts(cases),
        "angles": len(angles),
        "angle_step_deg": step,
        "harmonics_resolved": max(len(angles) // 2 - 1, 0),
        "cases": case_counts(cases),
        "one_sided_points": one_sided,
        "scenarios": scenarios,
        "eps_L_median": median(eps_l_values),
        "eps_L_max": max([v for v in eps_l_values if not math.isnan(v)], default=None),
        "reciprocity_median": reciprocity_median,
        "torque_checks": torque_checks,
        "loops": loops,
        "regions": regions,
        "zero_state": zero_state,
        "rom_status": rom_status,
        "blocking_gates": blocking,
        "warnings": warnings,
        "gates": gates,
        "incomplete_points": incomplete,
        "assumptions": {
            "pole_pairs": pole_pairs,
            "current_base_A": ir,
            "note": "p и I_r — параметры расчёта, не измеренные паспортные данные",
        },
        "note_angle_derivative": (
            "dpsi/dtheta_e — производная при фиксированных i_dq0 (спектральная), "
            "это НЕ момент: момент есть dW'/dtheta при фиксированных I_U,I_V,I_W"),
        "note_gauge": (
            "psi — абсолютное потокосцепление замкнутых катушек (контракт FEM, "
            "ревизия b): аддитивная постоянная — ошибка экспорта, её ловит "
            "zero_state_energy_consistency; L от неё не зависит, psi_dq0 и "
            "J psi в DOE-2 зависят"),
    }
    return rows, summary


# Гейты-диагностики: попадают в summary["warnings"], но не в блокировку READY
DIAGNOSTIC_GATES = ("zero_state_angular_ripple", "angular_harmonics_resolved",
                    "cross_derivative_oracle", "cross_derivative_symmetry")


def _zero_state_unavailable(note: str) -> dict:
    return {"psi0_dq0_mean": None, "records": [],
            "energy_consistency": {"status": "NOT_RUN", "mismatch": None,
                                   "note": note},
            "angular_ripple": {"status": "NOT_RUN", "pp_rel": None,
                               "note": note}}


EPS_PSI = 1e-3   # Run Card §8 / контракт POC-5: eps_psi, ревизия (c)
BH_RANGE_OK = ("IN_RANGE", "LINEAR_MATERIAL")


def uvw_self_inductances(row: dict, theta_e_deg: float) -> tuple[float, float, float]:
    """Диагональ L_UVW = T^T L_dq0 T; L_dq0[m][n] = row['L' + m + n] = dpsi_m/di_n."""
    theta = math.radians(theta_e_deg)
    axes = ("d", "q", "0")
    columns = [uvw_to_dq0(*unit, theta) for unit in ((1.0, 0.0, 0.0),
                                                       (0.0, 1.0, 0.0),
                                                       (0.0, 0.0, 1.0))]
    out = []
    for column in columns:                      # column = T[:, a]
        out.append(sum(column[m] * row["L" + axes[m] + axes[n]] * column[n]
                       for m in range(3) for n in range(3)))
    return tuple(out)


def uvw_inductance_matrix(row: dict, theta_e_deg: float) -> list[list[float]]:
    """Полная L_UVW = T^T L_dq0 T; [a][b] = dpsi_a/di_b, индексы U, V, W."""
    theta = math.radians(theta_e_deg)
    axes = ("d", "q", "0")
    columns = [uvw_to_dq0(*unit, theta) for unit in ((1.0, 0.0, 0.0),
                                                       (0.0, 1.0, 0.0),
                                                       (0.0, 0.0, 1.0))]
    return [[sum(columns[a][m] * row["L" + axes[m] + axes[n]] * columns[b][n]
                 for m in range(3) for n in range(3))
             for b in range(3)] for a in range(3)]


def _zero_reference(rows: list[dict]) -> dict:
    """Нулевой токовый узел на каждом угле: theta -> строка."""
    return {round(row["theta_e_deg"], 6): row for row in rows
            if row["xd"] == 0.0 and row["xq"] == 0.0 and row["x0"] == 0.0}


def psi_delta_scale(rows: list[dict]) -> float | None:
    """max_(theta, k, узлы) |psi_k(i, theta) - psi_k(0, theta)|; None — нет нулевого узла."""
    zero = _zero_reference(rows)
    values = []
    for row in rows:
        base = zero.get(round(row["theta_e_deg"], 6))
        if base is None:
            return None
        values.extend(abs(row[name] - base[name]) for name in ("psi_d", "psi_q", "psi_0"))
    return max(values, default=None)


def field_increment_consistency(rows: list[dict], psi_delta_floor: float | None) -> dict:
    """Предпосылка P2-N1 (контракт POC-2 nonlinear §2.4): ошибка приращения psi.

    max |[psi - grad W'](i, theta) - [psi - grad W'](0, theta)| /
        max(psi_delta_scale, psi_delta_floor) <= eps_psi.
    Постоянная по току ошибка (в том числе PM-части) сокращается.
    """
    names = (("psi_d", "psi_d_W"), ("psi_q", "psi_q_W"), ("psi_0", "psi_0_W"))
    scale = psi_delta_scale(rows)
    base = {"eps_psi": EPS_PSI, "psi_delta_scale_Wb": scale,
            "psi_delta_floor_Wb": psi_delta_floor, "value": None}
    blocking = []
    if psi_delta_floor is None:
        blocking.append("psi_delta_floor")
    if scale is None:
        blocking.append("zero_current_node")
    zero = _zero_reference(rows)
    worst = None
    for row in rows:
        ref = zero.get(round(row["theta_e_deg"], 6))
        if ref is None:
            continue
        for psi, grad in names:
            if any(math.isnan(item[grad]) for item in (row, ref)):
                worst = float("nan")
                break
            error = abs((row[psi] - row[grad]) - (ref[psi] - ref[grad]))
            worst = error if worst is None else max(worst, error)
        if worst is not None and math.isnan(worst):
            break
    if worst is None or math.isnan(worst):
        blocking.append("W_prime")
    if blocking:
        return {**base, "status": "NOT_EVALUATED", "blocking": blocking}
    value = worst / max(scale, psi_delta_floor)
    return {**base, "status": "PASS" if value <= EPS_PSI else "FAIL", "value": value,
            "blocking": []}


STENCIL_WEIGHT = {"central": 1.0, "fwd2": 4.0, "bwd2": 4.0}   # sum|w_k| / Δi

N2_PAIRS = ((0, 1, "UV"), (0, 2, "UW"), (1, 2, "VW"))


def p2_n2_gate(rows: list[dict], delta_i: float, increment: dict,
               expected_nodes: int) -> dict:
    """P2-N2 (контракт §4): L_ab = L_ba в UVW, допуск из бюджета ошибки DOE-1.

    delta_rec,ab = e_L,ab + e_L,ba;  e_L,ab = (sum_m |T_ma|)(sum_n |T_nb| W_n) e.
    eps_rec — только диагностика (L_scale по тому же домену). DIAGNOSTIC нет.
    """
    scale = increment["psi_delta_scale_Wb"]
    floor = increment["psi_delta_floor_Wb"]
    e = (EPS_PSI * max(scale, floor)
         if scale is not None and floor is not None else None)
    keys = ["L" + m + n for m in "dq0" for n in "dq0"]
    records: list[dict] = []
    incomplete = 0
    for row in rows:
        stencils = [row.get("stencil_" + axis) for axis in "dq0"]
        if (any(math.isnan(row.get(key, float("nan"))) for key in keys)
                or any(item not in STENCIL_WEIGHT for item in stencils)):
            incomplete += 1
            continue
        theta = row["theta_e_deg"]
        matrix = uvw_inductance_matrix(row, theta)
        columns = {phase: uvw_to_dq0(*[1.0 if p == phase else 0.0 for p in "UVW"],
                                     math.radians(theta))
                   for phase in "UVW"}
        weights = [STENCIL_WEIGHT[item] / delta_i for item in stencils]

        def e_l(a: int, b: int) -> float | None:
            if e is None:
                return None
            ca, cb = columns["UVW"[a]], columns["UVW"[b]]
            return (sum(abs(c) for c in ca)
                    * sum(abs(c) * w for c, w in zip(cb, weights)) * e)

        record = {"case_id": row["case_id"], "theta_e_deg": theta}
        worst_pair, worst_asym = None, -1.0
        fails = []
        for a, b, name in N2_PAIRS:
            forward, backward = matrix[a][b], matrix[b][a]
            asym = abs(forward - backward)
            tol_ab, tol_ba = e_l(a, b), e_l(b, a)
            record["L_" + name + "_H"] = forward
            record["L_" + name[::-1] + "_H"] = backward
            record["delta_L_" + name + "_H"] = (None if tol_ab is None
                                                else tol_ab + tol_ba)
            record["_asym_" + name] = asym
            if asym > worst_asym:
                worst_asym, worst_pair = asym, name
            if tol_ab is not None and asym > (tol_ab + tol_ba) * (1.0 + 1e-12):
                fails.append(name)          # 1e-12 — поправка на шум округления у границы
        record["worst_pair"] = worst_pair
        if e is None:
            record["status"] = "NOT_EVALUATED"
        elif fails:
            record["status"] = "FAIL"
        else:
            record["status"] = "PASS"
        records.append(record)
    l_scale = max((max(rec["L_" + n + "_H"], rec["L_" + n[::-1] + "_H"], key=abs)
                   for rec in records for _, _, n in N2_PAIRS), key=abs,
                  default=0.0)
    l_scale = abs(l_scale)
    for rec in records:
        for _, _, n in N2_PAIRS:
            rec["eps_rec_" + n] = (rec["_asym_" + n] / l_scale
                                   if l_scale > 0.0 else float("nan"))
            del rec["_asym_" + n]
    missing = expected_nodes - (len(rows) - incomplete)
    blocking: list[str] = []
    if increment["status"] != "PASS":
        blocking.append("field_increment_consistency")
    if floor is None:
        blocking.append("psi_delta_floor")
    if incomplete or missing > 0 or not records:
        blocking.append("ROM_DOMAIN_INCOMPLETE")
    counts = {name: sum(1 for rec in records if rec["status"] == name)
              for name in ("PASS", "FAIL", "NOT_EVALUATED")}
    worst = None
    for rec in records:
        n = rec["worst_pair"]
        if n is None:
            continue
        asym = abs(rec["L_" + n + "_H"] - rec["L_" + n[::-1] + "_H"])
        if worst is None or asym > worst[1]:
            worst = (rec, asym)
    if counts["FAIL"]:
        status = "FAIL"
    elif not blocking and counts["NOT_EVALUATED"] == 0:
        status = "PASS"
    else:
        status = "NOT_EVALUATED"
    return {"status": status, "class": "physics", "blocking": blocking,
            "worst_case": None if worst is None else worst[0]["case_id"],
            "worst_pair": None if worst is None else worst[0]["worst_pair"],
            "max_abs_asymmetry_H": None if worst is None else worst[1],
            "max_eps_rec": max((rec["eps_rec_" + rec["worst_pair"]]
                                for rec in records if rec["worst_pair"]),
                               default=None),
            "L_scale_H": l_scale, "e_Wb": e, "delta_i_A": delta_i,
            "pass_count": counts["PASS"], "fail_count": counts["FAIL"],
            "not_evaluated_count": counts["NOT_EVALUATED"],
            "domain_status": ("COMPLETE" if not incomplete and missing <= 0
                              else "ROM_DOMAIN_INCOMPLETE"),
            "field_increment_consistency": {"status": increment["status"],
                                            "value": increment["value"]},
            "records": records,
            "note": "L_UVW = T^T L_dq0 T по якобиану DOE-1 (не независимый); "
                    "допуск из бюджета ошибки DOE-1, eps_rec — только диагностика"}


def p2_n3_gate(rows: list[dict], h: float, increment: dict,
               expected_lines: int | None = None) -> dict:
    """P2-N3 (контракт §5): cross-scale consistency ∫L di ≈ Δpsi на решётке.

    Коллинеарные тройки базовых узлов с шагом h (нормированный шаг 0.5),
    Simpson: Δψ_L,m = (h/3)[L_ma(-h) + 4 L_ma(0) + L_ma(+h)].
    Точка FAIL при |r_m| > e (frozen budget, тот же что в N1/N2).
    quadrature_indicator = |Simpson - trapezoid| — только диагностика.
    """
    scale = increment["psi_delta_scale_Wb"]
    floor = increment["psi_delta_floor_Wb"]
    e = (EPS_PSI * max(scale, floor)
         if scale is not None and floor is not None else None)
    keys = ["L" + m + n for m in "dq0" for n in "dq0"]
    axes = ("d", "q", "0")
    complete = [row for row in rows
                if not any(math.isnan(row.get(key, float("nan"))) for key in keys)]
    records: list[dict] = []
    by_angle: dict[float, dict] = {}
    for row in complete:
        by_angle.setdefault(round(row["theta_e_deg"], 6), {})[
            (round(row["xd"], 6), round(row["xq"], 6), round(row["x0"], 6))] = row
    step = 0.5                                # нормированный шаг решётки
    for theta, nodes in sorted(by_angle.items()):
        theta_deg = next(iter(nodes.values()))["theta_e_deg"]
        for center_xyz, center in sorted(nodes.items()):
            for ax in range(3):
                minus_xyz = list(center_xyz)
                plus_xyz = list(center_xyz)
                minus_xyz[ax] = round(minus_xyz[ax] - step, 6)
                plus_xyz[ax] = round(plus_xyz[ax] + step, 6)
                minus = nodes.get(tuple(minus_xyz))
                plus = nodes.get(tuple(plus_xyz))
                if minus is None or plus is None:
                    continue
                axis = axes[ax]
                record = {"theta_e_deg": theta_deg, "axis": axis,
                          "center": list(center_xyz),
                          "case_id": [minus["case_id"], center["case_id"],
                                      plus["case_id"]],
                          "quadrature_method": "SIMPSON", "grid_step_A": h}
                worst = 0.0
                fails = []
                for m in axes:
                    name = "L" + m + axis
                    data = plus["psi_" + m] - minus["psi_" + m]
                    simpson = (h / 3.0) * (minus[name] + 4.0 * center[name]
                                           + plus[name])
                    trapezoid = h * 0.5 * (minus[name] + plus[name])
                    residual = data - simpson
                    record["residual_" + m + "_Wb"] = residual
                    record["indicator_" + m + "_Wb"] = abs(simpson - trapezoid)
                    worst = max(worst, abs(residual))
                    if e is not None and abs(residual) > e * (1.0 + 1e-12):
                        fails.append(m)
                record["residual_norm_Wb"] = worst
                record["tolerance_Wb"] = e
                record["quadrature_indicator_Wb"] = max(
                    record["indicator_" + m + "_Wb"] for m in axes)
                if e is None:
                    record["status"] = "NOT_EVALUATED"
                elif fails:
                    record["status"] = "FAIL"
                else:
                    record["status"] = "PASS"
                records.append(record)
    blocking: list[str] = []
    if increment["status"] != "PASS":
        blocking.append("field_increment_consistency")
    if floor is None:
        blocking.append("psi_delta_floor")
    if len(complete) != len(rows) or not records:
        blocking.append("ROM_DOMAIN_INCOMPLETE")
    counts = {name: sum(1 for rec in records if rec["status"] == name)
              for name in ("PASS", "FAIL", "NOT_EVALUATED")}
    worst = max(records, key=lambda rec: rec["residual_norm_Wb"], default=None)
    if counts["FAIL"]:
        status = "FAIL"
    elif not blocking and counts["NOT_EVALUATED"] == 0:
        status = "PASS"
    else:
        status = "NOT_EVALUATED"
    return {"status": status, "class": "physics", "blocking": blocking,
            "worst_case": None if worst is None else worst["case_id"][1],
            "worst_line": None if worst is None else {
                "axis": worst["axis"], "center": worst["center"],
                "theta_e_deg": worst["theta_e_deg"]},
            "max_residual_Wb": None if worst is None else worst["residual_norm_Wb"],
            "max_normalized_residual": (None if worst is None or not e
                                        else worst["residual_norm_Wb"] / e),
            "tolerance_Wb": e, "grid_step_A": h,
            "pass_count": counts["PASS"], "fail_count": counts["FAIL"],
            "not_evaluated_count": counts["NOT_EVALUATED"],
            "lines_total": len(records),
            "domain_status": ("COMPLETE" if len(complete) == len(rows) and records
                              else "ROM_DOMAIN_INCOMPLETE"),
            "field_increment_consistency": {"status": increment["status"],
                                            "value": increment["value"]},
            "records": records,
            "note": "cross-scale consistency: Simpson-интеграл локального L на "
                    "масштабе решётки vs измеренный Δpsi; residual включает "
                    "квадратурную ошибку — семантика по контракту §5.3"}


def p2_n1_gate(rows: list[dict], delta_i: float, increment: dict,
               bh_range_status: str | None, expected_nodes: int) -> dict:
    """P2-N1 (POC2_NONLINEAR_CONTRACT_V0_1 §2, ревизия b): L_UU, L_VV, L_WW > 0.

    e = eps_psi max(psi_delta_scale, psi_delta_floor); по оси n W_n = sum|w|/Δi;
    delta_L_aa = (sum_m |T_ma|)(sum_n |T_na| W_n) e — граница худшего случая.
    Точка FAIL остаётся FAIL при любых предпосылках. PM-поток не вычитается.
    """
    blocking: list[str] = []
    if increment["status"] != "PASS":
        blocking.append("field_increment_consistency")
    if bh_range_status not in BH_RANGE_OK:
        blocking.append("BH_RANGE_UNPROVEN")
    scale = increment["psi_delta_scale_Wb"]
    floor = increment["psi_delta_floor_Wb"]
    e = (EPS_PSI * max(scale, floor)
         if scale is not None and floor is not None else None)
    keys = ["L" + m + n for m in "dq0" for n in "dq0"]
    records: list[dict] = []
    incomplete = 0
    for row in rows:
        stencils = [row.get("stencil_" + axis) for axis in "dq0"]
        if (any(math.isnan(row.get(key, float("nan"))) for key in keys)
                or any(item not in STENCIL_WEIGHT for item in stencils)):
            incomplete += 1
            continue
        theta = row["theta_e_deg"]
        currents = dq0_to_uvw(row["Id_A"], row["Iq_A"], row["I0_A"], math.radians(theta))
        columns = [uvw_to_dq0(*unit, math.radians(theta))
                   for unit in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))]
        weights = [STENCIL_WEIGHT[item] / delta_i for item in stencils]
        for phase, value, column in zip("UVW", uvw_self_inductances(row, theta), columns):
            if e is None:
                delta_l, status = None, "NOT_EVALUATED"
            else:
                delta_l = (sum(abs(c) for c in column)
                           * sum(abs(c) * w for c, w in zip(column, weights)) * e)
                status = ("PASS" if value >= delta_l else
                          "FAIL" if value <= -delta_l else "DIAGNOSTIC")
            records.append({"case_id": row["case_id"], "phase": phase,
                            "theta_e_deg": theta, "I_U_A": currents[0],
                            "I_V_A": currents[1], "I_W_A": currents[2],
                            "L_aa_H": value, "delta_L_H": delta_l,
                            "stencil_d": stencils[0], "stencil_q": stencils[1],
                            "stencil_0": stencils[2], "status": status})
    missing = expected_nodes - (len(rows) - incomplete)
    if incomplete or missing > 0 or not records:
        blocking.append("ROM_DOMAIN_INCOMPLETE")
    counts = {name: sum(1 for item in records if item["status"] == name)
              for name in ("PASS", "FAIL", "DIAGNOSTIC", "NOT_EVALUATED")}
    if counts["DIAGNOSTIC"]:
        blocking.append("DIAGNOSTIC_POINTS")
    if counts["NOT_EVALUATED"] and "field_increment_consistency" not in blocking:
        blocking.append("error_budget")
    worst = min(records, key=lambda item: item["L_aa_H"]) if records else None
    if counts["FAIL"]:
        status = "FAIL"
    elif not blocking:
        status = "PASS"
    else:
        status = "NOT_EVALUATED"
    return {"status": status, "class": "physics", "blocking": blocking,
            "L_min_H": None if worst is None else worst["L_aa_H"],
            "worst": worst, "e_Wb": e, "delta_i_A": delta_i, "eps_psi": EPS_PSI,
            "psi_delta_scale_Wb": scale, "psi_delta_floor_Wb": floor,
            "field_increment_consistency": {"status": increment["status"],
                                            "value": increment["value"]},
            "counts": counts, "nodes_incomplete": incomplete,
            "angle_grid_status": "HYPOTHESIS_P",
            "bh_range_status": bh_range_status or "BH_RANGE_UNPROVEN",
            "records": records,
            "note": "L_UVW = T^T L_dq0 T по якобиану DOE-1 (не независимый "
                    "якобиан); PM не вычитается; delta_L — граница худшего случая"}


def _zero_state(cases_by_key: dict, results: dict[str, dict], deltas,
                ir: float, angles: list[float], psi_scale: float,
                psi_floor: float | None = None) -> dict:
    """ψ(0,θ) и ∇W'(0,θ) — две разные проверки.

    energy_consistency: psi_FEM(0,θ) == grad_i W'(0,θ). Нулевой ψ не требуется:
    линейный член c*i в W' даёт ту же постоянную в ψ. Это блокирующий гейт:
    max_k|ψ_k - ∂W'/∂i_k| / max(psi_scale, psi_floor) <= EPS_PSI; psi_floor
    задаётся до серии (manifest), а не из результата. step_refinement — то же
    при Δi/2: ошибка центральной разности обязана не расти (не блокирует).

    angular_ripple: разброс ψ(0) по углу в dq0. В реальной машине пазы и
    гармоники PM дают пульсации, а dW'/dtheta|_0 = T_cog. Поэтому только
    предупреждение (mean, peak-to-peak, rel), а не FAIL.
    """
    centers = [case for case in cases_by_key.values()
               if case.kind == "base" and case.norm2 == 0.0]
    if not centers:
        return _zero_state_unavailable("нет точки (0,0,0) в сетке")
    records = []
    for case in sorted(centers, key=lambda item: item.theta_e_deg):
        flux = _flux(results.get(case.case_id, {}), case.theta_e_deg)
        energy = _number(results.get(case.case_id, {}), "W_prime")
        gradient = fine = None
        if flux is not None and energy is not None:
            gradient, _ = energy_gradient(cases_by_key, results, case, deltas[0],
                                          ir, energy)
            if len(deltas) > 1:
                fine, _ = energy_gradient(cases_by_key, results, case, deltas[1],
                                          ir, energy)
        records.append({"theta_e_deg": case.theta_e_deg, "psi": flux,
                        "psi_W": gradient, "psi_W_fine": fine})
    if any(record["psi"] is None for record in records):
        return _zero_state_unavailable("нет psi в точке (0,0,0)")

    vectors = [record["psi"] for record in records]
    # масштаб — поток при номинальном токе (L*I_r), а не абсолютный ноль:
    # иначе допуск в 1% от нуля пропускает любую добавку
    scale = max([max(abs(value) for value in vector) for vector in vectors]
                + [abs(psi_scale), abs(psi_floor or 0.0), 1e-15])
    mismatches = [max(abs(a - b) for a, b in zip(record["psi"], record["psi_W"]))
                  for record in records if record["psi_W"] is not None]
    mismatch = max(mismatches, default=None)
    fine_mismatches = [max(abs(a - b) for a, b in zip(record["psi"],
                                                      record["psi_W_fine"]))
                       for record in records if record["psi_W_fine"] is not None]
    mismatch_fine = max(fine_mismatches, default=None)
    mean = [sum(vector[index] for vector in vectors) / len(vectors)
            for index in range(3)]
    peak_to_peak = [max(vector[index] for vector in vectors)
                    - min(vector[index] for vector in vectors) for index in range(3)]
    pp_rel = [peak_to_peak[index] / max(abs(mean[index]), scale, 1e-15)
              for index in range(3)]
    ripple_threshold = 1e-2
    records_out = [{"theta_e_deg": record["theta_e_deg"],
                    "psi": [round(value, 9) for value in record["psi"]],
                    "psi_W": ([round(value, 9) for value in record["psi_W"]]
                              if record["psi_W"] else None)}
                   for record in records]

    energy = {
        "status": "NOT_RUN" if mismatch is None else "PASS",
        "mismatch": mismatch,
        "mismatch_rel": (mismatch / scale) if mismatch is not None else None,
        "tolerance": EPS_PSI * scale,
        "eps_psi": EPS_PSI, "psi_floor": psi_floor,
        "note": "psi_FEM(0,theta) против grad_i W'(0,theta); постоянная добавка "
                "допустима, если есть линейный член в W'",
    }
    if mismatch is not None and mismatch > energy["tolerance"]:
        energy["status"] = "FAIL"
        energy["note"] = ("psi(0) != dW'/di|0: проверить winding reference, "
                          "полярность обмоток и единицы W'")
    if mismatch is not None and psi_floor is None:
        energy["status"] = "NOT_EVALUATED"
        energy["blocking"] = ["psi_floor"]
        energy["note"] = "psi_floor не задан (manifest сетки): допуск не определён"
    if mismatch is not None and mismatch_fine is not None:
        refined = mismatch_fine <= mismatch + 1e-12 * scale
        energy["step_refinement"] = {
            "status": "PASS" if refined else "FAIL", "blocking": False,
            "mismatch_coarse": mismatch, "mismatch_fine": mismatch_fine,
            "note": "eps_psi(Δi/2) <= eps_psi(Δi): ошибка разности убывает, "
                    "ошибка экспорта — нет"}

    ripple = {
        "status": "PASS" if max(pp_rel) <= ripple_threshold else "WARN",
        "mean_dq0": [round(value, 9) for value in mean],
        "pp_dq0": [round(value, 9) for value in peak_to_peak],
        "pp_rel": [round(value, 9) for value in pp_rel],
        "threshold": ripple_threshold,
        "note": "диагностика, не блокирует ROM: пульсации от пазов/гармоник PM "
                "и T_cog = dW'/dtheta|0 физичны; вычитать из FEM нельзя, "
                "пока не проверена угловая привязка",
    }
    return {"psi0_dq0_mean": [round(value, 9) for value in mean],
            "records": records_out,
            "energy_consistency": energy,
            "angular_ripple": ripple}


def _quantiles(values: list[float]) -> dict:
    clean = sorted(value for value in values if not math.isnan(value))
    if not clean:
        return {"n": 0}

    def pick(q: float) -> float:
        return clean[min(len(clean) - 1, int(round(q * (len(clean) - 1))))]

    return {"n": len(clean), "median": pick(0.5), "p95": pick(0.95),
            "max": clean[-1], "min": clean[0]}


def _l0_rel(row: dict) -> float:
    scale = max(abs(row["Ldd"]), abs(row["Lqq"]), 1e-12)
    return max(abs(row["Ld0"]), abs(row["Lq0"])) / scale


def _region_summary(rows: list[dict], te_gate: float, l_gate: float) -> dict:
    """Классификация ОБЛАСТИ, а не одной точки: по распределению S0n и L_d0/L_q0."""

    def classify(items: list[dict]) -> dict:
        sens = [row["dTe_di0_rel"] for row in items if not math.isnan(row["dTe_di0_rel"])]
        lrel = [_l0_rel(row) for row in items]
        if not sens:
            scenario = "incomplete"
        elif max(sens) > te_gate:
            scenario = "C"
        elif max(lrel) > l_gate:
            scenario = "B"
        else:
            scenario = "A"
        share = sum(1 for value in sens if value > te_gate) / max(len(sens), 1)
        return {
            "scenario": scenario,
            "points": len(items),
            "S0n_abs": _quantiles([abs(row["S0n"]) for row in items]),
            "S0n_signed": _quantiles([row["S0n"] for row in items]),
            "dTe_di0_rel": _quantiles(sens),
            "L0_rel_max": max(lrel, default=None),
            "share_with_significant_sensitivity": share,
        }

    complete = [row for row in rows if not math.isnan(row["Ldd"])]
    by_group: dict[str, dict] = {}
    for name in sorted({row["group"] for row in complete}):
        by_group[name] = classify([row for row in complete if row["group"] == name])
    return {"global": classify(complete), "by_group": by_group,
            "thresholds": {"dTe_di0_rel": te_gate, "L0_rel": l_gate}}


def _torque_checks(cases: list[Case], results: dict[str, dict],
                   pole_pairs: float, ir: float) -> list[dict]:
    groups: dict[str, list[Case]] = {}
    for case in cases:
        if case.kind == "torque":
            groups.setdefault(case.group, []).append(case)
    checks: list[dict] = []
    for group, members in sorted(groups.items()):
        members.sort(key=lambda case: case.theta_e_deg)
        if len(members) != 3:
            continue
        minus, center, plus = members
        w_minus = _number(results.get(minus.case_id, {}), "W_prime")
        w_plus = _number(results.get(plus.case_id, {}), "W_prime")
        te_fem = _number(results.get(center.case_id, {}), "Te")
        flux = _flux(results.get(center.case_id, {}), center.theta_e_deg)
        entry = {"group": group, "Te_fem": te_fem, "Te_energy": None,
                 "Te_flux": None, "rel_error": None}
        if flux is not None:
            entry["Te_flux"] = pole_pairs * (flux[0] * center.xq * ir
                                             - flux[1] * center.xd * ir)
        if w_minus is not None and w_plus is not None:
            h_mech = math.radians(plus.theta_m_deg - minus.theta_m_deg) / 2.0
            te_energy = (w_plus - w_minus) / (2.0 * h_mech)
            entry["Te_energy"] = te_energy
            if te_fem:
                entry["rel_error"] = abs(te_energy - te_fem) / max(abs(te_fem), 1e-9)
        checks.append(entry)
    return checks


# ── self-test на аналитической модели ────────────────────────────────────

class Synthetic:
    """Консервативная модель: psi = dF/di, T = p (dF/dtheta + psi.di/dtheta).

    F зависит только от i_dq0, поэтому psi_UVW = T^T psi_dq0 — градиент по
    построению, а L_dq0 аналитически известна. swirl != 0 добавляет
    неконсервативное поле (для проверки замыкания контура).
    """

    def __init__(self, ld=200e-6, lq=320e-6, l0=80e-6, a4=-8e-7, b4=-1.28e-6,
                 kdq=1e-5, kd0=2e-5, kq0=0.0, psi_f=0.0, psi_cog=0.0, c6=0.0,
                 swirl=0.0, pole_pairs=3.0):
        self.ld, self.lq, self.l0 = ld, lq, l0
        self.a4, self.b4, self.kdq, self.kd0 = a4, b4, kdq, kd0
        self.kq0 = kq0
        self.psi_f = psi_f
        self.psi_cog = psi_cog
        self.c6, self.swirl, self.p = c6, swirl, pole_pairs

    def _dq(self, uvw, theta):
        return uvw_to_dq0(uvw[0], uvw[1], uvw[2], math.radians(theta))

    def energy(self, uvw, theta) -> float:
        id_, iq, i0 = self._dq(uvw, theta)
        th = math.radians(theta)
        return (0.5 * self.ld * id_ ** 2 + 0.25 * self.a4 * id_ ** 4
                + 0.5 * self.lq * iq ** 2 + 0.25 * self.b4 * iq ** 4
                + 0.5 * self.l0 * i0 ** 2
                + 0.5 * self.c6 * iq ** 2 * math.cos(6.0 * th)
                + self.kdq * id_ * iq + self.kd0 * id_ * i0 + self.kq0 * iq * i0
                + self.psi_f * id_
                + self.psi_cog * math.cos(2.0 * th) * id_)

    def flux_uvw(self, uvw, theta) -> tuple[float, float, float]:
        id_, iq, i0 = self._dq(uvw, theta)
        th = math.radians(theta)
        psi_d = (self.ld * id_ + self.a4 * id_ ** 3 + self.kdq * iq
                 + self.kd0 * i0 + self.swirl * iq + self.psi_f
                 + self.psi_cog * math.cos(2.0 * th))
        psi_q = (self.lq * iq + self.b4 * iq ** 3 + self.kdq * id_
                 + self.c6 * iq * math.cos(6.0 * th) - self.swirl * id_
                 + self.kq0 * i0)
        psi_0 = self.l0 * i0 + self.kd0 * id_ + self.kq0 * iq
        ia, ib = inv_park(psi_d, psi_q, th)
        return inv_clarke(ia, ib, psi_0)

    def torque(self, uvw, theta) -> float:
        id_, iq, i0 = self._dq(uvw, theta)
        psi_d, psi_q, _ = self._dq(self.flux_uvw(uvw, theta), theta)
        th = math.radians(theta)
        return self.p * (-3.0 * self.c6 * iq ** 2 * math.sin(6.0 * th)
                         + psi_d * iq - psi_q * id_)

    def analytic_l(self, uvw, theta) -> dict[str, float]:
        id_, iq, _ = self._dq(uvw, theta)
        th = math.radians(theta)
        return {
            "dd": self.ld + 3.0 * self.a4 * id_ ** 2,
            "dq": self.kdq + self.swirl,
            "d0": self.kd0,
            "qd": self.kdq - self.swirl,
            "qq": self.lq + 3.0 * self.b4 * iq ** 2 + self.c6 * math.cos(6.0 * th),
            "q0": self.kq0,
            "0d": self.kd0,
            "0q": self.kq0,
            "00": self.l0,
        }

    def results(self, cases: list[Case], ir: float) -> dict[str, dict]:
        rows = {}
        for case in cases:
            uvw = case.currents(ir)
            psi = self.flux_uvw(uvw, case.theta_e_deg)
            rows[case.case_id] = {
                "case_id": case.case_id,
                "theta_e_deg": case.theta_e_deg,
                "psi_U": psi[0], "psi_V": psi[1], "psi_W": psi[2],
                "Te": self.torque(uvw, case.theta_e_deg),
                "W_prime": self.energy(uvw, case.theta_e_deg),
            }
        return rows


def _selftest() -> int:
    ir, pole_pairs = 5.0, 3.0
    model = Synthetic(pole_pairs=pole_pairs)
    # точность восстановления L проверяется на малом шаге: центральная разность
    # даёт O(delta^2), на 0.05*I_r это ~1e-4 относительной ошибки
    fine = (0.001, 0.0005)
    cases = build_cases(ir, pole_pairs, deltas=fine)
    rows, summary = analyse(cases, model.results(cases, ir), ir, pole_pairs, deltas=fine,
                            psi_floor=0.0)
    worst = 0.0
    compared = 0
    for row in rows:
        if math.isnan(row["Ldd"]):
            continue
        compared += 1
        case = next(c for c in cases if c.case_id == row["case_id"])
        expected = model.analytic_l(case.currents(ir), case.theta_e_deg)
        for key, value in expected.items():
            got = row["L" + key]
            scale = max(abs(value), 1e-7)   # нулевые элементы сравниваются по абсолютной шкале
            worst = max(worst, abs(got - value) / scale)
    print(f"L computed for {compared} points")
    print(f"points={summary['points']} angles={summary['angles']} groups={summary['groups']}")
    print(f"cases={summary['cases']}")
    print(f"L worst relative error={worst:.3e}")
    print(f"eps_L={summary['eps_L_median']} reciprocity={summary['reciprocity_median']}")
    print(f"gates={ {key: value['status'] for key, value in summary['gates'].items()} }")
    for check in summary["torque_checks"]:
        print(f"  {check['group']}: Te_fem={check['Te_fem']:.6g} "
              f"Te_energy={check['Te_energy']} rel={check['rel_error']}")
    for loop in summary["loops"]:
        print(f"  {loop['loop']}@{loop['theta_e_deg']}: rel={loop['rel']}")

    # штатные шаги DOE и полный derivative-охват: та же модель, delta = 0.05 / 0.025 * I_r
    spec = build_cases(ir, pole_pairs, stencil="all")
    _, spec_summary = analyse(spec, model.results(spec, ir), ir, pole_pairs,
                              psi_floor=0.0)
    print(f"eps_L at delta=0.05/0.025: {spec_summary['eps_L_median']:.3g}")
    print(f"scenarios at delta=0.05/0.025: {spec_summary['scenarios']}")
    ok = (worst < 1e-6 and summary["points"] == 33
          and spec_summary["eps_L_median"] < 1e-3
          and spec_summary["gates"]["loop_closure"]["status"] == "PASS"
          and spec_summary["gates"]["reciprocity"]["status"] == "PASS"
          and spec_summary["gates"]["derivative_coverage"]["status"] == "PASS"
          and spec_summary["gates"]["field_consistency"]["status"] == "PASS"
          and spec_summary["rom_status"] == "READY")
    print("SELFTEST:", "ok" if ok else "FAIL")
    return 0 if ok else 1


# ── CLI ──────────────────────────────────────────────────────────────────

def load_manifest_field(cases_path: Path, name: str):
    manifest = Path(cases_path).with_suffix(".manifest.json")
    if not manifest.exists():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get(name)


def load_psi_floor(cases_path: Path) -> tuple[float | None, str]:
    """psi_floor из <cases>.manifest.json (пишет grid --psi-floor).

    Нет манифеста или поля — (None, причина): eps_psi остаётся NOT_EVALUATED,
    подстановки 0 нет.
    """
    manifest = Path(cases_path).with_suffix(".manifest.json")
    if not manifest.exists():
        return None, f"нет {manifest.name}"
    value = json.loads(manifest.read_text(encoding="utf-8")).get("psi_floor")
    if value is None:
        return None, f"{manifest.name}: psi_floor не задан"
    value = float(value)
    if not value >= 0.0:
        raise ValueError(f"{manifest.name}: psi_floor = {value} < 0")
    return value, manifest.name


RUN_CARD_CASE_ROW = re.compile(r"^\|\s*\d+\s*\|\s*`(POC1_[A-Za-z0-9_]+)`\s*\|")


def run_card_case_ids(run_card: Path) -> list[str]:
    """case_id из матрицы расчётных состояний (§6) Run Card POC-1."""
    ids: list[str] = []
    inside = False
    for line in Path(run_card).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.startswith("## 6.")
            continue
        match = RUN_CARD_CASE_ROW.match(line) if inside else None
        if match:
            ids.append(match.group(1))
    return ids


def poc_case_set(cases_dir: Path, run_card: Path) -> dict:
    """Гейт A0: набор case_id в cases/*.json == набор §6 Run Card.

    Только обнаружение расхождения: недостающие файлы не создаются, лишние не
    удаляются. Сравнивается множество, порядок не важен.
    """
    expected = run_card_case_ids(run_card)
    result = {"expected": len(expected), "run_card": str(run_card),
              "cases_dir": str(cases_dir)}
    duplicates_rc = sorted({case for case in expected if expected.count(case) > 1})
    if not expected or duplicates_rc:
        return {**result, "status": "FAIL",
                "note": "в §6 Run Card нет кейсов или есть повторы",
                "run_card_duplicates": duplicates_rc}
    directory = Path(cases_dir)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return {**result, "status": "NOT_EVALUATED", "blocking": ["cases/*.json"],
                "note": "файлы кейсов ещё не созданы"}
    found: dict[str, list[str]] = {}
    unnamed: list[str] = []
    for path in files:
        try:
            case_id = json.loads(path.read_text(encoding="utf-8")).get("case_id")
        except (json.JSONDecodeError, AttributeError):
            case_id = None
        if not case_id:
            unnamed.append(path.name)
            continue
        found.setdefault(case_id, []).append(path.name)
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    duplicates = {case: names for case, names in found.items() if len(names) > 1}
    ok = not (missing or extra or duplicates or unnamed)
    return {**result, "status": "PASS" if ok else "FAIL", "found": len(found),
            "missing": missing, "extra": extra, "duplicates": duplicates,
            "without_case_id": unnamed}


def _load_cases(path: Path, pole_pairs: float) -> list[Case]:
    cases: list[Case] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            cases.append(Case(
                row["case_id"], row["kind"], row.get("group", ""),
                float(row["xd"]), float(row["xq"]), float(row["x0"]),
                float(row["theta_e_deg"]), pole_pairs,
                delta=float(row.get("delta_x") or 0.0),
                axis=row.get("axis", ""), sign=int(row.get("sign") or 0),
                method=row.get("method", ""), step_mult=int(row.get("step_mult") or 1),
                seq=int(row.get("seq") or 0)))
    return cases


def _load_results(path: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            results[row["case_id"]] = row
    return results


def write_doe2_skeleton(path: Path, rows: list[dict], pole_pairs: float) -> int:
    """Заготовка состояний для DOE_2: только то, что уже известно из DOE_1.

    Электрические величины (u_UVW, Vdc, duty, запасы) НЕ вычисляются: нет R,
    напряжения шины и скорости. pwm_feasible = NOT_EVALUATED — не PASS.
    """
    written = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOE2_HEADER)
        writer.writeheader()
        for row in rows:
            if math.isnan(row["Ldd"]):
                continue
            iu, iv, iw = dq0_to_uvw(row["Id_A"], row["Iq_A"], row["I0_A"],
                                    math.radians(row["theta_e_deg"]))
            writer.writerow({
                "case_id": row["case_id"],
                "theta_m_deg": f"{row['theta_e_deg'] / pole_pairs:.10g}",
                "Id_A": f"{row['Id_A']:.10g}", "Iq_A": f"{row['Iq_A']:.10g}",
                "I0_A": f"{row['I0_A']:.10g}",
                "IU_A": f"{iu:.10g}", "IV_A": f"{iv:.10g}", "IW_A": f"{iw:.10g}",
                "pwm_feasible": "NOT_EVALUATED",
                "note": "нет R, Vdc и omega_m: DOE_2 не выполнялся",
            })
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    grid = sub.add_parser("grid", help="сгенерировать точки DOE")
    grid.add_argument("--out", required=True, type=Path)
    grid.add_argument("--ir", required=True, type=float,
                      help="I_r, A — нормировка сетки, не паспортное значение")
    grid.add_argument("--pole-pairs", type=float, default=3.0)
    grid.add_argument("--plan", choices=("electrical", "revolution"), default="electrical",
                      help="electrical: 12 точек на электрический период; "
                           "revolution: диагностический полный оборот")
    grid.add_argument("--points", type=int, default=12)
    grid.add_argument("--max-harmonic", type=int, default=None,
                      help="по умолчанию points//2 - 1: большее значение требует "
                           "больше точек и будет отвергнуто")
    grid.add_argument("--span-mech-deg", type=float, default=None,
                      help="свой механический диапазон вместо плана")
    grid.add_argument("--angle-step", type=float, default=None,
                      help="шаг в электрических градусах (перекрывает --points)")
    grid.add_argument("--delta", type=float, nargs="+", default=list(DEFAULT_DELTAS))
    grid.add_argument("--stencil", choices=("none", "axes", "groups", "all"),
                      default="axes")
    grid.add_argument("--fem-radius", type=float, default=FEM_RADIUS)
    grid.add_argument("--harmonic-margin", type=float, default=2.0,
                      help="запас по числу точек сверх Найквиста (только предупреждение)")
    grid.add_argument("--loop-steps", type=int, default=LOOP_STEPS,
                      help="шагов на ребро в контуре ∮psi·di")
    grid.add_argument("--psi-floor", type=float, default=None,
                      help="psi_floor, Wb: нижняя граница нормировки eps_psi; "
                           "фиксируется до серии и пишется в manifest")
    grid.add_argument("--psi-delta-floor", type=float, default=None,
                      help="psi_delta_floor, Wb: нижняя граница масштаба приращения "
                           "psi для P2-N1; фиксируется до серии")
    grid.add_argument("--no-torque", action="store_true")
    grid.add_argument("--no-loops", action="store_true")
    grid.add_argument("--force", action="store_true",
                      help="писать сетку, даже если план по углу неразрешим")

    syn = sub.add_parser("synthetic", help="синтетический прогон DOE (S0/S1/S2)")
    syn.add_argument("--out", required=True, type=Path)
    syn.add_argument("--ir", type=float, default=5.0)
    syn.add_argument("--pole-pairs", type=float, default=3.0)
    syn.add_argument("--points", type=int, default=12)
    syn.add_argument("--angle-step", type=float, default=None,
                     help="шаг в электрических градусах (перекрывает --points)")
    syn.add_argument("--delta", type=float, nargs="+", default=list(DEFAULT_DELTAS))
    syn.add_argument("--stencil", choices=("none", "axes", "groups", "all"),
                     default="axes")
    syn.add_argument("--fem-radius", type=float, default=FEM_RADIUS)
    syn.add_argument("--c6", type=float, default=0.0,
                     help="validation-only: c6·i_q·cos 6θ, не оценка физики BM1109")
    syn.add_argument("--psi-cog", type=float, default=0.0)
    syn.add_argument("--swirl", type=float, default=0.0)
    syn.add_argument("--no-torque", action="store_true")
    syn.add_argument("--no-loops", action="store_true")

    process = sub.add_parser("process", help="обработать результаты FEM")
    process.add_argument("--cases", required=True, type=Path)
    process.add_argument("--results", required=True, type=Path)
    process.add_argument("--out", required=True, type=Path)
    process.add_argument("--ir", required=True, type=float)
    process.add_argument("--pole-pairs", type=float, default=3.0)
    process.add_argument("--scenario-te-rel", type=float, default=0.01)
    process.add_argument("--scenario-l-rel", type=float, default=0.01)
    process.add_argument("--gate-reciprocity", type=float, default=0.01)
    process.add_argument("--gate-eps-l", type=float, default=0.05)
    process.add_argument("--gate-loop", type=float, default=0.02)
    process.add_argument("--gate-fit", type=float, default=0.05,
                         help="допуск cross-validation углового ряда Фурье")
    process.add_argument("--doe2-out", type=Path, default=None,
                         help="выгрузить заготовку DOE_2 (pwm_feasible=NOT_EVALUATED)")

    sub.add_parser("selftest", help="проверка на аналитической модели")
    caseset = sub.add_parser("caseset", help="A0: cases/*.json против §6 Run Card")
    caseset.add_argument("--cases-dir", required=True, type=Path)
    caseset.add_argument("--run-card", type=Path,
                         default=Path(__file__).resolve().parents[1]
                         / "docs" / "tz" / "POC1_RUN_CARD_V0_3.md")

    args = parser.parse_args(argv)
    if args.command == "caseset":
        report = poc_case_set(args.cases_dir, args.run_card)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "selftest":
        return _selftest()

    if args.command == "synthetic":
        points = (int(round(360.0 / args.angle_step))
                  if args.angle_step else args.points)
        plan = electrical_period_plan(args.pole_pairs, points)
        cases = build_cases(args.ir, args.pole_pairs, deltas=tuple(args.delta),
                            stencil=args.stencil, fem_radius=args.fem_radius,
                            with_torque=not args.no_torque,
                            with_loops=not args.no_loops, plan=plan)
        model = Synthetic(pole_pairs=args.pole_pairs, c6=args.c6,
                          psi_cog=args.psi_cog, swirl=args.swirl)
        rows, summary = analyse(cases, model.results(cases, args.ir),
                                args.ir, args.pole_pairs,
                                deltas=tuple(args.delta), c6=args.c6,
                                psi_floor=0.0)
        args.out.mkdir(parents=True, exist_ok=True)
        write_cases_csv(args.out / "cases.csv", cases, args.ir)
        with (args.out / "L_dq0.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=L_HEADER)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: (f"{value:.10g}" if isinstance(value, float)
                                       and math.isfinite(value)
                                       else "" if isinstance(value, float) else value)
                                 for key, value in row.items()})
        summary["synthetic"] = {"c6": args.c6, "psi_cog": args.psi_cog,
                                "swirl": args.swirl, "points": points,
                                "stencil": args.stencil}
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        cross_gates = {name: summary["gates"][name]
                       for name in ("cross_derivative_symmetry",
                                    "cross_derivative_oracle")}
        print(f"rows={len(rows)} C_ROM={summary['C_ROM']:.3f} "
              f"angles={summary['angles']} harmonics={summary['harmonics_resolved']} "
              f"stencil={args.stencil} c6={args.c6:g}")
        print(f"cross: {summary['cross_derivative']}")
        print("gates: " + " ".join(f"{name}={gate['status']}"
                                   for name, gate in summary["gates"].items()))
        print(f"C_ROM={summary['C_ROM']:.3f} "
              f"physics_coverage={summary['physics_coverage']['status']} "
              f"rom_status={summary['rom_status']} out={args.out}")
        failed = [name for name, gate in cross_gates.items()
                  if gate["status"] == "FAIL"]
        return 0 if (summary["rom_status"] == "READY"
                     and summary["physics_coverage"]["status"] == "PASS"
                     and not failed) else 1

    if args.command == "grid":
        margin = args.harmonic_margin
        if args.angle_step:
            step_points = int(round(360.0 / args.angle_step))
            plan = electrical_period_plan(
                args.pole_pairs, step_points,
                step_points // 2 - 1 if args.max_harmonic is None else args.max_harmonic,
                margin)
        elif args.span_mech_deg is not None:
            plan = AnglePlan(args.points, args.span_mech_deg, args.pole_pairs,
                             args.max_harmonic, margin)
        elif args.plan == "revolution":
            plan = full_revolution_plan(args.pole_pairs, args.points,
                                        args.max_harmonic, margin)
        else:
            plan = electrical_period_plan(args.pole_pairs, args.points,
                                          args.max_harmonic, margin)

        issues = plan.issues()
        if issues and not args.force:
            for issue in issues:
                print(f"PLAN ERROR: {issue}")
            print("PLAN: отказ (--force, если сетка нужна как диагностическая)")
            return 2
        for issue in issues:
            print(f"PLAN WARNING: {issue}")
        for warning in plan.warnings():
            print(f"PLAN HINT: {warning}")

        cases = build_cases(args.ir, args.pole_pairs, deltas=tuple(args.delta),
                            stencil=args.stencil, fem_radius=args.fem_radius,
                            with_torque=not args.no_torque,
                            with_loops=not args.no_loops, plan=plan,
                            loop_steps=args.loop_steps)
        write_cases_csv(args.out, cases, args.ir)
        counts = case_counts(cases)
        manifest = {
            "ir_A": args.ir,
            "pole_pairs": args.pole_pairs,
            "pole_pairs_status": "гипотеза, не измерено",
            "plan": {
                "points": plan.points,
                "span_mech_deg": plan.span_mech_deg,
                "span_e_deg": plan.span_e_deg,
                "magnetic_period_mech_deg": plan.magnetic_period_mech_deg,
                "samples_per_period": plan.samples_per_period,
                "harmonics_resolved": plan.harmonics_resolved,
                "max_harmonic_requested": plan.max_harmonic,
                "minimum_points": plan.required_points(),
                "recommended_points": plan.recommended_points,
                "margin": plan.margin,
                "issues": issues,
                "warnings": plan.warnings(),
            },
            "deltas": list(args.delta),
            "psi_floor": args.psi_floor,
            "psi_floor_unit": "Wb",
            "psi_delta_floor": args.psi_delta_floor,
            "psi_delta_floor_unit": "Wb",
            "bh_range_status": None,
            "bh_range_note": "IN_RANGE | LINEAR_MATERIAL — задаётся после "
                             "проверки max|B|; пусто = BH_RANGE_UNPROVEN",
            "stencil": args.stencil,
            "fem_radius": args.fem_radius,
            "points": len(base_points()),
            "groups": group_counts(cases),
            "cases": counts,
            "total": len(cases),
            "note": "углы электрические; L = dpsi/di (центральная разность, "
                    "на границе Omega_FEM — односторонняя 2-го порядка); "
                    "узлы вне Omega_op помечены outside_app_sphere",
        }
        manifest_path = args.out.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        # консоль Windows часто cp1251: полный манифест — в файле, здесь только ASCII
        print(f"cases={len(cases)} base={counts.get('base', 0)} "
              f"stencil={counts.get('stencil', 0)} torque={counts.get('torque', 0)} "
              f"loop={counts.get('loop', 0)} points={manifest['points']}")
        print(f"plan: {plan.points} pts / {plan.span_mech_deg:g} deg mech, "
              f"{plan.samples_per_period:g} samples per period, "
              f"harmonics<={plan.harmonics_resolved}")
        print(f"written: {args.out} and {manifest_path}")
        return 0

    cases = _load_cases(args.cases, args.pole_pairs)
    results = _load_results(args.results)
    psi_floor, floor_source = load_psi_floor(args.cases)
    bh_range_status = load_manifest_field(args.cases, "bh_range_status")
    psi_delta_floor = load_manifest_field(args.cases, "psi_delta_floor")
    if psi_delta_floor is not None and not float(psi_delta_floor) >= 0.0:
        raise ValueError(f"psi_delta_floor = {psi_delta_floor} < 0")
    if psi_floor is None:
        print(f"psi_floor: нет ({floor_source}) -> eps_psi NOT_EVALUATED")
    rows, summary = analyse(cases, results, args.ir, args.pole_pairs,
                            psi_floor=psi_floor,
                            bh_range_status=bh_range_status,
                            psi_delta_floor=(None if psi_delta_floor is None
                                             else float(psi_delta_floor)),
                            scenario_te_rel=args.scenario_te_rel,
                            scenario_l_rel=args.scenario_l_rel,
                            gate_reciprocity=args.gate_reciprocity,
                            gate_eps_l=args.gate_eps_l,
                            gate_loop=args.gate_loop,
                            gate_fit=args.gate_fit)
    summary["psi_floor_source"] = floor_source
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "L_dq0.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=L_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (f"{value:.10g}" if isinstance(value, float)
                                   and math.isfinite(value)
                                   else "" if isinstance(value, float) else value)
                             for key, value in row.items()})
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"rows={len(rows)} scenarios={summary['scenarios']} "
          f"eps_L_median={summary['eps_L_median']}")
    print("gates: " + " ".join(f"{name}={gate['status']}"
                               for name, gate in summary["gates"].items()))
    print(f"rom_status={summary['rom_status']} region={summary['regions']['global']['scenario']} "
          f"blocking={summary['blocking_gates']}")
    n1 = summary["poc2_nonlinear"]["P2_N1"]
    print(f"P2-N1={n1['status']} L_min_H={n1['L_min_H']} e_Wb={n1['e_Wb']} "
          f"blocking={n1['blocking']}")
    n2 = summary["poc2_nonlinear"]["P2_N2"]
    print(f"P2-N2={n2['status']} max_asymmetry_H={n2['max_abs_asymmetry_H']} "
          f"blocking={n2['blocking']}")
    n3 = summary["poc2_nonlinear"]["P2_N3"]
    print(f"P2-N3={n3['status']} max_residual_Wb={n3['max_residual_Wb']} "
          f"lines={n3['lines_total']} blocking={n3['blocking']}")
    print(f"written: {args.out / 'L_dq0.csv'} and {args.out / 'summary.json'}")
    if args.doe2_out is not None:
        count = write_doe2_skeleton(args.doe2_out, rows, args.pole_pairs)
        print(f"doe2 skeleton: {count} states -> {args.doe2_out} (pwm_feasible=NOT_EVALUATED)")
    return 0 if summary["rom_status"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
