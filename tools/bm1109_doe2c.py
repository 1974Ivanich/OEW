"""DOE_2C для BM1109: мощность каждого моста и усреднённая DC-шина (OEW-6T).

OEW-6T: шесть силовых концов, два моста на общей шине, общей нейтрали нет —
sum_k i_k != 0 допустим и влияет на распределение мощности между мостами.
Ток i_k считается текущим от моста 1 через обмотку к мосту 2:

    v_k1 = Vdc (c_k + delta_k/2),   v_k2 = Vdc (c_k - delta_k/2),
    u_k  = v_k1 - v_k2 = Vdc delta_k,
    P_DC1 =  sum_k v_k1 i_k,        P_DC2 = -sum_k v_k2 i_k,
    P_DC1 + P_DC2 = sum_k u_k i_k            (идеальные мосты, тождество).

Отсюда

    P_DC1 =  Vdc sum_k c_k i_k + 0.5 sum_k u_k i_k,
    P_DC2 = -Vdc sum_k c_k i_k + 0.5 sum_k u_k i_k,
    P_DC1 - P_DC2 = 2 Vdc sum_k c_k i_k.

Каждый мост отдаёт в обмотки половину полезной мощности (0.5 sum u i), а
common-mode составляющая входит в них с ПРОТИВОПОЛОЖНЫМИ знаками: c-ток течёт
через обмотку от одного моста к другому, т.е. один мост отдаёт то, что другой
принимает. Поэтому P_DC,total = sum u_k i_k от c не зависит вообще.

Два РАЗНЫХ тока, их нельзя путать:

    I_phase_sum   = sum_k i_k       — сумма фазных токов; в ортонормированном
                                      Clarke это I_zero_sequence = I_phase_sum/sqrt(3).
                                      В OEW-6T I_phase_sum != 0 не является ошибкой;
    I_c           = sum_k c_k i_k   — duty-взвешенный ток; при ЛЮБЫХ c_k:
                                      P_DC1 - P_DC2 = 2 Vdc I_c,
                                      P_common_mode = Vdc I_c.

Равенство P_DC1 - P_DC2 = Vdc I_phase_sum верно ТОЛЬКО при c_k = 0.5.

c_k=0.5 симметрирует запас duty, но при sum_i != 0 он же задаёт обмен
мощностью между мостами, а не только их duty. Оптимизация c_k имеет смысл
только при известных пределах токов DC-входов и модели потерь; до этого c_k
не оптимизируется (см. remaining в summary).

Знаки (единые для всех гейтов):
    P_DC_j > 0 — мост потребляет из шины;  P_DC_j < 0 — возвращает в шину;
    P_src  > 0 — источник питает шину;     P_src  < 0 — поглощает.
Если источник не умеет поглощать (src_sink_w не задан), отрицательный P_src
не допускается: избыток энергии обязан идти в C_DC, иначе гейт
source_reverse_power падает.

Источник Тевенина: I_s = (V_src - V_DC)/R_s, и три РАЗНЫЕ мощности

    P_src_terminal = V_src I_s      (идеальный источник),
    P_src_series   = I_s^2 R_s      (потери в R_s),
    P_src_bus      = V_DC I_s       (то, что реально входит в шину).

Без ограничения P_terminal = P_bus + P_series. В энергобаланс шины входит
P_bus, а НЕ P_terminal.

Ограничение источника задаётся током I_lim = P/SRC_V и меняет I_s. После
клиппинга рабочая точка уже не на прямой Тевенина, поэтому вводится

    P_source_curtailed = I_s (V_src - V_DC - I_s R_s),

т.е. мощность, недоступная из-за ограничения. Это НЕ доказанные тепловые
потери: у источника с управляемым током избыток просто не производится.
Модели рассеивающего ограничителя (P_clamp_dissipated) в DOE_2C нет.

Конденсатор: идеальная ёмкость C последовательно с ESR.

    V_DC = V_cap + ESR I_cap,                E_cap = 0.5 C V_cap^2,
    dE_cap/dt = V_cap I_cap = V_DC I_cap - I_cap^2 ESR,
    I_cap = I_s - (P_DC1 + P_DC2 + P_aux)/V_DC.

Смешивать E_DC = 0.5 C V_DC^2 и отдельный учёт I_cap^2 ESR нельзя: это
энергия ёмкости при напряжении на ВНЕШНИХ выводах. Поэтому в CSV есть
V_dc_terminal_V, V_cap_internal_V, I_cap_A, P_cap_esr_W, E_cap_J.

Интегрирование: шаг step_s — время пребывания в состоянии. Для замороженной
нагрузки уравнение dv_cap/dt = (V_ss - v_cap)/(R_s + ESR) линейно и решается
точно (экспонента), поэтому схема A-устойчива и не разваливается при
dt ~ R_s C_DC. Явный Эйлер при dt > 2 R_s C_DC расходится; здесь он
используется только в ветке клиппированного источника (там ток постоянен).
Проверки: integration_stability (dt/(R_s+ESR)C), integration_step_accuracy
(сравнение с 8 подшагами), integration_energy_residual (Симпсон против
dE_cap), integration_stopped_at_zero_energy.

I_cap, посчитанный через средние мощности, — НЕ действующий ток пульсаций:
I_cap_rms != |I_cap_avg|. Колонки I_bridge_rms_* и I_cap_ripple_rms_A
остаются пустыми (NOT_EVALUATED) до появления модели ключей.

Чего нет во входных данных (C_DC, ESR, пределы токов/мощностей/напряжения,
модель потерь мостов) — то остаётся NOT_EVALUATED и блокирует PASS_2C.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

IN_HEADER = ("case_id", "IU_A", "IV_A", "IW_A",
             "duty_U1", "duty_V1", "duty_W1", "duty_U2", "duty_V2", "duty_W2",
             "uU_required_V", "uV_required_V", "uW_required_V", "Vdc_V")

OUT_HEADER = [
    "case_id", "theta_e_deg", "omega_m_rad_s",
    "IU_A", "IV_A", "IW_A",
    "duty_U1", "duty_V1", "duty_W1", "duty_U2", "duty_V2", "duty_W2",
    "uU_V", "uV_V", "uW_V", "Vdc_V",
    "I_phase_sum_A", "I_zero_sequence_A", "I_c_A",
    "P_common_mode_W", "P_half_W",
    "P_DC_1_W", "P_DC_2_W", "P_DC_total_W",
    "P_split_identity_W", "P_split_residual_W", "P_split_imbalance_W",
    "I_DC_1_A", "I_DC_2_A", "I_DC_total_A",
    # будущие RMS/пиковые оценки: без модели ключей остаются пустыми
    "I_bridge_rms_1_A", "I_bridge_rms_2_A", "I_cap_ripple_rms_A",
    "P_bus_demand_W", "P_src_W", "P_src_terminal_W", "P_src_series_W",
    "P_source_curtailed_W",
    "V_dc_terminal_V", "V_cap_internal_V", "I_cap_A", "P_cap_esr_W",
    "P_cap_power_W", "E_cap_J", "dVdc_dt_V_s", "Vdc_predicted_V",
    "Vdc_margin_V",
    "P_loss_inv1_W", "P_loss_inv2_W", "dc_link_feasible", "note",
]

BUS_TEMPLATE = {
    "vdc_v": None,
    "c_dc_f": None,
    "esr_ohm": None,
    "vdc_max_v": None,
    "dvdt_max_v_s": None,
    "src_v": None,
    "src_r_ohm": None,
    "src_source_w": None,
    "src_sink_w": None,
    "p_aux_w": None,
    "p_dc_max_w": None,
    "i_dc_max_a": None,
    "step_s": None,
    "note": "пустые поля = нет измеренных данных; соответствующие гейты 2C "
            "остаются NOT_EVALUATED",
}


def _num(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle)
                if _num(row.get("Vdc_V")) is not None]


def bridge_power(i_uvw, duty1, duty2, vdc: float) -> dict:
    """Мощности двух мостов при заданных duty (по фазе) и токах.

    Полярность: i_k — от моста 1 через обмотку к мосту 2.
    """
    v1 = [vdc * value for value in duty1]
    v2 = [vdc * value for value in duty2]
    u = [a - b for a, b in zip(v1, v2)]
    p1 = sum(v * i for v, i in zip(v1, i_uvw))
    p2 = -sum(v * i for v, i in zip(v2, i_uvw))
    identity = sum(u_k * i_k for u_k, i_k in zip(u, i_uvw))
    # c_k восстанавливается из пары duty: v1 + v2 = 2 Vdc c_k
    c_k = [(a + b) / (2.0 * vdc) if vdc else 0.0 for a, b in zip(v1, v2)]
    i_c = sum(c * i for c, i in zip(c_k, i_uvw))
    phase_sum = sum(i_uvw)
    return {
        "v1": v1, "v2": v2, "u": u, "c": c_k,
        "P_DC_1": p1, "P_DC_2": p2, "P_DC_total": p1 + p2,
        "P_identity": identity, "residual": (p1 + p2) - identity,
        # common-mode доля моста 1 = Vdc sum_k c_k i_k (у моста 2 обратный знак)
        "P_common_mode": vdc * i_c,
        "P_half": 0.5 * identity,
        "P_imbalance": p1 - p2,
        # сумма фазных токов — это НЕ duty-взвешенный ток
        "I_phase_sum": phase_sum,
        "I_zero_sequence": phase_sum / math.sqrt(3.0),
        # duty-взвешенный: P_DC1 - P_DC2 = 2 Vdc I_c при любых c_k
        "I_c": i_c,
    }


def source_power(vdc: float, params: dict) -> dict | None:
    """Источник Тевенина: P на клеммах, P на входе шины, R_s и ограничение.

    I_s = (V_src - V_DC)/R_s, P_terminal = V_src I_s, P_bus = V_DC I_s,
    P_series = I_s^2 R_s. Без ограничения P_terminal = P_bus + P_series.

    Пределы источника задаются мощностью на клеммах и переводятся в ток
    I_lim = P/V_src. После клиппинга точка не на прямой Тевенина:
    P_source_curtailed = I_s (V_src - V_DC - I_s R_s) — мощность, недоступная
    из-за ограничения, а не доказанные тепловые потери (зависит от типа
    ограничителя; рассеивающая модель в 2C не заявлена).
    """
    src_v, r_s = params.get("src_v"), params.get("src_r_ohm")
    if src_v is None or not r_s:
        return None
    i_raw = (src_v - vdc) / r_s
    current, clipped = i_raw, None
    if src_v > 0.0:
        limit_out = params.get("src_source_w")
        if limit_out is not None and current * src_v > limit_out:
            current, clipped = limit_out / src_v, "source limit"
        if current * src_v < 0.0:
            allowed = params.get("src_sink_w")
            allowed = 0.0 if allowed is None else abs(allowed)
            if current * src_v < -allowed:
                current = -allowed / src_v
                clipped = ("sink limit" if params.get("src_sink_w") is not None
                           else "no reverse path")
    bus = vdc * current
    terminal = src_v * current
    series = current ** 2 * r_s
    return {"P_src": bus, "P_src_bus": bus,
            "P_src_terminal": terminal,
            "P_src_raw_bus": vdc * i_raw,
            "P_series": series,
            "P_source_curtailed": terminal - bus - series,
            "I_src": current, "I_src_raw": i_raw, "clipped": clipped}


def _advance(v_cap: float, p_load: float, params: dict, *, dt: float,
             c_dc: float | None, esr: float | None,
             i_load: float | None = None) -> dict:
    """Один шаг по C+ESR при нагрузке p_load (Вт), замороженной на шаге.

    Уравнение в терминах напряжения идеальной ёмкости:

        dv_cap/dt = (V_ss - v_cap)/(R_s + ESR),   V_ss = V_src - i_load R_s.

    Решение точное (экспонента), поэтому шаг не ограничен 2 R_s C. Ветка
    клиппированного источника идёт по постоянному току (линейный рост v_cap).

    i_load: замороженный ток нагрузки. Передаётся при разбиении шага на
    подшаги, иначе шаги решали бы разные задачи и сравнение по шагу теряло
    бы смысл (нагрузка зависит от напряжения).
    """
    esr = esr or 0.0
    r_s = params.get("src_r_ohm") or 0.0
    src_v = params.get("src_v")
    v_term, i_cap, src = v_cap, 0.0, None
    frozen = i_load
    # V_DC = V_cap + ESR I_cap, I_cap зависит от V_DC: три итерации достаточно
    for _ in range(4):
        if frozen is None:
            i_load = p_load / v_term if v_term > 0.0 else 0.0
        src = source_power(v_term, params)
        i_s = 0.0 if src is None else src["I_src"]
        i_cap = i_s - i_load
        v_term = v_cap + esr * i_cap
        if v_term <= 0.0:
            v_term = 1e-9
    frozen = i_load
    p_esr = i_cap ** 2 * esr if esr else 0.0
    p_cap = v_cap * i_cap
    v_next, residual = None, None
    r_eff = r_s + esr
    if c_dc is not None and dt > 0.0:
        exact = (src is not None and not src["clipped"] and r_eff > 0.0)
        if exact:
            v_ss = src_v - i_load * r_s
            tau = c_dc * r_eff
            decay = math.exp(-dt / tau) if tau > 0.0 else 0.0

            def power_at(t: float) -> float:
                v = v_ss + (v_cap - v_ss) * math.exp(-t / tau)
                return v * (v_ss - v) / r_eff

            est = (dt / 6.0) * (power_at(0.0) + 4.0 * power_at(0.5 * dt)
                                + power_at(dt))
            v_next = v_ss + (v_cap - v_ss) * decay
        else:
            v_next = v_cap + i_cap * dt / c_dc
            est = v_cap * i_cap * dt
        true = 0.5 * c_dc * (v_next ** 2 - v_cap ** 2)
        scale = max(abs(true), abs(est), 1e-12)
        residual = abs(true - est) / scale
    e_cap = None if c_dc is None else 0.5 * c_dc * v_cap ** 2
    return {"v_term": v_term, "v_cap": v_cap, "i_load": frozen,
            "i_src": None if src is None else src["I_src"],
            "i_cap": i_cap, "p_esr": p_esr, "p_cap": p_cap,
            "v_next": v_next, "e_cap": e_cap,
            "energy_residual": residual, "src": src}


def analyse(rows: list[dict], params: dict | None = None) -> tuple[list[dict], dict]:
    params = dict(params or {})
    vdc_fixed = params.get("vdc_v")
    c_dc = params.get("c_dc_f")
    step_s = params.get("step_s")
    step_s = step_s if (step_s and step_s > 0) else None
    esr = params.get("esr_ohm")
    p_aux = params.get("p_aux_w") or 0.0

    # состояние: напряжение идеальной ёмкости (внешние выводы = V_cap + ESR I)
    v_cap_state = None
    for row in rows:
        if _num(row.get("Vdc_V")) is not None:
            v_cap_state = _num(row.get("Vdc_V"))
            break
    if vdc_fixed is not None:
        v_cap_state = vdc_fixed

    out = []
    identity_rel: list[float] = []
    common_rel: list[float] = []
    esr_rel: list[float] = []
    energy_rel: list[float] = []
    step_rel: list[float] = []
    sign_issues: list[str] = []
    absorbing_states = 0
    integrated = 0
    stopped_states = 0
    halted = False
    peak_vdc = v_cap_state
    peak_dvdt = 0.0
    integrating = c_dc is not None and step_s is not None

    for row in rows:
        base = {"case_id": row["case_id"],
                "theta_e_deg": _num(row.get("theta_e_deg")),
                "omega_m_rad_s": _num(row.get("omega_m_rad_s")),
                "Vdc_V": _num(row.get("Vdc_V")),
                "uU_V": _num(row.get("uU_required_V")),
                "uV_V": _num(row.get("uV_required_V")),
                "uW_V": _num(row.get("uW_required_V"))}
        i_uvw = tuple(_num(row.get(f"I{name}_A")) for name in "UVW")
        duty1 = tuple(_num(row.get(f"duty_{name}1")) for name in "UVW")
        duty2 = tuple(_num(row.get(f"duty_{name}2")) for name in "UVW")
        for name in "UVW":
            base[f"I{name}_A"] = _num(row.get(f"I{name}_A"))
            base[f"duty_{name}1"] = _num(row.get(f"duty_{name}1"))
            base[f"duty_{name}2"] = _num(row.get(f"duty_{name}2"))
        if None in i_uvw or None in duty1 or None in duty2 or base["Vdc_V"] is None:
            base["dc_link_feasible"] = "NOT_EVALUATED"
            base["note"] = "нет duty или токов в строке DOE_2A/2B"
            out.append(base)
            continue
        local_vdc = base["Vdc_V"]
        power = bridge_power(i_uvw, duty1, duty2, local_vdc)
        scale = max(abs(power["P_identity"]), abs(power["P_DC_1"]),
                    abs(power["P_DC_2"]), 1e-12)
        identity_rel.append(abs(power["residual"]) / scale)

        i_dc_1 = power["P_DC_1"] / local_vdc
        i_dc_2 = power["P_DC_2"] / local_vdc
        i_dc_total = power["P_DC_total"] / local_vdc
        p_load = power["P_DC_total"] + p_aux

        start_v = v_cap_state if integrating else local_vdc
        step = _advance(start_v, p_load, params,
                        dt=(step_s or 0.0), c_dc=c_dc, esr=esr)
        src = step["src"]
        v_term = step["v_term"]
        i_cap = step["i_cap"] if src is not None else None
        v_cap_here = step["v_cap"] if src is not None else None
        e_cap = step["e_cap"] if src is not None else None

        # мгновенное тождество C+ESR: V_dc I_cap - I_cap^2 ESR = V_cap I_cap
        if src is not None and esr is not None and i_cap is not None:
            lhs = v_term * i_cap - i_cap ** 2 * esr
            scale_e = max(abs(lhs), abs(v_cap_here * i_cap), 1e-12)
            esr_rel.append(abs(lhs - v_cap_here * i_cap) / scale_e)

        predicted, dvdt = None, None
        if integrating and not halted and src is not None:
            if step["v_next"] is None or step["v_next"] <= 0.0:
                stopped_states += 1
                halted = True
            else:
                integrated += 1
                dvdt = i_cap / c_dc
                peak_dvdt = max(peak_dvdt, abs(dvdt))
                if step["energy_residual"] is not None:
                    energy_rel.append(step["energy_residual"])
                # сходимость по шагу: 8 подшагов при той же нагрузке
                fine_v = v_cap_state
                for _ in range(8):
                    sub = _advance(fine_v, p_load, params, dt=step_s / 8.0,
                                   c_dc=c_dc, esr=esr,
                                   i_load=step["i_load"])
                    fine_v = sub["v_next"] if sub["v_next"] is not None else fine_v
                ref = max(abs(fine_v), 1e-12)
                step_rel.append(abs(step["v_next"] - fine_v) / ref)
                v_cap_state = step["v_next"]
                predicted = (v_cap_state + (esr or 0.0) * i_cap
                             if esr is not None else v_cap_state)
                peak_vdc = predicted if peak_vdc is None else \
                    max(peak_vdc, predicted)
        elif src is not None:
            # без интегрирования: точка независима, V_dc — из строки DOE_2B
            predicted = None
            v_cap_here = local_vdc
            e_cap = None if c_dc is None else 0.5 * c_dc * local_vdc ** 2
            dvdt = None

        if src is not None:
            p_bus = src["P_src_bus"]
            if p_bus < 0.0:
                absorbing_states += 1
            # знаки через произведения: V_src, Vdc > 0 => знак мощности = знак тока
            if src["P_src_terminal"] * src["I_src"] < 0.0:
                sign_issues.append(f"{row['case_id']}: P_terminal vs I_s")
            if p_bus * src["I_src"] < 0.0:
                sign_issues.append(f"{row['case_id']}: P_bus vs I_s")
            if src["P_series"] < 0.0:
                sign_issues.append(f"{row['case_id']}: P_series < 0")
        else:
            p_bus = None

        checks = []          # (name, status) по этой точке
        limit_i = params.get("i_dc_max_a")
        limit_p = params.get("p_dc_max_w")
        for label, value in (("bridge1", abs(i_dc_1)), ("bridge2", abs(i_dc_2))):
            if limit_i is None:
                checks.append((f"{label}_power_limit", "NOT_EVALUATED"))
            else:
                checks.append((f"{label}_power_limit",
                               "PASS" if value <= limit_i else "FAIL"))
        if limit_p is not None:
            for label, value in (("bridge1", abs(power["P_DC_1"])),
                                 ("bridge2", abs(power["P_DC_2"]))):
                checks.append((f"{label}_p_limit",
                               "PASS" if value <= limit_p else "FAIL"))
        if limit_i is None:
            checks.append(("dc_current_limit", "NOT_EVALUATED"))
        else:
            checks.append(("dc_current_limit",
                           "PASS" if abs(i_dc_total) <= limit_i else "FAIL"))

        base.update({
            "I_phase_sum_A": power["I_phase_sum"],
            "I_zero_sequence_A": power["I_zero_sequence"],
            "I_c_A": power["I_c"],
            "P_common_mode_W": power["P_common_mode"],
            "P_half_W": power["P_half"],
            "P_DC_1_W": power["P_DC_1"], "P_DC_2_W": power["P_DC_2"],
            "P_DC_total_W": power["P_DC_total"],
            "P_split_identity_W": power["P_identity"],
            "P_split_residual_W": power["residual"],
            "P_split_imbalance_W": power["P_imbalance"],
            "I_DC_1_A": i_dc_1, "I_DC_2_A": i_dc_2, "I_DC_total_A": i_dc_total,
            "P_bus_demand_W": p_load,
            "P_src_W": p_bus,
            "P_src_terminal_W": None if src is None else src["P_src_terminal"],
            "P_src_series_W": None if src is None else src["P_series"],
            "P_source_curtailed_W": None if src is None
            else src["P_source_curtailed"],
            "V_dc_terminal_V": v_term,
            "V_cap_internal_V": v_cap_here,
            "I_cap_A": i_cap,
            "P_cap_esr_W": None if esr is None or i_cap is None else step["p_esr"],
            "P_cap_power_W": None if src is None else step["p_cap"],
            "E_cap_J": e_cap,
            "dVdc_dt_V_s": dvdt,
            "Vdc_predicted_V": predicted,
            "P_loss_inv1_W": None, "P_loss_inv2_W": None,   # нет модели ключей
        })
        verdict = ("FAIL" if any(status == "FAIL" for _, status in checks)
                   else (None if any(status == "NOT_EVALUATED" for _, status in checks)
                         else "PASS"))
        base["dc_link_feasible"] = verdict or "NOT_EVALUATED"
        base["note"] = "" if verdict else "нет пределов/параметров шины"
        if src is not None and src["clipped"]:
            base["note"] = (base["note"] + "; " if base["note"] else "") + \
                f"P_src curtailed: {src['clipped']}"
        if halted:
            base["note"] = (base["note"] + "; " if base["note"] else "") + \
                "интегрирование остановлено (E_cap <= 0)"
        out.append(base)

    gates = {}
    gates["bridge_power_identity"] = {
        "status": "PASS" if identity_rel and max(identity_rel) <= 1e-9 else
                  ("FAIL" if identity_rel else "NOT_RUN"),
        "worst_rel": max(identity_rel) if identity_rel else None,
        "note": "P_DC_1 + P_DC_2 == sum_k u_k i_k — независимая проверка "
                "разбиения мощности по мостам, а не энергобаланс двигателя"
    }
    # P_DC_1 - P_DC_2 == 2 Vdc sum_k c_k i_k в КАЖДОЙ точке. Сверять с
    # I_phase_sum = sum_k i_k нельзя: они равны только при c_k = 0.5.
    for row in out:
        if row.get("P_split_imbalance_W") is None or row.get("I_c_A") is None:
            continue
        predicted = 2.0 * row["Vdc_V"] * row["I_c_A"]
        scale = max(abs(row["P_split_imbalance_W"]), abs(predicted), 1.0)
        common_rel.append(abs(row["P_split_imbalance_W"] - predicted) / scale)
        scale_pc = max(abs(row["P_common_mode_W"]), abs(predicted), 1.0) / 2.0
        common_rel.append(abs(row["P_common_mode_W"] - 0.5 * predicted) / scale_pc)
    gates["bridge_power_common_mode"] = {
        "status": "PASS" if common_rel and max(common_rel) <= 1e-9 else
                  ("FAIL" if common_rel else "NOT_RUN"),
        "worst_rel": max(common_rel) if common_rel else None,
        "note": "P_DC_1 - P_DC_2 == 2 Vdc sum_k c_k i_k; P_common_mode == Vdc I_c; "
                "I_phase_sum совпадает с этим только при c_k = 0.5"}
    for gate_name, key, label in (("bridge1_power_limit", "I_DC_1_A", "мост 1"),
                                  ("bridge2_power_limit", "I_DC_2_A", "мост 2")):
        if params.get("i_dc_max_a") is None:
            gates[gate_name] = {"status": "NOT_EVALUATED",
                                "blocking": ["i_dc_max_a"],
                                "note": f"предел тока DC-входа {label}"}
        else:
            failed = [row["case_id"] for row in out
                      if row.get(key) is not None
                      and abs(row[key]) > params["i_dc_max_a"]]
            gates[gate_name] = {"status": "FAIL" if failed else "PASS",
                                "failed_states": len(failed)}
    if params.get("i_dc_max_a") is None:
        gates["dc_current_limit"] = {"status": "NOT_EVALUATED",
                                     "blocking": ["i_dc_max_a"]}
    else:
        failed = [row["case_id"] for row in out
                  if row.get("I_DC_total_A") is not None
                  and abs(row["I_DC_total_A"]) > params["i_dc_max_a"]]
        gates["dc_current_limit"] = {"status": "FAIL" if failed else "PASS",
                                     "failed_states": len(failed),
                                     "note": "усреднённый I_DC = P_DC,total/Vdc"}
    if esr is None:
        gates["capacitor_energy_esr_identity"] = {
            "status": "NOT_EVALUATED", "blocking": ["esr_ohm"],
            "note": "V_DC I_cap - I_cap^2 ESR == V_cap I_cap проверяется "
                    "только при заданном ESR"}
    else:
        gates["capacitor_energy_esr_identity"] = {
            "status": "PASS" if esr_rel and max(esr_rel) <= 1e-9 else
                      ("FAIL" if esr_rel else "NOT_RUN"),
            "worst_rel": max(esr_rel) if esr_rel else None,
            "note": "V_DC = V_cap + ESR I_cap; внутренняя мощность = "
                    "V_cap I_cap = V_DC I_cap - I_cap^2 ESR"}
    if c_dc is None or step_s is None or params.get("src_r_ohm") is None:
        missing = [name for name in ("c_dc_f", "step_s", "src_r_ohm")
                   if params.get(name) is None]
        gates["integration_stability"] = {
            "status": "NOT_EVALUATED", "blocking": missing,
            "note": "критерий dt <= (R_s+ESR) C_DC: за один шаг напряжение "
                    "ёмкости должно успевать отреагировать"}
    else:
        tau = (params["src_r_ohm"] + (esr or 0.0)) * c_dc
        ratio = step_s / tau if tau > 0.0 else float("inf")
        gates["integration_stability"] = {
            "status": "PASS" if ratio <= 1.0 else "FAIL",
            "dt_over_tau": ratio, "tau_s": tau,
            "note": "экспоненциальное обновление A-устойчиво; критерий нужен, "
                    "чтобы нагрузка внутри шага считалась постоянной"}
    if not step_rel:
        gates["integration_step_accuracy"] = {
            "status": "NOT_EVALUATED",
            "blocking": ["c_dc_f", "step_s", "src_v", "src_r_ohm"],
            "note": "сравнение одного шага с 8 подшагами"}
    else:
        gates["integration_step_accuracy"] = {
            "status": "PASS" if max(step_rel) <= 1e-6 else "FAIL",
            "worst_rel": max(step_rel), "substeps": 8,
            "note": "|V(dt) - V(dt/8)| / |V(dt/8)| при той же нагрузке"}
    if not energy_rel:
        gates["integration_energy_residual"] = {
            "status": "NOT_EVALUATED",
            "blocking": ["c_dc_f", "step_s", "src_v", "src_r_ohm"],
            "note": "dE_cap против интеграла V_cap I_cap (Симпсон)"}
    else:
        gates["integration_energy_residual"] = {
            "status": "PASS" if max(energy_rel) <= 1e-3 else "FAIL",
            "worst_rel": max(energy_rel),
            "note": "квадратура Симпсона для dE_cap = (V_DC I_cap - I_cap^2 ESR) dt "
                    "против точного ΔE_cap: ESR не создаёт и не теряет энергию "
                    "сверх погрешности численного интегрирования"}
    gates["integration_stopped_at_zero_energy"] = {
        "status": "PASS" if integrating and stopped_states == 0
                  else "NOT_EVALUATED",
        "stopped_states": stopped_states,
        "blocking": [] if (integrating and stopped_states == 0) else ["step_s"],
        "note": "остановка при E_cap <= 0 означает выход за модель, а не PASS"}
    if c_dc is None:
        gates["dc_energy_balance"] = {
            "status": "NOT_EVALUATED", "blocking": ["c_dc_f"],
            "note": "E_cap = 0.5 C V_cap^2 и dE_cap/dt = V_cap I_cap = "
                    "P_bus - P_DC,1 - P_DC,2 - P_aux - I_cap^2 ESR (P_bus = Vdc I_s)"
        }
    elif not integrated:
        gates["dc_energy_balance"] = {
            "status": "NOT_EVALUATED", "blocking": ["step_s", "src_v", "src_r_ohm"],
            "note": "C_DC есть, но последовательность состояний и шаг не заданы: "
                    "интегрировать нечего"}
    elif integrated < len(out):
        gates["dc_energy_balance"] = {
            "status": "NOT_EVALUATED", "blocking": ["step_s"],
            "steps": integrated, "states": len(out),
            "note": "интегрирование остановлено: E_cap <= 0 или нет v_cap; "
                    "шаг велик относительно R_s*C_DC"}
    else:
        gates["dc_energy_balance"] = {
            "status": "PASS", "steps": integrated, "states": len(out),
            "E_start_J": 0.5 * c_dc * (_num(rows[0].get("Vdc_V")) or 0.0) ** 2,
            "E_end_J": v_cap_state ** 2 * 0.5 * c_dc,
            "note": "тождество учёта по энергии шины (P_bus = Vdc I_s, ESR по "
                    "току конденсатора); независимые потери мостов — после "
                    "модели ключей"}
    if params.get("vdc_max_v") is None or params.get("dvdt_max_v_s") is None:
        missing = [name for name in ("vdc_max_v", "dvdt_max_v_s")
                   if params.get(name) is None]
        gates["dc_overvoltage"] = {"status": "NOT_EVALUATED",
                                   "blocking": missing,
                                   "note": "нужны и статический предел Vdc, "
                                           "и предел dVdc/dt"}
    elif not integrated:
        gates["dc_overvoltage"] = {"status": "NOT_EVALUATED",
                                   "blocking": ["step_s", "src_v", "src_r_ohm"],
                                   "note": "без шага и модели источника "
                                           "dVdc/dt не проверить"}
    else:
        over = peak_vdc > params["vdc_max_v"] or peak_dvdt > params["dvdt_max_v_s"]
        gates["dc_overvoltage"] = {
            "status": "FAIL" if over else "PASS",
            "peak_vdc_v": peak_vdc, "peak_dvdt_v_s": peak_dvdt,
            "vdc_max_v": params["vdc_max_v"],
            "dvdt_max_v_s": params["dvdt_max_v_s"]}
    if params.get("src_v") is None or not params.get("src_r_ohm"):
        gates["source_power_accounting"] = {
            "status": "NOT_EVALUATED", "blocking": ["src_v", "src_r_ohm"],
            "note": "P_terminal = V_src I_s, P_bus = Vdc I_s, "
                    "P_series = I_s^2 R_s; в баланс входит P_bus"}
    else:
        worst_src = 0.0
        clipped_rows = 0
        worst_curtailed = 0.0
        for row in out:
            if row.get("Vdc_V") is None:
                continue
            source = source_power(row["Vdc_V"], params)
            if source is None:
                continue
            scale = max(abs(source["P_src_terminal"]),
                        abs(source["P_src_bus"]), 1e-12)
            worst_src = max(worst_src, abs(source["P_src_terminal"]
                                           - source["P_src_bus"]
                                           - source["P_series"]
                                           - source["P_source_curtailed"]) / scale)
            worst_curtailed = max(worst_curtailed,
                                  source["P_source_curtailed"])
            if source["clipped"]:
                clipped_rows += 1
        gates["source_power_accounting"] = {
            "status": "PASS" if worst_src <= 1e-9 else "FAIL",
            "worst_rel": worst_src, "clipped_states": clipped_rows,
            "max_curtailed_w": worst_curtailed,
            "note": "P_terminal = P_bus + I_s^2 R_s + P_source_curtailed; в "
                    "энергобаланс шины входит P_bus = Vdc I_s; curtailed — "
                    "недоступная мощность, не доказанные тепловые потери"}
    if params.get("src_v") is None or not params.get("src_r_ohm"):
        gates["source_power_reverse_direction"] = {
            "status": "NOT_EVALUATED", "blocking": ["src_v", "src_r_ohm"],
            "note": "знаки P_src, I_s и P_series при обратном потоке"}
    else:
        gates["source_power_reverse_direction"] = {
            "status": "FAIL" if sign_issues else "PASS",
            "absorbing_states": absorbing_states,
            "issues": sign_issues[:5],
            "note": "P_src < 0 означает поглощение; знак P_terminal = знак I_s "
                    "(V_src > 0); P_series = I_s^2 R_s >= 0 всегда"}
    if params.get("src_v") is None or not params.get("src_r_ohm"):
        gates["source_reverse_power"] = {
            "status": "NOT_EVALUATED", "blocking": ["src_v", "src_r_ohm"],
            "note": "нужна модель источника: P_src = Vsrc(Vsrc-Vdc)/Rs"}
    else:
        # нужна ли источнику способность поглощать — по P_src до клиппинга
        allowed = abs(params.get("src_sink_w") or 0.0)
        required = []
        for row in out:
            if row.get("Vdc_V") is None:
                continue
            source = source_power(row["Vdc_V"], params)
            if source is not None and source["P_src_raw_bus"] < -allowed:
                required.append(row["case_id"])
        gates["source_reverse_power"] = {
            "status": "FAIL" if required else "PASS",
            "reverse_states": len(required),
            "src_sink_w": params.get("src_sink_w"),
            "note": ("источник без пути поглощения: P_src < 0 запрещён"
                     if params.get("src_sink_w") is None else
                     "проверено против src_sink_w") +
                    "; избыток энергии обязан идти в C_DC или тормозной резистор"}
    gates["inverter_loss_model"] = {
        "status": "NOT_EVALUATED",
        "blocking": ["switch_model", "rds_on", "switching_losses"],
        "note": "P_loss_inv1/2 остаются пустыми: модель потерь не заявлена"}
    gates["dc_link_feasibility"] = {
        "status": ("FAIL" if any(gate["status"] == "FAIL" for gate in gates.values())
                   else ("PASS" if all(gate["status"] == "PASS"
                                       for name, gate in gates.items()
                                       if name != "inverter_loss_model")
                         else "NOT_EVALUATED")),
        "note": "агрегат гейтов 2C"}

    blocking = [name for name, gate in gates.items()
                if gate["status"] in ("FAIL", "NOT_EVALUATED")
                and name not in ("inverter_loss_model", "dc_link_feasibility")]
    status = ("FAIL" if any(gate["status"] == "FAIL" for gate in gates.values())
              else ("PASS_2C" if not blocking else "INCOMPLETE"))
    summary = {
        "status": status, "gates": gates, "blocking_gates": blocking,
        "rows": len(out), "computed": sum(1 for row in out
                                          if row["P_split_identity_W"] is not None),
        "sequence_steps": integrated,
        "integration_note": (
            None if (integrating and integrated == len(out)) else
            f"проинтегрировано {integrated} из {len(out)} состояний: "
            "экспоненциальный шаг остановлен при E_cap <= 0 или не задан шаг; "
            "состояния — не траектория"),
        "params": params,
        "peak_vdc_v": peak_vdc, "peak_dvdt_v_s": peak_dvdt,
        "sign_convention": {"P_DC_j": "+ потребляет, - возвращает в шину",
                            "P_src": "+ питает шину, - поглощает"},
        "power_definitions": {
            "P_src_W": "мощность на входе DC-шины Vdc*I_s (после клиппинга)",
            "P_src_terminal_W": "мощность идеального источника V_src*I_s",
            "P_src_series_W": "потери в R_s: I_s^2 R_s",
            "P_source_curtailed_W": "недоступная мощность при ограничении "
                                    "источника I_s(V_src-Vdc-I_s R_s); НЕ "
                                    "доказанные тепловые потери",
            "V_dc_terminal_V": "напряжение на внешних выводах батареи "
                               "конденсаторов: V_cap + ESR*I_cap",
            "V_cap_internal_V": "напряжение идеальной ёмкости",
            "E_cap_J": "0.5*C*V_cap_internal^2 (не 0.5*C*V_dc^2)",
            "I_cap_A": "I_s - (P_DC,total+P_aux)/V_dc (баланс токов узла)",
            "P_cap_power_W": "V_cap*I_cap: скорость изменения E_cap",
            "P_cap_esr_W": "I_cap^2 ESR; ESR греется током конденсатора",
            "I_phase_sum_A": "sum_k i_k — сумма фазных токов (не duty-взвешенная)",
            "I_zero_sequence_A": "I_phase_sum/sqrt(3) — нулевая "
                                 "последовательность ортонормального Clarke",
            "I_c_A": "sum_k c_k i_k; P_DC_1-P_DC_2 = 2 Vdc I_c при любых c_k",
            "P_common_mode_W": "Vdc*I_c (доля моста 1; у моста 2 обратный знак)",
            "I_DC_1_2_total_A": "УСРЕДНЁННЫЕ эквивалентные токи по мощности: "
                                "не пиковые, не RMS, не ток ключей",
            "I_bridge_rms_peak_A": "не рассчитаны: нужна модель ключей/пульсаций",
            "I_cap_ripple_rms_A": "не рассчитан: без ESL/коммутационной модели"},
        "note_currents": "I_DC_* = P_DC/Vdc — средние эквивалентные; "
                         "I_bridge_rms/peak и I_cap_ripple_rms требуют модели "
                         "ключей и не заменяются этими величинами",
        "note_c_choice": (
            "c_k=0.5 выбран для симметричного запаса duty; при sum_k i_k != 0 "
            "он же задаёт P_common = Vdc*c*sum_i, т.е. распределение мощности "
            "между мостами. Оптимизация c_k не делается, пока нет пределов "
            "токов DC-входов и модели потерь"),
        "remaining": ["switch/driver loss model (2C)",
                      "ESL и пульсации шины (2C)",
                      "P_clamp_dissipated для рассеивающего ограничителя (2C)",
                      "оптимизация c_U,c_V,c_W (2C)",
                      "оптимизация разгона (2D)"],
    }
    return out, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: ("" if row.get(name) is None else row.get(name))
                             for name in OUT_HEADER})


def selftest() -> int:
    vdc = 48.0
    # 1) тождество P1 + P2 = sum u i при sum_i != 0 (нет нейтрали)
    i_uvw = (2.0, -3.0, 1.5)                       # sum = 0.5
    u_uvw = (10.0, -6.0, 4.0)
    c = 0.5
    duty1 = tuple(c + 0.5 * u / vdc for u in u_uvw)
    duty2 = tuple(c - 0.5 * u / vdc for u in u_uvw)
    power = bridge_power(i_uvw, duty1, duty2, vdc)
    direct = sum(u * i for u, i in zip(u_uvw, i_uvw))
    print(f"total: {power['P_DC_total']:.6f} W vs sum u i = {direct:.6f} W, "
          f"residual {power['residual']:.2e} W")
    assert abs(power["P_DC_total"] - direct) < 1e-12
    # 2) P1 = Vdc c sum_i + 0.5 sum u i, P2 = Vdc c sum_i - 0.5 sum u i
    for c_test in (0.3, 0.5, 0.72):
        d1 = tuple(c_test + 0.5 * u / vdc for u in u_uvw)
        d2 = tuple(c_test - 0.5 * u / vdc for u in u_uvw)
        split = bridge_power(i_uvw, d1, d2, vdc)
        common = vdc * c_test * sum(i_uvw)
        assert abs(split["P_DC_1"] - (common + 0.5 * direct)) < 1e-9
        assert abs(split["P_DC_2"] - (-common + 0.5 * direct)) < 1e-9
        assert abs(split["P_DC_total"] - direct) < 1e-12
        assert abs(split["P_DC_1"] - split["P_DC_2"] - 2.0 * common) < 1e-9
    print(f"phase sum = {power['I_phase_sum']:.3f} A (i0 = "
          f"{power['I_zero_sequence']:.3f} A), I_c = {power['I_c']:.3f} A "
          f"-> P_common = {power['P_common_mode']:.3f} W (c=0.5: I_c = sum_i/2)")
    assert abs(power["I_c"] - 0.5 * power["I_phase_sum"]) < 1e-12
    assert abs(power["P_imbalance"] - 2.0 * power["P_common_mode"]) < 1e-9
    # 3) источник: три мощности и недоступная мощность при клиппинге
    src_params = {"src_v": 60.0, "src_r_ohm": 0.5, "src_source_w": 50.0}
    source = source_power(vdc, src_params)
    assert source["clipped"] == "source limit"
    assert source["P_source_curtailed"] > 0.0
    assert abs(source["P_src_terminal"] - source["P_src_bus"]
               - source["P_series"] - source["P_source_curtailed"]) < 1e-9
    print(f"source: I_s = {source['I_src']:.4f} A, P_bus = "
          f"{source['P_src_bus']:.3f} W, P_series = {source['P_series']:.3f} W, "
          f"P_curtailed = {source['P_source_curtailed']:.3f} W")
    # 4) C + ESR: V_dc = V_cap + ESR I_cap, dE_cap/dt = V_cap I_cap
    bus = {"src_v": 48.0, "src_r_ohm": 0.1, "src_source_w": 1e3}
    state = _advance(48.0, 20.0, bus, dt=2e-5, c_dc=470e-6, esr=0.01)
    assert abs(state["v_term"] - (state["v_cap"] + 0.01 * state["i_cap"])) < 1e-12
    assert abs(state["v_term"] * state["i_cap"] - state["i_cap"] ** 2 * 0.01
               - state["v_cap"] * state["i_cap"]) < 1e-9
    print(f"cap: V_cap = {state['v_cap']:.4f} V, V_dc = {state['v_term']:.4f} V, "
          f"I_cap = {state['i_cap']:.4f} A, E_residual = "
          f"{state['energy_residual']:.2e}")
    assert state["energy_residual"] < 1e-3
    # 5) гейты без параметров шины остаются NOT_EVALUATED
    row = {"case_id": "S1", "IU_A": i_uvw[0], "IV_A": i_uvw[1], "IW_A": i_uvw[2],
           "duty_U1": duty1[0], "duty_V1": duty1[1], "duty_W1": duty1[2],
           "duty_U2": duty2[0], "duty_V2": duty2[1], "duty_W2": duty2[2],
           "uU_required_V": u_uvw[0], "uV_required_V": u_uvw[1],
           "uW_required_V": u_uvw[2], "Vdc_V": vdc}
    _, bare = analyse([dict(row)], dict(BUS_TEMPLATE))
    print("bare status:", bare["status"], "blocking:", bare["blocking_gates"])
    assert bare["status"] == "INCOMPLETE"
    assert bare["gates"]["bridge_power_identity"]["status"] == "PASS"
    assert bare["gates"]["dc_overvoltage"]["status"] == "NOT_EVALUATED"
    assert bare["gates"]["integration_stability"]["status"] == "NOT_EVALUATED"
    ok = (bare["status"] == "INCOMPLETE"
          and bare["gates"]["bridge_power_identity"]["status"] == "PASS")
    print("DOE_2C SELFTEST: " + ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DOE_2C: мощности двух мостов и DC-шина")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="P_DC,1/2 и усреднённая шина")
    run_parser.add_argument("--voltages", required=True,
                            help="doe2_voltage.csv из DOE_2A/2B")
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--params", default=None)
    sub.add_parser("selftest", help="аналитическая проверка мощностей")
    template_parser = sub.add_parser("template", help="пустой bus params.json")
    template_parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    if args.command == "template":
        path = Path(args.out)
        path.write_text(json.dumps(BUS_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"written: {path}")
        return 0

    params = dict(BUS_TEMPLATE)
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    rows, summary = analyse(load_rows(Path(args.voltages)), params)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "doe2c_bus.csv", rows)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows)} computed={summary['computed']} "
          f"status={summary['status']}")
    print("gates: " + " ".join(f"{name}={gate['status']}"
                               for name, gate in summary["gates"].items()))
    print(f"written: {out / 'doe2c_bus.csv'}")
    return 0 if summary["status"] == "PASS_2C" else 1


if __name__ == "__main__":
    sys.exit(main())
