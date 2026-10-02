#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Самотест vflog_step_report.py на синтетическом логе с ИЗВЕСТНЫМИ значениями.
Проверяет: нарезку по ступеням, отбрасывание рампы по --settle-ms, min/max,
подсчёт commit=0/fault/SD и все три типа вердикта (OK / замечание / нет данных).
"""
import subprocess, sys, tempfile, os

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vflog_step_report.py")

def line(t, target, meas, fe, fslip, vmag, du, dv, dw, i1, i2, fault=0,
         commit=1, sd1=1, sd2=1, drp=0, swing=0):
    return ("@VFLOG:t=%d:target=%d:meas=%d:fe=%d:fslip=%d:vmag=%d:theta=123456:"
            "du=%d:dv=%d:dw=%d:i1=%d:i2=%d:ires=0:vbus=32000:"
            "eangle=100:espeed=%d:eerr=0:fault=%d:drp=%d:sd1=%d:sd2=%d:"
            "swing=%d:commit=%d\r\n") % (t, target, meas, fe, fslip, vmag, du, dv, dw,
                                         i1, i2, meas, fault, drp, sd1, sd2, swing, commit)

def build():
    out = []
    # ступень 1: target=40, рампа 10 с (в неё кладём мусорные значения), затем устой
    t = 0
    for i in range(250):                      # 10 с рампы — должно быть отброшено
        out.append(line(t, 40, i, 0, 0, 15, 50, 50, 50, 100, 100))
        t += 40
    for i in range(1000):                     # устой: fslip 1..2, vmag 19..20
        out.append(line(t, 40, 40, 2, 1 + (i % 2), 19 + (i % 2), 40, 60, 50,
                        700 + i % 7, 690, drp=i // 10))
        t += 40
    # ступень 2: target=100, fslip в клампе -5 -> замечание
    t2 = t + 100
    for i in range(250):
        out.append(line(t2, 100, i, 0, 0, 15, 50, 50, 50, 100, 100))
        t2 += 40
    for i in range(1000):
        out.append(line(t2, 100, 80, -3, -5, 19, 41, 59, 50, 800, 795))
        t2 += 40
    # ступень 3: target=200, всего 20 строк устоя -> "нет данных"
    t3 = t2 + 100
    for i in range(270):
        out.append(line(t3, 200, 200, 10, 0, 35, 33, 67, 50, 900, 880,
                        commit=0 if i % 100 == 0 else 1))
        t3 += 40
    return "".join(out)

def main():
    log = build()
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(log)
        path = f.name
    # Кодировка задаётся ЯВНО для дочернего процесса и для чтения его вывода: иначе
    # сверка строк зависит от локали платформы (Windows cp1251 против Linux UTF-8)
    # и самотест проходит локально, но падает в CI.
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run([sys.executable, TOOL, path, "--pp", "3", "--settle-ms", "10000"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env)
    out = res.stdout
    # сравнение по нормализованным строкам (пробелы-выравнивание не проверяем)
    norm = [" ".join(l.split()) for l in out.splitlines()]
    checks = [
        ("rc=1 (есть замечания)", res.returncode == 1),
        ("нарезка на 3 ступени", out.count("ступень target=") == 3),
        ("ступень 40 rpm -> OK", "ВЕРДИКТ: OK" in norm),
        ("fslip 40 rpm: 1..2", "fslip 1 … 2 Гц" in norm),
        ("vmag 40 rpm: 19..20", "vmag 19 … 20 %" in norm),
        ("commit=0 40 rpm = 0.0 %", "commit=0 0.0 % установившихся тиков (0 из 1000)" in norm),
        ("ступень 100 rpm -> кламп", "fslip упёрся в кламп +-5 Гц" in out),
        ("ступень 200 rpm -> нет данных", "ВЕРДИКТ: НЕТ ДАННЫХ" in out),
        ("drp показан", "drp (потеряно пакетов)" in out),
        ("итог: 2 из 3", "Итог: 2 ступень(ей) с замечаниями из 3" in out),
    ]
    print(out)
    print("-" * 60)
    bad = 0
    for name, ok in checks:
        print(("  OK   " if ok else "  FAIL ") + name)
        bad += 0 if ok else 1
    os.unlink(path)
    print("-" * 60)
    print("самотест: %d проверок, %d провалов" % (len(checks), bad))
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
