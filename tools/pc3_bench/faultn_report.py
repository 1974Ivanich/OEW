#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разбор характеризации FAULT_N (сессия 4, ПК-3) на диагностическом образе monitor-only.

    py -3 faultn_report.py <файл.log | папка> [--gate] [--json out.json]

Считает по @VFLOG (25 Гц = 40 мс на кадр):
  * события SD1/SD2: кадры, где sd1=0 или sd2=0 — время t (мс от старта МК), индекс кадра,
    сколько кадров линия держалась, вернулась ли в 1, что в это время в fault/FAULT_R/commit;
  * офсетно-независимую амплитуду сырых токов i1/i2/ires в окне события (размах max-min)
    в отсчётах и в амперах по номиналу прошивки 63 мВ/А (без калибровки) + базу всего файла;
  * невалидные кадры: кадр, у которого i1=i2=ires=vbus=0 ОДНОВРЕМЕННО (кадр не получен),
    для выводов о токах и шине не используется — только считается;
  * классификацию (печатает, ничего не «лечит»):
        событие + линия вернулась  + токи в норме  -> короткий импульс: вход/наводка/дребезг
        событие + линия НЕ вернулась               -> латчируемый источник (модуль/UVLO)
        событие + амплитуда тока выше 12 А         -> реальный сверхток
ВСЕГДА печатается ограничение: при 25 Гц импульс короче 40 мс может не попасть в выборку —
физическую причину устанавливает осциллограф (спад SD + 15 В рейл + VBUS), лог только коррелирует.

