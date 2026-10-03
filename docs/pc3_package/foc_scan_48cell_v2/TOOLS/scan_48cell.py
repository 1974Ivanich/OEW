#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""48-ячеечный скан FOC: bookkeeping по ячейкам.

Порядок ячейки (ред. 2; отличается от v1 — добавлен шаг `i=`):
  1) sysinfo                        — JEOS/OVR "до"
  2) 1                              — дословный ответ ("FOC started")
  3) i=<Id_ma>,<Iq_ma>              — ПОСЛЕ `1` и ДО окна, см. ниже
  4) окно (только чтение, ничего не отправляется)
  5) 0
  6) sysinfo                        — JEOS/OVR "после"
  7) пост-мортем: dumpa, dump, p?, sysinfo, a?, breakdiag

Почему `i=` после `1`, а не до: первый `FOC_Start()` вызывает `FOC_Init()`,
который ставит `id_ref_ma = FOC_DEFAULT_ID_REF_MA` (2000 мА). Уставки переживают
повторные старты (сброса нет), поэтому `i=` обязан идти ПОСЛЕ первого `1`,
иначе он будет затёрт. Внутрь окна он не попадает: окно открывается после него.

`f` не отправляется никогда: при выключенном FOC `f` снимает латч и через
`ADC_CalibrateOffsets` сдвигает нули между ячейками (в v1 это не выполнялось —
дефект про `f_list.txt`, см. CORRECTIONS_v2.md).

Телеметрия читается только пассивно; ADC-команды (`a`, `c`, автотюн) внутри
окна не вызываются.

Живые поля берутся из `@FOC` (у v1 их брали из `sysinfo`, где их нет):
  Id_ref/Iq_ref — уставки в мА (что реально принял `i=`),
  FAULT/FAULT_R/FAIL/RUN/STATE/SPD/TH/CCR1..3/em_stop1/em_stop2 — состояние в окне.
`em_stopN` = уровень линии SD (1 = высокая = здоровая): 0 = авария -> стоп.

Весь вывод в консоль — ASCII (v1-комплекты ПК-3 отклоняли за UnicodeEncodeError
на cp1251/ascii-консоли).
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
import time

POST_MORTEM = ['dumpa', 'dump', 'p?', 'sysinfo', 'a?', 'breakdiag']

FOC_FIELDS = ('I1', 'I2', 'Ires', 'Id', 'Iq', 'Id_ref', 'Iq_ref', 'VBUS', 'STATE',
              'SPD', 'TH', 'sector', 'window', 'CCR1', 'CCR2', 'CCR3', 'ADC_STATUS',
              'FAULT', 'FAULT_R', 'FAIL', 'RUN', 'em_stop1', 'em_stop2')
SYS_FIELDS = ('CLK', 'PSC', 'TCLK', 'OVR', 'JEOS', 'TO', 'JQOVF', 'uart_drp', 'uart_trunc')

CSV_FIELDS = ('cell', 'amp_ma', 'angle_el', 'id_ma', 'iq_ma', 'start_reply', 'i_reply',
              't_window_s', 'jeos_before', 'jeos_after', 'd_jeos', 'n_expected', 'ratio',
              'level', 'ovr_before', 'ovr_after', 'd_ovr', 'state', 'run', 'fault', 'fault_r',
              'fail', 'em_stop1', 'em_stop2', 'spd', 'th', 'ccr1', 'ccr2', 'ccr3',
              'id', 'iq', 'id_ref', 'iq_ref', 'vbus', 'postmortem')

FOC_MAX_MA = 10000       # src/foc.c: FOC_I_MAX_MA — жёсткий кламп уставок `i=`


def _last_line(text, token):
    """Последняя строка, начинающаяся с `token` (без смешивания "до"/"после")."""
    lines = [ln for ln in text.replace('\r', '\n').split('\n') if ln.startswith(token)]
    return lines[-1] if lines else ''


def _ints(line, names):
    """Поля `NAME=<int>` из одной строки телеметрии (нижний регистр ключей)."""
    out = {}
    for nm in names:
        vals = re.findall(r'(?<![A-Za-z0-9_])%s=(-?\d+)' % re.escape(nm), line)
        if vals:
            out[nm.lower()] = int(vals[-1])
    return out


def parse_foc(text):
    return _ints(_last_line(text, '@FOC'), FOC_FIELDS)


def parse_sys(text):
    return _ints(_last_line(text, '@SYS'), SYS_FIELDS)


def flatten(text):
    return ' '.join((text or '').split())


def current_pair(amp_ma, angle_el_deg):
    """`i=<Id>,<Iq>` из амплитуды (мА) и электрического угла вектора тока."""
    rad = math.radians(float(angle_el_deg))
    return int(round(float(amp_ma) * math.cos(rad))), int(round(float(amp_ma) * math.sin(rad)))


