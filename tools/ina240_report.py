#!/usr/bin/env python3
"""Analyze a CSV of three INA240A1 channels (0.1 ohm shunts).

Input: CSV produced by ``tao3104a_cap.py`` (columns
``sample_index,time_s,ch1_v,ch2_v,ch3_v,ch4_v``) plus optional sidecars
``<csv>.head.json`` and ``<csv>.measure.json``.

Hardware model
    INA240A1 (G = 20 V/V), Rshunt = 0.1 ohm, VCC = 5 V so Vref = 2.5 V.
        Vout = 2.5 + I * 2.0     ->     I = (Vout - 2.5) / 2.0   [A]
    One LSB at 500 mV/div, 1X probe is 20 mV = 10 mA.

Verdict convention matches ``scope_export_audit.py``:
    0  REFERENCE_OK  one code <= --max-code-fraction of the amplitude
    3  TIMING_ONLY   the trace is real but too coarse to be the reference
    1  parse error

Usage:
    python tools/ina240_report.py --csv scope_region_00.csv \\
        [--head scope_region_00.head.json] \\
        [--measure scope_region_00.measure.json] \\
        [--vzero 2.5] [--gain 2.0] [--max-code-fraction 0.2]
"""
from __future__ import annotations

import argparse
import csv as csv_mod
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ─ hardware constants ────────────────────────────────────────────
VREF_NOMINAL = 2.5    # V, VCC = 5 V -> internal reference at half supply
GAIN_VPERA = 20.0     # INA240A1
RSHUNT_OHM = 0.1      # ohm
VPER_A = GAIN_VPERA * RSHUNT_OHM      # 2.0 V/A
CODES_PER_DIV = 25.0
SELFCHECK_TOL_CODES = 3.0
# A balanced three-phase set sums to zero, so any residual peak of the order of
# the amplitude means a swapped or mis-signed channel rather than a real current.
KCL_PEAK_FRACTION = 0.2

_UNIT_RE = re.compile(r'^\s*([0-9.]+)\s*([kKmMuUnNgG]?)')
_UNIT_FACTOR = {'k': 1e3, 'K': 1e3, 'M': 1e6,
                'm': 1e-3, 'u': 1e-6, 'n': 1e-9}


def parse_scalar(s: str) -> float:
    """'500mV' -> 0.5, '1.00V' -> 1.0, '(1MSa/s)' -> 1e6.

    HEAD wraps some values in parentheses and M there is *mega*, not milli:
    the same trap tools/tao3104a_cap.py documents for the sample rate.
    """
    s = s.strip().strip('()').strip()
    m = _UNIT_RE.match(s)
    if not m:
        return float('nan')
    return float(m.group(1)) * _UNIT_FACTOR.get(m.group(2) or '', 1.0)


def parse_csv(path: Path) -> Tuple[List[str], np.ndarray]:
    """-> (column names, data[N, cols]) with non-numeric cells as NaN."""
    rows: List[List[float]] = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv_mod.reader(f)
        names = [c.strip() for c in next(reader)]
        for row in reader:
            if not row or not row[0].strip():
                continue
            vals: List[float] = []
            for cell in row:
                try:
                    vals.append(float(cell.strip()))
                except ValueError:
                    vals.append(float('nan'))
            if len(vals) == len(names):
                rows.append(vals)
    if not rows:
        raise SystemExit('ERROR: no data rows in %s' % path)
    return names, np.array(rows)


def load_json(path: Optional[Path]) -> Dict[str, Any]:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding='utf-8'))
    return {}


def chan_meta(head: Dict[str, Any], ch: str) -> Dict[str, Any]:
    """Per-channel scope settings from the HEAD block."""
    for c in head.get('CHANNEL', []):
        if c.get('NAME') == ch:
            scale = parse_scalar(c.get('SCALE', '500mV'))
            if not (scale > 0):
                scale = 0.5
            probe = str(c.get('PROBE', '1X')).upper()
            return {'scale_v': scale,
                    'probe_x': 10.0 if '10X' in probe else 1.0,
                    'coupling': c.get('COUPLING', 'DC')}
    return {'scale_v': 0.5, 'probe_x': 1.0, 'coupling': 'DC'}


def code_size_ma(scale_v: float, probe_x: float, vper_a: float) -> float:
    """One LSB expressed in milliamps."""
    return (scale_v * probe_x / CODES_PER_DIV) / vper_a * 1000.0


def level_step(v: np.ndarray) -> Tuple[int, float]:
    """(number of unique levels, smallest non-zero spacing) in volts."""
    clean = v[~np.isnan(v)]
    if clean.size < 2:
        return int(clean.size), 0.0
    lv = np.unique(np.round(clean, 4))
    if lv.size < 2:
        return int(lv.size), 0.0
    return int(lv.size), float(np.min(np.diff(lv)))


