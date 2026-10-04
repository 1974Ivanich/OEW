#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Самопроверка пакета FOC 48-ячеечного скана (ред. 2).

Проверяются свойства пакета, а не «файлы на месте»:
  1) состав (все обязательные файлы) и SHA256SUMS без пропусков и лишних строк;
  2) байтовая гигиена: в файлах нет CRLF (иначе хеши пакета зависят от core.autocrlf);
  3) шкала метрики: calc_expected.py на числах платы -> f_pwm=5000, f_JEOS=5000, N=10000;
  4) охранник шкалы: TCLK вместо CLK (дефект v1) -> rc != 0 (шкала не может «молча» уехать);
  5) инвариант драйвера: --dry-run не содержит `f`, но содержит `i=` строго после `1`;
  6) атрибуция шага 0: строку `@PWM:FULL` печатает `pdump` (не `p?`), `p?` не отвечает
     за `SYS`/`PSC`/`ARR`, радиксы `CR1` (hex в `dump`/`pdump`, dec в `p?`) указаны;
  7) словарь команд: `eangle` — поле `@VFLOG` (V/f), не способ читать энкодер (для этого
     в образе есть `enc` → `@ENC:angle=…`); `rev`/`cv` упоминаются только как отсутствующие;
  8) шаг 0 — вердикт, а не глаза: `TOOLS/check_step0.py --selftest` (1 положительный + 6
     негативных случаев) проходит, пустой вход даёт ERROR (не PASS), доки называют инструмент;
  9) имена полей шага 0: в `@PWM:FULL` префикс группы только у первого поля (`T1:PSC=…`,
     `T8:PSC=…`), токены `T1:ARR`/`T1:CR1` в выводе образа отсутствуют (дефект B14).
 10) коды гейтов старта (B16/B17): `rc=-6` назван как `params_out_of_range`, `rc=-2` — как
     `map_unverified`; в доках есть состояние карты (`map_id`/`map_crc32`), путь её получения
     (`mapcap build=`) и строгий порядок снятия аппаратного break (`c`, затем `f`).

Вывод ASCII-only (консоль оператора cp1251/ascii не должна падать).
Коды возврата: 0 — PASS, 1 — FAIL, 2 — ошибка прогона.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEED = ['README.md', 'START_HERE_PC3.md', 'COMMANDS.md', 'FOC_SCAN_48CELL_PROTOCOL.md',
        'IDENTITY_AND_BASELINE.md', 'RETURN_TEMPLATE.md', 'CORRECTIONS_v2.md',
        'TOOLS/scan_48cell.py', 'TOOLS/calc_expected.py', 'TOOLS/check_package.py',
        'TOOLS/check_step0.py']
TEXT_EXT = ('.md', '.py')
SCALE = ['--clk', '170000000', '--psc', '16', '--arr', '999', '--cms', '3',
         '--rcr', '1', '--tclk', '10000000', '--window', '2.0']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for blk in iter(lambda: fh.read(65536), b''):
            h.update(blk)
    return h.hexdigest()