def level_of(d_jeos, n_expected):
    """L0/L1/L2 по протоколу §4; без шкалы — 'unscaled'."""
    if d_jeos is None:
        return 'n/a'
    if d_jeos == 0:
        return 'L0'
    if d_jeos == 1:
        return 'L1'
    if n_expected and n_expected > 0:
        if abs(d_jeos - n_expected) <= 0.05 * n_expected:
            return 'L2'
        return 'unscaled'
    return 'L1+'


def decode_hint(ratio):
    """Подсказка кросс-чека декода по отношению dJEOS/N_expected (§2)."""
    if ratio is None:
        return ''
    if 0.45 <= ratio <= 0.55:
        return 'ratio ~ 0.5: decode is 2x too high (check RCR anchor and CMS bits)'
    if 1.8 <= ratio <= 2.2:
        return 'ratio ~ 2: decode is 2x too low (RCR=0 instead of 1?)'
    if 0.95 <= ratio <= 1.05:
        return 'ratio ~ 1: decode confirmed by data'
    return 'ratio %.2f: decode not confirmed by data' % ratio


class Link(object):
    """Реальный UART-линк: команды пишет только сессия, окно — только чтение."""

    def __init__(self, port, baud, eol):
        import serial  # pyserial
        self.eol = eol
        self.sent = []
        self.buf = ''
        self.ser = serial.Serial(port, baud, timeout=0.2)

    def write(self, cmd):
        self.sent.append(cmd)
        self.ser.write((cmd + self.eol).encode('ascii'))

    def pump(self, quiet=0.25, limit=5.0):
        """Читает, пока тихо `quiet` секунд (или пока идут данные)."""
        out = []
        t0 = time.time()
        while time.time() - t0 < limit:
            chunk = self.ser.read(4096)
            if chunk:
                out.append(chunk.decode('ascii', 'replace'))
                t0 = time.time()
            elif out:
                break
            time.sleep(0.02)
        txt = ''.join(out)
        self.buf += txt
        return txt

    def window(self, seconds):
        """Окно: ровно `seconds` секунд, ни одной отправки, только чтение."""
        got = []
        t_end = time.time() + seconds
        while time.time() < t_end:
            got.append(self.pump(quiet=0.05, limit=0.3))
        got.append(self.pump(quiet=0.2, limit=1.0))
        return ''.join(got)

    def close(self):
        if self.ser is not None:
            self.ser.close()


class DryLink(object):
    """`--dry-run`: порта нет, команды только записываются, ответов нет."""

    def __init__(self):
        self.eol = '\r\n'
        self.sent = []
        self.buf = ''

    def write(self, cmd):
        self.sent.append(cmd)

    def pump(self, quiet=0.25, limit=5.0):
        return ''

    def window(self, seconds):
        return ''

    def close(self):
        pass


