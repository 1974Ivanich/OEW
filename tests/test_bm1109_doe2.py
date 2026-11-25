import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bm1109_doe1 as doe1
import bm1109_doe2 as doe2

P = 3.0
IR = 5.0
VDC = 48.0
R_PHASE = 0.05
ZERO3 = [[0.0] * 3 for _ in range(3)]
ABS = {"psi_reference": "ABSOLUTE", "l_sigma_end_uvw_h": ZERO3}


def rom_row(xd: float, xq: float, x0: float, theta: float = 0.0,
            model: doe1.Synthetic | None = None, case_id: str = "T0") -> dict:
    model = model or doe1.Synthetic(pole_pairs=P)
    uvw = doe1.dq0_to_uvw(xd * IR, xq * IR, x0 * IR, math.radians(theta))
    psi = doe1.uvw_to_dq0(*model.flux_uvw(uvw, theta), math.radians(theta))
    table = model.analytic_l(uvw, theta)
    row = {"case_id": case_id, "theta_e_deg": theta,
           "Id_A": xd * IR, "Iq_A": xq * IR, "I0_A": x0 * IR,
           "psi_d": psi[0], "psi_q": psi[1], "psi_0": psi[2],
           "dpsi_d_dtheta_e": 0.0, "dpsi_q_dtheta_e": 0.0,
           "dpsi_0_dtheta_e": 0.0}
    for key in ("dd", "dq", "d0", "qd", "qq", "q0", "0d", "0q", "00"):
        row["L" + key] = table[key]
    return row


@pytest.mark.physics
def test_voltage_matches_direct_differentiation_of_flux() -> None:
    model = doe1.Synthetic(pole_pairs=P, psi_f=0.02)
    row = rom_row(0.2, 0.3, 0.1, theta=37.0, model=model)
    calc = doe2.voltage(row, omega_m=100.0, r_phase=R_PHASE, pole_pairs=P)
    w_e, step = P * 100.0, 1e-6
    current = (0.2 * IR, 0.3 * IR, 0.1 * IR)

    def uvw_flux(theta_deg: float) -> tuple[float, float, float]:
        uvw = doe1.dq0_to_uvw(*current, math.radians(theta_deg))
        return model.flux_uvw(uvw, theta_deg)

    ahead, behind = uvw_flux(37.0 + math.degrees(w_e * step)), \
        uvw_flux(37.0 - math.degrees(w_e * step))
    i_uvw = doe1.dq0_to_uvw(*current, math.radians(37.0))
    direct = [(a - b) / (2.0 * step) + R_PHASE * i
              for a, b, i in zip(ahead, behind, i_uvw)]
    scale = max(abs(value) for value in direct)
    assert max(abs(a - b) for a, b in zip(direct, calc["u_uvw"])) < 1e-4 * scale


@pytest.mark.physics
def test_constant_dq0_vector_still_makes_phase_emf() -> None:
    model = doe1.Synthetic(pole_pairs=P, psi_f=0.02)
    row = rom_row(0.0, 0.0, 0.0, model=model)
    calc = doe2.voltage(row, omega_m=100.0, r_phase=R_PHASE, pole_pairs=P)
    # di_dq0/dt = 0, но ЭДС вращения есть: u_q = w_e * psi_f
    assert calc["u_dq0"][0] == pytest.approx(0.0, abs=1e-12)
    assert calc["u_dq0"][1] == pytest.approx(P * 100.0 * 0.02, rel=1e-9)
    assert calc["u_emf"] == pytest.approx(P * 100.0 * 0.02, rel=1e-9)
    assert calc["u_uvw"][0] + calc["u_uvw"][1] + calc["u_uvw"][2] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.physics
def test_two_bridges_give_the_requested_voltage() -> None:
    model = doe1.Synthetic(pole_pairs=P, psi_f=0.02)
    row = rom_row(0.3, 0.5, 0.0, theta=20.0, model=model)
    calc = doe2.voltage(row, omega_m=80.0, r_phase=R_PHASE, pole_pairs=P)
    split = doe2.pwm_split(calc["u_uvw"], VDC)
    for name, value in zip("UVW", calc["u_uvw"]):
        phase = split["phases"][name]
        assert VDC * (phase["d1"] - phase["d2"]) == pytest.approx(value, rel=1e-12)
        assert phase["d1"] + phase["d2"] == pytest.approx(1.0, rel=1e-12)


