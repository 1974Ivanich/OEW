"""Тесты DOE_3: plant dq0 3x3, линеаризация и цифровой токовый контур BM1109.

Контракт: docs/tz/TZ_DOE3_CONTROL_CONTRACT_V0_1.md."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bm1109_ctrl as ctrl  # noqa: E402

P = 3
R = 0.1
TS = 1.0 / 5000.0
TD = ctrl.PWM_DELAY_FACTOR * TS
L_DD, L_DQ, L_QQ, L_00 = 2e-4, 1e-5, 3.2e-4, 8e-5
L_END_ZERO = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


def make_row(xd: int = 0, xq: int = 0, x0: int = 0, theta: float = 0.0,
             ir: float = 5.0, dpsi_d: float = 0.02, dpsi_q: float = 0.0,
             i0: float = 0.0) -> dict:
    """Строка ROM с линейной зависимостью psi = L i (тогда L_inc = L)."""
    i_d, i_q = xd * ir, xq * ir
    psi_d = L_DD * i_d + L_DQ * i_q
    psi_q = L_DQ * i_d + L_QQ * i_q
    return {
        "case_id": f"X{xd}{xq}{x0}T{theta:g}", "xd": xd, "xq": xq, "x0": x0,
        "theta_e_deg": theta, "Id_A": i_d, "Iq_A": i_q, "I0_A": i0,
        "Ldd": L_DD, "Ldq": L_DQ, "Lqd": L_DQ, "Lqq": L_QQ,
        "Ld0": 0.0, "Lq0": 0.0, "L0d": 0.0, "L0q": 0.0, "L00": L_00,
        "psi_d": psi_d, "psi_q": psi_q, "psi_0": L_00 * i0,
        "dpsi_d_dtheta_e": dpsi_d, "dpsi_q_dtheta_e": dpsi_q,
        "dpsi_0_dtheta_e": 0.0,
        "Te_coenergy": P * (psi_d * i_q - psi_q * i_d),
    }


def small_grid() -> list[dict]:
    rows = [make_row(0, 0), make_row(1, 0), make_row(-1, 0),
            make_row(0, 1), make_row(0, -1),
            make_row(0, 0, x0=1, i0=5.0), make_row(0, 0, x0=-1, i0=-5.0)]
    return rows


PARAMS = {"pole_pairs": P, "r_phase_ohm": R, "f_pwm_hz": 5000.0,
          "omega_c_rad_s": 300.0, "pm_target_deg": 45.0,
          "psi_reference": "ABSOLUTE", "l_sigma_end_uvw_h": L_END_ZERO}


def rom_with_n2(tmp_path: Path, status: str = "PASS") -> Path:
    rom = tmp_path / "L_dq0.csv"
    rom.write_text("case_id\n", encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps(
        {"poc2_nonlinear": {"P2_N2": {"status": status}}}), encoding="utf-8")
    return rom


def test_torque_source_is_coenergy_column_not_cross_product():
    row = make_row(0, 1)
    row["Te_coenergy"] = 1.234                        # отличается от cross-product
    assert ctrl.torque_source(row) == 1.234
    cross = P * (L_DQ * 5.0) * 5.0
    assert math.isclose(ctrl.torque_cross_product(row, P), cross, rel_tol=1e-12)
    del row["Te_coenergy"]
    assert ctrl.torque_source(row) is None            # нет колонки — NOT_EVALUATED


def test_plant_derivative_zero_at_rest_with_zero_voltage():
    derivative, torque = ctrl.plant_derivative(make_row(0, 0), (0.0, 0.0, 0.0),
                                               0.0, P, R, L_END_ZERO)
    assert derivative == (0.0, 0.0, 0.0)


def test_plant_derivative_uses_inverse_inductance_3x3():
    row = make_row(0, 0)
    derivative, _ = ctrl.plant_derivative(row, (1.0, 0.0, 0.0), 0.0, P, R, L_END_ZERO)
    inverse = ctrl.inv3([[L_DD, L_DQ, 0.0], [L_DQ, L_QQ, 0.0], [0.0, 0.0, L_00]])
    for k in range(3):
        assert math.isclose(derivative[k], inverse[k][0], rel_tol=1e-12)


def test_zero_sequence_current_is_a_state_variable():
    """Контракт §2: I0 не исключается; di0/dt = (u0 - R i0)/L00."""
    row = make_row(0, 0, x0=1, i0=5.0)
    derivative, _ = ctrl.plant_derivative(row, (0.0, 0.0, 2.0), 0.0, P, R, L_END_ZERO)
    assert math.isclose(derivative[2], (2.0 - R * 5.0) / L_00, rel_tol=1e-12)


def test_leakage_enters_plant_inductance():
    l_end = [[1e-4, 0.0, 0.0], [0.0, 1e-4, 0.0], [0.0, 0.0, 1e-4]]
    row = make_row(0, 0)
    derivative, _ = ctrl.plant_derivative(row, (0.0, 0.0, 1.0), 0.0, P, R, l_end)
    assert math.isclose(derivative[2], 1.0 / (L_00 + 1e-4), rel_tol=1e-9)


def test_phase_margin_identity_at_design_bandwidth():
    pm = ctrl.phase_margin(L_DD, R, 300.0, TD)
    assert pm is not None
    assert math.isclose(pm, 90.0 - math.degrees(300.0 * TD), abs_tol=0.05)


def test_bandwidth_ceiling_formula_and_monotonicity():
    ceiling = ctrl.bandwidth_ceiling(TD, 45.0)
    assert math.isclose(ceiling, (math.pi / 2 - math.radians(45.0)) / TD, rel_tol=1e-12)
    assert ctrl.bandwidth_ceiling(TD / 2, 45.0) > ceiling


def test_discrete_loop_stable_below_ceiling_and_unstable_far_above():
    stable = ctrl.closed_loop_poles(L_DD, R, 300.0, TS)
    unstable = ctrl.closed_loop_poles(L_DD, R, 20000.0, TS)
    assert max(abs(p) for p in stable) < 1.0
    assert max(abs(p) for p in unstable) > 1.0


def test_poly_roots_recover_known_roots():
    roots = sorted(ctrl.poly_roots([1.0, 1.0, -6.0]), key=lambda z: z.real)
    assert abs(roots[0] - (-3.0)) < 1e-9
    assert abs(roots[1] - 2.0) < 1e-9


def test_linearization_at_rest_equals_minus_inverse_l_times_r():
    rows = small_grid()
    index = ctrl.grid_index(rows)
    center = next(r for r in rows if r["case_id"] == "X000T0")
    a_matrix, method = ctrl.linearize_a(center, index, 5.0, 0.0, P, R, L_END_ZERO)
    assert a_matrix is not None, method
    inverse = ctrl.inv3([[L_DD, L_DQ, 0.0], [L_DQ, L_QQ, 0.0], [0.0, 0.0, L_00]])
    for i in range(3):
        for j in range(3):
            expected = -R * inverse[i][j]
            assert math.isclose(a_matrix[i][j], expected, rel_tol=1e-9, abs_tol=1e-9)


def test_eigen3_preserves_complex_pairs():
    a_matrix = [[-1.0, 10.0, 0.0], [-10.0, -1.0, 0.0], [0.0, 0.0, -2.0]]
    roots = ctrl.eigen3(a_matrix)
    assert len(roots) == 3
    assert any(abs(root.imag) > 1.0 for root in roots)
    pair = [root for root in roots if abs(root.imag) > 1.0]
    assert len(pair) == 2 and math.isclose(pair[0].real, -1.0, abs_tol=1e-9)


def test_l_inc_recovered_from_flux_nodes():
    rows = small_grid()
    index = ctrl.grid_index(rows)
    center = next(r for r in rows if r["case_id"] == "X000T0")
    fresh, _ = ctrl.l_from_flux(center, index, 5.0)
    assert fresh is not None
    assert math.isclose(fresh[0][0], L_DD, rel_tol=1e-12)
    assert math.isclose(fresh[0][1], L_DQ, rel_tol=1e-12)
    assert math.isclose(fresh[1][1], L_QQ, rel_tol=1e-12)


def test_zero_sequence_rows_are_included_not_excluded():
    rows = small_grid()
    points, summary = ctrl.analyse(rows, PARAMS)
    assert not any(p["status"] == "MODEL_EXCLUDED" for p in points)
    row0 = [p for p in points if abs(p.get("I0_A") or 0.0) > 1.0]
    assert len(row0) == 2                                     # обе строки ±5 А в работе
    assert summary["gates"]["i0_data_consistency"] == "PASS"


def test_analyse_passes_on_consistent_grid(tmp_path):
    rom = rom_with_n2(tmp_path)
    points, summary = ctrl.analyse(small_grid(), PARAMS, rom_path=rom)
    assert summary["status"] == "PASS_3", summary["gates"]
    assert summary["gates"]["phase_margin"] == "PASS"
    assert summary["gates"]["discrete_stability"] == "PASS"
    assert summary["gates"]["reciprocity_contract"] == "PASS"
    assert summary["gates"]["torque_source"] == "PASS"
    assert summary["gates"]["leakage_contract"] == "PASS"
    assert summary["gates"]["l_positive_definite"] == "DIAGNOSTIC"
    assert summary["gates"]["diagonal_approximation"] == "DIAGNOSTIC"
    assert "delay_budget" not in summary["gates"]
    assert "l2_symmetric" not in summary["gates"]
    assert len(points) == 7


def test_missing_contract_inputs_give_not_evaluated_not_pass(tmp_path):
    rom = rom_with_n2(tmp_path)
    no_leakage = dict(PARAMS)
    del no_leakage["l_sigma_end_uvw_h"]
    _, summary = ctrl.analyse(small_grid(), no_leakage, rom_path=rom)
    assert summary["status"] == "NOT_EVALUATED"
    assert "leakage_contract" in summary["contract_missing"]
    relative = dict(PARAMS, psi_reference="RELATIVE")
    _, summary = ctrl.analyse(small_grid(), relative, rom_path=rom)
    assert summary["status"] == "NOT_EVALUATED"
    assert "psi_reference_absolute" in summary["contract_missing"]
    rows = [dict(row, Te_coenergy=None) for row in small_grid()]
    for row in rows:
        del row["Te_coenergy"]
    _, summary = ctrl.analyse(rows, PARAMS, rom_path=rom)
    assert summary["status"] == "NOT_EVALUATED"
    assert "torque_source" in summary["contract_missing"]
    _, summary = ctrl.analyse(small_grid(), PARAMS)          # без summary.json ROM
    assert summary["gates"]["reciprocity_contract"] == "NOT_EVALUATED"


def test_bandwidth_above_ceiling_is_fail_not_pass(tmp_path):
    rom = rom_with_n2(tmp_path)
    fast = dict(PARAMS, omega_c_rad_s=4000.0, pm_target_deg=45.0)
    _, summary = ctrl.analyse(small_grid(), fast, rom_path=rom)
    assert summary["gates"]["bandwidth_ceiling"] == "FAIL"
    assert summary["gates"]["phase_margin"] == "FAIL"
    assert summary["status"] == "FAIL_3"


def test_nan_in_ldd_is_missing_value_not_number():
    row = make_row(0, 0)
    row["Ldd"] = float("nan")
    assert ctrl.row_complete(row) is False
    _, summary = ctrl.analyse([row], PARAMS)
    assert summary["gates"]["rom_fields_complete"] == "FAIL"
    assert summary["status"] == "FAIL_3"


def test_parametric_kp_ki_from_l_and_r():
    point = ctrl.design_point(make_row(0, 0), ctrl.grid_index(small_grid()), 5.0, PARAMS)
    assert math.isclose(point["Kp_d"], L_DD * 300.0, rel_tol=1e-12)
    assert math.isclose(point["Kp_q"], L_QQ * 300.0, rel_tol=1e-12)
    assert math.isclose(point["Ki_d"], R * 300.0, rel_tol=1e-12)
