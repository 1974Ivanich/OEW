#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Шаг 0: машинный вердикт «что на плате» (дефект B13, ред. 2).

Дефект B13: в первых редакциях шаг 0 был описан словами («снять и посмотреть
глазами»), и пакет сам заявлял, что проверить образ софтом невозможно. Глазами
радиксы и склейка строк читаются неверно — ровно из этого класса вырос B11
(строку `@PWM:FULL` приписали `p?`, который её не печатает). Инструмент
превращает шаг 0 в вердикт с кодом возврата:

  py -3 TOOLS/check_step0.py --log step0_raw.log        # офлайн, по транскрипту
  py -3 TOOLS/check_step0.py --port COM4 --out .\\step0  # снять сам (только чтение)
  py -3 TOOLS/check_step0.py --selftest                 # самопроверка инструмента

Источники (реальные форматы образа `ab1274d`, `src/cli.c`; НЕ по памяти):

  sysinfo  `@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:...:JEOS=%lu:...`
  pdump    `@PWM:FULL:SYS=%lu:...:T1:PSC=%u:ARR=%u:...:CR1=0x%08lX:...`  (hex)
  p?       `@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu`                     (dec)
  dump     `@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:...`     (hex)
  enc      `@ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u`

Важно (дефект B14): в строке `@PWM:FULL` префикс группы стоит только у первого
поля группы — образ печатает `T1:PSC=16:ARR=999:…:CR1=0x…E0:CNT=0:T8:PSC=16:…`,
то есть токенов `T1:ARR`/`T1:CR1`/`T1:CCER`/`T1:BDTR` в выводе НЕТ. Поиск по ним
даёт ложное «на плате другой образ» (тот же класс, что B11). Здесь поля обеих
групп разбираются отдельно.

Проверяется identity образа по живым регистрам: `CLK=170000000`, `PSC=16`,
`ARR=999`, `CR1` (CMS=`11`, ARPE=1, CEN=0), `CCER=0`, `BDTR` с MOE=0. Тот же
регистр сверяется в двух радиксах (`p?` dec против `pdump` hex) и в двух
источниках (`dump` против `pdump`) — ошибка в чтении строки не проходит.

Честность вердикта (правила пакета: «не измерено» != PASS):
  * `RCR` не печатает ни одна команда образа (`PWM_DumpRegs` читает только
    PSC/ARR/BDTR/CR1/CR2/CCER) -> RCR помечается NOT MEASURED и остаётся
    якорем на исходник (`src/pwm.c:273`), а не «проверенным»;
  * нет телеметрии вовсе (пусто, не тот порт, плата обесточена) -> код 2
    (ERROR), а не PASS;
  * нет обязательного источника (`sysinfo`/`pdump`/`p?`) -> FAIL с указанием,
    какую команду не отправили: «молча пройти» этот случай не может.

Ничего не пишется на плату и ничего не стартует: набор команд (при `--port`)
только читающий (`sysinfo`, `a?`, `pdump`, `p?`, `dump`, `breakdiag`, `enc`).
`1`, `i=`, `f`, `a`, `c` этим инструментом не отправляются никогда.

Коды возврата: 0 — PASS, 1 — FAIL (расхождение identity), 2 — ERROR (нет
данных / ошибка вызова). Вывод в консоль — ASCII-only (консоли cp1251/ascii).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

EXP_CLK = 170000000          # `@SYS:CLK` / `@PWM:FULL:SYS` (SystemCoreClock)
EXP_PSC = 16                 # CLK/10 МГц - 1 (src/pwm.c:243)
EXP_ARR = 999                # f_pwm = 5000 Гц (src/pwm.c:258)
EXP_CMS = 3                  # TIM1->CR1[6:5] = 11 (center-aligned)
EXP_BDTR_DEC = 7360          # `p?` dec: 0x1CC0 (MOE=0, OSSI/OSSR/BKE=1)
DECLARED_SOURCE = 'ab1274d'  # образ, заявленный в §1 протокола
RCR_ANCHOR = 'src/pwm.c:273 (TIM1->RCR = 1u)'
ENC_BITS = 16384             # 14 бит: 16384 = один оборот вала

CMD_SWEEP = ('sysinfo', 'a?', 'pdump', 'p?', 'dump', 'breakdiag', 'enc')
CORE = (('sysinfo', 'sys', '@SYS'), ('pdump', 'full', '@PWM:FULL'),
        ('p?', 'pwm', '@PWM:CR1'))
