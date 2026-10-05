"""Host tests for the TAO3104A volts model and frame geometry (no scope needed).

Instrument: TAO3104A, SN 2306027, firmware V3.0.0. Every number here is a live
calibration point recorded in tools/OWON_TAO3104A_PROTOCOL.md.

Two defects are pinned down here; both were refuted by the same live session
(2026-10-05, 1 V/div, 1X, OFFSET=-126, 0..5.08 V square wave, instrument's own
MIN=-40.00mV / MAX=+5.08V / PKPK=5.12V / F=1.000KHz):

1. `v = (code - offset) * scale/25` reads high by CODE_0V * scale/25 (that is
   +6.11 V at 500 mV/div: a grounded input "showed" 6.1 V and a 0.33 V trace
   read 6.44 V). CODE_0V is a fit to ONE configuration, so the zero-volt code
   is now calibrated per frame from the instrument's own MIN/MAX
   (calibrate_zero_code); the legacy constant survives only as a flagged
   fallback.

2. `dt = 10 * TIMEBASE / n` (3.289 us at 500 us/div) is wrong twice over: a
   SCREEN frame is 760 pixels x [max, min] (so it is not even a uniform time
   series - both samples of a pixel share the pixel's time), and its pixel
   period is TIMEBASE/50 = 10 us at 500 us/div. That makes the SCREEN window
   7.6 ms, which the DEPMEM HEAD of the same capture confirms with
   SAMPLE.FULLSCREEN = 7600 samples at 1 MSa/s. Counting SCREEN samples between
   edges over-reports frequency by 1.52x (1.52 kHz for a 1.000 kHz signal);
   the pixel grid gives it exactly.

tools/tao3104a_cap.py imports pyusb at module level, so the module-level skip
keeps hosts without pyusb usable; CI installs python3-usb + python3-numpy so
the assertions really run there.
"""
import numpy as np
import pytest

pytest.importorskip('usb', reason='tools/tao3104a_cap.py imports pyusb')

import inspect  # noqa: E402  (after importorskip by design)

from tools.tao3104a_cap import (  # noqa: E402  (after importorskip by design)
    CODE_0V,
    CODES_PER_DIV,
    CSV_HDR,
    SCREEN_PX_PER_DIV,
    Scope,
    calibrate_zero_code,
    chan_meta,
    frame_layout,
    legacy_zero_code,
    measure_json_values,
    measure_reply_value,
    robust_levels,
    selfcheck,
    time_axis,
    to_volts,
    unit,
    volts_per_code,
)

CH1_500MV = {'NAME': 'CH1', 'DISPLAY': 'ON', 'COUPLING': 'DC', 'PROBE': '1X',
             'SCALE': '500mV', 'OFFSET': -100}

# Live 2026-10-05: 1 V/div, 1X, OFFSET=-126, 0..5.08 V square wave. The frame
# itself sits at codes 64..194 (0.5/99.5 percentiles) and 0 V lands near code
# 65 - not at the legacy 179.5, which is what made the old model read -4.6 V
# where the instrument reads -0.04 V. LIVE_MEAS is the instrument's own reply.
CH1_LIVE_SQUARE = {'NAME': 'CH1', 'DISPLAY': 'ON', 'COUPLING': 'DC', 'PROBE': '1X',
                   'SCALE': '1V', 'OFFSET': -126}
LIVE_CODES = np.array([64.0] * 20 + [194.0] * 20)      # synthetic square frame
LIVE_MEAS = {'pkpk': 5.12, 'max': 5.08, 'min': -0.04,
             'period': 1e-3, 'frequency': 1000.0}


def head_500us(ch1=None):
    """HEAD shaped exactly like the live one (same keys, trimmed channel list)."""
    return {
        'MODEL': 'TAO3104A', 'DATATYPE': 'SCREEN', 'RUNSTATUS': 'TRIG',
        'TIMEBASE': {'SCALE': '500us', 'HOFFSET': 0},
        'SAMPLE': {'DATALEN': 1520, 'SAMPLERATE': '(1MSa/s)', 'DEPMEM': 10000},
        'CHANNEL': [dict(ch1 or CH1_500MV)],
    }


