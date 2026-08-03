"""
Автоматизированный прогон ТЗ "Тестирование Auto-Tune v2" (tz_test_autotune.md).

Выполняет секции, доступные через UART без ручных действий:
  1 — предварительные проверки (c, a, sysinfo)               [ТЗ 1.3]
  3 — автоопределение канала: 5 запусков ch, повторяемость   [ТЗ 3.1-3.3]
  4 — multi-point Rs (iv): линейность I(U), Rs               [ТЗ 4.1]
  5 — idle: кривая Ls(I) монотонна, spread < 15%             [ТЗ 5.1-5.2]
      + сверка Rs(idle) vs Rs(iv) в пределах 10%             [ТЗ 4.3]
      + Isat > 0                                             [ТЗ 7.1]
  6 — pairs: ASYM < 10%, сверка с idle в пределах 15%        [ТЗ 6.1-6.3]

Секции 2 (обрыв/КЗ/Vbus), 4.2 (мультиметр), 7.2 (осциллограф), 8 (GUI) —
ручные, скрипт их не выполняет.

Использование:
    python test_autotune.py --port COM15 [--baud 115200] [--sections 1,3,4,5,6]

Критерии PASS/FAIL — раздел 9 ТЗ. Код возврата: 0 = все PASS, 1 = есть FAIL.
"""

import argparse
import re
import sys
import time
from datetime import datetime

import serial

from at_errors import at_error_cause

# ─── Regex телеметрии (форматы из src/autotune.c и main.c) ───────────────
RE_ADC_CAL   = re.compile(r"@ADC:CAL:offset_i1=(\d+):offset_i2=(\d+):offset_ires=(\d+)")
RE_ADC_READ  = re.compile(r"@ADC:I1=(\d+):I2=(\d+):Ires=(\d+):VBUS=(\d+)")
RE_SYS_CLK   = re.compile(r"@SYS:CLK=(\d+)")
RE_CH_OK     = re.compile(r"@AT:CH_DETECT:OK:CH=(\d+):I=(-?\d+):SIGN=(-?\d+)")
RE_IV_POINT  = re.compile(r"@AT:RS_IV:POINT:D=(\d+):U=(-?\d+):I=(-?\d+)")
RE_IV_OK     = re.compile(r"@AT:RS_IV:OK:Rs=(-?\d+)")
RE_STAT      = re.compile(r"@AT:STAT:Rs=(-?\d+):(-?\d+):(-?\d+):(-?\d+)%"
                          r":Ls=(-?\d+):(-?\d+):(-?\d+):(-?\d+)%"
                          r":Isat=(-?\d+):(-?\d+):(-?\d+):(-?\d+)%")
RE_PARAMS    = re.compile(r"@PARAMS:")
RE_KV        = re.compile(r"(\w+)=(-?\d+)")
RE_CURVE     = re.compile(r"I=(-?\d+),L=(-?\d+)")
RE_PAIR      = re.compile(r"@AT:PAIR:(AB|BC|CA):Rs=(-?\d+):Ls=(-?\d+):Isat=(-?\d+):V=(\d+)")
RE_PAIRS_OK  = re.compile(r"@AT:PAIRS:OK:Rs=(-?\d+):Ls=(-?\d+):ASYM=(-?\d+)%")

CH_NAMES = {0: "?", 1: "I1", 2: "I2", 3: "Ires"}


