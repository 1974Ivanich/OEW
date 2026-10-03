#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Шкала метрики dJEOS: f_cnt / f_pwm / f_UEV / f_JEOS / N_expected.

Входные значения берутся с платы (`sysinfo`, `dump`, `p?`), не по памяти:

  CLK   поле `CLK=` в sysinfo = SystemCoreClock (тактовая таймера TIM1).
        main.c: `cli_pwm_sysinfo()` -> `out[0]=SystemCoreClock`.
  PSC   поле `PSC=` в sysinfo = TIM1->PSC; реальный делитель = PSC+1.
  TCLK  поле `TCLK=` в sysinfo = CLK/(PSC+1) (src/pwm.c `PWM_GetSysInfo`).
        TCLK — частота СЧЁТЧИКА. Подать TCLK вместо CLK = разделить на (PSC+1)
        второй раз (дефект пакета v1: 294 Гц вместо 5000 Гц на этой плате).
        `--tclk` здесь — только сверка `--clk/(PSC+1)`, а не вход шкалы.
  ARR   TIM1->ARR (из `dump` / `p?`).
  CMS   TIM1->CR1[6:5] (из `dump` / `p?`, поле `CR1=`): 00 edge; 01/10/11 center.
  MMS   TIM1->CR2[6:4] (из `dump`); ожидается 010 (UEV -> TRGO).
  URS   TIM1->CR1[8] (из `dump`); при 1 `UG` не порождает update/TRGO.
  RCR   TIM1->RCR. В `dump` НЕ печатается (`PWM_DumpRegs` читает только
        PSC/ARR/BDTR/CR1/CR2/CCER), поэтому RCR якорится на исходник образа:
        src/pwm.c `TIM1->RCR = 1u`. При смене образа — перечитать исходник.

Формулы (RM0440, центр-выровненный TIM1 + RCR):
  f_cnt  = CLK / (PSC+1)
  f_pwm  = f_cnt / (ARR+1)             CMS=00 (edge): 1 update-событие/период
           f_cnt / (2*(ARR+1))         CMS=01/10/11 (center)
  f_UEV  = f_pwm                       CMS=00
           2*f_pwm                     CMS=01/10/11 (вверх + вниз)
  f_JEOS = f_TRGO = f_UEV / (RCR+1)    MMS=010
  N_expected = f_JEOS * окно

Контрольные числа для замороженного образа ab1274d (обязательная сверка):
  --clk 170000000 --psc 16 --arr 999 --cms 3 --rcr 1 --window 2.0
    -> f_cnt=10000000  f_pwm=5000  f_JEOS=5000  N_expected=10000
  v1 с `--sysclk <TCLK>` давал 294 Гц / 1176 — ошибка шкалы в 17 раз.

Коды возврата: 0 — OK, 1 — расхождение (сверка/охранник), 2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import sys


def compute(clk, psc, arr, cms, rcr=1):
    """Шкала метрики, все частоты в Гц. clk — тактовая таймера (не TCLK)."""
    if clk <= 0:
        raise ValueError('CLK must be > 0')
    if psc < 0 or arr < 0 or rcr < 0:
        raise ValueError('PSC/ARR/RCR must be >= 0')
    if cms not in (0, 1, 2, 3):
        raise ValueError('CMS must be 0..3')
    f_cnt = float(clk) / (psc + 1.0)
    center = cms != 0
    f_pwm = f_cnt / ((2.0 if center else 1.0) * (arr + 1.0))
    f_uev = f_pwm * (2.0 if center else 1.0)
    f_jeos = f_uev / (rcr + 1.0)
    return dict(f_cnt=f_cnt, f_pwm=f_pwm, f_uev=f_uev, f_jeos=f_jeos, center=center)