def head_live_depmem():
    """The live DEPMEM HEAD: 1 MSa/s, 9999 samples, 7600-sample screen window.

    DATATYPE is 'WAVEDEPMEM' - the command is ':DATA:WAVE:DEPMEM:*' but the
    HEAD does not echo 'DEPMEM'; frame_layout must accept both spellings.
    """
    head = head_500us(CH1_LIVE_SQUARE)
    head['DATATYPE'] = 'WAVEDEPMEM'
    head['SAMPLE'] = {'DATALEN': 10000, 'SAMPLERATE': '(1MSa/s)',
                      'DEPMEM': '10K', 'FULLSCREEN': 7600, 'SCREENOFFSET': 1200}
    return head



# ---- unit parser ----------------------------------------------------------
def test_unit_parser_handles_scope_labels():
    assert unit('500mV') == pytest.approx(0.5)
    assert unit('100mV') == pytest.approx(0.1)
    assert unit('500us') == pytest.approx(5e-4)
    assert unit('(1MSa/s)') == pytest.approx(1e6)   # M is mega, not milli
    assert unit('(50kSa/s)') == pytest.approx(5e4)
    assert unit('') == pytest.approx(1.0)
    assert unit(None) == pytest.approx(1.0)


def test_unit_parser_handles_signed_replies_and_khz():
    # instrument replies are signed ('-40.00mV') and frequency comes as 'KHz'
    assert unit('-40.00mV') == pytest.approx(-0.04)
    assert unit('5.120V') == pytest.approx(5.12)
    assert unit('1.000KHz') == pytest.approx(1e3)
    assert unit('1.000ms') == pytest.approx(1e-3)
    assert unit('50.29267') == pytest.approx(50.29267)    # FREQUENCE, no unit
    assert unit('10mV') == pytest.approx(0.01)


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


# ---- frame geometry: SCREEN is pair-packed, DEPMEM is uniform -------------
def test_screen_frame_is_pixel_pairs_not_a_time_series():
    head = head_500us()
    lay = frame_layout(head, 1520)
    assert lay['mode'] == 'SCREEN' and lay['pair_packed'] is True
    assert lay['pixels'] == 760 and lay['samples'] == 1520     # 1520 = 2 x 760
    assert lay['dt_s'] == pytest.approx(10e-6)                 # 500 us / 50 px
    assert lay['sample_dt_s'] == pytest.approx(5e-6)           # decimation step
    assert SCREEN_PX_PER_DIV == 50.0
    t, dt = time_axis(head, 1520)
    assert dt == pytest.approx(lay['dt_s'])                    # pixel period
    # both samples of a pixel share its time -> only 760 distinct times
    assert len(np.unique(t)) == 760
    assert t[0] == t[1] == 0.0
    assert t[2] == t[3] == pytest.approx(10e-6)
    assert t[-1] == pytest.approx(759 * 10e-6)


def test_screen_window_is_7_6_ms_not_5_ms():
    lay = frame_layout(head_500us(), 1520)
    assert lay['span_s'] == pytest.approx(7.6e-3)              # 760 px x 10 us
    assert lay['span_s'] == pytest.approx(760 * unit('500us') / 50.0, rel=1e-12)
    # the superseded 10*TB/n model called this same frame 5 ms wide
    assert lay['span_s'] != pytest.approx(5e-3, rel=1e-3)


def test_pixel_period_scales_with_timebase():
    for tb, px_dt in (('500us', 10e-6), ('200us', 4e-6), ('1ms', 20e-6)):
        head = head_500us()
        head['TIMEBASE']['SCALE'] = tb
        assert frame_layout(head, 1520)['dt_s'] == pytest.approx(px_dt, rel=1e-12)