class Session(object):
    """Порядок ячейки из docstring; без железа тестируется через DryLink/подставной линк."""

    def __init__(self, link, cells, out_dir, window=2.0, n_expected=0.0, post=None,
                 set_current=True, require_start=True, sd_stop=True):
        self.link = link
        self.cells = list(cells)
        self.out_dir = out_dir
        self.window = float(window)
        self.n_expected = float(n_expected or 0.0)
        self.post = list(POST_MORTEM if post is None else post)
        self.set_current = bool(set_current)
        self.require_start = bool(require_start)
        self.sd_stop = bool(sd_stop)
        self.rows = []
        self.abort = None
        self.cells_path = os.path.join(out_dir, 'cells.csv')
        self.raw_path = os.path.join(out_dir, 'session_raw.log')
        self.summary_path = os.path.join(out_dir, 'summary.txt')

    def run_cell(self, index, total, cell):
        rec = dict.fromkeys(CSV_FIELDS, '')
        rec['cell'] = cell['cell']
        rec['amp_ma'] = cell['amp_ma']
        rec['angle_el'] = cell['angle_el']
        rec['id_ma'] = cell['id_ma']
        rec['iq_ma'] = cell['iq_ma']
        print('[%d/%d] %s  (i=%d,%d mA)' % (index, total, rec['cell'], rec['id_ma'], rec['iq_ma']))

        self.link.write('sysinfo')
        before = parse_sys(self.link.pump())
        rec['jeos_before'] = before.get('jeos')
        rec['ovr_before'] = before.get('ovr')

        t0 = time.time()
        self.link.write('1')
        ans = self.link.pump(quiet=0.4, limit=3.0)
        rec['start_reply'] = flatten(ans)[:200]
        started = 'FOC started' in ans

        if self.set_current:
            self.link.write('i=%d,%d' % (rec['id_ma'], rec['iq_ma']))
            rec['i_reply'] = flatten(self.link.pump(quiet=0.3, limit=2.0))[:120]

        win_text = self.link.window(self.window)
        rec['t_window_s'] = round(time.time() - t0, 3)

        self.link.write('0')
        self.link.pump()
        self.link.write('sysinfo')
        after = parse_sys(self.link.pump())
        rec['jeos_after'] = after.get('jeos')
        rec['ovr_after'] = after.get('ovr')
        rec['d_jeos'] = _diff(rec['jeos_before'], rec['jeos_after'])
        rec['d_ovr'] = _diff(rec['ovr_before'], rec['ovr_after'])
        if self.n_expected > 0 and rec['d_jeos'] is not None:
            rec['ratio'] = round(rec['d_jeos'] / self.n_expected, 3)
        rec['n_expected'] = int(self.n_expected) if self.n_expected > 0 else ''
        rec['level'] = level_of(rec['d_jeos'], self.n_expected)

        live = parse_foc(win_text) or parse_foc(self.link.buf)
        for key, val in live.items():
            if key in CSV_FIELDS:
                rec[key] = val

        pm = []
        for cmd in self.post:
            self.link.write(cmd)
            pm.append('$ %s\n%s' % (cmd, self.link.pump()))
        rec['postmortem'] = ' | '.join(pm)[:2000]
        return rec, win_text, started

    def stop_reason(self, rec, started):
        sr = rec['start_reply'] or ''
        if self.require_start and not started:
            extra = ''
            if 'rc=-5' in sr:
                extra = ('  [rc=-5: PWM/interlock enable failed -> the board is likely NOT an '
                         'energize build (-DOEW_HS1_COMMISSIONING_RELEASE=1). Do not scan.]')
            elif '-7' in sr or '-8' in sr:
                extra = '  [note: -7/-8 are V/f start codes (vf=), never FOC start codes]'
            return 'FOC did not start (reply: %s)%s' % (sr, extra)
        if self.set_current and '@I:OK' not in (rec['i_reply'] or ''):
            return 'i= not acknowledged (reply: %s)' % (rec['i_reply'] or '<empty>')
        if (rec['fault'] or 0) != 0 or (rec['fault_r'] or 0) != 0:
            return 'FAULT latched (FAULT=%s FAULT_R=%s)' % (rec['fault'], rec['fault_r'])
        if self.sd_stop and (rec['em_stop1'] == 0 or rec['em_stop2'] == 0):
            return ('SD line asserted (em_stop1=%s em_stop2=%s; the field is line level HIGH, '
                    'so 0 = alarm)' % (rec['em_stop1'], rec['em_stop2']))
        return None


    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        total = len(self.cells)
        for i, cell in enumerate(self.cells, 1):
            rec, _win_text, started = self.run_cell(i, total, cell)
            self.rows.append(rec)
            with open(self.raw_path, 'a', encoding='utf-8') as fh:
                fh.write('\n===== %s (i=%d,%d mA) =====\n%s\n'
                         % (rec['cell'], rec['id_ma'], rec['iq_ma'], self.link.buf))
            self.link.buf = ''
            _write_rows(self.cells_path, self.rows)
            if rec['fail'] not in ('', None) and rec['fail'] != 0:
                print('    WARNING: FAIL=%s (FOC_GetStartupFailReason; recorded, not a stop)'
                      % rec['fail'])
            if rec['d_jeos'] is not None and rec['level'] == 'unscaled':
                print('    NOTE: dJEOS=%s vs N_expected=%s -> %s'
                      % (rec['d_jeos'], rec['n_expected'], decode_hint(rec['ratio'])))
            reason = self.stop_reason(rec, started)
            if reason:
                self.abort = 'cell %s: %s' % (rec['cell'], reason)
                print('    STOP: %s' % self.abort)
                break
        self._write_summary()
        return 1 if self.abort else 0

    def _write_summary(self):
        ratios = [r['ratio'] for r in self.rows if isinstance(r['ratio'], float)]
        med = sorted(ratios)[len(ratios) // 2] if ratios else None
        levels = {}
        for r in self.rows:
            levels[r['level']] = levels.get(r['level'], 0) + 1
        f_sent = [c for c in self.link.sent if c.strip() == 'f']
        lines = [
            'cells: %d of %d' % (len(self.rows), len(self.cells)),
            'levels: %s' % (', '.join('%s=%d' % kv for kv in sorted(levels.items())) or 'none'),
            'N_expected over window %.2f s: %s' % (self.window, self.n_expected or 'not given'),
            'median dJEOS/N_expected: %s' % ('%.3f' % med if med is not None else 'n/a'),
            'decode cross-check: %s' % (decode_hint(med) or 'no live ratio'),
            '"f" sent: %d' % len(f_sent),
            'abort: %s' % (self.abort or 'none'),
        ]
        text = '\n'.join(lines) + '\n'
        with open(self.summary_path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        with open(os.path.join(self.out_dir, 'f_list.txt'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(f_sent) + '\n')
        print('')
        sys.stdout.write(text)


def _diff(before, after):
    if before is None or after is None:
        return None
    return after - before


def _write_rows(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS), extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def plan_cells(amps, angles, set_current=True):
    """Сетка: сначала амплитуда (вал фиксирован), затем угол (протокол §3)."""
    cells = []
    for ang in angles:
        for amp in amps:
            id_ma, iq_ma = current_pair(amp, ang)
            cells.append(dict(cell='A%s_ANG%g' % (amp, ang), amp_ma=amp, angle_el=ang,
                              id_ma=id_ma if set_current else 0,
                              iq_ma=iq_ma if set_current else 0))
    return cells


def command_plan(cell, set_current=True, post=None, window=2.0):
    """План команд одной ячейки (для --dry-run и для отчёта)."""
    cmds = ['sysinfo', '1']
    if set_current:
        cmds.append('i=%d,%d' % (cell['id_ma'], cell['iq_ma']))
    cmds += ['<window %.2f s: read-only>' % window, '0', 'sysinfo']
    cmds += ['post-mortem: %s' % ', '.join(POST_MORTEM if post is None else post)]
    return cmds


def run_dry(cells, set_current, post, window):
    n = 0
    for i, cell in enumerate(cells, 1):
        print('[%d/%d] %s' % (i, len(cells), cell['cell']))
        for cmd in command_plan(cell, set_current, post, window):
            print('    send: %s' % cmd)
            n += 1
    print('')
    print('commands total: %d; "f" among them: %d' % (n, 0))
    print('dry run: nothing was sent, no files written')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='48-cell FOC scan bookkeeping (package rev. 2).')
    ap.add_argument('--port', default='COM4')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--eol', default='crlf', choices=['crlf', 'lf'])
    ap.add_argument('--amp', required=True,
                    help='current amplitude(s), mA, comma separated, low to high')
    ap.add_argument('--angles-el', default='0',
                    help='current vector angle(s), electrical degrees')
    ap.add_argument('--window', type=float, default=2.0)
    ap.add_argument('--n-expected', type=float, default=0.0,
                    help='N_expected from TOOLS/calc_expected.py for this window (required)')
    ap.add_argument('--out', default='run1')
    ap.add_argument('--postmortem', default=','.join(POST_MORTEM))
    ap.add_argument('--no-set-current', action='store_true',
                    help='do not send i= (amp/angle become labels only; say so in the return)')
    ap.add_argument('--allow-nonstart', action='store_true',
                    help='debug only: do not stop when the reply to 1 is not "FOC started"')
    ap.add_argument('--no-sd-stop', action='store_true',
                    help='bench waiver: do not stop when a SD line reads low (record it!)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    try:
        amps = [int(x) for x in a.amp.split(',') if x.strip()]
        angles = [float(x) for x in a.angles_el.split(',') if x.strip()]
    except ValueError:
        print('INPUT ERROR: --amp must be integers (mA), --angles-el numbers (deg)')
        return 2
    post = [x.strip() for x in a.postmortem.split(',') if x.strip()]
    if not amps:
        print('INPUT ERROR: --amp is empty')
        return 2
    if any(m <= 0 or m > FOC_MAX_MA for m in amps):
        print('INPUT ERROR: --amp must be 1..%d mA (FOC_I_MAX_MA clamps i=)' % FOC_MAX_MA)
        return 2
    if not angles:
        print('INPUT ERROR: --angles-el is empty')
        return 2
    set_current = not a.no_set_current
    cells = plan_cells(amps, angles, set_current)
    print('cells: %d (%d amplitudes x %d angles); window %.2f s; set_current=%s'
          % (len(cells), len(amps), len(angles), a.window, set_current))

    if a.dry_run:
        return run_dry(cells, set_current, post, a.window)

    if a.n_expected <= 0:
        print('INPUT ERROR: --n-expected is required for a real run. Run'
              ' TOOLS/calc_expected.py in the baseline step: without the scale dJEOS'
              ' has no meaning.')
        return 2
    eol = '\r\n' if a.eol == 'crlf' else '\n'
    link = Link(a.port, a.baud, eol)
    session = Session(link, cells, a.out, window=a.window, n_expected=a.n_expected,
                      post=post, set_current=set_current,
                      require_start=not a.allow_nonstart, sd_stop=not a.no_sd_stop)
    try:
        rc = session.run()
    finally:
        link.close()
    if session.abort:
        print('SESSION ABORTED: %s' % session.abort)
    return rc


if __name__ == '__main__':
    sys.exit(main())
