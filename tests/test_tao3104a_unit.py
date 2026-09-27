"""Host tests for the TAO3104A volts model and time axis (no scope needed).

Instrument: TAO3104A, SN 2306027, firmware V3.0.0. Every number here is a live
calibration point recorded in tools/OWON_TAO3104A_PROTOCOL.md.

The defect being pinned down: `v = (code - offset) * scale/25` reads high by
CODE_0V * scale/25, i.e. +6.11 V at 500 mV/div (a grounded input "showed" 6.1 V
and a 0.33 V trace read 6.44 V). The second defect: the SCREEN buffer spans all
10 divisions, so `dt = 1/SAMPLE.SAMPLERATE` (1 us) is wrong - it is 3.289 us at
500 us/div, and the timebase/SAMPLERATE pair differs by 3.3x.

tools/tao3104a_cap.py imports pyusb at module level, so the module-level skip
keeps hosts without pyusb usable; CI installs python3-usb + python3-numpy so
the assertions really run there.
"""
import numpy as np
import pytest

pytest.importorskip('usb', reason='tools/tao3104a_cap.py imports pyusb')

from tools.tao3104a_cap import (  # noqa: E402  (after importorskip by design)
    CODE_0V,
    chan_meta,
    time_axis,
    to_volts,
    unit,
)

CH1_500MV = {'NAME': 'CH1', 'DISPLAY': 'ON', 'COUPLING': 'DC', 'PROBE': '1X',
             'SCALE': '500mV', 'OFFSET': -100}


def head_500us(ch1=None):
    """HEAD shaped exactly like the live one (same keys, trimmed channel list)."""
    return {
        'MODEL': 'TAO3104A', 'DATATYPE': 'SCREEN', 'RUNSTATUS': 'TRIG',
        'TIMEBASE': {'SCALE': '500us', 'HOFFSET': 0},
        'SAMPLE': {'DATALEN': 1520, 'SAMPLERATE': '(1MSa/s)', 'DEPMEM': 10000},
        'CHANNEL': [dict(ch1 or CH1_500MV)],
    }


# ---- unit parser ----------------------------------------------------------
def test_unit_parser_handles_scope_labels():
    assert unit('500mV') == pytest.approx(0.5)
    assert unit('100mV') == pytest.approx(0.1)
    assert unit('500us') == pytest.approx(5e-4)
    assert unit('(1MSa/s)') == pytest.approx(1e6)   # M is mega, not milli
    assert unit('(50kSa/s)') == pytest.approx(5e4)
    assert unit('') == pytest.approx(1.0)
    assert unit(None) == pytest.approx(1.0)


# ---- volts model ----------------------------------------------------------
def test_zero_volt_code_is_zero_volts():
    # grounded input at OFFSET=-100 sits at code CODE_0V + offset = 205.5
    grounded = CODE_0V + float(CH1_500MV['OFFSET'])
    assert 0.0 <= grounded <= 255.0
    assert to_volts(np.array([grounded]), CH1_500MV)[0] == pytest.approx(0.0, abs=1e-12)


def test_grounded_input_costs_at_most_one_code():
    grounded = CODE_0V + float(CH1_500MV['OFFSET'])
    one_code = 0.5 / 25.0
    for code in (grounded - 1.0, grounded + 1.0):
        assert abs(to_volts(np.array([code]), CH1_500MV)[0]) == pytest.approx(one_code)


def test_live_calibration_point_500mv_div():
    # live capture 2026-09-27: code 222 -> +0.33 V, where the scope's own
    # trigger-level marker (330 mV) sits on the trace
    v = to_volts(np.array([222.0]), CH1_500MV)[0]
    assert v == pytest.approx(0.330, abs=0.02)          # +-1 code = +-20 mV


def test_old_model_error_was_6_11_v_at_500mv_div():
    old = (222.0 - CH1_500MV['OFFSET']) * (0.5 / 25.0)   # pre-calibration formula
    new = to_volts(np.array([222.0]), CH1_500MV)[0]
    assert old == pytest.approx(6.44)
    assert old - new == pytest.approx(6.11)
    assert old - new == pytest.approx(CODE_0V * (0.5 / 25.0))


def test_probe_and_scale_keep_zero_at_zero():
    for probe in ('1X', '10X'):
        for scale in ('100mV', '200mV', '500mV', '1V'):
            meta = {'SCALE': scale, 'PROBE': probe, 'OFFSET': -100}
            code = CODE_0V - 100.0
            assert to_volts(np.array([code]), meta)[0] == pytest.approx(0.0, abs=1e-12)


def test_peak_to_peak_is_model_independent():
    # superseded 100 mV/div run: codes 63..195 -> pp 0.528 V under either model
    meta = {'SCALE': '100mV', 'PROBE': '1X', 'OFFSET': -139}
    v = to_volts(np.array([63.0, 195.0]), meta)
    assert v[1] - v[0] == pytest.approx(0.528, abs=1e-9)
    assert v[0] == pytest.approx(-0.414, abs=1e-3)       # old model said +0.808 V
    assert v[1] == pytest.approx(+0.114, abs=1e-3)       # old model said +1.336 V


# ---- time axis ------------------------------------------------------------
def test_time_axis_follows_timebase_not_samplerate():
    head = head_500us()
    t, dt = time_axis(head, 1520)
    assert dt == pytest.approx(3.2894736842e-6, rel=1e-9)
    assert dt == pytest.approx(10.0 * 5e-4 / 1520)
    assert 1.0 / dt == pytest.approx(304000.0, rel=1e-3)     # not the 1 MSa/s label
    assert head['SAMPLE']['SAMPLERATE'] == '(1MSa/s)'        # the misleading field
    assert t.shape == (1520,)
    assert t[0] == 0.0
    assert t[-1] == pytest.approx(1519 * dt, rel=1e-12)


def test_time_axis_window_covers_all_ten_divisions():
    _, dt = time_axis(head_500us(), 1520)
    assert 1520 * dt == pytest.approx(5e-3, rel=1e-12)       # 10 x 500 us screen


def test_time_axis_dt_scales_with_timebase():
    head = head_500us()
    head['TIMEBASE']['SCALE'] = '200us'
    _, dt = time_axis(head, 1520)
    assert dt == pytest.approx(10.0 * 2e-4 / 1520, rel=1e-12)


# ---- the path the tool actually walks -------------------------------------
def test_chan_meta_plus_model_on_live_head_shape():
    head = head_500us()
    meta = chan_meta(head, 'CH1')
    assert meta['SCALE'] == '500mV' and meta['OFFSET'] == -100
    assert to_volts(np.array([222.0]), meta)[0] == pytest.approx(0.330, abs=0.02)
    with pytest.raises(KeyError):
        chan_meta(head, 'CH3')
