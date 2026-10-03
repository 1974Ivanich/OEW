"""Регрессионные тесты пакета FOC 48-ячеечного скана (ред. 2) — без железа.

Проверяются ровно те дефекты, из-за которых ред. 1 нельзя было запускать:

1. шкала: числа платы ab1274d -> f_pwm=5000, f_JEOS=5000, N_expected(2 с)=10000
   (ред. 1 давала 294/588/1176 — ошибка x17 из-за TCLK и выпавшего RCR);
2. шкала не может «молча» уехать: TCLK вместо CLK отвергается (rc=1), RCR учтён;
3. драйвер: --dry-run не содержит `f`, содержит `i=` строго после `1`;
4. сессия целиком на подставном линке: cells.csv, автостопы по FAULT/em_stop, нет `f`;
5. консоль ASCII/cp1251: инструменты не падают UnicodeEncodeError (дефект комплектов ПК-3);
6. самопроверка пакета: PASS, SHA256SUMS покрывает состав без пропусков и лишних строк;
7. сборка ZIP детерминирована, сайдкар `.sha256` сходится, `--verify` проходит;
8. шаг 0 приписан правильной команде: `@PWM:FULL` (`SYS`/`T1:PSC`/`T1:ARR`) печатает `pdump`,
   а не `p?`; `p?` — только десятичные `CR1`/`CCER`/`BDTR`/`CNT` (дефект B11);
9. словарь команд: энкодер читается `enc` (`@ENC`), а `eangle` — только поле `@VFLOG` (B12).
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'docs' / 'pc3_package' / 'foc_scan_48cell_v2'
TOOLS = PKG / 'TOOLS'
BUILDER = ROOT / 'scripts' / 'build_foc_scan_48cell_pkg.py'
BOARD = ['--clk', '170000000', '--psc', '16', '--arr', '999', '--cms', '3', '--rcr', '1',
         '--tclk', '10000000', '--window', '2.0']
SYS_BEFORE = '@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x41540828:OVR=0:JEOS=100:' \
             'TO=0:JQOVF=0:uart_drp=0:uart_trunc=0\r\n'
SYS_AFTER = '@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x41540828:OVR=0:JEOS=10100:' \
            'TO=0:JQOVF=0:uart_drp=0:uart_trunc=0\r\n'
FOC_OK = '@FOC:t=1000:run_id=r1:map_id=M0:map_crc32=0000ABCD:I1=1200:I2=-300:Ires=900:' \
         'Id=1900:Iq=100:Id_ref=2000:Iq_ref=0:VBUS=48000:STATE=4:SPD=0:TH=0:' \
         'sector=0:window=63:CCR1=500:CCR2=500:CCR3=500:ADC_STATUS=1:' \
         'FAULT=0:FAULT_R=0:FAIL=0:RUN=1:em_stop1=1:em_stop2=1\r\n'


def load(name):
    spec = importlib.util.spec_from_file_location('pc3_' + name, TOOLS / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_tool(script, *args, console=None):
    env = dict(os.environ)
    if console:
        env['PYTHONIOENCODING'] = console
    proc = subprocess.run([sys.executable, str(script)] + list(args),
                          capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


class SessionLink(object):
    """Подставной линк: ответы выдаются по порядку вызовов pump()."""

    def __init__(self, responses, window_text=''):
        self.responses = list(responses)
        self.window_text = window_text
        self.sent = []
        self.buf = ''

    def write(self, cmd):
        self.sent.append(cmd)

    def pump(self, quiet=0.25, limit=5.0):
        txt = self.responses.pop(0) if self.responses else ''
        self.buf += txt
        return txt

    def window(self, seconds):
        self.buf += self.window_text
        return self.window_text

    def close(self):
        pass


# --- 1-3: шкала метрики ---

def test_scale_board_numbers():
    rc, out = run_tool(TOOLS / 'calc_expected.py', *BOARD)
    assert rc == 0, out
    assert 'f_pwm      = 5000.000 Hz' in out
    assert 'f_JEOS     = 5000.000 Hz' in out
    assert 'N_expected = 10000' in out
    assert 'CLK/(PSC+1) cross-check: OK' in out
    assert 'RESULT     : PASS' in out


def test_scale_rejects_tclk_as_clk():
    """Класс дефекта ред. 1: TCLK подставлен в поле CLK -> отказ, а не «PASS»."""
    rc, out = run_tool(TOOLS / 'calc_expected.py', '--clk', '10000000', '--psc', '16',
                       '--arr', '999', '--cms', '3', '--rcr', '1', '--window', '2.0')
    assert rc == 1
    assert 'MISMATCH' in out
    assert '294.118' in out          # именно это число давала ред. 1
    assert 'N_expected = 588' in out


def test_scale_rejects_tclk_mismatch():
    rc, out = run_tool(TOOLS / 'calc_expected.py', '--clk', '170000000', '--psc', '16',
                       '--arr', '999', '--cms', '3', '--tclk', '10000000')
    assert rc == 0, out                       # CLK/(PSC+1) == TCLK -> сверка проходит
    rc2, out2 = run_tool(TOOLS / 'calc_expected.py', '--clk', '10000000', '--psc', '16',
                         '--arr', '999', '--cms', '3', '--tclk', '10000000')
    assert rc2 == 1 and 'MISMATCH' in out2


def test_rcr_enters_the_formula():
    calc = load('calc_expected')
    m0 = calc.compute(170000000, 16, 999, 3, 0)
    m1 = calc.compute(170000000, 16, 999, 3, 1)
    assert m0['f_jeos'] == pytest.approx(10000.0)   # RCR=0: TRGO на каждом update
    assert m1['f_jeos'] == pytest.approx(5000.0)    # RCR=1 (плата): через один
    assert m1['f_pwm'] == pytest.approx(5000.0)
    assert m1['f_uev'] == pytest.approx(10000.0)
    assert 'RCR=1' in calc.formula(3, 1)


def test_edge_aligned_has_one_update_per_period():
    calc = load('calc_expected')
    m = calc.compute(170000000, 16, 999, 0, 0)
    assert m['f_pwm'] == pytest.approx(10000.0)
    assert m['f_uev'] == pytest.approx(10000.0)
    assert m['f_jeos'] == pytest.approx(10000.0)


# --- 4: драйвер ---

def _lines(out):
    return [ln.strip() for ln in out.splitlines()]


def test_dry_run_plan_has_no_f_and_i_after_1():
    rc, out = run_tool(TOOLS / 'scan_48cell.py', '--port', 'COM4', '--amp', '2000,3000,4000,5000',
                       '--angles-el', '0', '--n-expected', '10000', '--dry-run')
    assert rc == 0, out
    assert '"f" among them: 0' in out
    assert sum(1 for ln in _lines(out) if ln == 'send: 1') == 4
    assert out.index('send: 1') < out.index('send: i=2000,0') < out.index('send: 0')


def test_real_run_requires_n_expected():
    rc, out = run_tool(TOOLS / 'scan_48cell.py', '--port', 'COM4', '--amp', '2000',
                       '--angles-el', '0')
    assert rc == 2 and '--n-expected is required' in out


def test_amp_above_clamp_rejected():
    rc, out = run_tool(TOOLS / 'scan_48cell.py', '--amp', '20000', '--dry-run')
    assert rc == 2 and 'FOC_I_MAX_MA' in out


def test_current_pair_and_plan():
    scan = load('scan_48cell')
    assert scan.current_pair(2000, 0) == (2000, 0)
    assert scan.current_pair(1000, 90) == (0, 1000)
    assert scan.current_pair(1000, 180) == (-1000, 0)
    cells = scan.plan_cells([2000, 3000], [0], True)
    assert [c['cell'] for c in cells] == ['A2000_ANG0', 'A3000_ANG0']
    assert (cells[0]['id_ma'], cells[0]['iq_ma']) == (2000, 0)
    labeled = scan.plan_cells([2000], [0], False)
    assert (labeled[0]['id_ma'], labeled[0]['iq_ma']) == (0, 0)   # --no-set-current


def test_parse_foc_uses_live_fields_not_sys():
    scan = load('scan_48cell')
    foc = scan.parse_foc(FOC_OK)
    assert foc['fault'] == 0 and foc['run'] == 1 and foc['ccr1'] == 500
    assert foc['id_ref'] == 2000 and foc['iq_ref'] == 0
    assert foc['em_stop1'] == 1 and foc['em_stop2'] == 1
    sysb = scan.parse_sys(SYS_BEFORE)
    assert sysb['jeos'] == 100 and sysb['clk'] == 170000000   # дефект B3: живое — в @FOC


# --- 5: сессия целиком на подставном линке ---

def _green_link(n_cells, post_cmds=()):
    resp = []
    for _ in range(n_cells):
        resp += [SYS_BEFORE, 'FOC started\r\n> ', '@I:OK:Id=2000:Iq=0\r\n> ', '', SYS_AFTER]
        resp += [''] * len(post_cmds)          # пост-мортем: ответы не анализируются
    return SessionLink(resp, window_text=FOC_OK)


def _foc_line(replacements):
    text = FOC_OK
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new)
    return text


def test_session_green_path_writes_csv_without_f(tmp_path):
    scan = load('scan_48cell')
    cells = scan.plan_cells([2000, 3000], [0], True)
    link = _green_link(2, ['sysinfo'])
    session = scan.Session(link, cells, str(tmp_path / 'run1'), window=0.0,
                           n_expected=10000.0, post=['sysinfo'])
    assert session.run() == 0 and session.abort is None
    assert [c.strip() for c in link.sent if c.strip() == 'f'] == []
    assert link.sent[0] == 'sysinfo' and link.sent[1] == '1'
    assert link.sent[2].startswith('i=') and link.sent[3] == '0'
    rows = list(csv.DictReader(open(tmp_path / 'run1' / 'cells.csv', encoding='utf-8')))
    assert len(rows) == 2
    assert rows[0]['cell'] == 'A2000_ANG0' and rows[1]['cell'] == 'A3000_ANG0'
    assert rows[0]['d_jeos'] == '10000' and rows[0]['ratio'] == '1.0'
    assert rows[0]['level'] == 'L2' and rows[0]['id_ref'] == '2000'
    assert rows[0]['em_stop1'] == '1' and rows[0]['fault'] == '0'
    summary = (tmp_path / 'run1' / 'summary.txt').read_text(encoding='utf-8')
    assert 'decode cross-check: ratio ~ 1: decode confirmed by data' in summary
    assert '"f" sent: 0' in summary and 'levels: L2=2' in summary
    assert (tmp_path / 'run1' / 'session_raw.log').exists()


def test_session_stops_on_fault(tmp_path):
    scan = load('scan_48cell')
    cells = scan.plan_cells([2000, 3000], [0], True)
    link = SessionLink([SYS_BEFORE, 'FOC started\r\n> ', '@I:OK:Id=2000:Iq=0\r\n> ', '',
                        SYS_AFTER],
                       window_text=_foc_line([('FAULT=0:FAULT_R=0', 'FAULT=1:FAULT_R=7')]))
    session = scan.Session(link, cells, str(tmp_path / 'run1'), window=0.0,
                           n_expected=10000.0, post=[])
    assert session.run() == 1
    assert 'FAULT latched' in session.abort
    assert link.sent.count('1') == 1                     # вторая ячейка не стартовала
    rows = list(csv.DictReader(open(tmp_path / 'run1' / 'cells.csv', encoding='utf-8')))
    assert len(rows) == 1 and rows[0]['fault'] == '1'


def test_session_stops_on_sd_low_and_waiver_works(tmp_path):
    scan = load('scan_48cell')
    cells = scan.plan_cells([2000], [0], True)
    sd_low = _foc_line([('em_stop1=1', 'em_stop1=0')])

    def link():
        return SessionLink([SYS_BEFORE, 'FOC started\r\n> ', '@I:OK:Id=2000:Iq=0\r\n> ', '',
                            SYS_AFTER], window_text=sd_low)

    strict = scan.Session(link(), cells, str(tmp_path / 'a'), window=0.0,
                          n_expected=10000.0, post=[])
    assert strict.run() == 1 and 'SD line asserted' in strict.abort
    waived = scan.Session(link(), cells, str(tmp_path / 'b'), window=0.0,
                          n_expected=10000.0, post=[], sd_stop=False)
    assert waived.run() == 0 and waived.abort is None


def test_session_stops_when_not_started_and_names_rc5(tmp_path):
    scan = load('scan_48cell')
    cells = scan.plan_cells([2000], [0], True)
    reply = '@FOC:START:FAIL:rc=-5 (0=OK -1=clock/fault -2=map_unverified -3=calib ' \
            '-4=arm -5=pwm_enable -6=params_out_of_range)\r\n'
    link = SessionLink([SYS_BEFORE, reply, '@I:OK:Id=2000:Iq=0\r\n> ', '', SYS_AFTER],
                       window_text='')
    session = scan.Session(link, cells, str(tmp_path / 'run1'), window=0.0,
                           n_expected=10000.0, post=[])
    assert session.run() == 1
    assert 'rc=-5' in session.abort and 'energize' in session.abort


def test_session_stops_when_i_not_acknowledged(tmp_path):
    scan = load('scan_48cell')
    cells = scan.plan_cells([2000], [0], True)
    link = SessionLink([SYS_BEFORE, 'FOC started\r\n> ', 'err: no such command\r\n> ', '',
                        SYS_AFTER], window_text=FOC_OK)
    session = scan.Session(link, cells, str(tmp_path / 'run1'), window=0.0,
                           n_expected=10000.0, post=[])
    assert session.run() == 1 and 'i= not acknowledged' in session.abort


def test_level_and_decode_hint():
    scan = load('scan_48cell')
    assert scan.level_of(0, 10000) == 'L0'
    assert scan.level_of(1, 10000) == 'L1'
    assert scan.level_of(10000, 10000) == 'L2'
    assert scan.level_of(5000, 10000) == 'unscaled'
    assert scan.level_of(None, 10000) == 'n/a'
    assert 'decode confirmed' in scan.decode_hint(1.0)
    assert '2x too high' in scan.decode_hint(0.5)
    assert '2x too low' in scan.decode_hint(2.0)


# --- 6: консоль и пакет ---

@pytest.mark.parametrize('console', ['cp1251', 'ascii'])
def test_tools_survive_legacy_console_encoding(console):
    rc, out = run_tool(TOOLS / 'calc_expected.py', *BOARD, console=console)
    assert rc == 0 and 'UnicodeEncodeError' not in out, out
    rc2, out2 = run_tool(TOOLS / 'scan_48cell.py', '--amp', '2000', '--angles-el', '0',
                         '--n-expected', '10000', '--dry-run', console=console)
    assert rc2 == 0 and 'UnicodeEncodeError' not in out2, out2


def test_check_package_passes():
    rc, out = run_tool(TOOLS / 'check_package.py')
    assert rc == 0, out
    assert 'TOTAL: PASS' in out
    assert 'OK rc=0 5000/5000/10000' in out
    assert 'driver dry-run invariants (no f; i= after 1): OK' in out
    assert 'step 0 attribution (pdump owns @PWM:FULL; p? = CR1/CCER/BDTR/CNT, dec): OK' in out
    assert 'command vocabulary (enc/@ENC named; eangle only as @VFLOG; rev/cv only as absent): OK' in out


def test_docs_do_not_carry_the_v1_trap():
    for name in ('COMMANDS.md', 'START_HERE_PC3.md', 'README.md'):
        text = (PKG / name).read_text(encoding='utf-8')
        assert '--sysclk' not in text, name
        assert '--clk' in text, name
    proto = (PKG / 'FOC_SCAN_48CELL_PROTOCOL.md').read_text(encoding='utf-8')
    assert 'RCR' in proto and 'N_expected=10 000' in proto


def test_docs_do_not_misattribute_the_step0_signature():
    """B11: `@PWM:FULL`/`SYS`/`T1:PSC`/`T1:ARR` не приписываются `p?`; радиксы CR1 указаны."""
    chk = load('check_package')                          # те же правила, что у самой проверки
    blob = ''
    docs = ('README.md', 'START_HERE_PC3.md', 'COMMANDS.md', 'FOC_SCAN_48CELL_PROTOCOL.md',
            'IDENTITY_AND_BASELINE.md', 'RETURN_TEMPLATE.md', 'CORRECTIONS_v2.md')
    for name in docs:
        text = (PKG / name).read_text(encoding='utf-8')
        blob += text + '\n'
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if '@PWM:FULL' in line:
                ctx = ' '.join(lines[max(0, i - 2):i + 1])
                assert 'pdump' in ctx, (name, line)
            if 'p?' in line and any(k in line for k in ('SYS', 'PSC', 'ARR')):
                assert any(n in line for n in chk.NEG), (name, line)
    assert 'pdump' in blob and 'T1:ARR' in blob
    assert 'CR1=224' in blob and '0xE0' in blob          # оба радикса зафиксированы


def test_check_package_catches_step0_misattribution(tmp_path):
    """Негативный тест правила `check_step0_attribution`: ловушка ред. 2.0 обязана валить прогон."""
    copy = tmp_path / 'pkg'
    shutil.copytree(PKG, copy)
    rc0, out0 = run_tool(copy / 'TOOLS' / 'check_package.py')
    assert rc0 == 0 and 'TOTAL: PASS' in out0, out0      # копия без правок проходит
    readme = copy / 'README.md'
    trap = '| `p?` (`@PWM:FULL…`: ожидается `SYS=170000000`, `T1:PSC=16`, `T1:ARR=999`) | |\n'
    readme.write_text(readme.read_text(encoding='utf-8') + '\n' + trap, encoding='utf-8')
    sums = copy / 'SHA256SUMS'                          # правим манифест: падение должно дать
    rows = []                                           # именно новое правило, а не хеши
    for line in sums.read_text(encoding='utf-8').splitlines():
        if line.strip().endswith('README.md'):
            line = '%s  README.md' % hashlib.sha256(readme.read_bytes()).hexdigest()
        rows.append(line)
    sums.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    rc1, out1 = run_tool(copy / 'TOOLS' / 'check_package.py')
    assert rc1 == 1, out1
    assert 'step 0 attribution' in out1 and 'FAIL' in out1


def test_docs_name_the_real_encoder_command():
    """B12: энкодер читается командой `enc` (`@ENC`), `eangle` — только поле `@VFLOG`."""
    chk = load('check_package')
    assert chk.check_command_vocabulary() == 0
    for name in ('FOC_SCAN_48CELL_PROTOCOL.md', 'START_HERE_PC3.md'):
        text = (PKG / name).read_text(encoding='utf-8')
        assert '`enc`' in text and '@ENC' in text, name
        for line in text.splitlines():
            if 'eangle' in line:
                assert any(c in line for c in chk.EANGLE_CAVEAT), (name, line)


def test_check_package_catches_encoder_misattribution(tmp_path):
    """Негативный тест правила `check_command_vocabulary`: `eangle` вместо `enc` валит прогон."""
    copy = tmp_path / 'pkg'
    shutil.copytree(PKG, copy)
    proto = copy / 'FOC_SCAN_48CELL_PROTOCOL.md'
    proto.write_text(proto.read_text(encoding='utf-8').replace('`enc`', '`eangle`'),
                     encoding='utf-8')
    sums = copy / 'SHA256SUMS'                          # манифест правим: падать должно правило,
    rows = []                                           # а не расхождение хешей
    for line in sums.read_text(encoding='utf-8').splitlines():
        if line.strip().endswith('FOC_SCAN_48CELL_PROTOCOL.md'):
            line = '%s  FOC_SCAN_48CELL_PROTOCOL.md' % \
                   hashlib.sha256(proto.read_bytes()).hexdigest()
        rows.append(line)
    sums.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    rc, out = run_tool(copy / 'TOOLS' / 'check_package.py')
    assert rc == 1, out
    assert 'command vocabulary' in out and 'FAIL' in out


def test_package_files_match_manifest_without_crlf():
    rows = {}
    for line in (PKG / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        if line.strip():
            digest, rel = line.split(None, 1)
            rows[rel.strip()] = digest
    assert len(rows) >= 10, rows
    for rel, want in rows.items():
        data = (PKG / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == want, rel
        assert b'\r\n' not in data, rel


# --- 7: сборка ZIP ---

def test_builder_zip_is_reproducible_and_verified(tmp_path):
    out = tmp_path / 'out'
    rc1, o1 = run_tool(BUILDER, '--out', str(out), '--name', 'pkg.zip')
    assert rc1 == 0, o1
    first = (out / 'pkg.zip').read_bytes()
    sidecar = (out / 'pkg.zip.sha256').read_text(encoding='ascii').split()[0]
    assert sidecar == hashlib.sha256(first).hexdigest()
    rc2, o2 = run_tool(BUILDER, '--out', str(out), '--name', 'pkg.zip')
    assert rc2 == 0, o2
    assert (out / 'pkg.zip').read_bytes() == first           # детерминированность
    rc3, o3 = run_tool(BUILDER, '--verify', str(out / 'pkg.zip'))
    assert rc3 == 0 and 'VERIFY: PASS' in o3
    with zipfile.ZipFile(out / 'pkg.zip') as zf:
        names = zf.namelist()
    assert 'TOOLS/scan_48cell.py' in names and 'SHA256SUMS' in names
    assert 'START_HERE_PC3.md' in names


def test_builder_verify_detects_tampered_member(tmp_path):
    out = tmp_path / 'out'
    rc, o = run_tool(BUILDER, '--out', str(out), '--name', 'pkg.zip')
    assert rc == 0, o
    with zipfile.ZipFile(out / 'pkg.zip') as zf:
        items = [(info, zf.read(info.filename)) for info in zf.infolist()]
    bad = out / 'bad.zip'
    with zipfile.ZipFile(bad, 'w', zipfile.ZIP_DEFLATED) as zf:
        for info, data in items:
            extra = b'\n# tampered\n' if info.filename.endswith('.py') else b''
            zf.writestr(info, data + extra)
    rc2, o2 = run_tool(BUILDER, '--verify', str(bad))
    assert rc2 == 1 and 'MISMATCH' in o2