def test_one_khz_spans_one_hundred_pixels():
    """The 1.52x defect: 200 SCREEN samples = 100 px = 1 ms, not 658 us."""
    t, _ = time_axis(head_500us(), 1520)
    period = t[200] - t[0]                    # 200 samples between 1 kHz edges
    assert period == pytest.approx(1e-3, rel=1e-12)
    assert 1.0 / period == pytest.approx(1000.0, rel=1e-9)
    old = 10 * unit('500us') / 1520 * 200     # superseded 10*TB/n model
    assert old == pytest.approx(6.5789e-4, rel=1e-4)
    assert (1.0 / old) / 1000.0 == pytest.approx(1.52, rel=1e-2)   # 1.52 kHz


def test_depmem_frame_is_uniform_1us():
    head = head_live_depmem()
    lay = frame_layout(head, 9999)                 # the live channel reply length
    assert lay['mode'] == 'DEPMEM' and lay['pair_packed'] is False
    assert lay['dt_s'] == pytest.approx(1e-6)                  # 1 MSa/s
    assert lay['sample_dt_s'] == pytest.approx(1e-6)
    assert lay['span_s'] == pytest.approx(9.999e-3)            # 9999 x 1 us
    # n defaults to the HEAD's DATALEN (10000) when there is no frame yet
    assert frame_layout(head)['span_s'] == pytest.approx(10e-3)
    # the cross-check: that capture's screen window is the SCREEN frame's span
    assert lay['screen_window_s'] == pytest.approx(7.6e-3)
    assert lay['screen_offset_samples'] == 1200
    assert lay['screen_window_s'] == pytest.approx(
        frame_layout(head_500us(), 1520)['span_s'], rel=1e-12)
    t, dt = time_axis(head, 9999)
    assert dt == pytest.approx(1e-6)
    assert len(np.unique(t)) == 9999
    assert t[1] - t[0] == pytest.approx(1e-6)


def test_frame_layout_rejects_unknown_datatype():
    head = head_500us()
    head['DATATYPE'] = 'ROLL'
    with pytest.raises(ValueError):
        frame_layout(head, 1520)


# ---- the path the tool actually walks -------------------------------------
def test_chan_meta_plus_model_on_live_head_shape():
    head = head_500us()
    meta = chan_meta(head, 'CH1')
    assert meta['SCALE'] == '500mV' and meta['OFFSET'] == -100
    assert to_volts(np.array([222.0]), meta)[0] == pytest.approx(0.330, abs=0.02)
    with pytest.raises(KeyError):
        chan_meta(head, 'CH3')


# ---- volts: scale convention and the instrument-anchored zero code --------
def test_volts_per_code_is_scale_over_25():
    assert CODES_PER_DIV == 25.0
    assert volts_per_code({'SCALE': '1V', 'PROBE': '1X'}) == pytest.approx(0.04)
    assert volts_per_code({'SCALE': '500mV', 'PROBE': '1X'}) == pytest.approx(0.02)
    assert volts_per_code({'SCALE': '1V', 'PROBE': '10X'}) == pytest.approx(0.4)


def test_legacy_zero_code_is_offset_plus_constant():
    assert legacy_zero_code(CH1_500MV) == pytest.approx(-100.0 + CODE_0V)
    assert legacy_zero_code(CH1_LIVE_SQUARE) == pytest.approx(-126.0 + CODE_0V)


def test_to_volts_uses_the_zero_code_it_is_given():
    codes = np.array([100.0, 200.0])
    assert to_volts(codes, CH1_LIVE_SQUARE, 100.0).tolist() == [0.0, 4.0]
    # omitting it falls back to the legacy constant (flagged by the callers)
    legacy = to_volts(codes, CH1_LIVE_SQUARE)
    assert legacy[0] == pytest.approx((100.0 - legacy_zero_code(CH1_LIVE_SQUARE)) * 0.04)


def test_live_frame_zero_code_comes_from_the_instrument():
    z, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS)
    assert info['zero_code_source'] == 'measurement_min'
    assert z == pytest.approx(65.0, abs=1.0)          # 0 V near code 65 live
    assert z != pytest.approx(legacy_zero_code(CH1_LIVE_SQUARE), rel=1e-2)
    v = to_volts(LIVE_CODES, CH1_LIVE_SQUARE, z)
    assert v.min() == pytest.approx(-0.04, abs=0.08)   # instrument MIN
    assert v.max() == pytest.approx(+5.08, abs=0.08)   # instrument MAX
    assert v.max() - v.min() == pytest.approx(5.12, abs=0.12)
    assert info['code_low'] == pytest.approx(64.0)
    assert info['code_high'] == pytest.approx(194.0)


