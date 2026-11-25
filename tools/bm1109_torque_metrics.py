"""Исполняемый расчёт метрик точности torque ROM (TZ_PHASE4, редакция V0.4).

Метрики E_trunc/E_noise/E_corner/E_total вычисляются на каждом наборе данных,
а не хранятся константами. eps_W берётся из psi_delta_scale, вычисленного тем
же способом, что frozen P2-N1 в DOE-1 (dq0-компоненты, полная ROM-сетка),
а не локальным d-only свипом.

Модель шума (frozen): ограниченный равномерный в [-eps_W, +eps_W],
независимые псевдослучайные значения на каждую оценку W', seeds (1,2,3),
phi = 0.3. E_noise/E_total — результаты конечного набора реализаций;
E_corner — аналитическая граница всего допустимого диапазона шума.

Решающее правило PASS (V0.4): E_trunc + E_corner <= 1% * T_peak.
E_total — контрольная метрика, не основание для PASS.
"""
import hashlib
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_doe1 as d1

P = 3
IR = 5.0
SEEDS = (1, 2, 3)
PHI = 0.3


def psi_delta_scale_rom(model) -> float:
    """psi_delta_scale как в frozen P2-N1: max по dq0-компонентам и полной
    ROM-сетке |psi_k(i,theta) - psi_k(0,theta)|."""
    angles = [30.0 * k for k in range(12)]
    scale = 0.0
    for th in angles:
        zero_uvw = (0.0, 0.0, 0.0)
        psi0 = d1.uvw_to_dq0(*model.flux_uvw(zero_uvw, th), math.radians(th))
        for xd, xq, x0 in d1.base_points():
            if xd == 0.0 and xq == 0.0 and x0 == 0.0:
                continue
            uvw = d1.dq0_to_uvw(xd * IR, xq * IR, x0 * IR, math.radians(th))
            psi = d1.uvw_to_dq0(*model.flux_uvw(uvw, th), math.radians(th))
            scale = max(scale, max(abs(a - b) for a, b in zip(psi, psi0)))
    return scale


def eps_w_from_scale(scale: float, ir: float = IR) -> float:
    """frozen формула: eps_W = 1e-3 * psi_delta_scale * I_r."""
    return 1e-3 * scale * ir


def noise(theta_e_deg: float, seed: int, eps_w: float) -> float:
    key = f"{seed}:{theta_e_deg:.9f}".encode()
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
    return eps_w * (2.0 * value - 1.0)


def assert_synthetic(model) -> None:
    """Защита от подмены: метрики этого модуля определены только для
    синтетической аналитической модели. Реальный FEM ROM должен поставлять
    собственный psi_delta_scale из своего summary.json и эталон T_exact —
    синтетическая производная не становится эталоном реальной машины."""
    if not isinstance(model, d1.Synthetic):
        raise TypeError("torque_metrics определён только для Synthetic; "
                        "реальный FEM требует отдельного data path (TZ_PHASE4 §13.2.4)")


INVARIANT_TOL = 1e-9


def check_invariants(r: dict) -> None:
    """Блокирующая проверка инвариантов: нарушение — ошибка, не диагностика.
    NaN проходит неравенства незамеченным — проверяем конечность и знак явно."""
    for name in ("e_trunc", "e_noise", "e_corner", "e_total", "worst_case", "t_peak"):
        v = r[name]
        if not math.isfinite(v):
            raise RuntimeError(f"метрика {name} не конечна: {v!r}")
        if v < 0.0:
            raise RuntimeError(f"метрика {name} отрицательна: {v!r}")
    if r["e_noise"] > r["e_corner"] + INVARIANT_TOL:
        raise RuntimeError(f"инвариант нарушен: e_noise {r['e_noise']:.6e} > "
                           f"e_corner {r['e_corner']:.6e}")
    if r["e_total"] > r["e_trunc"] + r["e_corner"] + INVARIANT_TOL:
        raise RuntimeError(f"инвариант нарушен: e_total {r['e_total']:.6e} > "
                           f"e_trunc+e_corner {r['e_trunc'] + r['e_corner']:.6e}")


