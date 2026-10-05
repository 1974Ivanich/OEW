#!/usr/bin/env python3
"""OWON TAO3104A raw-USB waveform capture (verified against V3.0.0 firmware).

Replaces the invalid pyVISA/:WAV:* package. Speaks plain text over the libusb
bulk endpoints; no USBTMC, no VISA.

CLI: --list --probe --measure --head --capture --campaign

The two frame types are NOT interchangeable (TZ: docs/TZ_TAO3104A_VOLTS_V2.md):

* SCREEN - 1520 samples = 760 pixels x [max, min] (peak detect over the screen
  window). This is NOT a uniform time series: both samples of a pixel share the
  pixel's time, so periodicity has to be read off the pixel grid (10 us/px at
  500 us/div) and never by counting samples between edges (that over-reports by
  1.52x).
* DEPMEM - the 10 kSa memory, uniform: SAMPLE.SAMPLERATE = 1 MSa/s so dt = 1 us
  (verified live). HEAD reports DATATYPE 'WAVEDEPMEM', DATALEN 10000 while the
  channel reply carries 9999 samples, and the screen window inside it as
  SAMPLE.FULLSCREEN = 7600 samples at SCREENOFFSET = 1200 - i.e. the same 7.6 ms
  the SCREEN frame covers. Use it for timing.

Volts: 25 codes per division, so one code is SCALE/25 * PROBE. The zero-volt
code is calibrated per frame against the instrument's own :MEASUrement:CHx:MIN?
and :MAX? readings (fallback: the legacy CODE_0V constant, flagged in the
sidecar). Every capture is compared with the instrument's own numbers by
selfcheck(); --strict turns a mismatch into a non-zero exit.
"""
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import usb

VID = 0x5345
PID = 0x1234
EP_IN = 0x81
EP_OUT = 0x03
PROMPT = b'->\n'
CSV_HDR = ['sample_index', 'time_s', 'ch1_v', 'ch2_v', 'ch3_v', 'ch4_v']
# Vertical convention: 25 codes per division (verified - a 0..5.08 V square
# wave at 1 V/div spans 128 codes = 5.12 V, which is the instrument's own PKPK).
CODES_PER_DIV = 25.0
# LEGACY zero-volt code: 0 V sits at OFFSET + CODE_0V. Fitted 2026-09-26
# (SN 2306027, V3.0.0) on ONE configuration (500 mV/div, 1X, OFFSET=-100, where
# code 222 = +0.330 V). It does not carry over - live 2026-10-05 (1 V/div, 1X,
# OFFSET=-126, 0..5.08 V square wave, instrument MIN=-40 mV, MAX=+5.08 V,
# PKPK=5.12 V) puts 0 V near code 65 while this constant predicts 179.5, so the
# frame decodes to -4.66..+0.62 V instead of -0.04..+5.08 V (+4.66 V = 117
# codes of error). Kept only as the fallback when the instrument's own
# measurements cannot be read; such frames are marked
# zero_code_source='legacy_constant' and fail selfcheck.
CODE_0V = 305.5
# Horizontal geometry of a SCREEN frame, measured live 2026-10-05 at 500 us/div:
# the 1520-sample frame covers 7.6 ms = 760 pixels of 10 us each (50 px per
# division). Cross-checks from the same session: the 1 kHz signal repeats every
# 200 SCREEN samples (100 pixels = 1 ms), and the DEPMEM HEAD reports
# SAMPLE.FULLSCREEN = 7600 samples (7.6 ms at 1 MSa/s) for that same window.
SCREEN_PX_PER_DIV = 50.0
# Read-only measurement queries (V3.0.0). Live replies look like
# ':MEASUrement:CH1:PKPK?  -> Vpp : 5.120V->|'. VPP?, MEAN? and RMS? give no
# reply on this firmware - see tools/OWON_TAO3104A_PROTOCOL.md.
MEAS_QUERIES = (
    ('pkpk', 'PKPK'),
    ('max', 'MAX'),
    ('min', 'MIN'),
    ('period', 'PERiod'),
    ('frequency', 'FREQuency'),
)