def selfcheck(measure: Dict[str, Any], ch: str, v: np.ndarray,
              scale_v: float, probe_x: float) -> Dict[str, Any]:
    """Decode vs the instrument's own measurements, in codes."""
    chm = measure.get('measurements', {}).get(ch)
    if not chm:
        return {}
    per_code = scale_v * probe_x / CODES_PER_DIV
    out: Dict[str, Any] = {}
    pairs = (('vmin', float(v.min()), float(chm.get('min') or 0.0)),
             ('vmax', float(v.max()), float(chm.get('max') or 0.0)),
             ('vpp', float(v.max() - v.min()), float(chm.get('pkpk') or 0.0)))
    for key, tool_v, scope_v in pairs:
        err = tool_v - scope_v
        out[key] = {'tool_v': tool_v, 'scope_v': scope_v, 'err_v': err,
                    'err_codes': err / per_code,
                    'ok': abs(err / per_code) <= SELFCHECK_TOL_CODES}
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--head', default=None)
    ap.add_argument('--measure', default=None)
    ap.add_argument('--vzero', default=None,
                    help='zero-current volts: a number, or "median" to take the '
                         'per-channel median. Default is the nominal %.1f V. '
                         'Do NOT use median for a DC point - the median of a '
                         'biased trace is the current being measured.'
                         % VREF_NOMINAL)
    ap.add_argument('--zero-csv', default=None,
                    help='CSV of a zero-current frame (PWM off) of the same '
                         'setup; its per-channel median becomes the zero level')
    ap.add_argument('--gain', type=float, default=VPER_A,
                    help='volts per amp (default %.1f = INA240A1 x 0.1R)' % VPER_A)
    ap.add_argument('--phases', default='ch1_v,ch2_v,ch3_v',
                    help='CSV columns for U,V,W')
    ap.add_argument('--marker', default='ch4_v',
                    help='CSV column of the GPIO marker')
    ap.add_argument('--max-code-fraction', type=float, default=0.2)
    ap.add_argument('--strict', action='store_true',
                    help='also fail (exit 4) when the selfcheck fails')
    args = ap.parse_args(argv)

    names, data = parse_csv(Path(args.csv))
    idx = {n: i for i, n in enumerate(names)}
    head = load_json(args.head)
    measure = load_json(args.measure)

    phase_cols = [c.strip() for c in args.phases.split(',') if c.strip()]
    missing = [c for c in phase_cols if c not in idx]
    if missing:
        raise SystemExit('ERROR: columns not in CSV: %s (have %s)'
                         % (missing, names))

    print('=' * 64)
    print('INA240A1 current report')
    print('=' * 64)
    print('file    : %s' % args.csv)
    print('rows    : %d' % data.shape[0])
    print('model   : Vout = %.1f + I x %.2f  (INA240A1 %.0f V/V, %.3f ohm)'
          % (VREF_NOMINAL, args.gain, GAIN_VPERA, RSHUNT_OHM))

    currents: Dict[str, np.ndarray] = {}
    codes_ma: Dict[str, float] = {}
    amps: Dict[str, float] = {}
    checks: Dict[str, Dict[str, Any]] = {}

    # Resolve the zero-current level once: an explicit number wins, then a real
    # zero frame (--zero-csv, captured with PWM off and no current), then the
    # nominal Vref/2. The median stays available but is never the default.
    vzero_src = ''
    zero_levels: Dict[str, float] = {}
    vzero_nominal = VREF_NOMINAL
    if args.vzero is not None and str(args.vzero).lower() == 'median':
        vzero_src = 'median'
    elif args.vzero is not None:
        vzero_nominal = float(args.vzero)
        vzero_src = 'command line'
    else:
        vzero_src = 'nominal Vref/2'
    if args.zero_csv:
        zn, zd = parse_csv(Path(args.zero_csv))
        zidx = {n: i for i, n in enumerate(zn)}
        for c in phase_cols:
            if c in zidx:
                zero_levels[c] = float(np.nanmedian(zd[:, zidx[c]]))
        vzero_src = 'zero frame'
        print('zero csv  : %s' % args.zero_csv)

    def zero_note(col: str) -> str:
        if vzero_src == 'median':
            return 'median of this trace'
        if col in zero_levels:
            return 'measured zero frame'
        return vzero_src

    for col in phase_cols:
        v = data[:, idx[col]]
        meta = chan_meta(head, col.split('_')[0].upper())
        scale_v, probe_x = meta['scale_v'], meta['probe_x']
        # The zero level must NOT come from the same trace when the point is a
        # DC current: the median of a biased trace is the bias itself, so the
        # measured current would be subtracted away and reported as zero.
        # Default is the nominal Vref/2; --zero-csv takes a real zero frame.
        if vzero_src == 'median':
            vzero = float(np.nanmedian(v))
        elif col in zero_levels:
            vzero = zero_levels[col]
        else:
            vzero = vzero_nominal

        i = (v - vzero) / args.gain * 1000.0          # mA
        currents[col] = i
        cma = code_size_ma(scale_v, probe_x, args.gain)
        codes_ma[col] = cma
        nlev, step_v = level_step(v)
        amp = float(np.nanmax(np.abs(i)))
        amps[col] = amp
        checks[col] = selfcheck(measure, col.split('_')[0].upper(),
                               v, scale_v, probe_x)

        print('\n%s:' % col)
        print('  scope     : %g V/div, %.1fX, %s'
              % (scale_v, probe_x, meta['coupling']))
        print('  zero      : %.4f V  (%s)' % (vzero, zero_note(col)))
        print('  levels    : %d, step %.4f V' % (nlev, step_v))
        print('  code      : %.1f mA' % cma)
        print('  I mean    : %.1f mA' % float(np.nanmean(i)))
        print('  I peak    : %.1f mA' % amp)
        print('  I rms     : %.1f mA' % float(np.sqrt(np.nanmean(i * i))))
        for key in ('vmin', 'vmax', 'vpp'):
            d = checks[col].get(key)
            if d:
                print('  %-9s: decode %+.4f V vs scope %+.4f V  (%+.1f codes) %s'
                      % (key, d['tool_v'], d['scope_v'], d['err_codes'],
                         'OK' if d['ok'] else 'FAIL'))

    print('\n' + '-' * 64)
    print('KCL: iU + iV + iW')
    print('-' * 64)
    if len(phase_cols) == 3:
        res = sum(currents[c] for c in phase_cols)
        amp_ref = max(amps.values()) if amps else 0.0
        peak_res = float(np.nanmax(np.abs(res)))
        print('  mean abs: %.1f mA' % abs(float(np.nanmean(res))))
        print('  rms     : %.1f mA' % float(np.nanstd(res)))
        print('  peak    : %.1f mA' % peak_res)
        # The mean of a balanced three-phase set is zero even when one phase is
        # inverted, so the residual has to be judged on its peak. A swapped or
        # mis-signed channel leaves a residual of the order of the amplitude.
        if amp_ref and peak_res <= KCL_PEAK_FRACTION * amp_ref:
            print('  KCL     : PASS (peak %.1f mA <= %.0f%% of %.1f mA peak)'
                  % (peak_res, 100.0 * KCL_PEAK_FRACTION, amp_ref))
        else:
            print('  KCL     : WARN -- residual peak %.1f mA against %.1f mA '
                  'amplitude, check polarity/mount' % (peak_res, amp_ref))
    else:
        print('  skipped (%d phase columns)' % len(phase_cols))

    if args.marker and args.marker in idx:
        mk = data[:, idx[args.marker]]
        mk = mk[~np.isnan(mk)]
        if mk.size:
            hi = mk[mk > 1.5]
            lo = mk[mk < 1.0]
            rising = int(np.count_nonzero((mk[1:] > 1.5) & (mk[:-1] <= 1.5)))
            print('\n' + '-' * 64)
            print('marker %s' % args.marker)
            print('-' * 64)
            print('  high    : %.3f V (n=%d)'
                  % (float(hi.mean()) if hi.size else float('nan'), hi.size))
            print('  low     : %.3f V (n=%d)'
                  % (float(lo.mean()) if lo.size else float('nan'), lo.size))
            print('  rising  : %d edge(s)' % rising)

    print('\n' + '=' * 64)
    if not codes_ma:
        print('VERDICT   : TIMING_ONLY (no phase channels)')
        return 3
    worst = max(codes_ma, key=lambda c: codes_ma[c])
    ref = max(amps.values()) if amps else 0.0
    if ref < 1.0:
        print('WARNING   : amplitudes below 1 mA -- likely noise, not current')
    frac = codes_ma[worst] / ref if ref > 0 else float('inf')
    print('worst code: %.1f mA of %.1f mA peak = %.1f%% (limit %.0f%%)'
          % (codes_ma[worst], ref, 100.0 * frac, 100.0 * args.max_code_fraction))

    failed = sorted(c for c, ch in checks.items()
                        if any(not d['ok'] for d in ch.values() if 'ok' in d))
    if failed:
        # Print channel names (CH1..CH4), not CSV column names
        ch_names = sorted(set(c.split('_')[0].upper() for c in failed))
        print('selfcheck : FAIL on %s' % ', '.join(ch_names))

    if frac <= args.max_code_fraction and not failed:
        print('VERDICT   : REFERENCE_OK')
        return 0
    print('VERDICT   : TIMING_ONLY (%s)'
          % '; '.join(filter(None, [
              'code too coarse' if frac > args.max_code_fraction else '',
              'selfcheck failed' if failed else ''])))
    if args.strict and failed:
        return 4
    return 3


if __name__ == '__main__':
    sys.exit(main())
