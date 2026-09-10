#!/usr/bin/env python3
"""Verify the BOAR geometry dataset numbers: counts, seq continuity, sector
angle labels vs the firmware transition table, identity provenance.

Read-only. Usage:
    py -3 tools/verify_boar_geometry_dataset.py [dataset.txt]
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

# Firmware transition table — src/map_capture_profiles.c:225-230
BOARD_MOD = {
    0: (8192, 0, -8192),
    1: (8192, -8192, 0),
    2: (0, 8192, -8192),
    3: (-8192, 8192, 0),
    4: (0, -8192, 8192),
    5: (-8192, 0, 8192),
}
ORDERING = {0: "mu>mv>mw", 1: "mu>mw>mv", 2: "mv>mu>mw",
            3: "mv>mw>mu", 4: "mw>mu>mv", 5: "mw>mv>mu"}

# Current board profile constants — src/map_capture_profiles.c:187-219
BOARD_PROFILE = {"board": "7", "pwm": "294", "arr": "999", "trigger": "0x4F455731",
                 "toff": "0", "dt": "192", "clk": "42500000", "smp": "1281",
                 "res": "0", "acs": "0x26B9B97B", "ccs": "0x13552B12"}
SYNTHETIC = {"pwm": "5000", "acs": "0x13572468", "ccs": "0x24681357",
             "toff": "12", "dt": "68", "smp": "257", "clk": "170000000"}


def kv(line: str) -> dict:
    return dict(tok.split("=", 1) for tok in line.split()[1:])


def angle_deg(mu: float, mv: float, mw: float) -> float:
    """Electrical angle for m_x = A*cos(theta - phi_x), phi=(0,120,240)."""
    alpha = (2.0 * mu - mv - mw) / 3.0
    beta = (mv - mw) / math.sqrt(3.0)
    return math.degrees(math.atan2(beta, alpha))


def norm180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _ols3(xs: list[tuple[float, float]], ys: list[float]) -> float:
    """RMS остатка OLS-фита y = a + b*x1 + c*x2 (3 неизвестных)."""
    rows = [[1.0, x[0], x[1]] for x in xs]
    m = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    v = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(3)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(m[r][i]))
        if m[p][i] == 0.0:
            return float("nan")          # вырожденная система (мало точек/нет вариации)
        m[i], m[p] = m[p], m[i]
        v[i], v[p] = v[p], v[i]
        for r in range(i + 1, 3):
            f = m[r][i] / m[i][i]
            for c in range(i, 3):
                m[r][c] -= f * m[i][c]
            v[r] -= f * v[i]
    coef = [0.0, 0.0, 0.0]
    for i in (2, 1, 0):
        s = v[i] - sum(m[i][j] * coef[j] for j in range(i + 1, 3))
        coef[i] = s / m[i][i]
    res = [y - sum(r[j] * coef[j] for j in range(3)) for r, y in zip(rows, ys)]
    return (sum(t * t for t in res) / len(res)) ** 0.5


def build_report(path: Path) -> str:
    out: list[str] = []
    seqs = defaultdict(list)
    cells = defaultdict(list)
    vals = defaultdict(list)
    identity = provenance = None
    row = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line.split()
        if not t or t[0].startswith("#"):
            continue
        if t[0] == "identity":
            identity = kv(line)
        elif t[0] == "provenance":
            provenance = kv(line)
        elif t[0] == "row":
            d = kv(line)
            row = (int(d["sector"]), int(d["window"]))
        elif t[0] == "sample":
            d = kv(line)
            seqs[row].append(int(d["seq"]))
            vals[row].append((float(d["idc1"]), float(d["idc2"]),
                              float(d["refu"]), float(d["refv"]), float(d["refw"])))
        elif t[0] == "cell":
            d = kv(line)
            cells[row].append((int(d["mu"]), int(d["mv"]), int(d["mw"])))

    print = out.append  # noqa: A001 — report sink
    print(f"file       : {path}")
    print(f"identity   : {identity}")
    print(f"provenance : {provenance}")
    print(f"rows       : {len(seqs)} -> {sorted(seqs)}")
    print(f"samples    : {sum(len(v) for v in seqs.values())}"
          f"   cells: {sum(len(v) for v in cells.values())}")

    print("\n--- per-row seq continuity ---")
    gap_total = 0
    for key in sorted(seqs):
        s = sorted(seqs[key])
        gaps = [(a, b) for a, b in zip(s, s[1:]) if b - a != 1]
        missing = sum(b - a - 1 for a, b in gaps)
        gap_total += missing
        print(f"sector {key[0]} window {key[1]}: n={len(s):2d} "
              f"seq {s[0]}..{s[-1]} span={s[-1]-s[0]+1:3d} "
              f"missing={missing:3d} gaps={len(gaps)}")
    print(f"total missing seq numbers: {gap_total}")

    print("\n--- firmware transition table (src/map_capture_profiles.c:225-230) ---")
    for sec in range(6):
        mu, mv, mw = BOARD_MOD[sec]
        print(f"  sector {sec}: ({mu:6d},{mv:6d},{mw:6d}) "
              f"{ORDERING[sec]:8s} theta = {angle_deg(mu, mv, mw):7.1f} deg")

    print("\n--- dataset rows: mean cell angle vs table angle ---")
    worst = 0.0
    for key in sorted(cells):
        pts = cells[key]
        am = sum(angle_deg(*c) for c in pts) / len(pts)
        fi = angle_deg(*BOARD_MOD[key[0]])
        dev = norm180(am - fi)
        worst = max(worst, abs(dev))
        amp = sum(math.hypot((2 * c[0] - c[1] - c[2]) / 3.0,
                             (c[1] - c[2]) / math.sqrt(3.0)) for c in pts) / len(pts)
        print(f"  sector {key[0]} window {key[1]}: n={len(pts):2d} "
              f"angle {am:7.2f}  table {fi:7.2f}  dev {dev:+6.2f} deg  "
              f"|i|={amp:7.0f} Q15")
    print(f"worst row deviation from table angle: {worst:.2f} deg")

    print("\n--- OLS-остаток ref ~ (idc1, idc2): линейность или тождество? ---")
    for key in sorted(vals):
        xs = [(s[0], s[1]) for s in vals[key]]
        r = [_ols3(xs, [s[2] for s in vals[key]]),
             _ols3(xs, [s[3] for s in vals[key]]),
             _ols3(xs, [s[4] for s in vals[key]])]
        print(f"  sector {key[0]} window {key[1]}: rms(refu)={r[0]:.4f} "
              f"rms(refv)={r[1]:.4f} rms(refw)={r[2]:.4f} mA")
    print("  refu == idc1 и refv == idc2 тождественно -> фит тривиален: "
          "остаток не является свидетельством линейности ADC")

    print("\n--- identity vs CURRENT board profile constants ---")
    for k, expect in BOARD_PROFILE.items():
        got = (identity or {}).get(k)
        if got == expect:
            note = "OK"
        elif SYNTHETIC.get(k) == got:
            note = "MISMATCH <- SYNTHETIC placeholder"
        else:
            note = "MISMATCH"
        print(f"  {k:8s} dataset={str(got):12s} board={expect:12s} {note}")
    return "\n".join(out)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "boar_geometry_dataset.txt")
    print(build_report(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