def torque_metrics(model, eps_w: float, h_m_deg: float, a_cog: float,
                   phi: float = PHI, seeds=SEEDS):
    assert_synthetic(model)
    """Четыре метрики для чистого cogging W'_cog = a_cog cos(6p th_m + phi)
    при i=0 на плотной сетке (0.05° мех, период 20°). Все нормированы на
    T_peak = 6p a_cog. Возвращает доли (не проценты)."""
    zero = (0.0, 0.0, 0.0)
    t_peak = 6.0 * P * a_cog
    h_m = math.radians(h_m_deg)
    h_e = h_m_deg * P
    thetas_m = [0.05 * k for k in range(0, 401)]

    def exact(tm):
        return -6.0 * P * a_cog * math.sin(math.radians(6.0 * P * tm) + phi)

    e_trunc = 0.0
    fd_clean = {}
    for tm in thetas_m:
        th_e = math.degrees(math.radians(tm) * P)
        fd = (model.energy(zero, th_e + h_e)
              - model.energy(zero, th_e - h_e)) / (2.0 * h_m)
        fd_clean[tm] = fd
        e_trunc = max(e_trunc, abs(fd - exact(tm)) / t_peak)

    e_noise = 0.0
    e_total = 0.0
    for seed in seeds:
        for tm in thetas_m:
            th_e = math.degrees(math.radians(tm) * P)
            wp = model.energy(zero, th_e + h_e) + noise(th_e + h_e, seed, eps_w)
            wm = model.energy(zero, th_e - h_e) + noise(th_e - h_e, seed, eps_w)
            fd = (wp - wm) / (2.0 * h_m)
            e_noise = max(e_noise, abs(fd - fd_clean[tm]) / t_peak)
            e_total = max(e_total, abs(fd - exact(tm)) / t_peak)

    e_corner = eps_w / h_m / t_peak
    r = dict(t_peak=t_peak, e_trunc=e_trunc, e_noise=e_noise,
             e_corner=e_corner, e_total=e_total,
             worst_case=e_trunc + e_corner)
    check_invariants(r)
    return r


def scan_h_minimum(model, eps_w: float, a_cog: float, phi: float = PHI,
                   h_lo: float = 0.2, h_hi: float = 1.5, n: int = 261):
    assert_synthetic(model)
    """Плотный scan
    h в [h_lo, h_hi] с шагом ~(h_hi-h_lo)/(n-1). trunc вычисляется без шума,
    corner аналитически. Возвращает (h_opt, worst_min, таблицу краёв)."""
    def worst_at(h):
        return worst_case_at(model, eps_w, a_cog, h, phi)
    best = (None, float("inf"))
    for k in range(n):
        h = h_lo + (h_hi - h_lo) * k / (n - 1)
        worst = worst_at(h)
        if worst < best[1]:
            best = (h, worst)
    return best


def worst_case_at(model, eps_w: float, a_cog: float, h: float, phi: float = PHI) -> float:
    """worst-case ошибка (trunc + corner, доли) при заданном h, град. мех."""
    assert_synthetic(model)
    zero = (0.0, 0.0, 0.0)
    t_peak = 6.0 * P * a_cog
    h_m = math.radians(h)
    h_e = h * P
    trunc = 0.0
    for kk in range(0, 401):
        tm = 0.05 * kk
        th_e = math.degrees(math.radians(tm) * P)
        exact = -6.0 * P * a_cog * math.sin(math.radians(6.0 * P * tm) + phi)
        fd = (model.energy(zero, th_e + h_e)
              - model.energy(zero, th_e - h_e)) / (2.0 * h_m)
        trunc = max(trunc, abs(fd - exact) / t_peak)
    return trunc + eps_w / h_m / t_peak


def refine_minimum(model, eps_w: float, a_cog: float, h0: float,
                   phi: float = PHI, h_lo: float = 0.2, h_hi: float = 1.5):
    """Локальное уточнение минимума scan золотым сечением в окрестности h0.
    Возвращает (h_opt, worst, info): устойчивость и проверку границ."""
    assert_synthetic(model)
    def f(h):
        return worst_case_at(model, eps_w, a_cog, h, phi)
    span = 0.1 * (h_hi - h_lo) / 2.0
    lo, hi = max(h_lo, h0 - span), min(h_hi, h0 + span)
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd_ = f(c), f(d)
    for _ in range(200):
        if abs(b - a) < 1e-4:
            break
        if fc < fd_:
            b, d, fd_ = d, c, fc
            c = b - gr * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd_
            d = a + gr * (b - a)
            fd_ = f(d)
    h_opt = 0.5 * (a + b)
    w_opt = f(h_opt)
    info = {
        "at_left_boundary": abs(h_opt - h_lo) < 1e-3,
        "at_right_boundary": abs(h_opt - h_hi) < 1e-3,
        "boundary_values": {"h_lo": f(h_lo), "h_hi": f(h_hi)},
        "interior": h_lo + 1e-3 < h_opt < h_hi - 1e-3,
    }
    return h_opt, w_opt, info


def verdict(metrics) -> str:
    """V0.4: решающее правило — worst-case; E_total контрольная."""
    return "PASS" if metrics["worst_case"] <= 0.01 else "FAIL"