OPT = ('dump', 'enc', 'a?', 'breakdiag')


def _lines(text):
    """Строки транскрипта; CRLF/CR/LF равноправны (терминалы разные)."""
    return [ln for ln in (text or '').replace('\r', '\n').split('\n')]


def _last(text, prefix):
    """Последняя строка, начинающаяся с `prefix` (ничего не смешивается)."""
    found = [ln for ln in _lines(text) if ln.startswith(prefix)]
    return found[-1] if found else ''


def _ints(line, names):
    """Десятичные поля `NAME=<int>` из одной строки (ключи в нижнем регистре)."""
    out = {}
    for nm in names:
        vals = re.findall(r'(?<![A-Za-z0-9_])%s=(-?\d+)' % re.escape(nm), line)
        if vals:
            out[nm.lower()] = int(vals[-1])
    return out


def _hexval(line, name):
    """Hex-поле `NAME=0x…` из одной строки (None, если поля нет)."""
    vals = re.findall(r'(?<![A-Za-z0-9_])%s=0[xX]([0-9A-Fa-f]+)' % re.escape(name), line)
    return int(vals[-1], 16) if vals else None


def _group_fields(group):
    """Поля одной группы `T1:`/`T8:` строки `@PWM:FULL`.

    В формате образа (`src/cli.c`) префикс группы стоит **только у первого поля**:
    `T1:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu:T8:PSC=…`.
    Токенов `T1:ARR`/`T1:CR1` в выводе нет — поиск по ним даёт ложное
    «на плате другой образ» (дефект B14). Поэтому поля группы разбираются
    отдельно, а не как `T1:ИМЯ`.
    """
    out = {}
    for key in ('PSC', 'ARR', 'CNT'):
        vals = re.findall(r'(?<![A-Za-z0-9_])%s=(-?\d+)' % key, group)
        if vals:
            out[key.lower()] = int(vals[-1])
    for key in ('BDTR', 'CCER', 'CR1'):
        vals = re.findall(r'(?<![A-Za-z0-9_])%s=0[xX]([0-9A-Fa-f]+)' % key, group)
        if vals:
            out[key.lower()] = int(vals[-1], 16)
    return out


def full_groups(line):
    """Строка `@PWM:FULL` -> (поля T1, поля T8, SYS).

    Граница групп — токен `T8:`; `SYS` берётся из головы строки.
    """
    body = line.split('FULL:', 1)[1] if 'FULL:' in line else line
    i8 = body.find('T8:')
    t1 = body[:i8] if i8 >= 0 else body
    t8 = body[i8:] if i8 >= 0 else ''
    sysv = re.findall(r'(?<![A-Za-z0-9_])SYS=(-?\d+)', line)
    return _group_fields(t1), _group_fields(t8), (int(sysv[-1]) if sysv else None)


def sections(text):
    """Кусочки транскрипта по владельцам строк (каждый формат — свой префикс)."""
    pwm_dec = ''
    for ln in _lines(text):
        if ln.startswith('@PWM:') and 'FULL' not in ln and 'DUMP' not in ln and 'CR1=' in ln:
            pwm_dec = ln
    return {
        'sys': _last(text, '@SYS'),
        'full': _last(text, '@PWM:FULL'),
        'pwm': pwm_dec,
        'dump': _last(text, '@PWM:DUMP'),
        'enc': _last(text, '@ENC'),
        'adc': _last(text, '@ADC'),
        'brk': _last(text, '@BRK'),
        'any': any(ln.startswith('@') for ln in _lines(text)),
    }


class Checks(object):
    """Проверки с именем, фактом и ожиданием (форма как в HWT-отчётах)."""

    def __init__(self):
        self.items = []

    def add(self, name, ok, actual=None, expected=None, detail=''):
        self.items.append({'name': name, 'ok': bool(ok), 'actual': actual,
                           'expected': expected, 'detail': detail})

    def failed(self):
        return [i for i in self.items if not i['ok']]

    def ok(self):
        return bool(self.items) and not self.failed()


