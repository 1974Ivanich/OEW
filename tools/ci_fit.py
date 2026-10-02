#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci_fit.py — offline-подгонка калибровки токовых шунтов по окнам команды ci.

Модель (TZ_CURRENT_SHUNT_CALIBRATION.md, единая на канал):
    raw = offset + sign * gain * I_ref
    I_ref = V_shunt_окно / R_shunt          (V_shunt в мВ, R_shunt в Ом -> I_ref в мА)

Вход: CSV-строки окон (заголовок обязателен):
    channel,point,vshunt_mv,raw_avg
  channel: 1 | 2   point: метка точки (0A, 0p5A, 1A, ...)   vshunt_mv: среднее за окно

Дополнительно: --rshunt (Ом, по умолчанию 0.03), --zero-raw (среднее raw при 0 A;
если не задано — берётся точка с point=0A/zero), --gain-uv-per-a (вывод в единицах
прошивки ADC_DC_SHUNT_UV_PER_A).

Выход: offset, sign, gain [raw/A], R^2, max |остаток| [raw] и предложение
прошивочных коэффициентов для будущего ТЗ подстановки.
"""
import argparse
import csv
import sys


def read_points(path):
    pts = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ch = int(row["channel"])
            if ch not in (1, 2):
                raise SystemExit("channel должен быть 1 или 2: %r" % row)
            pts.append({
                "ch": ch,
                "point": row["point"].strip(),
                "vshunt_mv": float(row["vshunt_mv"]),
                "raw": float(row["raw_avg"]),
            })
    if not pts:
        raise SystemExit("нет строк данных")
    return pts


def zero_raw_for(pts, forced):
    if forced is not None:
        return forced
    zeros = [p for p in pts if p["point"].strip().lower() in ("0a", "0", "zero")]
    if not zeros:
        raise SystemExit("нет точки 0A и не задан --zero-raw")
    z = {}
    for p in zeros:
        z.setdefault(p["ch"], []).append(p["raw"])
    return {ch: sum(v) / len(v) for ch, v in z.items()}


def fit_channel(pts, ch, zraw, rshunt):
    data = []
    for p in pts:
        if p["ch"] != ch:
            continue
        i_ref_ma = p["vshunt_mv"] / rshunt          # мВ / Ом = мА
        dy = p["raw"] - zraw                        # raw - offset
        data.append((i_ref_ma, dy))
    if len(data) < 2:
        raise SystemExit("канал %d: нужно >= 2 точки (есть %d)" % (ch, len(data)))

    n = len(data)
    sx = sum(x for x, _ in data)
    sy = sum(y for _, y in data)
    sxx = sum(x * x for x, _ in data)
    sxy = sum(x * y for x, y in data)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        raise SystemExit("канал %d: вырожденная регрессия (все токи одинаковы?)" % ch)
    slope = (n * sxy - sx * sy) / denom             # raw на мА
    intercept = (sy - slope * sx) / n               # должен быть ~0 при верном offset
    sign = 1.0 if slope >= 0 else -1.0
    gain_per_a = abs(slope) * 1000.0                # raw на А
    mean_y = sy / n
    ss_tot = sum((y - mean_y) ** 2 for _, y in data)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in data)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    max_res = max(abs(y - (slope * x + intercept)) for x, y in data)
    return {
        "n": n,
        "offset": zraw,
        "sign": int(sign),
        "gain_raw_per_a": gain_per_a,
        "uv_per_a": 3300.0 * 1e6 / (4095.0 * gain_per_a) if gain_per_a > 0 else float("nan"),
        "intercept_raw": intercept,
        "r2": r2,
        "max_res_raw": max_res,
        "points": data,
    }


def main():
    ap = argparse.ArgumentParser(description="Offline-подгонка калибровки шунтов по окнам ci")
    ap.add_argument("csv", help="CSV: channel,point,vshunt_mv,raw_avg")
    ap.add_argument("--rshunt", type=float, default=0.03, help="сопротивление шунта, Ом (по умолчанию 0.03)")
    ap.add_argument("--zero-raw", type=float, default=None,
                    help="среднее raw при 0 A; иначе берётся точка point=0A")
    args = ap.parse_args()

    pts = read_points(args.csv)
    zraw_map = zero_raw_for(pts, args.zero_raw)

    print("=" * 72)
    print("Модель: raw = offset + sign * gain * I_ref;  I_ref = V_shunt_окно / R_shunt")
    print("R_shunt = %g Ом; точки: %d" % (args.rshunt, len(pts)))
    ok = True
    for ch in (1, 2):
        if not any(p["ch"] == ch for p in pts):
            continue
        zraw = zraw_map[ch] if isinstance(zraw_map, dict) else zraw_map
        r = fit_channel(pts, ch, zraw, args.rshunt)
        print("-" * 72)
        print("КАНАЛ %d (Inv%d):" % (ch, ch))
        print("  offset = %.2f raw; sign = %+d" % (r["offset"], r["sign"]))
        print("  gain = %.2f raw/A (%.1f uV/A после conditioning; прошивочный номинал 63000)" % (
            r["gain_raw_per_a"], r["uv_per_a"]))
        print("  intercept = %.3f raw (при верном offset должен быть ~0)" % r["intercept_raw"])
        print("  R^2 = %.6f; max |остаток| = %.2f raw" % (r["r2"], r["max_res_raw"]))
        for i_ref, dy in r["points"]:
            print("    I_ref=%9.2f мА  raw-offset=%9.2f" % (i_ref, dy))
        if r["r2"] < 0.999:
            print("  ВНИМАНИЕ: R^2 < 0.999 — проверить точку(и) или линейность тракта")
            ok = False
        if abs(r["intercept_raw"]) > 2.0:
            print("  ВНИМАНИЕ: intercept > 2 raw — смещение нуля дрейфует между окнами")
            ok = False
        print("  прошивочные коэффициенты (для будущего ТЗ подстановки):")
        print("    ADC_CH%d: sign=%+d gain_raw_per_a=%.2f => UV_PER_A=%.1f" % (
            ch, r["sign"], r["gain_raw_per_a"], r["uv_per_a"]))
    print("=" * 72)
    print("Проверка модели: %s" % ("замечаний нет" if ok else "ЕСТЬ замечания — см. выше"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
