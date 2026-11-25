"""Проверка чисел §2–§7 документа BM1109_LOSS_OPTIMAL_CONTROL.md.

Форма ЭДС (§2.1): кусочно-линейная трапеция, полка F=120, площадки Z=60-2R,
рампы R, амплитуда полки 1. Фазы сдвинуты на 120, мгновенная нулевая
последовательность удалена. P = 1, потери нормированы на R*P^2.
"""
import math

N = 7200
TWO_PI = 2.0 * math.pi


def prof(th):
    """th в градусах [0,360). R передаётся через глобальную переменную R."""
    x = th % 360.0
    F, Z = 120.0, 60.0 - 2.0 * R
    if R == 0.0:
        # идеальная трапеция: полка 120, площадка 60
        if x < 120.0:
            return 1.0
        if x < 180.0:
            return 0.0
        if x < 300.0:
            return -1.0
        return 0.0
    if x < F:
        return 1.0
    if x < F + R:                       # спад 1 -> 0
        return 1.0 - (x - F) / R
    if x < F + R + Z:                   # площадка 0
        return 0.0
    if x < 180.0:                       # спад 0 -> -1
        return -((x - F - R - Z) / R)
    if x < 300.0:                       # полка -1
        return -1.0
    if x < 300.0 + R:                   # подъём -1 -> 0
        return -1.0 + (x - 300.0) / R
    if x < 300.0 + R + Z:               # площадка 0
        return 0.0
    return (x - 300.0 - R - Z) / R      # подъём 0 -> 1


def analyse(Rval):
    global R
    R = Rval
    e = []
    for i in range(N):
        th = 360.0 * i / N
        ph = [prof(th), prof(th - 120.0), prof(th - 240.0)]
        m = sum(ph) / 3.0
        e.append([v - m for v in ph])
    E2 = [sum(v * v for v in ph) for ph in e]
    meanE2 = sum(E2) / N
    mean_inv = sum(1.0 / v for v in E2) / N
    L_optc = mean_inv          # P=1
    L_opta = 1.0 / meanE2
    # 6-step: две фазы с max|e|, знак по знаку ЭДС
    q = 0.0
    for ph in e:
        idx = sorted(range(3), key=lambda k: -abs(ph[k]))[:2]
        q += sum(ph[k] * (1.0 if ph[k] > 0 else -1.0) for k in idx)
    q /= N
    L_6st = 2.0 * (1.0 / q) ** 2
    # основная гармоника фазы a
    sc = sum(e[i][0] * math.cos(TWO_PI * i / N) for i in range(N)) * 2.0 / N
    ss = sum(e[i][0] * math.sin(TWO_PI * i / N) for i in range(N)) * 2.0 / N
    A1 = math.hypot(sc, ss)
    L_sin = 1.0 / (1.5 * A1 * A1)              # корректно: P^2/(1.5 A1^2)
    L_sin_doc_formula = 1.5 / (A1 * A1)        # как в приложении документа
    # спектральная screening-оценка §4
    s = 0.0
    for n in range(2, 60):
        if n % 3 == 0:
            continue
        cn = sum(e[i][0] * math.cos(TWO_PI * n * i / N) for i in range(N)) * 2.0 / N
        sn = sum(e[i][0] * math.sin(TWO_PI * n * i / N) for i in range(N)) * 2.0 / N
        an = math.hypot(cn, sn)
        s += (an / A1) ** 2
    L_spec = L_sin / (1.0 + s)
    return dict(A1=A1, L_optc=L_optc, L_opta=L_opta, L_6st=L_6st, L_sin=L_sin,
                L_sin_doc=L_sin_doc_formula, sum_h=s, L_spec=L_spec)


print("=== синусоидальная ЭДС: контроль схемы (ожидается L_optc=L_opta=2/3) ===")


def sine_analyse():
    e = []
    for i in range(N):
        th = TWO_PI * i / N
        e.append([math.sin(th), math.sin(th - TWO_PI / 3), math.sin(th - TWO_PI * 2 / 3)])
    E2 = [sum(v * v for v in ph) for ph in e]
    return sum(1.0 / v for v in E2) / N, 1.0 / (sum(E2) / N)


a, b = sine_analyse()
print(f"L_optc={a:.4f}  L_opta={b:.4f}  отношение={a/b:.4f}\n")

