#!/usr/bin/env python3
"""
OWON TAO3104A ACS712 20A waveform capture for FOC current map.

Adaptatsiya tao3104a_cap.py pod ACS712:
  - CH2 = faznaya liniya U (shunt OUT -> osstsillograf)
  - CH3 = faznaya liniya V (shunt OUT -> osstsillograf)
  - CH1 = PB6 TIM1 TRGO sync marker (50% duty meand, ~5 kHz)
  - Sohranyaet: time_s, ch_u_mv, ch_v_mv, ch_sync_mv
  - Vvod: calibration JSON dlya mV -> mA
  - Vyvod: CSV dlya map_scope_ingest.py --scope-waiver

CLI: --list --probe --capture --calib <json> --out <dir>
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


def unit(text):
    """Parse a scope quantity string. Returns 1.0 on failure."""
    if text is None:
        return 1.0
    s = str(text).strip()
    if not s:
        return 1.0
    m = re.search(r'([0-9.]+)\s*([kKmMuUnngG]?)Sa/s', s)
    if m:
        v = float(m.group(1)); u = m.group(2)
        return v * {'k':1e3,'K':1e3,'m':1e-3,'u':1e-6,'M':1e6,'G':1e9}.get(u, 1.0)
    m = re.match(r'^\s*\(?([0-9.]+)\s*([kKmMuUnNgG]?)', s)
    if not m:
        m2 = re.match(r'^\s*([0-9.]+)\s*$', s)
        if m2:
            return float(m2.group(1))
        return 1.0
    v = float(m.group(1)); u = m.group(2)
    return v * {'k':1e3,'K':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'M':1e6,'G':1e9}.get(u, 1.0)


class Scope:
    """Raw bulk transport for TAO3104A V3.0.0."""

    def __init__(self):
        d = usb.core.find(idVendor=VID, idProduct=PID)
        if d is None:
            raise RuntimeError('TAO3104A ne naiden (VID %04x PID %04x)' % (VID, PID))
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
        r = self.query('*IDN?\r\n', timeout=5000)
        if not r:
            raise RuntimeError('net otveta na *IDN? - osstsillograf mertv ili nevernyj driver')
        self.idn = r[:-len(PROMPT)].decode(errors='replace').strip()
        return self.idn

    def head(self, mode='SCREEN'):
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
        raw = self.bulk(':DATA:WAVE:%s:%s?' % (mode, ch), timeout_s=15.0)
        if len(raw) < 6:
            raise RuntimeError('%s read failed: %d bytes' % (ch, len(raw)))
        ln = int.from_bytes(raw[:4], 'little')
        if ln < 2 or len(raw) < 4 + ln:
            truncated_at = len(raw) - 4
            if truncated_at in (2043, 2047) and ln > truncated_at + 100:
                raise RuntimeError(
                    '%s USB bulk-IN truncated: declared %d B, got %d B. '
                    'Eto bug proshivki V3.0.0; vypolni power-cycle osstsillografa i povtori.' % (
                        ch, ln, truncated_at))
            raise RuntimeError('%s short frame: %d/%d' % (ch, len(raw), 4 + ln))
        container = np.frombuffer(raw[4:4 + ln], dtype='<u2')
        return (container >> 8).astype(float)


def chan_meta(head, ch):
    for c in head.get('CHANNEL', []):
        if c.get('NAME') == ch:
            return c
    raise KeyError('kanal %s otsutstvuet v HEAD' % ch)


def to_volts(codes, meta):
    scale = unit(meta['SCALE'])
    probe = 1.0 if str(meta.get('PROBE', '1X')).strip().upper() == '1X' else 10.0
    off = float(meta.get('OFFSET', 0))
    return (codes - off) * (scale / 25.0) * probe


def time_axis(head, n):
    sr_label = head['SAMPLE']['SAMPLERATE']
    timebase = unit(head['TIMEBASE']['SCALE'])
    datalen = int(head['SAMPLE']['DATALEN'])
    span_s = timebase * 10.0
    if datalen > 0:
        sr = datalen / span_s
    else:
        sr = unit(sr_label)
    dt = 1.0 / sr
    return np.arange(n, dtype=float) * dt, sr


def load_calibration(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def mv_to_ma(v_mv, v0_mv, sens_mv_per_a):
    return (v_mv - v0_mv) / sens_mv_per_a


def cmd_list(args):
    d = usb.core.find(idVendor=VID, idProduct=PID)
    print('USB: %s' % ('naiden VID %04x PID %04x' % (VID, PID) if d else 'NE NAIDEN'))
    if d:
        print('  product :', usb.util.get_string(d, d.iProduct))
        print('  driver  : libusb-win32 (raw bulk) - pyvisa NE MOZHET otkryt etot ustrojstvo')


def cmd_probe(args):
    sc = Scope()
    try:
        print('IDN =', sc.identify())
        h = sc.head()
        print('DATATYPE =', h.get('DATATYPE'), 'RUNSTATUS =', h.get('RUNSTATUS'))
        print('DATALEN =', h['SAMPLE']['DATALEN'], 'SAMPLERATE =', h['SAMPLE']['SAMPLERATE'])
        for c in h['CHANNEL']:
            name = c.get('NAME', '?')
            if c.get('DISPLAY', 'OFF') == 'ON':
                print('  %s: scale=%-8s probe=%-4s offset=%-6s freq=%s Hz' % (
                    name, c.get('SCALE'), c.get('PROBE'),
                    c.get('OFFSET'), c.get('FREQUENCE')))
    finally:
        sc.close()


def cmd_capture(args):
    sc = Scope()
    out_dir = Path(args.out or 'scope_capture')
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        print('IDN =', sc.identify())
        h = sc.head()

        calib = None
        if args.calib:
            calib = load_calibration(args.calib)
            print('Calibration:', calib['vcc_mv'], 'mV')
            for name in ('U', 'V'):
                c = calib['sensors'][name]
                print('  %s: v0=%d mV sens=%.1f mV/A' % (
                    name, c['v0_mv'], c['sens_mv_per_a']))

        ch_sync = to_volts(sc.waveform('CH1'), chan_meta(h, 'CH1'))
        ch_u    = to_volts(sc.waveform('CH2'), chan_meta(h, 'CH2'))
        ch_v    = to_volts(sc.waveform('CH3'), chan_meta(h, 'CH3'))
        t, sr = time_axis(h, len(ch_sync))

        n = min(len(ch_sync), len(ch_u), len(ch_v))
        print('Points: %d (sr=%.0f Sa/s, span=%.3f ms)' % (n, sr, t[n-1]*1e3))

        csv_path = out_dir / 'scope_capture.csv'
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['pulse', 'time_s', 'ch_sync_mv', 'ch_u_mv', 'ch_v_mv',
                        'ref_u_ma', 'ref_v_ma', 'scope_qualified'])
            for i in range(n):
                sync_mv = ch_sync[i]
                u_mv    = ch_u[i]
                v_mv    = ch_v[i]
                u_ma = mv_to_ma(u_mv, calib['sensors']['U']['v0_mv'],
                              calib['sensors']['U']['sens_mv_per_a']) if calib else ''
                v_ma = mv_to_ma(v_mv, calib['sensors']['V']['v0_mv'],
                              calib['sensors']['V']['sens_mv_per_a']) if calib else ''
                qualified = 1 if (abs(sync_mv - 2500) > 500) else 0
                w.writerow([i + 1,
                            '%.9f' % t[i],
                            '%.4f' % sync_mv,
                            '%.4f' % u_mv,
                            '%.4f' % v_mv,
                            '%.4f' % u_ma if u_ma else '',
                            '%.4f' % v_ma if v_ma else '',
                            qualified])
        print('Saved:', csv_path)

        pre_path = out_dir / 'scope_capture.preamble.txt'
        with open(pre_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({
                'idn': sc.idn,
                'head': h,
                'calibration': calib,
                'n_points': n,
                'sample_rate_hz': sr,
                'capture_span_s': float(t[n-1]),
            }, ensure_ascii=False, indent=1))
        print('Saved:', pre_path)

    finally:
        sc.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list', action='store_true', help='Spisok USB ustrojstv')
    p.add_argument('--probe', action=store_true, help='Probe TAO3104A: IDN + HEAD')
    p.add_argument('--capture', action=store_true, help='Zakhvat waveforms (CH1 sync + CH2 U + CH3 V)')
    p.add_argument('--calib', type=Path, default=None,
                   help='Putt k acs712_calibration.json')
    p.add_argument('--out', type=Path, default=None,
                   help='Vykhodnoj katalog (default: scope_capture/)')
    a = p.parse_args()
    if a.list:
        cmd_list(a)
    elif a.probe:
        cmd_probe(a)
    elif a.capture:
        cmd_capture(a)
    else:
        p.print_help()
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