class Board:
    """UART-обёртка: отправка команды и сбор строк до финального маркера."""

    def __init__(self, port, baud, logfile):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.logfile = logfile

    def close(self):
        self.ser.close()

    def _log(self, direction, text):
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.logfile.write(f"{stamp} {direction} {text}\n")

    def flush_input(self, quiet_s=0.3):
        """Дочитать всё, что плата шлёт сейчас (телеметрия @FOC и т.п.)."""
        deadline = time.time() + quiet_s
        while time.time() < deadline:
            if self.ser.in_waiting:
                self.ser.read(self.ser.in_waiting)
                deadline = time.time() + quiet_s
            else:
                time.sleep(0.02)

    def run(self, cmd, done_markers, fail_markers=(), timeout=15.0):
        """Отправить cmd, собрать строки до done/fail-маркера или таймаута.

        Возвращает (lines, status): status = 'done' | 'fail' | 'timeout'.
        """
        self.flush_input()
        self._log(">>", cmd)
        self.ser.write((cmd + "\n").encode())
        lines, buf = [], b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode(errors="replace").strip().lstrip("> ").strip()
                    if not line:
                        continue
                    self._log("<<", line)
                    lines.append(line)
                    if any(line.startswith(m) for m in fail_markers):
                        self._log("!!", f"FAIL for '{cmd}': {line}")
                        self._log("!!", at_error_cause(line))
                        return lines, "fail"
                    if any(line.startswith(m) for m in done_markers):
                        return lines, "done"
        self._log("!!", f"timeout {timeout}s for '{cmd}'")
        # При таймауте пытаемся извлечь причину из последней AT-ошибки в буфере
        for l in reversed(lines):
            if "@" in l and ("ERROR" in l or "FAIL" in l or "ABORT" in l):
                self._log("!!", at_error_cause(l))
                break
        return lines, "timeout"