class Scope:
    """Raw bulk transport for the TAO3104A (V3.0.0)."""

    def __init__(self):
        d = usb.core.find(idVendor=VID, idProduct=PID)
        if d is None:
            raise RuntimeError('TAO3104A not found (VID %04x PID %04x)' % (VID, PID))
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

    # ---- low level -----------------------------------------------------
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
        """Text command -> text answer (terminated by the scope prompt)."""
        self.dev.write(EP_OUT, (cmd + '\r\n').encode(), timeout=5000)
        return self._read_until(time.time() + timeout_s)

    def bulk(self, cmd, timeout_s=20.0, maxbytes=1 << 18):
        """Binary read: 4-byte LE length prefix + payload, no prompt.

        The scope streams payloads in 512-byte bulk packets with variable
        inter-packet latency (observed up to ~1 s on this firmware). We poll
        with short per-read timeouts and stop as soon as the announced body
        is fully received.
        """
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
                # Quiet read: if we already have a complete frame, stop;
                # otherwise keep waiting until the overall timeout_s elapses.
                if len(got) >= 4:
                    ln = int.from_bytes(got[:4], 'little')
                    if 4 <= ln < maxbytes and len(got) >= 4 + ln:
                        break
        return got

    # ---- verified SCPI subset ------------------------------------------
    def identify(self):
        r = self.query('*IDN?', timeout_s=6.0)
        if not r:
            raise RuntimeError('no reply to *IDN? - scope dead or wrong driver')
        self.idn = r[:-len(PROMPT)].decode(errors='replace').strip()
        return self.idn

    def head(self, mode='SCREEN'):
        """mode: 'SCREEN' (current 1520-point frame) or 'DEPMEM' (full deep memory)."""
        raw = self.bulk(':DATA:WAVE:%s:HEAD?' % mode, timeout_s=15.0)
        if len(raw) < 6:
            raise RuntimeError('HEAD read failed: %d bytes' % len(raw))
        ln = int.from_bytes(raw[:4], 'little')
        body = raw[4:4 + ln]
        try:
            return json.loads(body.decode('utf-8'))
        except Exception as e:
            raise RuntimeError('HEAD JSON parse failed: %s (got %d B)' % (e, len(body)))

    def waveform(self, ch, mode='SCREEN'):
        """Bulk-read a channel. mode 'SCREEN' -> 3040 B; 'DEPMEM' -> variable.

        On a stale USB bulk-IN state (V3.0.0 firmware bug) the read may be
        truncated at 2047 B (4 bulk packets of 512 B + 3 B header remainder).
        In that case the declared length-prefix will exceed the actual payload
        and we raise a specific error so the caller can power-cycle and retry.
        """
        raw = self.bulk(':DATA:WAVE:%s:%s?' % (mode, ch), timeout_s=15.0)
        if len(raw) < 6:
            raise RuntimeError('%s read failed: %d bytes' % (ch, len(raw)))
        ln = int.from_bytes(raw[:4], 'little')
        if ln < 2 or len(raw) < 4 + ln:
            # Heuristic: scope promised ln bytes but the bulk-IN cut us off.
            # Observed signatures: ln=3040 raw=2047 (SCREEN) or ln=19998 raw=2047 (DEPMEM).
            truncated_at = len(raw) - 4
            if truncated_at in (2043, 2047) and ln > truncated_at + 100:
                raise RuntimeError(
                    '%s USB bulk-IN truncated: declared %d B, got %d B. '
                    'This is the V3.0.0 firmware bug; power-cycle the scope '
                    'and retry.' % (ch, ln, truncated_at))
            raise RuntimeError('%s short frame: %d/%d' % (ch, len(raw), 4 + ln))
        container = np.frombuffer(raw[4:4 + ln], dtype='<u2')
        return (container >> 8).astype(float)   # 8-bit code in 16-bit container

    def measure(self, ch, keys=MEAS_QUERIES):
        """The instrument's own measurements for one channel (read-only).

        Live V3.0.0: PKPK -> 'Vpp : 5.120V', MAX -> 'Ma : 5.080V',
        MIN -> 'Mi : -40.00mV', PERiod -> 'T : 1.000ms', FREQuency -> 'F :
        1.000KHz'. A key is None when the firmware does not answer (VPP?,
        MEAN?, RMS? return nothing at all).
        """
        out = {}
        for name, scpi in keys:
            raw = self.query(':MEASUrement:%s:%s?' % (ch, scpi), timeout_s=6.0)
            out[name] = measure_reply_value(raw)
        return out


