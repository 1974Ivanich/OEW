#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Самопроверка пакета FOC 48-ячеечного скана (ред. 2).

Проверяются свойства пакета, а не «файлы на месте»:
  1) состав (все обязательные файлы) и SHA256SUMS без пропусков и лишних строк;
  2) байтовая гигиена: в файлах нет CRLF (иначе хеши пакета зависят от core.autocrlf);
  3) шкала метрики: calc_expected.py на числах платы -> f_pwm=5000, f_JEOS=5000, N=10000;
  4) охранник шкалы: TCLK вместо CLK (дефект v1) -> rc != 0 (шкала не может «молча» уехать);
  5) инвариант драйвера: --dry-run не содержит `f`, но содержит `i=` строго после `1`.

Вывод ASCII-only (консоль оператора cp1251/ascii не должна падать).
Коды возврата: 0 — PASS, 1 — FAIL, 2 — ошибка прогона.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEED = ['README.md', 'START_HERE_PC3.md', 'COMMANDS.md', 'FOC_SCAN_48CELL_PROTOCOL.md',
        'IDENTITY_AND_BASELINE.md', 'RETURN_TEMPLATE.md', 'CORRECTIONS_v2.md',
        'TOOLS/scan_48cell.py', 'TOOLS/calc_expected.py', 'TOOLS/check_package.py']
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
    print('')
    print('TOTAL: %s' % ('PASS' if bad == 0 else 'FAIL (%d)' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