class Report:
    def __init__(self, logfile=None):
        self.items = []          # (section, name, verdict, detail)
        self._logfile = logfile  # опциональный файловый хэндл (auto-log)

    def _w(self, s):
        """Печать в консоль + дублирование в лог-файл (если задан)."""
        print(s)
        if self._logfile:
            try:
                stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self._logfile.write(f"{stamp} [rep] {s}\n")
                self._logfile.flush()
            except Exception:
                pass

    def add(self, section, name, verdict, detail=""):
        self.items.append((section, name, verdict, detail))
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}[verdict]
        self._w(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
        # Причина — только если деталь содержит код ошибки платы (@...:ERROR/FAIL/ABORT),
        # иначе она уже зафиксирована в Board.run (!! строки)
        if verdict == "FAIL" and ("@" in detail or ":ABORTED" in detail or "TIMEOUT" in detail):
            self._w(f"     {at_error_cause(detail)}")

    def summary(self):
        n_pass = sum(1 for *_x, v, _ in self.items if v == "PASS")
        n_fail = sum(1 for *_x, v, _ in self.items if v == "FAIL")
        n_warn = sum(1 for *_x, v, _ in self.items if v == "WARN")
        n_skip = sum(1 for *_x, v, _ in self.items if v == "SKIP")
        self._w("\n" + "=" * 64)
        self._w(f"ИТОГ: {n_pass} PASS, {n_fail} FAIL, {n_warn} WARN, {n_skip} SKIP")
        self._w("=" * 64)
        return n_fail == 0

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Auto-Tune test report — {datetime.now().isoformat()}\n\n")
            f.write("| Секция | Тест | Вердикт | Детали |\n|---|---|---|---|\n")
            for sec, name, verdict, detail in self.items:
                f.write(f"| {sec} | {name} | {verdict} | {detail} |\n")


def parse_kv(line):
    return {k: int(v) for k, v in RE_KV.findall(line)}


def find_match(lines, regex):
    for line in lines:
        m = regex.search(line)
        if m:
            return m
    return None


# ─── Секция 1: предварительные проверки ──────────────────────────────────
def test_section1(board, rep):
    rep._w("\n── Секция 1: предварительные проверки (c, a, sysinfo)")
    lines, st = board.run("c", ["@ADC:CAL:"], timeout=5)
    m = find_match(lines, RE_ADC_CAL)
    if st == "done" and m:
        o1, o2, on = (int(m.group(i)) for i in (1, 2, 3))
        ok = all(100 < v < 4000 for v in (o1, o2, on))
        rep.add(1, "1.3 Калибровка ADC (c)", "PASS" if ok else "WARN",
                f"offsets i1={o1} i2={o2} in={on}")
    else:
        rep.add(1, "1.3 Калибровка ADC (c)", "FAIL", f"нет @ADC:CAL ({st})")

    lines, st = board.run("a", ["@ADC:I1="], timeout=5)
    m = find_match(lines, RE_ADC_READ)
    if st == "done" and m:
        rep.add(1, "1.3 Чтение ADC (a)", "PASS",
                f"I1={m.group(1)} I2={m.group(2)} IN={m.group(3)} VBUS={m.group(4)}")
    else:
        rep.add(1, "1.3 Чтение ADC (a)", "FAIL", f"нет @ADC: ({st})")

    lines, st = board.run("sysinfo", ["@SYS:CLK="], timeout=5)
    m = find_match(lines, RE_SYS_CLK)
    if st == "done" and m:
        clk = int(m.group(1))
        rep.add(1, "1.3 Тактирование 170 МГц", "PASS" if clk == 170_000_000 else "FAIL",
                f"CLK={clk}")
    else:
        rep.add(1, "1.3 Тактирование 170 МГц", "FAIL", f"нет @SYS:CLK ({st})")


# ─── Секция 3: автоопределение канала ────────────────────────────────────
def test_section3(board, rep, runs=5):
    rep._w(f"\n── Секция 3: детект канала тока (ch × {runs})")
    results = []
    for i in range(runs):
        lines, st = board.run("ch", ["@AT:CH:OK"], ["@AT:CH:FAIL"], timeout=15)
        m = find_match(lines, RE_CH_OK)
        if st == "done" and m:
            ch, cur, sign = int(m.group(1)), int(m.group(2)), int(m.group(3))
            results.append((ch, cur, sign))
            rep._w(f"    run {i+1}: CH={CH_NAMES.get(ch, ch)} I={cur} mA SIGN={sign}")
        else:
            results.append(None)
            rep._w(f"    run {i+1}: FAIL ({st})")
    good = [r for r in results if r]
    if not good:
        rep.add(3, "3.1 Детект канала", "FAIL", "все запуски неудачны (NO_CURRENT?)")
        return None
    channels = {r[0] for r in good}
    if len(good) == runs and len(channels) == 1:
        ch, cur, sign = good[0]
        rep.add(3, "3.3 Повторяемость 5/5", "PASS", f"CH={CH_NAMES.get(ch, ch)} во всех запусках")
        rep.add(3, "3.1 Ток теста > 50 мА", "PASS" if cur > 50 else "WARN", f"I={cur} mA")
        rep.add(3, "3.2 Полярность SIGN=1", "PASS" if sign == 1 else "WARN", f"SIGN={sign}")
        return ch
    rep.add(3, "3.3 Повторяемость 5/5", "FAIL",
            f"каналы {sorted(channels)}, успешно {len(good)}/{runs} — шум или плохой контакт")
    return None


# ─── Секция 4: multi-point Rs (iv) ───────────────────────────────────────
def test_section4(board, rep):
    rep._w("\n── Секция 4: multi-point Rs (iv)")
    lines, st = board.run("iv", ["@AT:IV:OK"], ["@AT:IV:FAIL"], timeout=30)
    points = [(int(m.group(2)), int(m.group(3)))
              for m in (RE_IV_POINT.search(l) for l in lines) if m]
    m_rs = find_match(lines, RE_IV_OK)
    if st != "done" or not m_rs:
        rep.add(4, "4.1 Измерение Rs (iv)", "FAIL", f"статус {st}, точек {len(points)}")
        return None
    rs_iv = int(m_rs.group(1))
    rep.add(4, "4.1 Измерение Rs (iv)", "PASS", f"Rs={rs_iv} mOhm, точек {len(points)}")

    # Линейность: наименьшие квадраты I = a*U + b, максимум отклонения точки
    if len(points) >= 3:
        n = len(points)
        su = sum(u for u, _ in points); si = sum(i for _, i in points)
        suu = sum(u * u for u, _ in points); sui = sum(u * i for u, i in points)
        den = n * suu - su * su
        if den != 0:
            a = (n * sui - su * si) / den
            b = (si - a * su) / n
            i_span = max(i for _, i in points) - min(i for _, i in points) or 1
            max_dev = max(abs(i - (a * u + b)) for u, i in points) / i_span * 100
            rep.add(4, "4.1 Линейность I(U)", "PASS" if max_dev < 10 else "FAIL",
                    f"max отклонение {max_dev:.1f}% от размаха тока")
        else:
            rep.add(4, "4.1 Линейность I(U)", "WARN", "нет разброса напряжений")
    else:
        rep.add(4, "4.1 Линейность I(U)", "WARN", f"мало точек ({len(points)})")
    return rs_iv


# ─── Секция 5 (+4.3, 7.1): idle ──────────────────────────────────────────
def test_section5(board, rep, rs_iv):
    rep._w("\n── Секция 5: idle (Rs/Ls/Isat, 5 повторов) — до 3 минут")
    lines, st = board.run("idle", ["@IDLE:OK"], ["@IDLE:FAIL", "@IDLE:ABORTED"], timeout=180)
    if st != "done":
        detail = next((l for l in lines if "ERROR" in l or "FAIL" in l), st)
        rep.add(5, "5.1 Запуск idle", "FAIL", str(detail))
        return None
    rep.add(5, "5.1 Запуск idle", "PASS")

    params = {}
    for l in lines:
        if RE_PARAMS.search(l):
            params = parse_kv(l)
    curve = []
    for l in lines:
        if l.startswith("@IDLE:CURVE:"):
            curve = [(int(i), int(L)) for i, L in RE_CURVE.findall(l)]

    # 5.1 монотонность Ls(I)
    if len(curve) >= 3:
        Ls = [p[1] for p in curve]
        inversions = sum(1 for k in range(len(Ls) - 1) if Ls[k + 1] > Ls[k])
        grows = Ls[-1] > Ls[0]
        if grows:
            rep.add(5, "5.1 Ls монотонно убывает", "FAIL",
                    f"Ls растёт с током ({Ls[0]} → {Ls[-1]}) — шум ADC / неверный канал")
        elif inversions > len(Ls) // 3:
            rep.add(5, "5.1 Ls монотонно убывает", "WARN",
                    f"{inversions} инверсий из {len(Ls)-1} — шумная кривая")
        else:
            rep.add(5, "5.1 Ls монотонно убывает", "PASS",
                    f"{len(curve)} точек, Ls {Ls[0]} → {Ls[-1]} uH")
    else:
        rep.add(5, "5.1 Кривая Ls(I)", "WARN", f"мало точек ({len(curve)})")

    # 5.2 spread по 5 повторам
    m = find_match(lines, RE_STAT)
    if not m:
        lines2, st2 = board.run("stats", ["@AT:STAT:"], timeout=5)
        m = find_match(lines2, RE_STAT)
    if m:
        g = [int(m.group(i)) for i in range(1, 13)]
        for name, off in (("Rs", 0), ("Ls", 4), ("Isat", 8)):
            med, mn, mx, sp = g[off:off + 4]
            verdict = "PASS" if sp < 15 else ("WARN" if sp < 30 else "FAIL")
            rep.add(5, f"5.2 Spread {name} < 15%", verdict,
                    f"median={med} min={mn} max={mx} spread={sp}%")
    else:
        rep.add(5, "5.2 Статистика (stats)", "FAIL", "нет @AT:STAT")

    # 4.3 сверка Rs idle vs iv
    if rs_iv and params.get("Rs", 0) > 0:
        diff = abs(params["Rs"] - rs_iv) / rs_iv * 100
        verdict = "PASS" if diff <= 10 else ("WARN" if diff <= 20 else "FAIL")
        rep.add(4, "4.3 Rs(idle) vs Rs(iv) ≤ 10%", verdict,
                f"idle={params['Rs']} iv={rs_iv} diff={diff:.1f}%")

    # 7.1 Isat
    isat = params.get("Isat", 0)
    max_ls = max((p[1] for p in curve), default=0)
    if isat > 0:
        verdict = "PASS" if isat >= 500 else "WARN"
        rep.add(7, "7.1 Isat > 0", verdict, f"Isat={isat} mA")
    elif max_ls > 0:
        rep.add(7, "7.1 Isat > 0", "FAIL",
                f"Isat=0 при max_Ls={max_ls} — кривая не достигла 70% падения, увеличить max duty")
    else:
        rep.add(7, "7.1 Isat > 0", "WARN", "Isat=0 и max_Ls=0")
    return params


# ─── Секция 6: pairs ─────────────────────────────────────────────────────
def test_section6(board, rep, idle_params):
    rep._w("\n── Секция 6: все пары фаз (pairs)")
    lines, st = board.run("pairs", ["@AT:PAIRS:RESULT_OK"],
                          ["@AT:PAIRS:RESULT_FAIL"], timeout=60)
    pairs = {}
    for l in lines:
        m = RE_PAIR.search(l)
        if m:
            pairs[m.group(1)] = (int(m.group(2)), int(m.group(3)), int(m.group(5)))
    m_ok = find_match(lines, RE_PAIRS_OK)
    if st != "done" or not m_ok:
        detail = next((l for l in lines if "ERROR" in l), st)
        rep.add(6, "6.1 Запуск pairs", "FAIL", str(detail))
        return
    rs_avg, ls_avg, asym = (int(m_ok.group(i)) for i in (1, 2, 3))
    valid = sum(1 for v in pairs.values() if v[2] == 1)
    rep.add(6, "6.1 Все пары измерены", "PASS" if valid == 3 else "FAIL",
            f"valid {valid}/3: " + " ".join(f"{k}:Rs={v[0]},Ls={v[1]}" for k, v in pairs.items()))
    verdict = "PASS" if asym < 10 else ("WARN" if asym <= 20 else "FAIL")
    rep.add(6, "6.2 Асимметрия < 10%", verdict, f"ASYM={asym}%")

    if idle_params:
        for name, val in (("Rs", rs_avg), ("Ls", ls_avg)):
            ref = idle_params.get(name, 0)
            if ref > 0:
                diff = abs(val - ref) / ref * 100
                rep.add(6, f"6.3 {name}(pairs) vs {name}(idle) ≤ 15%",
                        "PASS" if diff <= 15 else "FAIL",
                        f"pairs={val} idle={ref} diff={diff:.1f}%")


# ─── main ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Авто-прогон ТЗ Auto-Tune v2 (tz_test_autotune.md)")
    ap.add_argument("--port", required=True, help="COM-порт, напр. COM15")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--sections", default="1,3,4,5,6",
                    help="какие секции выполнять (по умолчанию 1,3,4,5,6)")
    args = ap.parse_args()
    sections = {int(s) for s in args.sections.split(",")}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"autotune_test_{stamp}.log"
    report_path = f"autotune_test_{stamp}.md"

    rep = Report()
    with open(log_path, "w", encoding="utf-8") as logfile:
        rep._logfile = logfile
        logfile.write(f"# Auto-Tune test — {datetime.now().isoformat()}\n")
        logfile.write(f"# Порт: {args.port} @ {args.baud}, секции: {sorted(sections)}\n")
        try:
            board = Board(args.port, args.baud, logfile)
        except serial.SerialException as e:
            print(f"Не удалось открыть {args.port}: {e}")
            return 2
        print(f"Подключено: {args.port} @ {args.baud}. Лог: {log_path}")
        logfile.write(f"Подключено: {args.port} @ {args.baud}\n")
        logfile.flush()
        try:
            board.run("f", [], timeout=2)  # clear any pending fault
            rs_iv, idle_params = None, None
            if 1 in sections:
                test_section1(board, rep)
            if 3 in sections:
                test_section3(board, rep)
            if 4 in sections:
                rs_iv = test_section4(board, rep)
            if 5 in sections:
                idle_params = test_section5(board, rep, rs_iv)
            if 6 in sections:
                test_section6(board, rep, idle_params)
        finally:
            board.close()

    ok = rep.summary()
    rep.save(report_path)
    print(f"Отчёт: {report_path}")
    print("Ручные секции (не автоматизируются): 2 (обрыв/КЗ/Vbus), "
          "4.2 (мультиметр), 7.2 (осциллограф), 8 (GUI).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