def verdict_for(text, exp_bdtr=EXP_BDTR_DEC, declared=DECLARED_SOURCE, require_all=False):
    """Вердикт по транскрипту. Возвращает dict: checks/notes/identity/rc/error."""
    sec = sections(text)
    c = Checks()
    notes = []
    identity = {'declared_source': declared, 'declared_bdtr': exp_bdtr}

    if not sec['any']:
        return {'checks': c, 'notes': notes, 'identity': identity, 'rc': 2,
                'error': 'no telemetry (@...) in the transcript: nothing was measured '
                         '(board off, wrong port/baud, or empty capture)'}

    # ── обязательные и необязательные источники ───────────────────────────
    for cmd, key, mark in CORE:
        present = bool(sec[key])
        c.add('source %s present (%s)' % (cmd, mark), present, present, True,
              '' if present else 'no %s output found; send `%s` and repeat step 0' % (mark, cmd))
    for mark, key in (('dump', 'dump'), ('enc', 'enc'), ('a?', 'adc'), ('breakdiag', 'brk')):
        present = bool(sec[key])
        if present:
            notes.append('%s read' % mark)
        elif require_all:
            c.add('source %s present (--require-all)' % mark, False, False, True,
                  'the documented step 0 set includes `%s`' % mark)
        else:
            notes.append('%s NOT PRESENT (not required by this check)' % mark)

    # ── sysinfo: часы и делитель (якорь шкалы, дефект B1) ────────────────
    si = _ints(sec['sys'], ('CLK', 'PSC', 'TCLK', 'JEOS'))
    clk, psc, tclk = si.get('clk'), si.get('psc'), si.get('tclk')
    c.add('sysinfo CLK == %d' % EXP_CLK, clk == EXP_CLK, clk, EXP_CLK, sec['sys'][:120])
    c.add('sysinfo PSC == %d' % EXP_PSC, psc == EXP_PSC, psc, EXP_PSC,
          'divider PSC+1 = %s' % (psc + 1 if isinstance(psc, int) else 'n/a'))
    if isinstance(clk, int) and isinstance(psc, int) and psc >= 0:
        want_tclk = clk // (psc + 1)
        c.add('sysinfo TCLK == CLK/(PSC+1)', tclk == want_tclk, tclk, want_tclk,
              'cross-check of the scale anchor: TCLK is the counter clock, not CLK (B1)')
    else:
        c.add('sysinfo TCLK == CLK/(PSC+1)', False, tclk, 'n/a', 'CLK/PSC missing')
    c.add('sysinfo JEOS present', isinstance(si.get('jeos'), int), si.get('jeos'), 'int',
          'JEOS is the metric source of every cell')

    # ── pdump: SYS/PSC/ARR/CR1 (hex) — источник identity, дефект B11 ─────
    g1, g8, f_sys = full_groups(sec['full'])
    f_cr1, f_ccer, f_bdtr = g1.get('cr1'), g1.get('ccer'), g1.get('bdtr')
    c.add('pdump SYS == sysinfo CLK', f_sys == clk, f_sys, clk,
          'both fields are SystemCoreClock (src/cli.c)')
    for up, g in (('T1', g1), ('T8', g8)):
        c.add('pdump %s group PSC == %d' % (up, EXP_PSC), g.get('psc') == EXP_PSC,
              g.get('psc'), EXP_PSC, 'field `%s:PSC` (prefix on the first field only)' % up)
        c.add('pdump %s group ARR == %d' % (up, EXP_ARR), g.get('arr') == EXP_ARR,
              g.get('arr'), EXP_ARR, 'field `ARR` inside the %s group (`T1:ARR` does not exist, B14)' % up)
        cr1 = g.get('cr1')
        if cr1 is None:
            c.add('pdump %s group CR1 present' % up, False, None, '0x...E0', 'hex CR1 missing')
            continue
        cms = (cr1 >> 5) & 3
        c.add('pdump %s group CR1 CMS(6:5) == 11' % up, cms == EXP_CMS, 'CMS=%d' % cms, 'CMS=3',
              'CR1=0x%08X (center-aligned mode 3)' % cr1)
        c.add('pdump %s group CR1 ARPE(7) == 1' % up, (cr1 >> 7) & 1 == 1,
              'ARPE=%d' % ((cr1 >> 7) & 1), 'ARPE=1', 'CR1=0x%08X' % cr1)
        c.add('pdump %s group CR1 CEN(0) == 0' % up, (cr1 & 1) == 0, 'CEN=%d' % (cr1 & 1), 'CEN=0',
              'the timer must be stopped before launch')

    # ── p?: тот же регистр в другом радиксе (ловушка B11) ────────────────
    dec = _ints(sec['pwm'], ('CR1', 'CCER', 'BDTR', 'CNT'))
    c.add('p? CR1 (dec) == pdump T1 group CR1 (hex)', dec.get('cr1') == f_cr1,
          dec.get('cr1'), f_cr1,
          'one register, two radices: 224 == 0xE0 (CMS=11); a wrong radix reads as CMS=00')
    c.add('p? CCER == 0', dec.get('ccer') == 0, dec.get('ccer'), 0)
    c.add('p? BDTR (dec) == pdump T1 group BDTR (hex)', dec.get('bdtr') == f_bdtr,
          dec.get('bdtr'), f_bdtr)
    c.add('p? BDTR == %d (--expect-bdtr)' % exp_bdtr, dec.get('bdtr') == exp_bdtr,
          dec.get('bdtr'), exp_bdtr, '0x%04X: MOE=0, OSSI/OSSR/BKE=1' % exp_bdtr)
    c.add('p? CNT == pdump T1 group CNT (timer frozen)', dec.get('cnt') == g1.get('cnt'),
          dec.get('cnt'), g1.get('cnt'), 'with CEN=0 both reads must agree')
    if f_bdtr is None:
        c.add('pdump T1 group BDTR present', False, None, '0x...', 'hex BDTR missing')
    else:
        c.add('pdump T1 group BDTR MOE(15) == 0', (f_bdtr >> 15) & 1 == 0,
              'MOE=%d' % ((f_bdtr >> 15) & 1), 0,
              'BDTR=0x%08X: the power stage must be off' % f_bdtr)
    if f_ccer is None:
        c.add('pdump T1 group CCER present', False, None, '0x00000000', 'hex CCER missing')
    else:
        c.add('pdump T1 group CCER == 0', f_ccer == 0, '0x%08X' % f_ccer, '0x00000000',
              'outputs disabled before launch')

    # ── dump: второй источник тех же регистров (перекрёстная сверка) ─────
    if sec['dump']:
        d = _ints(sec['dump'], ('PSC', 'ARR'))
        c.add('dump PSC/ARR == pdump T1 group', d.get('psc') == g1.get('psc')
              and d.get('arr') == g1.get('arr'),
              '%s/%s' % (d.get('psc'), d.get('arr')),
              '%s/%s' % (g1.get('psc'), g1.get('arr')))
        c.add('dump BDTR/CR1/CCER == pdump',
              _hexval(sec['dump'], 'BDTR') == f_bdtr and _hexval(sec['dump'], 'CR1') == f_cr1
              and _hexval(sec['dump'], 'CCER') == f_ccer,
              'BDTR=0x%08X' % (_hexval(sec['dump'], 'BDTR') or 0),
              'BDTR=0x%08X' % (f_bdtr or 0), 'independent read of the same registers')

    # ── enc: энкодер читается (нужен шагам 2 и 4, дефект B12) ────────────
    if sec['enc']:
        e = _ints(sec['enc'], ('angle', 'err'))
        c.add('enc err == 0', e.get('err') == 0, e.get('err'), 0,
              'the encoder must answer before the shaft is moved')
        ang = e.get('angle')
        c.add('enc angle in [0,%d)' % ENC_BITS, isinstance(ang, int) and 0 <= ang < ENC_BITS,
              ang, '0..%d' % (ENC_BITS - 1), '14 bits: 16384 = one shaft revolution')
        identity['encoder_angle'] = ang

    identity.update({
        'clk': clk, 'psc': psc, 'tclk': tclk, 'arr': g1.get('arr'),
        'cms': None if f_cr1 is None else (f_cr1 >> 5) & 3,
        'cr1_hex': None if f_cr1 is None else '0x%08X' % f_cr1,
        'cr1_dec': f_cr1 if f_cr1 is not None else dec.get('cr1'),
        'bdtr_dec': dec.get('bdtr'), 'cnt': dec.get('cnt'), 'jeos': si.get('jeos'),
    })
    notes.append('field names: the image prints `T1:PSC=..:ARR=..:..:CR1=0x..` and then '
                 '`T8:PSC=..:ARR=..:..:CR1=0x..` (the group prefix is on the first field of the '
                 'group only; tokens `T1:ARR`/`T1:CR1` do not exist - defect B14)')
    notes.append('RCR: NOT MEASURED - no command of this image prints RCR (PWM_DumpRegs reads '
                 'only PSC/ARR/BDTR/CR1/CR2/CCER); it stays an anchor on the declared source '
                 '%s (%s), not a measurement' % (declared, RCR_ANCHOR))
    notes.append('identity proof = live registers over UART (SYS/PSC/ARR/CR1/CMS + CCER/BDTR/MOE); '
                 'the Flash CRC is not readable over UART (that is the HWT image check, needs SWD)')
    rc = 0 if c.ok() else 1
    return {'checks': c, 'notes': notes, 'identity': identity, 'rc': rc, 'error': None}


