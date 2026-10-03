#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B по знаку (сессия 3, ПК-3): сравнение знака meas/eangle в двух конфигурациях разводки.

    py -3 sign_ab_report.py <лог_A.log> <лог_B.log> [--label1 ".."] [--label2 ".."] [--gate]

Порядок аргументов = порядок в протоколе: сначала текущая разводка, затем после перестановки.
Печатает по каждой конфигурации знак `meas` и знак развёрнутого `eangle` и вердикт A/B:

  * знаки РАЗНЫЕ  -> знак следует за порядком фаз: править mapping/разводку (энкодер ни при чём);
  * знак ОДИН     -> перестановка фаз знак не меняет: вопрос к конвенции энкодера.

Осциллограмма (5 В/дел, нули каналов совмещены) — независимый арбитр направления поля;
этот скрипт её не заменяет и по логу направление поля не восстанавливает.

Коды возврата: 0 = знаки разные, 3 = знак одинаковый, 4 = знак не определён (мало данных),
1 = данные непригодны (нет @VFLOG), 2 = ошибка входа.
"""
import argparse
import json
import math
import re
import sys

EANGLE_PER_REV = 16384.0        # eangle: 1/16384 оборота механического
MEAS_DEADBAND = 5               # rpm: меньше — движения нет
EANGLE_DEADBAND = 100           # отсчётов: меньше — движения нет


def force_safe_stdout():
    """Не дать консоли (cp1251 / ascii / 866) уронить разбор: незаменяемый символ -> '?'."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def frames_of(path):
    s = open(path, encoding='utf-8', errors='replace').read()
    return [dict(kv.split('=', 1) for kv in m.split(':') if '=' in kv)
            for m in re.findall(r'@VFLOG:([^\r\n]*)', s)]


def ival(f, key):
    v = f.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def unwrap(ea):
    total, prev = 0, ea[0]
    for x in ea[1:]:
        d = x - prev
        if d > EANGLE_PER_REV / 2:
            d -= int(EANGLE_PER_REV)
        elif d < -EANGLE_PER_REV / 2:
            d += int(EANGLE_PER_REV)
        total += d
        prev = x
    return total


def sign_of(delta, deadband):
    if delta is None or abs(delta) < deadband:
        return 'не определён'
    return 'плюс' if delta > 0 else 'минус'


def analyse(path, label):
    fr = frames_of(path)
    out = {'file': path, 'label': label, 'frames': len(fr), 'commit0': 0,
           'fault_values': [], 't_span_s': None}
    if not fr:
        return out
    ts = [ival(f, 't') for f in fr]
    ts = [t for t in ts if t is not None]
    if ts:
        out['t_span_s'] = (max(ts) - min(ts)) / 1000.0
    out['commit0'] = sum(1 for f in fr if ival(f, 'commit') == 0)
    out['fault_values'] = sorted({ival(f, 'fault') for f in fr if ival(f, 'fault') is not None})
    meas = [(ival(f, 't'), ival(f, 'meas')) for f in fr]
    meas = [(t, m) for t, m in meas if m is not None]
    out['meas_first'] = meas[0][1] if meas else None
    out['meas_last'] = meas[-1][1] if meas else None
    out['meas_delta'] = (out['meas_last'] - out['meas_first']) if meas else None
    out['meas_sign'] = sign_of(out['meas_delta'], MEAS_DEADBAND)
    ea = [ival(f, 'eangle') for f in fr]
    ea = [v for v in ea if v is not None]
    out['eangle_delta'] = unwrap(ea) if len(ea) > 1 else None
    out['eangle_revs'] = (out['eangle_delta'] / EANGLE_PER_REV) if out['eangle_delta'] is not None else None
    out['eangle_sign'] = sign_of(out['eangle_delta'], EANGLE_DEADBAND)
    out['sign_consistent'] = (out['meas_sign'] == out['eangle_sign'] and
                              out['meas_sign'] != 'не определён')
    if out['meas_sign'] == 'не определён' or out['eangle_sign'] == 'не определён':
        out['sign'] = 'не определён'
    elif out['sign_consistent']:
        out['sign'] = out['meas_sign']
    else:
        out['sign'] = 'противоречие (meas %s, eangle %s)' % (out['meas_sign'], out['eangle_sign'])
    return out


def verdict(a, b):
    if a['sign'] == 'не определён' or b['sign'] == 'не определён':
        return 'НЕ ОПРЕДЕЛЁН: движения нет или данных мало — A/B недействителен', 4
    if a['sign'] != b['sign']:
        return ('знаки РАЗНЫЕ (%s -> %s): знак следует за порядком фаз; правится mapping/разводка, '
                'а не конвенция энкодера' % (a['sign'], b['sign']), 0)
    return ('знак ОДИН и тот же (%s) при перестановке фаз: разводка фазами это не объясняет, '
            'вопрос к конвенции энкодера' % a['sign'], 3)