def test_legacy_zero_code_misses_the_live_frame_by_4_6_v():
    z_legacy = legacy_zero_code(CH1_LIVE_SQUARE)
    assert z_legacy == pytest.approx(179.5)
    v = to_volts(LIVE_CODES, CH1_LIVE_SQUARE, z_legacy)
    assert v.min() == pytest.approx(-4.62, abs=0.05)   # instrument says -0.04
    assert v.min() - (-0.04) == pytest.approx(-4.58, abs=0.05)
    assert v.max() == pytest.approx(+0.58, abs=0.05)   # instrument says +5.08


def test_calibrate_without_measurements_flags_the_legacy_fallback():
    z, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, None)
    assert info['zero_code_source'] == 'legacy_constant'
    assert z == pytest.approx(legacy_zero_code(CH1_LIVE_SQUARE))
    assert info['scale_mode'] == 'nominal'
    assert info['min_scope'] is None and info['pkpk_scope'] is None
    assert selfcheck(info)['verdict'] == 'UNKNOWN'     # nothing to check against


def test_explicit_zero_code_wins_and_is_flagged():
    z, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS,
                                  zero_code=65.0)
    assert z == pytest.approx(65.0)
    assert info['zero_code_source'] == 'explicit'


def test_scale_mode_auto_prefers_the_two_point_fit_within_5pct():
    _, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS)
    assert info['scale_mode'] == 'fit'
    assert info['v_per_code'] == pytest.approx(LIVE_MEAS['pkpk'] / 130.0)
    assert info['v_per_code'] == pytest.approx(0.0394, abs=1e-4)
    assert abs(info['v_per_code'] / info['v_per_code_nominal'] - 1.0) < 0.05
    assert info['v_per_code_nominal'] == pytest.approx(0.04)
    _, forced = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS,
                                    scale_mode='nominal')
    assert forced['scale_mode'] == 'nominal'
    assert forced['v_per_code'] == pytest.approx(0.04)


def test_scale_mode_auto_rejects_an_implausible_fit():
    bad = dict(LIVE_MEAS, pkpk=6.5)       # +27 % off nominal: settings suspect
    _, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, bad)
    assert info['scale_mode'] == 'nominal'
    assert info['v_per_code'] == pytest.approx(0.04)
    _, forced = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, bad,
                                    scale_mode='fit')
    assert forced['scale_mode'] == 'fit'
    assert forced['v_per_code'] == pytest.approx(6.5 / 130.0)


def test_robust_levels_ignore_single_sample_glitches():
    # DEPMEM frames really do carry 10/255 spikes from cable transients; at 1 MSa/s
    # a 0.5 % percentile sits 50 samples inside the flat tops, so a couple of
    # spikes cannot move the levels the way raw min/max would.
    frame = np.concatenate([[10.0, 10.0], np.full(1000, 64.0),
                            np.full(1000, 194.0), [255.0, 255.0]])
    assert robust_levels(frame) == pytest.approx((64.0, 194.0))
    assert (float(frame.min()), float(frame.max())) == (10.0, 255.0)
    assert frame.max() - frame.min() == pytest.approx(245.0)   # what NOT to use
    with pytest.raises(ValueError):
        robust_levels(np.array([]))


# ---- selfcheck: the tool must agree with the instrument -------------------
def test_selfcheck_passes_on_the_live_frame():
    _, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS)
    chk = selfcheck(info)
    assert chk['verdict'] == 'PASS'
    for key in ('vmin', 'vmax', 'vpp'):
        assert chk[key]['ok'] is True
        assert abs(chk[key]['err_codes']) <= 3.0
    assert chk['zero_code_source'] == 'measurement_min'
    assert chk['scale_mode'] == 'fit'


