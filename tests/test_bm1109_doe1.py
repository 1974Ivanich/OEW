"""Тесты процедуры DOE для BM1109 (tools/bm1109_doe1.py).

Проверяется не «код запускается», а свойства конструкции:
  * сфера содержит 33 точки, группы A..H дают 1/12/12/8, база 396 на 12 углах;
  * угловой план не выдаёт алиасы: 12 точек на период разрешают до 5-й
    гармоники, 6-я требует 24, полный оборот при p=3 — 42;
  * преобразование ортонормальное, сфера равна RMS-ресурсу, i0 = sum/sqrt(3);
  * L = dpsi/di восстанавливается на аналитической модели точно, на границе
    Omega_FEM схема переходит в одностороннюю и остаётся второго порядка;
  * метрика взаимности ловит асимметрию, метрика сходимости — сдвиг шага;
  * 12 угловых точек не видят 6-ю гармонику, 24 видят;
  * энергетический момент (фиксированные I_U,I_V,I_W) сходится с Maxwell,
    а dpsi/dtheta на dq-сетке моментом не является;
  * dT/di0 = p(L_d0 i_q - L_q0 i_d): сценарий B как режим невозможен;
  * ∮psi·di = 0 на консервативном поле и != 0 на вихревом.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bm1109_doe1 as doe

IR = 5.0
P = 3.0
PSI_FLOOR = 0.0     # синтетика: нижняя граница задана явно


# ── сетка и группы ───────────────────────────────────────────────────────

@pytest.mark.implementation
def test_base_grid_has_33_points() -> None:
    points = doe.base_points()
    assert len(points) == 33
    assert (0.0, 0.0, 0.0) in points
    # одна ненулевая координата: 6 точек |x|=1 и 6 точек |x|=0.5
    assert sum(1 for point in points if sum(v != 0.0 for v in point) == 1) == 12
    assert sum(1 for point in points if abs(sum(v * v for v in point) - 0.5) < 1e-12) == 12


@pytest.mark.implementation
def test_groups_split_is_1_12_12_8() -> None:
    groups = {name: 0 for name in "ABCDEFGH"}
    for point in doe.base_points():
        groups[doe.group_of(*point)] += 1
    assert groups == {"A": 1, "B": 4, "C": 4, "D": 4,
                      "E": 4, "F": 4, "G": 4, "H": 8}


@pytest.mark.implementation
def test_case_count_is_396_not_972() -> None:
    twelve = doe.build_cases(IR, P, 30.0, with_torque=False, with_loops=False)
    assert doe.case_counts(twelve)["base"] == 396
    twenty_four = doe.build_cases(IR, P, 15.0, with_torque=False, with_loops=False)
    assert doe.case_counts(twenty_four)["base"] == 792


# ── угловой план ─────────────────────────────────────────────────────────

@pytest.mark.implementation
def test_electrical_plan_resolves_up_to_five_harmonics() -> None:
    plan = doe.electrical_period_plan(P, 12, max_harmonic=5)
    assert plan.samples_per_period == pytest.approx(12.0)
    assert plan.harmonics_resolved == 5
    assert plan.issues() == []
    assert len(plan.angles_e_deg()) == 12
    assert plan.angles_e_deg()[1] == pytest.approx(30.0)


@pytest.mark.implementation
def test_plan_refuses_sixth_harmonic_on_twelve_points() -> None:
    plan = doe.electrical_period_plan(P, 12, max_harmonic=6)
    assert plan.issues()
    assert plan.required_points() == 14          # 2*(n+1) по Найквисту
    assert doe.electrical_period_plan(P, 14, max_harmonic=6).issues() == []
    assert doe.electrical_period_plan(P, 24, max_harmonic=6).issues() == []


@pytest.mark.implementation
def test_full_revolution_needs_42_points_for_sixth_harmonic() -> None:
    twelve = doe.full_revolution_plan(P, 12)
    assert twelve.samples_per_period == pytest.approx(4.0)
    assert twelve.harmonics_resolved == 1
    assert twelve.issues()

    ok = doe.full_revolution_plan(P, 42, max_harmonic=6)
    assert ok.samples_per_period == pytest.approx(14.0)
    assert ok.harmonics_resolved == 6
    assert ok.issues() == []


@pytest.mark.implementation
def test_plan_detects_a_span_that_is_not_a_multiple_of_the_period() -> None:
    plan = doe.AnglePlan(points=24, span_mech_deg=100.0, pole_pairs=3.0, max_harmonic=2)
    assert any("не кратен" in issue for issue in plan.issues())


@pytest.mark.implementation
def test_grid_cli_refuses_an_unresolvable_plan(tmp_path: Path) -> None:
    out = tmp_path / "cases.csv"
    code = doe.main(["grid", "--out", str(out), "--ir", str(IR), "--pole-pairs", str(P),
                     "--plan", "revolution", "--points", "12"])
    assert code == 2
    assert not out.exists()


# ── точки и стенсилы ─────────────────────────────────────────────────────

@pytest.mark.implementation
def test_stencil_points_never_exceed_the_identification_radius() -> None:
    cases = doe.build_cases(IR, P, 30.0, stencil="all", with_loops=False)
    stencils = [case for case in cases if case.kind == "stencil"]
    assert stencils
    assert max(case.norm2 for case in stencils) <= doe.FEM_RADIUS ** 2 + 1e-12
    assert any(case.norm2 > 1.0 for case in stencils)


@pytest.mark.implementation
def test_boundary_point_switches_to_one_sided_stencil() -> None:
    """При Omega_FEM = Omega_op у точек |x| = 1 остаётся только продольная
    односторонняя производная: поперечные соседи уже вне области, и это
    сообщается как incomplete, а не подменяется схемой."""
    cases = doe.build_cases(IR, P, 30.0, stencil="axes", fem_radius=1.0,
                            with_torque=False, with_loops=False)
    methods = {case.method for case in cases if case.kind == "stencil"}
    assert "bwd2" in methods and "fwd2" in methods
    axial = [case for case in cases
             if case.kind == "stencil" and case.axis == "d" and case.method == "bwd2"]
    assert axial and {case.step_mult for case in axial} == {1, 2}
    assert max(case.norm2 for case in cases if case.kind == "stencil") <= 1.0 + 1e-12

    model = doe.Synthetic(pole_pairs=P)
    rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    boundary = [row for row in rows
                if row["group"] in "BCD" and abs(row["xd"]) + abs(row["xq"]) + abs(row["x0"]) == 1.0]
    assert boundary and all(math.isnan(row["Ldd"]) for row in boundary)
    assert all("derivative_stencil_incomplete" in row["notes"] for row in boundary)
    assert all(row["scenario"] == "incomplete" for row in boundary)
    assert summary["scenarios"].get("incomplete", 0) >= len(boundary)


@pytest.mark.numerics
def test_one_sided_derivative_stays_second_order() -> None:
    """Точка 0.95*I_r: d-сосед +2Δ уже вне Omega_FEM, схема — односторонняя."""
    model = doe.Synthetic(pole_pairs=P)
    centre = (0.95, 0.0, 0.0)
    delta = 0.05
    cases = [doe.Case("C0", "base", "B", *centre, 0.0, P)]
    for axis, slot in (("d", 0), ("q", 1), ("0", 2)):
        offsets = ((delta, 2.0 * delta) if axis == "d"
                   else (delta, -delta))
        for offset in offsets:
            coords = list(centre)
            coords[slot] += offset
            cases.append(doe.Case(
                f"S{axis}{offset:+.2f}", "stencil", "", coords[0], coords[1], coords[2],
                0.0, P, delta=delta, axis=axis,
                sign=1 if offset > 0 else -1,
                method="fwd2" if axis == "d" else "central"))
    rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    assert summary["one_sided_points"] == ["C0"]
    uvw = doe.dq0_to_uvw(0.95 * IR, 0.0, 0.0, 0.0)
    expected = model.analytic_l(uvw, 0.0)
    assert rows[0]["Ldd"] == pytest.approx(expected["dd"], rel=2e-3)
    assert rows[0]["Lqq"] == pytest.approx(expected["qq"], rel=2e-3)
    assert "one_sided" in rows[0]["notes"]


@pytest.mark.implementation
def test_torque_cases_hold_physical_currents_fixed() -> None:
    cases = [case for case in doe.build_cases(IR, P) if case.kind == "torque"]
    assert len(cases) == 9
    for group in {case.group for case in cases}:
        members = sorted((case for case in cases if case.group == group),
                         key=lambda case: case.theta_e_deg)
        currents = [case.currents(IR) for case in members]
        for other in currents[1:]:
            for first, second in zip(currents[0], other):
                assert first == pytest.approx(second, abs=1e-9)


# ── преобразования ───────────────────────────────────────────────────────

@pytest.mark.parametrize("xd,xq,x0,theta", [
    (1.0, 0.0, 0.0, 0.0), (0.5, 0.5, 0.0, 37.0), (0.0, 0.0, 1.0, 200.0),
    (-0.5, 0.25, 0.5, 315.0),
])
@pytest.mark.physics
def test_clarke_park_is_orthonormal_and_invertible(xd, xq, x0, theta) -> None:
    uvw = doe.dq0_to_uvw(xd, xq, x0, math.radians(theta))
    assert sum(v * v for v in uvw) == pytest.approx(xd ** 2 + xq ** 2 + x0 ** 2, rel=1e-12)
    back = doe.uvw_to_dq0(uvw[0], uvw[1], uvw[2], math.radians(theta))
    for first, second in zip((xd, xq, x0), back):
        assert first == pytest.approx(second, abs=1e-12)


@pytest.mark.physics
def test_zero_axis_is_phase_sum_over_sqrt3() -> None:
    u, v, w = 1.0, -2.0, 3.0
    assert doe.clarke(u, v, w)[2] == pytest.approx((u + v + w) / math.sqrt(3.0))


# ── L, взаимность, сходимость ────────────────────────────────────────────

def run_model(model: doe.Synthetic, stencil: str = "all", angle: float = 30.0,
              deltas=(0.05, 0.025)):
    cases = doe.build_cases(IR, P, angle, deltas=deltas, stencil=stencil)
    return cases, doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR, deltas=deltas)


@pytest.mark.physics
def test_analytic_L_is_recovered() -> None:
    """На малом шаге L восстанавливается точно; на 0.05*I_r остаётся O(delta^2)."""
    cases, (rows, summary) = run_model(doe.Synthetic(pole_pairs=P),
                                       deltas=(0.001, 0.0005))
    complete = [row for row in rows if not math.isnan(row["Ldd"])]
    assert len(complete) == 33 * 12
    for row in complete:
        case = next(case for case in cases if case.case_id == row["case_id"])
        expected = doe.Synthetic(pole_pairs=P).analytic_l(case.currents(IR),
                                                          case.theta_e_deg)
        for key, value in expected.items():
            assert row["L" + key] == pytest.approx(value, rel=1e-6, abs=1e-10)
    assert summary["eps_L_median"] < 1e-6
    assert summary["reciprocity_median"]["eps_dq"] == pytest.approx(0.0, abs=1e-9)
    assert summary["gates"]["reciprocity"]["status"] == "PASS"


@pytest.mark.implementation
def test_symmetry_metric_detects_injected_asymmetry() -> None:
    matrix = {"dd": 1.0, "dq": 0.20, "qd": 0.10, "d0": 0.0, "0d": 0.0,
              "qq": 1.0, "q0": 0.0, "0q": 0.0, "00": 1.0}
    metrics = doe.symmetry_metrics(matrix, floor=1e-6)
    assert metrics["eps_dq"] == pytest.approx(0.5)
    assert metrics["eps_d0"] == 0.0
    assert metrics["sym_rel"] > 0.0


@pytest.mark.implementation
def test_perturbed_flux_shows_up_as_asymmetry() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="axes", with_loops=False)
    results = model.results(cases, IR)
    center = next(case for case in cases
                  if case.kind == "base" and case.norm2 == 0.0 and case.theta_e_deg == 0.0)
    disturbed = dict(results)
    for case in cases:
        if (case.kind == "stencil" and case.axis == "q" and case.sign > 0
                and case.theta_e_deg == 0.0 and case.xd == center.xd
                and case.xq == pytest.approx(center.xq + 0.05)):
            row = dict(disturbed[case.case_id])
            row["psi_V"] = float(row["psi_V"]) + 1e-4
            disturbed[case.case_id] = row
    rows, summary = doe.analyse(cases, disturbed, IR, P, psi_floor=PSI_FLOOR)
    row = next(item for item in rows if item["case_id"] == center.case_id)
    clean, _ = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    baseline = next(item for item in clean if item["case_id"] == center.case_id)
    assert baseline["eps_dq"] == pytest.approx(0.0, abs=1e-9)
    assert row["eps_dq"] > 1e-3
    assert summary["reciprocity_median"]["eps_dq"] > 0.0


@pytest.mark.physics
def test_reciprocity_defines_torque_sensitivity_to_zero_axis() -> None:
    """dT/di0 = p(L_d0 i_q - L_q0 i_d) — проверяется на всех 396 точках."""
    model = doe.Synthetic(pole_pairs=P, kd0=2e-5)
    _, (rows, _) = run_model(model)
    checked = 0
    for row in rows:
        if math.isnan(row["Ldd"]) or math.isnan(row["dTe_di0"]):
            continue
        predicted = P * (row["Ld0"] * row["Iq_A"] - row["Lq0"] * row["Id_A"])
        assert row["dTe_di0"] == pytest.approx(predicted, rel=1e-6, abs=1e-9)
        checked += 1
    assert checked == 33 * 12


# ── угловая производная и момент ─────────────────────────────────────────

@pytest.mark.numerics
def test_twelve_angles_are_blind_to_sixth_harmonic() -> None:
    true_value = -6.0 * math.sin(math.radians(60.0))     # d(cos6t)/dt при 10°
    values12 = [math.cos(6.0 * math.radians(10.0 + 30.0 * k)) for k in range(12)]
    values24 = [math.cos(6.0 * math.radians(10.0 + 15.0 * k)) for k in range(24)]
    derivative12, harmonics12 = doe.fourier_derivative(values12, 10.0, 30.0,
                                                       start_deg=10.0)
    derivative24, harmonics24 = doe.fourier_derivative(values24, 10.0, 15.0,
                                                       start_deg=10.0)
    assert harmonics12 == 5
    assert harmonics24 == 11
    assert abs(derivative24 - true_value) < 1e-9
    assert abs(derivative12 - true_value) > 0.5 * abs(true_value)


@pytest.mark.numerics
@pytest.mark.parametrize("pole_pairs", [1, 3])
@pytest.mark.parametrize("span", ["electrical", "revolution"])
def test_fourier_derivative_uses_actual_span_and_pole_pairs(pole_pairs, span) -> None:
    """sin(6 theta_e) + 0.3 cos(theta_e): ловит масштаб, фазу и путаницу мех./эл."""
    points = 24 if span == "electrical" else 42 * pole_pairs
    span_e = 360.0 if span == "electrical" else 360.0 * pole_pairs
    step = span_e / points
    start = 7.0

    def f(theta_deg: float) -> float:
        t = math.radians(theta_deg)
        return math.sin(6.0 * t) + 0.3 * math.cos(t)

    values = [f(start + step * k) for k in range(points)]
    theta0 = start + 5.0 * step
    t0 = math.radians(theta0)
    true_value = 6.0 * math.cos(6.0 * t0) - 0.3 * math.sin(t0)
    derivative, harmonics = doe.fourier_derivative(values, theta0, step,
                                                   start_deg=start)
    assert harmonics >= 6
    assert derivative == pytest.approx(true_value, rel=1e-9, abs=1e-9)


@pytest.mark.numerics
def test_fourier_derivative_rejects_a_non_periodic_span() -> None:
    values = [math.sin(math.radians(6.25 * k)) for k in range(24)]   # 150° эл.
    with pytest.raises(ValueError, match="не кратен"):
        doe.fourier_derivative(values, 0.0, 6.25, start_deg=0.0)


@pytest.mark.physics
def test_energy_torque_matches_maxwell_torque() -> None:
    model = doe.Synthetic(pole_pairs=P, c6=0.0)
    _, (_, summary) = run_model(model)
    assert summary["torque_checks"]
    for check in summary["torque_checks"]:
        assert check["Te_flux"] == pytest.approx(check["Te_fem"], rel=1e-9, abs=1e-12)
        assert check["rel_error"] < 0.01


@pytest.mark.physics
def test_flux_torque_misses_the_cogging_term_that_energy_sees() -> None:
    """T = p(psi_d i_q - psi_q i_d) теряет dF/dtheta: разницу видит только W'."""
    c6 = 2e-5
    model = doe.Synthetic(pole_pairs=P, c6=c6)
    _, (_, summary) = run_model(model)
    reference_step = 1e-3          # градусы: независимая мелкая разность по W'
    for check in summary["torque_checks"]:
        group = int(check["group"][2:])
        state = doe.TORQUE_STATES[group - 1]
        iq = state[1] * IR
        theta = math.radians(doe.TORQUE_THETA_DEG)
        uvw = doe.dq0_to_uvw(state[0] * IR, state[1] * IR, state[2] * IR, theta)
        reference = ((model.energy(uvw, doe.TORQUE_THETA_DEG + reference_step)
                      - model.energy(uvw, doe.TORQUE_THETA_DEG - reference_step))
                     / (2.0 * math.radians(reference_step) / P))
        assert check["Te_fem"] == pytest.approx(reference, rel=1e-6, abs=1e-12)
        # 5-градусный стенсил ослабляет 8-ю гармонику на ~8%: допуск 10%
        assert check["Te_energy"] == pytest.approx(reference, rel=0.10, abs=1e-12)
        h = math.radians(doe.TORQUE_H_DEG)
        damping = math.sin(6.0 * h) / (6.0 * h)
        expected_gap = -3.0 * P * c6 * iq ** 2 * math.sin(6.0 * theta) * damping
        gap = check["Te_energy"] - check["Te_flux"]
        if abs(iq) < 1e-9:
            # при i_q = 0 когтинг-члена нет: остаётся только ошибка стенсила
            assert abs(gap) < 0.1 * abs(reference)
        else:
            assert gap == pytest.approx(expected_gap, rel=0.03, abs=1e-12)