@pytest.mark.physics
def test_r_dq0_is_positive_definite_for_positive_phase_resistance() -> None:
    """R_U,R_V,R_W > 0 => R_dq0 симметрична И положительно определена."""
    rng = random.Random(3)
    for _ in range(40):
        r = tuple(rng.uniform(0.01, 0.5) for _ in range(3))
        theta = rng.uniform(0.0, 360.0)
        m = doe2.resistance_dq0(r, theta)
        minor = m[0][0] * m[1][1] - m[0][1] * m[1][0]
        trace = m[0][0] + m[1][1] + m[2][2]
        det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
               - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        # критерий Сильвестра: все главные миноры положительны
        assert m[0][0] > 0.0
        assert minor > 0.0
        assert det > 0.0
        assert trace == pytest.approx(sum(r), rel=1e-12)   # след инвариантен
        for i in range(3):
            for j in range(3):
                assert m[i][j] == pytest.approx(m[j][i], rel=1e-12, abs=1e-15)


@pytest.mark.implementation
def test_ideal_voltage_bound_is_vdc() -> None:
    assert doe2.pwm_split((0.9 * VDC, -0.5 * VDC, 0.0), VDC)["ideal_ok"]
    over = doe2.pwm_split((1.2 * VDC, 0.0, 0.0), VDC)
    assert not over["ideal_ok"]
    assert over["max_delta"] == pytest.approx(1.2)


@pytest.mark.implementation
def test_missing_limits_keep_pwm_not_evaluated() -> None:
    rom = {"B1": rom_row(0.2, 0.3, 0.0, model=doe1.Synthetic(pole_pairs=P, psi_f=0.02)),
           "B2": rom_row(0.4, 0.1, 0.0, theta=30.0,
                         model=doe1.Synthetic(pole_pairs=P, psi_f=0.02), case_id="B2")}
    _, summary = doe2.analyse(rom, omega_m=50.0, vdc=VDC, r_phase=R_PHASE,
                              pole_pairs=P, params={**ABS, "duty_min": 0.0, "duty_max": 1.0})
    assert summary["gates"]["pwm_feasible"]["status"] == "NOT_EVALUATED"
    assert "dead_time_s" in summary["gates"]["pwm_feasible"]["blocking"]
    assert summary["status"] == "INCOMPLETE"


@pytest.mark.implementation
def test_dead_time_and_min_pulse_shrink_the_duty_window() -> None:
    u = (0.95 * VDC, 0.0, 0.0)
    split = doe2.pwm_split(u, VDC, duty_min=0.0, duty_max=1.0, band=0.04)
    assert split["ideal_ok"]
    assert split["phases"]["U"]["margin"] < 0.0


@pytest.mark.physics
def test_power_coordinate_invariance_between_phase_and_dq0_frames() -> None:
    rom = {"B1": rom_row(0.2, 0.3, 0.1, theta=10.0,
                         model=doe1.Synthetic(pole_pairs=P, psi_f=0.02))}
    _, summary = doe2.analyse(rom, omega_m=90.0, vdc=VDC, r_phase=R_PHASE,
                              pole_pairs=P, params=ABS)
    gate = summary["gates"]["power_coordinate_invariance"]
    assert gate["status"] == "PASS"
    assert gate["worst_rel"] < 1e-12
    assert "power_balance" not in summary["gates"]


