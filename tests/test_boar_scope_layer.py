"""Contract tests for the scope-layer frame writer (no scope needed).

The 2026-10-13 campaign lost a shift to exactly this class of defect in the
capture tool: the header list and the rows actually written drifted apart, and
the guard that was supposed to hold the contract compared the header constant
with itself, so it stayed green while every row on disk was wider than the
header. map_scope_ingest reads these files by name, verify_boar_campaign_ready
parses them, and a positional reader would see the extras as corrupt data - so
the check belongs on the bytes the writer puts on disk.

tools/boar_scope_layer.py imports tao3104a_cap, which imports pyusb, hence the
module-level skip.
"""
import csv

import numpy as np
import pytest

pytest.importorskip('usb', reason='tools/boar_scope_layer.py imports tao3104a_cap')

from tools.boar_scope_layer import (  # noqa: E402  (after importorskip by design)
    HDR,
    N_PULSES,
    analyse,
    fail_rows,
    write_rows,
)

# CH1 = ACS712 U, CH2 = ACS712 V; 100 mV/A around a 2.5 V bias.
CAL = {'CH1': (2500.0, 100.0), 'CH2': (2500.0, 100.0)}
QUIESCENT_MV, BURST_MV = 2500.0, 3000.0      # 0 mA and 5000 mA
QUIESCENT_CODE, BURST_CODE = 100.0, 150.0    # strictly inside (1.0, 254.0)


def _frame(pulses=N_PULSES, quiescent=False):
    """Rest for 200 samples, then `pulses` windows of 200 samples each."""
    n = 200 + 200 * pulses
    mv = np.full(n, QUIESCENT_MV if quiescent else BURST_MV)
    codes = np.full(n, QUIESCENT_CODE if quiescent else BURST_CODE)
    mv[:200] = QUIESCENT_MV
    codes[:200] = QUIESCENT_CODE
    data = {ch: (codes.copy(), mv.copy()) for ch in ('CH1', 'CH2')}
    return (None, None, data)


def _rows_on_disk(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.reader(row for row in f
                               if not row.lstrip().startswith('#')))


def test_header_contract_is_unchanged():
    # consumers (map_scope_ingest.parse_scope_csv, verify_boar_campaign_ready)
    # read these files by name
    assert HDR == ['pulse', 'ref_u_ma', 'ref_v_ma', 'scope_qualified',
                   'margin_ticks', 'blanking_ticks', 'note']


def test_written_rows_keep_the_header_width(tmp_path):
    rows = analyse(_frame(), CAL, thr_ma=300.0, min_plateau=5)
    assert [r[3] for r in rows] == [1] * N_PULSES   # the happy path really ran
    out = tmp_path / 'scope_region_0_0.csv'
    write_rows(out, rows, 'run-card 20261014')
    table = _rows_on_disk(out)
    assert table[0] == HDR
    # the check that the capture tool lacked: width of the bytes on disk, not
    # of the constant in memory
    assert {len(r) for r in table[1:]} == {len(HDR)}


def test_failed_rows_keep_the_header_width_too(tmp_path):
    # a frame with no burst above the threshold goes through fail_rows, a
    # different builder - it must not drift in width either
    rows = analyse(_frame(quiescent=True), CAL, thr_ma=300.0, min_plateau=5)
    assert [r[3] for r in rows] == [0] * N_PULSES
    assert rows == fail_rows(rows[0][6])
    out = tmp_path / 'scope_region_0_0.csv'
    write_rows(out, rows, 'run-card 20261014')
    table = _rows_on_disk(out)
    assert table[0] == HDR
    assert {len(r) for r in table[1:]} == {len(HDR)}