@pytest.mark.physics
def test_angle_stencil_on_dq_grid_is_not_torque() -> None:
    """При фиксированных i_dq0 dW'/dtheta = 0 — момент так не считается."""
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, with_torque=False, with_loops=False)
    results = model.results(cases, IR)
    centre = next(case for case in cases
                  if case.kind == "base" and case.xd == 0.5 and case.xq == 0.5
                  and case.theta_e_deg == 0.0)
    neighbours = [case for case in cases
                  if case.kind == "base" and case.xd == 0.5 and case.xq == 0.5
                  and case.x0 == 0.0 and case.theta_e_deg in (30.0, 330.0)]
    assert len(neighbours) == 2
    energies = [results[case.case_id]["W_prime"] for case in neighbours]
    naive = (energies[0] - energies[1]) / (2.0 * math.radians(30.0 / P))
    assert naive == pytest.approx(0.0, abs=1e-9)
    assert abs(model.torque(centre.currents(IR), 0.0)) > 1e-6


# ── замыкание контура ────────────────────────────────────────────────────

@pytest.mark.physics
def test_loop_closure_passes_for_a_conservative_model() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="none", with_torque=False)
    loops = [case for case in cases if case.kind == "loop"]
    assert len(loops) == 2 * 2 * 13
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["loop_closure"]["status"] == "PASS"
    assert all(loop["rel"] < 0.02 for loop in summary["loops"])