@pytest.mark.physics
def test_unequal_phase_resistances_use_the_full_matrix() -> None:
    r_phase = (0.05, 0.07, 0.06)
    theta = 30.0
    rom = {"B1": rom_row(0.3, 0.4, 0.2, theta=theta,
                         model=doe1.Synthetic(pole_pairs=P, psi_f=0.02))}
    # фазный расчёт: u_R = R_UVW i_UVW, затем в dq0
    current = (0.3 * IR, 0.4 * IR, 0.2 * IR)
    i_uvw = doe1.dq0_to_uvw(*current, math.radians(theta))
    u_phase = [r * i for r, i in zip(r_phase, i_uvw)]
    u_dq0_phase = doe1.uvw_to_dq0(*u_phase, math.radians(theta))
    # dq0-расчёт: R_dq0 * i_dq0
    table = doe2.resistance_dq0(r_phase, theta)
    u_dq0_matrix = tuple(sum(table[i][j] * current[j] for j in range(3))
                         for i in range(3))
    assert u_dq0_matrix == pytest.approx(u_dq0_phase, rel=1e-12)
    # и напряжение через ROM берёт именно эту матрицу
    calc = doe2.voltage(rom["B1"], omega_m=0.0, r_phase=r_phase, pole_pairs=P)
    assert calc["u_dq0"] == pytest.approx(u_dq0_matrix, rel=1e-12)
    # при разных R появляются перекрёстные члены: не диагональна
    assert abs(table[0][1]) > 1e-4


def _matvec(table, vector):
    return [sum(table[i][j] * vector[j] for j in range(3)) for i in range(3)]


@pytest.mark.implementation
def test_frame_matrix_matches_uvw_to_dq0() -> None:
    """T = Park*Clarke: столбец j — образ базисного вектора фазы j."""
    for theta in (-150.0, -37.0, 0.0, 21.5, 90.0, 175.0):
        table = doe2.frame_matrix(theta)
        for j in range(3):
            basis = [0.0, 0.0, 0.0]
            basis[j] = 1.0
            expected = doe1.uvw_to_dq0(*basis, math.radians(theta))
            assert [table[i][j] for i in range(3)] == pytest.approx(
                list(expected), rel=1e-15, abs=1e-15)


@pytest.mark.implementation
def test_r_dq0_is_an_independent_transformation() -> None:
    """Три условия P0 на случайных неравных R и разных углах.

    1. T u_R,UVW == R_dq0 i_dq0;
    2. i_UVW^T R_UVW i_UVW == i_dq0^T R_dq0 i_dq0;
    3. R_dq0 симметрична (до точности вычислений).
    """
    rng = random.Random(20261124)
    for _ in range(40):
        r_phase = tuple(rng.uniform(0.01, 0.15) for _ in range(3))
        theta = rng.uniform(-180.0, 180.0)
        i_dq0 = tuple(rng.uniform(-6.0, 6.0) for _ in range(3))
        angle = math.radians(theta)
        i_uvw = doe1.dq0_to_uvw(*i_dq0, angle)
        u_phase = [r * i for r, i in zip(r_phase, i_uvw)]
        u_transformed = doe1.uvw_to_dq0(*u_phase, angle)
        table = doe2.resistance_dq0(r_phase, theta)
        assert _matvec(table, i_dq0) == pytest.approx(list(u_transformed),
                                                     rel=1e-12, abs=1e-12)
        losses_phase = sum(r * i * i for r, i in zip(r_phase, i_uvw))
        losses_dq0 = sum(i * u for i, u in zip(i_dq0, _matvec(table, i_dq0)))
        assert losses_dq0 == pytest.approx(losses_phase, rel=1e-12, abs=1e-15)
        for a in range(3):
            for b in range(3):
                assert table[a][b] == pytest.approx(table[b][a],
                                                    rel=1e-12, abs=1e-15)


@pytest.mark.implementation
def test_r_dq0_reduces_to_scalar_for_equal_phases() -> None:
    table = doe2.resistance_dq0(0.07, 63.0)
    for a in range(3):
        for b in range(3):
            assert table[a][b] == pytest.approx(0.07 if a == b else 0.0,
                                                abs=1e-15)


@pytest.mark.implementation
def test_dead_time_accuracy_stays_not_evaluated() -> None:
    rom = {"B1": rom_row(0.2, 0.3, 0.0, model=doe1.Synthetic(pole_pairs=P, psi_f=0.02))}
    params = {"duty_min": 0.02, "duty_max": 0.98, "dead_time_s": 1e-6,
              "min_pulse_s": 1e-6, "pwm_period_s": 50e-6}
    rows, summary = doe2.analyse(rom, omega_m=50.0, vdc=VDC, r_phase=R_PHASE,
                                 pole_pairs=P, params={**ABS, **params})
    # уровень A — геометрия duty; уровень B (точность напряжения) не считается
    assert summary["gates"]["pwm_feasible"]["status"] == "PASS"
    assert summary["gates"]["dead_time_accuracy"]["status"] == "NOT_EVALUATED"
    assert rows[0]["pwm_level"] == "A"
    assert rows[0]["u_effective_U_V"] is None
    assert summary["status"] == "PASS_2A2B"


