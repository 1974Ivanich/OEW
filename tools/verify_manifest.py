#!/usr/bin/env python3
"""Переносимый верификатор манифеста SHA256 для возвратов со стенда (TZ-02 hygiene).

Зачем: манифесты, снятые на Windows (PowerShell/`Get-FileHash`/python-текстовый режим),
часто записываются с CRLF. Тогда `sha256sum -c` в POSIX-окружении падает на каждой строке:
имя файла приходит с завершающим `\\r` → «No such file or directory». Проверка целостности
превращается в ложный отказ (или, что хуже, в «сверку глазами»).

Инструмент:
  * читает манифест терпимо к CRLF/CR (и к разделителю из одного/двух пробелов,
    и к бинарному маркеру `*` как в coreutils);
  * проверяет каждый файл и сообщает: отсутствующие, с несовпавшим sha256,
    и файлы на диске, НЕ покрытые манифестом;
  * `--normalize-lf` переписывает манифест с LF и каноническим форматом `sha256␠␠путь`
    (только если манифест сначала успешно проверен — сломанный манифест не «лечим»);
  * `--json` пишет машинный вердикт.

Коды: 0 — манифест сходится; 1 — нарушение (или отказ нормализации); 2 — манифест не найден.

Использование:
    python tools/verify_manifest.py <папка>
    python tools/verify_manifest.py <папка> --normalize-lf
    python tools/verify_manifest.py <папка> --manifest RETURN_SHA256.txt --json report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEFAULT_MANIFESTS = ("SHA256SUMS.txt", "RETURN_SHA256.txt", "SHA256SUMS", "checksums.txt")
ENTRY_RE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+[* ]?(.+)$")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: Path) -> list[tuple[str, str]]:
    """[(sha256, относительный путь)] — терпимо к CRLF/CR и разделителям."""
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    entries: list[tuple[str, str]] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENTRY_RE.match(line)
        if m:
            entries.append((m.group(1).lower(), m.group(2).strip().replace("\\", "/")))
    return entries


def verify(folder: Path, manifest: Path) -> dict:
    entries = read_manifest(manifest)
    listed = {rel: digest for digest, rel in entries}
    missing: list[str] = []
    mismatched: list[str] = []
    for rel, digest in listed.items():
        target = folder / rel
        if not target.exists():
            missing.append(rel)
        elif sha256_of(target) != digest:
            mismatched.append(rel)
    on_disk = {p.relative_to(folder).as_posix() for p in folder.rglob("*")
               if p.is_file() and p.resolve() != manifest.resolve()}
    untracked = sorted(on_disk - set(listed))
    ok = bool(listed) and not missing and not mismatched and not untracked
    return {"manifest": manifest.name, "entries": len(entries), "listed": len(listed),
            "missing": missing, "mismatched": mismatched, "untracked": untracked, "ok": ok,
            "line_endings": detect_endings(manifest)}


def detect_endings(path: Path) -> str:
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf and lf:
        return "mixed"
    if crlf:
        return "crlf"
    return "lf"


def normalize_lf(folder: Path, manifest: Path, entries: list[tuple[str, str]]) -> None:
    """Переписать манифест с LF и каноническим форматом (порядок записей сохраняется)."""
    text = "\n".join(f"{digest}  {rel}" for digest, rel in entries) + "\n"
    manifest.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Переносимый верификатор манифеста SHA256")
    ap.add_argument("folder")
    ap.add_argument("--manifest", default=None, help="имя/путь манифеста (по умолчанию — поиск по имени)")
    ap.add_argument("--normalize-lf", action="store_true",
                    help="переписать манифест с LF (только если проверка прошла)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"BLOCKED: нет каталога {folder}")
        return 2
    if args.manifest:
        manifest = Path(args.manifest)
        if not manifest.is_absolute():
            manifest = folder / manifest
    else:
        manifest = next((folder / n for n in DEFAULT_MANIFESTS if (folder / n).exists()), None)
    if manifest is None or not manifest.exists():
        print(f"BLOCKED: манифест не найден в {folder} (искал: {', '.join(DEFAULT_MANIFESTS)})")
        return 2

    report = verify(folder, manifest)
    verdict = "PASS" if report["ok"] else "FAIL"
    print(f"MANIFEST {verdict}: {report['manifest']} — записей {report['entries']}, "
          f"переводы строк: {report['line_endings']}")
    if report["missing"]:
        print(f"  отсутствуют файлы: {', '.join(report['missing'][:6])}")
    if report["mismatched"]:
        print(f"  sha256 не совпал: {', '.join(report['mismatched'][:6])}")
    if report["untracked"]:
        print(f"  не покрыто манифестом: {', '.join(report['untracked'][:6])}")

    rc = 0 if report["ok"] else 1
    if args.normalize_lf:
        if not report["ok"]:
            print("  нормализация отклонена: манифест не сходится (это дефект содержимого, а не переводов строк)")
            rc = 1
        else:
            entries = read_manifest(manifest)
            normalize_lf(folder, manifest, entries)
            after = verify(folder, manifest)
            print(f"  манифест переписан с LF: переводы строк {report['line_endings']} -> "
                  f"{after['line_endings']}; повторная проверка: {'PASS' if after['ok'] else 'FAIL'}")
            report = after
            rc = 0 if after["ok"] else 1

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")
        print(f"json: {args.json}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
