"""ПК-3 комиссионинг: гейт VBUS обязан читать СЫРОЙ кадр `a`, а не `@FOC:VBUS`.

Дефект B20 (найден живым прогоном 2026-10-05): `tools/pc3_commission.py` брал VBUS
из телеметрии `@FOC`, где в покое всегда `VBUS=0` (кадр ADC не армнут —
`ADC_FRAME_NOT_ARMED`, известная особенность F12). Из-за этого
`capture --allow-energize --vbus-min 20000` был непроходим по построению: при
реально поданных 32.5 В гейт видел 0 и отказывал (см.
`docs/evidence/incident_20261005_hwbreak/incident.md`). Правильный источник —
сырой кадр `a` (`@ADC:I1=..:I2=..:Ires=..:VBUS=<raw>`), шкала делителя 1:125,
VREF 3.3 В, 12 бит (`src/adc.h:11,19-21`).

Тесты без железа: подставной линк + арифметика шкалы, сверенная с живыми числами
стенда (`raw=619 -> 62353 мВ` из инцидента 2026-10-04, `raw=323 -> 32537 мВ` из
инцидента 2026-10-05).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'pc3_commission.py'

FOC_IDLE = ('@FOC:t=1000:run_id=UNSET:map_id=M0:map_crc32=00000000:I1=0:I2=0:Ires=0:'
            'Id=0:Iq=0:Id_ref=2000:Iq_ref=0:VBUS=0:STATE=0:SPD=0:TH=0:sector=0:window=0:'
            'CCR1=500:CCR2=500:CCR3=500:ADC_STATUS=2:FAULT=0:FAULT_R=0:FAIL=0:RUN=0:'
            'em_stop1=1:em_stop2=1\r\n> ')
PWM_OFF = '@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0\r\n> '


def _load():
    spec = importlib.util.spec_from_file_location('pc3_commission', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeLink(object):
    """Мини-линк: отвечает на команды, как BOARD-образ (только чтения + арм)."""

    def __init__(self, adc_raw_frame, foc_line=FOC_IDLE):
        self.adc = adc_raw_frame + '\r\n> '
        self.foc_line = foc_line
        self.sent = []

    def idle(self, seconds=0.0):
        return self.foc_line

    def command(self, line, quiet=0.0, limit=0.0):
        self.sent.append(line)
        if line == 'a':
            return self.adc
        if line == 'a?':
            return '@ADC:STATUS:offset_i1=2039:stream=0\r\n> '
        if line == 'p?':
            return PWM_OFF
        if line.startswith('mcarm='):
            return '@MC:ARM:cap=1:rc=0:offsets_valid=1:inj_start_rc=0\r\n> '
        if line == 'mapcap status':
            return ('@MC:STATUS:state=1:term=0:cap=1:frames=0:dropped=0:periods=0:avail=0:'
                    'detail=0:raw_vbus=0:vbus_mv=0:i1_ma=0:i2_ma=0:adc_status=0:sector=0:'
                    'window=0\r\n> ')
        return ''

    def close(self):
        pass


def test_scale_matches_live_bench_numbers():
    m = _load()
    assert m.VBUS_VREF_MV == 3300 and m.VBUS_DIVIDER == 125 and m.ADC_MAX_CODE == 4095
    # живые числа стенда: 60 В -> raw=619 (@FOC:VBUS=62353 мВ), 32.5 В -> raw=323
    assert round(619 * m.VBUS_MV_PER_COUNT) == 62353
    assert round(323 * m.VBUS_MV_PER_COUNT) == 32537


def test_raw_frame_pattern_does_not_match_status_answer():
    m = _load()
    # `a?` (offset-статус) не имеет права подменять сырой кадр
    assert m.first_line('@ADC:STATUS:offset_i1=2039:stream=0', m.ADC_RAW) is None
    body = m.first_line('> a\r\n@ADC:I1=2040:I2=2070:Ires=1:VBUS=323\r\n> ', m.ADC_RAW)
    assert body == 'I1=2040:I2=2070:Ires=1:VBUS=323'


class CaptureFakeLink(FakeLink):
    """Линк для полной цепочки capture: терминальный state=3, build -> @MAP:READY."""

    def __init__(self, adc_raw_frame, build_answer, drain_records=0):
        FakeLink.__init__(self, adc_raw_frame)
        self.build_answer = build_answer
        self.drain_records = drain_records

    def command(self, line, quiet=0.0, limit=0.0):
        if line == 'mapcap status':
            self.sent.append(line)
            return ('@MC:STATUS:state=3:term=0:cap=2:frames=8:dropped=0:periods=0:avail=8:'
                    'detail=0:raw_vbus=0:vbus_mv=0:i1_ma=0:i2_ma=0:adc_status=0:sector=0:'
                    'window=0\r\n> ')
        if line == 'mapcap run':
            self.sent.append(line)
            return '@MC:RUN:rc=0\r\n> '
        if line.startswith('mapcap build='):
            self.sent.append(line)
            return self.build_answer
        if line == 'mapcap drain':
            self.sent.append(line)
            rec = ('@MC:REC:cap=2:seq=24:raw_i1=2042:raw_i2=2088:raw_ct=51:raw_vbus=323:'
                   'i1=38:i2=255:vbus=32536:ccr1=625,500,375:ccr8=500,375,625:arr=999:'
                   'trig=1329944369:status=7:fault=0\r\n> ')
            return rec * self.drain_records + \
                '@MC:DRAIN:records=%d\r\n> ' % self.drain_records
        return FakeLink.command(self, line, quiet, limit)


def test_capture_build_runs_before_drain(capsys):
    """Регресс 2026-10-05: drain вычерпывает буфер -> build после него получает
    `@MAP:BUILD:BLOCKED:…:AVAILABLE=0`. Порядок обязан быть run -> build -> drain."""
    m = _load()
    link = CaptureFakeLink('@ADC:I1=2041:I2=2099:Ires=0:VBUS=320',
                           '@MAP:READY:records=8:rows=48\r\n> ')
    rc = m.cmd_capture(link, m.build_parser().parse_args(
        ['capture', '1112490322', '--allow-energize', '--vbus-min', '20000', '--build']))
    out = capsys.readouterr().out
    assert '@MAP:READY:records=8:rows=48' in out
    assert rc == 0
    assert link.sent.index('mapcap run') < link.sent.index('mapcap build=1112490322')
    assert link.sent.index('mapcap build=1112490322') < link.sent.index('mapcap drain')


def test_capture_reports_failclosed_qualification(capsys):
    """BOARD-профиль fail-closed по recon (`recon->valid=false`): это НЕ сбой порядка,
    capture остаётся валидным evidence -> rc=0 с явной строкой FAIL-CLOSED."""
    m = _load()
    link = CaptureFakeLink('@ADC:I1=2041:I2=2099:Ires=0:VBUS=320',
                           '@MAP:BUILD:ERROR:QUALIFICATION\r\n> ')
    rc = m.cmd_capture(link, m.build_parser().parse_args(
        ['capture', '1112490322', '--allow-energize', '--vbus-min', '20000', '--build']))
    out = capsys.readouterr().out
    assert 'FAIL-CLOSED: offline recon absent' in out
    assert rc == 0


def test_capture_fails_when_build_blocked_by_empty_buffer(capsys):
    """Обратный случай: `AVAILABLE=0` (build после drain) — это ошибка порядка команд."""
    m = _load()
    link = CaptureFakeLink('@ADC:I1=2041:I2=2099:Ires=0:VBUS=320',
                           '@MAP:BUILD:BLOCKED:CAPTURE_STATE=3:TERM=0:AVAILABLE=0\r\n> ')
    rc = m.cmd_capture(link, m.build_parser().parse_args(
        ['capture', '1112490322', '--allow-energize', '--vbus-min', '20000', '--build']))
    out = capsys.readouterr().out
    assert 'FAIL: map build/load did not reach @MAP:READY' in out
    assert rc == 1


TRUNCATED_FOC = '@FOC:t=1:STATE=0:VBUS=0\r\n'


def test_first_line_ignores_unterminated_tail():
    """Обрезанный хвост буфера (нет `\\n`) не должен считаться кадром."""
    m = _load()
    text = ('x\r\n@FOC:t=1:FAULT=0:FAULT_R=0:RUN=0:sector=0\r\n'
            '@FOC:t=2:FAU')
    assert m.first_line(text, m.FOC_FRAME) == 't=1:FAULT=0:FAULT_R=0:RUN=0:sector=0'


def test_gate_foc_refuses_when_frame_is_incomplete():
    """Fail-closed: кадр без FAULT/FAULT_R/RUN не должен выглядеть здоровым."""
    m = _load()
    link = FakeLink('@ADC:I1=1:I2=1:Ires=0:VBUS=320', foc_line=TRUNCATED_FOC)
    ok, reason, _foc = m.gate_foc(link)
    assert ok is False
    assert 'incomplete @FOC frame (missing FAULT,FAULT_R,RUN)' in reason
    assert 'refusing' in reason


def test_capture_refuses_on_incomplete_foc_frame(capsys):
    m = _load()
    link = FakeLink('@ADC:I1=1:I2=1:Ires=0:VBUS=320', foc_line=TRUNCATED_FOC)
    rc = m.cmd_capture(link, _capture_args(m))
    out = capsys.readouterr().out
    assert 'REFUSED: incomplete @FOC frame' in out
    assert rc == 2


def test_read_vbus_mv_uses_the_raw_frame():
    m = _load()
    link = FakeLink('@ADC:I1=2040:I2=2070:Ires=1:VBUS=323')
    raw, mv = m.read_vbus_mv(link)
    assert (raw, mv) == (323, 32537)
    assert 'a' in link.sent
    # кадра нет (не тот образ/нет телеметрии) -> fail-closed, не 0 мВ
    raw, mv = m.read_vbus_mv(FakeLink(''))
    assert (raw, mv) == (None, None)


def _capture_args(m, vbus_min='20000'):
    return m.build_parser().parse_args(
        ['capture', '1112490322', '--allow-energize', '--vbus-min', vbus_min])


def test_capture_gate_passes_when_foc_vbus_is_zero_but_link_is_powered(capsys):
    """Регресс инцидента 2026-10-05: @FOC:VBUS=0 при реальных 32.5 В не блокирует шаг."""
    m = _load()
    rc = m.cmd_capture(FakeLink('@ADC:I1=2040:I2=2070:Ires=1:VBUS=323'), _capture_args(m))
    out = capsys.readouterr().out
    assert 'REFUSED: VBUS' not in out
    assert 'raw=323 -> 32537 mV' in out
    assert 'note: @FOC VBUS=0' in out
    assert rc == 1  # дальше арм прошёл, run не подтверждён подставным линком


def test_capture_gate_refuses_on_unpowered_link(capsys):
    m = _load()
    rc = m.cmd_capture(FakeLink('@ADC:I1=2040:I2=2070:Ires=1:VBUS=100'), _capture_args(m))
    out = capsys.readouterr().out
    assert 'REFUSED: VBUS=10073 mV < --vbus-min=20000 mV' in out
    assert rc == 2


def test_capture_gate_refuses_without_raw_frame(capsys):
    m = _load()
    rc = m.cmd_capture(FakeLink(''), _capture_args(m))
    out = capsys.readouterr().out
    assert 'REFUSED: no raw @ADC frame' in out
    assert rc == 2


def test_calib_gate_refuses_on_powered_link(capsys):
    """calib раньше смотрел @FOC:VBUS=0 -> пропускал калибровку при поданном звене."""
    m = _load()
    args = m.build_parser().parse_args(['calib', '--max-vbus-for-calib', '1000'])
    rc = m.cmd_calib(FakeLink('@ADC:I1=2040:I2=2070:Ires=1:VBUS=323'), args)
    out = capsys.readouterr().out
    assert 'REFUSED: VBUS=32537 mV > --max-vbus-for-calib=1000' in out
    assert rc == 2


def test_status_reports_raw_vbus_and_warns_about_idle_zero(capsys):
    m = _load()
    rc = m.cmd_status(FakeLink('@ADC:I1=2040:I2=2070:Ires=1:VBUS=323'), None)
    out = capsys.readouterr().out
    assert 'VBUS(raw a)      raw=323 -> 32537 mV' in out
    assert 'VBUS=0 in idle is NOT' in out
    assert rc == 0
