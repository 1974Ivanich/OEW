"""C1-C3 h-эксперимент по TZ_PHASE4 §11: выбор torque_h_mech_deg.

Чистый cogging при i=0: W'_cog = A6 cos(6p theta_m + phi), точный момент
известен аналитически. Плотная угловая сетка (не 12 точек ROM).
eps_W = 1e-3 * psi_delta_scale * I_r — frozen эталон шума.
"""
import math
import sys
import hashlib

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
import bm1109_doe1 as d1

P = 3
IR = 5.0

# psi_delta_scale синтетики: max |psi(i,theta) - psi(0,theta)| по сетке/углам
model_scale = d1.Synthetic(pole_pairs=P, psi_f=0.0)
scale = 0.0
for x in (-1.0, -0.5, 0.5, 1.0):
    for th in range(0, 360, 30):
        uvw = d1.dq0_to_uvw(x * IR, 0.0, 0.0, math.radians(th))
        psi = model_scale.flux_uvw(uvw, float(th))
        psi0 = model_scale.flux_uvw((0.0, 0.0, 0.0), float(th))
        scale = max(scale, max(abs(a - b) for a, b in zip(psi, psi0)))
print(f"psi_delta_scale ~= {scale:.3e} Wb")
EPS_W = 1e-3 * scale * IR
print(f"eps_W = {EPS_W:.3e} J")

def noise(theta_e_deg: float, seed: int) -> float:
    """Детерминированный шум в [-eps_W, +eps_W] на evaluation."""
    key = f"{seed}:{theta_e_deg:.9f}".encode()
    value = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
    return EPS_W * (2.0 * value - 1.0)

def run(a_cog: float, phi: float, noise_mode: str, seeds=(1, 2, 3)):
    model = d1.Synthetic(pole_pairs=P, a_cog=a_cog, phi_cog=phi)
    zero = (0.0, 0.0, 0.0)
    t_peak = 6.0 * P * a_cog
    h_candidates = [0.25, 0.5, 0.78, 1.0, 1.667, 5.0]     # мех. градусы
    # плотная сетка: период cogging 360/(6p) = 20° мех, шаг 0.05°
    thetas_m = [0.05 * k for k in range(0, 401)]
    rows = []
    for h_m_deg in h_candidates:
        h_m = math.radians(h_m_deg)
        h_e = math.degrees(h_m) * P                       # эл. градусы сдвига
        c1_worst = c2_worst = 0.0
        for tm in thetas_m:
            th_e = math.degrees(math.radians(tm) * P)     # эл. градусы точки
            exact = -6.0 * P * a_cog * math.sin(math.radians(6.0 * P * tm) + phi)
            w_plus = model.energy(zero, th_e + h_e)
            w_minus = model.energy(zero, th_e - h_e)
            fd = (w_plus - w_minus) / (2.0 * h_m)
            err = abs(fd - exact)
            c1_worst = max(c1_worst, err / t_peak)
            if abs(exact) < 0.05 * t_peak:                # окрестность нулей
                c2_worst = max(c2_worst, err / t_peak)
        # C3: seeded noise, полная ошибка vs exact
        c3_worst = 0.0
        for seed in seeds:
            for tm in thetas_m:
                th_e = math.degrees(math.radians(tm) * P)
                exact = -6.0 * P * a_cog * math.sin(math.radians(6.0 * P * tm) + phi)
                wp = model.energy(zero, th_e + h_e) + noise(th_e + h_e, seed)
                wm = model.energy(zero, th_e - h_e) + noise(th_e - h_e, seed)
                fd = (wp - wm) / (2.0 * h_m)
                c3_worst = max(c3_worst, abs(fd - exact) / t_peak)
        corner = EPS_W / h_m / t_peak                     # worst-case corner
        rows.append(dict(h=h_m_deg, c1=c1_worst, c2=c2_worst,
                         c3=c3_worst, corner=corner))
    return t_peak, rows

print()
for a_cog, label in ((1e-3, "A6=1e-3 J"), (5e-3, "A6=5e-3 J")):
    t_peak, rows = run(a_cog, 0.3, "seeded")
    print(f"{label}: T_peak = {t_peak:.3e} N·m, 1% = {0.01*t_peak:.3e}")
    print(f"{'h_m°':>7} {'C1_trunc':>9} {'C2_zeros':>9} {'C3_noise':>9} {'corner':>9}  verdict")
    for r in rows:
        ok = r['c1'] <= 0.01 and r['c2'] <= 0.01 and r['c3'] <= 0.01
        print(f"{r['h']:7.2f} {r['c1']*100:8.3f}% {r['c2']*100:8.3f}% {r['c3']*100:8.3f}% {r['corner']*100:8.3f}%  {'PASS' if ok else 'fail'}")
    print()