@pytest.mark.implementation
def test_full_limits_pass_and_missing_data_is_not_evaluated() -> None:
    model = doe1.Synthetic(pole_pairs=P, psi_f=0.02)
    rom = {"B1": rom_row(0.2, 0.3, 0.0, model=model),
           "B2": rom_row(0.4, 0.1, 0.0, theta=30.0, model=model, case_id="B2")}
    params = {"duty_min": 0.02, "duty_max": 0.98, "dead_time_s": 1e-6,
              "min_pulse_s": 1e-6, "pwm_period_s": 50e-6}
    rows, summary = doe2.analyse(rom, omega_m=50.0, vdc=VDC, r_phase=R_PHASE,
                                 pole_pairs=P, params={**ABS, **params})
    assert summary["gates"]["pwm_feasible"]["status"] == "PASS"
    assert summary["status"] == "PASS_2A2B"
    assert all(row["pwm_feasible"] == "PASS" for row in rows)

    rows, summary = doe2.analyse(rom, omega_m=50.0, vdc=None, r_phase=None,
                                 pole_pairs=P, params={**ABS, **params})
    assert summary["gates"]["ideal_voltage"]["status"] == "NOT_RUN"
    assert summary["status"] == "INCOMPLETE"
    assert all(row["pwm_feasible"] == "NOT_EVALUATED" for row in rows)


@pytest.mark.implementation
def test_incomplete_rom_row_blocks_the_run() -> None:
    good = rom_row(0.2, 0.3, 0.0, model=doe1.Synthetic(pole_pairs=P, psi_f=0.02))
    broken = dict(good)
    broken["case_id"] = "B2"
    broken["psi_q"] = ""
    _, summary = doe2.analyse({"B1": good, "B2": broken}, omega_m=10.0, vdc=VDC,
                              r_phase=R_PHASE, pole_pairs=P, params=ABS)
    assert summary["gates"]["rom_integrity"]["status"] == "FAIL"
    assert summary["status"] == "FAIL"
    assert "rom_integrity" in summary["blocking_gates"]


# ── фаза 2, пункт A: абсолютный psi и лобовые части ─────────────────────

L_CIRC = [[40e-6, -8e-6, -8e-6], [-8e-6, 40e-6, -8e-6], [-8e-6, -8e-6, 40e-6]]
L_UNEQUAL = [[40e-6, -8e-6, -5e-6], [-8e-6, 55e-6, -9e-6], [-5e-6, -9e-6, 35e-6]]


def _direct_phase_voltage(model, current, theta, omega_m, l_uvw):
    """u_UVW = R i + d/dt[psi_FEM,UVW + L_s i_UVW] вдоль вращения при const i_dq0."""
    w_e = P * omega_m
    step = 1e-7

    def psi_at(theta_deg):
        uvw = doe1.dq0_to_uvw(*current, math.radians(theta_deg))
        fem = model.flux_uvw(uvw, theta_deg)
        return [fem[i] + sum(l_uvw[i][j] * uvw[j] for j in range(3)) for i in range(3)]

    plus = psi_at(theta + math.degrees(w_e * step))
    minus = psi_at(theta - math.degrees(w_e * step))
    i_uvw = doe1.dq0_to_uvw(*current, math.radians(theta))
    return [(a - b) / (2.0 * step) + R_PHASE * i for a, b, i in zip(plus, minus, i_uvw)]


