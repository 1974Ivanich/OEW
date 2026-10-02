#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отчёт по V/f-сессии из лога @VFLOG: по каждой ступени target — установившиеся
значения и вердикт. Написан под ТЗ VF-3 (запись fslip_min/max, vmag, i1/i2, fault
на каждой ступени).

Использование:
    python vflog_step_report.py session.log [--pp 3] [--rated 50] [--settle-ms 10000]

Что делает:
  * читает текстовый лог UART (строки @VFLOG:...), прочие строки игнорирует;
  * режет поток на ступени: новая ступень = смена поля target (командованный rpm);
  * внутри ступени отбрасывает первые --settle-ms (по полю t=, дефолт 10000 мс =
    экспоненциальная рампа V/f с tau=2000 мс успевает сойтись на ~99 %);
  * печатает min/max по f_e, fslip, vmag, meas(rpm), i1, i2, vbus, долю commit=0,
    события fault, минимум sd1/sd2;
  * вердикт на ступень: OK либо причина (кламп fslip, потолок апертуры, fault,
    SD/EM_STOP активен, непрерывный commit=0, мало данных).

Код выхода: 0 — все ступени OK; 1 — есть ступени с замечаниями; 2 — нет данных.

Host-инструмент разбора лога: прошивку не меняет, запускается на ПК-3 рядом с сессией.
Копия входит в стендовый комплект ПК-3 (собирается scripts/assemble_pc3_bench_kit.py).
"""
from __future__ import annotations

import argparse
import sys


def force_safe_stdout():
    """Консоль без UTF-8 (cp1251/ascii) не должна ронять отчёт: незаменяемый символ -> '?'."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


SETTLED_MIN_TICKS = 50          # меньше — мало данных для вывода
COMMIT0_BAD_PCT = 20.0          # доля commit=0 в установившемся режиме
VMAG_APERTURE_CEILING_PCT = 73  # контракт апертуры: CCR 135..999 при mid=500
SLIP_CLAMP_HZ = 5


