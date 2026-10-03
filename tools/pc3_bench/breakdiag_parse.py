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

# Позиции бит взяты ИЗ ЗАГОЛОВКА CMSIS, а не по памяти:
#   Drivers/CMSIS/Device/ST/STM32G4xx/Include/stm32g474xx.h
#   TIM_BDTR_LOCK_Pos=8, OSSI=10, OSSR=11, BKE=12, BKP=13, AOE=14, MOE=15
# Раскладка «OSSR=10, OSSI=9, BKP=11» (как в RM0440 для других серий) здесь НЕВЕРНА:
# на ней штатное состояние покоя 0x1CC0 читается как «BKP=1 и OSSI=0» — ложное «противоречие».
BDTR = [(15, 'MOE'), (14, 'AOE'), (13, 'BKP'), (12, 'BKE'), (11, 'OSSR'),
        (10, 'OSSI'), (8, 'LOCK')]
FORBIDDEN = {'BKP', 'BK2E', 'AOE'}
REQUIRED = {'BKE', 'OSSR', 'OSSI'}

# Профиль прошивки: какие биты BDTR обязательны, какие запрещены и чем грозит несоответствие.
#   production   — штатная защита: BKIN активен, BKE обязателен; ожидаемо 0x1CC0 покой / 0x9CC0 под током.
#   monitor-only — диагностический образ (OEW_SD_MONITOR_ONLY=1): BKIN выключен НАМЕРЕННО,
#                  BKE ОБЯЗАН быть 0; ожидаемо 0x0CC0 покой / 0x8CC0 под током.
# Профиль выбирается аргументом --profile; по умолчанию production.
PROFILES = {
    'production': {
        'required': {'BKE', 'OSSR', 'OSSI'},
        'forbidden': {'BKP', 'BK2E', 'AOE'},
        'consequence': 'pwm_bdtr_healthy() (pwm.c:159-161) отверг бы такое состояние, '
                       'и пуск не состоялся бы',
        'what': 'BKE=1 (BKIN активен), BKP/AOE=0',
    },
    'monitor-only': {
        'required': {'OSSR', 'OSSI'},
        'forbidden': {'BKE', 'BKP', 'BK2E', 'AOE'},
        'consequence': 'диагностический образ обязан иметь BKE=0 (pwm.c:275-277: pwm_break_bits=0), '
                       'иначе дамп описывает ДРУГОЙ образ — сверьте прошивку с IMAGE/PROVENANCE.txt',
        'what': 'BKE=0 (BKIN выключен намеренно), OSSR/OSSI=1, BKP/AOE=0',
    },
}
PROFILE = 'production'
SR = [(9, 'CC1OF'), (8, 'B2IF'), (7, 'BIF'), (6, 'TIF'), (5, 'COMIF'), (4, 'CC4IF'),
      (3, 'CC3IF'), (2, 'CC2IF'), (1, 'CC1IF'), (0, 'UIF')]   # TIM_SR_*_Pos из того же заголовка
CCER = [(15, 'CC4NP'), (14, 'CC4NE'), (13, 'CC4P'), (12, 'CC4E'), (11, 'CC3NP'),
        (10, 'CC3NE'), (9, 'CC3P'), (8, 'CC3E'), (7, 'CC2NP'), (6, 'CC2NE'), (5, 'CC2P'),
        (4, 'CC2E'), (3, 'CC1NP'), (2, 'CC1NE'), (1, 'CC1P'), (0, 'CC1E')]
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
    """Печатает разбор BDTR для ТЕКУЩЕГО профиля. Возвращает (отсутствующие, запрещённые)."""
    prof = PROFILES[PROFILE]
    f = bdtr_bits(val)
    miss = [n for n in prof['required'] if not f.get(n)]
    forb = [n for n in prof['forbidden'] if f.get(n)]
    print('    %s BDTR=0x%08X: %s' % (who, val, flags(val, BDTR)))
    if miss:
        print('      (x) не установлено обязательное: %s' % ', '.join(miss))
    if forb:
        print('      (x) УСТАНОВЛЕНО ЗАПРЕЩЁННОЕ: %s' % ', '.join(forb))
        print('        -> такой BDTR был бы отвергнут PWM_HardwareInterlockHealthy(), '
              'пуск не состоялся бы')
    if not miss and not forb:
        print('      OK: соответствует профилю %s (%s)' % (PROFILE, prof['what']))
    return miss, forb


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
        miss_req, forb = check_bdtr(bd[i], names[i])
        # Нарушением считается И запрещённый бит, И отсутствие обязательного:
        # раньше отсутствие BKE/OSSR/OSSI только печаталось, но код возврата оставался 0.
        if forb or miss_req:
            contra += 1
            if forb:
                print('      ПРОТИВОРЕЧИЕ С ПРОФИЛЕМ %s: bd=0x%X содержит %s — для этого профиля запрещено'
                      % (PROFILE, bd[i], '/'.join(forb)))
            else:
                print('      ПРОТИВОРЕЧИЕ С ПРОФИЛЕМ %s: bd=0x%X не содержит обязательного %s'
                      % (PROFILE, bd[i], '/'.join(miss_req)))
            print('      %s.' % PROFILES[PROFILE]['consequence'])
            print('      Нужен повторный захват (или проверка профиля: --profile monitor-only).')
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
    ('штатный ПОКОЙ, снято с платы 26.09: 0x1CC0 (MOE=0, BKE|OSSR|OSSI=1)',
     '@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0:cap=0,0,0',
     RC_OK),
    ('штатное ПОД ТОКОМ, снято с платы 26.09: 0x9CC0 (MOE=1)',
     '@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=81,81:sd=1,1:bd=9CC0,9CC0:ce=555,555:cnt=0,0:cap=0,0,0',
     RC_OK),
    ('НАСТОЯЩЕЕ нарушение: BKP=1 (бит 13) — неверная полярность входа break',
     '@BRK:valid=1:seq=3:src=TIM1:cyc=0:sr=81,81:sd=0,1:bd=BCC0,BCC0:ce=555,555:cnt=0,0:cap=0,0,0',
     RC_CONTRA),
    ('НАСТОЯЩЕЕ нарушение: BKE=0 — break выключен, защита не сработает',
     '@BRK:valid=1:seq=4:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=8CC0,8CC0:ce=555,555:cnt=0,0:cap=0,0,0',
     RC_CONTRA),
    ('нет событий', '@BRK:valid=0', RC_OK),
    ('УСЕЧЁННАЯ строка (нет cyc/ce/cnt/cap) при ЗДОРОВОМ bd — неполный вывод, не «противоречие»',
     '@BRK:valid=1:seq=1:src=TIM1:…:sr=81,81:sd=1,1:bd=1CC0,1CC0', RC_TRUNCATED),
    ('усечённая строка без bd (сверять нечего)',
     '@BRK:valid=1:seq=2:src=TIM8:cyc=5:sr=81,81:sd=1,1', RC_TRUNCATED),
    ('вход без @BRK', 'hello world', RC_NO_INPUT),
]