@pytest.mark.physics
@pytest.mark.parametrize("l_uvw", [L_CIRC, L_UNEQUAL], ids=["circulant", "unequal"])
def test_end_leakage_voltage_matches_direct_phase_differentiation(l_uvw) -> None:
    model = doe1.Synthetic(pole_pairs=P, psi_f=0.02)
    theta, omega_m = 37.0, 120.0
    row = rom_row(0.3, 0.5, 0.2, theta=theta, model=model)
    current = (row["Id_A"], row["Iq_A"], row["I0_A"])
    direct = _direct_phase_voltage(model, current, theta, omega_m, l_uvw)
    calc = doe2.voltage(row, omega_m=omega_m, r_phase=R_PHASE, pole_pairs=P,
                        l_sigma_end_uvw=l_uvw)
    scale = max(abs(value) for value in direct)
    assert max(abs(a - b) for a, b in zip(direct, calc["u_uvw"])) < 1e-6 * scale
    without = doe2.voltage(row, omega_m=omega_m, r_phase=R_PHASE, pole_pairs=P)
    # перекрёстная ЭДС w_e J L_s i реально присутствует
    assert max(abs(a - b) for a, b in zip(calc["u_uvw"], without["u_uvw"])) > 1e-4


@pytest.mark.physics
def test_circulant_end_leakage_is_angle_independent_in_dq0() -> None:
    ls, ms = 40e-6, -8e-6
    for theta in (0.0, 23.0, 151.0):
        l_dq0, d_l = doe2.end_leakage_dq0(L_CIRC, theta)
        expected = [[ls - ms, 0, 0], [0, ls - ms, 0], [0, 0, ls + 2 * ms]]
        for i in range(3):
            for j in range(3):
                assert l_dq0[i][j] == pytest.approx(expected[i][j], abs=1e-15)
                assert d_l[i][j] == pytest.approx(0.0, abs=1e-15)
    _, d_unequal = doe2.end_leakage_dq0(L_UNEQUAL, 23.0)
    assert max(abs(value) for row in d_unequal for value in row) > 1e-7


@pytest.mark.implementation
def test_end_leakage_adds_to_inductive_term() -> None:
    row = rom_row(0.3, 0.5, 0.2, theta=10.0)
    rates = (1e3, -2e3, 5e2)
    with_l = doe2.voltage(row, omega_m=0.0, r_phase=R_PHASE, pole_pairs=P,
                          rates=rates, l_sigma_end_uvw=L_CIRC)
    without = doe2.voltage(row, omega_m=0.0, r_phase=R_PHASE, pole_pairs=P,
                           rates=rates)
    l_dq0, _ = doe2.end_leakage_dq0(L_CIRC, 10.0)
    extra = [sum(l_dq0[i][j] * rates[j] for j in range(3)) for i in range(3)]
    for i in range(3):
        assert with_l["u_dq0"][i] - without["u_dq0"][i] == pytest.approx(extra[i],
                                                                         abs=1e-12)


@pytest.mark.implementation
def test_missing_psi_reference_or_end_leakage_blocks_voltage() -> None:
    rom = {"B1": rom_row(0.2, 0.3, 0.0, model=doe1.Synthetic(pole_pairs=P, psi_f=0.02))}
    for params, gate in (({"l_sigma_end_uvw_h": ZERO3}, "psi_reference"),
                         ({"psi_reference": "ABSOLUTE"}, "end_leakage")):
        rows, summary = doe2.analyse(rom, omega_m=50.0, vdc=VDC, r_phase=R_PHASE,
                                     pole_pairs=P, params=params)
        assert summary["gates"][gate]["status"] == "NOT_EVALUATED"
        assert summary["status"] == "INCOMPLETE"
        assert rows[0].get("uU_required_V") is None
    _, summary = doe2.analyse(rom, omega_m=50.0, vdc=VDC, r_phase=R_PHASE, pole_pairs=P,
                              params={"psi_reference": "RELATIVE",
                                      "l_sigma_end_uvw_h": ZERO3})
    assert summary["gates"]["psi_reference"]["status"] == "FAIL"
    assert summary["status"] == "FAIL"


@pytest.mark.implementation
def test_end_leakage_rejects_scalar_and_asymmetric_matrix() -> None:
    with pytest.raises(ValueError):
        doe2.end_leakage_matrix(40e-6)
    with pytest.raises(ValueError):
        doe2.end_leakage_matrix([[1e-6, 2e-6, 0.0], [0.0, 1e-6, 0.0], [0.0, 0.0, 1e-6]])
