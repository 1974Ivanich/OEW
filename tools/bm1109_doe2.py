"""DOE_2A/DOE_2B для BM1109: напряжение из ROM и допустимость PWM (OEW-6T).

2A — восстановление u_U, u_V, u_W из магнитной ROM (L, dpsi/dtheta) и траектории.
2B — шесть duty двух мостов (c=0.5) и проверка допустимости по реальным пределам.

Модель (dq0, ортонормальные Кларк/Парк):

    u_dq0 = R i + L di_dq0/dt + w_e [ dpsi_dq0/dtheta_e|_i + J psi ],
    J psi = (-psi_q, psi_d, 0),  w_e = p * omega_m.

Фазная форма получается тем же ортонормальным обратным преобразованием.
При ПОСТОЯННОМ dq0-векторе фазные токи всё равно меняются с углом ротора:
это учитывает член J psi, а не di/dt (di_dq0/dt = 0 — законный вход).

Два моста, общая шина, полярность отсчёта от минуса шины:

    u_k = Vdc (d_k1 - d_k2),   d_k1 = c + delta_k/2,  d_k2 = c - delta_k/2,
    delta_k = u_k / Vdc,       |delta_k|/2 <= c <= 1 - |delta_k|/2.

Сопротивления фаз могут быть разными: R_dq0 = T R_UVW T^T (T = Park*Clarke),
при равных R это R*I, при разных появляются перекрёстные члены.

Идеальная граница |u_k| <= Vdc — это математика, а не паспорт железа.
pwm_feasible=PASS выдаётся только для УРОВНЯ A (геометрическая достижимость
duty при известных duty_min/max), и это не точность выходного напряжения.
Уровень B (эффективное напряжение с dead-time compensation) требует модели
ключей, драйверов и полярности токов — пока dead_time_accuracy=NOT_EVALUATED,
а поля u_effective_* остаются пустыми.

DC-link (2C) здесь НЕ считается: P_motor = sum u_k i_k пишется как идеальная
оценка без потерь. Мощности мостов, C_DC и источник — tools/bm1109_doe2c.py;
гейт dc_link ниже — только указатель на него.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_doe1 as doe1  # noqa: E402

ROM_FIELDS = ("Ldd", "Ldq", "Ld0", "Lqd", "Lqq", "Lq0", "L0d", "L0q", "L00",
              "psi_d", "psi_q", "psi_0",
              "dpsi_d_dtheta_e", "dpsi_q_dtheta_e", "dpsi_0_dtheta_e")
ROM_MATRIX = (("d", "d", "Ldd"), ("d", "q", "Ldq"), ("d", "0", "Ld0"),
              ("q", "d", "Lqd"), ("q", "q", "Lqq"), ("q", "0", "Lq0"),
              ("0", "d", "L0d"), ("0", "q", "L0q"), ("0", "0", "L00"))

OUT_HEADER = [
    "case_id", "theta_m_deg", "theta_e_deg", "omega_m_rad_s",
    "Id_A", "Iq_A", "I0_A", "IU_A", "IV_A", "IW_A",
    "dId_dt", "dIq_dt", "dI0_dt",
    "u_d_V", "u_q_V", "u_0_V",
    "uU_required_V", "uV_required_V", "uW_required_V",
    "u_R_V", "u_L_V", "u_emf_V",
    "Vdc_V",
    "duty_U1", "duty_V1", "duty_W1", "duty_U2", "duty_V2", "duty_W2",
    "duty_margin_U", "duty_margin_V", "duty_margin_W", "voltage_margin",
    "dc_power_estimate_W", "dc_current_estimate_A",
    "u_effective_U_V", "u_effective_V_V", "u_effective_W_V", "dead_time_offset_V",
    "pwm_level", "pwm_feasible", "note",
]

PARAM_TEMPLATE = {
    "pole_pairs": 3,
    "vdc_v": None,
    "r_phase_ohm": None,
    "duty_min": None,
    "duty_max": None,
    "dead_time_s": None,
    "min_pulse_s": None,
    "pwm_period_s": None,
    "max_current_a": None,
    "note": "пустые поля = нет измеренных данных; DOE_2 остаётся NOT_EVALUATED",
}


def _num(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_rom(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["case_id"]: row for row in csv.DictReader(handle)}


def rom_complete(row: dict) -> bool:
    return all(_num(row.get(name)) is not None for name in ROM_FIELDS)


def matrix(row: dict) -> list[list[float]]:
    index = {"d": 0, "q": 1, "0": 2}
    table = [[0.0] * 3 for _ in range(3)]
    for axis_a, axis_b, name in ROM_MATRIX:
        table[index[axis_a]][index[axis_b]] = _num(row[name])
    return table


def flux(row: dict) -> tuple[float, float, float]:
    return (_num(row["psi_d"]), _num(row["psi_q"]), _num(row["psi_0"]))


CLARKE = ((doe1.SQ23, -0.5 * doe1.SQ23, -0.5 * doe1.SQ23),
          (0.0, doe1.SQ12, -doe1.SQ12),
          (doe1.SQ13, doe1.SQ13, doe1.SQ13))


def phase_resistances(r_phase) -> tuple[float, float, float]:
    """Скаляр или (R_U, R_V, R_W) — без предположения об одинаковых фазах."""
    if isinstance(r_phase, (int, float)):
        return (float(r_phase), float(r_phase), float(r_phase))
    values = tuple(float(value) for value in r_phase)
    if len(values) != 3:
        raise ValueError("r_phase: нужен скаляр или три сопротивления фаз")
    return values


def frame_matrix(theta_e_deg: float) -> list[list[float]]:
    """T = Park(theta) * Clarke: UVW -> dq0, ортонормальная."""
    theta = math.radians(theta_e_deg)
    c, s = math.cos(theta), math.sin(theta)
    park = ((c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0))
    return [[sum(park[i][k] * CLARKE[k][j] for k in range(3))
             for j in range(3)] for i in range(3)]


def resistance_dq0(r_phase, theta_e_deg: float) -> list[list[float]]:
    """R_dq0 = T R_UVW T^T; симметрична, при равных R это R*I."""
    ru, rv, rw = phase_resistances(r_phase)
    transform = frame_matrix(theta_e_deg)
    diagonal = [[ru, 0.0, 0.0], [0.0, rv, 0.0], [0.0, 0.0, rw]]
    # T R T^T
    temp = [[sum(transform[i][k] * diagonal[k][j] for k in range(3))
             for j in range(3)] for i in range(3)]
    return [[sum(temp[i][k] * transform[j][k] for k in range(3))
             for j in range(3)] for i in range(3)]


def voltage(row: dict, *, omega_m: float, r_phase, pole_pairs: float,
            rates: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> dict:
    """u_dq0 и u_UVW по ROM. rates — производные i_dq0 по времени (A/s).

    r_phase — скаляр или (R_U, R_V, R_W); используется полная R_dq0.
    """
    current = (_num(row["Id_A"]), _num(row["Iq_A"]), _num(row["I0_A"]))
    psi = flux(row)
    dpsi = (_num(row["dpsi_d_dtheta_e"]), _num(row["dpsi_q_dtheta_e"]),
            _num(row["dpsi_0_dtheta_e"]))
    table = matrix(row)
    w_e = pole_pairs * omega_m
    r_dq0 = resistance_dq0(r_phase, _num(row["theta_e_deg"]))
    resistive = tuple(sum(r_dq0[i][j] * current[j] for j in range(3))
                      for i in range(3))
    inductive = tuple(sum(table[i][j] * rates[j] for j in range(3)) for i in range(3))
    # J psi = (-psi_q, psi_d, 0): вращение координат при постоянном i_dq0
    rotation = (dpsi[0] - psi[1], dpsi[1] + psi[0], dpsi[2])
    emf = tuple(w_e * value for value in rotation)
    u_dq0 = tuple(resistive[i] + inductive[i] + emf[i] for i in range(3))
    theta = math.radians(_num(row["theta_e_deg"]))
    u_uvw = doe1.dq0_to_uvw(u_dq0[0], u_dq0[1], u_dq0[2], theta)
    i_uvw = doe1.dq0_to_uvw(current[0], current[1], current[2], theta)
    power_phase = sum(u * i for u, i in zip(u_uvw, i_uvw))
    power_dq0 = sum(u * i for u, i in zip(u_dq0, current))
    return {
        "u_dq0": u_dq0, "u_uvw": u_uvw, "i_uvw": i_uvw,
        "u_R": math.sqrt(sum(value ** 2 for value in resistive)),
        "u_L": math.sqrt(sum(value ** 2 for value in inductive)),
        "u_emf": math.sqrt(sum(value ** 2 for value in emf)),
        "power_phase": power_phase, "power_dq0": power_dq0,
        "omega_e": w_e,
    }


def pwm_split(u_uvw: tuple[float, float, float], vdc: float, *,
              duty_min: float | None = None, duty_max: float | None = None,
              band: float = 0.0) -> dict:
    """Шесть duty при c=0.5 и запас до действующих пределов.

    band — запретная зона у краёв (dead time + минимальный импульс в долях
    периода); при неизвестных пределах маржа не считается.
    """
    low = None if duty_min is None else max(duty_min, band)
    high = None if duty_max is None else min(duty_max, 1.0 - band)
    phases = {}
    for name, value in zip("UVW", u_uvw):
        delta = value / vdc
        d1 = 0.5 + 0.5 * delta
        d2 = 0.5 - 0.5 * delta
        margin = None
        if low is not None and high is not None:
            margin = min(d1 - low, high - d1, d2 - low, high - d2)
        phases[name] = {"delta": delta, "d1": d1, "d2": d2, "margin": margin}
    ideal_ok = all(abs(phase["delta"]) <= 1.0 + 1e-12 for phase in phases.values())
    margins = [phase["margin"] for phase in phases.values()]
    return {
        "phases": phases,
        "ideal_ok": ideal_ok,
        "max_delta": max(abs(phase["delta"]) for phase in phases.values()),
        "margin": None if any(m is None for m in margins) else min(margins),
        "low": low, "high": high,
    }


def _limits(params: dict) -> tuple[dict, list[str]]:
    """Реальные пределы PWM; чего нет — то и блокирует pwm_feasible."""
    missing = [name for name in ("duty_min", "duty_max", "dead_time_s",
                                 "min_pulse_s", "pwm_period_s")
               if params.get(name) is None]
    band = 0.0
    if not missing:
        band = (params["dead_time_s"] + params["min_pulse_s"]) / params["pwm_period_s"]
    return {"band": band, "low": params.get("duty_min"),
            "high": params.get("duty_max")}, missing


def analyse(rom: dict[str, dict], *, omega_m: float, vdc: float | None,
            r_phase: float | None, pole_pairs: float,
            rates: tuple[float, float, float] = (0.0, 0.0, 0.0),
            params: dict | None = None) -> tuple[list[dict], dict]:
    params = dict(params or {})
    limits, missing_limits = _limits(params)
    rows = []
    power_errors = []
    incomplete = 0
    for case_id in sorted(rom):
        source = rom[case_id]
        theta_e = _num(source["theta_e_deg"])
        current = (_num(source["Id_A"]), _num(source["Iq_A"]), _num(source["I0_A"]))
        base = {"case_id": case_id,
                "theta_e_deg": theta_e,
                "theta_m_deg": theta_e / pole_pairs,
                "omega_m_rad_s": omega_m,
                "Id_A": current[0], "Iq_A": current[1], "I0_A": current[2],
                "dId_dt": rates[0], "dIq_dt": rates[1], "dI0_dt": rates[2]}
        if not rom_complete(source):
            incomplete += 1
            base.update({"pwm_feasible": "NOT_EVALUATED",
                         "note": "неполная строка ROM (нет L или dpsi/dtheta)"})
            rows.append(base)
            continue
        i_uvw = doe1.dq0_to_uvw(current[0], current[1], current[2],
                                math.radians(theta_e))
        base.update({"IU_A": i_uvw[0], "IV_A": i_uvw[1], "IW_A": i_uvw[2]})
        if vdc is None or r_phase is None:
            base.update({"pwm_feasible": "NOT_EVALUATED",
                         "note": "нет Vdc или R: напряжение не считается"})
            rows.append(base)
            continue
        calc = voltage(source, omega_m=omega_m, r_phase=r_phase,
                       pole_pairs=pole_pairs, rates=rates)
        u = calc["u_uvw"]
        split = pwm_split(u, vdc, duty_min=limits["low"], duty_max=limits["high"],
                          band=limits["band"])
        scale = max(abs(calc["power_phase"]), abs(calc["power_dq0"]), 1e-12)
        power_errors.append(abs(calc["power_phase"] - calc["power_dq0"]) / scale)
        base.update({
            "u_d_V": calc["u_dq0"][0], "u_q_V": calc["u_dq0"][1],
            "u_0_V": calc["u_dq0"][2],
            "uU_required_V": u[0], "uV_required_V": u[1], "uW_required_V": u[2],
            "u_R_V": calc["u_R"], "u_L_V": calc["u_L"], "u_emf_V": calc["u_emf"],
            "Vdc_V": vdc,
            "dc_power_estimate_W": calc["power_phase"],
            "dc_current_estimate_A": calc["power_phase"] / vdc,
        })
        for name in "UVW":
            phase = split["phases"][name]
            base[f"duty_{name}1"] = phase["d1"]
            base[f"duty_{name}2"] = phase["d2"]
            base[f"duty_margin_{name}"] = phase["margin"]
        base["voltage_margin"] = split["margin"]
        base["pwm_level"] = "A"
        base["u_effective_U_V"] = None              # уровень B: dead-time, ключи
        base["u_effective_V_V"] = None
        base["u_effective_W_V"] = None
        base["dead_time_offset_V"] = None
        if missing_limits:
            base["pwm_feasible"] = "NOT_EVALUATED"
            base["note"] = "нет пределов PWM: " + ", ".join(missing_limits)
        elif not split["ideal_ok"]:
            base["pwm_feasible"] = "FAIL"
            base["note"] = f"|u| > Vdc (max {split['max_delta']:.3f} Vdc)"
        elif split["margin"] < 0.0:
            base["pwm_feasible"] = "FAIL"
            base["note"] = f"duty вне [low, high] (margin {split['margin']:.4f})"
        else:
            base["pwm_feasible"] = "PASS"
            base["note"] = "" if rates != (0.0, 0.0, 0.0) else "di_dq0/dt=0"
        rows.append(base)

    gates = {}
    fraction = (len(rom) - incomplete) / len(rom) if rom else 0.0
    gates["rom_integrity"] = {"status": "PASS" if fraction == 1.0 else "FAIL",
                              "complete_fraction": fraction}
    # Это равенство следует из ортонормальности Clarke/Park: проверка
    # реализации преобразований, а не энергобаланс двигателя (он — в DOE_2C).
    if power_errors:
        worst = max(power_errors)
        gates["power_coordinate_invariance"] = {
            "status": "PASS" if worst <= 1e-9 else "FAIL",
            "worst_rel": worst,
            "note": "u_UVW.i_UVW == u_dq0.i_dq0; энергобаланс с потерями — DOE_2C"}
    else:
        gates["power_coordinate_invariance"] = {"status": "NOT_RUN",
                                               "worst_rel": None}
    computed = [row for row in rows if row.get("uU_required_V") is not None]
    if not computed:
        gates["ideal_voltage"] = {"status": "NOT_RUN", "worst_rel": None}
    else:
        worst = max(max(abs(row["uU_required_V"]), abs(row["uV_required_V"]),
                        abs(row["uW_required_V"])) / row["Vdc_V"] for row in computed)
        gates["ideal_voltage"] = {"status": "PASS" if worst <= 1.0 else "FAIL",
                                  "worst_rel": worst}
    if not computed or missing_limits:
        gates["pwm_feasible"] = {"status": "NOT_EVALUATED",
                                 "blocking": missing_limits or ["нет Vdc/R"]}
    else:
        failed = [row for row in computed if row["pwm_feasible"] != "PASS"]
        gates["pwm_feasible"] = {"status": "FAIL" if failed else "PASS",
                                 "failed_states": len(failed),
                                 "band": limits["band"]}
    gates["dead_time_accuracy"] = {
        "status": "NOT_EVALUATED",
        "note": "уровень B: u_effective = Vdc(d1-d2) + du_dead(i_k, sign i_k) "
                "требует модели ключей, драйверов и полярности токов; "
                "pwm_feasible подтверждает только уровень A",
        "blocking": ["switch_model", "driver_timing", "current_polarity"]}
    gates["dc_link"] = {"status": "NOT_RUN",
                        "note": "вне 2A/2B: tools/bm1109_doe2c.py "
                                "(P_DC_1/2, C_DC, источник, потери)"}
    later = ("dc_link", "dead_time_accuracy")
    blocking = [name for name, gate in gates.items()
                if gate["status"] in ("FAIL", "NOT_EVALUATED") and name not in later]
    later_gates = [gates[name] for name in later]
    status = "FAIL" if any(g["status"] == "FAIL" for g in gates.values()
                           if g not in later_gates) \
        else ("INCOMPLETE" if blocking else "PASS_2A2B")
    summary = {"status": status, "gates": gates, "blocking_gates": blocking,
               "rows": len(rows), "computed": len(computed),
               "omega_m_rad_s": omega_m, "vdc_v": vdc, "r_phase_ohm": r_phase,
               "pole_pairs": pole_pairs, "rates": list(rates),
               "params": params, "remaining": ["dc_link (2C)",
                                               "dead-time accuracy (level B)",
                                               "acceleration optimisation (2D)"]}
    return rows, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: ("" if row.get(name) is None else row.get(name))
                             for name in OUT_HEADER})


def selftest() -> int:
    pole_pairs, vdc, r_phase, omega = 3.0, 48.0, 0.05, 100.0
    model = doe1.Synthetic(pole_pairs=pole_pairs, psi_f=0.02)
    theta = 37.0
    # Траектория: i_dq0 постоянен, ротор вращается => токи в UVW вращаются.
    # Именно такую траекторию описывает dq0-форма с членом J psi.
    current = (1.0, 2.0, 0.5)
    w_e = pole_pairs * omega
    step = 1e-6

    def psi_at(theta_deg):
        uvw = doe1.dq0_to_uvw(*current, math.radians(theta_deg))
        return model.flux_uvw(uvw, theta_deg)

    plus = psi_at(theta + math.degrees(w_e * step))
    minus = psi_at(theta - math.degrees(w_e * step))
    direct = [(a - b) / (2.0 * step) for a, b in zip(plus, minus)]
    i_uvw = doe1.dq0_to_uvw(*current, math.radians(theta))
    direct = [value + r_phase * i for value, i in zip(direct, i_uvw)]
    # та же величина через dq0-формулу: L и dpsi/dθ из модели
    id_, iq, i0 = current
    table = {"Ldd": model.ld + 3 * model.a4 * id_ ** 2,
             "Ldq": model.kdq, "Ld0": model.kd0, "Lqd": model.kdq,
             "Lqq": model.lq + 3 * model.b4 * iq ** 2, "Lq0": model.kq0,
             "L0d": model.kd0, "L0q": model.kq0, "L00": model.l0,
             "psi_d": model.ld * id_ + model.a4 * id_ ** 3 + model.kdq * iq
             + model.kd0 * i0 + model.psi_f,
             "psi_q": model.lq * iq + model.b4 * iq ** 3 + model.kdq * id_
             + model.kq0 * i0,
             "psi_0": model.l0 * i0 + model.kd0 * id_ + model.kq0 * iq,
             "dpsi_d_dtheta_e": 0.0, "dpsi_q_dtheta_e": 0.0,
             "dpsi_0_dtheta_e": 0.0,
             "Id_A": id_, "Iq_A": iq, "I0_A": i0, "theta_e_deg": theta}
    calc = voltage(table, omega_m=omega, r_phase=r_phase, pole_pairs=pole_pairs)
    diff = max(abs(a - b) for a, b in zip(direct, calc["u_uvw"]))
    print(f"voltage: direct-vs-dq0 max diff = {diff:.3e} V")
    split = pwm_split(calc["u_uvw"], vdc)
    print(f"duty: max|delta| = {split['max_delta']:.4f}, ideal_ok={split['ideal_ok']}")
    for name in "UVW":
        phase = split["phases"][name]
        check = vdc * (phase["d1"] - phase["d2"])
        assert abs(check - calc["u_uvw"]["UVW".index(name)]) < 1e-12
    assert diff < 1e-4 * max(abs(value) for value in direct), diff
    assert split["ideal_ok"]
    ok = diff < 1e-4 and split["ideal_ok"]
    print("DOE_2A/2B SELFTEST: " + ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DOE_2A/2B: voltage and PWM")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="u и duty по ROM и траектории")
    run_parser.add_argument("--rom", required=True)
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--params", default=None)
    run_parser.add_argument("--omega", type=float, default=0.0)
    run_parser.add_argument("--vdc", type=float, default=None)
    run_parser.add_argument("--r-phase", type=float, nargs="+", default=None,
                            help="R или R_U R_V R_W")
    run_parser.add_argument("--pole-pairs", type=float, default=None)
    run_parser.add_argument("--di-dt", type=float, nargs=3, default=(0.0, 0.0, 0.0))
    sub.add_parser("selftest", help="аналитическая проверка на PM-модели")
    template_parser = sub.add_parser("template", help="пустой params.json")
    template_parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    if args.command == "template":
        path = Path(args.out)
        path.write_text(json.dumps(PARAM_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"written: {path}")
        return 0

    params = dict(PARAM_TEMPLATE)
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    for name, value in (("vdc_v", args.vdc), ("pole_pairs", args.pole_pairs)):
        if value is not None:
            params[name] = value
    if args.r_phase:
        params["r_phase_ohm"] = (args.r_phase[0] if len(args.r_phase) == 1
                                 else list(args.r_phase))
    rom = load_rom(Path(args.rom))
    rows, summary = analyse(rom, omega_m=args.omega, vdc=params["vdc_v"],
                            r_phase=params["r_phase_ohm"],
                            pole_pairs=params["pole_pairs"],
                            rates=tuple(args.di_dt), params=params)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "doe2_voltage.csv", rows)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows)} computed={summary['computed']} status={summary['status']}")
    print("gates: " + " ".join(f"{name}={gate['status']}"
                               for name, gate in summary["gates"].items()))
    print(f"written: {out / 'doe2_voltage.csv'}")
    return 0 if summary["status"] == "PASS_2A2B" else 1


if __name__ == "__main__":
    sys.exit(main())
