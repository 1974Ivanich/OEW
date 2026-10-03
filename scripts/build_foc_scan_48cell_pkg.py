#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка и проверка ZIP-пакета FOC 48-ячеечного скана (ред. 2).

Пакет не собирается руками: состав берётся из `docs/pc3_package/foc_scan_48cell_v2/`,
SHA256SUMS считает этот скрипт, а перед упаковкой обязателен зелёный
`TOOLS/check_package.py` (fail-closed: пакет с расхождением в ZIP не попадает).

Режимы:
  --manifest              пересобрать SHA256SUMS в каталоге пакета
  --build (по умолчанию)  manifest -> check_package -> ZIP в --out + сайдкар
                          `<zip>.sha256` (sha256sum-совместимый, маркер `*имя`)
  --verify ZIP            сверить ZIP: состав, SHA256SUMS внутри, сайдкар рядом
  --list                  напечатать состав пакета

ZIP детерминирован: записи отсортированы, время фиксировано (1980-01-01), deflate,
forward slashes -> один и тот же вход даёт один и тот же SHA-256 (проверено тестом).

Коды возврата: 0 — OK, 1 — расхождение/провал проверки, 2 — ошибка вызова.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'docs' / 'pc3_package' / 'foc_scan_48cell_v2'
MANIFEST = 'SHA256SUMS'
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
TEXT_EXT = ('.md', '.py')


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    with open(path, 'rb') as fh:
        return sha_bytes(fh.read())


def package_files():
    """Относительные пути файлов пакета (forward slashes).

    Исключаются `SHA256SUMS` (он сам — манифест), `.gitattributes` и любые артефакты
    прогонов (`__pycache__/`, `*.pyc`), чтобы упаковка не зависела от локальных прогонов.
    """
    out = []
    for path in sorted(PKG.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(PKG).as_posix()
        if rel in (MANIFEST, '.gitattributes'):
            continue
        if '__pycache__' in rel.split('/') or rel.endswith(('.pyc', '.pyo')):
            continue
        out.append(rel)
    return out


def normalize_eol():
    """CRLF -> LF для текстовых файлов пакета (байтовая стабильность хешей).

    Нужно там, где рабочее дерево лежит на машине с `core.autocrlf=true`:
    `check_package.py` такие файлы отвергает (CRLF FOUND), а этот режим их лечит.
    """
    fixed = []
    for rel in package_files():
        if not rel.endswith(TEXT_EXT):
            continue
        path = PKG / rel
        data = path.read_bytes()
        if b'\r\n' in data:
            path.write_bytes(data.replace(b'\r\n', b'\n'))
            fixed.append(rel)
    return fixed


def write_manifest():
    rows = ['%s  %s' % (sha_file(PKG / rel), rel) for rel in package_files()]
    data = ('\n'.join(rows) + '\n').encode('utf-8')
    if b'\r\n' in data:
        raise RuntimeError('manifest must use LF line ends')
    (PKG / MANIFEST).write_bytes(data)
    return rows


def run_check():
    proc = subprocess.run([sys.executable, str(PKG / 'TOOLS' / 'check_package.py')],
                          capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def read_manifest(text):
    """Разбор SHA256SUMS: список (sha, rel)."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError('malformed manifest line: %r' % line)
        rows.append((parts[0], parts[1].strip().lstrip('*')))
    return rows


def build_zip(out_dir, name):
    names = package_files() + [MANIFEST]
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(names):
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (PKG / rel).read_bytes())
    zip_path.write_bytes(buf.getvalue())
    digest = sha_bytes(buf.getvalue())
    sidecar = out_dir / (name + '.sha256')
    sidecar.write_bytes(('%s *%s\n' % (digest, name)).encode('ascii'))
    print('zip        : %s (%d bytes)' % (zip_path, zip_path.stat().st_size))
    print('zip sha256 : %s' % digest)
    print('sidecar    : %s (send it together with the zip)' % sidecar)
    return zip_path, digest


def verify_zip(zip_path):
    """Сверка ZIP: состав + хеши внутри SHA256SUMS + сайдкар рядом."""
    bad = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if MANIFEST not in names:
            print('zip does not contain %s' % MANIFEST)
            return 1
        rows = read_manifest(zf.read(MANIFEST).decode('utf-8'))
        listed = [rel for _sha, rel in rows]
        expected = package_files()
        for rel in expected:
            if rel not in names:
                print('missing in zip: %s' % rel)
                bad += 1
        for rel in listed:
            if rel not in expected:
                print('manifest lists unexpected file: %s' % rel)
                bad += 1
        for want, rel in rows:
            got = sha_bytes(zf.read(rel)) if rel in names else 'NO FILE'
            ok = got == want
            print('sha %-30s %s' % (rel, 'OK' if ok else 'MISMATCH'))
            bad += 0 if ok else 1
        stray = [n for n in names if n not in listed and n != MANIFEST]
        if stray:
            print('files in zip but not in manifest: %s' % ', '.join(stray))
            bad += 1
    sidecar = Path(str(zip_path) + '.sha256')
    digest = sha_file(zip_path)
    if sidecar.exists():
        want = sidecar.read_text(encoding='ascii').split()[0]
        ok = want == digest
        print('sidecar    : %s' % ('OK' if ok else 'MISMATCH (file=%s, sha=%s)' % (want, digest)))
        bad += 0 if ok else 1
    else:
        print('sidecar    : MISSING (%s)' % sidecar)
        bad += 1
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build/verify the FOC 48-cell scan package.')
    ap.add_argument('--manifest', action='store_true', help='only refresh SHA256SUMS')
    ap.add_argument('--normalize', action='store_true',
                    help='CRLF -> LF for package text files (byte-stable hashes)')
    ap.add_argument('--build', action='store_true', help='manifest + check + zip (default)')
    ap.add_argument('--verify', metavar='ZIP', help='verify an existing zip')
    ap.add_argument('--list', action='store_true', help='print package contents')
    ap.add_argument('--out', default=str(ROOT / 'build'), help='output dir for the zip')
    ap.add_argument('--name', default=None, help='zip file name (default: dated)')
    a = ap.parse_args(argv)

    if a.normalize:
        fixed = normalize_eol()
        print('normalize  : %d file(s) CRLF -> LF%s'
              % (len(fixed), (': ' + ', '.join(fixed)) if fixed else ' (already LF)'))

    if a.list:
        for rel in package_files():
            print('%8d  %s' % ((PKG / rel).stat().st_size, rel))
        return 0
    if a.verify:
        bad = verify_zip(Path(a.verify))
        print('VERIFY: %s' % ('PASS' if bad == 0 else 'FAIL (%d)' % bad))
        return 0 if bad == 0 else 1
    if a.manifest and not a.build:
        rows = write_manifest()
        print('manifest   : %s (%d files)' % (PKG / MANIFEST, len(rows)))
        return 0

    rows = write_manifest()
    print('manifest   : %s (%d files)' % (PKG / MANIFEST, len(rows)))
    rc = run_check()
    if rc != 0:
        print('check_package FAILED (rc=%d) -> zip not built (fail-closed)' % rc)
        return 1
    name = a.name or ('FOC_SCAN_48CELL_PC3_V2_%s.zip'
                      % datetime.date.today().strftime('%Y%m%d'))
    build_zip(Path(a.out), name)
    print('BUILD: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