def render(res, source='-'):
    """ASCII-отчёт (консоли cp1251/ascii не должны падать)."""
    lines = ['check_step0: what image is on the board (step 0, package rev. 2)',
             'source    : %s' % source]
    for it in res['checks'].items:
        detail = (' (%s)' % it['detail']) if it['detail'] else ''
        lines.append('  [%s] %-44s actual=%r expected=%r%s'
                     % ('OK' if it['ok'] else '!!', it['name'], it['actual'],
                        it['expected'], detail))
    for nt in res['notes']:
        lines.append('  [--] %s' % nt)
    ok_n = sum(1 for i in res['checks'].items if i['ok'])
    verdict = 'ERROR' if res['rc'] == 2 else ('PASS' if res['rc'] == 0 else 'FAIL')
    lines.append('checks    : %d/%d OK' % (ok_n, len(res['checks'].items)))
    lines.append('verdict   : %s (rc=%d)' % (verdict, res['rc']))
    if res['error']:
        lines.append('error     : %s' % res['error'])
    if res['rc'] == 1:
        for it in res['checks'].failed()[:8]:
            lines.append('reason    : %s -> actual=%r expected=%r'
                         % (it['name'], it['actual'], it['expected']))
        lines.append('reason    : the board does not match the declared image; '
                     'do NOT start the scan')
    return '\n'.join(lines) + '\n'


