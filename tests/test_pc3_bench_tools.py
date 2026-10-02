"""Регрессионные тесты стендового комплекта ПК-3.

Проверяются ровно те дефекты, из-за которых комплект v1 не приняли:

1. инструменты не падают ``UnicodeEncodeError`` на консоли оператора (cp1251) и на
   худшей консоли (ascii) — в тестах это эмулируется ``PYTHONIOENCODING``;
2. ``breakdiag_parse.py`` разбирает УСЕЧЁННУЮ строку ``@BRK`` из возврата ПК-3
   (в v1 — ``ValueError``/traceback) и различает коды возврата 0/1/2/3;
3. ``vf_raw.py`` (захват ответов платы) присутствует в комплекте;
4. сборка и проверка комплекта воспроизводимы и обнаруживают подмену файлов
   (``scripts/assemble_pc3_bench_kit.py``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / 'tools' / 'pc3_bench'
ASSEMBLE = ROOT / 'scripts' / 'assemble_pc3_bench_kit.py'
# реальная усечённая строка из возврата ПК-3 (pc3_vf2_return_20260926/README_RETURN_PC3.md:20)
TRUNCATED_BRK = '@BRK:valid=1:seq=1:src=TIM1:\u2026:sr=81,81:sd=1,1:bd=1CC0,1CC0\n'
TRUNCATED_BRK_NO_BD = '@BRK:valid=1:seq=2:src=TIM8:cyc=5:sr=81,81:sd=1,1\n'
FULL_BRK_HEALTHY = ('@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=81,81:sd=1,1:bd=9600,9600:'
                    'ce=5555,5555:cnt=0,0:cap=0,0,0\n')


def run(script, *args, console: str = 'cp1251', timeout: int = 300, extra_env: dict = None):
    """Запуск инструмента отдельным процессом — так, как это делает оператор."""
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = console
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, env=env, timeout=timeout,
                          encoding=console, errors='replace')


def load_assembler():
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    import assemble_pc3_bench_kit
    return assemble_pc3_bench_kit


# ---------------------------------------------------------------- сессия 1: инструменты

def test_enc_sign_selftest_survives_cp1251_console():
    res = run(TOOLS / 'enc_sign.py', '--selftest', console='cp1251')
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'Traceback' not in res.stdout + res.stderr
    assert 'cp1251 OK' in res.stdout
    assert '0 провалов' in res.stdout


def test_enc_sign_selftest_survives_ascii_console():
    res = run(TOOLS / 'enc_sign.py', '--selftest', console='ascii')
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'Traceback' not in res.stdout + res.stderr


def test_enc_sign_reports_encoder_sign(tmp_path):
    capture = tmp_path / 'enc_stream.txt'
    capture.write_text('\n'.join('@ENC:angle=%d:speed=12:period_us=1000:pulse_us=500:err=0'
                                 % (200 * i) for i in range(40)), encoding='utf-8')
    res = run(TOOLS / 'enc_sign.py', capture)
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'ПЛЮС' in res.stdout


def test_breakdiag_selftest_survives_cp1251_and_ascii_console():
    for console in ('cp1251', 'ascii'):
        res = run(TOOLS / 'breakdiag_parse.py', '--selftest', console=console)
        assert res.returncode == 0, console + ' | ' + res.stdout + res.stderr
        assert 'Traceback' not in res.stdout + res.stderr


def test_breakdiag_selftest_reports_cp1251_safe_output():
    res = run(TOOLS / 'breakdiag_parse.py', '--selftest', console='cp1251')
    assert 'cp1251 OK' in res.stdout


def test_breakdiag_truncated_line_from_pc3_return_is_parsed(tmp_path):
    """Строка из README_RETURN_PC3.md:20 (нет cyc/ce/cnt/cap) — разбор без падения."""
    sample = tmp_path / 'brk_raw.txt'
    sample.write_text(TRUNCATED_BRK, encoding='utf-8')
    res = run(TOOLS / 'breakdiag_parse.py', sample)
    out = res.stdout + res.stderr
    assert 'Traceback' not in out, out
    assert res.returncode == 1, out                 # противоречие (BKP=1) найдено несмотря на усечение
    assert 'УСЕЧЕНА' in res.stdout
    assert 'ПРОТИВОРЕЧИЕ' in res.stdout
    assert 'BKP' in res.stdout


def test_breakdiag_truncated_line_without_bd_reports_code_2(tmp_path):
    sample = tmp_path / 'brk_raw.txt'
    sample.write_text(TRUNCATED_BRK_NO_BD, encoding='utf-8')
    res = run(TOOLS / 'breakdiag_parse.py', sample)
    assert 'Traceback' not in res.stdout + res.stderr
    assert res.returncode == 2, res.stdout + res.stderr


def test_breakdiag_full_firmware_format_line_is_ok(tmp_path):
    """Полная строка в формате src/cli.c:147 — разбор полный, противоречий нет."""
    sample = tmp_path / 'brk_raw.txt'
    sample.write_text(FULL_BRK_HEALTHY, encoding='utf-8')
    res = run(TOOLS / 'breakdiag_parse.py', sample)
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'OK' in res.stdout
    assert 'УСЕЧЕНА' not in res.stdout


def test_breakdiag_input_without_brk_line(tmp_path):
    sample = tmp_path / 'not_brk.txt'
    sample.write_text('hello world\n', encoding='utf-8')
    res = run(TOOLS / 'breakdiag_parse.py', sample)
    assert res.returncode == 3, res.stdout + res.stderr


# ---------------------------------------------------------------- инструменты сессии 2 / комплект

def test_kit_has_capture_tool_with_pyserial_hint():
    src = (TOOLS / 'vf_raw.py').read_text(encoding='utf-8')
    assert 'import serial' in src
    assert 'pyserial' in src        # подсказка об установке вместо traceback
    assert 'sys.exit(3)' in src


def test_vflog_step_report_selftest_passes():
    res = run(TOOLS / 'vflog_step_report_selftest.py', console='cp1251')
    assert res.returncode == 0, res.stdout + res.stderr
    assert '0 провалов' in res.stdout


def test_vflog_selftest_is_locale_independent_utf8_mode():
    """PYTHONUTF8=1 эмулирует локаль Linux (UTF-8): без явной кодировки дочернего процесса
    самотест падал UnicodeDecodeError и ронял CI при зелёном локальном прогоне."""
    res = run(TOOLS / 'vflog_step_report_selftest.py', console='cp1251', extra_env={'PYTHONUTF8': '1'})
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'UnicodeDecodeError' not in res.stdout + res.stderr


def test_assembler_selftest_builds_and_verifies_kit():
    res = run(ASSEMBLE, '--selftest', console='utf-8')
    assert res.returncode == 0, res.stdout + res.stderr
    assert 'OK' in res.stdout


def test_assembler_verify_detects_tampering(tmp_path):
    asm = load_assembler()
    kit = tmp_path / 'kit'
    asm.build_kit(kit, None, verbose=False)
    assert asm.selfcheck(kit) == []

    doc = kit / 'START_HERE_PC3.md'
    doc.write_text(doc.read_text(encoding='utf-8') + '\nправка\n', encoding='utf-8', newline='')
    assert any('РАСХОЖДЕНИЕ' in p for p in asm.selfcheck(kit))

    doc.unlink()
    assert any('НЕТ файла' in p for p in asm.selfcheck(kit))


def test_assembler_flags_extra_file_and_bare_vf_raw_path(tmp_path):
    asm = load_assembler()
    kit = tmp_path / 'kit'
    asm.build_kit(kit, None, verbose=False)

    extra = kit / 'лишний.txt'
    extra.write_text('x', encoding='utf-8')
    assert any('ЛИШНИЙ' in p for p in asm.selfcheck(kit))
    extra.unlink()

    howto = kit / 'SESSION_1_SIGN_BREAK/TOOLS/HOWTO.md'
    howto.write_text(howto.read_text(encoding='utf-8') + '\npy -3 vf_raw.py COM4 dump\n',
                     encoding='utf-8', newline='')
    assert any('без пути TOOLS/' in p for p in asm.selfcheck(kit))


def test_assembler_flags_absolute_drive_path_in_instructions(tmp_path):
    asm = load_assembler()
    kit = tmp_path / 'kit'
    asm.build_kit(kit, None, verbose=False)

    start = kit / 'START_HERE_PC3.md'
    start.write_text(start.read_text(encoding='utf-8').replace(
        'cd /d <корень этого комплекта>', 'cd /d E:\\'), encoding='utf-8', newline='')
    assert any('абсолютный путь носителя' in p for p in asm.selfcheck(kit))


def test_assembler_rejects_foreign_image(tmp_path):
    asm = load_assembler()
    image_dir = tmp_path / 'image'
    image_dir.mkdir()
    for name in asm.KIT_IMAGE:
        (image_dir / name).write_bytes(b'not-the-agreed-image')
    with pytest.raises(ValueError):
        asm.build_kit(tmp_path / 'kit', image_dir, verbose=False)


def test_assembler_quarantine_moves_and_hashes(tmp_path):
    asm = load_assembler()
    old_pkg = tmp_path / 'old_pkg'
    old_pkg.mkdir()
    (old_pkg / 'README.md').write_text('устаревшая инструкция\n', encoding='utf-8')
    # у снятого пакета свой манифест: он ДОЛЖЕН попасть в манифест карантина
    (old_pkg / 'SHA256SUMS').write_text('deadbeef *README.md\n', encoding='utf-8')
    quarantine = tmp_path / 'kit' / asm.QUARANTINE_DIR

    assert asm.do_quarantine(quarantine, [old_pkg]) == 1
    assert not old_pkg.exists()
    entries = asm.read_manifest(quarantine)
    assert 'old_pkg/README.md' in entries
    assert 'old_pkg/SHA256SUMS' in entries
    assert asm.MANIFEST not in entries
    assert asm.verify_tree(quarantine) == []
