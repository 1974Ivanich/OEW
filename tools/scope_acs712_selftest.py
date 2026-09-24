#!/usr/bin/env python3
"""
Synthetic self-test for tools/scope_acs712_capture.py - runs without hardware.

Purpose: prove the pure functions are correct before any bench time is spent,
and specifically demonstrate that the scope_qualified gate now REJECTS traces
the old amplitude sniff would have accepted.

Run:  python tools/scope_acs712_selftest.py
Exit: 0 = all checks pass, 1 = failure
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scope_acs712_capture as S   # noqa: E402

FAILURES = []

# 500 mV/div, 1X, offset 0 -> to_mv() yields 20 mV per code. Codes stay well
# inside 1..254 so that no channel is falsely reported as rail-clipped.
META = {'SCALE': '500mV', 'PROBE': '1X', 'OFFSET': '0'}
MV_PER_CODE = 20.0


def check(name, ok, detail=''):
    print('  [%s] %-40s %s' % ('PASS' if ok else 'FAIL', name, detail))
    if not ok:
        FAILURES.append(name)


def to_codes(mv):
    """mV (as commanded) -> 8-bit codes, the way the scope would quantise."""
    return np.clip(np.round(np.asarray(mv) / MV_PER_CODE), 0, 255).astype(np.int32)


def chan(mv):
    """Build a channel dict the way cmd_capture does: codes + converted mV."""
    codes = to_codes(mv)
    return {'codes': codes, 'mv': S.to_mv(codes, META)}


def good_acquisition(sr=200000.0, n=4000, pwm_hz=5000.0,
                     u_amp_mv=200.0, v_amp_mv=150.0):
    """Periodic marker + two phase currents with switching structure."""
    t = np.arange(n) / sr
    sync_mv = np.where((t * pwm_hz) % 1.0 < 0.5, 3300.0, 500.0)
    u_mv = 2680.0 + u_amp_mv * np.sin(2 * np.pi * pwm_hz * t)
    v_mv = 2661.0 + v_amp_mv * np.sin(2 * np.pi * pwm_hz * t + 2.1)
    return {'sync': chan(sync_mv), 'u': chan(u_mv), 'v': chan(v_mv)}


def dc_acquisition(n=4000, sync_mv=3300.0, u_mv=2680.0, v_mv=2661.0):
    """DC-only: no periodicity, no signal span - the old gate's blind spot."""
    return {'sync': chan(np.full(n, sync_mv)),
            'u': chan(np.full(n, u_mv)),
            'v': chan(np.full(n, v_mv))}


