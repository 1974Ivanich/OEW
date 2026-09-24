#!/usr/bin/env python3
"""
Synthetic software acceptance for the ACS712 -> scope CSV -> map_scope_ingest
contract. Runs without hardware and without touching any safety module.

It exercises the REAL emitter (scope_acs712_capture.write_scope_csv) and the
REAL consumer (map_scope_ingest.parse_scope_csv), so a passing run proves the
two tools agree - not merely that a formula was retyped correctly here.

Contract under test:
    scope writes RAW INTEGER mV
    ingest converts  mA = (mV - v0) / sens_mv_per_a * 1000
    W is derived as -(U + V)

Run:  python tools/acs712_ingest_acceptance.py
Exit: 0 = PASS, 1 = FAIL
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import map_scope_ingest as ING          # noqa: E402
import scope_acs712_capture as S        # noqa: E402

CALIB = HERE / 'acs712_calibration.example.json'
N_ROWS = 16
FAILURES = []


def check(name, ok, detail=''):
    print('  [%s] %-44s %s' % ('PASS' if ok else 'FAIL', name, detail))
    if not ok:
        FAILURES.append(name)


def main():
    print('=== ACS712 conversion / plumbing acceptance ===')

    check('calibration example exists', CALIB.exists(), str(CALIB))
    if not CALIB.exists():
        return 1

    raw = json.loads(CALIB.read_text(encoding='utf-8'))
    calib = ING._load_calibration(CALIB)
    u0, us = calib['sensors']['U']
    v0, vs = calib['sensors']['V']
    check('loader reads v0/sens for U', (u0, us) == (2680.0, 100.0),
          'U v0=%.1f sens=%.1f' % (u0, us))
    check('loader reads v0/sens for V', (v0, vs) == (2661.0, 100.0),
          'V v0=%.1f sens=%.1f' % (v0, vs))

    # Commanded currents -> the mV values the scope would report.
    # I_mA = (mV - v0) / sens * 1000  =>  mV = v0 + I_mA * sens / 1000
    want_u_ma = 1000      # +1.000 A
    want_v_ma = -1000     # -1.000 A
    mv_u_val = u0 + want_u_ma * us / 1000.0      # 2780 mV
    mv_v_val = v0 + want_v_ma * vs / 1000.0      # 2561 mV

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        csv_path = tdp / 'scope_region_0.csv'

        # 16 pulses, constant current - produced by the real emitter
        pulses = np.arange(N_ROWS) + 5
        mv_u = np.full(200, mv_u_val)
        mv_v = np.full(200, mv_v_val)
        S.write_scope_csv(csv_path, pulses, mv_u, mv_v,
                          margin_ticks=900, blanking_ticks=15,
                          qualified=1, note='')

        check('CSV written by real emitter', csv_path.exists(),
              '%d bytes' % csv_path.stat().st_size)

        head = csv_path.read_text(encoding='utf-8').splitlines()[0]
        check('CSV header contract',
              head == 'pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,'
                      'blanking_ticks,scope_qualified,note', head)

        body = csv_path.read_text(encoding='utf-8').splitlines()[1]
        check('CSV emits integer mV (not float)', '.' not in body.split(',')[1],
              body)

        rows = ING.parse_scope_csv(csv_path, expected_rows=N_ROWS,
                                   calibration=calib)
        check('ingest parsed %d rows' % N_ROWS, len(rows) == N_ROWS,
              'got %d' % len(rows))

        r = rows[0]
        check('ref_u_ma == +1000', r['ref_u_ma'] == 1000, str(r['ref_u_ma']))
        check('ref_v_ma == -1000', r['ref_v_ma'] == -1000, str(r['ref_v_ma']))
        check('ref_w_ma == 0 (KCL-derived)', r['ref_w_ma'] == 0,
              str(r['ref_w_ma']))
        check('raw-sign: mV>v0 -> I>0', r['ref_u_ma'] > 0 and r['ref_v_ma'] < 0,
              'U=%d V=%d' % (r['ref_u_ma'], r['ref_v_ma']))
        check('scope_qualified carried through', r['scope_qualified'] == 1,
              str(r['scope_qualified']))
        check('margin_ticks carried through', r['margin_ticks'] == 900,
              str(r['margin_ticks']))

        # a 1 mV offset must produce exactly 10 mA at 100 mV/A
        csv2 = tdp / 'scope_region_1.csv'
        S.write_scope_csv(csv2, pulses, np.full(200, mv_u_val + 1),
                          np.full(200, mv_v_val), 900, 15, 1, '')
        rows2 = ING.parse_scope_csv(csv2, expected_rows=N_ROWS, calibration=calib)
        check('1 mV == 10 mA at 100 mV/A',
              rows2[0]['ref_u_ma'] - r['ref_u_ma'] == 10,
              'delta=%d mA' % (rows2[0]['ref_u_ma'] - r['ref_u_ma']))

        # closing the loop on the unit error: a float-formatting emitter
        # (the old defect) must be rejected by the consumer
        csv3 = tdp / 'scope_region_2.csv'
        csv3.write_text(
            'pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,'
            'scope_qualified,note\n'
            '1,2780.0000,2561.0000,,900,15,1,\n', encoding='utf-8')
        try:
            ING.parse_scope_csv(csv3, expected_rows=1, calibration=calib)
            check('float mV rejected by ingest', False,
                  'ingest accepted a float mV value')
        except ValueError as e:
            check('float mV rejected by ingest', 'не int' in str(e), str(e)[:70])

        # calibration JSON documentation fields must not break the loader
        check('documentation-only JSON fields tolerated',
              'qualification' in raw and 'sensitivity_type'
              in raw['sensors']['U'], 'loader ignored them')

        # The calibration example must satisfy the project's ALREADY EXISTING
        # validator. Calling it is stronger than re-typing its bounds here.
        import shutil
        import acs712_nohv_validator as VAL
        camp = tdp / 'campaign'
        (camp / 'calibration').mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CALIB, camp / 'calibration' / 'acs712_calibration.json')
        rec = VAL.Recorder()
        data = VAL._check_calibration(camp, rec)
        vfails = [c.check_id for c in rec.checks if c.result == 'FAIL']
        check('calibration passes existing validator',
              data is not None and not vfails,
              '%d checks, fails=%s' % (len(rec.checks), vfails or 'none'))

    print()
    if FAILURES:
        print('ACCEPTANCE FAILED: %d check(s): %s' % (len(FAILURES), FAILURES))
        return 1
    print('ACCEPTANCE PASS - scope emitter and map_scope_ingest agree')
    print('NOTE: this proves the SOFTWARE contract only.')
    print('      ACS712 physical metrology remains NOT QUALIFIED (see TZ-REF-01).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
