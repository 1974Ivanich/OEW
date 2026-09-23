"""TAO3104A soak test for V3.0.0 firmware query/readback stability.

Two modes:
  A (default): HEAD + CH1, N iterations, fast liveness check.
  B          : HEAD + CH1..CH4, N iterations, full production pattern.

Hard stops at the first violation, no recovery attempts (no SETs).

Usage:
    py -3 tao3104a_soak.py             # mode A, N=100
    py -3 tao3104a_soak.py --mode B    # mode B, N=50
    py -3 tao3104a_soak.py --mode A --n 200 --out soak.csv
"""
import argparse
import csv
import json
import sys
import time
import usb

VID = 0x5345
PID = 0x1234
EP_IN = 0x81
EP_OUT = 0x03

# Hard invariants: when SCOPE is healthy they hold exactly.
EXPECT_HEAD_LEN = 1247
EXPECT_CH_LEN = 3040
EXPECT_DATALEN = 1520
HEAD_PREFIX = 4

CHANNELS = ['CH1', 'CH2', 'CH3', 'CH4']


class Scope:
    def __init__(self):
        d = usb.core.find(idVendor=VID, idProduct=PID)
        if d is None:
            raise RuntimeError('TAO3104A not found')
        self.dev = d
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

    def bulk(self, cmd, settle=1.0, rounds=8):
        self.dev.write(EP_OUT, (cmd + '\r\n').encode(), timeout=5000)
        time.sleep(settle)
        got = b''
        for _ in range(rounds):
            try:
                r = bytes(self.dev.read(EP_IN, 512, timeout=1500))
            except usb.core.USBError:
                break
            if not r:
                break
            got += r
            # Binary replies (HEAD, CHx) end after length-prefix is consumed.
            # Text replies end with the prompt.
            if got.endswith(b'->\n'):
                break
            # For binary: stop as soon as we have at least length+body
            if len(got) >= 4:
                ln = int.from_bytes(got[:4], 'little')
                if 4 <= ln < (1 << 16) and len(got) >= 4 + ln:
                    break
        return got, time.time()

    def identify(self):
        got, _ = self.bulk('*IDN?', settle=1.0, rounds=8)
        if not got:
            return False, 0, ''
        # Only accept a proper IDN reply (text + prompt), reject leftover tails.
        if got.endswith(b'->\n') and got.startswith(b'OWON,'):
            return True, len(got), got[:-3].decode(errors='replace').strip()
        return False, len(got), ''


def check_head(raw):
    """Return (ok, json, datalen, payload_len). Strict invariant."""
    if len(raw) < HEAD_PREFIX + 2:
        return False, None, 0, 0
    plen = int.from_bytes(raw[:HEAD_PREFIX], 'little')
    body = raw[HEAD_PREFIX:HEAD_PREFIX + plen]
    if len(body) != plen or plen < 200:
        return False, None, 0, plen
    try:
        j = json.loads(body.decode('utf-8'))
    except Exception:
        return False, None, 0, plen
    dl = int(j.get('SAMPLE', {}).get('DATALEN', 0))
    return (dl == EXPECT_DATALEN), j, dl, plen


def check_ch(raw, datalen=EXPECT_DATALEN):
    if len(raw) < HEAD_PREFIX + 2:
        return False, 0
    plen = int.from_bytes(raw[:HEAD_PREFIX], 'little')
    body = raw[HEAD_PREFIX:HEAD_PREFIX + plen]
    if len(body) != plen or plen < 2:
        return False, plen
    if plen // 2 != datalen:
        return False, plen
    return True, plen


def run(mode, n, out_path, settle):
    sc = Scope()
    rows = []
    fail = None
    try:
        for i in range(1, n + 1):
            t0 = time.time()
            id_ok, id_len, id_str = sc.identify()
            head_raw, head_elapsed = sc.bulk(':DATA:WAVE:SCREEN:HEAD?', settle=settle)
            head_ok, head_j, head_dl, head_plen = check_head(head_raw)

            ch_rows = []
            ch_all_ok = True
            if mode == 'B':
                for ch in CHANNELS:
                    raw, e = sc.bulk(':DATA:WAVE:SCREEN:%s?' % ch, settle=settle)
                    ok, plen = check_ch(raw, head_dl)
                    ch_rows.append((ch, ok, plen, len(raw), e))
                    ch_all_ok = ch_all_ok and ok

            elapsed = time.time() - t0
            row = {
                'iter': i,
                'ts': t0,
                'idn_ok': id_ok,
                'idn_len': id_len,
                'idn_str': id_str,
                'head_ok': head_ok,
                'head_plen': head_plen,
                'head_datalen': head_dl,
                'head_raw_len': len(head_raw),
                'head_elapsed_s': round(head_elapsed, 3),
            }
            if mode == 'B':
                for ch, ok, plen, rlen, e in ch_rows:
                    row['%s_ok' % ch.lower()] = ok
                    row['%s_plen' % ch.lower()] = plen
                    row['%s_raw_len' % ch.lower()] = rlen
                    row['%s_elapsed_s' % ch.lower()] = round(e, 3)

            # Strict liveness: all must be OK
            live = id_ok and head_ok
            if mode == 'B':
                live = live and ch_all_ok
            row['live'] = live
            row['elapsed_s'] = round(elapsed, 3)
            rows.append(row)

            verdict = 'LIVE' if live else 'FAIL'
            if mode == 'A':
                print('%4d %-4s idn=%s head=%s pl=%d raw=%d dl=%d (%.2fs)' %
                      (i, verdict, id_ok, head_ok, head_plen,
                       len(head_raw), head_dl, elapsed))
            else:
                flags = ' '.join('%s=%s/pl=%d' % (ch, ok, plen)
                                 for ch, ok, plen, _, _ in ch_rows)
                print('%4d %-4s idn=%s head=%s/dl=%d %s (%.2fs)' %
                      (i, verdict, id_ok, head_ok, head_dl, flags, elapsed))

            if not live:
                fail = (i, row)
                break
    finally:
        sc.close()

    if out_path:
        keys = sorted({k for r in rows for k in r.keys()})
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print('saved', out_path)

    if fail is None:
        print('RESULT: PASS %d/%d iterations, no degradation' % (len(rows), n))
        return 0
    print('RESULT: FAIL at iteration %d' % fail[0])
    print(json.dumps(fail[1], indent=1, ensure_ascii=False))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=['A', 'B'], default='A')
    ap.add_argument('--n', type=int, default=None,
                    help='iterations (default A=100, B=50)')
    ap.add_argument('--out', default='soak.csv')
    ap.add_argument('--settle', type=float, default=2.0,
                    help='per-command settle seconds')
    a = ap.parse_args()
    n = a.n if a.n is not None else (100 if a.mode == 'A' else 50)
    return run(a.mode, n, a.out, a.settle)


if __name__ == '__main__':
    sys.exit(main())