def _sha(text):
    import hashlib
    return hashlib.sha256((text or '').encode('utf-8', 'replace')).hexdigest()


def write_outputs(res, out_dir, text, source):
    """`step0_report.txt` + `result.json` (вердикт не зависит от консоли)."""
    report = render(res, source)
    if not out_dir:
        return None
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, 'step0_report.txt'), 'w',
                  encoding='utf-8', newline='\n') as fh:
            fh.write(report)
        payload = {
            'tool': 'check_step0.py', 'source': source,
            'verdict': 'ERROR' if res['rc'] == 2 else ('PASS' if res['rc'] == 0 else 'FAIL'),
            'exit_code': res['rc'], 'identity': res['identity'],
            'checks': res['checks'].items,
            'not_measured': [n for n in res['notes'] if 'NOT MEASURED' in n],
            'error': res['error'], 'transcript_sha256': _sha(text),
        }
        with open(os.path.join(out_dir, 'result.json'), 'w',
                  encoding='utf-8', newline='\n') as fh:
            json.dump(payload, fh, ensure_ascii=True, indent=2)
            fh.write('\n')
    except OSError as exc:
        print('WARNING: cannot write report to %s: %s' % (out_dir, exc))
        return None
    return os.path.join(out_dir, 'step0_report.txt')


STEP0_POS = ('> sysinfo\r\n'
             '@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x41540828:OVR=0:JEOS=100:TO=0:'
             'JQOVF=0:uart_drp=0:uart_trunc=0\r\n'
             '> a?\r\n@ADC:STATUS:offset_i1=2048:stream=0\r\n'
             '> pdump\r\n'
             '@PWM:FULL:SYS=170000000:CFGR=0x00000000:T1:PSC=16:ARR=999:CCR=500,500,500:'
             'BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0:T8:PSC=16:ARR=999:'
             'CCR=500,500,500:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0\r\n'
             '> p?\r\n@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0\r\n'
             '> dump\r\n@PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:'
             'CR2=0x00000000:CCER=0x00000000\r\n'
             '> breakdiag\r\n@BRK:valid=0\r\n'
             '> enc\r\n@ENC:angle=8192:speed=0:period_us=0:pulse_us=0:err=0\r\n> ')


