#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка, проверка и карантин стендового комплекта ПК-3 (`pc3_bench_kit`).

Комплект НЕ собирается руками: состав берётся из репозитория — документы из
`docs/pc3_bench_kit/`, инструменты из `tools/pc3_bench/`; образ копируется из
внешней папки сборки и обязан совпасть по SHA256 с IMAGE_SHA256 (иначе сборка
падает: нельзя подсунуть оператору другой образ).

Режимы:
  --out DIR                  собрать комплект в DIR (--image-dir обязателен)
  --verify DIR               сверить DIR с его SHA256SUMS (+ карантин, если есть)
  --list                     напечатать состав комплекта
  --quarantine-into DIR SRC… убрать в карантин указанные объекты и посчитать их хеши
  --selftest                 собрать комплект без образа во временную папку, сверить
                             манифест и прогнать настоящие самотесты инструментов
                             (носитель и железо не нужны; это ворота CI)

Пример:
  py -3 scripts/assemble_pc3_bench_kit.py --out F:\\pc3_bench_kit_20260926_v2 \\
        --image-dir F:\\pc3_bench_kit_20260926\\IMAGE
  py -3 scripts/assemble_pc3_bench_kit.py --verify F:\\pc3_bench_kit_20260926_v2

Коды возврата: 0 — OK, 1 — расхождение/провал проверки, 2 — ошибка вызова.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / 'docs' / 'pc3_bench_kit'
TOOLS_DIR = ROOT / 'tools' / 'pc3_bench'
MANIFEST = 'SHA256SUMS'
QUARANTINE_DIR = '_SUPERSEDED'

# инструменты: файл в репозитории -> путь внутри комплекта (единый источник правды)
KIT_TOOLS = {
    'vf_raw.py': 'TOOLS/vf_raw.py',
    'enc_sign.py': 'SESSION_1_SIGN_BREAK/TOOLS/enc_sign.py',
    'breakdiag_parse.py': 'SESSION_1_SIGN_BREAK/TOOLS/breakdiag_parse.py',
    'vflog_step_report.py': 'SESSION_2_VF2_VF3/TOOLS/vflog_step_report.py',
    'vflog_step_report_selftest.py': 'SESSION_2_VF2_VF3/TOOLS/vflog_step_report_selftest.py',
    'audit_vf2a.py': 'CONTEXT/audit_vf2a.py',
    'sign_ab_report.py': 'SESSION_3_SIGN_AB/TOOLS/sign_ab_report.py',
    'faultn_report.py': 'SESSION_4_FAULTN_CHARACTERIZATION/TOOLS/faultn_report.py',
}
# Образы комплекта: профиль -> файлы, провенанс, ожидания BDTR и границы применения.
# SHA256 зафиксирован для КАЖДОГО файла: комплект fail-closed, «похожий» образ не примется.
IMAGE_PROFILES = {
    'production': {
        'files': {
            'firmware.bin': ('IMAGE/firmware_production.bin',
                             '96e9747028a5bf6ff7bf87a4e825dbe930e43d4545b8dcaaa2f9a80538d11d64'),
            'firmware.elf': ('IMAGE/firmware_production.elf',
                             '0de9055f926aca8779eeb152cd0215bc86e655e3acd381a80565bcef7b3f30c0'),
        },
        'flags': "EXTRA_CFLAGS='-DOEW_HS1_COMMISSIONING_RELEASE=1'",
        'bdtr': 'покой 0x1CC0 / под током 0x9CC0 (MOE=1, BKE=1)',
        'breakdiag_profile': 'production',
        'use': 'сессии VF-2B / VF-3: штатная защита, все проверки break',
        'safety': 'BKIN активен: низкий SD снимает MOE аппаратно и латчит FAULT_R=18',
    },
    'monitor-only': {
        'files': {
            'firmware.bin': ('IMAGE/firmware_monitor_only.bin',
                             '27a813ccb79327f8b887b7ef9f6f2401e92e1d43b32b09363581ff911146ca39'),
            'firmware.elf': ('IMAGE/firmware_monitor_only.elf',
                             '35a01ee30a9a3b7160a75787f08790fc5f26eece10cf48a7d014224979bcdf7c'),
        },
        'flags': "EXTRA_CFLAGS='-DOEW_HS1_COMMISSIONING_RELEASE=1 -DOEW_SD_MONITOR_ONLY=1'",
        'bdtr': 'покой 0x0CC0 / под током 0x8CC0 (MOE=1, BKE=0)',
        'breakdiag_profile': 'monitor-only',
        'use': 'СЕССИЯ 4: характеризация SD/FAULT_N — диагностический инструмент',
        'safety': 'BKIN ВЫКЛЮЧЕН НАМЕРЕННО (pwm.c:275-277): низкий SD не снимает MOE и не латчит '
                  'FAULT_R; остаются программные защиты (>12 А, VBUS-low, stall) и smart-shutdown модуля',
    },
}
IMAGE_PROVENANCE_KEYS = (
    ('SOURCE_COMMIT', 'ab1274d3506587303672325089d58a72bb941564'),
    ('SOURCE_BRANCH', 'ai2/vf-telemetry-rate-fix'),
    ('MAIN_AT_BUILD', '11ad97daafd77b00fb995877732897d15e74f8e7'),
    ('MAIN_UNTOUCHED', 'YES'),
    ('COMMISSIONING_RELEASE', '1'),
)


