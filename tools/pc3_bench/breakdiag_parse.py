#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разбор сырой строки `breakdiag` (команда `breakdiag`, src/cli.c:147) в читаемые флаги.

Использование:
    py -3 breakdiag_parse.py <файл со строкой(ами) @BRK:...>
    py -3 breakdiag_parse.py --selftest

Формат (полный):
  @BRK:valid=1:seq=%lu:src=%s:cyc=%lu:sr=%lX,%lX:sd=%u,%u:bd=%lX,%lX:ce=%lX,%lX:cnt=%u,%u:cap=%u,%lu,%u
Порядок пар — TIM1,TIM8.

Строка может быть УСЕЧЕНА. В возврате ПК-3 цитируется именно усечённый вариант:
  `@BRK:valid=1:seq=1:src=TIM1:…:sr=81,81:sd=1,1:bd=1CC0,1CC0`  (нет cyc, ce, cnt, cap)
Такой дамп разбирается частично: печатается то, что есть, отсутствующие поля помечаются,
код возврата 2. Падения (ValueError / traceback) на неполной строке нет.

Коды возврата:
    0 — разбор полный, противоречий с правилом прошивки нет (или @BRK:valid=0)
    1 — найдено противоречие с правилом прошивки
    2 — строка усечена/испорчена: вывод неполный, нужен повторный захват `breakdiag` в файл
    3 — входа нет или он не содержит строки @BRK:

Правило прошивки, с которым сверяется дамп (src/pwm.c:37-38, 159-161):
  PWM_BDTR_REQUIRED  = BKE | OSSR | OSSI      -> должно быть установлено
  PWM_BDTR_FORBIDDEN = BKP | BK2E | AOE       -> должно быть СНЯТО
  иначе pwm_bdtr_healthy()==0 -> PWM_HardwareInterlockHealthy()==0 -> PWM_Enable() не пустит пуск.

Консоль Windows по умолчанию — cp1251: символы «✓»/«✗»/«Δ» роняли инструмент
UnicodeEncodeError ещё до вывода результата. Здесь весь вывод — символы cp1251
(OK / (x) вместо галочек), плюс страховка errors='replace'.
"""
import re
import sys

BDTR = [(15, 'MOE'), (14, 'AOE'), (13, 'BKBID'), (12, 'BKE'), (11, 'BKP'),
        (10, 'OSSR'), (9, 'OSSI'), (8, 'LOCK')]
FORBIDDEN = {'BKP', 'BK2E', 'AOE'}
REQUIRED = {'BKE', 'OSSR', 'OSSI'}
SR = [(7, 'BIF'), (6, 'B2IF'), (5, 'BRK2?'), (4, 'COMIF'), (3, '?'), (2, '?'), (1, 'CC2IF'), (0, 'UIF')]
CCER = [(15, 'CC1NP'), (14, 'CC1NE'), (13, 'CC1P'), (12, 'CC1E'), (11, 'CC2NP'),
        (10, 'CC2NE'), (9, 'CC2P'), (8, 'CC2E'), (7, 'CC3NP'), (6, 'CC3NE'), (5, 'CC3P'), (4, 'CC3E')]
# поля, без которых разбор нельзя считать полным (формат src/cli.c:147)
FULL_KEYS = ('valid', 'seq', 'src', 'cyc', 'sr', 'sd', 'bd', 'ce', 'cnt', 'cap')

RC_OK, RC_CONTRA, RC_TRUNCATED, RC_NO_INPUT = 0, 1, 2, 3


def force_safe_stdout():
    """Не дать консоли (cp1251 / ascii / 866) уронить разбор: незаменяемый символ -> '?'."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def flags(val, table):
    return ' '.join('%s=%d' % (n, (val >> b) & 1) for b, n in table if b < 32)


def bdtr_bits(val):
    return {n: (val >> b) & 1 for b, n in BDTR}


def check_bdtr(val, who):
    f = bdtr_bits(val)
    miss = [n for n in REQUIRED if not f[n]]
    forb = [n for n in ('BKP', 'AOE') if f.get(n)]
    print('    %s BDTR=0x%08X: %s' % (who, val, flags(val, BDTR)))
    if miss:
        print('      (x) не установлено обязательное: %s' % ', '.join(miss))
    if forb:
        print('      (x) УСТАНОВЛЕНО ЗАПРЕЩЁННОЕ: %s' % ', '.join(forb))
        print('        -> такой BDTR был бы отвергнут PWM_HardwareInterlockHealthy(), '
              'пуск не состоялся бы')
    if not miss and not forb:
        print('      OK: соответствует правилу прошивки (BKE/OSSR/OSSI=1, BKP/AOE=0)')
    return forb


def parse(line):
    m = re.search(r'@BRK:([^\r\n]*)', line)
    if not m:
        return None
    out = {}
    for tok in m.group(1).split(':'):
        if '=' not in tok:
            continue          # в цитатах встречается «…» вместо поля
        k, _, v = tok.partition('=')
        out[k.strip()] = v.strip()
    return out


def numbers(d, key, base):
    """Числовое поле вида `a,b` -> (значения, поле полное). Пустое/битое поле не роняет разбор."""
    raw = d.get(key)
    if raw is None or raw.strip() == '':
        return [], False
    vals = []
    for tok in raw.split(','):
        tok = tok.strip()
        if tok == '':
            return vals, False
        try:
            vals.append(int(tok, base))
        except ValueError:
            return vals, False
    return vals, True