@pytest.mark.physics
def test_loop_closure_detects_a_non_conservative_field() -> None:
    model = doe.Synthetic(pole_pairs=P, swirl=3e-4)
    cases = doe.build_cases(IR, P, 30.0, stencil="none", with_torque=False)
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["loop_closure"]["status"] == "FAIL"
    assert all(loop["rel"] > 0.02 for loop in summary["loops"] if loop["status"] == "ok")


# ── покрытие derivative-сетки ────────────────────────────────────────────

@pytest.mark.implementation
def test_partial_stencil_is_not_reported_as_a_pass() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="axes", with_loops=False)
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    coverage = summary["gates"]["derivative_coverage"]
    assert coverage["status"] == "PARTIAL"
    assert coverage["complete"] == 13 * 12
    assert coverage["points"] == 396


@pytest.mark.implementation
def test_full_stencil_coverage_passes() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="all", with_loops=False)
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["derivative_coverage"]["status"] == "PASS"
    assert summary["coverage"]["complete"] == 396


# ── P0: чувствительность к i0, поле, энергия контура ────────────────────

@pytest.mark.physics
def test_iq_zero_does_not_force_zero_sensitivity() -> None:
    """На i_q = 0: dT/di0 = -p L_q0 i_d, поэтому сценарий C возможен."""
    model = doe.Synthetic(pole_pairs=P, kd0=2e-5, kq0=1e-5)
    _, (rows, summary) = run_model(model)
    iq_zero = [row for row in rows
               if abs(row["Iq_A"]) < 1e-9 and abs(row["Id_A"]) > 0.1
               and not math.isnan(row["dTe_di0"])]
    assert iq_zero
    for row in iq_zero:
        expected = -P * row["Lq0"] * row["Id_A"]
        assert row["dTe_di0"] == pytest.approx(expected, rel=1e-6, abs=1e-12)
    assert any(row["scenario"] == "C" for row in iq_zero)
    assert summary["regions"]["global"]["scenario"] == "C"


@pytest.mark.physics
def test_scenario_b_exists_where_L0_changes_state_but_torque_sensitivity_vanishes() -> None:
    model = doe.Synthetic(pole_pairs=P, kd0=2e-5, kq0=2e-5)
    _, (rows, _) = run_model(model)
    diagonal = [row for row in rows
                if not math.isnan(row["Ldd"]) and abs(row["Id_A"] - row["Iq_A"]) < 1e-9
                and abs(row["Id_A"]) > 0.1 and row["theta_e_deg"] == 0.0]
    assert diagonal
    assert all(abs(row["dTe_di0"]) < 1e-9 for row in diagonal)
    assert all(row["scenario"] == "B" for row in diagonal)
    assert all(abs(row["Ld0"]) > 0.0 for row in diagonal)


@pytest.mark.physics
def test_field_consistency_compares_psi_with_dW_di() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    results = model.results(cases, IR)
    _, summary = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["field_consistency"]["status"] == "PASS"
    assert summary["gates"]["field_consistency"]["eps_psi"] <= 1e-3
    assert summary["rom_status"] == "READY"

    stripped = {case_id: {key: value for key, value in row.items() if key != "W_prime"}
                for case_id, row in results.items()}
    _, without = doe.analyse(cases, stripped, IR, P, psi_floor=PSI_FLOOR)
    assert without["gates"]["field_consistency"]["status"] == "NOT_RUN"
    assert without["rom_status"] == "INCOMPLETE"
    assert "field_consistency" in without["blocking_gates"]


@pytest.mark.physics
def test_loop_absolute_energy_matches_the_enclosed_area() -> None:
    swirl = 3e-4
    model = doe.Synthetic(pole_pairs=P, swirl=swirl)
    cases = doe.build_cases(IR, P, 30.0, stencil="none", with_torque=False)
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    loop = next(item for item in summary["loops"]
                if item["loop"] == "LP1" and item["theta_e_deg"] == 0.0)
    area = 0.5 * (0.5 * IR) * (0.5 * IR)          # треугольник в (i_d, i_q), A^2
    # обход против часовой: ∮(i_q di_d - i_d di_q) = -2A, знак важен
    expected_mJ = -2.0 * swirl * area * 1e3
    assert loop["W_loop_mJ"] == pytest.approx(expected_mJ, rel=0.01)
    assert loop["steps_per_edge"] == doe.LOOP_STEPS


@pytest.mark.numerics
def test_loop_integral_does_not_depend_on_step_count_for_a_linear_field() -> None:
    swirl = 3e-4
    model = doe.Synthetic(pole_pairs=P, swirl=swirl)
    values = []
    for steps in (2, 8):
        cases = doe.build_cases(IR, P, 30.0, stencil="none", with_torque=False,
                                loop_steps=steps)
        _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR,
                                 loop_steps=steps)
        loop = next(item for item in summary["loops"]
                    if item["loop"] == "LP1" and item["theta_e_deg"] == 0.0)
        values.append(loop["W_loop_mJ"])
    assert values[0] == pytest.approx(values[1], rel=0.01)


@pytest.mark.numerics
def test_fourier_residual_detects_a_harmonic_above_the_fit_order() -> None:
    samples = 12
    low = [math.cos(2.0 * math.radians(30.0 * k)) for k in range(samples)]
    high = [math.cos(5.0 * math.radians(30.0 * k)) for k in range(samples)]
    assert doe.fourier_fit_residual(low) < 1e-9
    assert doe.fourier_fit_residual(high) > 0.5