def kit_image_paths() -> list:
    """Пути образов внутри комплекта (все профили)."""
    return [kit_path for prof in IMAGE_PROFILES.values()
            for kit_path, _sha in prof['files'].values()]


def image_provenance(profile: str, src_dir: Path) -> str:
    """Текст PROVENANCE для профиля: SHA256, размер и ГРАНИЦЫ применения рядом с образом."""
    prof = IMAGE_PROFILES[profile]
    lines = []
    for key, val in IMAGE_PROVENANCE_KEYS:
        lines.append('%-22s = %s' % (key, val))
    lines.append('%-22s = %s' % ('IMAGE_PROFILE', profile))
    for name, (kit_path, sha) in sorted(prof['files'].items()):
        size = (Path(src_dir) / name).stat().st_size
        lines.append('%-22s = %s (%d Б, в комплекте %s)'
                     % ('IMAGE_SHA256_' + name.split('.')[-1].upper(), sha, size, kit_path))
    lines.append('%-22s = %s' % ('BUILD_FLAGS', prof['flags']))
    lines.append('%-22s = %s' % ('BDTR_EXPECTED', prof['bdtr']))
    lines.append('%-22s = %s' % ('BREAKDIAG_PROFILE', prof['breakdiag_profile']))
    lines.append('%-22s = %s' % ('USE', prof['use']))
    lines.append('%-22s = %s' % ('SAFETY', prof['safety']))
    lines.append('')
    lines.append('Проверка дампа под этот образ:')
    lines.append('  py -3 SESSION_1_SIGN_BREAK/TOOLS/breakdiag_parse.py --profile %s <файл с @BRK>'
                 % prof['breakdiag_profile'])
    return '\n'.join(lines) + '\n'