def run_tool(path, args):
    proc = subprocess.run([sys.executable, path] + args, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


def check_composition():
    bad = 0
    for rel in NEED:
        ok = os.path.exists(os.path.join(ROOT, rel))
        print('%-34s %s' % (rel, 'present' if ok else 'MISSING'))
        bad += 0 if ok else 1
    return bad


def check_manifest():
    sums = os.path.join(ROOT, 'SHA256SUMS')
    if not os.path.exists(sums):
        print('SHA256SUMS missing')
        return 1
    listed = []
    bad = 0
    with open(sums, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                print('manifest line malformed: %r' % line)
                bad += 1
                continue
            want, rel = parts[0], parts[1].strip().lstrip('*')
            listed.append(rel)
            path = os.path.join(ROOT, rel.replace('/', os.sep))
            got = sha(path) if os.path.exists(path) else 'NO FILE'
            ok = got == want
            print('sha %-30s %s' % (rel, 'OK' if ok else 'MISMATCH'))
            bad += 0 if ok else 1
    missing = [r for r in NEED if r not in listed]
    extra = [r for r in listed if r not in NEED]
    if missing:
        print('manifest does not cover: %s' % ', '.join(missing))
        bad += 1
    if extra:
        print('manifest lists unexpected files: %s' % ', '.join(extra))
        bad += 1
    return bad


def check_eol():
    bad = 0
    for rel in NEED:
        if not rel.endswith(TEXT_EXT):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as fh:
            data = fh.read()
        if b'\r\n' in data:
            print('%-34s CRLF FOUND (hashes would drift via core.autocrlf)' % rel)
            bad += 1
        if data.startswith(b'\xef\xbb\xbf'):
            print('%-34s UTF-8 BOM found' % rel)
            bad += 1
    return bad


def check_scale():
    path = os.path.join(ROOT, 'TOOLS', 'calc_expected.py')
    bad = 0
    rc, out = run_tool(path, SCALE)
    expect = ['f_pwm      = 5000.000 Hz', 'f_JEOS     = 5000.000 Hz',
              'N_expected = 10000', 'RESULT     : PASS']
    ok = rc == 0 and all(s in out for s in expect)
    print('scale on board numbers (CLK=170e6 PSC=16 ARR=999 CMS=3 RCR=1): %s'
          % ('OK rc=0 5000/5000/10000' if ok else 'FAIL rc=%d' % rc))
    if not ok:
        bad += 1
        print(out.rstrip())
    rc2, out2 = run_tool(path, ['--clk', '10000000', '--psc', '16', '--arr', '999', '--cms', '3',
                                '--rcr', '1', '--window', '2.0'])
    ok2 = rc2 != 0 and 'MISMATCH' in out2
    print('scale guard against the v1 defect (TCLK passed as CLK): %s'
          % ('OK rc=%d (rejected)' % rc2 if ok2 else 'FAIL rc=%d (accepted!)' % rc2))
    if not ok2:
        bad += 1
        print(out2.rstrip())
    return bad


def check_driver():
    path = os.path.join(ROOT, 'TOOLS', 'scan_48cell.py')
    rc, out = run_tool(path, ['--port', 'COM4', '--amp', '2000,3000,4000,5000',
                              '--angles-el', '0', '--n-expected', '10000', '--dry-run'])
    bad = 0
    if rc != 0:
        print('dry-run rc=%d' % rc)
        bad += 1
    if '"f" among them: 0' not in out:
        print('dry-run does not prove the "f" invariant')
        bad += 1
    if 'i=2000,0' not in out:
        print('dry-run plan does not contain the i= command (v1 never sent it)')
        bad += 1
    i1 = out.find('send: 1')
    i2 = out.find('send: i=')
    if not (0 <= i1 < i2):
        print('i= is not planned after 1 (FOC_Init would overwrite the refs)')
        bad += 1
    print('driver dry-run invariants (no f; i= after 1): %s' % ('OK' if bad == 0 else 'FAIL'))
    return bad


FULL_OWNER = 'pdump'   # `@PWM:FULL:…` печатает `pdump` (src/cli.c), а не `p?`
NEG = ('не печатает', 'не отвечает', 'ни SYS', 'ни `SYS`', 'НЕ `p?`', '**нет**', 'не искать',
       'десятичн')
RADIX = ('CR1=224', '0xE0')   # dec-вид из `p?` и hex-вид из `dump`/`pdump`


def check_step0_attribution():
    """Шаг 0: `@PWM:FULL` приписан `pdump`; `p?` — только CR1/CCER/BDTR/CNT (десятичные).

    Дефект ред. 2.0: строка `@PWM:FULL…` была приписана `p?`, который печатает лишь
    `@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu` (десятичные; ни SYS, ни PSC, ни ARR).
    """
    bad = 0
    docs = []
    for rel in NEED:
        if not rel.endswith('.md'):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        docs.append('\n'.join(lines))
        for i, line in enumerate(lines):
            if '@PWM:FULL' in line:
                ctx = ' '.join(lines[max(0, i - 2):i + 1])
                if FULL_OWNER not in ctx:
                    print('%s:%d: @PWM:FULL without %s nearby (that line is printed by pdump)'
                          % (rel, i + 1, FULL_OWNER))
                    bad += 1
            if 'p?' in line and any(k in line for k in ('SYS', 'PSC', 'ARR')):
                if not any(n in line for n in NEG):
                    print('%s:%d: p? next to SYS/PSC/ARR without a "does not print" caveat'
                          % (rel, i + 1))
                    bad += 1
    blob = '\n'.join(docs)
    for want in RADIX:
        if want not in blob:
            print('docs do not pin both CR1 radices: %r missing' % want)
            bad += 1
    print('step 0 attribution (pdump owns @PWM:FULL; p? = CR1/CCER/BDTR/CNT, dec): %s'
          % ('OK' if bad == 0 else 'FAIL'))
    return bad


STEP0_TOOL = 'TOOLS/check_step0.py'
STEP0_DOCS = ('README.md', 'START_HERE_PC3.md', 'COMMANDS.md', 'FOC_SCAN_48CELL_PROTOCOL.md',
              'RETURN_TEMPLATE.md')
FIELD_TRAP = ('T1:ARR', 'T1:CR1', 'T1:CCER', 'T1:BDTR', 'T1:CNT',
              'T8:ARR', 'T8:CR1', 'T8:CCER', 'T8:BDTR', 'T8:CNT')
FIELD_TRAP_CAVEAT = ('НЕТ', 'нет', 'не ', 'без префикса', 'групп')


def check_step0_machine_verdict():
    """Шаг 0 обязан иметь вердикт из софта, а не «посмотреть глазами» (дефект B13)."""
    bad = 0
    path = os.path.join(ROOT, 'TOOLS', 'check_step0.py')
    if not os.path.exists(path):
        print('step 0 has no machine verdict tool (%s missing)' % STEP0_TOOL)
        return 1
    rc, out = run_tool(path, ['--selftest'])
    ok = rc == 0 and 'SELFTEST: PASS' in out
    print('step 0 tool selftest (1 positive + 6 negatives): %s'
          % ('OK rc=0' if ok else 'FAIL rc=%d' % rc))
    if not ok:
        bad += 1
        print(out.rstrip())
    tmp = tempfile.mkdtemp(prefix='step0probe')
    try:
        log = os.path.join(tmp, 'empty.log')
        with open(log, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('terminal opened, nothing received\n> ')
        rc2, out2 = run_tool(path, ['--log', log, '--out', os.path.join(tmp, 'out')])
        ok2 = rc2 == 2 and 'ERROR' in out2 and 'verdict   : PASS' not in out2
        print('step 0 without telemetry is ERROR, not PASS: %s'
              % ('OK rc=2' if ok2 else 'FAIL rc=%d' % rc2))
        if not ok2:
            bad += 1
            print(out2.rstrip())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for rel in STEP0_DOCS:
        doc = os.path.join(ROOT, rel)
        if not os.path.exists(doc):
            continue
        with open(doc, encoding='utf-8') as fh:
            text = fh.read()
        if 'check_step0.py' not in text:
            print('%s: step 0 is still described without a machine verdict (name %s)'
                  % (rel, 'check_step0.py'))
            bad += 1
    print('step 0 machine verdict (selftest + no-telemetry->ERROR + named in docs): %s'
          % ('OK' if bad == 0 else 'FAIL'))
    return bad


def check_step0_field_names():
    """B14: в `@PWM:FULL` префикс группы стоит только у первого поля (`T1:PSC`/`T8:PSC`)."""
    bad = 0
    blob = ''
    for rel in NEED:
        if not rel.endswith('.md'):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        blob += '\n'.join(lines) + '\n'
        for i, line in enumerate(lines):
            for tok in FIELD_TRAP:
                if tok in line and not any(c in line for c in FIELD_TRAP_CAVEAT):
                    print('%s:%d: %s is not a field of the image line (the group prefix is on the '
                          'first field only: `T1:PSC=..:ARR=..`; `T1:ARR` does not exist)'
                          % (rel, i + 1, tok))
                    bad += 1
    for want in ('T1:PSC=', 'T8:PSC=', 'ARR=999'):
        if want not in blob:
            print('docs do not pin the real field form %r of @PWM:FULL' % want)
            bad += 1
    print('step 0 field names (no T1:ARR/T1:CR1; group prefix on PSC only): %s'
          % ('OK' if bad == 0 else 'FAIL'))
    return bad


EANGLE_CAVEAT = ('@VFLOG', 'vflog', 'V/f', 'не команда', 'вместо', 'не помощник')

DEAD_CMD = ('`rev`', '`cv`')                          # в образе этих команд нет
DEAD_CAVEAT = 'нет'                                   # ...и доки обязаны это писать
ENC_OWNER = ('`enc`', '@ENC')                         # реальное чтение энкодера

# B16/B17: ответ на `1` — это последовательность гейтов, а не только `FOC started`/`rc=-5`.
GATES = (('rc=-6', 'params_out_of_range',
          'B16: это гейт параметров Rs/Ls, а не "не energize-сборка"'),
         ('rc=-2', 'map_unverified', 'B17: это гейт карты реконструкции'))
GATE_PINS = (('rc=-6', 'код гейта параметров (B16)'),
             ('params_out_of_range', 'имя гейта параметров (B16)'),
             ('rc=-2', 'код гейта карты (B17)'),
             ('map_crc32', 'состояние карты в `@FOC` (B17)'),
             ('mapcap build=', 'единственный путь получить карту в этом образе (B17)'))
BREAK_ORDER_DOCS = ('START_HERE_PC3.md', 'CORRECTIONS_v2.md')   # где порядок обязателен
BREAK_ORDER_HINT = ('сначала', 'затем', 'потом', '→', '->', 'порядок')


def check_command_vocabulary():
    """Словарь команд: доки называют только то, что есть в образе (находка B12)."""
    bad = 0
    enc_docs = ('FOC_SCAN_48CELL_PROTOCOL.md', 'START_HERE_PC3.md')
    for rel in NEED:
        if not rel.endswith('.md'):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            if 'eangle' in line and not any(c in line for c in EANGLE_CAVEAT):
                print('%s:%d: `eangle` used without the @VFLOG caveat (it is not an encoder '
                      'command; the image has `enc`)' % (rel, i + 1))
                bad += 1
            for dead in DEAD_CMD:
                if dead in line and DEAD_CAVEAT not in line:
                    print('%s:%d: %s without "нет" (the image has no such command)'
                          % (rel, i + 1, dead))
                    bad += 1
        if rel in enc_docs:
            text = '\n'.join(lines)
            for want in ENC_OWNER:
                if want not in text:
                    print('%s: real encoder readout %r is not named' % (rel, want))
                    bad += 1
    print('command vocabulary (enc/@ENC named; eangle only as @VFLOG; rev/cv only as absent): %s'
          % ('OK' if bad == 0 else 'FAIL'))
    return bad


def check_start_gate_codes():
    """B16/B17: ответ на `1` — это гейты; коды и порядок снятия break обязаны быть в доках."""
    bad = 0
    blob = ''
    for rel in NEED:
        if not rel.endswith('.md'):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            text = '\n'.join(fh.read().splitlines())
        blob += text + '\n'
        for code, name, tag in GATES:
            if code in text and name not in text:
                print('%s: mentions %s but never names it as %s (%s)'
                      % (rel, code, name, tag))
                bad += 1
    for want, why in GATE_PINS:
        if want not in blob:
            print('docs do not pin %r: %s' % (want, why))
            bad += 1
    for rel in BREAK_ORDER_DOCS:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        if not any('`c`' in ln and '`f`' in ln
                   and any(h in ln for h in BREAK_ORDER_HINT) for ln in lines):
            print('%s: break recovery order (`c` first, then `f`) is not described (B17)' % rel)
            bad += 1
    print('start gate codes (rc=-6 params / rc=-2 map; map state; c before f): %s'
          % ('OK' if bad == 0 else 'FAIL'))
    return bad


def main():
    bad = 0
    print('--- composition ---')
    bad += check_composition()
    print('--- manifest ---')
    bad += check_manifest()
    print('--- bytes ---')
    bad += check_eol()
    print('--- metric scale ---')
    bad += check_scale()
    print('--- driver invariants ---')
    bad += check_driver()
    print('--- step 0 attribution ---')
    bad += check_step0_attribution()
    print('--- step 0 machine verdict ---')
    bad += check_step0_machine_verdict()
    print('--- step 0 field names ---')
    bad += check_step0_field_names()
    print('--- command vocabulary ---')
    bad += check_command_vocabulary()
    print('--- start gate codes ---')
    bad += check_start_gate_codes()
    print('')
    print('TOTAL: %s' % ('PASS' if bad == 0 else 'FAIL (%d)' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