# ── P1: запас плана, статус ROM, интерфейс DOE_2 ─────────────────────────

@pytest.mark.implementation
def test_plan_margin_is_a_hint_not_an_error() -> None:
    plan = doe.electrical_period_plan(P, 12, max_harmonic=5)
    assert plan.issues() == []
    assert plan.recommended_points == 24
    assert plan.warnings() and "24" in plan.warnings()[0]
    assert doe.electrical_period_plan(P, 24, max_harmonic=5).warnings() == []


@pytest.mark.physics
def test_zero_state_accepts_pm_flux_backed_by_energy() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_f=0.02)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    energy = summary["gates"]["zero_state_energy_consistency"]
    assert energy["status"] == "PASS"                     # psi(0) != 0 — это норма
    assert summary["zero_state"]["psi0_dq0_mean"][0] == pytest.approx(0.02, rel=1e-6)
    ripple = summary["gates"]["zero_state_angular_ripple"]
    assert ripple["status"] == "PASS"
    assert max(ripple["pp_rel"]) < 1e-3
    assert summary["gates"]["field_consistency"]["status"] == "PASS"
    assert summary["rom_status"] == "READY"


@pytest.mark.implementation
def test_zero_state_flags_offset_not_backed_by_energy() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    results = model.results(cases, IR)
    center = next(case for case in cases
                  if case.kind == "base" and case.norm2 == 0.0
                  and case.theta_e_deg == 0.0)
    results[center.case_id]["psi_U"] += 2e-3
    _, summary = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["zero_state_energy_consistency"]["status"] == "FAIL"
    assert summary["rom_status"] != "READY"


@pytest.mark.implementation
def test_zero_state_uses_eps_psi_1e3_and_reports_step_refinement() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_f=0.02)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    results = model.results(cases, IR)
    _, clean = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    energy = clean["gates"]["zero_state_energy_consistency"]
    assert energy["eps_psi"] == 1e-3
    assert energy["step_refinement"]["status"] == "PASS"
    scale = energy["tolerance"] / 1e-3
    center = next(case for case in cases
                  if case.kind == "base" and case.norm2 == 0.0
                  and case.theta_e_deg == 0.0)
    results[center.case_id]["psi_U"] += 3e-3 * scale      # прошло бы при 1e-2
    _, summary = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["zero_state_energy_consistency"]["status"] == "FAIL"


@pytest.mark.implementation
def test_psi_floor_enters_the_zero_state_scale() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_f=0.02)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=10.0)
    energy = summary["gates"]["zero_state_energy_consistency"]
    assert energy["tolerance"] == pytest.approx(1e-3 * 10.0)
    assert energy["psi_floor"] == 10.0


@pytest.mark.diagnostic
def test_angular_ripple_is_a_warning_not_a_blocker() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_cog=2e-3)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    assert summary["gates"]["zero_state_energy_consistency"]["status"] == "PASS"
    ripple = summary["gates"]["zero_state_angular_ripple"]
    assert ripple["status"] == "WARN"                      # пульсация — физика
    assert max(ripple["pp_rel"]) > ripple["threshold"]
    assert "zero_state_angular_ripple" in summary["warnings"]
    assert "zero_state_angular_ripple" not in summary["blocking_gates"]
    assert summary["rom_status"] == "READY"


@pytest.mark.implementation
def test_doe2_skeleton_is_not_a_pass(tmp_path: Path) -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="axes", with_loops=False)
    rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    out = tmp_path / "doe2.csv"
    written = doe.write_doe2_skeleton(out, rows, P)
    assert written == summary["coverage"]["complete"]
    with out.open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    assert records and records[0]["case_id"]
    for record in records:
        assert record["pwm_feasible"] == "NOT_EVALUATED"
        for key in ("uU_required_V", "uV_required_V", "uW_required_V", "Vdc_V",
                    "duty_U1", "duty_U2", "voltage_margin", "omega_m_rad_s"):
            assert record[key] in ("", None), key


# ── сценарии и CLI ───────────────────────────────────────────────────────

@pytest.mark.diagnostic
def test_scenario_a_when_zero_axis_is_inert() -> None:
    _, (rows, _) = run_model(doe.Synthetic(pole_pairs=P, kd0=0.0, c6=0.0))
    scenarios = {row["scenario"] for row in rows if not math.isnan(row["Ldd"])}
    assert scenarios == {"A"}


@pytest.mark.diagnostic
def test_scenario_c_when_zero_axis_makes_torque() -> None:
    _, (rows, _) = run_model(doe.Synthetic(pole_pairs=P, kd0=2e-5))
    scenarios = [row["scenario"] for row in rows
                 if not math.isnan(row["Ldd"]) and abs(row["Iq_A"]) > 1.0]
    assert scenarios
    assert "C" in scenarios


@pytest.mark.implementation
def test_cli_grid_writes_manifest(tmp_path: Path) -> None:
    out = tmp_path / "cases.csv"
    assert doe.main(["grid", "--out", str(out), "--ir", str(IR),
                     "--pole-pairs", str(P), "--no-torque"]) == 0
    assert out.is_file()
    manifest = (tmp_path / "cases.manifest.json").read_text(encoding="utf-8")
    assert '"points": 33' in manifest
    assert '"base": 396' in manifest
    assert '"harmonics_resolved": 5' in manifest


@pytest.mark.implementation
def test_cross_oracle_not_verified_without_c6() -> None:
    _, (_, summary) = run_model(doe.Synthetic(pole_pairs=P), stencil="axes")
    gate = summary["gates"]["cross_derivative_oracle"]
    assert gate["status"] == "NOT_VERIFIED"
    assert "c6" in gate["note"]


@pytest.mark.implementation
def test_cross_oracle_not_verified_at_twelve_angles() -> None:
    """6-я гармоника на 12 углах — предел схемы, а не FAIL и не PASS."""
    model = doe.Synthetic(pole_pairs=P, c6=1e-3)
    cases = doe.build_cases(IR, P, 30.0, stencil="axes")
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR, c6=1e-3)
    gate = summary["gates"]["cross_derivative_oracle"]
    assert gate["status"] == "NOT_VERIFIED"
    assert gate["harmonics_resolved"] == 5
    assert gate["alias_ratio"] is not None
    assert 0.0 < gate["alias_ratio"] < 1.0


@pytest.mark.implementation
def test_cross_oracle_passes_with_resolved_sixth_harmonic() -> None:
    model = doe.Synthetic(pole_pairs=P, c6=1e-3)
    cases = doe.build_cases(IR, P, 22.5, stencil="all")   # 16 углов → 7 гармоник
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR, c6=1e-3)
    gate = summary["gates"]["cross_derivative_oracle"]
    assert gate["harmonics_resolved"] >= 6
    assert gate["status"] == "PASS", gate
    assert gate["worst_rel"] < 0.02
    assert summary["gates"]["cross_derivative_symmetry"]["status"] == "PASS"


@pytest.mark.numerics
def test_cross_symmetry_ignores_nodes_below_noise_scale() -> None:
    """Относительная ошибка на величине ~1e-18 — шум, а не расхождение."""
    records = [
        {"case_id": "tiny", "norm_L_theta": 1e-18, "norm_M_i": 1e-18,
         "rel": 1.5, "abs_max": 1e-18, "rms": 1e-18,
         "measured_Mq_iq": 0.0, "oracle_Mq_iq": 0.0},
        {"case_id": "big", "norm_L_theta": 6e-3, "norm_M_i": 6e-3,
         "rel": 1e-13, "abs_max": 1e-18, "rms": 1e-18,
         "measured_Mq_iq": 6e-3, "oracle_Mq_iq": 6e-3},
    ]
    cross = {"records": records, "nodes": 2, "worst_rel": 1.5,
             "worst_case": "tiny", "abs_max": 1e-18, "rms": 1e-18,
             "oracle_max": 6e-3}
    gate = doe._cross_symmetry_gate(cross, 0.05)
    assert gate["status"] == "PASS"
    assert gate["significant_nodes"] == 1


@pytest.mark.implementation
def test_cross_symmetry_detects_real_mismatch() -> None:
    records = [{"case_id": "bad", "norm_L_theta": 6e-3, "norm_M_i": 6e-3,
                "rel": 0.5, "abs_max": 1e-3, "rms": 1e-3,
                "measured_Mq_iq": 3e-3, "oracle_Mq_iq": 6e-3}]
    cross = {"records": records, "nodes": 1, "worst_rel": 0.5,
             "worst_case": "bad", "abs_max": 1e-3, "rms": 1e-3,
             "oracle_max": 6e-3}
    assert doe._cross_symmetry_gate(cross, 0.05)["status"] == "FAIL"