def unit(text):
    """Parse a scope quantity string.

    Examples:
      '100mV'      -> 0.1
      '1.00V'      -> 1.0
      '500us'      -> 5e-4
      '-40.00mV'   -> -0.04      (signed: :MEASUrement:CHx:MIN?)
      '(1MSa/s)'   -> 1e6   (SAMPLERATE label)
      '(50kSa/s)'  -> 5e4
      '1.000KHz'   -> 1e3        (:MEASUrement:CHx:FREQuency?)
      '50.29267'   -> 50.29267   (FREQUENCE with no unit)
      '' or None   -> 1.0   (returns 1.0 so callers can keep going)
    """
    if text is None:
        return 1.0
    s = str(text).strip()
    if not s:
        return 1.0
    # try with Sa/s suffix first (SAMPLERATE)
    m = re.search(r'([0-9.]+)\s*([kKmMuUnngG]?)Sa/s', s)
    if m:
        v = float(m.group(1)); u = m.group(2)
        return v * {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'u': 1e-6,
                    'n': 1e-9, 'M': 1e6, 'G': 1e9}.get(u, 1.0)
    # generic SI suffix ('5.120V', '-40.00mV', '1.000KHz', '500us'); the sign
    # matters because the instrument's own replies are signed
    m = re.match(r'^\s*\(?\s*([-+]?[0-9.]+)\s*([kKmMuUnNgG]?)', s)
    if not m:
        # could be a bare number like '50.29267' from FREQUENCE
        m2 = re.match(r'^\s*([-+]?[0-9.]+)\s*$', s)
        if m2:
            return float(m2.group(1))
        raise ValueError('cannot parse quantity %r' % text)
    v = float(m.group(1)); u = m.group(2)
    return v * {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'u': 1e-6,
                'n': 1e-9, 'M': 1e6, 'G': 1e9}.get(u, 1.0)


def chan_meta(head, ch):
    for c in head.get('CHANNEL', []):
        if c.get('NAME') == ch:
            return c
    raise KeyError('channel %s absent from HEAD' % ch)


def volts_per_code(meta):
    """Volts per sample code: SCALE/CODES_PER_DIV * PROBE (25 codes per div)."""
    probe = 1.0 if str(meta['PROBE']).strip().upper() == '1X' else 10.0
    return unit(meta['SCALE']) / CODES_PER_DIV * probe


def legacy_zero_code(meta):
    """Zero-volt code under the superseded model: OFFSET + CODE_0V.

    Only valid for the configuration CODE_0V was fitted in; kept so that
    existing callers keep their arithmetic and so that a frame without
    instrument measurements is still decodable - as a flagged fallback.
    """
    return float(meta['OFFSET']) + CODE_0V


def to_volts(codes, meta, zero_code=None):
    """8-bit sample codes -> volts.

    zero_code is the code that means 0 V for this channel and frame. When it is
    omitted the legacy constant is used (see CODE_0V); the capture path always
    passes the calibrated value from calibrate_zero_code().
    """
    k = volts_per_code(meta)
    z = legacy_zero_code(meta) if zero_code is None else float(zero_code)
    return (np.asarray(codes, dtype=float) - z) * k


def robust_levels(codes, pct=0.5):
    """Low/high codes of a frame, robust against single-sample glitches.

    Live 2026-10-05: the 0..5.08 V square wave sits at codes 64/194 (0.5/99.5
    percentiles) while raw min/max also catch cable glitches (10 and 255 appear
    in DEPMEM frames), which would inflate any amplitude read from them.
    """
    a = np.asarray(codes, dtype=float)
    if a.size == 0:
        raise ValueError('empty frame')
    p = float(pct)
    return float(np.percentile(a, p)), float(np.percentile(a, 100.0 - p))


