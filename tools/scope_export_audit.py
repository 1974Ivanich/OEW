#!/usr/bin/env python3
"""Audit an oscilloscope panel export before it is used as evidence.

Input: a TAO3104A ``Copy -> Type=Wave, Format=CSV`` export. On this firmware the
panel export is the only working route (the USB DEPMEM frame read tears at a fixed
byte offset), and it carries 8-bit codes -- 25 codes per division -- so the code
size *in engineering units* follows the vertical scale of the channel rather than
the instrument's hardware class.

The audit answers one question per file: **can this trace carry the reference the
campaign needs, or is it good only for timing?** It takes the code size from the
measured level grid (never from a datasheet), takes the burst amplitude from the
target's own calibrated records when a UART log is supplied, and prints the ratio.

Exit codes:
  0  trace can carry the reference (one code <= --max-code-fraction of the amplitude)
  3  trace is real but too coarse to carry the reference (numbers printed)
  1  the export could not be parsed

Usage:
  python tools/scope_export_audit.py --csv <export.csv> \
      --calibration <acs712_calibration.json> [--log region_0_0.log] \
      [--max-code-fraction 0.2]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

TRIG_HIGH_MV = 1000.0        # PB6 is 3.3 V logic; anything above 1 V is "high"
WINDOW_US = 200              # one MapCapture window, at 1 us/sample
N_WINDOWS = 8
REFERENCE_CHANNEL = {'CH1': 'U', 'CH2': 'V', 'CH3': 'W'}


def parse_export(path):
    """-> (meta lines, column names, array).

    The panel appends the row index to *every* value at full precision
    (``2640.000000`` + ``5197``), so each cell is stripped of its own index first.
    """
    raw = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    try:
        head_at = next(k for k, l in enumerate(raw) if l.startswith('index,'))
    except StopIteration:
        raise SystemExit('exit 1: no "index," header row -- not a Copy->CSV panel export')
    meta = [l.strip() for l in raw[:head_at] if l.strip()]
    names = [c.strip() for c in raw[head_at].split(',')[1:]]
    rows, bad = [], 0
    for line in raw[head_at + 1:]:
        if not line.strip():
            continue
        cells = line.split(',')
        idx = cells[0].strip()
        vals = []
        for cell in cells[1:]:
            cell = cell.strip()
            if idx and cell.endswith(idx) and len(cell) > len(idx):
                cell = cell[:-len(idx)]
            try:
                vals.append(float(cell))
            except ValueError:
                bad += 1
                break
        if len(vals) == len(names):
            rows.append(vals)
    if bad or not rows:
        raise SystemExit('exit 1: %d unparsable rows, %d good' % (bad, len(rows)))
    return meta, names, np.array(rows)


def level_grid(series):
    """(unique levels, smallest non-zero spacing) -- the spacing is the code size."""
    lv = np.unique(np.round(series, 4))
    if lv.size < 2:
        return lv, 0.0
    return lv, float(np.min(np.diff(lv)))


def parse_calibration(path):
    """{'U': (v0_mv, sens_mv_per_a), ...} from either a nested or a flat file."""
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    flat = dict(d)
    if isinstance(d.get('sensors'), dict):
        flat.update(d['sensors'])
    out = {}
    for phase, src in flat.items():
        if isinstance(src, dict) and 'v0_mv' in src and 'sens_mv_per_a' in src:
            out[str(phase).upper()] = (float(src['v0_mv']), float(src['sens_mv_per_a']))
    if not out:
        raise SystemExit('exit 1: no {phase: {v0_mv, sens_mv_per_a}} entries in %s' % path)
    return out


REC_FIELD = re.compile(r'^(raw_)?i([12])=(-?\d+)$')


def parse_log(path):
    """Per-record calibrated shunt currents (mA) from @MC:REC, raw_i* fields excluded.

    Matching a bare ``i1=`` also matches inside ``raw_i1=`` and returns a 12-bit ADC
    count where milliamps were expected, so the raw_ prefix is filtered here.
    """
    recs = []
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        if '@MC:REC' not in line:
            continue
        i1 = i2 = None
        for field in line.split(':'):
            m = REC_FIELD.match(field.strip())
            if not m or m.group(1):
                continue
            if m.group(2) == '1':
                i1 = int(m.group(3))
            else:
                i2 = int(m.group(3))
        if i1 is not None and i2 is not None:
            recs.append((i1, i2))
    return recs


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    ap.add_argument('--csv', required=True)
    ap.add_argument('--calibration', required=True)
    ap.add_argument('--log', help='UART region log of the same point (@MC:REC cross-check)')
    ap.add_argument('--phase-channels', default='CH1,CH2',
                    help='scope channels carrying phase currents, in U,V(,W) order')
    ap.add_argument('--trigger-channel', default='CH4')
    ap.add_argument('--bus-channel', default='CH3')
    ap.add_argument('--max-code-fraction', type=float, default=0.2,
                    help='one code must be <= this fraction of the amplitude (default 0.2)')
    args = ap.parse_args()

    meta, names, data = parse_export(args.csv)
    # Column names carry a unit suffix on this instrument (CH1_Voltage(mV)), so index by
    # both the full name and the short channel name -- --*-channel CH1 works either way.
    col = {}
    for k, n in enumerate(names):
        col[n] = data[:, k]
        col[n.split('_')[0].strip().upper()] = data[:, k]
    if args.trigger_channel not in col:
        raise SystemExit('exit 1: no %s column; columns are %s'
                         % (args.trigger_channel, names))
    phases = [c.strip() for c in args.phase_channels.split(',')]
    cal = parse_calibration(args.calibration)

    print('file      : %s' % args.csv)
    print('samples   : %d, span %d us' % (data.shape[0], data.shape[0]))
    for line in meta:
        if line.split(':')[0].strip() in ('Time interval', 'Voltage per ADC value',
                                          'Average', 'PK-PK', 'Probe attenuation'):
            print('  %s' % line)

    high = np.flatnonzero(col[args.trigger_channel] > TRIG_HIGH_MV)
    if high.size == 0:
        print('VERDICT   : TIMING_ONLY -- no %s edge above %g mV, trace holds no burst'
              % (args.trigger_channel, TRIG_HIGH_MV))
        return 3
    start, stop = int(high[0]), int(high[-1])
    print('burst     : %s high at samples %d..%d = %d us (%.3f ms)'
          % (args.trigger_channel, start, stop, stop - start + 1,
             (stop - start + 1) / 1000.0))
    if args.bus_channel in col:
        print('bus       : mean %.0f mV over the burst'
              % col[args.bus_channel][start:stop + 1].mean())

    print('%-6s %8s %8s %8s %10s %10s' % ('chan', 'v0_mV', 'sens', 'step_mV',
                                          'code_mA', 'levels'))
    code_ma = {}
    for ch, phase in zip(phases, ['U', 'V', 'W']):
        if ch not in col or phase not in cal:
            continue
        v0, sens = cal[phase]
        lv, step = level_grid(col[ch])
        code_ma[ch] = step / sens * 1000.0
        print('%-6s %8.1f %8.0f %8.2f %10.1f %10d'
              % ('%s/%s' % (ch, phase), v0, sens, step, step / sens * 1000.0, lv.size))

    print('--- %d windows of %d us from the rising edge, mA ---' % (N_WINDOWS, WINDOW_US))
    means = {}
    header = '  win' + ''.join(' %10s' % c for c in phases)
    if len(phases) == 2:
        header += ' %10s' % 'W=-(U+V)'
    print(header)
    for k in range(N_WINDOWS):
        a = start + k * WINDOW_US
        b = a + WINDOW_US
        if b > data.shape[0]:
            print('  %2d  truncated by frame end' % (k + 1))
            break
        vals = {}
        for ch, phase in zip(phases, ['U', 'V', 'W']):
            v0, sens = cal[phase]
            vals[phase] = (col[ch][a:b].mean() - v0) / sens * 1000.0
        for phase in vals:
            means.setdefault(phase, []).append(vals[phase])
        line = '  %2d' % (k + 1) + ''.join(' %10.1f' % vals[p] for p in vals)
        if len(phases) == 2:
            line += ' %10.1f' % (-(vals['U'] + vals['V']))
        print(line)

    amps = {}
    for phase, series in means.items():
        sp = max(abs(v) for v in series)
        amps[phase] = sp
        print('amplitude : %s peak |mean per window| = %.1f mA' % (phase, sp))

    log_recs = parse_log(args.log) if args.log else []
    if log_recs:
        print('--- target\'s own records (%s), mA ---' % args.log)
        for k, (i1, i2) in enumerate(log_recs):
            print('  rec %d  i1 %6d  i2 %6d' % (k + 1, i1, i2))
        ref = max(max(abs(a), abs(b)) for a, b in log_recs)
        print('reference : target peak |i| = %d mA (authoritative)' % ref)
    else:
        ref = max(amps.values()) if amps else 0.0
        print('reference : no --log given, using the trace\'s own peak (not authoritative)')

    worst_ch = max(code_ma, key=lambda c: code_ma[c]) if code_ma else None
    if worst_ch is None:
        return 3
    frac = code_ma[worst_ch] / ref if ref else float('inf')
    print('code/ref  : %.1f mA code / %d mA reference = %.1f%% (limit %.0f%%)'
          % (code_ma[worst_ch], ref, 100.0 * frac, 100.0 * args.max_code_fraction))
    if frac <= args.max_code_fraction:
        print('VERDICT   : REFERENCE_OK -- the trace can carry the aperture reference')
        return 0
    print('VERDICT   : TIMING_ONLY -- each window mean is quantisation noise; '
          'do not write it into a reference field')
    return 3


if __name__ == '__main__':
    sys.exit(main())