def main():
    sr = 200000.0
    pwm = 5000.0

    print('=== unit() parsing ===')
    check('unit("(50kSa/s)") == 50000', abs(S.unit('(50kSa/s)') - 50000.0) < 1e-6,
          '%.1f' % S.unit('(50kSa/s)'))
    check('unit("(500kSa/s)") == 500000', abs(S.unit('(500kSa/s)') - 500000.0) < 1e-6,
          '%.1f' % S.unit('(500kSa/s)'))
    check('unit("10 ms") == 0.01', abs(S.unit('10 ms') - 0.01) < 1e-9,
          '%.9f' % S.unit('10 ms'))
    check('unit("500us") == 5e-4', abs(S.unit('500us') - 5e-4) < 1e-12,
          '%.12f' % S.unit('500us'))
    check('unit("50.0mV") == 0.05', abs(S.unit('50.0mV') - 0.05) < 1e-12,
          '%.12f' % S.unit('50.0mV'))
    check('unit("") == 0.0 (no exception)', S.unit('') == 0.0, repr(S.unit('')))
    check('unit(None) == 0.0 (no exception)', S.unit(None) == 0.0, repr(S.unit(None)))
    check('unit("garbage") == 0.0 (no exception)', S.unit('garbage') == 0.0,
          repr(S.unit('garbage')))

    print('=== to_mv() conversion ===')
    meta1 = {'SCALE': '1.00 V', 'PROBE': '1X', 'OFFSET': '0'}
    mv = S.to_mv(np.array([0, 25, 50, 100, 200], dtype=np.int32), meta1)
    check('1V/div, 1X: 25 codes == 1000 mV', abs(mv[1] - 1000.0) < 1e-6,
          '%.1f mV' % mv[1])
    meta10 = {'SCALE': '1.00 V', 'PROBE': '10X', 'OFFSET': '0'}
    mv10 = S.to_mv(np.array([0, 25], dtype=np.int32), meta10)
    check('1V/div, 10X: 25 codes == 10000 mV', abs(mv10[1] - 10000.0) < 1e-6,
          '%.1f mV' % mv10[1])
    meta2 = {'SCALE': '500mV', 'PROBE': '1X', 'OFFSET': '0'}
    mv2 = S.to_mv(np.array([0, 1], dtype=np.int32), meta2)
    check('500mV/div, 1X: 1 code == 20 mV', abs(mv2[1] - 20.0) < 1e-6,
          '%.1f mV' % mv2[1])

    print('=== time_axis() consistency ===')
    head_ok = {'TIMEBASE': {'SCALE': '500us'},
               'SAMPLE': {'DATALEN': 1000, 'SAMPLERATE': '(200kSa/s)'}}
    _, sr_ok, rep_ok = S.time_axis(head_ok, 1000)
    check('consistent span vs label', rep_ok['consistent'], 'sr=%.0f' % sr_ok)

    head_bad = {'TIMEBASE': {'SCALE': '10ms'},
                'SAMPLE': {'DATALEN': 1520, 'SAMPLERATE': '(50kSa/s)'}}
    _, _, rep_bad = S.time_axis(head_bad, 1520)
    check('inconsistent span vs label flagged', not rep_bad['consistent'],
          'ratio=%.3f' % rep_bad['ratio'])

    print('=== scope_qualified gate ===')
    chans = good_acquisition(sr, 4000, pwm)
    margin = 900.0
    q_ok, checks_ok, notes_ok = S.qualify(chans, sr, pwm, margin, 110)
    check('valid synthetic trace ACCEPTED', q_ok == 1,
          'qualified=%d notes=%s' % (q_ok, notes_ok[:2]))

    dc = dc_acquisition()
    q_dc, checks_dc, notes_dc = S.qualify(dc, sr, pwm, margin, 110)
    check('DC-only trace REJECTED', q_dc == 0,
          'failed=%s' % [k for k, v in checks_dc.items() if not v])

    # The defect the new gate closes: a flat DC marker sitting away from
    # mid-rail satisfied the old amplitude test.
    old_gate = 1 if abs(float(dc['sync']['mv'].mean()) - 2500.0) > 500.0 else 0
    check('old amplitude sniff PASSED that DC trace', old_gate == 1,
          'old gate returned %d - this is the defect now closed' % old_gate)

    chans = good_acquisition(sr, 4000, pwm)
    _, checks_wrongf, _ = S.qualify(chans, sr, 20000.0, margin, 110)
    check('wrong declared PWM freq REJECTED',
          not checks_wrongf['pwm_frequency_match'],
          'pwm_frequency_match=%s' % checks_wrongf['pwm_frequency_match'])

    chans = good_acquisition(sr, 4000, pwm)
    chans['u']['codes'][100:120] = 255
    q_clip, checks_clip, _ = S.qualify(chans, sr, pwm, margin, 110)
    check('clipped trace REJECTED', q_clip == 0 and not checks_clip['no_clipping'],
          'no_clipping=%s' % checks_clip['no_clipping'])

    chans = good_acquisition(sr, 4000, pwm)
    _, checks_lowm, _ = S.qualify(chans, sr, pwm, 50.0, 110)
    check('margin below minimum REJECTED',
          not checks_lowm['margin_above_minimum'],
          'margin_above_minimum=%s' % checks_lowm['margin_above_minimum'])

    chans = good_acquisition(sr, 4000, pwm)
    _, checks_nom, _ = S.qualify(chans, sr, pwm, None, 110)
    check('unmeasurable margin REJECTED',
          not checks_nom['margin_above_minimum'],
          'margin_above_minimum=%s' % checks_nom['margin_above_minimum'])

    chans = good_acquisition(sr, 4000, pwm)
    _, checks_short, _ = S.qualify(chans, sr, pwm, margin, 100000.0)
    check('margin below a large minimum REJECTED',
          not checks_short['margin_above_minimum'], 'ok')

    print('=== measure_margin_ticks() ===')
    timer_hz = 170e6
    n = 4000
    idx = np.arange(n)
    # marker rises at i = 40, 80, ... ; switching edge 5 samples later
    sync_mv = np.where((idx % 40) < 20, 3300.0, 500.0)
    u_mv = np.where((idx % 40) >= 5, np.where((idx % 40) < 10, 3000.0, 2680.0),
                    2680.0)
    m, err = S.measure_margin_ticks(S.to_mv(to_codes(u_mv), META),
                                    S.to_mv(to_codes(sync_mv), META), sr, timer_hz)
    want = (5.0 / sr) * timer_hz
    check('margin measured', m is not None, 'err=%s' % err)
    if m is not None:
        check('margin value correct', abs(m - want) < 1.0,
              'got %.2f ticks, want %.2f' % (m, want))

    m2, err2 = S.measure_margin_ticks(S.to_mv(to_codes(np.full(n, 2680.0)), META),
                                      S.to_mv(to_codes(sync_mv), META), sr, timer_hz)
    check('flat current channel -> margin not claimed', m2 is None,
          'err=%s' % err2)

    print()
    if FAILURES:
        print('SELFTEST FAILED: %d check(s) failed: %s' % (len(FAILURES), FAILURES))
        return 1
    print('SELFTEST PASS - all checks OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