@pytest.mark.implementation
def test_physics_coverage_is_independent_of_c_rom() -> None:
    """C_ROM = 1.0 сама по себе не даёт PASS по кросс-производным."""
    cases = doe.build_cases(IR, P, 22.5, stencil="all")
    _, summary = doe.analyse(cases, doe.Synthetic(pole_pairs=P).results(cases, IR), IR, P, psi_floor=PSI_FLOOR)
    physics = summary["physics_coverage"]
    assert summary["C_ROM"] == 1.0
    assert physics["status"] == "NOT_VERIFIED"
    assert physics["gates"]["angular_harmonics_resolved"] == "PASS"
    assert physics["gates"]["cross_derivative_oracle"] == "NOT_VERIFIED"
    assert summary["rom_status"] == "READY"


@pytest.mark.numerics
def test_oracle_uses_absolute_tolerance_near_zero() -> None:
    """|oracle| < eps ⇒ абсолютный допуск, а не деление на ~0."""
    records = [
        {"case_id": "zero", "norm_L_theta": 0.0, "norm_M_i": 0.0, "rel": 0.0,
         "abs_max": 0.0, "rms": 0.0, "measured_Mq_iq": 1e-12, "oracle_Mq_iq": 0.0},
        {"case_id": "big", "norm_L_theta": 6e-3, "norm_M_i": 6e-3, "rel": 0.0,
         "abs_max": 0.0, "rms": 0.0, "measured_Mq_iq": 6e-3, "oracle_Mq_iq": 6e-3},
    ]
    cross = {"records": records, "nodes": 2, "worst_rel": 0.0, "worst_case": "big",
             "abs_max": 0.0, "rms": 0.0, "oracle_max": 6e-3}
    gate = doe._cross_oracle_gate(cross, 1e-3, 24)
    assert gate["status"] == "PASS"
    assert gate["absolute_nodes"] == 1
    assert gate["relative_nodes"] == 1
    assert gate["eps_abs"] == pytest.approx(6e-9)
    records[0]["measured_Mq_iq"] = 1e-6      # >> eps_abs = 6e-9
    assert doe._cross_oracle_gate(cross, 1e-3, 24)["status"] == "FAIL"


@pytest.mark.implementation
def test_harmonics_gate_requires_sixth() -> None:
    assert doe._harmonics_gate(5)["status"] == "NOT_VERIFIED"
    assert doe._harmonics_gate(11)["status"] == "PASS"


@pytest.mark.implementation
def test_default_cross_parameters_preserve_baseline_output() -> None:
    """Умолчания (c6 = psi_cog = swirl = 0) не меняют ни строки, ни summary."""
    def report(model: doe.Synthetic):
        cases = doe.build_cases(IR, P, 30.0, deltas=(0.05, 0.025), stencil="axes",
                                with_torque=False, with_loops=False)
        rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=PSI_FLOOR,
                                    deltas=(0.05, 0.025))
        return rows, summary

    base_rows, base = report(doe.Synthetic(pole_pairs=P))
    zero_rows, zero = report(doe.Synthetic(pole_pairs=P, c6=0.0, psi_cog=0.0, swirl=0.0))
    # Сравнение через JSON: в строках есть nan, а nan != nan при разных объектах.
    assert (json.dumps(base_rows, sort_keys=True, default=str)
            == json.dumps(zero_rows, sort_keys=True, default=str))
    assert (json.dumps(base, sort_keys=True, default=str)
            == json.dumps(zero, sort_keys=True, default=str))
    assert base["C_ROM"] < 1.0
    assert base["physics_coverage"]["status"] == "NOT_VERIFIED"
    assert base["gates"]["cross_derivative_oracle"]["status"] == "NOT_VERIFIED"
    assert base["gates"]["cross_derivative_symmetry"]["status"] == "PASS"


# ── P2-FOLLOWUP: psi_floor из manifest, field_consistency, A0 ───────────

RUN_CARD = Path(__file__).resolve().parents[1] / "docs" / "tz" / "POC1_RUN_CARD_V0_3.md"


@pytest.mark.implementation
def test_without_psi_floor_eps_psi_gates_are_not_evaluated() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_f=0.02)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    _, summary = doe.analyse(cases, model.results(cases, IR), IR, P)
    for name in ("field_consistency", "zero_state_energy_consistency"):
        gate = summary["gates"][name]
        assert gate["status"] == "NOT_EVALUATED", name
        assert gate["blocking"] == ["psi_floor"]
        assert name in summary["blocking_gates"]
    assert summary["rom_status"] != "READY"


@pytest.mark.implementation
def test_field_consistency_uses_the_worst_node_not_the_median() -> None:
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, stencil="all")
    results = model.results(cases, IR)
    _, clean = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    gate = clean["gates"]["field_consistency"]
    assert gate["status"] == "PASS"
    node = next(case for case in cases if case.kind == "base" and case.norm2 > 0.0)
    results[node.case_id]["psi_U"] += 5e-3 * gate["psi_scale"]
    _, summary = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR)
    broken = summary["gates"]["field_consistency"]
    assert broken["median_eps_psi_node"] < 1e-3          # медиана бы пропустила
    assert broken["status"] == "FAIL"


@pytest.mark.implementation
def test_psi_floor_is_read_from_the_grid_manifest(tmp_path: Path) -> None:
    out = tmp_path / "cases.csv"
    assert doe.load_psi_floor(out) == (None, "нет cases.manifest.json")
    assert doe.main(["grid", "--out", str(out), "--ir", str(IR), "--pole-pairs", str(P),
                     "--no-loops"]) == 0
    value, source = doe.load_psi_floor(out)
    assert value is None and "не задан" in source
    assert doe.main(["grid", "--out", str(out), "--ir", str(IR), "--pole-pairs", str(P),
                     "--no-loops", "--psi-floor", "2.5e-4"]) == 0
    assert doe.load_psi_floor(out) == (2.5e-4, "cases.manifest.json")


@pytest.mark.implementation
def test_run_card_lists_19_unique_cases_including_zero_t040() -> None:
    ids = doe.run_card_case_ids(RUN_CARD)
    assert len(ids) == 19 == len(set(ids))
    assert "POC1_1_zero_t040" in ids


def _write_cases(directory: Path, ids) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, case_id in enumerate(ids, start=1):
        (directory / f"case_{index:03d}.json").write_text(
            json.dumps({"schema": "oew6t-fem-case/v0.1", "case_id": case_id}),
            encoding="utf-8")


@pytest.mark.integration
def test_case_set_gate_detects_missing_extra_and_duplicate_files(tmp_path: Path) -> None:
    ids = doe.run_card_case_ids(RUN_CARD)
    assert doe.poc_case_set(tmp_path / "none", RUN_CARD)["status"] == "NOT_EVALUATED"

    exact = tmp_path / "exact"
    _write_cases(exact, list(reversed(ids)))                # порядок не важен
    assert doe.poc_case_set(exact, RUN_CARD)["status"] == "PASS"

    missing = tmp_path / "missing"
    _write_cases(missing, [case for case in ids if case != "POC1_1_zero_t040"])
    report = doe.poc_case_set(missing, RUN_CARD)
    assert report["status"] == "FAIL" and report["missing"] == ["POC1_1_zero_t040"]
    assert not (missing / "case_019.json").exists()        # gate не создаёт файлы

    stale = tmp_path / "stale"
    _write_cases(stale, ids + ["POC1_old_removed"])
    report = doe.poc_case_set(stale, RUN_CARD)
    assert report["status"] == "FAIL" and report["extra"] == ["POC1_old_removed"]

    twice = tmp_path / "twice"
    _write_cases(twice, ids + [ids[0]])
    report = doe.poc_case_set(twice, RUN_CARD)
    assert report["status"] == "FAIL" and ids[0] in report["duplicates"]

    unnamed = tmp_path / "unnamed"
    _write_cases(unnamed, ids)
    (unnamed / "case_999.json").write_text("{}", encoding="utf-8")
    assert doe.poc_case_set(unnamed, RUN_CARD)["without_case_id"] == ["case_999.json"]


# ── P2-N1: диагональ L_inc в UVW по якобиану DOE-1 ─────────────────────