def show(d):
    """Печатает разбор. Возвращает (код, список отсутствующих полей)."""
    if d.get('valid') is None:
        print('  в строке нет поля valid= — это не ответ на команду breakdiag')
        return RC_NO_INPUT, ['valid']
    if d.get('valid') == '0':
        print('  @BRK:valid=0 — записанных break-событий нет')
        return RC_OK, []
    missing = [k for k in FULL_KEYS if k not in d]
    print('  src=%s  seq=%s  cyc=%s  cnt=%s  cap=%s'
          % (d.get('src'), d.get('seq'), d.get('cyc', 'НЕТ В СТРОКЕ'),
             d.get('cnt', 'НЕТ В СТРОКЕ'), d.get('cap', 'НЕТ В СТРОКЕ')))
    sr, sr_ok = numbers(d, 'sr', 16)
    sd, sd_ok = numbers(d, 'sd', 10)
    bd, bd_ok = numbers(d, 'bd', 16)
    ce, ce_ok = numbers(d, 'ce', 16)
    for key, ok in (('sr', sr_ok), ('sd', sd_ok), ('bd', bd_ok), ('ce', ce_ok)):
        if not ok:
            print('  поле %s= отсутствует/испорчено -> эта часть разбора пропущена' % key)
    if missing:
        print('  ВНИМАНИЕ: строка УСЕЧЕНА, нет полей: %s' % ', '.join(missing))
        print('           вывод неполный: цитата в документе не заменяет дамп — '
              'нужен повторный захват `breakdiag` в файл')
    names = ('TIM1', 'TIM8')
    contra = 0
    for i in range(min(2, len(bd))):
        print('  %s:' % names[i])
        if i < len(sr):
            print('    SR=0x%X: %s' % (sr[i], flags(sr[i], SR)))
        if i < len(sd):
            print('    SD/EM_STOP при захвате: %s (%s)'
                  % (sd[i], 'линия ЗДОРОВА (высокий)' if sd[i] == 1 else 'линия в fault (низкий)'))
        if i < len(ce):
            print('    CCER=0x%08X: %s' % (ce[i], flags(ce[i], CCER)))
        forb = check_bdtr(bd[i], names[i])
        if forb:
            contra += 1
            print('      ПРОТИВОРЕЧИЕ С ПРОШИВКОЙ: bd=0x%X содержит %s, но pwm.c:38 запрещает его,'
                  % (bd[i], '/'.join(forb)))
            print('      а pwm_bdtr_healthy() (pwm.c:159-161) отверг бы такое состояние ->')
            print('      этот дамп НЕ может описывать момент успешного пуска. Нужен повторный захват.')
    if len(bd) >= 2 and bd[0] == bd[1]:
        print('  Примечание: BDTR обоих таймеров идентичны (0x%X) — ожидаемо для симметричной схемы.'
              % bd[0])
    if contra:
        return RC_CONTRA, missing
    if missing or not bd_ok:
        return RC_TRUNCATED, missing
    return RC_OK, missing


SELFTEST = [
    # (имя, строка, ожидаемый код возврата)
    ('полный дамп, запрещённый BKP=1 (спорный отчёт)',
     '@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0:cap=0,0,0',
     RC_CONTRA),
    ('полный дамп, штатное состояние (BKE|OSSR|OSSI, MOE=1)',
     '@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=81,81:sd=1,1:bd=9600,9600:ce=5555,5555:cnt=0,0:cap=0,0,0',
     RC_OK),
    ('нет событий', '@BRK:valid=0', RC_OK),
    ('УСЕЧЁННАЯ строка из README_RETURN_PC3.md (нет cyc/ce/cnt/cap)',
     '@BRK:valid=1:seq=1:src=TIM1:…:sr=81,81:sd=1,1:bd=1CC0,1CC0', RC_CONTRA),
    ('усечённая строка без bd (сверять нечего)',
     '@BRK:valid=1:seq=2:src=TIM8:cyc=5:sr=81,81:sd=1,1', RC_TRUNCATED),
    ('вход без @BRK', 'hello world', RC_NO_INPUT),
]


def _cp1251_state(text):
    try:
        text.encode('cp1251')
    except UnicodeEncodeError as exc:
        return 'cp1251 ПРОВАЛ: %s' % repr(exc.object[exc.start:exc.end])
    return 'cp1251 OK'


def selftest():
    """Проверяет коды возврата на полной/усечённой строке и печатаемость вывода в cp1251."""
    import io
    bad = 0
    for name, line, want_rc in SELFTEST:
        print('  --- %s ---' % name)
        d = parse(line)
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            rc, _missing = show(d) if d is not None else (RC_NO_INPUT, ['@BRK'])
        finally:
            sys.stdout = old
        out = buf.getvalue()
        enc = _cp1251_state(out)
        for ln in out.splitlines():
            print('  | %s' % ln)
        ok = (rc == want_rc) and enc.endswith('OK')
        bad += not ok
        print('    ожидался rc=%d, получен %d -> %s; %s'
              % (want_rc, rc, 'OK' if ok else 'ПРОВАЛ', enc))
    print('  самотест: %d проверок, %d провалов' % (len(SELFTEST), bad))
    return bad


if __name__ == '__main__':
    force_safe_stdout()
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        sys.exit(1 if selftest() else 0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(RC_NO_INPUT)
    text = open(sys.argv[1], encoding='utf-8', errors='replace').read()
    parsed = parse(text)
    if parsed is None:
        print('  в файле нет строки @BRK: — разбирать нечего')
        sys.exit(RC_NO_INPUT)
    rc, _missing = show(parsed)
    sys.exit(rc)