def test_selfcheck_warns_when_the_frame_disagrees():
    # a frame decoded with the legacy zero code cannot match the instrument
    # (nominal scale so the arithmetic is the documented 0.04 V/code)
    _, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS,
                                  zero_code=legacy_zero_code(CH1_LIVE_SQUARE),
                                  scale_mode='nominal')
    chk = selfcheck(info)
    assert chk['verdict'] == 'WARN'
    assert chk['vmin']['ok'] is False
    assert chk['vmin']['err_codes'] == pytest.approx(-114.5, abs=0.5)
    assert chk['vmin']['err_v'] == pytest.approx(-4.58, abs=0.05)


def test_selfcheck_tolerance_is_configurable_in_codes():
    _, info = calibrate_zero_code(LIVE_CODES, CH1_LIVE_SQUARE, LIVE_MEAS,
                                  zero_code=66.5)         # 1.5 codes off
    assert selfcheck(info, tol_codes=3.0)['verdict'] == 'PASS'
    assert selfcheck(info, tol_codes=0.5)['verdict'] == 'WARN'
    assert selfcheck(info, tol_codes=0.5)['tol_codes'] == 0.5


def test_selfcheck_unknown_when_there_is_nothing_to_compare():
    info = {'v_per_code': 0.04, 'zero_code': 65.0, 'zero_code_source': 'legacy_constant',
            'scale_mode': 'nominal', 'vmin_tool': -0.04, 'vmax_tool': 5.08,
            'vpp_tool': 5.12, 'min_scope': None, 'max_scope': None,
            'pkpk_scope': None}
    chk = selfcheck(info)
    assert chk['verdict'] == 'UNKNOWN' and 'vmin' not in chk


# ---- :MEASUrement reply parsing -------------------------------------------
def test_measure_reply_value_reads_live_replies():
    assert measure_reply_value(
        b'\n:MEASUrement:CH1:PKPK?  -> Vpp : 5.120V->|') == pytest.approx(5.12)
    assert measure_reply_value(b'-> Ma : 5.080V->|') == pytest.approx(5.08)
    assert measure_reply_value(b'-> Mi : -40.00mV->|') == pytest.approx(-0.04)
    assert measure_reply_value(b'-> T : 1.000ms->|') == pytest.approx(1e-3)
    assert measure_reply_value(b'-> F : 1.000KHz->|') == pytest.approx(1e3)
    assert measure_reply_value(b'-> F : 50.29267->|') == pytest.approx(50.29267)


def test_measure_reply_value_none_when_the_firmware_is_silent():
    # VPP?, MEAN? and RMS? return nothing at all on V3.0.0
    assert measure_reply_value(b'') is None
    assert measure_reply_value(None) is None
    assert measure_reply_value(b'\n:MEASUrement:CH1:VPP?  ->|') is None
    assert measure_reply_value(b'->  ->|') is None
    # non-numeric text is "no measurement", not a crash: a capture must survive
    assert measure_reply_value(b'-> <<no reply>>') is None
    assert measure_reply_value(b'\n:MEASUrement:CH1:PKPK?  -> Vpp : ERROR->|') is None


# --- consumer contract (tools/shunt_cal_campaign.py, CSV readers) --------------

def test_scope_api_surface_used_by_consumers_is_intact():
    """tools/shunt_cal_campaign.py drives tao3104a_cap through Scope only.

    It calls Scope(), identify(), head(), waveform(ch, mode='SCREEN') and
    close() - the volts/time rewrite must not move that surface. (tools/
    tao3104a_soak.py carries its own Scope copy and is not affected at all.)
    """
    for name in ('identify', 'head', 'waveform', 'close'):
        assert callable(getattr(Scope, name)), name
    assert list(inspect.signature(Scope.__init__).parameters) == ['self']
    assert inspect.signature(Scope.head).parameters['mode'].default == 'SCREEN'
    assert inspect.signature(Scope.waveform).parameters['mode'].default == 'SCREEN'


def test_csv_header_contract_is_unchanged():
    # consumers (map_scope_ingest.parse_scope_csv, soak, campaign) read by name
    assert CSV_HDR == ['sample_index', 'time_s', 'ch1_v', 'ch2_v', 'ch3_v', 'ch4_v']


