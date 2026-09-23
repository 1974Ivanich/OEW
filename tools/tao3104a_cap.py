#!/usr/bin/env python3
"""OWON TAO3104A raw-USB waveform capture (verified against V3.0.0 firmware).

Replaces the invalid pyVISA/:WAV:* package. Speaks plain text over the libusb
bulk endpoints; no USBTMC, no VISA.

CLI: --list --probe --head --capture --campaign
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
        """Bulk-read a channel. mode 'SCREEN' -> 3040 B; 'DEPMEM' -> variable."""
        raw = self.bulk(':DATA:WAVE:%s:%s?' % (mode, ch), timeout_s=15.0)
        if len(raw) < 6:
            raise RuntimeError('%s read failed: %d bytes' % (ch, len(raw)))
        ln = int.from_bytes(raw[:4], 'little')
        if ln < 2 or len(raw) < 4 + ln:
            raise RuntimeError('%s short frame: %d/%d' % (ch, len(raw), 4 + ln))
        container = np.frombuffer(raw[4:4 + ln], dtype='<u2')
        return (container >> 8).astype(float)   # 8-bit code in 16-bit container


def unit(text):
    """'100mV' -> 0.1 ; '(1MSa/s)' -> 1e6 ; '500us' -> 5e-4."""
    m = re.match(r'^\s*\(?([0-9.]+)\s*([kKmMuUnNgG]?)', str(text).strip())
    if not m:
        raise ValueError('cannot parse quantity %r' % text)
    v = float(m.group(1))
    u = m.group(2)
    f = {'k': 1e3, 'K': 1e3, 'm': 1e-3, 'u': 1e-6,
         'n': 1e-9, 'M': 1e6, 'G': 1e9}
    return v * f.get(u, 1.0)


def chan_meta(head, ch):
    for c in head.get('CHANNEL', []):
        if c.get('NAME') == ch:
            return c
    raise KeyError('channel %s absent from HEAD' % ch)


def to_volts(codes, meta):
    scale = unit(meta['SCALE'])
    probe = 1.0 if str(meta['PROBE']).strip().upper() == '1X' else 10.0
    off = float(meta['OFFSET'])
    return (codes - off) * (scale / 25.0) * probe


def time_axis(head, n):
    sr = unit(head['SAMPLE']['SAMPLERATE'])
    dt = 1.0 / sr
    return np.arange(n, dtype=float) * dt, sr


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
        h = sc.head()
        print('MODEL=', h.get('MODEL'))
        print('DATATYPE=', h.get('DATATYPE'), 'RUNSTATUS=', h.get('RUNSTATUS'))
        print('DATALEN=', h['SAMPLE']['DATALEN'], 'SAMPLERATE=', h['SAMPLE']['SAMPLERATE'])
        for c in h['CHANNEL']:
            print('  %s display=%s scale=%s probe=%s offset=%s' % (
                c['NAME'], c.get('DISPLAY'), c.get('SCALE'),
                c.get('PROBE'), c.get('OFFSET')))
    finally:
        sc.close()


def cmd_head(args):
    sc = Scope()
    try:
        sc.identify()
        print(json.dumps(sc.head(), ensure_ascii=False, indent=1))
    finally:
        sc.close()


def capture_region(sc, out, label, include_off=False):
    """HEAD + 4 channels -> scope_region_<label>.csv. Returns the JSON head."""
    h = sc.head()
    volts = {}
    for ch in ('CH1', 'CH2', 'CH3', 'CH4'):
        meta = chan_meta(h, ch)
        if str(meta.get('DISPLAY', 'ON')).upper() != 'ON' and not include_off:
            volts[ch] = None
            continue
        codes = sc.waveform(ch, mode='SCREEN')
        volts[ch] = to_volts(codes, meta)
    active = [c for c in ('CH1', 'CH2', 'CH3', 'CH4') if volts[c] is not None]
    if not active:
        raise RuntimeError('no channel has data')
    n = max(len(volts[c]) for c in active)
    t, sr = time_axis(h, n)
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(CSV_HDR)
        for i in range(n):
            row = [i, '%.9f' % t[i]]
            for c in ('CH1', 'CH2', 'CH3', 'CH4'):
                if volts[c] is None or i >= len(volts[c]):
                    row.append('')
                else:
                    row.append('%.9f' % volts[c][i])
            w.writerow(row)
    (Path(out).with_suffix('.head.json')).write_text(
        json.dumps(h, ensure_ascii=False, indent=1), encoding='utf-8')
    return h


def cmd_capture(args):
    sc = Scope()
    try:
        print('IDN=', sc.identify())
        out = args.out or 'scope_capture.csv'
        h = capture_region(sc, out, Path(out).stem, args.include_off)
        print('saved', out, '(%d pts, %s)' % (
            h['SAMPLE']['DATALEN'], h['SAMPLE']['SAMPLERATE']))
    finally:
        sc.close()


def cmd_campaign(args):
    sc = Scope()
    try:
        sc.identify()
        base = Path(args.out)
        base.mkdir(parents=True, exist_ok=True)
        manifest = {'scope_idn': sc.idn, 'regions': args.regions,
                    'channels': ['CH1', 'CH2', 'CH3', 'CH4'],
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
            h = capture_region(sc, str(rd / ('scope_region_%d.csv' % r)), str(r),
                              args.include_off)
            (rd / 'region_meta.json').write_text(json.dumps(
                {'region': r, 'captured_at_unix': t0,
                 'capture_offset_s': time.time() - t0,
                 'samplerate': h['SAMPLE']['SAMPLERATE'],
                 'datalen': h['SAMPLE']['DATALEN'],
                 'runstatus': h.get('RUNSTATUS')}, ensure_ascii=False, indent=1),
                encoding='utf-8')
            print('region', r, 'saved')
        (base / 'manifest.json').write_text(
            json.dumps(manifest, indent=1), encoding='utf-8')
        print('campaign written to', base)
    finally:
        sc.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list', action='store_true')
    p.add_argument('--probe', action='store_true')
    p.add_argument('--head', action='store_true')
    p.add_argument('--capture', action='store_true')
    p.add_argument('--out')
    p.add_argument('--campaign', action='store_true')
    p.add_argument('--regions', type=int, default=12)
    p.add_argument('--delay', type=float, default=0.0,
                   help='settlement delay per region (NOT a trigger)')
    p.add_argument('--include-off', action='store_true',
                   help='read channels whose DISPLAY is OFF')
    a = p.parse_args()
    if a.list:
        cmd_list(a)
    elif a.probe:
        cmd_probe(a)
    elif a.head:
        cmd_head(a)
    elif a.capture:
        cmd_capture(a)
    elif a.campaign:
        cmd_campaign(a)
    else:
        p.print_help()
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