Коды возврата: 0 = всё хорошо (--gate: событий нет), 3 = события есть (--gate),
4 = события есть и линия не вернулась, 1 = данные непригодны, 2 = ошибка входа.
"""
import argparse
import glob
import json
import os
import re
import sys

FRAME_MS = 40.0                     # @VFLOG: 25 Гц
LSB_MA = 3300.0 * 1e6 / (4095.0 * 63000.0)   # мА на отсчёт: 63 мВ/А, без калибровки
I_MAX_MA = 12000.0                  # PROTECT_FAULT_OVERCURRENT: порог 12 А
WINDOW = 5                          # кадров вокруг события для оценки тока
RECOVER_LOOKAHEAD = 5               # кадров, в которых ищем возврат линии в 1
LINE_KEYS = ('sd1', 'sd2')


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


def frame_invalid(f):
    """Кадр не получен: i1=i2=ires=vbus=0 одновременно (см. FINDINGS сессии 1)."""
    vals = [ival(f, k) for k in ('i1', 'i2', 'ires', 'vbus')]
    got = [v for v in vals if v is not None]
    return bool(got) and all(v == 0 for v in got)


def amp_counts(fr, key):
    vals = [ival(f, key) for f in fr]
    vals = [v for v in vals if v is not None]
    return (max(vals) - min(vals), len(vals)) if vals else (0, 0)


def counts_to_a(counts):
    return counts * LSB_MA / 1000.0


def analyse_file(path):
    fr = frames_of(path)
    out = {'file': path, 'frames': len(fr), 'invalid_frames': 0, 'events': [],
           'fault_values': [], 'commit0': 0, 't_first': None, 't_last': None,
           'sd_in_invalid_frames': 0}
    if not fr:
        return out
    ts = [ival(f, 't') for f in fr]
    ts = [t for t in ts if t is not None]
    if ts:
        out['t_first'], out['t_last'] = min(ts), max(ts)
    out['invalid_frames'] = sum(1 for f in fr if frame_invalid(f))
    out['fault_values'] = sorted({ival(f, 'fault') for f in fr if ival(f, 'fault') is not None})
    out['commit0'] = sum(1 for f in fr if ival(f, 'commit') == 0)
    base = {}
    fr_valid = [f for f in fr if not frame_invalid(f)]
    out['valid_frames'] = len(fr_valid)
    for key in ('i1', 'i2', 'ires'):
        base[key] = amp_counts(fr_valid, key)[0]
    for idx, f in enumerate(fr):
        for key in LINE_KEYS:
            if ival(f, key) == 0:
                if frame_invalid(f):
                    # кадр не получен: sd-поле из него не доказательство
                    out['sd_in_invalid_frames'] += 1
                    continue
                t = ival(f, 't')
                last = out['events'][-1] if out['events'] else None
                if last and last['line'] == key and last['idx_end'] == idx - 1:
                    last['idx_end'] = idx
                    last['t_end'] = t
                    continue
                out['events'].append({'line': key, 'idx_start': idx, 'idx_end': idx,
                                      't_start': t, 't_end': t})
    for ev in out['events']:
        lo = max(0, ev['idx_start'] - WINDOW)
        hi = min(len(fr), ev['idx_end'] + WINDOW + 1)
        win = [f for f in fr[lo:hi] if not frame_invalid(f)]
        ev['win_invalid'] = (hi - lo) - len(win)
        ev['win_amp'] = {}
        for key in ('i1', 'i2', 'ires'):
            ev['win_amp'][key] = amp_counts(win, key)[0]
        ev['i_max_a'] = max(counts_to_a(v) for v in ev['win_amp'].values())
        ev['fault'] = ival(fr[ev['idx_start']], 'fault')
        ev['fault_r'] = fr[ev['idx_start']].get('fault_r', fr[ev['idx_start']].get('FAULT_R'))
        ev['commit'] = ival(fr[ev['idx_start']], 'commit')
        # Возврат линии в 1 ищем ОТ НАЧАЛА события до конца лога: low-кадры идут подряд,
        # поэтому 1 в хвосте означает возврат, а отсутствие 1 — отсутствие возврата.
        nxt = [ival(f, ev['line']) for f in fr[ev['idx_start'] + 1:]]
        nxt = [v for v in nxt if v is not None]
        ev['recovered'] = True if 1 in nxt else (None if not nxt else False)
        ev['frames_low'] = ev['idx_end'] - ev['idx_start'] + 1
        ev['ms_low_ge'] = ev['frames_low'] * FRAME_MS
        ev['class'] = classify(ev)
    out['baseline_amp_counts'] = base
    return out


def classify(ev):
    if ev['i_max_a'] > I_MAX_MA / 1000.0:
        return 'реальный сверхток (амплитуда > 12 А в окне события)'
    if ev['recovered'] is None:
        return 'не определено: лог кончился на низкой линии (удлинить захват)'
    if ev['recovered'] is False:
        return 'латчируемый источник (линия не вернулась в 1)'
    return 'короткий импульс (линия вернулась, токи в норме): вход/наводка/дребезг'


def collect(target):
    if os.path.isdir(target):
        files = sorted(glob.glob(os.path.join(target, '**', '*.log'), recursive=True))
        files += sorted(glob.glob(os.path.join(target, '**', '*.txt'), recursive=True))
    else:
        files = [target]
    return [f for f in files if os.path.isfile(f)]


def report(results, gate):
    ev_all = [ev for r in results for ev in r['events']]
    print('=== характеризация SD/FAULT_N: разбор @VFLOG ===')
    for r in results:
        t0, t1 = r['t_first'], r['t_last']
        span = ('%.1f с' % ((t1 - t0) / 1000.0)) if t0 is not None and t1 is not None else 'н/д'
        print('  %s' % r['file'])
        print('     кадров %d (валидных %d), длительность %s, невалидных кадров %d, commit=0 %d, '
              'fault=%s' % (r['frames'], r.get('valid_frames', r['frames']), span,
                            r['invalid_frames'], r['commit0'], r['fault_values']))
        if r.get('sd_in_invalid_frames'):
            print('     sd=0 встречено в невалидных кадрах: %d (не считается событием)'
                  % r['sd_in_invalid_frames'])
        b = r['baseline_amp_counts']
        print('     база (размах сырых токов по ВАЛИДНЫМ кадрам), отсч.: i1=%d i2=%d ires=%d'
              % (b['i1'], b['i2'], b['ires']))
        if not r['events']:
            print('     событий SD1/SD2=0 не зафиксировано')
    if ev_all:
        print('  события:')
        for ev in ev_all:
            rec = {True: 'да', False: 'НЕТ', None: 'нет данных (лог кончился)'}[ev['recovered']]
            print('     %s t=%s мс: линия низкая %d кадр(ов) (>= %.0f мс), вернулась=%s, '
                  'ток в окне max %.2f А (i1=%d i2=%d ires=%d отсч.), fault=%s, commit=%s'
                  % (ev['line'], ev['t_start'], ev['frames_low'], ev['ms_low_ge'],
                     rec, ev['i_max_a'],
                     ev['win_amp']['i1'], ev['win_amp']['i2'], ev['win_amp']['ires'],
                     ev['fault'], ev['commit']))
            if ev.get('win_invalid'):
                print('        в окне события %d невалидных кадр(ов) — из оценки токов исключены'
                      % ev['win_invalid'])
            print('        класс: %s' % ev['class'])
    print('  ОГРАНИЧЕНИЕ: 25 Гц -> импульс SD короче 40 мс может не попасть в выборку; '
          'физическую причину устанавливает осциллограф (спад SD + 15 В рейл + VBUS).')
    print('  ВНИМАНИЕ: амплитуда токов — по номиналу 63 мВ/А без калибровки; '
          'невалидные кадры (i1=i2=ires=vbus=0) в выводы не берутся.')
    if not gate:
        return 0
    if not ev_all:
        return 0
    if any(ev['recovered'] is False for ev in ev_all):
        return 4
    return 3


def selftest():
    import tempfile
    fails = []
    checks = [0]

    def line(t, sd1, sd2, i1, i2, ires=0, vbus=600, fault=0, commit=1):
        return ('@VFLOG:t=%d:target=50:meas=5:fe=2:fslip=0:vmag=19:theta=1:du=51:dv=55:dw=41:'
                'i1=%d:i2=%d:ires=%d:vbus=%d:eangle=1000:espeed=5:eerr=0:fault=%d:drp=0:'
                'sd1=%d:sd2=%d:swing=0:commit=%d\n' % (t, i1, i2, ires, vbus, fault, sd1, sd2, commit))

    cases = []
    # (имя, строки, ожидаемый код, ожидаемый класс в тексте)
    clean = [line(40 * i, 1, 1, 2050, 2050) for i in range(10)]
    cases.append(('чистый базис', clean, 0, None))
    spike = [line(40 * i, 1, 1, 2050, 2050) for i in range(10)]
    spike[4] = line(160, 1, 0, 2050, 2052)
    cases.append(('короткий импульс SD2', spike, 3, 'короткий импульс'))
    latched = [line(40 * i, 1, 1, 2050, 2050) for i in range(10)]
    for i in range(6, 10):
        latched[i] = line(40 * i, 1, 0, 2050, 2051)
    cases.append(('линия не вернулась', latched, 4, 'латчируемый'))
    # размах внутри ОДНОГО канала (i1: 2600 -> 1500 = 1100 отсч. = 14.1 А при 63 мВ/А)
    over = [line(40 * i, 1, 1, 2050, 2050, ires=2050) for i in range(10)]
    over[4] = line(160, 1, 0, 2600, 2050, ires=2050)
    over[5] = line(200, 1, 0, 1500, 2050, ires=2050)
    cases.append(('сверхток в окне', over, 3, 'сверхток'))
    tail = [line(40 * i, 1, 1, 2050, 2050) for i in range(5)]
    tail.append(line(200, 1, 0, 2050, 2050))
    cases.append(('лог кончился на низкой линии', tail, 3, 'не определено'))

    with tempfile.TemporaryDirectory() as td:
        for name, lines, want, want_cls in cases:
            p = os.path.join(td, 'case.log')
            open(p, 'w', encoding='utf-8').write(''.join(lines))
            res = [analyse_file(p)]
            got = report(res, gate=True)
            text = '\n'.join(ev['class'] for r in res for ev in r['events'])
            checks[0] += 2
            if got != want:
                fails.append('«%s»: код %d, ожидался %d' % (name, got, want))
            if want_cls and want_cls not in text:
                fails.append('«%s»: класс «%s» не найден в «%s»' % (name, want_cls, text))
        # невалидный кадр (i1=i2=ires=vbus=0) не должен считаться событием и должен быть посчитан
        p = os.path.join(td, 'invalid.log')
        bad = [line(40 * i, 1, 1, 2050, 2050) for i in range(5)]
        bad.append('@VFLOG:t=240:target=50:meas=5:fe=2:fslip=0:vmag=19:theta=1:du=0:dv=0:dw=0:'
                   'i1=0:i2=0:ires=0:vbus=0:eangle=1000:espeed=5:eerr=0:fault=0:drp=0:'
                   'sd1=1:sd2=1:swing=0:commit=1\n')
        open(p, 'w', encoding='utf-8').write(''.join(bad))
        r = analyse_file(p)
        checks[0] += 2
        if r['invalid_frames'] != 1:
            fails.append('невалидный кадр не посчитан (got %d)' % r['invalid_frames'])
        if report([r], gate=True) != 0:
            fails.append('невалидный кадр принят за событие SD')
    if fails:
        print('  САМОТЕСТ ПРОВАЛЕН (%d):' % len(fails))
        for f in fails:
            print('   ! %s' % f)
        return 1
    print('  самотест: %d проверок, 0 провалов' % checks[0])
    return 0


def main(argv=None):
    force_safe_stdout()
    ap = argparse.ArgumentParser(description='разбор SD/FAULT_N по @VFLOG (25 Гц)')
    ap.add_argument('target', nargs='*', help='файлы логов и/или папки')
    ap.add_argument('--gate', action='store_true', help='код возврата по событиям (0/3/4)')
    ap.add_argument('--json', dest='json_out', help='сохранить результат в JSON')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    files = []
    for tgt in a.target:
        files += collect(tgt)
    files = sorted(dict.fromkeys(files))
    if not files:
        ap.error('нужны файлы логов или папки')
    if not files:
        print('  ошибка: не найдено файлов для разбора', file=sys.stderr)
        return 2
    results = [analyse_file(f) for f in files]
    results = [r for r in results if r['frames']]
    if not results:
        print('  данные непригодны: в файлах нет @VFLOG', file=sys.stderr)
        return 1
    code = report(results, gate=a.gate)
    if a.json_out:
        json.dump(results, open(a.json_out, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    return code


if __name__ == '__main__':
    sys.exit(main())