# ---- reply framing (live 2026-10-05: the format is NOT guaranteed) ---------
def test_measure_reply_value_reads_the_plain_framing():
    # Second live session, same scope and firmware: no echoed command and no
    # trailing '|' - the reply is b'Vpp : 5.120V->\n'. The shipped parser read
    # nothing at all here, so every capture fell back to the legacy zero code
    # (the v1 -4.66..+0.62 V frame) and --strict had to refuse the frame.
    assert measure_reply_value(b'Vpp : 5.120V->\n') == pytest.approx(5.12)
    assert measure_reply_value(b'Ma : 5.080V->\n') == pytest.approx(5.08)
    assert measure_reply_value(b'Mi : -40.00mV->\n') == pytest.approx(-0.04)
    assert measure_reply_value(b'T : 1.000ms->\n') == pytest.approx(1e-3)
    assert measure_reply_value(b'F : 1.000KHz->\n') == pytest.approx(1e3)
    # a bare value (no '<label> : ') is still a value: :TRIGger:SINGle:EDGe:LEVel?
    assert measure_reply_value(b'2.54V->\n') == pytest.approx(2.54)


def test_both_framings_of_the_same_reading_agree():
    pairs = ((b'Vpp : 5.120V->\n',
              b'\n:MEASUrement:CH1:PKPK?  -> Vpp : 5.120V->|'),
             (b'Mi : -40.00mV->\n',
              b'\n:MEASUrement:CH1:MIN?  -> Mi : -40.00mV->|'),
             (b'F : 1.000KHz->\n',
              b'\n:MEASUrement:CH1:FREQuency?  -> F : 1.000KHz->|'))
    for plain, echoed in pairs:
        assert measure_reply_value(plain) == pytest.approx(
            measure_reply_value(echoed))


def test_measure_json_values_maps_the_live_body():
    # Body of ':MEASUrement:CH1?' (live 2026-10-05, 645 B). The ',ON'/…',OFF'
    # panel flag only ever follows the value, it never replaces it.
    body = {'CH1': {'MAX': 'Ma : 5.080V', 'MIN': 'Mi : -40.00mV',
                    'PKPK': 'Vpp : 5.120V', 'PERiod': 'T : 1.000ms',
                    'FREQuency': 'F : 1.000KHz,ON', 'RTime': '<10.000us',
                    'AREA': ''}}
    vals = measure_json_values(body)
    assert vals['pkpk'] == pytest.approx(5.12)
    assert vals['max'] == pytest.approx(5.08)
    assert vals['min'] == pytest.approx(-0.04)
    assert vals['period'] == pytest.approx(1e-3)
    assert vals['frequency'] == pytest.approx(1e3)
    # an all-empty body means "no measurements": never a crash, never a fake 0
    empty = measure_json_values({'CH1': {}})
    assert [empty[k] for k in ('pkpk', 'max', 'min', 'period', 'frequency')] \
        == [None] * 5
    assert measure_json_values(None) is None


def test_scope_measure_falls_back_to_the_json_body(monkeypatch):
    # No USB device needed: the fallback is pure routing logic.
    sc = Scope.__new__(Scope)
    monkeypatch.setattr(sc, 'query', lambda cmd, timeout_s=0: b'')
    monkeypatch.setattr(sc, 'measure_json', lambda ch: dict(LIVE_MEAS))
    got = sc.measure('CH1')
    assert got['source'] == 'json'
    assert got['pkpk'] == pytest.approx(5.12)
    assert got['min'] == pytest.approx(-0.04)


def test_scope_measure_prefers_the_text_replies(monkeypatch):
    sc = Scope.__new__(Scope)
    monkeypatch.setattr(sc, 'query',
                        lambda cmd, timeout_s=0: b'Vpp : 5.120V->\n')

    def never(ch):
        raise AssertionError('JSON fallback must not run when text replies work')

    monkeypatch.setattr(sc, 'measure_json', never)
    got = sc.measure('CH1')
    assert got['source'] == 'text'
    assert got['pkpk'] == pytest.approx(5.12)