# Второй набор — диагностический образ (monitor-only). Проверяется ОТДЕЛЬНО, с --profile.
SELFTEST_MONITOR_ONLY = [
    ('диагностический образ, ПОКОЙ: 0x0CC0 (BKE снят намеренно, OSSR|OSSI=1)',
     '@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=0,0:sd=1,1:bd=0CC0,0CC0:ce=0,0:cnt=0,0:cap=0,0,0',
     RC_OK),
    ('диагностический образ, ПОД ТОКОМ: 0x8CC0 (MOE=1)',
     '@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=0,0:sd=1,1:bd=8CC0,8CC0:ce=555,555:cnt=0,0:cap=0,0,0',
     RC_OK),
    ('подсунули штатный BDTR 0x1CC0 под профиль monitor-only: это ДРУГОЙ образ, не противоречие прошивке',
     '@BRK:valid=1:seq=3:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=555,555:cnt=0,0:cap=0,0,0',
     RC_CONTRA),
]


def _cp1251_state(text):
    try:
        text.encode('cp1251')
    except UnicodeEncodeError as exc:
        return 'cp1251 ПРОВАЛ: %s' % repr(exc.object[exc.start:exc.end])
    return 'cp1251 OK'


def selftest():
    """Коды возврата на полной/усечённой строке, печатаемость в cp1251, оба профиля."""
    import io
    global PROFILE
    bad = 0
    cases = [(PROFILE, c) for c in SELFTEST] + [('monitor-only', c) for c in SELFTEST_MONITOR_ONLY]
    for prof, (name, line, want_rc) in cases:
        PROFILE = prof
        name = '[%s] %s' % (prof, name)
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
    PROFILE = 'production'
    print('  самотест: %d проверок (%d + %d профилей), %d провалов'
          % (len(SELFTEST) + len(SELFTEST_MONITOR_ONLY), len(SELFTEST),
             len(SELFTEST_MONITOR_ONLY), bad))
    return bad


def _take_profile(argv):
    """Выдёргивает --profile <имя> из аргументов; возвращает остаток."""
    global PROFILE
    if '--profile' in argv:
        i = argv.index('--profile')
        val = argv[i + 1] if i + 1 < len(argv) else ''
        if val not in PROFILES:
            print('  неизвестный профиль %r; доступны: %s' % (val, ', '.join(sorted(PROFILES))))
            sys.exit(RC_NO_INPUT)
        PROFILE = val
        del argv[i:i + 2]
    return argv


if __name__ == '__main__':
    force_safe_stdout()
    argv = _take_profile(sys.argv[1:])
    if '--selftest' in argv:
        sys.exit(1 if selftest() else 0)
    if not argv:
        print(__doc__)
        sys.exit(RC_NO_INPUT)
    text = open(argv[0], encoding='utf-8', errors='replace').read()
    parsed = parse(text)
    if parsed is None:
        print('  в файле нет строки @BRK: — разбирать нечего')
        sys.exit(RC_NO_INPUT)
    rc, _missing = show(parsed)
    sys.exit(rc)