def selftest():
    """Самопроверка: положительный транскрипт + негативные (в т.ч. ловушка B11)."""
    cases = [
        ('POS', STEP0_POS, 0, ''),
        ('NEG_ARR', STEP0_POS.replace('T1:PSC=16:ARR=999', 'T1:PSC=16:ARR=998'), 1, 'ARR'),
        ('NEG_NO_PDUMP', '\r\n'.join(ln for ln in _lines(STEP0_POS)
                                     if 'FULL' not in ln and 'DUMP' not in ln) + '\r\n', 1,
         'pdump'),
        ('NEG_MOE', STEP0_POS.replace('BDTR=0x00001CC0', 'BDTR=0x00009CC0'), 1, 'MOE'),
        ('NEG_CMS', STEP0_POS.replace('CR1=0x000000E0', 'CR1=0x00000080'), 1, 'CMS'),
        ('NEG_ENC_ERR', STEP0_POS.replace('pulse_us=0:err=0', 'pulse_us=0:err=2'), 1, 'enc err'),
        ('NEG_EMPTY', 'PuTTY session, nothing received\r\n> ', 2, ''),
    ]
    bad = 0
    for name, text, want_rc, want_in in cases:
        res = verdict_for(text)
        reasons = ' '.join(it['name'] for it in res['checks'].failed()) + ' ' + (res['error'] or '')
        ok = res['rc'] == want_rc and want_in in reasons
        print('  [%s] %-13s rc=%d (want %d)%s'
              % ('OK' if ok else '!!', name, res['rc'], want_rc,
                 '' if ok else ' reasons=%r' % reasons.strip()))
        bad += 0 if ok else 1
    print('SELFTEST: %s %d/%d' % ('PASS' if bad == 0 else 'FAIL', len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


def capture_port(port, baud, eol):
    """Снять шаг 0 самому: только читающие команды, ни одной записи в цель."""
    try:
        import serial  # pyserial
    except ImportError:
        raise RuntimeError('pyserial is required for --port; use --log <transcript> instead')
    ser = serial.Serial(port, baud, timeout=0.2)
    chunks = ['# check_step0 capture: port=%s baud=%d eol=%s commands=%s'
              % (port, baud, eol, ','.join(CMD_SWEEP))]
    try:
        for cmd in CMD_SWEEP:
            chunks.append('> ' + cmd)
            ser.write((cmd + eol).encode('ascii'))
            t0 = time.time()
            got = []
            while time.time() - t0 < 3.0:
                blk = ser.read(4096)
                if blk:
                    got.append(blk.decode('ascii', 'replace'))
                    t0 = time.time()
                elif got:
                    break
                time.sleep(0.02)
            chunks.append(''.join(got))
    finally:
        ser.close()
    return ''.join(chunks)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Step 0 verdict: what image is on the board (package rev. 2; defect B13).')
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--log', help='transcript file (captured by any terminal)')
    src.add_argument('--port', help='COM port: the tool captures itself (read-only commands)')
    src.add_argument('--selftest', action='store_true', help='built-in positive/negative fixtures')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--eol', default='crlf', choices=['crlf', 'lf'])
    ap.add_argument('--out', default='step0', help='output dir (step0_report.txt, result.json)')
    ap.add_argument('--expect-bdtr', type=int, default=EXP_BDTR_DEC,
                    help='BDTR dec from p? (bench waiver: record it in the return)')
    ap.add_argument('--declared-source', default=DECLARED_SOURCE,
                    help='image source declared in protocol section 1')
    ap.add_argument('--require-all', action='store_true',
                    help='also require dump/enc/a?/breakdiag lines in the transcript')
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.log and not a.port:
        print('usage error: give --log <transcript> or --port COMx')
        print('nothing is sent to the board by default: this tool never starts FOC, '
              'never sends 1/i=/f/a/c and never powers anything')
        return 2

    if a.port:
        try:
            text = capture_port(a.port, a.baud, '\r\n' if a.eol == 'crlf' else '\n')
        except Exception as exc:  # noqa: BLE001 - транспорт: это ERROR, а не FAIL
            print('ERROR: cannot read port %s: %s' % (a.port, exc))
            return 2
        source = 'port:%s@%d' % (a.port, a.baud)
        if a.out:
            try:
                os.makedirs(a.out, exist_ok=True)
                with open(os.path.join(a.out, 'step0_transcript.log'), 'w',
                          encoding='utf-8', newline='\n') as fh:
                    fh.write(text)
            except OSError as exc:
                print('WARNING: cannot save the transcript: %s' % exc)
    else:
        try:
            with open(a.log, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError as exc:
            print('ERROR: cannot read %s: %s' % (a.log, exc))
            return 2
        source = os.path.basename(a.log)

    res = verdict_for(text, exp_bdtr=a.expect_bdtr, declared=a.declared_source,
                      require_all=a.require_all)
    write_outputs(res, a.out, text, source)
    sys.stdout.write(render(res, source))
    return res['rc']


if __name__ == '__main__':
    sys.exit(main())