def measure_reply_value(raw):
    """Numeric value out of a :MEASUrement:*? reply, or None when there is none.

    Live reply: b'\\n:MEASUrement:CH1:PKPK?  -> Vpp : 5.120V->|' - the value is
    the field after the last ':' of the segment that follows the first '->'
    (that one separates the echoed command from the answer, the trailing '->'
    closes it).
    """
    if not raw:
        return None
    s = raw.decode('utf-8', errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw)
    if '->' in s:
        s = s.split('->')[1]
    s = s.rsplit(':', 1)[-1].strip().strip('|').strip()
    if not s:
        return None
    try:
        return unit(s)
    except (TypeError, ValueError):
        # a placeholder/error text is not a measurement: treat it as "no reply"
        # rather than killing the capture (VPP?/MEAN?/RMS? are silent on V3.0.0)
        return None


def calibrate_zero_code(codes, meta, meas=None, zero_code=None,
                        scale_mode='auto', scale_tol=0.05):
    """Zero-volt code (and scale) for one frame, anchored on the instrument.

    The instrument's own MIN/MAX/PKPK readings are absolute volts, so they turn
    the 8-bit code axis into a calibrated one:

        k = SCALE/25 * PROBE                          (nominal convention)
        z = code_low - MIN / k

    scale_mode='auto' prefers the two-point fit k = PKPK/(code_high - code_low)
    as soon as MIN and MAX are readable, but only while the fit stays within
    scale_tol of the nominal scale - a bigger deviation means the SCALE/PROBE
    settings (or the probe itself), not the measurement, are suspect. 'nominal'
    and 'fit' force one of the two.

    Returns (zero_code, info). info carries everything needed to audit a frame:
    the codes, the levels, the scale actually used and the instrument readings.
    """
    k_nom = volts_per_code(meta)
    code_low, code_high = robust_levels(codes)
    v_min = None if not meas else meas.get('min')
    v_max = None if not meas else meas.get('max')
    v_pp = None if not meas else meas.get('pkpk')
    k, mode = k_nom, 'nominal'
    if scale_mode != 'nominal' and v_pp is not None and code_high > code_low:
        k_fit = float(v_pp) / (code_high - code_low)
        if scale_mode == 'fit' or abs(k_fit / k_nom - 1.0) <= scale_tol:
            k, mode = k_fit, 'fit'
    if zero_code is not None:
        z, src = float(zero_code), 'explicit'
    elif v_min is not None:
        z, src = code_low - float(v_min) / k, 'measurement_min'
    else:
        z, src = legacy_zero_code(meta), 'legacy_constant'
    info = {'zero_code': z, 'zero_code_source': src, 'v_per_code': k,
            'v_per_code_nominal': k_nom, 'scale_mode': mode,
            'code_low': code_low, 'code_high': code_high,
            'vmin_tool': (code_low - z) * k, 'vmax_tool': (code_high - z) * k}
    info['vpp_tool'] = info['vmax_tool'] - info['vmin_tool']
    for key in ('pkpk', 'max', 'min', 'period', 'frequency'):
        info[key + '_scope'] = None if not meas else meas.get(key)
    return z, info


def selfcheck(info, tol_codes=3.0):
    """Compare the decoded frame with the instrument's own measurements.

    Errors are reported in codes as well as volts because one code is the
    resolution of the data (40 mV at 1 V/div, 4 mV at 100 mV/div) and because
    both anchors are quantised to +-1 code. verdict is PASS, WARN, or UNKNOWN
    when the instrument could not be read at all (nothing to compare with).
    """
    k = abs(info['v_per_code'])
    out = {}
    for name, tool_key, scope_key in (('vmin', 'vmin_tool', 'min_scope'),
                                      ('vmax', 'vmax_tool', 'max_scope'),
                                      ('vpp', 'vpp_tool', 'pkpk_scope')):
        tool, scope = info.get(tool_key), info.get(scope_key)
        if tool is None or scope is None:
            continue
        err = tool - scope
        out[name] = {'tool_v': tool, 'scope_v': scope, 'err_v': err,
                     'err_codes': err / k if k else None,
                     'ok': abs(err) <= tol_codes * k}
    results = [c['ok'] for c in out.values()]
    out['verdict'] = 'UNKNOWN' if not results else ('PASS' if all(results) else 'WARN')
    out['tol_codes'] = tol_codes
    out['zero_code_source'] = info['zero_code_source']
    out['scale_mode'] = info['scale_mode']
    return out


