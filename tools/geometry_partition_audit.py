#!/usr/bin/env python3
"""Аудит разбиения окружности: предикаты секторов vs прямоугольники certifier'а.

Отвечает на вопрос «вариант A (partition) или вариант B (намеренные dead zones)»
числами, а не мнением. Ничего не меняет, только читает.

    py -3 tools/geometry_partition_audit.py [dataset.txt]

Проверяет три вещи:
  1. θ-покрытие предикатов секторов как они записаны в коде/TZ (набор точек окружности);
  2. θ-допуск прямоугольников geometry-режима (map_region_certifier.c:21-71)
     при заданных порогах — то есть что реально сертифицируется;
  3. проходят ли 32 cell каждого ряда датасета через свой прямоугольник
     при «adjusted» порогах и при порогах TZ.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

# Предикаты секторов: map_region_certifier.c:36-69 / TZ_MAP_REGION_GEOMETRY_REFACTOR.md §4
SECTOR_PREDICATE = {
    0: lambda u, v, w: u > 0 > v > w,
    1: lambda u, v, w: u > w > v and u > 0 > v,
    2: lambda u, v, w: v > 0 > u > w,
    3: lambda u, v, w: v > w > u and v > 0 > u,
    4: lambda u, v, w: w > 0 > v > u,
    5: lambda u, v, w: w > u > v and w > 0 > v,
}

ZONES = {
    "adjusted 7500/9000/9000/9600": (7500, 9000, 9000, 9600),
    "TZ 6000/10000/10000/14000": (6000, 10000, 10000, 14000),
}


def mod_triple(theta_deg: float, amp: float) -> tuple[float, float, float]:
    r = math.radians(theta_deg)
    return (amp * math.cos(r),
            amp * math.cos(r - math.radians(120)),
            amp * math.cos(r + math.radians(120)))


def sector_rect(sector: int, window: int, zones: tuple[int, int, int, int]):
    """map_region_certifier.c:21-71 — axis-aligned bounds по сектору и окну."""
    w0_min, w0_max, w1_min, w1_max = zones
    mod_min, mod_max = (w0_min, w0_max) if window == 0 else (w1_min, w1_max)
    b = {}
    if sector == 0:
        b = {"mu": (mod_min, mod_max), "mv": (-mod_max, -1), "mw": (-mod_max, -mod_min)}
    elif sector == 1:
        b = {"mu": (mod_min, mod_max), "mw": (-mod_max, -1), "mv": (-mod_max, -mod_min)}
    elif sector == 2:
        b = {"mv": (mod_min, mod_max), "mu": (-mod_max, -1), "mw": (-mod_max, -mod_min)}
    elif sector == 3:
        b = {"mv": (mod_min, mod_max), "mw": (-mod_max, -1), "mu": (-mod_max, -mod_min)}
    elif sector == 4:
        b = {"mw": (mod_min, mod_max), "mv": (-mod_max, -1), "mu": (-mod_max, -mod_min)}
    elif sector == 5:
        b = {"mw": (mod_min, mod_max), "mu": (-mod_max, -1), "mv": (-mod_max, -mod_min)}
    return b


def inside(bounds: dict, mods: tuple[float, float, float]) -> bool:
    u, v, w = mods
    d = {"mu": u, "mv": v, "mw": w}
    return all(lo <= d[k] <= hi for k, (lo, hi) in bounds.items())


def load_rows(path: Path):
    rows = defaultdict(list)
    row = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line.split()
        if not t or t[0].startswith("#"):
            continue
        if t[0] == "row":
            d = dict(kv.split("=", 1) for kv in t[1:])
            row = (int(d["sector"]), int(d["window"]))
        elif t[0] == "cell":
            d = dict(kv.split("=", 1) for kv in t[1:])
            rows[row].append((int(d["mu"]), int(d["mv"]), int(d["mw"])))
    return rows


def main() -> int:
    print("=== 1. θ-покрытие предикатов секторов (шаг 0.05°) ===")
    per_sector = {s: 0 for s in range(6)}
    dead = []
    for i in range(7200):
        th = i * 0.05 - 180.0
        u, v, w = mod_triple(th, 1.0)
        hits = [s for s, f in SECTOR_PREDICATE.items() if f(u, v, w)]
        assert len(hits) <= 1, f"перекрытие в θ={th}: секторы {hits}"
        if hits:
            per_sector[hits[0]] += 1
        else:
            dead.append(th)
    total = 7200
    for s in range(6):
        print(f"  sector {s}: {per_sector[s] * 0.05:6.2f}°")
    print(f"  покрыто всего: {sum(per_sector.values()) * 0.05:.1f}° "
          f"({100 * sum(per_sector.values()) / total:.1f} %), "
          f"dead zones: {len(dead) * 0.05:.1f}°")
    groups = []
    for th in dead:
        if groups and abs(th - groups[-1][-1] - 0.05) < 1e-9:
            groups[-1].append(th)
        else:
            groups.append([th])
    for g in groups:
        print(f"    dead zone: {g[0]:7.2f}° … {g[-1]:7.2f}°")

    print("\n=== 2. θ-допуск прямоугольников geometry-режима ===")
    print("  (амплитуда свипа 9459 Q15 = табличный вектор; «ни одной точки» — регион пуст)")
    for window, label in ((0, "window 0"), (1, "window 1")):
        for zname, zones in ZONES.items():
            hits = []
            for i in range(7200):
                th = i * 0.05 - 180.0
                for s in range(6):
                    if inside(sector_rect(s, window, zones), mod_triple(th, 9459.0)):
                        hits.append(th)
                        break
            width = len(hits) * 0.05
            rng = f"{min(hits):.1f}…{max(hits):.1f}°" if hits else "—"
            print(f"  {label}, {zname}: {width:5.2f}°  {rng}")

    print("\n=== 3. cell каждого ряда против своего прямоугольника ===")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "boar_geometry_dataset.txt")
    if not path.exists():
        print(f"  ({path} не найден — пропуск)")
        return 0
    rows = load_rows(path)
    verdict = {}
    for zname, zones in ZONES.items():
        print(f"  пороги: {zname}")
        fails = 0
        for key in sorted(rows):
            cells = rows[key]
            b = sector_rect(key[0], key[1], zones)
            outside = sum(1 for c in cells if not inside(b, c))
            if outside:
                fails += 1
            print(f"    sector {key[0]} window {key[1]}: outside {outside:2d}/{len(cells)}")
        verdict[zname] = fails

    print("\n=== 4. вердикт certifier'а (map_region_certifier.c:117-123) ===")
    print("  В geometry-режиме любой VALID-cell вне прямоугольника => MAP_CERT_VALID_OUTSIDE (7),")
    print("  конвейер fail-closed (MAP_PIPELINE_CERT_FAILED), артефакт не создаётся.")
    for zname, fails in verdict.items():
        state = "REJECT" if fails else "OK"
        print(f"  {zname}: рядов с выходом cells за регион — {fails}/12 -> geometry-артефакт: {state}")

    print("\nПримечание: центры векторов кампании имеют среднюю фазу ровно 0")
    print("(например (8192, 0, -8192) для sector 0), а прямоугольник требует mv <= -1")
    print("(map_region_certifier.c:39) — расхождение конструктивное, не вычислительное.")
    print("Авторитетный вердикт certifier'а воспроизводится C-пробником (см. отчёт §6.2).")
    return 1 if any(verdict.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
