#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка КАМПАНИЙНОГО (BOARD-профиль) образа и самопроверка класса образа.

Зачем. Штатный «commissioning»-артефакт CI собирается с
-DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1 (docs/ACCEPTANCE_LESSONS.md
§25.12), из-за чего ветка BOARD-профиля (BOAR) компилируется ВОН и кампания
mapcap на таком образе невозможна (`mcarm <BOAR id>` -> @MC:ARM:BLOCKED:PROFILE).
Этот скрипт собирает образ с дефайнами кампании БЕЗ SYNT/HOST_TEST и падает,
если класс собранного образа не BOARD.

Что делает:
  1. fail-closed: отказ, если в дефайнах есть OEW_MAP_SYNTHETIC_PROFILE /
     OEW_HOST_TEST (можно только явным --allow-synthetic);
  2. жёсткая очистка build/ (shutil.rmtree — не зависит от `rm` из PATH),
     затем `make` с EXTRA_CFLAGS в окружении (работает в cmd.exe и в bash);
  3. sha256 + статический вердикт класса через tools/check_firmware_image.py;
  4. provenance-JSON рядом с образом (commit, дефайны, sha256, размер).

Типовой запуск (ПК-3):
  py -3 scripts/build_campaign_image.py --log backup/campaign_build.log
Коды выхода: 0 — PASS (класс BOARD), 1 — FAIL (не BOARD), 2 — ошибка прогона.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMPAIGN_DEFINES = [
    "OEW_MAP_CAPTURE=1",
    "OEW_MAP_L3=1",
    "PWM_OEW_BOARD_REVISION=7",
    "OEW_HS1_COMMISSIONING_RELEASE=1",
]
FORBIDDEN_IN_CAMPAIGN = ("OEW_MAP_SYNTHETIC_PROFILE", "OEW_HOST_TEST")


class Tee:
    """Пишет в stdout и в файл одновременно."""

    def __init__(self, path: Path | None):
        self.fh = open(path, "w", encoding="utf-8") if path else None

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        if self.fh:
            self.fh.write(text)
            self.fh.flush()

    def close(self) -> None:
        if self.fh:
            self.fh.close()


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def run_make(args: list[str], defines: list[str], tee: Tee) -> int:
    env = os.environ.copy()
    env["EXTRA_CFLAGS"] = " ".join("-D" + d for d in defines)
    tee.write(f"\n$ EXTRA_CFLAGS={env['EXTRA_CFLAGS']}\n$ make {' '.join(args)}\n")
    proc = subprocess.run(["make"] + args, cwd=ROOT, env=env, capture_output=True, text=True)
    tee.write(proc.stdout)
    tee.write(proc.stderr)
    return proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сборка кампанийного (BOARD) образа + самопроверка")
    ap.add_argument("--define", action="append", default=None,
                    help="дополнительный дефайн (можно повторять); по умолчанию набор кампании")
    ap.add_argument("--allow-synthetic", action="store_true",
                    help="разрешить SYNT/HOST_TEST (только для экспериментов, не для кампании)")
    ap.add_argument("--keep", action="store_true", help="не чистить build/ (инкрементальная сборка)")
    ap.add_argument("--build-dir", default="build", help="каталог сборки (по умолчанию build)")
    ap.add_argument("--image", default=None, help="готовый образ (пропустить сборку, только проверить)")
    ap.add_argument("--log", default=None, help="файл лога (tee)")
    args = ap.parse_args(argv)

    tee = Tee(Path(args.log) if args.log else None)
    defines = list(args.define) if args.define else list(CAMPAIGN_DEFINES)
    if not args.allow_synthetic:
        bad = [d for d in defines if any(f in d for f in FORBIDDEN_IN_CAMPAIGN)]
        if bad:
            tee.write(f"ERROR: кампанийный образ не собирается с {bad} (дефект §25.12)\n")
            tee.close()
            return 2

    head = git_head()
    tee.write(f"ROOT={ROOT}\nHEAD={head}\nDEFINES={defines}\n")

    image = Path(args.image) if args.image else (ROOT / args.build_dir / "firmware.bin")
    if not args.image:
        build_dir = ROOT / args.build_dir
        if not args.keep and build_dir.exists():
            import shutil
            shutil.rmtree(build_dir)
            tee.write(f"cleaned {build_dir}\n")
        rc = run_make([], defines, tee)
        if rc != 0:
            tee.write(f"FAIL: make rc={rc}\n")
            tee.close()
            return 2

    if not image.is_file():
        tee.write(f"ERROR: образ не найден: {image}\n")
        tee.close()
        return 2

    data = image.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    sys.path.insert(0, str(ROOT / "tools"))
    from check_firmware_image import marker_report  # noqa: E402

    rep = marker_report(data)
    tee.write(f"image={image} size={len(data)} sha256={sha}\n")
    for group in ("synthetic", "board", "extra"):
        for name, hits in rep[group].items():
            tee.write(f"  {name}: {hits}\n")
    tee.write(f"VERDICT={rep['verdict']} — {rep['note']}\n")

    prov = {
        "source_commit": head,
        "defines": defines,
        "image": str(image.relative_to(ROOT)) if image.is_relative_to(ROOT) else str(image),
        "size_bytes": len(data),
        "sha256": sha,
        "profile_class": rep["verdict"],
        "profile_class_note": rep["note"],
    }
    prov_path = image.with_suffix(".provenance.json")
    prov_path.write_text(json.dumps(prov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tee.write(f"provenance={prov_path}\n")

    ok = rep["verdict"] == "BOARD"
    tee.write("RESULT: PASS\n" if ok else "RESULT: FAIL (класс образа не BOARD)\n")
    tee.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