def report(res, verdict_pair, gate):
    for r in res:
        print('  [%s] %s' % (r['label'], r['file']))
        if not r['frames']:
            print('     нет @VFLOG — файл непригоден')
            continue
        print('     кадров %d, интервал %s с, commit=0 %d, fault=%s'
              % (r['frames'],
                 ('%.1f' % r['t_span_s']) if r['t_span_s'] is not None else 'н/д',
                 r['commit0'], r['fault_values']))
        print('     meas: %s -> %s (дельта %s rpm), знак: %s'
              % (r['meas_first'], r['meas_last'], r['meas_delta'], r['meas_sign']))
        print('     eangle: дельта %s отсч. (%s об.), знак: %s'
              % (r['eangle_delta'],
                 ('%.3f' % r['eangle_revs']) if r['eangle_revs'] is not None else 'н/д',
                 r['eangle_sign']))
        print('     знак конфигурации: %s%s' % (r['sign'],
              '' if r['sign_consistent'] else '  <- meas и eangle расходятся'))
    print('  ВЕРДИКТ A/B: %s' % verdict_pair[0])
    print('  НАПОМИНАНИЕ: направление поля этим разбором НЕ доказывается — нужен кадр с осциллографа '
          '(CH1-CH2 на 5 В/дел, нули каналов совмещены).')
    return verdict_pair[1] if gate else 0


def selftest():
    import os
    import tempfile

    def frame(t, meas, eangle):
        return ('@VFLOG:t=%d:target=50:meas=%d:fe=2:fslip=0:vmag=19:theta=1:du=51:dv=55:dw=41:'
                'i1=2050:i2=2050:ires=0:vbus=600:eangle=%d:espeed=%d:eerr=0:fault=0:drp=0:'
                'sd1=1:sd2=1:swing=0:commit=1\n' % (t, meas, eangle, meas))

    fails = []
    with tempfile.TemporaryDirectory() as td:
        def write(name, rows):
            p = os.path.join(td, name)
            open(p, 'w', encoding='utf-8').write(''.join(rows))
            return p

        up = [frame(40 * i, -1 + 3 * i, 8835 + 600 * i) for i in range(8)]
        down = [frame(40 * i, 18 - 3 * i, 14681 - 600 * i) for i in range(8)]
        flat = [frame(40 * i, 0, 8835) for i in range(8)]
        p_up1, p_down, p_up2, p_flat = (write('up1.log', up), write('down.log', down),
                                       write('up2.log', up), write('flat.log', flat))

        a, b = analyse(p_up1, 'A'), analyse(p_down, 'B')
        v, code = verdict(a, b)
        if code != 0 or 'РАЗНЫЕ' not in v:
            fails.append('разные знаки: код %d, вердикт «%s»' % (code, v))
        if a['sign'] != 'плюс' or b['sign'] != 'минус':
            fails.append('знаки конфигураций: %s / %s' % (a['sign'], b['sign']))
        a2, b2 = analyse(p_up1, 'A'), analyse(p_up2, 'B')
        v2, code2 = verdict(a2, b2)
        if code2 != 3 or 'ОДИН' not in v2:
            fails.append('одинаковые знаки: код %d, вердикт «%s»' % (code2, v2))
        a3, b3 = analyse(p_flat, 'A'), analyse(p_up2, 'B')
        v3, code3 = verdict(a3, b3)
        if code3 != 4 or 'НЕ ОПРЕДЕЛЁН' not in v3:
            fails.append('стоящий вал: код %d, вердикт «%s»' % (code3, v3))
        # разрыв через границу 14-битного счётчика (обёртка) не должен менять знак
        wrap = [frame(40 * 0, -1, 16200), frame(40 * 1, 5, 16350), frame(40 * 2, 11, 120)]
        p_wrap = write('wrap.log', wrap)
        w = analyse(p_wrap, 'wrap')
        if w['eangle_sign'] != 'плюс':
            fails.append('обёртка счётчика: знак %s, ожидался плюс' % w['eangle_sign'])
    if fails:
        print('  САМОТЕСТ ПРОВАЛЕН (%d):' % len(fails))
        for f in fails:
            print('   ! %s' % f)
        return 1
    print('  самотест: 4 проверки, 0 провалов')
    return 0


def main(argv=None):
    force_safe_stdout()
    ap = argparse.ArgumentParser(description='A/B по знаку meas/eangle по @VFLOG')
    ap.add_argument('logs', nargs='*', help='лог конфигурации A и B (в порядке протокола)')
    ap.add_argument('--label1', default='конфигурация A (до перестановки)')
    ap.add_argument('--label2', default='конфигурация B (после перестановки)')
    ap.add_argument('--gate', action='store_true', help='код возврата по вердикту (0/3/4)')
    ap.add_argument('--json', dest='json_out')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if len(a.logs) != 2:
        ap.error('нужно ровно два лога: A и B')
    res = [analyse(a.logs[0], a.label1), analyse(a.logs[1], a.label2)]
    if not res[0]['frames'] or not res[1]['frames']:
        print('  данные непригодны: в одном из файлов нет @VFLOG', file=sys.stderr)
        return 1
    v, code = verdict(res[0], res[1])
    code = report(res, (v, code), gate=a.gate)
    if a.json_out:
        json.dump(res + [{'verdict': v}], open(a.json_out, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    return code


if __name__ == '__main__':
    sys.exit(main())