def frame_layout(head, n=None):
    """How a frame maps onto time - the structured replacement for 10*TB/n.

    SCREEN (peak detect) is pair-packed, so the numbers that matter are:
      dt_s        - the pixel period (10 us at 500 us/div): this one carries the
                    signal's periodicity
      sample_dt_s - half of it (the raw decimation step, 7600/1520 = 5 us)
      span_s      - the whole window (7.6 ms at 500 us/div)
    Deep memory is uniform: dt_s = 1/SAMPLE.SAMPLERATE = 1 us. Its HEAD reports
    DATATYPE 'WAVEDEPMEM' (not 'DEPMEM' - that is only the command name),
    DATALEN 10000 while the channel reply carries 9999 samples, and the screen
    window inside it as SAMPLE.FULLSCREEN = 7600 samples (= 7.6 ms, exactly the
    SCREEN span - the cross-check used in the unit tests) at SAMPLE.SCREENOFFSET.
    """
    mode = str(head.get('DATATYPE', '')).upper()
    sample = head.get('SAMPLE', {}) or {}
    n = int(n or sample.get('DATALEN') or 0)
    if mode.startswith('SCREEN'):
        px = n // 2
        px_dt = unit(head['TIMEBASE']['SCALE']) / SCREEN_PX_PER_DIV
        return {'mode': 'SCREEN', 'samples': n, 'pair_packed': True, 'pixels': px,
                'dt_s': px_dt, 'sample_dt_s': px_dt / 2.0, 'span_s': px * px_dt,
                'time_of_sample': '(i//2)*dt_s'}
    if 'DEPMEM' in mode:
        dt = 1.0 / unit(sample.get('SAMPLERATE'))
        lay = {'mode': 'DEPMEM', 'samples': n, 'pair_packed': False, 'pixels': n,
               'dt_s': dt, 'sample_dt_s': dt, 'span_s': n * dt,
               'time_of_sample': 'i*dt_s'}
        if sample.get('FULLSCREEN'):
            lay['screen_window_s'] = float(sample['FULLSCREEN']) * dt
            lay['screen_offset_samples'] = sample.get('SCREENOFFSET')
        return lay
    raise ValueError('unknown DATATYPE %r in HEAD' % head.get('DATATYPE'))