def _n1_summary(model, psi_delta_floor=0.0, bh="LINEAR_MATERIAL", drop=None,
                results_hook=None):
    cases = doe.build_cases(IR, P, 30.0, stencil="all", with_loops=False)
    results = model.results(cases, IR)
    if drop is not None:
        results.pop(next(case.case_id for case in cases if case.kind == drop))
    if results_hook is not None:
        results_hook(cases, results)
    _, summary = doe.analyse(cases, results, IR, P, psi_floor=PSI_FLOOR,
                             bh_range_status=bh, psi_delta_floor=psi_delta_floor)
    return summary["poc2_nonlinear"]


def _n1(model, **kwargs):
    return _n1_summary(model, **kwargs)["P2_N1"]


@pytest.mark.physics
def test_uvw_self_inductance_matches_direct_phase_derivative() -> None:
    model = doe.Synthetic(pole_pairs=P, psi_f=0.02)
    theta = 37.0
    uvw = doe.dq0_to_uvw(0.3 * IR, 0.5 * IR, 0.2 * IR, math.radians(theta))
    row = {"L" + key: value for key, value in model.analytic_l(uvw, theta).items()}
    diagonal = doe.uvw_self_inductances(row, theta)
    step = 1e-4
    for phase in range(3):
        plus, minus = list(uvw), list(uvw)
        plus[phase] += step
        minus[phase] -= step
        direct = (model.flux_uvw(plus, theta)[phase]
                  - model.flux_uvw(minus, theta)[phase]) / (2.0 * step)
        assert diagonal[phase] == pytest.approx(direct, rel=1e-6)


@pytest.mark.physics
@pytest.mark.parametrize("psi_f", [0.0, 0.02], ids=["no_pm", "pm"])
def test_p2_n1_passes_for_a_positive_self_inductance_model(psi_f) -> None:
    """С бюджетом приращения сильный PM (psi_f >> L*I) не делает гейт неразрешимым."""
    gate = _n1(doe.Synthetic(pole_pairs=P, psi_f=psi_f))
    assert gate["status"] == "PASS", gate["blocking"]
    assert gate["counts"]["FAIL"] == gate["counts"]["DIAGNOSTIC"] == 0
    assert gate["counts"]["PASS"] == 3 * 33 * 12
    assert gate["L_min_H"] == gate["worst"]["L_aa_H"] > gate["worst"]["delta_L_H"]
    assert gate["field_increment_consistency"]["status"] == "PASS"
    assert gate["angle_grid_status"] == "HYPOTHESIS_P"


@pytest.mark.physics
def test_pm_flux_does_not_enter_the_increment_scale() -> None:
    without = _n1(doe.Synthetic(pole_pairs=P))
    with_pm = _n1(doe.Synthetic(pole_pairs=P, psi_f=0.02))
    assert with_pm["psi_delta_scale_Wb"] == pytest.approx(without["psi_delta_scale_Wb"],
                                                          rel=1e-9)
    assert with_pm["e_Wb"] == pytest.approx(without["e_Wb"], rel=1e-9)


@pytest.mark.physics
def test_p2_n1_fails_on_negative_differential_slope() -> None:
    gate = _n1(doe.Synthetic(pole_pairs=P, a4=-2e-5))     # L_dd < 0 при |i_d| ~ I_r
    assert gate["status"] == "FAIL"
    assert gate["L_min_H"] < -gate["worst"]["delta_L_H"]
    assert gate["worst"]["status"] == "FAIL"
    assert {"phase", "theta_e_deg", "I_U_A", "I_V_A", "I_W_A",
            "stencil_d", "stencil_q", "stencil_0"} <= set(gate["worst"])


@pytest.mark.implementation
def test_p2_n1_is_not_passed_without_bh_range_or_psi_delta_floor() -> None:
    gate = _n1(doe.Synthetic(pole_pairs=P), bh=None)
    assert gate["status"] == "NOT_EVALUATED"
    assert gate["blocking"] == ["BH_RANGE_UNPROVEN"]
    assert gate["counts"]["PASS"] == 3 * 33 * 12
    gate = _n1(doe.Synthetic(pole_pairs=P), psi_delta_floor=None)
    assert gate["status"] == "NOT_EVALUATED"
    assert "field_increment_consistency" in gate["blocking"]
    assert gate["e_Wb"] is None


@pytest.mark.implementation
def test_p2_n1_flags_incomplete_rom_domain() -> None:
    gate = _n1(doe.Synthetic(pole_pairs=P), drop="stencil")
    assert gate["status"] == "NOT_EVALUATED"
    assert "ROM_DOMAIN_INCOMPLETE" in gate["blocking"]
    assert gate["nodes_incomplete"] >= 1


@pytest.mark.implementation
def test_p2_n1_threshold_uses_stencil_weights_and_transform() -> None:
    gate = _n1(doe.Synthetic(pole_pairs=P, psi_f=0.02), psi_delta_floor=0.05)
    assert gate["e_Wb"] == pytest.approx(1e-3 * 0.05)
    delta_i = doe.DEFAULT_DELTAS[0] * IR
    for record in gate["records"]:
        theta = math.radians(record["theta_e_deg"])
        column = doe.uvw_to_dq0(*[1.0 if p == record["phase"] else 0.0 for p in "UVW"],
                                theta)
        weights = [doe.STENCIL_WEIGHT[record["stencil_" + axis]] / delta_i
                   for axis in "dq0"]
        expected = (sum(abs(c) for c in column)
                    * sum(abs(c) * w for c, w in zip(column, weights)) * gate["e_Wb"])
        assert record["delta_L_H"] == pytest.approx(expected, rel=1e-12)
    assert doe.STENCIL_WEIGHT == {"central": 1.0, "fwd2": 4.0, "bwd2": 4.0}


@pytest.mark.implementation
def test_constant_export_offset_cancels_in_increment_consistency() -> None:
    def offset(cases, results):
        for case in cases:
            if case.theta_e_deg == 0.0 and case.case_id in results:
                results[case.case_id]["psi_U"] += 1e-3        # одинаково по току
    poc2 = _n1_summary(doe.Synthetic(pole_pairs=P, psi_f=0.02), results_hook=offset)
    assert poc2["field_increment_consistency"]["status"] == "PASS"
    assert poc2["P2_N1"]["status"] == "PASS"


@pytest.mark.implementation
def test_current_dependent_error_blocks_p2_n1_but_keeps_fail() -> None:
    def corrupt(cases, results):
        node = next(case for case in cases if case.kind == "base" and case.norm2 > 0.0)
        results[node.case_id]["psi_U"] += 1e-4
    poc2 = _n1_summary(doe.Synthetic(pole_pairs=P), results_hook=corrupt)
    assert poc2["field_increment_consistency"]["status"] == "FAIL"
    assert poc2["P2_N1"]["status"] == "NOT_EVALUATED"
    assert "field_increment_consistency" in poc2["P2_N1"]["blocking"]
    failing = _n1_summary(doe.Synthetic(pole_pairs=P, a4=-2e-5), results_hook=corrupt)
    assert failing["field_increment_consistency"]["status"] == "FAIL"
    assert failing["P2_N1"]["status"] == "FAIL"          # отрицательный наклон не скрыт


# ── P2-N2: differential reciprocity ─────────────────────────────────────────

def _n2_row_from_uvw(matrix_uvw, theta_deg=30.0, case_id="X"):
    """Строка с L_dq0, дающим заданную матрицу L_UVW (T ортонормирована)."""
    theta = math.radians(theta_deg)
    cols = [doe.uvw_to_dq0(*unit, theta)
            for unit in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))]
    row = {"case_id": case_id, "theta_e_deg": theta_deg,
           "stencil_d": "central", "stencil_q": "central", "stencil_0": "central"}
    axes = "dq0"
    for m in range(3):
        for n in range(3):
            row["L" + axes[m] + axes[n]] = sum(
                cols[a][m] * matrix_uvw[a][b] * cols[b][n]
                for a in range(3) for b in range(3))
    return row


def _n2_increment(scale=1e-3, floor=0.0, status="PASS"):
    return {"status": status, "value": 0.0,
            "psi_delta_scale_Wb": scale, "psi_delta_floor_Wb": floor}


