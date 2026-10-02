#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Определение конвенции знака энкодера по захваченному потоку `enc`.

Использование:
    py -3 enc_sign.py <файл с строками @ENC:...>       # разбор
    py -3 enc_sign.py --selftest                       # самотест (железо не нужно)

Формат строки прошивки (src/cli.c:344):
    @ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u
`angle` — 14 бит (16384 = 1 оборот мех.), поэтому при разборе учитывается переполнение.

Консоль Windows по умолчанию — cp1251: символы вне этой кодовой страницы (например
знак дельты) роняли инструмент `UnicodeEncodeError` ещё до вывода результата. Здесь
весь вывод состоит из символов cp1251, плюс стоит страховка errors='replace' —
самотест проверяет и то, и другое.
"""
import re
import sys

WIND = 16384
PAT = (r'@ENC:angle=(\d+):speed=(-?\d+):period_us=(\d+):pulse_us=(\d+):err=(\d+)')


def force_safe_stdout():
    """Не дать консоли (cp1251 / ascii / 866) уронить разбор: незаменяемый символ -> '?'."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def parse(text):
    out = []
    for m in re.finditer(PAT, text):
        out.append(dict(angle=int(m.group(1)), speed=int(m.group(2)),
                        period=int(m.group(3)), pulse=int(m.group(4)), err=int(m.group(5))))
    return out


def unwrap(samples):
    """Разворачивает 14-битный угол в монотонную последовательность (для дельты)."""
    if len(samples) < 2:
        return 0
    acc, prev = 0, samples[0]['angle']
    for s in samples[1:]:
        d = s['angle'] - prev
        if d > WIND // 2:
            d -= WIND
        elif d < -WIND // 2:
            d += WIND
        acc += d
        prev = s['angle']
    return acc


def format_report(samples):
    """Текст отчёта о знаке (весь вывод — только символы cp1251)."""
    s = samples
    if len(s) < 2:
        return 2, ['  строк @ENC найдено: %d — мало для вывода о знаке' % len(s)]
    errs = [x for x in s if x['err'] != 0]
    delta = unwrap(s)
    speeds = [x['speed'] for x in s]
    turns = delta / float(WIND)
    lines = ['  строк @ENC: %d; err!=0: %d' % (len(s), len(errs)),
             '  dangle: %+d отсчётов = %+.4f оборота мех.' % (delta, turns),
             '  speed: %+d .. %+d rpm' % (min(speeds), max(speeds))]
    if errs:
        lines.append('  ВНИМАНИЕ: есть строки с err!=0 -> канал невалиден, '
                     'вывод о знаке недействителен')
        return 3, lines
    if abs(delta) < 8:
        lines.append('  ВЫВОД: смещения нет (< 8 отсчётов) — вал не двигали или он не вращался')
        return 4, lines
    sign = 'ПЛЮС (angle растёт)' if delta > 0 else 'МИНУС (angle убывает)'
    lines.append('  ВЫВОД: вращение в этом направлении даёт %s' % sign)
    lines.append('  Запишите в отчёт: «направление X (какое именно) -> %s, speed %s»'
                 % (sign, 'положительный' if max(speeds) > 0 else 'отрицательный'))
    return 0, lines


def main(text):
    rc, lines = format_report(parse(text))
    for line in lines:
        print(line)
    return rc


SELFTEST = [
    # (имя, [(angle14, speed)], ожидаемый знак, минимальный |dangle|)
    ('плюс, пол-оборота', [(0, 0)] + [(200 * i, 12) for i in range(1, 41)], '+', 100),
    # «минус»: angle печатается как %u, поэтому убывание выглядит как счёт вниз по 14-битной шкале
    ('минус, пол-оборота', [(16000, 0)] + [(16000 - 200 * i, -12) for i in range(1, 41)], '-', 100),
    ('переполнение вверх', [(16000, 10), (16300, 10), (100, 10), (400, 10)], '+', 100),
]


def _cp1251_state(text):
    try:
        text.encode('cp1251')
    except UnicodeEncodeError as exc:
        return 'cp1251 ПРОВАЛ: %s' % repr(exc.object[exc.start:exc.end])
    return 'cp1251 OK'


def selftest():
    """Проверяет разбор, знак и то, что весь вывод печатается на консоли cp1251."""
    import io
    bad = 0
    cases = [(name, '\n'.join('@ENC:angle=%d:speed=%d:period_us=1000:pulse_us=500:err=0' % p
                              for p in pairs), want, minabs, 0)
             for name, pairs, want, minabs in SELFTEST]
    cases.append(('пустой вход (нет @ENC)', 'нет строк', '+', 0, 2))
    for name, txt, want, minabs, want_rc in cases:
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            rc = main(txt)
        finally:
            sys.stdout = old
        out = buf.getvalue()
        delta = unwrap(parse(txt))
        got = '+' if delta > 0 else '-'
        enc = _cp1251_state(out)
        ok = (rc == want_rc) and enc.endswith('OK')
        if want_rc == 0:
            ok = ok and (got == want) and abs(delta) >= minabs
        bad += not ok
        print('  %-22s строк=%-3d d=%+8d  знак %s  ожидалось %s (|d|>=%d)  rc=%d  %s  %s'
              % (name, len(parse(txt)), delta, got, want, minabs, rc,
                 'OK' if ok else 'ПРОВАЛ', enc))
    print('  самотест: %d проверок, %d провалов' % (len(cases), bad))
    return bad


if __name__ == '__main__':
    force_safe_stdout()
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        sys.exit(1 if selftest() else 0)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(open(sys.argv[1], encoding='utf-8', errors='replace').read()))