# документы, которые оператор ИСПОЛНЯЕТ: абсолютные пути носителя в них запрещены
INSTRUCTION_DOCS = (
    'START_HERE_PC3.md',
    'SESSION_1_SIGN_BREAK/PROTOCOL.md',
    'SESSION_1_SIGN_BREAK/RETURN_TEMPLATE.md',
    'SESSION_1_SIGN_BREAK/TOOLS/HOWTO.md',
    'SESSION_2_VF2_VF3/SESSION_VF2_VF3.md',
    'SESSION_2_VF2_VF3/RUNBOOK_VF2_VF3.md',
    'SESSION_2_VF2_VF3/ACCEPTANCE_VF2A_VF2B.md',
    'SESSION_2_VF2_VF3/SESSION_REPORT_TEMPLATE.md',
    'SESSION_3_SIGN_AB/PROTOCOL.md',
    'SESSION_3_SIGN_AB/RETURN_TEMPLATE.md',
    'SESSION_4_FAULTN_CHARACTERIZATION/PROTOCOL.md',
    'SESSION_4_FAULTN_CHARACTERIZATION/RETURN_TEMPLATE.md',
)
DRIVE_RE = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]')
# ошибка = ЗАПУСК vf_raw.py без пути (не упоминание имени инструмента в тексте)
BARE_VF_RAW_INVOKE_RE = re.compile(r'(?:\bpy|\bpython3?)(?:\s+-3)?\s+(?<!TOOLS[\\/])vf_raw\.py')
ENCODING_HINTS = ('PYTHONIOENCODING', 'chcp 65001')
# реальная усечённая строка из возврата ПК-3 (README_RETURN_PC3.md, стр. 20)
TRUNCATED_BRK = '@BRK:valid=1:seq=1:src=TIM1:…:sr=81,81:sd=1,1:bd=1CC0,1CC0\n'
TRUNCATED_BRK_NO_BD = '@BRK:valid=1:seq=2:src=TIM8:cyc=5:sr=81,81:sd=1,1\n'
# Полные строки на РЕАЛЬНЫХ состояниях платы 26.09.2026 (сняты в сессии 1):
# 0x1CC0 — покой (MOE=0), 0x9CC0 — под током (MOE=1); оба штатные, «противоречия» быть не должно.
FULL_BRK_OK = ('@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:'
               'ce=0,0:cnt=0,0:cap=0,0,0\n')
FULL_BRK_OK_HOT = ('@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=81,81:sd=1,1:bd=9CC0,9CC0:'
                   'ce=555,555:cnt=0,0:cap=0,0,0\n')
# Диагностический образ (monitor-only): BKE снят НАМЕРЕННО, поэтому 0x0CC0/0x8CC0 штатные,
# а 0x1CC0/0x9CC0 — это ДРУГОЙ образ (под профиль monitor-only не годятся).
FULL_BRK_MO_IDLE = ('@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=0,0:sd=1,1:bd=0CC0,0CC0:'
                    'ce=0,0:cnt=0,0:cap=0,0,0\n')
FULL_BRK_MO_HOT = ('@BRK:valid=1:seq=2:src=TIM8:cyc=1:sr=0,0:sd=1,1:bd=8CC0,8CC0:'
                   'ce=555,555:cnt=0,0:cap=0,0,0\n')
# настоящее нарушение: BKP (бит 13) — полярность входа break неверна
FULL_BRK_FORBIDDEN = ('@BRK:valid=1:seq=3:src=TIM1:cyc=0:sr=81,81:sd=0,1:bd=BCC0,BCC0:'
                      'ce=555,555:cnt=0,0:cap=0,0,0\n')
