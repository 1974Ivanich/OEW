"""Тесты DOE_2C: мощности двух мостов, C+ESR и усреднённая DC-шина (OEW-6T)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bm1109_doe2c as doe2c  # noqa: E402

VDC = 48.0


def _duties(u_uvw, vdc=VDC, c=0.5):
    d1 = tuple(c + 0.5 * u / vdc for u in u_uvw)
    d2 = tuple(c - 0.5 * u / vdc for u in u_uvw)
    return d1, d2


def _row(case_id, i_uvw, u_uvw, vdc=VDC, c=0.5):
    d1, d2 = _duties(u_uvw, vdc, c)
    return {"case_id": case_id, "IU_A": i_uvw[0], "IV_A": i_uvw[1],
            "IW_A": i_uvw[2], "duty_U1": d1[0], "duty_V1": d1[1],
            "duty_W1": d1[2], "duty_U2": d2[0], "duty_V2": d2[1],
            "duty_W2": d2[2], "uU_required_V": u_uvw[0],
            "uV_required_V": u_uvw[1], "uW_required_V": u_uvw[2],
            "Vdc_V": vdc}


def _row_c(case_id, i_uvw, u_uvw, c, vdc=VDC):
    """Строка с разными c_k по фазам (не только c=0.5)."""
    d1 = [ck + 0.5 * u / vdc for ck, u in zip(c, u_uvw)]
    d2 = [ck - 0.5 * u / vdc for ck, u in zip(c, u_uvw)]
    return {"case_id": case_id, "IU_A": i_uvw[0], "IV_A": i_uvw[1],
            "IW_A": i_uvw[2], "duty_U1": d1[0], "duty_V1": d1[1],
            "duty_W1": d1[2], "duty_U2": d2[0], "duty_V2": d2[1],
            "duty_W2": d2[2], "uU_required_V": u_uvw[0],
            "uV_required_V": u_uvw[1], "uW_required_V": u_uvw[2],
            "Vdc_V": vdc}


def test_bridge_power_identity_holds_without_neutral() -> None:
    """P_DC_1 + P_DC_2 = sum u_k i_k при sum_k i_k != 0 (общей нейтрали нет)."""
    rng = random.Random(11)
    for _ in range(30):
        i_uvw = tuple(rng.uniform(-5.0, 5.0) for _ in range(3))
        assert abs(sum(i_uvw)) > 1e-3          # условие OEW-6T выполнено
        u_uvw = tuple(rng.uniform(-40.0, 40.0) for _ in range(3))
        d1, d2 = _duties(u_uvw)
        power = doe2c.bridge_power(i_uvw, d1, d2, VDC)
        direct = sum(u * i for u, i in zip(u_uvw, i_uvw))
        assert power["P_DC_total"] == pytest.approx(direct, rel=1e-12, abs=1e-12)
        assert power["P_identity"] == pytest.approx(direct, rel=1e-12, abs=1e-12)
        assert abs(power["residual"]) <= 1e-12 * max(abs(direct), 1.0)


def test_bridge_split_follows_common_mode_and_half_power() -> None:
    """P_1 = +Vdc c sum i + (1/2) sum u i; P_2 = -Vdc c sum i + (1/2) sum u i."""
    i_uvw = (2.0, -3.0, 1.5)                 # sum = 0.5 A
    u_uvw = (10.0, -6.0, 4.0)
    direct = sum(u * i for u, i in zip(u_uvw, i_uvw))
    for c in (0.25, 0.5, 0.75):
        d1, d2 = _duties(u_uvw, c=c)
        power = doe2c.bridge_power(i_uvw, d1, d2, VDC)
        common = VDC * c * sum(i_uvw)
        # common-mode часть входит в мосты с разными знаками: мост 1 отдаёт
        # то, что мост 2 принимает (ток идёт по обмотке между мостами)
        assert power["P_DC_1"] == pytest.approx(common + 0.5 * direct, rel=1e-11)
        assert power["P_DC_2"] == pytest.approx(-common + 0.5 * direct, rel=1e-11)
        assert power["P_DC_total"] == pytest.approx(direct, rel=1e-12)
        assert power["P_imbalance"] == pytest.approx(2.0 * common, rel=1e-11)
    # dP_DC_1/dc = +Vdc sum_i, dP_DC_2/dc = -Vdc sum_i
    low = doe2c.bridge_power(i_uvw, *_duties(u_uvw, c=0.4), VDC)
    high = doe2c.bridge_power(i_uvw, *_duties(u_uvw, c=0.6), VDC)
    delta_c = 0.2
    assert (high["P_DC_1"] - low["P_DC_1"]) / delta_c == pytest.approx(
        VDC * sum(i_uvw), rel=1e-11)
    assert (high["P_DC_2"] - low["P_DC_2"]) / delta_c == pytest.approx(
        -VDC * sum(i_uvw), rel=1e-11)


def test_common_mode_uses_duty_weighted_current() -> None:
    """P_1 - P_2 = 2 Vdc sum c_k i_k при любых c_k; sum i_k — не то же самое."""
    rng = random.Random(5)
    for _ in range(40):
        i_uvw = tuple(rng.uniform(-8.0, 8.0) for _ in range(3))
        u_uvw = tuple(rng.uniform(-30.0, 30.0) for _ in range(3))
        c = [rng.uniform(0.2, 0.8) for _ in range(3)]
        d1 = [ck + 0.5 * u / VDC for ck, u in zip(c, u_uvw)]
        d2 = [ck - 0.5 * u / VDC for ck, u in zip(c, u_uvw)]
        power = doe2c.bridge_power(i_uvw, d1, d2, VDC)
        i_c = sum(ck * i for ck, i in zip(c, i_uvw))
        assert power["I_c"] == pytest.approx(i_c, rel=1e-12, abs=1e-12)
        assert power["I_phase_sum"] == pytest.approx(sum(i_uvw), rel=1e-12)
        assert power["I_zero_sequence"] == pytest.approx(sum(i_uvw) / 3 ** 0.5,
                                                         rel=1e-12)
        assert power["P_common_mode"] == pytest.approx(VDC * i_c,
                                                       rel=1e-11, abs=1e-9)
        assert power["P_imbalance"] == pytest.approx(2.0 * VDC * i_c,
                                                     rel=1e-11, abs=1e-9)
    # при равных c=0.5 duty-взвешенный ток — ровно половина суммы фазных
    i_uvw = (2.0, -3.0, 1.5)
    u_uvw = (10.0, -6.0, 4.0)
    power = doe2c.bridge_power(i_uvw, *_duties(u_uvw), VDC)
    assert power["I_c"] == pytest.approx(0.5 * sum(i_uvw), rel=1e-12)
    assert power["P_imbalance"] == pytest.approx(2.0 * VDC * power["I_c"],
                                                 rel=1e-11)


def test_common_mode_gate_passes_on_unequal_c_rows() -> None:
    rows_in = [_row_c("A", (2.0, -3.0, 1.5), (10.0, -6.0, 4.0), (0.3, 0.5, 0.7)),
               _row_c("B", (-1.0, 4.0, 2.0), (-8.0, 12.0, 5.0), (0.45, 0.55, 0.5))]
    rows, summary = doe2c.analyse(rows_in, dict(doe2c.BUS_TEMPLATE))
    for row, source in zip(rows, rows_in):
        i_c = (sum(row[f"duty_{name}1"] * row[f"I{name}_A"] for name in "UVW")
               + sum(row[f"duty_{name}2"] * row[f"I{name}_A"] for name in "UVW")) / 2.0
        assert row["I_c_A"] == pytest.approx(i_c, rel=1e-12)
        assert row["P_split_imbalance_W"] == pytest.approx(
            2.0 * VDC * row["I_c_A"], rel=1e-11, abs=1e-9)
    gate = summary["gates"]["bridge_power_common_mode"]
    assert gate["status"] == "PASS"
    assert gate["worst_rel"] <= 1e-9


def test_bare_params_leave_everything_not_evaluated() -> None:
    row = _row("S1", (2.0, -3.0, 1.5), (10.0, -6.0, 4.0))
    rows, summary = doe2c.analyse([row], dict(doe2c.BUS_TEMPLATE))
    gates = summary["gates"]
    assert gates["bridge_power_identity"]["status"] == "PASS"
    for name in ("bridge1_power_limit", "bridge2_power_limit",
                 "dc_current_limit", "dc_energy_balance", "dc_overvoltage",
                 "source_reverse_power", "inverter_loss_model",
                 "capacitor_energy_esr_identity", "integration_stability",
                 "integration_step_accuracy", "integration_energy_residual",
                 "integration_stopped_at_zero_energy"):
        assert gates[name]["status"] == "NOT_EVALUATED", name
    assert summary["status"] == "INCOMPLETE"
    assert "c_dc_f" in gates["dc_energy_balance"]["blocking"]
    assert rows[0]["P_loss_inv1_W"] is None
    assert rows[0]["P_src_W"] is None
    assert rows[0]["dc_link_feasible"] == "NOT_EVALUATED"


def test_bus_discharges_and_charges_with_known_limits() -> None:
    """Двигательный режим сажает шину, рекуперация её поднимает."""
    # шаг должен быть не больше R_s*C_DC, иначе нагрузка внутри шага не постоянна
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 200e-6, "step_s": 10e-6, "src_v": VDC,
                   "src_r_ohm": 0.5, "src_source_w": 5e3, "src_sink_w": 5e3,
                   "vdc_max_v": 55.0, "dvdt_max_v_s": 1e5})
    motor = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))
    regen = _row("R", (2.0, -2.0, 0.0), (-20.0, -10.0, 0.0))
    rows, summary = doe2c.analyse([motor] * 5, params)
    assert rows[0]["P_DC_total_W"] > 0.0
    assert summary["sequence_steps"] == 5
    assert rows[0]["Vdc_predicted_V"] is not None
    assert rows[-1]["Vdc_predicted_V"] < rows[0]["Vdc_predicted_V"]
    assert summary["gates"]["source_reverse_power"]["status"] == "PASS"
    assert summary["gates"]["integration_stability"]["status"] == "PASS"
    rows, _ = doe2c.analyse([regen] * 5, params)
    assert rows[0]["P_DC_total_W"] < 0.0
    assert rows[-1]["Vdc_predicted_V"] > rows[0]["Vdc_predicted_V"]


def test_reverse_power_without_sink_path_fails() -> None:
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 200e-6, "step_s": 10e-6, "src_v": VDC,
                   "src_r_ohm": 0.5, "src_source_w": 5e3,
                   "vdc_max_v": 55.0, "dvdt_max_v_s": 1e5})
    regen = _row("R", (2.0, -2.0, 0.0), (-20.0, -10.0, 0.0))
    _, summary = doe2c.analyse([regen], params)
    # при Vsrc == Vdc обратного тока нет: претензий к источнику нет
    assert summary["gates"]["source_reverse_power"]["status"] == "PASS"
    # источник ниже напряжения шины: обратный ток неизбежен
    params_r = dict(params)
    params_r["src_v"] = VDC - 1.0
    _, summary = doe2c.analyse([regen], params_r)
    assert summary["gates"]["source_reverse_power"]["status"] == "FAIL"
    assert summary["gates"]["source_reverse_power"]["reverse_states"] == 1
    assert summary["status"] == "FAIL"


def test_reverse_direction_signs_are_consistent() -> None:
    """Обратный поток: P_bus < 0, I_s < 0, P_series >= 0 — без путаницы знаков."""
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 470e-6, "step_s": 20e-6, "src_v": VDC,
                   "src_r_ohm": 0.5, "src_source_w": 1e3, "src_sink_w": 1e3,
                   "vdc_max_v": 60.0, "dvdt_max_v_s": 1e5})
    # шина выше источника: ток течёт в источник (поглощение)
    row = _row("A", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0), vdc=50.0)
    rows, summary = doe2c.analyse([row], params)
    assert rows[0]["P_src_W"] < 0.0
    assert rows[0]["P_src_series_W"] > 0.0
    gate = summary["gates"]["source_power_reverse_direction"]
    assert gate["status"] == "PASS"
    assert gate["absorbing_states"] == 1


def test_overvoltage_checks_dvdt_not_only_static_value() -> None:
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 20e-6, "step_s": 1e-3, "src_v": VDC,
                   "src_r_ohm": 10.0, "src_sink_w": 1e3,
                   "vdc_max_v": 48.5, "dvdt_max_v_s": 100.0})
    regen = _row("R", (2.0, -2.0, 0.0), (-20.0, -10.0, 0.0))
    rows, summary = doe2c.analyse([regen] * 20, params)
    gate = summary["gates"]["dc_overvoltage"]
    assert gate["status"] == "FAIL"
    # статическая точка ещё в допуске, но dVdc/dt уже превышает предел
    assert rows[0]["Vdc_V"] <= 48.5
    assert gate["peak_dvdt_v_s"] > 100.0
    assert summary["peak_vdc_v"] > 48.5
    assert summary["status"] == "FAIL"


def test_step_larger_than_tau_is_not_passed() -> None:
    """dt > (R_s+ESR)C не даёт права на PASS: нагрузка внутри шага не постоянна."""
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 200e-6, "step_s": 1e-3, "src_v": VDC,
                   "src_r_ohm": 0.5, "src_sink_w": 1e3,
                   "vdc_max_v": 55.0, "dvdt_max_v_s": 1e9})
    regen = _row("R", (2.0, -2.0, 0.0), (-20.0, -10.0, 0.0))
    _, summary = doe2c.analyse([regen], params)
    gate = summary["gates"]["integration_stability"]
    assert gate["status"] == "FAIL"
    assert gate["dt_over_tau"] > 1.0
    assert summary["status"] != "PASS_2C"


def test_current_limits_report_failed_states() -> None:
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"i_dc_max_a": 0.1})
    row = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))
    _, summary = doe2c.analyse([row], params)
    assert summary["gates"]["bridge1_power_limit"]["status"] == "FAIL"
    assert summary["gates"]["dc_current_limit"]["status"] == "FAIL"
    params["i_dc_max_a"] = 100.0
    _, summary = doe2c.analyse([row], params)
    assert summary["gates"]["bridge1_power_limit"]["status"] == "PASS"
    assert summary["gates"]["bridge2_power_limit"]["status"] == "PASS"
    assert summary["gates"]["dc_current_limit"]["status"] == "PASS"


def test_source_power_splits_bus_and_series_loss() -> None:
    """V_src I_s = Vdc I_s + I_s^2 R_s; в баланс входит P_bus = Vdc I_s."""
    params = {"src_v": 60.0, "src_r_ohm": 0.5}
    src = doe2c.source_power(VDC, params)
    assert src["I_src"] == pytest.approx((60.0 - VDC) / 0.5)
    assert src["P_src_bus"] == pytest.approx(VDC * src["I_src"])
    assert src["P_src_terminal"] == pytest.approx(60.0 * src["I_src"])
    assert src["P_src_terminal"] - src["P_src_bus"] - src["P_series"] == \
        pytest.approx(0.0, abs=1e-9)
    assert src["P_source_curtailed"] == pytest.approx(0.0, abs=1e-9)
    assert src["P_src"] == src["P_src_bus"]
    # клиппинг пересчитывается в ТОК; после него точка не на прямой Тевенина,
    # разница — недоступная мощность (НЕ доказанные тепловые потери)
    params["src_source_w"] = 100.0
    clipped = doe2c.source_power(VDC, params)
    assert clipped["clipped"] == "source limit"
    assert clipped["I_src"] == pytest.approx(100.0 / 60.0)
    assert clipped["P_src_bus"] == pytest.approx(VDC * clipped["I_src"])
    assert clipped["P_src_terminal"] == pytest.approx(100.0)
    assert clipped["P_source_curtailed"] == pytest.approx(
        100.0 - clipped["P_src_bus"] - clipped["P_series"])
    assert clipped["P_source_curtailed"] > 0.0


def test_source_accounting_gate_and_bus_power_in_row() -> None:
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"src_v": 60.0, "src_r_ohm": 0.5, "src_source_w": 1e3})
    row = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))
    rows, summary = doe2c.analyse([row], params)
    assert summary["gates"]["source_power_accounting"]["status"] == "PASS"
    # клиппинг 1000 Вт на клеммах: I_s = 1000/60 A, P_bus = Vdc I_s = 800 Вт
    i_s = 1000.0 / 60.0
    assert rows[0]["P_src_W"] == pytest.approx(VDC * i_s)
    assert rows[0]["P_src_terminal_W"] == pytest.approx(1000.0)
    assert rows[0]["P_src_W"] == pytest.approx(800.0)
    assert rows[0]["P_src_W"] != pytest.approx(rows[0]["P_src_terminal_W"])
    assert rows[0]["P_src_terminal_W"] - rows[0]["P_src_W"] \
        - rows[0]["P_src_series_W"] - rows[0]["P_source_curtailed_W"] == \
        pytest.approx(0.0, abs=1e-9)
    assert rows[0]["P_source_curtailed_W"] > 0.0


def test_esr_loss_uses_capacitor_current_not_source_current() -> None:
    """Vsrc == Vdc: I_s = 0, но конденсатор разряжается — ESR обязан греться."""
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 470e-6, "step_s": 20e-6, "src_v": VDC,
                   "src_r_ohm": 0.5, "src_sink_w": 0.0, "esr_ohm": 0.01,
                   "vdc_max_v": 60.0, "dvdt_max_v_s": 1e6})
    row = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))      # P_DC,total = 20 W
    rows, summary = doe2c.analyse([row], params)
    # ESR сдвигает выводы вниз, поэтому малый прямой ток источника есть;
    # главное — ESR греется током КОНДЕНСАТОРА, а не током R_s
    assert abs(rows[0]["P_src_W"]) < 1.0
    assert rows[0]["I_cap_A"] < 0.0                       # конденсатор отдаёт
    assert rows[0]["P_cap_esr_W"] == pytest.approx(
        rows[0]["I_cap_A"] ** 2 * 0.01)
    assert rows[0]["P_cap_esr_W"] > 0.0
    assert summary["gates"]["source_power_accounting"]["status"] == "PASS"
    assert summary["gates"]["dc_energy_balance"]["status"] == "PASS"


def test_capacitor_esr_identity_and_internal_voltage() -> None:
    """V_dc = V_cap + ESR I_cap и E_cap = 0.5 C V_cap^2, а не 0.5 C V_dc^2."""
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 470e-6, "step_s": 20e-6, "src_v": VDC,
                   "src_r_ohm": 0.1, "src_source_w": 1e3, "src_sink_w": 1e3,
                   "esr_ohm": 0.02, "vdc_max_v": 60.0, "dvdt_max_v_s": 1e6})
    row = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))
    rows, summary = doe2c.analyse([row] * 3, params)
    for row_out in rows:
        assert row_out["V_dc_terminal_V"] == pytest.approx(
            row_out["V_cap_internal_V"] + 0.02 * row_out["I_cap_A"], abs=1e-12)
        assert row_out["E_cap_J"] == pytest.approx(
            0.5 * 470e-6 * row_out["V_cap_internal_V"] ** 2, rel=1e-12)
        # энергия идеальной ёмкости, а не 0.5 C V_dc^2
        assert row_out["E_cap_J"] != pytest.approx(
            0.5 * 470e-6 * row_out["V_dc_terminal_V"] ** 2)
    assert summary["gates"]["capacitor_energy_esr_identity"]["status"] == "PASS"
    assert summary["gates"]["integration_energy_residual"]["status"] == "PASS"
    assert summary["gates"]["integration_energy_residual"]["worst_rel"] < 1e-3


def test_step_convergence_and_accuracy_gates() -> None:
    """Один шаг и восемь подшагов дают одно и то же при замороженной нагрузке."""
    params = dict(doe2c.BUS_TEMPLATE)
    params.update({"c_dc_f": 470e-6, "step_s": 20e-6, "src_v": VDC,
                   "src_r_ohm": 0.1, "src_source_w": 1e3, "src_sink_w": 1e3,
                   "esr_ohm": 0.01, "vdc_max_v": 60.0, "dvdt_max_v_s": 1e6})
    row = _row("M", (2.0, -2.0, 0.0), (20.0, 10.0, 0.0))
    _, summary = doe2c.analyse([row] * 4, params)
    accuracy = summary["gates"]["integration_step_accuracy"]
    assert accuracy["status"] == "PASS"
    assert accuracy["worst_rel"] < 1e-9
    assert summary["gates"]["integration_stability"]["status"] == "PASS"
    assert summary["gates"]["integration_stopped_at_zero_energy"]["status"] == "PASS"


def test_selftest_exit_code() -> None:
    assert doe2c.selftest() == 0