def parse_vflog(line: str):
    """Строка @VFLOG -> dict[int] или None. Терпимо к добавлению полей."""
    if "@VFLOG:" not in line:
        return None
    body = line[line.index("@VFLOG:") + len("@VFLOG:"):]
    body = body.split("\r", 1)[0].split("\n", 1)[0].strip()
    if not body:
        return None
    out = {}
    for part in body.split(":"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if not key:
            continue
        try:
            out[key] = int(value)
        except ValueError:
            return None
    return out if "target" in out and "t" in out else None


class Stats:
    __slots__ = ("name", "target", "t0", "n_all", "n", "vals", "commit0",
                 "fault_events", "sd_low", "drp_first", "drp_last")

    def __init__(self, target, t0):
        self.name = []
        self.target = target
        self.t0 = t0
        self.n_all = 0
        self.n = 0
        self.vals = {}
        self.commit0 = 0
        self.fault_events = 0
        self.sd_low = 0
        self.drp_first = None
        self.drp_last = None

    def add(self, rec, settled: bool):
        self.n_all += 1
        if self.drp_first is None and "drp" in rec:
            self.drp_first = rec["drp"]
        if "drp" in rec:
            self.drp_last = rec["drp"]
        if not settled:
            return
        self.n += 1
        for key, value in rec.items():
            self.vals.setdefault(key, []).append(value)
        if rec.get("commit") == 0:
            self.commit0 += 1
        if rec.get("fault", 0) != 0:
            self.fault_events += 1
        if rec.get("sd1") == 0 or rec.get("sd2") == 0:
            self.sd_low += 1

    def rng(self, key):
        seq = self.vals.get(key)
        if not seq:
            return None
        return min(seq), max(seq)


def report(stats: list[Stats], pp: int, rated: int) -> int:
    bad = 0
    print("=" * 78)
    print("Отчёт по ступеням @VFLOG   (pp=%d, rated=%d Гц)" % (pp, rated))
    print("=" * 78)
    for st in stats:
        cmd_hz = st.target * pp / 60.0
        vmag_hz = 100.0 * cmd_hz / rated + 15.0
        print("\nступень target=%d rpm  (~%.1f Гц, ожидаемый vmag ~%.0f %%)"
              % (st.target, cmd_hz, vmag_hz))
        print("  строк всего: %d, в установившейся области: %d" % (st.n_all, st.n))
        if st.n < SETTLED_MIN_TICKS:
            print("  ВЕРДИКТ: НЕТ ДАННЫХ (мало строк в установившейся области; "
                  "уменьшить --settle-ms или продлить ступень)")
            bad += 1
            continue
        notes = []
        for key, label, unit in (("fe", "f_e", "Гц"), ("fslip", "fslip", "Гц"),
                                 ("vmag", "vmag", "%"), ("meas", "rpm(энкодер)", "rpm"),
                                 ("fe", None, None)):
            if label is None:
                continue
            r = st.rng(key)
            if r:
                print("  %-12s %8d … %8d  %s" % (label, r[0], r[1], unit))
        for key, label in (("i1", "i1 (raw ADC)"), ("i2", "i2 (raw ADC)"),
                           ("ires", "ires (raw ADC)"), ("vbus", "vbus (raw ADC)"),
                           ("du", "du, %"), ("dv", "dv, %"), ("dw", "dw, %"),
                           ("swing", "swing, град")):
            r = st.rng(key)
            if r:
                print("  %-12s %8d … %8d" % (label, r[0], r[1]))
        fl = st.rng("fslip")
        vm = st.rng("vmag")
        c0 = 100.0 * st.commit0 / st.n
        print("  commit=0     %.1f %% установившихся тиков (%d из %d)"
              % (c0, st.commit0, st.n))
        if st.drp_first is not None and st.drp_last is not None:
            print("  drp (потеряно пакетов)  %d -> %d" % (st.drp_first, st.drp_last))
        # ── вердикты ────────────────────────────────────────────────────────
        if st.fault_events:
            notes.append("FAULT: fault!=0 в %d строках" % st.fault_events)
        if st.sd_low:
            notes.append("SD/EM_STOP активен (sd1/sd2=0) в %d строках" % st.sd_low)
        if fl and max(abs(fl[0]), abs(fl[1])) >= SLIP_CLAMP_HZ:
            notes.append("fslip упёрся в кламп +-%d Гц" % SLIP_CLAMP_HZ)
        if vm and vm[1] > VMAG_APERTURE_CEILING_PCT:
            notes.append("vmag %d %% выше потолка апертуры ~%d %% "
                         "(селектор отвергает, CCR удержан)"
                         % (vm[1], VMAG_APERTURE_CEILING_PCT))
        if c0 > COMMIT0_BAD_PCT:
            notes.append("непрерывный commit=0 (%.1f %% тиков) — структурный отказ "
                         "геометрии, не переходный" % c0)
        if notes:
            bad += 1
            print("  ВЕРДИКТ: ЗАМЕЧАНИЯ")
            for note in notes:
                print("    - " + note)
        else:
            print("  ВЕРДИКТ: OK")
    print("\n" + "=" * 78)
    print("Итог: %d ступень(ей) с замечаниями из %d" % (bad, len(stats)))
    return 1 if bad else 0


def main(argv):
    force_safe_stdout()
    ap = argparse.ArgumentParser(description="Отчёт по ступеням V/f из лога @VFLOG")
    ap.add_argument("log", help="файл лога UART (или - для stdin)")
    ap.add_argument("--pp", type=int, default=3, help="пары полюсов (шильдик: 3)")
    ap.add_argument("--rated", type=int, default=50, help="rated_freq_hz (по умолчанию 50)")
    ap.add_argument("--settle-ms", type=int, default=10000,
                    help="отбросить первые N мс каждой ступени (рампа tau=2000 мс)")
    args = ap.parse_args(argv)

    stream = sys.stdin if args.log == "-" else open(args.log, "r", encoding="utf-8",
                                                   errors="replace")
    stats: list[Stats] = []
    cur = None
    with stream:
        for line in stream:
            rec = parse_vflog(line)
            if rec is None:
                continue
            if cur is None or rec["target"] != cur.target:
                cur = Stats(rec["target"], rec["t"])
                stats.append(cur)
            dt = rec["t"] - cur.t0
            if dt < 0:                      # sys_tick_ms переполнился (32 бита)
                dt += 1 << 32
            cur.add(rec, dt >= args.settle_ms)

    if not stats:
        print("нет строк @VFLOG — данных для отчёта нет", file=sys.stderr)
        return 2
    return report(stats, args.pp, args.rated)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