# самотесты инструментов, которые обязаны проходить НА КОПИИ ИЗ КОМПЛЕКТА:
# (путь в комплекте, в каких консолях оператора гонять)
TOOL_SELFTESTS = (
    ('SESSION_1_SIGN_BREAK/TOOLS/enc_sign.py', ('cp1251', 'ascii')),
    ('SESSION_1_SIGN_BREAK/TOOLS/breakdiag_parse.py', ('cp1251', 'ascii')),
    # самотест vflog сам сверяет текст отчёта с русскими подписями, поэтому только cp1251
    ('SESSION_2_VF2_VF3/TOOLS/vflog_step_report_selftest.py', ('cp1251',)),
    ('SESSION_3_SIGN_AB/TOOLS/sign_ab_report.py', ('cp1251', 'ascii')),
    ('SESSION_4_FAULTN_CHARACTERIZATION/TOOLS/faultn_report.py', ('cp1251', 'ascii')),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def kit_relpaths(root: Path) -> list:
    """Файлы комплекта (без карантина и без манифеста), POSIX-пути, сортировка."""
    out = []
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST or rel.startswith(QUARANTINE_DIR + '/'):
            continue
        out.append(rel)
    return sorted(out)


def write_manifest(root: Path, relpaths: list, manifest: str = MANIFEST) -> int:
    lines = ['%s *%s\n' % (sha256_file(root / rel), rel) for rel in relpaths]
    with open(root / manifest, 'w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(lines)
    return len(lines)


def read_manifest(root: Path, manifest: str = MANIFEST) -> dict:
    path = root / manifest
    if not path.is_file():
        raise FileNotFoundError('нет файла манифеста: %s' % path)
    entries = {}
    for ln in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not ln.strip():
            continue
        digest, _, name = ln.partition(' ')
        entries[name.lstrip('*').strip()] = digest
    return entries


def verify_tree(root: Path, manifest: str = MANIFEST, skip_prefix: tuple = ()) -> list:
    """Сверяет дерево с манифестом: недостающие / изменённые / лишние файлы."""
    entries = read_manifest(root, manifest)
    problems = []
    for rel in sorted(entries):
        path = root / rel
        if not path.is_file():
            problems.append('НЕТ файла: %s' % rel)
            continue
        got = sha256_file(path)
        if got != entries[rel]:
            problems.append('РАСХОЖДЕНИЕ %s: %s != %s' % (rel, got[:16], entries[rel][:16]))
    known = set(entries) | {manifest}
    present = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    for rel in sorted(present - known):
        if any(rel.startswith(pref) for pref in skip_prefix):
            continue
        problems.append('ЛИШНИЙ файл (нет в манифесте): %s' % rel)
    return problems


def build_kit(out: Path, image_dirs=None, verbose: bool = True) -> list:
    """Собирает комплект в `out`. Карантин (`_SUPERSEDED/`) не трогается.

    image_dirs: {профиль: папка с firmware.bin/firmware.elf}. Для --out нужны ОБА профиля:
    образы проверяются по SHA256 ДО того, как что-либо будет стёрто в out.
    """
    if image_dirs is not None:
        missing = [p for p in IMAGE_PROFILES if p not in image_dirs]
        if missing:
            raise ValueError('не заданы папки образов для профилей: %s' % ', '.join(sorted(missing)))
        for profile, prof in sorted(IMAGE_PROFILES.items()):
            for name, (_kit_path, want) in sorted(prof['files'].items()):
                src = Path(image_dirs[profile]) / name
                if not src.is_file():
                    raise FileNotFoundError('нет файла образа (профиль %s): %s' % (profile, src))
                got = sha256_file(src)
                if got != want:
                    raise ValueError('образ %s (профиль %s) НЕ совпал с зафиксированным SHA256\n'
                                     '  получено:      %s\n  зафиксировано: %s'
                                     % (src, profile, got, want))
    if out.exists():
        for entry in sorted(out.iterdir()):
            if entry.name == QUARANTINE_DIR:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    out.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in sorted(p for p in DOCS_DIR.rglob('*') if p.is_file()):
        dst = out / src.relative_to(DOCS_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
    for name, kit_path in sorted(KIT_TOOLS.items()):
        src = TOOLS_DIR / name
        if not src.is_file():
            raise FileNotFoundError('нет инструмента в репозитории: %s' % src)
        dst = out / kit_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
    if image_dirs is not None:
        for profile, prof in sorted(IMAGE_PROFILES.items()):
            for name, (kit_path, _sha) in sorted(prof['files'].items()):
                src = Path(image_dirs[profile]) / name
                dst = out / kit_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                copied += 1
            prov = out / ('IMAGE/PROVENANCE_%s.txt' % profile)
            prov.write_text(image_provenance(profile, Path(image_dirs[profile])),
                            encoding='utf-8', newline='\n')
            copied += 1
    relpaths = kit_relpaths(out)
    total = write_manifest(out, relpaths)
    if verbose:
        print('комплект собран: %s' % out)
        print('  файлов скопировано %d, записей в %s: %d' % (copied, MANIFEST, total))
    return relpaths


def do_quarantine(qdir: Path, sources) -> int:
    """Переносит объекты в карантин и считает их хеши (ничего не удаляется)."""
    qdir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for src in sources:
        src = Path(src)
        if not src.exists():
            print('ПРОПУСК (нет на носителе): %s' % src)
            continue
        dst = qdir / src.name
        if dst.exists():
            print('ПРОПУСК (уже в карантине): %s' % dst)
            continue
        shutil.move(str(src), str(dst))
        moved += 1
        print('в карантин: %s' % dst)
    (qdir / 'README.md').write_text(
        '# _SUPERSEDED — карантин носителя\n\n'
        'Здесь лежат объекты, снятые с верхнего уровня носителя (НЕ удалены).\n'
        'Актуальная инструкция — только `../START_HERE_PC3.md`; содержимое карантина\n'
        'к исполнению не предназначено.\n\n'
        'Состав и хеши — `SHA256SUMS` в этом каталоге. У объекта\n'
        '`pc3_bench_kit_20260926_v1/` остался его исходный `SHA256SUMS`: он по-прежнему\n'
        'действителен, содержимое v1 не изменялось.\n\n'
        'Что заменено и почему — `../CONTEXT/SUPERSEDED.txt`.\n',
        encoding='utf-8', newline='\n')
    files = sorted(p for p in qdir.rglob('*') if p.is_file() and p != qdir / MANIFEST)
    total = write_manifest(qdir, [p.relative_to(qdir).as_posix() for p in files], MANIFEST)
    print('карантин: объектов перенесено %d, файлов в манифесте %d' % (moved, total))
    return moved


def list_kit() -> list:
    """Состав комплекта по источникам (что будет собрано)."""
    rels = sorted(p.relative_to(DOCS_DIR).as_posix() for p in DOCS_DIR.rglob('*') if p.is_file())
    rels += sorted(KIT_TOOLS.values())
    rels += kit_image_paths()
    return sorted(rels)


def run_tool(script: Path, args=(), timeout: int = 120, console: str = 'cp1251'):
    """Запускает инструмент в консоли оператора (cp1251) — так выглядит реальный отказ v1."""
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = console
    env.pop('PYTHONPATH', None)
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          capture_output=True, text=True, env=env, timeout=timeout,
                          encoding=console, errors='replace')


def _first_traceback_line(text: str) -> str:
    for ln in text.splitlines():
        if 'Error' in ln or 'Traceback' in ln:
            return ln.strip()
    return text.strip().splitlines()[-1] if text.strip() else ''


def check_docs(root: Path) -> list:
    """Проверки инструкционной части: относительные пути и указание кодировки консоли."""
    problems = []
    for rel in INSTRUCTION_DOCS:
        path = root / rel
        if not path.is_file():
            problems.append('нет инструкционного документа: %s' % rel)
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for m in DRIVE_RE.finditer(text):
            line = text[:m.start()].count('\n') + 1
            problems.append('%s:%d абсолютный путь носителя в инструкции: %r'
                            % (rel, line, text[max(0, m.start() - 15):m.end() + 15].replace('\n', ' ')))
        for m in BARE_VF_RAW_INVOKE_RE.finditer(text):
            line = text[:m.start()].count('\n') + 1
            problems.append('%s:%d `vf_raw.py` запускается без пути TOOLS/ (инструмент лежит в TOOLS/)'
                            % (rel, line))
    for rel in ('START_HERE_PC3.md', 'SESSION_1_SIGN_BREAK/TOOLS/HOWTO.md'):
        path = root / rel
        text = path.read_text(encoding='utf-8', errors='replace') if path.is_file() else ''
        if not any(hint in text for hint in ENCODING_HINTS):
            problems.append('%s: нет указания про консольную кодировку (%s)'
                            % (rel, ' или '.join(ENCODING_HINTS)))
    return problems


def check_tools(root: Path, workdir: Path) -> list:
    """Прогон инструментов КОПИИ ИЗ КОМПЛЕКТА в консолях оператора (cp1251 и ascii)."""
    problems = []
    for path in sorted(p for p in root.rglob('*.py') if QUARANTINE_DIR not in p.parts):
        try:
            compile(path.read_text(encoding='utf-8', errors='replace'), str(path), 'exec')
        except SyntaxError as exc:
            problems.append('%s не компилируется: %s' % (path.relative_to(root).as_posix(), exc))
    for rel, consoles in TOOL_SELFTESTS:
        path = root / rel
        if not path.is_file():
            problems.append('нет инструмента: %s' % rel)
            continue
        for console in consoles:
            res = run_tool(path, ['--selftest'], console=console)
            out = res.stdout + res.stderr
            if res.returncode != 0:
                problems.append('самотест провален (%s, консоль %s): rc=%d | %s'
                                % (rel, console, res.returncode, _first_traceback_line(out)))
            elif 'Traceback' in out:
                problems.append('самотест с падением (%s, консоль %s): %s'
                                % (rel, console, _first_traceback_line(out)))
    brk = root / 'SESSION_1_SIGN_BREAK/TOOLS/breakdiag_parse.py'
    MO = ('--profile', 'monitor-only')
    if brk.is_file():
        cases = (('штатный покой, bd=1CC0 (снят с платы 26.09)', [], FULL_BRK_OK, 0),
                 ('под током, bd=9CC0 (снят с платы 26.09)', [], FULL_BRK_OK_HOT, 0),
                 ('запрещённый BKP=1 (бит 13)', [], FULL_BRK_FORBIDDEN, 1),
                 ('усечённая строка с bd=1CC0', [], TRUNCATED_BRK, 2),
                 ('усечённая строка без bd', [], TRUNCATED_BRK_NO_BD, 2),
                 ('диагностический образ: покой 0x0CC0', MO, FULL_BRK_MO_IDLE, 0),
                 ('диагностический образ: под током 0x8CC0', MO, FULL_BRK_MO_HOT, 0),
                 ('диагностический образ + штатный bd=1CC0 (BKE=1) = ДРУГОЙ образ', MO, FULL_BRK_OK, 1),
                 ('штатный образ + профиль monitor-only (bd=9CC0 c BKE=1)', MO, FULL_BRK_OK_HOT, 1))
        for name, extra, text, want_rc in cases:
            sample = workdir / 'brk_sample.txt'
            sample.write_text(text, encoding='utf-8', newline='\n')
            res = run_tool(brk, list(extra) + [sample])
            out = res.stdout + res.stderr
            if res.returncode != want_rc:
                problems.append('breakdiag_parse на «%s»: rc=%d, ожидался %d | %s'
                                % (name, res.returncode, want_rc, _first_traceback_line(out)))
            if 'Traceback' in out:
                problems.append('breakdiag_parse упал на «%s»: %s'
                                % (name, _first_traceback_line(out)))
            if want_rc == 2 and 'УСЕЧЕНА' not in res.stdout:
                problems.append('breakdiag_parse не сообщил об усечении строки «%s»' % name)
            if want_rc == 1 and 'ПРОТИВОРЕЧИЕ' not in res.stdout:
                problems.append('breakdiag_parse не назвал противоречие на «%s»' % name)
    raw = root / 'TOOLS/vf_raw.py'
    if not raw.is_file():
        problems.append('нет TOOLS/vf_raw.py — шаги сессии 1 (захват ответов платы) не выполнить')
    return problems


def selfcheck(root: Path) -> list:
    """Полная проверка комплекта: манифест + документы + прогон инструментов (+ карантин)."""
    if not root.is_dir():
        return ['нет каталога: %s' % root]
    problems = ['манифест: %s' % p
                for p in verify_tree(root, MANIFEST, (QUARANTINE_DIR + '/',))]
    problems += check_docs(root)
    with tempfile.TemporaryDirectory(prefix='pc3_kit_check_') as tmp:
        problems += check_tools(root, Path(tmp))
    qdir = root / QUARANTINE_DIR
    if (qdir / MANIFEST).is_file():
        problems += ['карантин: %s' % p for p in verify_tree(qdir, MANIFEST)]
    return problems


def _report(problems: list, what: str) -> int:
    for problem in problems:
        print('ПРОВАЛ: %s' % problem)
    print('%s: %s' % (what, 'OK' if not problems else 'ПРОВАЛ (%d)' % len(problems)))
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Сборка/проверка/карантин стендового комплекта ПК-3 (pc3_bench_kit).',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--out', type=Path, metavar='DIR', help='собрать комплект в DIR')
    group.add_argument('--verify', type=Path, metavar='DIR', help='сверить DIR с его SHA256SUMS')
    group.add_argument('--list', action='store_true', help='напечатать состав комплекта')
    group.add_argument('--quarantine-into', type=Path, metavar='DIR', dest='quarantine_into',
                       help='перенести объекты аргументами в карантин DIR')
    group.add_argument('--selftest', action='store_true',
                       help='собрать без образа во временную папку и проверить всё, что можно')
    ap.add_argument('--image', action='append', default=[], metavar='PROFILE=DIR',
                    help='папка с firmware.bin/firmware.elf для профиля (production|monitor-only); '
                         'для --out нужны оба профиля')
    ap.add_argument('--image-dir', type=Path, metavar='DIR',
                    help='синоним --image production=DIR')
    ap.add_argument('sources', nargs='*', help='объекты для --quarantine-into')
    args = ap.parse_args(argv)

    if args.list:
        for rel in list_kit():
            print(rel)
        return 0

    if args.quarantine_into:
        if not args.sources:
            print('для --quarantine-into нужен хотя бы один объект', file=sys.stderr)
            return 2
        do_quarantine(args.quarantine_into, args.sources)
        return 0

    if args.out:
        image_dirs = {}
        for spec in args.image:
            if '=' not in spec:
                print('--image требует вид PROFILE=DIR, получено: %s' % spec, file=sys.stderr)
                return 2
            prof, _, path = spec.partition('=')
            if prof not in IMAGE_PROFILES:
                print('неизвестный профиль образа: %s (есть %s)'
                      % (prof, ', '.join(sorted(IMAGE_PROFILES))), file=sys.stderr)
                return 2
            image_dirs[prof] = Path(path)
        if args.image_dir:
            image_dirs.setdefault('production', args.image_dir)
        if not image_dirs:
            print('для --out обязателен --image PROFILE=DIR (образы сверяются по SHA256)',
                  file=sys.stderr)
            return 2
        try:
            build_kit(args.out, image_dirs)
        except (FileNotFoundError, ValueError) as exc:
            print('ОШИБКА сборки: %s' % exc, file=sys.stderr)
            return 2
        return _report(selfcheck(args.out), 'проверка собранного комплекта')

    if args.verify:
        return _report(selfcheck(args.verify), 'проверка комплекта %s' % args.verify)

    with tempfile.TemporaryDirectory(prefix='pc3_kit_selftest_') as tmp:
        out = Path(tmp) / 'pc3_bench_kit'
        build_kit(out, None)
        return _report(selfcheck(out), 'самотест сборки/проверки комплекта')


if __name__ == '__main__':
    sys.exit(main())
