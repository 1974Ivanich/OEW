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


def c_n(h_m_deg: float, n: int = 6, p: int = P) -> float:
    """Доля усечения центральной разности для гармоники n по электрическому углу.

    c_n(h) = 1 - sin(n*p*h)/(n*p*h); h — механический шаг в ГРАДУСАХ на входе
    (в формуле — радианы), n = 6 по определению теста TZ_PHASE4 §12.2, p — пары
    полюсов. Возвращает безразмерную долю, не проценты. Допустимо 0 < h < 180/(n*p).
    """
    if not (0.0 < h_m_deg < 180.0 / (n * p)):
        raise ValueError(f"h={h_m_deg!r}° вне (0, {180.0 / (n * p):.6f})°")
    x = n * p * math.radians(h_m_deg)
    return 1.0 - math.sin(x) / x


def h_max_c1(n: int = 6, p: int = P, tol: float = 0.01) -> float:
    """Граница области C3 — тот же корень, что h_max(C1): c_n(h) = tol.

    Совпадение по построению: |sinc-1| = 1-sinc при 0 < sinc < 1.
    Возвращает h в механических градусах; при n=6, p=3, tol=1% -> 0.780871°.
    """
    if not (0.0 < tol < 1.0):
        raise ValueError(f"tol={tol!r} вне (0, 1)")
    lo, hi = 0.0, 180.0 / (n * p)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if c_n(mid, n, p) < tol:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def t_min(h_m_deg: float, eps_w: float, n: int = 6, p: int = P,
          tol: float = 0.01) -> float:
    """Минимальная T_peak, при которой worst-case критерий выполняется при данном h.

    T_min = (eps_W / h_m) / (tol - c_n(h)), Н·м: из c_n + eps_W/(h_m*T_peak) <= tol.
    При c_n(h) >= tol конечной амплитуды не существует -> inf (область пуста).
    """
    if not math.isfinite(eps_w) or eps_w <= 0.0:
        raise ValueError(f"eps_w={eps_w!r} должно быть конечным и > 0")
    if not (0.0 < tol < 1.0):
        raise ValueError(f"tol={tol!r} вне (0, 1)")
    c = c_n(h_m_deg, n, p)
    if c >= tol:
        return float("inf")
    return (eps_w / math.radians(h_m_deg)) / (tol - c)


def applicability(eps_w: float, h_frozen: float = 0.5, n: int = 6, p: int = P,
                  tol: float = 0.01, h_lo: float = 0.2, h_hi: float = 0.78) -> dict:
    """Область применимости замороженного шага: нормативная точка и справочный минимум.

    Две разные постановки, их нельзя смешивать:
      * t_min_frozen — минимальная амплитуда при замороженном h (норматив);
      * h_tmin_opt / t_min_opt — минимум функции T_min(h) по h (справочно).
    Граница области — h_max_c1(); при h >= h_max конечной амплитуды нет.
    """
    if not math.isfinite(eps_w) or eps_w <= 0.0:
        raise ValueError(f"eps_w={eps_w!r} должно быть конечным и > 0")

    def f(h):
        return t_min(h, eps_w, n, p, tol)

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = h_lo, h_hi
    c1, c2 = b - gr * (b - a), a + gr * (b - a)
    f1, f2 = f(c1), f(c2)
    for _ in range(200):
        if abs(b - a) < 1e-9:
            break
        if f1 < f2:
            b, c2, f2 = c2, c1, f1
            c1 = b - gr * (b - a)
            f1 = f(c1)
        else:
            a, c1, f1 = c1, c2, f2
            c2 = a + gr * (b - a)
            f2 = f(c2)
    h_opt = 0.5 * (a + b)
    return {"eps_w_J": eps_w, "tol": tol, "n": n, "p": p,
            "h_frozen_deg": h_frozen, "c_frozen": c_n(h_frozen, n, p),
            "t_min_frozen_Nm": f(h_frozen),
            "h_tmin_opt_deg": h_opt, "t_min_opt_Nm": f(h_opt),
            "h_max_deg": h_max_c1(n, p, tol)}


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
    """Четыре метрики для чистого cogging W'_cog = a_cog cos(6p th_m + phi)
    при i=0 на плотной сетке (0.05° мех, период 20°). Все нормированы на
    T_peak = 6p a_cog. Возвращает доли (не проценты)."""
    assert_synthetic(model)
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
    """Плотный scan по h в [h_lo, h_hi] с шагом ~(h_hi-h_lo)/(n-1).

    trunc считается без шума, corner аналитически; возвращает (h_opt, worst_min).
    Целевая функция — worst-case ошибка при фиксированной амплитуде; это НЕ то же,
    что минимум требуемой амплитуды T_min(h) (см. applicability()).
    """
    assert_synthetic(model)
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