def formula(cms, rcr=1):
    pwm = 'f_cnt/(2*(ARR+1))' if cms != 0 else 'f_cnt/(ARR+1)'
    uev = '2*f_pwm' if cms != 0 else 'f_pwm'
    return 'f_cnt=CLK/(PSC+1); f_pwm=%s; f_UEV=%s; f_JEOS=f_UEV/(RCR+1); RCR=%d' % (
        pwm, uev, rcr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Scale of the dJEOS metric (f_pwm / f_JEOS / N_expected).')
    ap.add_argument('--clk', type=float, required=True,
                    help='CLK from sysinfo (SystemCoreClock, TIM1 kernel clock); NOT TCLK')
    ap.add_argument('--psc', type=int, required=True, help='PSC from sysinfo')
    ap.add_argument('--arr', type=int, required=True, help='ARR from dump / p?')
    ap.add_argument('--cms', type=int, required=True,
                    help='TIM1->CR1[6:5] decoded from dump / p? CR1=')
    ap.add_argument('--rcr', type=int, default=1,
                    help='TIM1->RCR (source-anchored, dump does not print it); ab1274d: 1')
    ap.add_argument('--tclk', type=float, default=None,
                    help='TCLK from sysinfo: cross-check of CLK/(PSC+1) only (NOT an input)')
    ap.add_argument('--mms', type=int, default=2, help='TIM1->CR2[6:4], expected 2 (010)')
    ap.add_argument('--urs', type=int, default=-1, help='TIM1->CR1[8]')
    ap.add_argument('--window', type=float, default=2.0, help='window, seconds')
    ap.add_argument('--pwm-expected', type=float, default=5000.0,
                    help='FOC_PWM_FREQ guard, Hz; 0 disables the guard')
    a = ap.parse_args(argv)

    try:
        m = compute(a.clk, a.psc, a.arr, a.cms, a.rcr)
    except ValueError as exc:
        print('INPUT ERROR: %s' % exc)
        return 2
    if a.window <= 0:
        print('INPUT ERROR: --window must be > 0')
        return 2

    bad = 0
    print('CLK        = %.0f Hz (sysinfo CLK = SystemCoreClock; NOT TCLK)' % a.clk)
    print('PSC        = %d  -> f_cnt = %.3f Hz' % (a.psc, m['f_cnt']))
    if a.tclk is None:
        print('TCLK       = not given (cross-check skipped; pass --tclk from sysinfo)')
    else:
        ok = abs(a.tclk - m['f_cnt']) <= 0.005 * m['f_cnt']
        print('TCLK       = %.0f Hz (sysinfo) -- CLK/(PSC+1) cross-check: %s'
              % (a.tclk, 'OK' if ok else 'MISMATCH'))
        if not ok:
            bad = 1
            print('             MISMATCH: %g != %g. CLK/TCLK confounded: pass CLK as --clk,'
                  % (a.tclk, m['f_cnt']))
            print('             never TCLK: that is the v1 scale defect (factor PSC+1).')
    print('ARR        = %d' % a.arr)
    print('CMS        = %d (%s)' % (a.cms, 'centre-aligned, 2 update events/period'
                                   if m['center'] else 'edge-aligned, 1 update event/period'))
    print('RCR        = %d (source-anchored: src/pwm.c TIM1->RCR; dump does not print it)'
          % a.rcr)
    print('MMS        = %d (%s)' % (a.mms, '010: UEV -> TRGO, OK' if a.mms == 2
                                   else 'NOT 010: update->TRGO link unproven'))
    if a.urs == 1:
        print('URS        = 1: UG does not raise update/TRGO (the scan never sends UG)')
    print('f_pwm      = %.3f Hz' % m['f_pwm'])
    print('f_UEV      = %.3f Hz' % m['f_uev'])
    print('f_JEOS     = %.3f Hz  (= f_TRGO)' % m['f_jeos'])
    print('N_expected = %.0f  over window %.2f s' % (m['f_jeos'] * a.window, a.window))
    print('formula    : %s' % formula(a.cms, a.rcr))

    if a.pwm_expected > 0:
        rel = abs(m['f_pwm'] - a.pwm_expected) / a.pwm_expected
        ok = rel <= 0.02
        print('guard      : f_pwm %s %g Hz (FOC_PWM_FREQ)%s'
              % ('==' if ok else '!=', a.pwm_expected, '' if ok else '  MISMATCH'))
        if not ok:
            bad = 1
            print('             decoded f_pwm is %.1f%% off the firmware constant: the decode'
                  % (rel * 100.0))
            print('             is wrong (usual causes: TCLK instead of CLK; RCR=0 vs 1;')
            print('             CMS read from CR1 without shifting bits 6:5).')
    if a.mms != 2:
        print('WARNING    : MMS != 010 -> f_JEOS is not proven to follow update events')
    if bad:
        print('RESULT     : FAIL')
        return 1
    print('RESULT     : PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