def _n2_tolerance(delta_i, scale, floor, theta_deg, a, b):
    """Независимое вычисление delta_rec,ab по формуле контракта §4.3."""
    theta = math.radians(theta_deg)
    cols = {p: doe.uvw_to_dq0(*[1.0 if x == p else 0.0 for x in "UVW"], theta)
            for p in "UVW"}
    e = doe.EPS_PSI * max(scale, floor)
    w = 1.0 / delta_i

    def e_l(x, y):
        return (sum(abs(c) for c in cols["UVW"[x]])
                * sum(abs(c) * w for c in cols["UVW"[y]]) * e)
    return e_l(a, b) + e_l(b, a)


def _symmetric_uvw():
    return [[1e-4, 2e-5, 1e-5],
            [2e-5, 1.2e-4, 1.5e-5],
            [1e-5, 1.5e-5, 0.8e-4]]


@pytest.mark.implementation
def test_n2_smoke_symmetric_dq0_gives_symmetric_uvw() -> None:
    matrix = [[1e-4, 2e-5, 3e-5], [2e-5, 1.1e-4, 4e-5], [3e-5, 4e-5, 0.9e-4]]
    row = {"theta_e_deg": 47.0}
    for m in range(3):
        for n in range(3):
            row["L" + "dq0"[m] + "dq0"[n]] = matrix[m][n]
    uvw = doe.uvw_inductance_matrix(row, 47.0)
    for a in range(3):
        for b in range(3):
            assert uvw[a][b] == pytest.approx(uvw[b][a], abs=1e-18)


@pytest.mark.physics
def test_n2_passes_for_exactly_symmetric_jacobian() -> None:
    rows = [_n2_row_from_uvw(_symmetric_uvw())]
    gate = doe.p2_n2_gate(rows, 1.0, _n2_increment(), expected_nodes=1)
    assert gate["status"] == "PASS"
    assert gate["pass_count"] == 1 and gate["max_abs_asymmetry_H"] == pytest.approx(0.0)


@pytest.mark.physics
def test_n2_fails_on_artificial_uv_asymmetry() -> None:
    matrix = _symmetric_uvw()
    matrix[0][1] += 1e-3                              # L_UV != L_VU
    rows = [_n2_row_from_uvw(matrix)]
    gate = doe.p2_n2_gate(rows, 1.0, _n2_increment(), expected_nodes=1)
    assert gate["status"] == "FAIL"
    assert gate["worst_pair"] == "UV"
    assert gate["max_abs_asymmetry_H"] == pytest.approx(1e-3)
    assert gate["records"][0]["status"] == "FAIL"
    assert gate["records"][0]["worst_pair"] == "UV"
    for key in ("L_UV_H", "L_VU_H", "delta_L_UV_H", "eps_rec_UV"):
        assert key in gate["records"][0]


@pytest.mark.implementation
def test_n2_tolerance_boundary() -> None:
    delta_i, scale, floor, theta = 0.5, 2e-3, 1e-4, 30.0
    tol = _n2_tolerance(delta_i, scale, floor, theta, 0, 1)
    for factor, expected in ((1.0, "PASS"), (1.0 + 1e-9, "FAIL")):
        matrix = _symmetric_uvw()
        matrix[0][1] += tol * factor                  # ровно порог / порог + eps
        rows = [_n2_row_from_uvw(matrix, theta_deg=theta)]
        gate = doe.p2_n2_gate(rows, delta_i, _n2_increment(scale, floor),
                              expected_nodes=1)
        assert gate["records"][0]["status"] == expected, factor
        assert gate["records"][0]["delta_L_UV_H"] == pytest.approx(tol, rel=1e-9)


@pytest.mark.implementation
def test_n2_not_evaluated_without_budget_or_domain() -> None:
    rows = [_n2_row_from_uvw(_symmetric_uvw())]
    gate = doe.p2_n2_gate(rows, 1.0, _n2_increment(floor=None), expected_nodes=1)
    assert gate["status"] == "NOT_EVALUATED"
    assert "psi_delta_floor" in gate["blocking"]
    assert gate["records"][0]["status"] == "NOT_EVALUATED"
    gate = doe.p2_n2_gate(rows, 1.0, _n2_increment(status="FAIL"), expected_nodes=1)
    assert gate["status"] == "NOT_EVALUATED"
    assert "field_increment_consistency" in gate["blocking"]
    assert gate["records"][0]["status"] == "PASS"      # точки оцениваются по бюджету
    gate = doe.p2_n2_gate(rows, 1.0, _n2_increment(), expected_nodes=2)
    assert "ROM_DOMAIN_INCOMPLETE" in gate["blocking"]


@pytest.mark.physics
@pytest.mark.parametrize("psi_f", [0.0, 0.02], ids=["no_pm", "pm"])
def test_p2_n2_passes_on_the_synthetic_model(psi_f) -> None:
    gate = _n1_summary(doe.Synthetic(pole_pairs=P, psi_f=psi_f))["P2_N2"]
    assert gate["status"] == "PASS", gate["blocking"]
    assert gate["pass_count"] == 33 * 12
    assert gate["fail_count"] == 0


@pytest.mark.physics
def test_p2_n2_fails_on_current_dependent_export_error() -> None:
    """Ошибка в одном stencil-узле ломает симметрию якобиана сверх допуска."""
    def corrupt(cases, results):
        node = next(c for c in cases if c.kind == "stencil"
                    and c.axis == "q" and abs(c.step_mult) == 1)
        results[node.case_id]["psi_U"] += 3e-5
    poc2 = _n1_summary(doe.Synthetic(pole_pairs=P), results_hook=corrupt)
    assert poc2["P2_N2"]["fail_count"] >= 1
    assert poc2["P2_N2"]["status"] == "FAIL"



# ── P2-N3: cross-scale integral consistency ─────────────────────────────────

def _n3(model, **kwargs):
    return _n1_summary(model, **kwargs)["P2_N3"]


@pytest.mark.physics
def test_n3_passes_for_linear_quadratic_coenergy() -> None:
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0))
    assert gate["status"] == "PASS", gate["blocking"]
    assert gate["pass_count"] == gate["lines_total"] == 396
    assert gate["max_residual_Wb"] < 1e-15
    assert gate["tolerance_Wb"] > 0.0


@pytest.mark.physics
def test_n3_passes_with_pm_and_zero_sequence_coupling() -> None:
    """PM не вычитается; линии вдоль 0-оси и coupling kd0 входят в проверку."""
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0, psi_f=0.02))
    assert gate["status"] == "PASS", gate["blocking"]
    axes = {rec["axis"] for rec in gate["records"]}
    assert axes == {"d", "q", "0"}                      # все три оси, включая 0
    zero_lines = [rec for rec in gate["records"] if rec["axis"] == "0"]
    assert zero_lines and all(rec["status"] == "PASS" for rec in zero_lines)
    assert all(rec["quadrature_method"] == "SIMPSON" for rec in gate["records"])


@pytest.mark.physics
def test_n3_passes_for_mild_nonlinearity_within_budget() -> None:
    """Слабая нелинейность: ненулевой, но допустимый residual — не vacuous PASS."""
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=-8e-9, b4=-1.28e-8))
    assert gate["status"] == "PASS", gate["blocking"]
    assert 0.0 < gate["max_residual_Wb"] < gate["tolerance_Wb"]


@pytest.mark.physics
def test_n3_fails_for_strong_nonlinearity_at_grid_scale() -> None:
    """Намеренный FAIL fixture: нелинейность усилена так, что на масштабе
    0.5·I_r локальный stencil-scale Jacobian не воспроизводит Δpsi в frozen
    budget (семантика §5.3; контракт и допуск под fixture не подстраиваются).
    Стандартная синтетика при IR=5 слабее budget и потому не годится в FAIL-
    fixture — это свойство сетки, а не контракта."""
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=-8e-6, b4=-1.28e-5))  # усиленная fixture
    assert gate["status"] == "FAIL"
    assert gate["fail_count"] > 0
    assert gate["max_normalized_residual"] > 1.0
    worst = gate["worst_line"]
    assert {"axis", "center", "theta_e_deg"} <= set(worst)


@pytest.mark.physics
def test_n3_detects_corrupted_stencil_flux() -> None:
    """Искажение psi в stencil-узле меняет локальный L, но не ψ в базовых
    узлах — интегральная согласованность ломается сверх budget."""
    def corrupt(cases, results):
        node = next(c for c in cases if c.kind == "stencil"
                    and c.axis == "d" and abs(c.step_mult) == 1)
        results[node.case_id]["psi_U"] += 2e-4
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0), results_hook=corrupt)
    assert gate["status"] == "FAIL"
    assert gate["fail_count"] >= 1


