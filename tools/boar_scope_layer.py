#!/usr/bin/env python3
"""BOAR campaign scope/ layer: one phase-current reading per pulse of a point.

Per point (r, p):  operator-armed single DEPMEM capture -> vf_run.py point run
-> wait until HEAD reports the frame is held (RUNSTATUS TRIG/STOP) -> DEPMEM
frame (CH1 = ACS712 U, CH2 = ACS712 V) -> burst located by edge ->
8 windows x 200 us -> flat-top mean -> mA -> scope/scope_region_<r>_<p>.csv.

The TAO3104A ignores every SET command from USB (tools/OWON_TAO3104A_PROTOCOL.md),
so the single trigger is armed on the front panel; this tool only observes the
armed and triggered state through HEAD (Trig.Sweep / RUNSTATUS) and never sends
SETs. Reading the frame without waiting for that state was a blind --settle sleep:
a trigger that never fired wrote idle into the map, and nothing in the written row
said so.

Fail-closed: scope_qualified=1 only when the frame was really read, the
instrument zero anchor was available, the trigger was in single mode, the burst
edge was found and the pulse window has a clean, unsaturated flat top with
|ref| <= 10000 mA. Everything else -> scope_qualified=0 with the reason in note.
margin_ticks/blanking_ticks are not derivable from the scope frame: written as
0 with the reason in note.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tao3104a_cap import (Scope, calibrate_zero_code, chan_meta,  # noqa: E402
                          frame_layout, to_volts)

HDR = ['pulse', 'ref_u_ma', 'ref_v_ma', 'scope_qualified', 'margin_ticks',
       'blanking_ticks', 'note']
N_PULSES = 8
PERIOD_US = 200.0
BASE_ID = 1112490322
REF_LIMIT_MA = 10000
CODE_RAIL_LO, CODE_RAIL_HI = 1.0, 254.0
TICKS_NOTE = 'margin/blanking ticks not derivable from scope frame'


def point_id(base, r, p):
    return base + r * 8 + p


def load_calibration(path):
    """{'CH1': (v0_mv, sens), 'CH2': (v0_mv, sens)}; raises when fields are missing."""
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    # Раскладка кампании BOAR: датчики лежат вложенно — {"sensors": {"U": {...}}}.
    # Плоское представление принимает обе формы, чтобы не зависеть от того,
    # какой из инструментов проекта записал файл.
    flat = dict(d)
    if isinstance(d.get('sensors'), dict):
        flat.update(d['sensors'])
    aliases = {'CH1': ('CH1', 'ch1', 'U', 'u', 'phase_u'),
               'CH2': ('CH2', 'ch2', 'V', 'v', 'phase_v')}
    out = {}
    for ch, keys in aliases.items():
        src = next((flat[k] for k in keys if isinstance(flat.get(k), dict)), flat)
        if 'v0_mv' not in src or 'sens_mv_per_a' not in src:
            raise KeyError('%s: v0_mv/sens_mv_per_a missing for %s' % (path, ch))
        sens = float(src['sens_mv_per_a'])
        if sens <= 0:
            raise ValueError('%s: sens_mv_per_a must be > 0 for %s' % (path, ch))
        out[ch] = (float(src['v0_mv']), sens)
    return out


def mv_to_ma(mv, cal):
    v0, sens = cal
    return int(round((mv - v0) / sens * 1000.0))


def fail_rows(reason):
    return [[k, 0, 0, 0, 0, 0, reason] for k in range(1, N_PULSES + 1)]


def check_armed(head):
    """None when HEAD says single-shot trigger, else the reason."""
    sweep = str((head.get('Trig') or {}).get('Sweep', '')).upper()
    if not sweep.startswith('SING'):
        return 'trigger sweep=%s not SINGLE (arm on panel)' % (sweep or '?')
    return None


# RUNSTATUS, при которых кадр удержан и его можно читать: по протоколу V3.0.0
# "TRIG" — прибор сработал на живом сигнале, "STOP" — остановлен. Всё прочее
# (WAIT/AUTO/RUN) означает, что кадра ещё нет: читать его в этот момент — значит
# записать в карту чужой или пустой кадр.
FRAME_HELD_RUNSTATUS = ('TRIG', 'STOP')


def wait_for_trigger(sc, timeout_s, poll_s=0.2, sleep=time.sleep,
                     monotonic=time.monotonic):
    """Дождаться удержанного кадра вместо слепой паузы. -> (reason, polls).

    reason is None, когда кадр удержан; иначе текст причины с последним
    увиденным RUNSTATUS (таймаут или отказ HEAD). Опрашиваются только
    query-формы HEAD: SET на V3.0.0 игнорируются
    (tools/OWON_TAO3104A_PROTOCOL.md), «вооружить» прибор из софта нельзя —
    можно только дождаться.

    Почему это важно: прибор вооружает человек, а burst наливает привод. Прежняя
    версия спала фиксированные --settle секунды и читала кадр независимо от того,
    сработал ли триггер. На 128 точках кампании это ровно тот класс отказа,
    который в кадр не видно: строка пишется, а в ней покой.
    """
    deadline = monotonic() + float(timeout_s)
    polls = 0
    last = '?'
    while True:
        try:
            head = sc.head('DEPMEM')
            last = str(head.get('RUNSTATUS', '?')).upper() or '?'
        except Exception as exc:   # обрыв связи — не «кадр готов»
            return ('HEAD while waiting for trigger failed: %s' % exc, polls)
        polls += 1
        if last in FRAME_HELD_RUNSTATUS:
            return (None, polls)
        if monotonic() >= deadline:
            return ('trigger did not fire within %.3g s: RUNSTATUS=%s'
                    % (timeout_s, last), polls)
        sleep(min(poll_s, max(0.0, deadline - monotonic())))


def read_frame(sc):
    """DEPMEM frame -> (head, layout, {ch: (codes, mv)}). Raises on any defect."""
    h = sc.head('DEPMEM')
    lay = frame_layout(h)
    if lay['mode'] != 'DEPMEM':
        raise RuntimeError('HEAD DATATYPE %r is not DEPMEM' % h.get('DATATYPE'))
    if abs(lay['dt_s'] - 1e-6) > 1e-9:
        raise RuntimeError('dt=%.3g s, expected 1 us' % lay['dt_s'])
    out = {}
    for ch in ('CH1', 'CH2'):
        meta = chan_meta(h, ch)
        if str(meta.get('DISPLAY', 'ON')).upper() != 'ON':
            raise RuntimeError('%s DISPLAY=OFF' % ch)
        codes = sc.waveform(ch, mode='DEPMEM')
        if codes.size == 0:
            raise RuntimeError('%s empty frame' % ch)
        z, info = calibrate_zero_code(codes, meta, sc.measure(ch))
        if info['zero_code_source'] == 'legacy_constant':
            raise RuntimeError('%s no instrument MIN anchor, volts untrusted' % ch)
        out[ch] = (codes, to_volts(codes, meta, z) * 1000.0)
    return h, lay, out


def find_burst(ma_u, ma_v, thr_ma):
    """Index of the first sample of the burst (rising |current| edge) or None."""
    mag = np.maximum(np.abs(ma_u), np.abs(ma_v))
    idx = np.flatnonzero(mag > thr_ma)
    return int(idx[0]) if idx.size else None


def find_burst_span(ma_u, ma_v, thr_ma):
    """(first, last+1) samples of the burst, i.e. where |I| exceeds the threshold."""
    mag = np.maximum(np.abs(ma_u), np.abs(ma_v))
    idx = np.flatnonzero(mag > thr_ma)
    return (int(idx[0]), int(idx[-1]) + 1) if idx.size else (None, None)


def flat_top(seg_u, seg_v, frac=0.5, trim=0.2):
    """Indices of the trimmed plateau of one pulse window, or None."""
    mag = np.maximum(np.abs(seg_u), np.abs(seg_v))
    peak = float(mag.max()) if mag.size else 0.0
    if peak <= 0:
        return None
    idx = np.flatnonzero(mag >= frac * peak)
    if idx.size < 5:
        return None
    lo, hi = idx[0], idx[-1] + 1
    cut = int((hi - lo) * trim)
    sel = np.arange(lo + cut, hi - cut)
    return sel if sel.size >= 3 else None


def analyse(frame, cal, thr_ma, min_plateau):
    _h, _lay, data = frame
    codes_u, mv_u = data['CH1']
    codes_v, mv_v = data['CH2']
    n = min(len(mv_u), len(mv_v))
    ma_u = (mv_u[:n] - cal['CH1'][0]) / cal['CH1'][1] * 1000.0
    ma_v = (mv_v[:n] - cal['CH2'][0]) / cal['CH2'][1] * 1000.0
    start_hi, _ = find_burst_span(ma_u, ma_v, thr_ma)
    if start_hi is None:
        return fail_rows('burst not found (|I|<=%g mA in whole frame); %s'
                         % (thr_ma, TICKS_NOTE))
    # Границы бурста ищутся по НИЗКОМУ порогу: первые ступени несут меньший ток
    # и порог burst-edge (300 мА) пробивают на 300-600 мкс позже начала бурста.
    # Сетку окон якорим по КОНЦУ бурста: прошивка выдаёт ровно N_PULSES ступеней
    # по PERIOD_US, поэтому последнее окно должно заканчиваться на сходе бурста,
    # иначе хвостовые окна уезжают в покой (ровная тишина проходила как ступень
    # с нулевым током) и число ступеней занижалось.
    mag = np.maximum(np.abs(ma_u), np.abs(ma_v))
    thr_lo = max(0.15 * thr_ma, 20.0)
    idx_lo = np.flatnonzero(mag > thr_lo)
    w = int(round(PERIOD_US))
    span = N_PULSES * w
    if idx_lo.size:
        # Сетка начинается там, где привод реально начал работать (низкий порог),
        # и идёт N_PULSES окон вперёд. Якорь по концу бурста давал ложную первую
        # ступень: первое окно попадало в предбурстовую тишину и сходило за
        # измерение с почти нулевым током.
        start = int(idx_lo[0])
        end_burst = int(idx_lo[-1]) + 1
    else:
        end_burst = start_hi + span
        start = start_hi
    if start + N_PULSES * w > n:
        return fail_rows('burst at sample %d truncated by frame end (n=%d); %s'
                         % (start, n, TICKS_NOTE))
    rows = []
    for k in range(N_PULSES):
        a, b = start + k * w, start + (k + 1) * w
        reasons = []
        if a >= end_burst:
            # Окно целиком лежит в покое после конца бурста. Ровная тишина по
            # форме неотличима от плоской вершины, поэтому раньше такие окна
            # проходили как «ступени» с нулевым током. Признак покоя — не форма
            # площадки, а принадлежность бурста: ступени живут в [start, end).
            sel = None
            reasons.append('window after burst end (idle, not a step)')
            ru = rv = 0
        else:
            sel = flat_top(ma_u[a:b], ma_v[a:b])
            if sel is None or sel.size < min_plateau:
                reasons.append('no flat top (plateau<%d samples)' % min_plateau)
                ru = rv = 0
            else:
                sel = sel + a
                cu, cv = codes_u[sel], codes_v[sel]
                if (cu.min() <= CODE_RAIL_LO or cu.max() >= CODE_RAIL_HI or
                        cv.min() <= CODE_RAIL_LO or cv.max() >= CODE_RAIL_HI):
                    reasons.append('ADC rail on plateau')
                ru = mv_to_ma(float(np.mean(mv_u[sel])), cal['CH1'])
                rv = mv_to_ma(float(np.mean(mv_v[sel])), cal['CH2'])
                if abs(ru) > REF_LIMIT_MA or abs(rv) > REF_LIMIT_MA:
                    reasons.append('|ref|>%d mA' % REF_LIMIT_MA)
        q = 0 if reasons else 1
        note = '; '.join(reasons + ['burst@%dus..%dus' % (start, end_burst), TICKS_NOTE])
        rows.append([k + 1, ru, rv, q, 0, 0, note])
    return rows


def write_rows(path, rows, header_comment):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        f.write('# %s\n' % header_comment)
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rows)
    q = sum(int(r[3]) for r in rows)
    print('%s: qualified %d/%d' % (path, q, len(rows)))
    return q


def run_point(args, cfg):
    cmd_a = [sys.executable, args.vf_run, args.port, str(cfg['log']), '0.8',
             'mapcap status', 'mcarm=%d' % cfg['id'], 'mapcap run', 'mapcap status']
    cmd_b = [sys.executable, args.vf_run, args.port, str(cfg['log_drain']), '1.5',
             'mapcap drain', 'mapcap status']
    for cmd in (cmd_a, cmd_b):
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            return 'vf_run rc=%d (%s)' % (rc, cmd[5] if len(cmd) > 5 else '')
    return None


def do_point(sc, args, cal, r, p):
    pid = point_id(args.base_id, r, p)
    out = Path(args.campaign_root) / 'scope' / ('scope_region_%d_%d.csv' % (r, p))
    logs = Path(args.campaign_root) / 'scope' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    comment = 'boar_scope_layer r=%d p=%d id=%d at %s' % (
        r, p, pid, time.strftime('%Y-%m-%dT%H:%M:%S'))
    # Режим развёртки прибор отдаёт только отдельным запросом (:TRIGger:...:SWEEp?);
    # в HEAD этого поля нет, поэтому спрашиваем у него самого. Кадр удержит и NORMAL
    # (прибор взводится сам на фронт PB6), и SINGLE; всё прочее — не удержит.
    try:
        sweep = sc.query(':TRIGger:SINGle:SWEEp?').decode(errors='replace')
        sweep = sweep.replace('->', '').strip()
        reason = None if sweep.upper().startswith(('NORM', 'SING')) else (
            'trigger sweep=%s: кадр не удержится, поставь NORMAL на панели' % sweep)
    except Exception as e:
        reason = 'pre-arm query failed: %s' % e
    if reason is None and not args.no_prompt:
        input('r=%d p=%d: press SINGLE on the scope, then Enter...' % (r, p))
    if reason is not None:
        return write_rows(out, fail_rows(reason + '; ' + TICKS_NOTE), comment)
    reason = run_point(args, {'id': pid,
                              'log': logs / ('point_%d_%d.log' % (r, p)),
                              'log_drain': logs / ('point_%d_%d_drain.log' % (r, p))})
    if reason is not None:
        return write_rows(out, fail_rows(reason + '; ' + TICKS_NOTE), comment)
    time.sleep(args.settle)
    # Кадр читается только после того, как прибор сам отчитался о триггере:
    # иначе в scope_region пишется покой, и по строке этого не видно.
    reason, polls = wait_for_trigger(sc, args.trigger_timeout, args.poll)
    if reason is not None:
        return write_rows(out, fail_rows(reason + '; ' + TICKS_NOTE),
                          comment + ' trigger_polls=%d' % polls)
    comment += ' trigger_polls=%d' % polls
    try:
        frame = read_frame(sc)
    except Exception as e:
        return write_rows(out, fail_rows('frame not read: %s; %s' % (e, TICKS_NOTE)),
                          comment)
    comment += ' runstatus=%s' % frame[0].get('RUNSTATUS')
    return write_rows(out, analyse(frame, cal, args.thr_ma, args.min_plateau), comment)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--campaign-root', required=True)
    ap.add_argument('--region', type=int, help='region r (0..11); omit for all')
    ap.add_argument('--points', default='0-7', help='e.g. 0-7 or 0,3,5')
    ap.add_argument('--point', type=int, help='single-point mode (needs --region)')
    ap.add_argument('--port', default='COM4')
    ap.add_argument('--base-id', type=int, default=BASE_ID)
    ap.add_argument('--vf-run', default='vf_run.py')
    ap.add_argument('--calibration', help='default <root>/calibration/acs712_calibration.json')
    ap.add_argument('--thr-ma', type=float, default=300.0, help='burst edge threshold')
    ap.add_argument('--min-plateau', type=int, default=10, help='samples (us)')
    ap.add_argument('--settle', type=float, default=0.3)
    ap.add_argument('--trigger-timeout', type=float, default=5.0,
                    help='сколько ждать RUNSTATUS=TRIG/STOP после burst')
    ap.add_argument('--poll', type=float, default=0.2,
                    help='пауза между опросами HEAD при ожидании триггера')
    ap.add_argument('--no-prompt', action='store_true')
    args = ap.parse_args()

    if args.point is not None:
        if args.region is None:
            ap.error('--point requires --region')
        jobs = [(args.region, args.point)]
    else:
        pts = []
        for part in args.points.split(','):
            a, _, b = part.partition('-')
            pts += list(range(int(a), int(b or a) + 1))
        regions = [args.region] if args.region is not None else range(12)
        jobs = [(r, p) for r in regions for p in pts]
    for r, p in jobs:
        if not (0 <= r <= 11 and 0 <= p <= 7):
            ap.error('r=%d p=%d out of range' % (r, p))

    cal = load_calibration(args.calibration or Path(args.campaign_root) /
                           'calibration' / 'acs712_calibration.json')
    sc = Scope()
    total_q = 0
    try:
        print('IDN=', sc.identify())
        for r, p in jobs:
            total_q += do_point(sc, args, cal, r, p)
    finally:
        sc.close()
    print('total qualified %d/%d' % (total_q, len(jobs) * N_PULSES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