doc = {0: (0.5000, 0.5000, 0.5000, 0.5483),
       5: (0.4922, 0.4909, 0.5000, 0.5230),
       10: (0.4845, 0.4821, 0.5000, 0.5019),
       20: (0.4690, 0.4655, 0.5000, 0.4705),
       30: (0.4534, 0.4500, 0.5000, 0.4510)}

print("=== §3: расчёт vs таблица документа ===")
print("R   L_optc  док     L_opta  док     L_6st   док     L_sin   док    A1")
for Rv in (0, 5, 10, 20, 30):
    r = analyse(Rv)
    d = doc[Rv]
    print(f"{Rv:<3} {r['L_optc']:.4f} {d[0]:.4f}   {r['L_opta']:.4f} {d[1]:.4f}   "
          f"{r['L_6st']:.4f} {d[2]:.4f}   {r['L_sin']:.4f} {d[3]:.4f}   {r['A1']:.5f}")

print("\n=== колонки '% хуже' — против чего считаны ===")
for Rv in (0, 5, 10, 20, 30):
    r = analyse(Rv)
    i6c = (math.sqrt(r['L_6st'] / r['L_optc']) - 1) * 100
    i6a = (math.sqrt(r['L_6st'] / r['L_opta']) - 1) * 100
    isc = (math.sqrt(r['L_sin'] / r['L_optc']) - 1) * 100
    isa = (math.sqrt(r['L_sin'] / r['L_opta']) - 1) * 100
    print(f"R={Rv:<3} 6-step: vs optc {i6c:.2f}%  vs opta {i6a:.2f}% | "
          f"sine: vs optc {isc:.2f}%  vs opta {isa:.2f}%")

print("\n=== приложение: формула L_sin ===")
for Rv in (0, 5, 10, 20, 30):
    r = analyse(Rv)
    print(f"R={Rv:<3} L_sin(корректно 2/3)={r['L_sin']:.4f}  "
          f"L_sin(как в приложении 1.5)={r['L_sin_doc']:.4f}  A1={r['A1']:.5f}")

print("\n=== §4: screening 1/(1+Σ(e_n/e1)^2) против точного L_sin/L_optc ===")
for Rv in (0, 5, 10, 20, 30):
    r = analyse(Rv)
    exact = r['L_optc'] / r['L_sin']
    est = 1.0 / (1.0 + r['sum_h'])
    print(f"R={Rv:<3} Σe_n/e1^2={r['sum_h']:.4f}  exact Lopt/Lsin={exact:.4f}  "
          f"оценка={est:.4f}  ошибка={abs(est - exact) / exact * 100:.2f}%")

print("\n=== §7: N_max = 60*fc/(3*n*p) ===")
for fc in (330.0, 333.3):
    for n in (5, 7, 11, 13):
        print(f"fc={fc} n={n}: N_max = {60 * fc / (3 * n):.1f}/p  -> p=4: "
              f"{60 * fc / (3 * n * 4):.0f} об/мин, p=6: {60 * fc / (3 * n * 6):.0f}, "
              f"p=8: {60 * fc / (3 * n * 8):.0f}")

print("\n=== §1.2.3 / OEW §5: энергетический баланс ===")
for Rs in (13.0, 14.0, 15.0):
    for I in (1.0, 2.0):
        print(f"Rs={Rs} Ом, I={I} А: медь 3*I^2*Rs = {3 * I * I * Rs:.0f} Вт; "
              f"P_DC при 48 В и том же токе = {48 * I:.0f} Вт")
print("G6: t_d/T_sw*U_dc при 1.5 мкс и 48 В: 5 кГц ->",
      f"{1.5 / 200 * 48:.2f} В, 10 кГц -> {1.5 / 100 * 48:.2f} В")
print("шестиугольник: вписанная окружность Vdc/sqrt3 =",
      f"{1 / math.sqrt(3):.4f} Vdc; вершина 2/3 Vdc = {2 / 3:.4f} Vdc; "
      f"Vdc/sqrt2 = {1 / math.sqrt(2):.4f} Vdc")
print("\n=== OEW §1: L_star/L_OEW = 1+(E3/E1)^2 ===")
for k in (0.05, 0.20, 0.35):
    r = 1 + k * k
    print(f"E3/E1={k}: потери +{(r - 1) * 100:.2f}%, RMS-ток +{(math.sqrt(r) - 1) * 100:.2f}%")