@pytest.mark.implementation
def test_n3_not_evaluated_without_budget_or_domain() -> None:
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0), psi_delta_floor=None)
    assert gate["status"] == "NOT_EVALUATED"
    assert "psi_delta_floor" in gate["blocking"]
    assert all(rec["status"] == "NOT_EVALUATED" for rec in gate["records"])
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0), drop="stencil")
    assert "ROM_DOMAIN_INCOMPLETE" in gate["blocking"]


def _n3_line_rows(l_center, delta_psi, h=5.0, theta=0.0):
    """Три коллинеарных узла вдоль d с заданными Ldd в центре и Δpsi_data."""
    rows = []
    for k, x in enumerate((-0.5, 0.0, 0.5)):
        row = {"case_id": f"N{k}", "theta_e_deg": theta,
               "xd": x, "xq": 0.0, "x0": 0.0,
               "psi_d": 0.0, "psi_q": 0.0, "psi_0": 0.0}
        for m in "dq0":
            for n in "dq0":
                row["L" + m + n] = 0.0
        rows.append(row)
    rows[0]["psi_d"], rows[2]["psi_d"] = -delta_psi / 2.0, delta_psi / 2.0
    rows[0]["Ldd"] = rows[2]["Ldd"] = 0.0
    rows[1]["Ldd"] = l_center
    return rows


@pytest.mark.implementation
def test_n3_tolerance_boundary() -> None:
    h, delta = 5.0, 1e-3
    e = 1e-6
    inc = {"status": "PASS", "value": 0.0,
           "psi_delta_scale_Wb": e / doe.EPS_PSI, "psi_delta_floor_Wb": 0.0}
    for factor, expected in ((1.0, "PASS"), (1.0 + 1e-6, "FAIL")):
        target = delta - e * factor                # residual = e·factor ровно
        l_center = 3.0 * target / (4.0 * h)
        rows = _n3_line_rows(l_center, delta, h)
        gate = doe.p2_n3_gate(rows, h, inc)
        assert gate["lines_total"] == 1
        assert gate["records"][0]["status"] == expected, (factor, gate["records"][0])
        assert gate["records"][0]["tolerance_Wb"] == pytest.approx(e, rel=1e-12)


@pytest.mark.implementation
def test_n3_artifact_schema() -> None:
    gate = _n3(doe.Synthetic(pole_pairs=P, a4=0.0, b4=0.0))
    rec = gate["records"][0]
    for key in ("theta_e_deg", "axis", "center", "case_id", "residual_d_Wb",
                "residual_q_Wb", "residual_0_Wb", "residual_norm_Wb",
                "tolerance_Wb", "quadrature_method", "grid_step_A",
                "quadrature_indicator_Wb", "status"):
        assert key in rec, key
    assert len(rec["case_id"]) == 3
    assert rec["grid_step_A"] == pytest.approx(0.5 * IR)
    for key in ("worst_case", "worst_line", "max_residual_Wb",
                "max_normalized_residual", "pass_count", "fail_count",
                "not_evaluated_count", "domain_status"):
        assert key in gate, key



@pytest.mark.implementation
def test_psi_delta_floor_is_written_to_the_grid_manifest(tmp_path: Path) -> None:
    out = tmp_path / "cases.csv"
    assert doe.main(["grid", "--out", str(out), "--ir", str(IR), "--pole-pairs", str(P),
                     "--no-loops", "--psi-delta-floor", "1e-5"]) == 0
    assert doe.load_manifest_field(out, "psi_delta_floor") == 1e-5


@pytest.mark.implementation
def test_bh_range_status_is_read_from_the_grid_manifest(tmp_path: Path) -> None:
    out = tmp_path / "cases.csv"
    assert doe.main(["grid", "--out", str(out), "--ir", str(IR), "--pole-pairs", str(P),
                     "--no-loops"]) == 0
    assert doe.load_manifest_field(out, "bh_range_status") is None
    manifest = out.with_suffix(".manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["bh_range_status"] = "LINEAR_MATERIAL"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert doe.load_manifest_field(out, "bh_range_status") == "LINEAR_MATERIAL"


# ── per-row torque data path (TZ_PHASE4) ──────────────────────────────────

def test_torque_row_cases_fixed_uvw_and_angles():
    """Per-row стенсил: 792 состояния, фиксированные UVW, сдвиг ±p·h_m эл."""
    cases = doe.build_cases(IR, P, 30.0, with_loops=False)
    tr = [c for c in cases if c.kind == "torque_row"]
    base = [c for c in cases if c.kind == "base"]
    assert len(tr) == 2 * len(base)
    sample_base = base[0]
    pair = [c for c in tr if c.group == sample_base.case_id]
    assert len(pair) == 2
    uvw_base = doe.dq0_to_uvw(sample_base.xd * IR, sample_base.xq * IR,
                              sample_base.x0 * IR, math.radians(sample_base.theta_e_deg))
    for member in pair:
        uvw_member = doe.dq0_to_uvw(member.xd * IR, member.xq * IR,
                                    member.x0 * IR, math.radians(member.theta_e_deg))
        for a, b in zip(uvw_base, uvw_member):
            assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)  # UVW фиксированы
    offsets = sorted(abs(c.theta_e_deg - sample_base.theta_e_deg) for c in pair)
    assert math.isclose(offsets[0], P * 0.5, rel_tol=1e-9)          # p * h_m = 1.5° эл
    assert math.isclose(offsets[1], P * 0.5, rel_tol=1e-9)


def _analyse_with_cogging(a_cog=1e-3, phi=0.3, **model_kwargs):
    model = doe.Synthetic(pole_pairs=P, a_cog=a_cog, phi_cog=phi, **model_kwargs)
    cases = doe.build_cases(IR, P, 30.0, with_loops=False)
    rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=0.0)
    return model, cases, rows, summary


def test_te_coenergy_present_and_matches_analytic():
    """Te_coenergy во всех строках; ошибка на уровне sinc-усечения h=0.5°."""
    model, cases, rows, summary = _analyse_with_cogging(psi_f=0.012)
    valued = [r for r in rows if not math.isnan(r["Te_coenergy"])]
    assert len(valued) == len(rows)
    worst = 0.0
    for row in valued:
        case = next(c for c in cases if c.case_id == row["case_id"] and c.kind == "base")
        exact = model.torque(case.currents(IR), case.theta_e_deg)
        scale = max(abs(exact), 1e-6)
        worst = max(worst, abs(row["Te_coenergy"] - exact) / scale)
    assert worst < 0.01                          # sinc(0.5°) ~ 0.4% + прочие гармоники
    assert summary["torque_rom"]["h_mech_deg"] == 0.5
    assert summary["torque_rom"]["te_coenergy_rows"] == len(rows)


def test_te_coenergy_cogging_nonzero_at_zero_current_with_sign():
    """При i=0 момент — ненулевой cogging с правильным знаком и величиной."""
    _, _, rows, _ = _analyse_with_cogging()
    zero_rows = [r for r in rows if abs(r["Id_A"]) < 1e-12
                 and abs(r["Iq_A"]) < 1e-12 and abs(r["I0_A"]) < 1e-12]
    assert zero_rows
    assert all(abs(r["Te_coenergy"]) > 1e-9 for r in zero_rows)
    row = next(r for r in zero_rows if r["theta_e_deg"] == 0.0)
    expected = -6.0 * P * 1e-3 * math.sin(0.3)            # -6p A6 sin(6 theta_e + phi)
    ratio = row["Te_coenergy"] / expected
    assert 0.99 < ratio < 1.0                            # sinc-затухание, знак совпадает


def test_te_coenergy_missing_without_torque_cases():
    """Без torque-состояний — отсутствующее значение, не ноль (контракт §3)."""
    model = doe.Synthetic(pole_pairs=P)
    cases = doe.build_cases(IR, P, 30.0, with_torque=False, with_loops=False)
    rows, summary = doe.analyse(cases, model.results(cases, IR), IR, P, psi_floor=0.0)
    assert rows
    assert all(math.isnan(r["Te_coenergy"]) for r in rows)
    assert summary["torque_rom"]["te_coenergy_rows"] == 0


def test_torque_row_states_do_not_enter_grid_products():
    """torque_row не участвуют в базовой сетке: число base-строк и углов неизменно."""
    cases = doe.build_cases(IR, P, 30.0, with_loops=False)
    base = [c for c in cases if c.kind == "base"]
    assert len(base) == 33 * 12
    assert all(c.kind != "torque_row" or c.group for c in cases)