def time_axis(head, n=None):
    """Sample times of one frame, plus the spacing that goes with them.

    DEPMEM: uniform grid, t[i] = i / SAMPLE.SAMPLERATE (1 MSa/s -> 1 us).
    SCREEN: pair-packed (760 px x [max, min]) - both samples of a pixel get the
    pixel's time, so t[i] = (i // 2) * px_dt and the returned spacing is the
    PIXEL period (10 us at 500 us/div), not a sample rate. The superseded
    '10 * TIMEBASE / n' model (3.289 us) is refuted by the live frame: one 1 kHz
    period spans 200 SCREEN samples = 100 pixels = 1 ms, a span the old model
    turns into 658 us (1.52x too short -> 1.52 kHz instead of 1.000 kHz).
    """
    lay = frame_layout(head, n)
    n = lay['samples']
    if lay['pair_packed']:
        t = (np.arange(n) // 2).astype(float) * lay['dt_s']
    else:
        t = np.arange(n, dtype=float) * lay['dt_s']
    return t, lay['dt_s']


# ---- CLI ---------------------------------------------------------------
def cmd_list(args):
    d = usb.core.find(idVendor=VID, idProduct=PID)
    print('USB: %s' % ('found VID %04x PID %04x' % (VID, PID) if d else 'not found'))
    if d:
        print('  product :', usb.util.get_string(d, d.iProduct))
        print('  serial  :', usb.util.get_string(d, d.iSerialNumber))
        print('  driver  : libusb-win32 (raw bulk) - pyvisa CANNOT open this device')


def cmd_probe(args):
    sc = Scope()
    try:
        print('IDN=', sc.identify())
        h = sc.head('DEPMEM' if args.depmem else 'SCREEN')
        print('MODEL=', h.get('MODEL'))
        print('DATATYPE=', h.get('DATATYPE'), 'RUNSTATUS=', h.get('RUNSTATUS'))
        lay = frame_layout(h)
        print('frame: mode=%s samples=%d pair_packed=%s pixels=%d dt=%.3f us '
              'span=%.6f s (time of sample: %s)'
              % (lay['mode'], lay['samples'], lay['pair_packed'], lay['pixels'],
                 lay['dt_s'] * 1e6, lay['span_s'], lay['time_of_sample']))
        print('  SAMPLERATE label %s describes the acquisition memory, not this '
              'frame' % h['SAMPLE']['SAMPLERATE'])
        if 'screen_window_s' in lay:
            print('  screen window was %.6f s (FULLSCREEN=%s samples, offset=%s)'
                  % (lay['screen_window_s'], h['SAMPLE'].get('FULLSCREEN'),
                     lay.get('screen_offset_samples')))
        for c in h['CHANNEL']:
            print('  %s display=%s scale=%s probe=%s offset=%s' % (
                c['NAME'], c.get('DISPLAY'), c.get('SCALE'),
                c.get('PROBE'), c.get('OFFSET')))
        for c in h['CHANNEL']:
            if str(c.get('DISPLAY', 'ON')).upper() != 'ON':
                continue
            print('  %s measurements: %s' % (c['NAME'], json.dumps(sc.measure(c['NAME']))))
    finally:
        sc.close()
    return 0


def cmd_head(args):
    sc = Scope()
    try:
        sc.identify()
        print(json.dumps(sc.head('DEPMEM' if args.depmem else 'SCREEN'),
                         ensure_ascii=False, indent=1))
    finally:
        sc.close()
    return 0


def cmd_measure(args):
    """The instrument's own per-channel measurements, as JSON.

    These are the numbers every capture is checked against; they are also the
    only trustworthy source of amplitude and frequency on this firmware.
    """
    sc = Scope()
    try:
        print('IDN=', sc.identify())
        h = sc.head()
        out = {}
        for c in h['CHANNEL']:
            if str(c.get('DISPLAY', 'ON')).upper() != 'ON' and not args.include_off:
                continue
            out[c['NAME']] = sc.measure(c['NAME'])
        print(json.dumps(out, indent=1))
    finally:
        sc.close()
    return 0


def capture_region(sc, out, label, include_off=False, measure=True,
                   mode='SCREEN', zero_code=None, scale_mode='auto',
                   tol_codes=3.0, strict=False):
    """HEAD + instrument measurements + channels -> scope_region_<label>.csv.

    Sidecars: <out>.head.json (raw HEAD) and <out>.measure.json (frame layout,
    per-channel calibration and the selfcheck verdict). Returns (head, layout,
    sidecar).

    Volts are calibrated per frame (calibrate_zero_code) and compared with the
    instrument's own MIN/MAX/PKPK (selfcheck); strict=True turns a verdict other
    than PASS into RuntimeError so a bad frame cannot pass silently. The
    measurements describe the signal, not the buffer, so they apply to DEPMEM
    captures as well.

    CSV columns are unchanged (sample_index, time_s, chN_v) for consumers that
    already read them, but time_s now follows the real geometry: for a
    pair-packed SCREEN frame both samples of a pixel carry the pixel's time.
    """
    h = sc.head(mode)
    if mode.upper().startswith('DEPMEM') and include_off:
        print('warning: DEPMEM on DISPLAY=OFF channels is unverified - '
              'frame it with --depmem alone', file=sys.stderr)
    measured = {}
    if measure:
        for ch in ('CH1', 'CH2', 'CH3', 'CH4'):
            meta = chan_meta(h, ch)
            if str(meta.get('DISPLAY', 'ON')).upper() != 'ON' and not include_off:
                continue
            measured[ch] = sc.measure(ch)
    volts, info_by_ch = {}, {}
    for ch in ('CH1', 'CH2', 'CH3', 'CH4'):
        meta = chan_meta(h, ch)
        if str(meta.get('DISPLAY', 'ON')).upper() != 'ON' and not include_off:
            volts[ch] = None
            continue
        codes = sc.waveform(ch, mode=mode)
        z, info = calibrate_zero_code(codes, meta, measured.get(ch), zero_code,
                                      scale_mode)
        info['selfcheck'] = selfcheck(info, tol_codes)
        volts[ch] = to_volts(codes, meta, z)
        info_by_ch[ch] = info
    active = [c for c in ('CH1', 'CH2', 'CH3', 'CH4') if volts.get(c) is not None]
    if not active:
        raise RuntimeError('no channel has data')
    n = max(len(volts[c]) for c in active)
    lay = frame_layout(h, n)
    t, dt = time_axis(h, n)
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(CSV_HDR)
        for i in range(n):
            row = [i, '%.9f' % t[i]]
            for c in ('CH1', 'CH2', 'CH3', 'CH4'):
                if volts.get(c) is None or i >= len(volts[c]):
                    row.append('')
                else:
                    row.append('%.9f' % volts[c][i])
            w.writerow(row)
    sidecar = {'label': label, 'mode': lay['mode'], 'frame_layout': lay,
               'dt_s': dt, 'captured_at_unix': time.time(),
               'zero_code_override': zero_code, 'scale_mode': scale_mode,
               'tol_codes': tol_codes, 'measurements': measured,
               'channels': info_by_ch,
               'note': 'time_s is the pixel time on pair-packed SCREEN frames '
                       '(both samples of a pixel share it); use --depmem for '
                       'uniform 1 us samples'}
    (Path(out).with_suffix('.head.json')).write_text(
        json.dumps(h, ensure_ascii=False, indent=1), encoding='utf-8')
    (Path(out).with_suffix('.measure.json')).write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding='utf-8')
    for ch in active:
        info = info_by_ch[ch]
        chk = info['selfcheck']
        print('  %s selfcheck %s (zero_code=%.2f via %s, scale=%s, k=%.6f V/code)'
              % (ch, chk['verdict'], info['zero_code'], info['zero_code_source'],
                 chk['scale_mode'], info['v_per_code']))
        for key in ('vmin', 'vmax', 'vpp'):
            d = chk.get(key)
            if d is None:
                continue
            print('    %-4s tool %+.4f V vs scope %+.4f V -> %+.4f V (%+.2f codes) %s'
                  % (key, d['tool_v'], d['scope_v'], d['err_v'], d['err_codes'],
                     'OK' if d['ok'] else 'FAIL'))
    if strict:
        bad = [ch for ch in active if info_by_ch[ch]['selfcheck']['verdict'] != 'PASS']
        if bad:
            raise RuntimeError('selfcheck not PASS for %s on %s: the decoded volts '
                               'disagree with the instrument' % (','.join(bad), out))
    return h, lay, sidecar


