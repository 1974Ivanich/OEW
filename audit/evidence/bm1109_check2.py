"""Дополнение: §4 против L_opta (как и заявлено в документе) и закон k_bw."""
import math
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "bm", r"C:\Users\190\AppData\Local\hermes\cache\scratch\bm1109_check.py")
# выполнять модуль не нужно целиком (он печатает) — просто пересчитаем здесь
N = 7200


def prof(x, R):
    x = x % 360.0
    if x < 120.0:
        return 1.0
    if x < 180.0:
        # переход 1 -> -1: рампа R, площадка 60-2R, рампа R
        Z = 60.0 - 2.0 * R
        if x < 120.0 + R:
            return 1.0 - (x - 120.0) / R
        if x < 120.0 + R + Z:
            return 0.0
        return -((x - 120.0 - R - Z) / R)
    if x < 300.0:
        return -1.0
    Z = 60.0 - 2.0 * R
    if x < 300.0 + R:
        return -1.0 + (x - 300.0) / R
    if x < 300.0 + R + Z:
        return 0.0
    return (x - 300.0 - R - Z) / R


print("=== §4: screening против L_opta (как заявлено в §4: 'оптимум по среднему моменту') ===")
for Rv in (0, 5, 10, 20, 30):
    e = []
    for i in range(N):
        th = 360.0 * i / N
        ph = [prof(th, Rv), prof(th - 120.0, Rv), prof(th - 240.0, Rv)]
        m = sum(ph) / 3.0
        e.append([v - m for v in ph])
    E2 = [sum(v * v for v in ph) for ph in e]
    L_opta = 1.0 / (sum(E2) / N)
    sc = sum(e[i][0] * math.cos(2 * math.pi * i / N) for i in range(N)) * 2.0 / N
    ss = sum(e[i][0] * math.sin(2 * math.pi * i / N) for i in range(N)) * 2.0 / N
    A1 = math.hypot(sc, ss)
    L_sin = 1.0 / (1.5 * A1 * A1)
    for nmax in (13, 59):
        s = 0.0
        for n in range(2, nmax + 1):
            if n % 3 == 0:
                continue
            cn = sum(e[i][0] * math.cos(2 * math.pi * n * i / N) for i in range(N)) * 2.0 / N
            sn = sum(e[i][0] * math.sin(2 * math.pi * n * i / N) for i in range(N)) * 2.0 / N
            s += (math.hypot(cn, sn) / A1) ** 2
        exact = L_opta / L_sin
        est = 1.0 / (1.0 + s)
        print(f"R={Rv:<3} n<={nmax:<3} exact L_opta/L_sin={exact:.5f} est={est:.5f} "
              f"ошибка={abs(est - exact) / exact * 100:.2f}%")

print("\n=== §8.6: линейный закон против (2k-k^2) ===")
print("k_bw   лин.доля  верная доля  отн.разница  ΔP при ΔP_ideal=2.9% P_DC: лин / верн")
for k in (0.9, 0.7, 0.5, 0.33, 0.2, 0.1, 0.05):
    lin = k
    quad = 2 * k - k * k
    print(f"{k:<6} {lin:<9.3f} {quad:<12.3f} {quad / lin:<12.2f} "
          f"{2.9 * lin:.2f}% / {2.9 * quad:.2f}%")

print("\n=== §7: рабочая точка при n=13 и fc=333 Гц ===")
for n in (7, 11, 13):
    row = []
    for p in (4, 6, 7, 8, 11, 12):
        row.append(f"p={p}:{60 * 333.3 / (3 * n * p):.0f}")
    print(f"n={n:<3} " + "  ".join(row))
