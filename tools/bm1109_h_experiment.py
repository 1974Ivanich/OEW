"""C1-C3 h-эксперимент по TZ_PHASE4 (редакция V0.4): выбор torque_h_mech_deg.

eps_W — из psi_delta_scale, вычисленного как в frozen P2-N1 (dq0-компоненты,
полная ROM-сетка), НЕ локальным d-only свипом. Решающее правило PASS —
worst-case E_trunc + E_corner <= 1%; E_total — контрольная метрика.
Воспроизводимость: bounded-uniform шум, seeds (1,2,3), phi = 0.3.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_doe1 as d1
import bm1109_torque_metrics as tm

scale = tm.psi_delta_scale_rom(d1.Synthetic(pole_pairs=tm.P))
EPS_W = tm.eps_w_from_scale(scale)
print(f"psi_delta_scale (P2-N1 ROM, dq0) = {scale:.3e} Wb")
print(f"eps_W = {EPS_W:.3e} J")

print()
print(f"{'case':>10} {'h_m°':>7} {'E_trunc':>9} {'E_noise':>9} {'E_corner':>9}"
      f" {'E_total':>9} {'trunc+corner':>12}  verdict(V0.4)")
for a_cog, label in ((1e-3, "A6=1e-3"), (5e-3, "A6=5e-3")):
    model = d1.Synthetic(pole_pairs=tm.P, a_cog=a_cog, phi_cog=tm.PHI)
    for h in (0.25, 0.5, 0.78, 1.0, 1.667, 5.0):
        r = tm.torque_metrics(model, EPS_W, h, a_cog)
        print(f"{label:>10} {h:7.2f} {r['e_trunc']*100:8.3f}% {r['e_noise']*100:8.3f}%"
              f" {r['e_corner']*100:8.3f}% {r['e_total']*100:8.3f}%"
              f" {r['worst_case']*100:11.3f}%  {tm.verdict(r)}")
    print()