def cmd_capture(args):
    sc = Scope()
    try:
        print('IDN=', sc.identify())
        out = args.out or 'scope_capture.csv'
        h, lay, side = capture_region(
            sc, out, Path(out).stem, args.include_off,
            measure=not args.no_measure,
            mode='DEPMEM' if args.depmem else 'SCREEN',
            zero_code=args.zero_code, scale_mode=args.scale_mode,
            tol_codes=args.tol_codes, strict=args.strict)
        print('saved %s (%d samples, %s frame: dt=%.3f us, span=%.6f s, pixels=%d)'
              % (out, lay['samples'], lay['mode'], lay['dt_s'] * 1e6,
                 lay['span_s'], lay['pixels']))
        print('sidecars: %s, %s' % (
            Path(out).with_suffix('.head.json').name,
            Path(out).with_suffix('.measure.json').name))
    except RuntimeError as e:
        print('ERROR:', e, file=sys.stderr)
        return 1
    finally:
        sc.close()
    return 0


def cmd_campaign(args):
    sc = Scope()
    try:
        sc.identify()
        base = Path(args.out)
        base.mkdir(parents=True, exist_ok=True)
        mode = 'DEPMEM' if args.depmem else 'SCREEN'
        manifest = {'scope_idn': sc.idn, 'regions': args.regions,
                    'channels': ['CH1', 'CH2', 'CH3', 'CH4'],
                    'frame_mode': mode, 'tol_codes': args.tol_codes,
                    'note': 'arm/hold-off is manual on this scope - '
                            'settlement delay only, no USB sweep control'}
        # Settlement delay before each region. This is NOT a trigger: the
        # TAO3104A V3.0.0 ignores every SET command over USB, so arming must
        # be done by hand on the front panel. The delay only guarantees the
        # scope is showing a settled frame.
        for r in range(args.regions):
            rd = base / ('region_%02d' % r)
            rd.mkdir(exist_ok=True)
            t0 = time.time()
            if args.delay > 0:
                time.sleep(args.delay)
            out_csv = str(rd / ('scope_region_%d.csv' % r))
            h, lay, side = capture_region(
                sc, out_csv, str(r), args.include_off,
                measure=not args.no_measure, mode=mode,
                zero_code=args.zero_code, scale_mode=args.scale_mode,
                tol_codes=args.tol_codes, strict=args.strict)
            (rd / 'region_meta.json').write_text(json.dumps(
                {'region': r, 'captured_at_unix': t0,
                 'capture_offset_s': time.time() - t0,
                 # frame_layout.dt_s is the spacing that goes with the frame;
                 # 'samplerate' is reported by HEAD but is NOT 1/dt (it is the
                 # 10 kSa acquisition memory, not the SCREEN buffer).
                 'frame_layout': lay,
                 'dt_s': lay['dt_s'],
                 'samplerate': h['SAMPLE']['SAMPLERATE'],
                 'samplerate_note': 'acquisition-memory rate from HEAD; dt_s is '
                                    'the frame spacing (see frame_layout)',
                 'datalen': h['SAMPLE']['DATALEN'],
                 'zero_code': {ch: v['zero_code']
                               for ch, v in side['channels'].items()},
                 'selfcheck': {ch: v['selfcheck']['verdict']
                               for ch, v in side['channels'].items()},
                 'runstatus': h.get('RUNSTATUS')}, ensure_ascii=False, indent=1),
                encoding='utf-8')
            print('region', r, 'saved')
        (base / 'manifest.json').write_text(
            json.dumps(manifest, indent=1), encoding='utf-8')
        print('campaign written to', base)
    except RuntimeError as e:
        print('ERROR:', e, file=sys.stderr)
        return 1
    finally:
        sc.close()
    return 0


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--list', action='store_true')
    p.add_argument('--probe', action='store_true')
    p.add_argument('--measure', action='store_true',
                   help="read the instrument's own measurements (JSON)")
    p.add_argument('--head', action='store_true')
    p.add_argument('--capture', action='store_true')
    p.add_argument('--out')
    p.add_argument('--campaign', action='store_true')
    p.add_argument('--regions', type=int, default=12)
    p.add_argument('--delay', type=float, default=0.0,
                   help='settlement delay per region (NOT a trigger)')
    p.add_argument('--include-off', action='store_true',
                   help='read channels whose DISPLAY is OFF')
    p.add_argument('--depmem', action='store_true',
                   help='use the 10 kSa DEPMEM frame (uniform 1 us samples)')
    p.add_argument('--no-measure', action='store_true',
                   help='skip :MEASUrement (volts fall back to the legacy '
                        'zero code and selfcheck reports UNKNOWN)')
    p.add_argument('--scale-mode', choices=('auto', 'nominal', 'fit'),
                   default='auto',
                   help='auto: two-point fit while it stays within 5%% of '
                        'SCALE/25')
    p.add_argument('--zero-code', type=float, default=None,
                   help='explicit zero-volt code (overrides the measurement)')
    p.add_argument('--tol-codes', type=float, default=3.0,
                   help='selfcheck tolerance in codes (default 3.0)')
    p.add_argument('--strict', action='store_true',
                   help='exit non-zero when selfcheck is not PASS')
    a = p.parse_args()
    if a.list:
        return cmd_list(a)
    elif a.probe:
        return cmd_probe(a)
    elif a.measure:
        return cmd_measure(a)
    elif a.head:
        return cmd_head(a)
    elif a.capture:
        return cmd_capture(a)
    elif a.campaign:
        return cmd_campaign(a)
    else:
        p.print_help()
        return 2


if __name__ == '__main__':
    sys.exit(main())
