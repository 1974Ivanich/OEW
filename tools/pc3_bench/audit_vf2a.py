#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Воспроизводимый разбор возврата VF-2A (ПК-3, 26.09.2026).

Использование:  py -3 audit_vf2a.py <папка возврата с *.log>
Печатает ровно те числа, что цитируются в FINDINGS_VF2A_PC3_20260926.md.
"""
import glob
import os
import re
import sys

EANGLE_PER_REV = 16384.0        # eangle: 1/16384 оборота мех.
THETA_PER_REV = 4294967296.0    # theta: 2^32 на оборот поля
LSB_MA = 3300.0 * 1e6 / (4095.0 * 63000.0)   # АЦП→мА, ADC_DC_SHUNT_UV_PER_A=63000


def rows(path):
    s = open(path, encoding='utf-8', errors='replace').read()
    return [dict(kv.split('=', 1) for kv in m.split(':') if '=' in kv)
            for m in re.findall(r'@VFLOG:([^\r\n]*)', s)]


def main(root):
    tot = {'run': 0, 'creep': 0, 'wd': 0, 'gate': 0}
    print('=== попытки (по файлам vf_vf2a*.log) ===')
    for fn in sorted(glob.glob(os.path.join(root, 'vf_vf2a*.log'))):
        r = rows(fn)
        if not r:
            tot['gate'] += 1
            continue
        ea = [int(x['eangle']) for x in r if 'eangle' in x]
        me = [int(x['meas']) for x in r]
        span = (ea[-1] - ea[0]) / EANGLE_PER_REV * 360.0
        kind = 'РАЗГОН' if abs(me[-1]) > 20 else ('полз' if abs(me[-1]) > 3 else 'стоп')
        tot[{'РАЗГОН': 'run', 'полз': 'creep', 'стоп': 'wd'}[kind]] += 1
        print('  %-26s n=%-4d eangle %+8.2f° мех.  meas %+4d→%+4d rpm  %s'
              % (os.path.basename(fn), len(r), span, me[0], me[-1], kind))
    print('  ИТОГ: возбуждено %d (разгон %d, полз %d, стоп-на-2000 %d), отсечено гейтом %d'
          % (tot['run'] + tot['creep'] + tot['wd'], tot['run'], tot['creep'], tot['wd'], tot['gate']))

    for fn in ('vf_vf2a_go_11.log', 'vf_vf2a_go_5.log'):
        p = os.path.join(root, fn)
        if not os.path.exists(p):
            continue
        s = open(p, encoding='utf-8', errors='replace').read()
        r = rows(p)
        t0, t1 = int(r[0]['t']), int(r[-1]['t'])
        dur = (t1 - t0) / 1000.0
        th = [int(x['theta']) for x in r if 'theta' in x]
        wraps, prev = 0, th[0]
        for x in th[1:]:
            if x < prev:
                wraps += 1
            prev = x
        rev = wraps + (th[-1] - th[0]) / THETA_PER_REV
        i2 = [int(x['i2']) for x in r if 'i2' in x]
        raw = [int(x['vbus']) for x in r if 'vbus' in x]
        bad = [v for v in raw if v == 0 or v > 900]
        print('\n=== %s: окно %.2f с ===' % (fn, dur))
        print('  theta: %.2f оборота поля → %.2f Гц в среднем (телеметрия fe=%s Гц в конце)'
              % (rev, rev / dur, r[-1].get('fe')))
        print('  meas: %s → %s rpm; fslip %s → %s; vmag %s → %s %%'
              % (r[0].get('meas'), r[-1].get('meas'), r[0].get('fslip'),
                 r[-1].get('fslip'), r[0].get('vmag'), r[-1].get('vmag')))
        print('  i2 (сырые): %d…%d LSB → размах %.2f A (1 LSB = %.2f мА)'
              % (min(i2), max(i2), (max(i2) - min(i2)) * LSB_MA / 1000.0, LSB_MA))
        print('  vbus (сырые отсчёты): %d…%d; загрязнённых (0 или >900): %d из %d'
              % (min(raw), max(raw), len(bad), len(raw)))
        foc = [(int(a), int(b)) for a, b in re.findall(r'@FOC:t=(\d+):[^\r\n]*?VBUS=(\d+)', s)]
        inw = [b for a, b in foc if t0 - 500 <= a <= t1 + 500]
        good = [v for v in inw if v > 40000]
        print('  @FOC в окне: %d строк, нулевых %d, достоверных %d, медиана %s мВ'
              % (len(inw), inw.count(0), len(good), sorted(good)[len(good) // 2] if good else '—'))
        print('  FAULT_R=18 в файле: %d строк' % s.count('FAULT_R=18'))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
