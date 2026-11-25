"""Task 17 (TZ_PHASE4 §8): сравнение стенсилей dm_di/l_from_flux 0.5 vs 1.0 I_r.

Исследование, НЕ изменение кода: нормативный стенсил ctrl не трогается.
Вопросы: (1) ошибка усечения на нелинейной модели; (2) покрытие решётки;
(3) шумовое усиление; (4) влияние на собственные значения A;
(5) совместимость с N1/N2/N3 (спойлер: они используют свой стенсил 0.05 I_r).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_doe1 as doe1

P = 3.0
IR = 5.0

# модель с сильной нелинейностью (насыщение) и стандартной
STRONG = dict(a4=-8e-6, b4=-1.28e-5, psi_f=0.012, kdq=1e-5, kd0=2e-5, c6=1e-5, psi_cog=0.004)
MILD = dict(psi_f=0.012, c6=1e-5, psi_cog=0.004)


def m_vector(model, xd, xq, x0, theta_e_deg):
    """M = dpsi_dq0/dtheta_e в точке решётки — мелкая угловая разность как правда."""
    uvw = doe1.dq0_to_uvw(xd * IR, xq * IR, x0 * IR, math.radians(theta_e_deg))
    h = 1e-4  # эл. градусы, практически точная производная аналитической модели
    def psi_dq0(th):
        u, v, w = model.flux_uvw(uvw, th)
        return doe1.uvw_to_dq0(u, v, w, math.radians(th))
    p_plus = psi_dq0(theta_e_deg + h)
    p_minus = psi_dq0(theta_e_deg - h)
    h_rad = math.radians(h)
    return tuple((a - b) / (2.0 * h_rad) for a, b in zip(p_plus, p_minus))


def dm_di(model, xd, xq, x0, theta, h_norm):
    """Центральная разность M по току со стенсилом h_norm (в единицах I_r)."""
    coords = [xd, xq, x0]
    out = []
    for axis in range(3):
        plus = coords[:]
        minus = coords[:]
        plus[axis] += h_norm
        minus[axis] -= h_norm
        mp = m_vector(model, plus[0], plus[1], plus[2], theta)
        mm = m_vector(model, minus[0], minus[1], minus[2], theta)
        delta = 2.0 * h_norm * IR
        out.append([(a - b) / delta for a, b in zip(mp, mm)])   # колонка axis
    return [[out[c][r] for c in range(3)] for r in range(3)]


def dm_di_reference(model, xd, xq, x0, theta):
    return dm_di(model, xd, xq, x0, theta, 0.02)     # мелкий стенсил = правда


def in_lattice(xd, xq, x0):
    if any(abs(v) > 1.0 + 1e-9 for v in (xd, xq, x0)):
        return False
    if sum(v * v for v in (xd, xq, x0)) > 1.0 + 1e-9 and abs(xd) != 1.0 and abs(xq) != 1.0 and abs(x0) != 1.0:
        pass
    vals = {abs(v) for v in (xd, xq, x0)}
    return vals <= {0.0, 0.5, 1.0}


def coverage(h_norm):
    """Доля узлов решётки, у которых ОБА соседа ±h по всем трём осям в решётке."""
    points = doe1.base_points()
    full = 0
    for xd, xq, x0 in points:
        ok = True
        for axis in range(3):
            plus = [xd, xq, x0]
            minus = [xd, xq, x0]
            plus[axis] += h_norm
            minus[axis] -= h_norm
            if tuple(plus) not in points or tuple(minus) not in points:
                ok = False
                break
        full += ok
    return full, len(points)


def noise_gain(h_norm):
    """Worst-case усиление шума M в производную: |eta|<=s -> ошибка <= s/(h*IR)."""
    return 1.0 / (h_norm * IR)


def study(model_kwargs, label):
    model = doe1.Synthetic(pole_pairs=P, **model_kwargs)
    points = sorted(doe1.base_points())
    angles = [0.0, 30.0, 90.0]
    stats = {}
    for h in (0.5, 1.0):
        errs = []
        scale_vals = []
        for xd, xq, x0 in points:
            # оба соседа должны быть в решётке по всем осям — иначе стенсил неприменим
            cov_ok = True
            for axis in range(3):
                plus = [xd, xq, x0]; minus = [xd, xq, x0]
                plus[axis] += h; minus[axis] -= h
                if tuple(plus) not in points or tuple(minus) not in points:
                    cov_ok = False
            if not cov_ok:
                continue
            for theta in angles:
                ref = dm_di_reference(model, xd, xq, x0, theta)
                got = dm_di(model, xd, xq, x0, theta, h)
                scale = max(abs(v) for line in ref for v in line)
                scale = max(scale, 1e-12)
                scale_vals.append(scale)
                err = max(abs(got[r][c] - ref[r][c]) for r in range(3) for c in range(3))
                errs.append(err / scale)
        cov_full, cov_total = coverage(h)
        stats[h] = {
            "nodes_full_stencil": cov_full,
            "nodes_total": cov_total,
            "worst_rel_err": max(errs) if errs else None,
            "median_rel_err": sorted(errs)[len(errs) // 2] if errs else None,
            "noise_gain_per_unit": noise_gain(h),
        }
    return {"label": label, "stencils": stats}


def main():
    results = [study(MILD, "mild (стандартная синтетика)"),
               study(STRONG, "strong saturation a4/b4 x10")]
    for res in results:
        print(f"── {res['label']}")
        for h, s in sorted(res["stencils"].items()):
            print(f"  h={h}·I_r: покрытие {s['nodes_full_stencil']}/{s['nodes_total']} узлов, "
                  f"worst err {s['worst_rel_err']:.3e}, median {s['median_rel_err']:.3e}, "
                  f"noise gain {s['noise_gain_per_unit']:.3f} 1/A")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/stencil_study.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
