#!/usr/bin/env python3
"""
TAO3104A + ACS712-5A: raw waveform capture for the OEW current-map pipeline.

SCOPE OF THIS TOOL (deliberately narrow):
  Timing / polarity / channel-chain qualification of the ACS712 -> TAO3104A
  measurement chain. It does NOT perform a quantitative current calibration
  and it does NOT produce map coefficients m00..m11.

  Reason: at bench currents 0.13..0.91 A the ACS712-20A delivered only
  13..91 mV of signal against ~100 mVpp of noise (see STEP_A_ACCEPTANCE).
  Quantitative scale qualification is reserved for TZ-REF-01.

CHANNEL MAP (single acquisition, one trigger):
  CH1 = PB6  - physical sync marker emitted by the firmware
  CH2 = ACS712 U - sensor output on phase U line
  CH3 = ACS712 V - sensor output on phase V line

UNIT CONTRACT (see map_scope_ingest.py):
  This tool writes RAW MILLIVOLTS. It never converts to amperes or
  milliamperes. The mV -> A -> mA conversion is owned by
  map_scope_ingest.py (ref_u_ma = (ref_u_mv - v0) / sens * 1000.0).

  Do NOT add a mV->mA conversion here. Doing so double-applies the gain
  and corrupts the solver input by a factor of 1000.

OUTPUT CSV (consumed by: map_scope_ingest.py --scope <file>):
  pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,scope_qualified,note
    pulse          : 1..N, sequential (parse_scope_csv requires row order)
    ref_u_mv       : mean ACS712 U output over the ADC aperture window, mV
    ref_v_mv       : mean ACS712 V output over the ADC aperture window, mV
    ref_w_mv       : always empty - W is KCL-derived inside the ingest tool
    margin_ticks   : measured switching aperture margin, timer ticks
    blanking_ticks : ADC blanking window, ticks (provenance passthrough)
    scope_qualified: real gate result (1 = all checks passed, 0 = REJECT)
    note           : free text; populated on every non-qualified row

CLI:
  --list                  enumerate USB devices
  --probe                 IDN + HEAD dump (no waveform)
  --g0                    PRECONDITION gate: IDN, HEAD, CH1/2/3 payload
                          completeness, repeat acquisition. Must PASS before
                          any physical measurement is authorised.
  --capture --out DIR     capture one acquisition -> CSV + preamble JSON
  --pwm-hz HZ             verified PWM frequency (REQUIRED for --capture).
                          Not defaulted: the correct value depends on the
                          real TIM1 PSC and counting mode and must be
                          confirmed against the firmware, not assumed.
  --timer-hz HZ           TIM1 counter clock, Hz (default 170e6)
  --blanking-ticks N      ADC blanking window, ticks (default 15)
  --min-margin-ticks N    minimum acceptable margin (default 110)
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

# pyusb is needed only for the hardware transport (Scope). The pure functions
# below - unit(), to_mv(), time_axis(), qualify(), measure_margin_ticks(),
# write_scope_csv() - must stay importable on a host without pyusb, otherwise
# the software-only tests cannot run in CI or on a clean checkout.
try:
    import usb
except ImportError:                                   # pragma: no cover
    usb = None

VID = 0x5345
PID = 0x1234
EP_IN = 0x81
EP_OUT = 0x03
PROMPT = b'->\n'

# Truncation floor observed on firmware V3.0.0 with the libusb0 driver on
# Windows: bulk-IN returns at most 2047 bytes (4x512 + 511 + ZLP) once the
# device has been running for a while. A power-cycle restores one full read.
TRUNC_SUSPECT = (2043, 2047)

RAIL_LOW = 0
RAIL_HIGH = 255

# Channel assignment. The project's existing no-HV checkout convention is
# CH1 = ACS712 U, CH2 = ACS712 V (docs/templates/acs712_nohv_checkout/
# README_ACS712_NOHV_PC3.md). This tool keeps that convention and puts the
# PB6 marker on CH3, so one wiring story holds across the whole project.
#
# The mapping is explicit and configurable because a silent mismatch is
# dangerous: an ACS712 current output is itself periodic at the PWM frequency,
# so a swapped marker channel can satisfy the periodicity and frequency gates
# and produce plausible-looking nonsense. MARKER_SPAN_MV below discriminates.
DEFAULT_U_CH = 'CH1'
DEFAULT_V_CH = 'CH2'
DEFAULT_SYNC_CH = 'CH3'

# A 3.3 V logic marker swings ~3300 mV. An ACS712-5A output is nominally ~185 mV/A,
# i.e. <=1000 mV even at 10 A. 1500 mV separates the two without guessing.
MARKER_SPAN_MV = 1500.0
DEFAULT_SENSOR_SENS_MV_PER_A = 185.0


def unit(text):
    """Parse a scope quantity string. Returns 0.0 on failure."""
    if text is None:
        return 0.0
    s = str(text).strip()
    if not s:
        return 0.0
    m = re.search(r'([0-9.]+)\s*([kKmMuUnngG]?)\s*Sa/s', s)
    if m:
        v = float(m.group(1))
        return v * {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'u': 1e-6, 'n': 1e-9,
                    'M': 1e6, 'G': 1e9}.get(m.group(2), 1.0)
    m = re.match(r'^\s*\(?([0-9.]+)\s*([kKmMuUnngG]?)', s)
    if not m:
        return 0.0
    v = float(m.group(1))
    return v * {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'u': 1e-6, 'n': 1e-9,
                'M': 1e6, 'G': 1e9}.get(m.group(2), 1.0)


class ScopeError(RuntimeError):
    pass


def _require_usb():
    """Fail with a clear message when pyusb is absent, instead of an AttributeError."""
    if usb is None:
        raise ScopeError(
            'pyusb is not installed - USB transport unavailable. '
            'Install it with: python -m pip install -r tools/requirements-acs712-scope.txt')


class Scope:
    """Raw bulk transport for TAO3104A V3.0.0 (libusb-win32)."""

    def __init__(self):
        _require_usb()
        d = usb.core.find(idVendor=VID, idProduct=PID)
        if d is None:
            raise ScopeError('TAO3104A not found (VID %04x PID %04x)' % (VID, PID))
        self.dev = d
        self.idn = None
        try:
            d.set_configuration()
        except usb.core.USBError:
            pass
        usb.util.claim_interface(d, 0)

    def close(self):
        try:
            usb.util.release_interface(self.dev, 0)
        except Exception:
            pass

    def _read(self, timeout_ms):
        try:
            return bytes(self.dev.read(EP_IN, 512, timeout=timeout_ms))
        except usb.core.USBError:
            return b''

    def _read_until(self, deadline_s):
        got = b''
        while time.time() < deadline_s:
            got += self._read(1500)
            if PROMPT in got:
                break
        return got

    def query(self, cmd, timeout_s=10.0):
        self.dev.write(EP_OUT, (cmd + '\r\n').encode(), timeout=5000)
        return self._read_until(time.time() + timeout_s)

    def bulk(self, cmd, timeout_s=20.0, maxbytes=1 << 18):
        self.dev.write(EP_OUT, (cmd + '\r\n').encode(), timeout=5000)
        t0 = time.time()
        got = b''
        while time.time() - t0 < timeout_s and len(got) < maxbytes:
            chunk = self._read(500)
            if chunk:
                got += chunk
                if len(got) >= 4:
                    ln = int.from_bytes(got[:4], 'little')
                    if 4 <= ln < maxbytes and len(got) >= 4 + ln:
                        break
            else:
                if len(got) >= 4:
                    ln = int.from_bytes(got[:4], 'little')
                    if 4 <= ln < maxbytes and len(got) >= 4 + ln:
                        break
        return got

    def identify(self):
        r = self.query('*IDN?\r\n', timeout_s=5.0)
        if not r:
            raise ScopeError('no response to *IDN? - scope dead or wrong driver')
        self.idn = r[:-len(PROMPT)].decode(errors='replace').strip()
        return self.idn

    def head(self, mode='SCREEN'):
        raw = self.bulk(':DATA:WAVE:%s:HEAD?' % mode, timeout_s=15.0)
        if len(raw) < 6:
            raise ScopeError('HEAD read failed: %d bytes' % len(raw))
        ln = int.from_bytes(raw[:4], 'little')
        body = raw[4:4 + ln]
        try:
            return json.loads(body.decode('utf-8'))
        except Exception as e:
            raise ScopeError('HEAD JSON parse failed: %s (got %d B)' % (e, len(body)))

    def waveform(self, ch, mode='SCREEN'):
        """Read one channel. Raises on short frame or suspected USB truncation."""
        raw = self.bulk(':DATA:WAVE:%s:%s?' % (mode, ch), timeout_s=15.0)
        if len(raw) < 6:
            raise ScopeError('%s read failed: %d bytes' % (ch, len(raw)))
        ln = int.from_bytes(raw[:4], 'little')
        got = len(raw) - 4
        if ln < 2 or len(raw) < 4 + ln:
            if got in TRUNC_SUSPECT and ln > got + 100:
                raise ScopeError(
                    '%s USB bulk-IN truncated: declared %d B, got %d B. '
                    'Firmware V3.0.0 / libusb0 defect. Power-cycle the scope '
                    'and repeat the acquisition.' % (ch, ln, got))
            raise ScopeError('%s short frame: declared %d B, got %d B'
                             % (ch, ln, got))
        container = np.frombuffer(raw[4:4 + ln], dtype='<u2')
        return (container >> 8).astype(np.int32)


def chan_meta(head, ch):
    for c in head.get('CHANNEL', []):
        if c.get('NAME') == ch:
            return c
    raise ScopeError('channel %s missing from HEAD' % ch)


def to_mv(codes, meta):
    """Convert 8-bit codes to millivolts using the channel preamble."""
    scale = unit(meta['SCALE'])              # V/div (e.g. 1.0 for 1 V/div)
    if scale == 0.0:
        scale = 1.0
    probe_s = str(meta.get('PROBE', '1X')).strip().upper()
    probe = 10.0 if probe_s.startswith('10') else 1.0
    off = float(meta.get('OFFSET', 0))
    return (codes - off) * (scale / 25.0) * probe * 1000.0   # V -> mV


def time_axis(head, n):
    """Return (t_seconds, sample_rate_hz, consistency_report)."""
    timebase_s = unit(head['TIMEBASE']['SCALE'])   # s/div
    span_s = timebase_s * 10.0                     # 10 horizontal divisions
    datalen = int(head['SAMPLE']['DATALEN'])
    sr_label = unit(head['SAMPLE']['SAMPLERATE'])

    sr_span = (datalen / span_s) if (datalen > 0 and span_s > 0) else 0.0
    report = {'sr_from_span': sr_span, 'sr_from_label': sr_label,
              'timebase_s_per_div': timebase_s, 'datalen': datalen,
              'consistent': False, 'ratio': None}

    if sr_span > 0 and sr_label > 0:
        ratio = sr_span / sr_label
        report['ratio'] = ratio
        report['consistent'] = 0.8 <= ratio <= 1.25
        sr = sr_span
    else:
        sr = sr_label or sr_span
    if sr <= 0:
        raise ScopeError('cannot determine sample rate from HEAD')
    return np.arange(n, dtype=float) / sr, sr, report


def edges(sig, thresh, rising=True):
    """Index list of threshold crossings."""
    above = sig >= thresh
    if rising:
        idx = np.flatnonzero(~above[:-1] & above[1:])
    else:
        idx = np.flatnonzero(above[:-1] & ~above[1:])
    return idx + 1


def qualify(chans, sr, pwm_hz, margin_ticks, min_margin_ticks):
    """
    Real waveform qualification. Returns (qualified:int, checks:dict, notes:list).

    chans = {'sync': {'codes':..., 'mv':...}, 'u': {...}, 'v': {...}}
    Rail/clipping is judged on the raw 8-bit CODES, not on converted mV:
    comparing mV against a code rail constant is a unit error.

    This replaces the old '|sync - 2500 mV| > 500' amplitude sniff, which any
    DC level away from mid-rail could pass and which therefore qualified
    nothing about periodicity, shape, clipping or channel identity.
    """
    checks = {}
    notes = []

    c_sync = chans['sync']['codes']
    c_u = chans['u']['codes']
    c_v = chans['v']['codes']
    mv_sync = chans['sync']['mv']
    mv_u = chans['u']['mv']
    mv_v = chans['v']['mv']

    # 1. payload completeness / equal channel lengths
    lens = (len(mv_sync), len(mv_u), len(mv_v))
    checks['payload_equal_length'] = len(set(lens)) == 1
    if not checks['payload_equal_length']:
        notes.append('channel length mismatch %s' % (lens,))

    # 2. minimum sample count for a usable aperture estimate
    checks['sufficient_samples'] = min(lens) >= 64
    if not checks['sufficient_samples']:
        notes.append('too few samples: %d' % min(lens))

    # 3. no ADC rail clipping (judged on raw codes)
    clip_u = int(np.count_nonzero((c_u <= RAIL_LOW) | (c_u >= RAIL_HIGH)))
    clip_v = int(np.count_nonzero((c_v <= RAIL_LOW) | (c_v >= RAIL_HIGH)))
    clip_s = int(np.count_nonzero((c_sync <= RAIL_LOW) | (c_sync >= RAIL_HIGH)))
    checks['no_clipping'] = (clip_u + clip_v + clip_s) == 0
    if not checks['no_clipping']:
        notes.append('clipping u=%d v=%d sync=%d' % (clip_u, clip_v, clip_s))

    # 4. the marker channel must look like a logic marker, not like a current
    #    output. A swapped ACS712 channel is periodic at the PWM frequency too,
    #    so periodicity alone cannot tell the two apart - signal span can.
    s_pp = float(mv_sync.max() - mv_sync.min())
    checks['marker_is_logic_signal'] = s_pp >= MARKER_SPAN_MV
    if not checks['marker_is_logic_signal']:
        notes.append('marker span %.1f mV < %.0f mV - looks like an ACS712 '
                     'output, check wiring' % (s_pp, MARKER_SPAN_MV))

    mid = float((mv_sync.max() + mv_sync.min()) / 2.0)
    rise = edges(mv_sync, mid, rising=True)
    checks['sync_periodic'] = len(rise) >= 3
    meas_pwm = None
    if checks['sync_periodic']:
        periods = np.diff(t_of(rise, sr))
        periods = periods[periods > 0]
        if len(periods):
            p_med = float(np.median(periods))
            meas_pwm = (1.0 / p_med) if p_med > 0 else None

    # 5. measured marker frequency must match the DECLARED, firmware-verified
    #    PWM frequency. The declared value is an input, never an assumption
    #    derived from ARR alone (center-aligned adds a factor of 2).
    checks['pwm_frequency_match'] = (
        meas_pwm is not None and abs(meas_pwm - pwm_hz) <= 0.10 * pwm_hz)
    if not checks['pwm_frequency_match']:
        notes.append('sync freq %s Hz vs declared %s Hz' % (
            '%.3f' % meas_pwm if meas_pwm else 'n/a', pwm_hz))

    # 6. ACS712 channels carry actual signal, and are NOT the logic marker
    u_span = float(mv_u.max() - mv_u.min())
    v_span = float(mv_v.max() - mv_v.min())
    checks['current_channels_present'] = (u_span >= 5.0 and v_span >= 5.0)
    if not checks['current_channels_present']:
        notes.append('flat current channel: u_span=%.1f v_span=%.1f mV'
                     % (u_span, v_span))
    checks['current_channels_not_marker'] = (
        u_span < MARKER_SPAN_MV and v_span < MARKER_SPAN_MV)
    if not checks['current_channels_not_marker']:
        notes.append('current channel span u=%.1f v=%.1f mV >= %.0f mV - '
                     'looks like the logic marker, check wiring'
                     % (u_span, v_span, MARKER_SPAN_MV))

    # 7. switching edge visible on the current channel (drives the aperture)
    sw = edges(mv_u, float(np.percentile(mv_u, 90)), rising=True)
    checks['switching_visible'] = len(sw) >= 1
    if not checks['switching_visible']:
        notes.append('no switching edges detected on CH2')

    # 8. aperture margin present and above the map's minimum
    if margin_ticks is None:
        checks['margin_above_minimum'] = False
        notes.append('margin not measurable')
    else:
        checks['margin_above_minimum'] = margin_ticks >= min_margin_ticks
        if not checks['margin_above_minimum']:
            notes.append('margin %.1f < %d ticks' % (margin_ticks, min_margin_ticks))

    qualified = all(checks.values())
    return (1 if qualified else 0), checks, notes


def t_of(idx, sr):
    """Sample indices -> seconds."""
    return np.asarray(idx, dtype=float) / float(sr)


def measure_margin_ticks(ch_u, ch_sync, sr, timer_hz):
    """
    Aperture margin = time from the PB6 marker edge to the nearest switching
    edge on the phase-current channel, expressed in TIM1 ticks.

    Definition note: this measures the PHYSICAL PB6 marker. Equating PB6 with
    the internal TIM1 TRGO / ADC trigger is a separate claim that must be
    proven from the firmware, not assumed from this waveform.
    """
    mid = float((ch_sync.max() + ch_sync.min()) / 2.0)
    rise = edges(ch_sync, mid, rising=True)
    if len(rise) < 2:
        return None, 'no periodic sync edges'

    lo = float(np.percentile(ch_u, 10))
    hi = float(np.percentile(ch_u, 90))
    span = hi - lo
    if span < 5.0:
        return None, 'no switching activity on CH2'
    sw = edges(ch_u, (lo + hi) / 2.0, rising=True)
    sw = sw[(sw > 0) & (sw < len(ch_u))]
    if len(sw) == 0:
        return None, 'no switching edges'

    t_sw = sw / float(sr)
    t_sync = rise / float(sr)
    per_tick = 1.0 / float(timer_hz)

    margins = []
    for ts in t_sync:
        d = np.abs(t_sw - ts)
        margins.append(float(np.min(d)))
    if not margins:
        return None, 'no margin candidates'
    margin_s = float(np.median(margins))
    return margin_s / per_tick, None


def write_scope_csv(path, pulses, mv_u, mv_v, margin_ticks, blanking_ticks,
                    qualified, note):
    """
    Write the CSV consumed by map_scope_ingest.py --scope.

    Values are emitted as INTEGERS: parse_scope_csv() reads every numeric field
    through int(), so a float such as '2680.0000' is rejected as 'не int'.
    Quantisation to 1 mV equals 10 mA at 100 mV/A, i.e. one tenth of the
    ACS712 noise floor, so no meaningful resolution is lost.

    ref_w_mv is always left empty on purpose: W is KCL-derived inside the
    ingest tool and must not be supplied independently here.
    """
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['pulse', 'ref_u_mv', 'ref_v_mv', 'ref_w_mv',
                    'margin_ticks', 'blanking_ticks', 'scope_qualified',
                    'note'])
        for i, p in enumerate(pulses, start=1):
            w.writerow([
                i,
                '%d' % round(float(mv_u[p])),
                '%d' % round(float(mv_v[p])),
                '',                                   # W is KCL-derived
                '%d' % round(margin_ticks) if margin_ticks is not None else '',
                int(blanking_ticks),
                int(qualified),
                note if qualified == 0 else '',
            ])


def cmd_list(args):
    _require_usb()
    d = usb.core.find(idVendor=VID, idProduct=PID)
    print('USB: %s' % ('found VID %04x PID %04x' % (VID, PID) if d else 'NOT FOUND'))
    if d:
        try:
            print('  product :', usb.util.get_string(d, d.iProduct))
        except Exception:
            print('  product : <unreadable>')
        print('  driver  : libusb-win32 (raw bulk) - pyvisa cannot open it')
    return 0


def cmd_probe(args):
    sc = Scope()
    try:
        print('IDN =', sc.identify())
        h = sc.head()
        print('DATATYPE =', h.get('DATATYPE'), 'RUNSTATUS =', h.get('RUNSTATUS'))
        print('DATALEN  =', h['SAMPLE']['DATALEN'],
              'SAMPLERATE =', h['SAMPLE']['SAMPLERATE'])
        for c in h['CHANNEL']:
            if c.get('DISPLAY', 'OFF') == 'ON':
                print('  %s: scale=%-8s probe=%-4s offset=%-6s freq=%s Hz' % (
                    c.get('NAME', '?'), c.get('SCALE'), c.get('PROBE'),
                    c.get('OFFSET'), c.get('FREQUENCE')))
    finally:
        sc.close()
    return 0


def cmd_g0(args):
    """
    PRECONDITION gate. Nothing physical may be measured until this passes.
    A perfectly formatted CSV produced from a partially broken acquisition
    is worse than no CSV, because it looks like evidence.
    """
    print('=== G0 precondition gate ===')
    results = []

    def rec(name, ok, detail=''):
        results.append((name, bool(ok), detail))
        print('  [%s] %-28s %s' % ('PASS' if ok else 'FAIL', name, detail))

    sc = None
    try:
        sc = Scope()
        sc.identify()
        rec('IDN', True, sc.idn)

        h = sc.head()
        rec('HEAD', True, 'DATALEN=%s SAMPLERATE=%s' % (
            h['SAMPLE']['DATALEN'], h['SAMPLE']['SAMPLERATE']))

        for ch in (args.sync_ch, args.u_ch, args.v_ch):
            try:
                w = sc.waveform(ch)
                rec('%s payload' % ch, len(w) > 64, '%d samples' % len(w))
            except ScopeError as e:
                rec('%s payload' % ch, False, str(e))

        # repeat acquisition: a single good read after power-cycle is not
        # evidence of a stable chain
        for ch in (args.sync_ch, args.u_ch, args.v_ch):
            try:
                w = sc.waveform(ch)
                rec('%s repeat' % ch, len(w) > 64, '%d samples' % len(w))
            except ScopeError as e:
                rec('%s repeat' % ch, False, str(e))
    except ScopeError as e:
        rec('transport', False, str(e))
    finally:
        if sc:
            sc.close()

    ok = all(r[1] for r in results)
    print('=== G0: %s ===' % ('PASS' if ok else 'FAIL'))
    if not ok:
        print('Do NOT run --capture. Power-cycle the scope and repeat --g0.')
    return 0 if ok else 1


def cmd_capture(args):
    if not args.pwm_hz:
        print('ERROR: --pwm-hz is required for --capture.')
        print('The PWM frequency depends on the real TIM1 PSC and counting')
        print('mode (center-aligned adds a factor of 2). Read it from the')
        print('firmware configuration; do not assume it from ARR alone.')
        return 2

    sc = Scope()
    out_dir = Path(args.out or 'scope_capture')
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        print('IDN =', sc.identify())
        h = sc.head()
        print('HEAD: DATALEN=%s SAMPLERATE=%s TIMEBASE=%s' % (
            h['SAMPLE']['DATALEN'], h['SAMPLE']['SAMPLERATE'],
            h['TIMEBASE']['SCALE']))

        codes_sync = sc.waveform(args.sync_ch)
        codes_u = sc.waveform(args.u_ch)
        codes_v = sc.waveform(args.v_ch)

        mv_sync = to_mv(codes_sync, chan_meta(h, args.sync_ch))
        mv_u = to_mv(codes_u, chan_meta(h, args.u_ch))
        mv_v = to_mv(codes_v, chan_meta(h, args.v_ch))
        print('wiring: %s=PB6 marker, %s=ACS712 U, %s=ACS712 V'
              % (args.sync_ch, args.u_ch, args.v_ch))

        t, sr, sr_report = time_axis(h, len(mv_sync))
        n = min(len(mv_sync), len(mv_u), len(mv_v))
        print('Points: %d  sr=%.0f Sa/s  span=%.3f ms' % (n, sr, t[n - 1] * 1e3))
        if not sr_report['consistent']:
            print('WARNING: sample rate inconsistent: from span %.0f Sa/s vs '
                  'label %.0f Sa/s (ratio %s)' % (
                      sr_report['sr_from_span'], sr_report['sr_from_label'],
                      '%.3f' % sr_report['ratio'] if sr_report['ratio'] else 'n/a'))

        chans = {
            'sync': {'codes': codes_sync[:n], 'mv': mv_sync[:n]},
            'u': {'codes': codes_u[:n], 'mv': mv_u[:n]},
            'v': {'codes': codes_v[:n], 'mv': mv_v[:n]},
        }

        margin_ticks, margin_err = measure_margin_ticks(
            mv_u[:n], mv_sync[:n], sr, args.timer_hz)
        if margin_err:
            print('margin: NOT computed - %s' % margin_err)

        qualified, checks, notes = qualify(
            chans, sr, args.pwm_hz, margin_ticks, args.min_margin_ticks)

        row_note = '; '.join(notes)[:200]

        # one row per detected marker pulse; values are RAW mV
        mid = float((mv_sync[:n].max() + mv_sync[:n].min()) / 2.0)
        pulses = edges(mv_sync[:n], mid, rising=True)
        if len(pulses) == 0:
            pulses = np.array([0])
        pulses = pulses[pulses < n]
        csv_path = out_dir / 'scope_capture.csv'
        write_scope_csv(csv_path, pulses, mv_u[:n], mv_v[:n],
                        margin_ticks, args.blanking_ticks, qualified, row_note)
        print('Saved:', csv_path, '(%d pulses)' % len(pulses))
        print('qualified =', qualified)
        for k, v in checks.items():
            print('   %-28s %s' % (k, 'PASS' if v else 'FAIL'))

        pre_path = out_dir / 'scope_capture.preamble.json'
        with open(pre_path, 'w', encoding='utf-8') as f:
            json.dump({
                'idn': sc.idn,
                'head': h,
                'declared_pwm_hz': args.pwm_hz,
                'declared_timer_hz': args.timer_hz,
                'blanking_ticks': args.blanking_ticks,
                'min_margin_ticks': args.min_margin_ticks,
                'channel_map': {'marker': args.sync_ch, 'u': args.u_ch,
                                'v': args.v_ch},
                'n_points': n,
                'n_pulses': int(len(pulses)),
                'sample_rate_hz': sr,
                'sample_rate_report': sr_report,
                'margin_ticks': margin_ticks,
                'margin_error': margin_err,
                'qualified': qualified,
                'checks': checks,
                'notes': notes,
                'units': 'raw millivolts; mV->mA conversion owned by map_scope_ingest.py',
                'pb6_trgo_correspondence': 'NOT ESTABLISHED by this tool',
                'quantitative_current_reference': 'NOT QUALIFIED (see TZ-REF-01)',
            }, f, ensure_ascii=False, indent=1)
        print('Saved:', pre_path)
        return 0 if qualified == 1 else 1

    finally:
        sc.close()


def cmd_phase0(args):
    """
    Phase-0 zero-current characterisation (TZ-REF-01 §4).

    Records the noise floor of the complete measurement chain
    (ACS712 + scope channel + probe) WITHOUT any PWM running.
    No periodic marker is expected, so qualify() gates are bypassed;
    the output is a JSON characterisation file, not a CSV.

    Gate semantics: this is NOT a --capture run; --pwm-hz is irrelevant here.
    The caller must not apply these statistics as if they were captured with
    the motor energised.
    """
    sc = Scope()
    out_dir = Path(args.out or '.tzref01_phase0')
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        idn = sc.identify()
        h = sc.head()

        characterisation = {}

        for ch_name, ch_label in [
                ('marker', args.sync_ch),
                ('u',      args.u_ch),
                ('v',      args.v_ch)]:
            codes = sc.waveform(ch_label)
            meta = chan_meta(h, ch_label)
            mv = to_mv(codes, meta)
            v_mean = float(np.mean(mv))
            v_min  = float(np.min(mv))
            v_max  = float(np.max(mv))
            v_pp   = v_max - v_min
            v_rms  = float(np.std(mv, ddof=1))   # sample std-dev, mV
            t, sr, _ = time_axis(h, len(mv))
            window_s = t[-1] if len(t) > 1 else 0.0

            characterisation[ch_label] = {
                'role': ch_label,
                'physical_label': ch_name,
                'codes_n': int(len(codes)),
                'v_mean_mv': round(v_mean, 3),
                'v_min_mv':  round(v_min,  3),
                'v_max_mv':  round(v_max,  3),
                'noise_vpp_mv': round(v_pp,  3),
                'noise_vrms_mv': round(v_rms, 3),
                'sample_rate_hz': sr,
                'window_duration_s': round(window_s, 6),
                'scale': meta.get('SCALE'),
                'probe': meta.get('PROBE'),
                'offset': meta.get('OFFSET'),
                'unit_raw': 'mV; ACS712-5A nominal sensitivity is configurable via --sens-mv-per-a',
            }

        manifest = {
            'idn': idn,
            'scope_head': h,
            'declared_timer_hz': args.timer_hz,
            'channel_map': {
                'marker': args.sync_ch,
                'u':      args.u_ch,
                'v':      args.v_ch,
            },
            'vcc_acs712_mv': args.vcc_mv,
            'acs712_sensitivity_mv_per_a': args.sens_mv_per_a,
            'map_control_range_a': {'min': 1.0, 'max': 3.0},
            'below_map_control': 'V/F',
            'characterisation': characterisation,
            'phase': 'PHASE0',
            'note': (
                'Zero-current characterisation only. Motor de-energised, '
                'MOE=0, PWM closed. No PWM marker present; --pwm-hz is '
                'irrelevant. These statistics are NOT evidence of SNR under '
                'load; they characterise only the electronic noise floor of '
                'the chain ACS712+scope channel+probe at the moment of '
                'capture. vcc_acs712_mv is the nominal external supply, '
                'measured separately by the operator before this run.'
            ),
        }

        # Conservative zero-noise budget for the whole chain (worst channel):
        # use whichever channel gives the larger noise estimate as the safe floor.
        noise_budget = max(d['noise_vpp_mv'] for d in characterisation.values())
        equiv_ma_pp  = noise_budget / args.sens_mv_per_a * 1000.0   # mA·pp at sens=100 mV/A nominal
        equiv_ma_rms = (max(d['noise_vrms_mv'] for d in characterisation.values())
                        / args.sens_mv_per_a * 1000.0)              # mA·RMS at sens=100 mV/A nominal
        for d in characterisation.values():
            d['zero_noise_pp_ma']  = round(equiv_ma_pp,  3)
            d['zero_noise_rms_ma'] = round(equiv_ma_rms, 3)

        # WRITE JSON AFTER all fields are computed
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        print('Saved:', out_path)

        # Pretty-print per-channel summary
        print()
        print('=== Phase-0 zero-current characterisation ===')
        print('IDN:', idn)
        for ch, d in characterisation.items():
            print()
            print('  %s (%s):' % (ch, d['physical_label']))
            print('    scale=%s probe=%s offset=%s' % (
                d['scale'], d['probe'], d['offset']))
            print('    v_mean=%.3f mV   noise_vpp=%.3f mV   noise_vrms=%.3f mV'
                  % (d['v_mean_mv'], d['noise_vpp_mv'], d['noise_vrms_mv']))
            print('    samples=%d  sr=%.0f Hz  window=%.3f s'
                  % (d['codes_n'], d['sample_rate_hz'], d['window_duration_s']))

        print('  Conservative noise floor: %.3f mVpp = %.1f mApp(pp)  %.3f mVrms = %.1f mApp(rms)'
              % (noise_budget, equiv_ma_pp,
                 max(d['noise_vrms_mv'] for d in characterisation.values()), equiv_ma_rms))
        print('  vcc_acs712 (nominal, operator-supplied): %s mV'
              % (args.vcc_mv if args.vcc_mv else '<not supplied>'))
        print()
        print('  NOTE: this data is PHASE0 only. Quantitative gate depends on')
        print('  Phase 0 session completion per TZ-REF-01 §4.3.')
        return 0

    finally:
        sc.close()


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--list', action='store_true', help='enumerate USB devices')
    p.add_argument('--probe', action='store_true', help='IDN + HEAD dump')
    p.add_argument('--g0', action='store_true',
                   help='precondition gate (must PASS before measurement)')
    p.add_argument('--capture', action='store_true',
                   help='capture one acquisition -> CSV (requires --pwm-hz)')
    p.add_argument('--phase0', action='store_true',
                   help='Phase-0 zero-current characterisation (TZ-REF-01 §4)')
    p.add_argument('--out', type=Path, default=None,
                   help='output directory (default: scope_capture/ or .tzref01_phase0/)')
    p.add_argument('--vcc-mv', type=float, default=None,
                   help='measured ACS712 Vcc, mV (used in --phase0 output)')
    p.add_argument('--sens-mv-per-a', type=float, default=DEFAULT_SENSOR_SENS_MV_PER_A,
                   help='ACS712 sensitivity used for Phase-0 noise conversion, mV/A (default 185 for ACS712-5A)')
    p.add_argument('--pwm-hz', type=float, default=None,
                   help='verified PWM frequency, Hz (required for --capture)')
    p.add_argument('--timer-hz', type=float, default=170e6,
                   help='TIM1 counter clock, Hz (default 170e6)')
    p.add_argument('--blanking-ticks', type=int, default=15,
                   help='ADC blanking window, ticks (default 15)')
    p.add_argument('--min-margin-ticks', type=int, default=110,
                   help='minimum acceptable aperture margin (default 110)')
    p.add_argument('--sync-ch', default=DEFAULT_SYNC_CH,
                   choices=['CH1', 'CH2', 'CH3', 'CH4'],
                   help='channel carrying the PB6 marker (default %s)' % DEFAULT_SYNC_CH)
    p.add_argument('--u-ch', default=DEFAULT_U_CH,
                   choices=['CH1', 'CH2', 'CH3', 'CH4'],
                   help='channel carrying ACS712 U (default %s)' % DEFAULT_U_CH)
    p.add_argument('--v-ch', default=DEFAULT_V_CH,
                   choices=['CH1', 'CH2', 'CH3', 'CH4'],
                   help='channel carrying ACS712 V (default %s)' % DEFAULT_V_CH)
    a = p.parse_args()

    if len({a.sync_ch, a.u_ch, a.v_ch}) != 3:
        p.error('--sync-ch/--u-ch/--v-ch must be three different channels')

    if a.list:
        return _dispatch(cmd_list, a)
    if a.probe:
        return _dispatch(cmd_probe, a)
    if a.g0:
        return _dispatch(cmd_g0, a)
    if a.capture:
        return _dispatch(cmd_capture, a)
    if a.phase0:
        return _dispatch(cmd_phase0, a)
    p.print_help()
    return 2


def _dispatch(fn, args):
    """Run a command, turning hardware-layer failures into a clean message."""
    try:
        return fn(args)
    except ScopeError as e:
        print('ERROR: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
