#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка класса прошитого образа: идентичность дампа Flash образу из пакета
и статические маркеры скомпилированной ветки профиля карты.

Зачем. На стенде может исполняться образ, не совпадающий с заявленным в ТЗ
пакетом (дефект docs/ACCEPTANCE_LESSONS.md §25.12: «commissioning»-артефакт CI
собирается с -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1, из-за чего ветка
BOARD-профиля (BOAR, 0x424F4152 + офсеты) компилируется ВОН). Класс образа
проверяется без SWD: (1) дамп Flash против firmware.bin из артефакта пакета,
(2) какие константы измерения вкомпилированы.

Что делает.
  * `--dump` + `--artifact-zip`: достаёт firmware.bin из ZIP, доказывает, что
    дамп начинается этим образом ровно (и хвост дампа — стирание 0xFF).
  * `--image` (или образ из ZIP): считает 32-битные LE-маркеры:
      - SYNTHETIC-ветка: acs=0x13572468, adc_clk=170000000;
      - BOARD-ветка:    acs=0x26B9B97B, ccs=0x13552B12 (rev-7 reference).
    Вердикт: SYNTHETIC (BOAR вырезан) / BOARD (BOAR доступен) / INDETERMINATE.
  * Вердикт по маркерам — статическая догадка, её ОБЯЗАТЕЛЬНО подтверждать
    поведением платы (`mcarm <BOAR id>` -> rc=0, `mcarm <SYNT id>` ->
    BLOCKED:PROFILE для BOARD-образа). База профиля (0x424F4152) в бинаре даёт
    0 совпадений даже у BOARD-сборки (константа материализуется побайтно) —
    поэтому она маркером НЕ является.

Запуск:
  py -3 tools/check_firmware_image.py --dump backup/flash.bin \
        --artifact-zip docs/pc3_package/pc3_al_pkg_v2/firmware/ci733/artifact_ci733.zip
  py -3 tools/check_firmware_image.py --image build/firmware.bin
Коды выхода: 0 — PASS, 1 — FAIL (расхождение/BOAR вырезан), 2 — ошибка прогона.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ERASE_BYTE = 0xFF

# acs/ccs — константы измерения (map_capture_profiles / map_measurement_reference).
SYNTHETIC_MARKERS = {
    "SYNT acs 0x13572468": (0x13572468).to_bytes(4, "little"),
    "SYNT adc_clk 170000000": (170000000).to_bytes(4, "little"),
}
BOARD_MARKERS = {
    "BRD acs 0x26B9B97B": (0x26B9B97B).to_bytes(4, "little"),
    "BRD ccs 0x13552B12": (0x13552B12).to_bytes(4, "little"),
}
EXTRA_MARKERS = {
    "TRIG 0x4F455731": (0x4F455731).to_bytes(4, "little"),
    "SYNT ccs 0x24681357": (0x24681357).to_bytes(4, "little"),
    "BOAR base 0x424F4152 (не маркер)": (0x424F4152).to_bytes(4, "little"),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_image_from_zip(zip_path: Path, entry_name: str = "firmware.bin") -> bytes:
    with zipfile.ZipFile(zip_path) as zf:
        matches = [n for n in zf.namelist() if Path(n).name == entry_name]
        if not matches:
            raise FileNotFoundError(f"{entry_name} не найден в {zip_path.name}")
        if len(matches) > 1:
            raise ValueError(f"{entry_name}: неоднозначно в ZIP ({len(matches)} совпадений)")
        return zf.read(matches[0])


def compare_dump_to_image(dump: bytes, image: bytes) -> dict:
    head = dump[: len(image)]
    tail = dump[len(image):]
    return {
        "identical": head == image,
        "dump_len": len(dump),
        "image_len": len(image),
        "dump_prefix_sha256": sha256_bytes(head),
        "image_sha256": sha256_bytes(image),
        "tail_erased": all(b == ERASE_BYTE for b in tail),
        "tail_len": len(tail),
    }


def marker_report(image: bytes) -> dict:
    synth = {name: image.count(pat) for name, pat in SYNTHETIC_MARKERS.items()}
    board = {name: image.count(pat) for name, pat in BOARD_MARKERS.items()}
    extra = {name: image.count(pat) for name, pat in EXTRA_MARKERS.items()}
    synth_ok = all(v > 0 for v in synth.values())
    board_ok = all(v > 0 for v in board.values())
    if synth_ok and not board_ok:
        verdict = "SYNTHETIC"
        note = "SYNTHETIC-ветка вкомпилирована => BOARD/BOAR-профиль вырезан (дефект §25.12)"
    elif board_ok and not synth_ok:
        verdict = "BOARD"
        note = "BOARD-ветка вкомпилирована => BOAR-профиль доступен (подтвердить `mcarm <BOAR id>` -> rc=0)"
    else:
        verdict = "INDETERMINATE"
        note = "маркеры неоднозначны — класс образа статикой не доказан, смотреть поведение платы"
    return {"synthetic": synth, "board": board, "extra": extra, "verdict": verdict, "note": note}


def _print_markers(image: bytes, label: str) -> dict:
    rep = marker_report(image)
    print(f"маркеры {label} ({len(image)} Б, sha256 {sha256_bytes(image)[:16]}…):")
    for group in ("synthetic", "board", "extra"):
        for name, hits in rep[group].items():
            print(f"   {name}: {hits} совпадений")
    print(f"   ВЕРДИКТ: {rep['verdict']} — {rep['note']}")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Проверка образа: идентичность дампу + ветка профиля карты")
    ap.add_argument("--dump", help="дамп Flash с платы (любой размер, хвост должен быть 0xFF)")
    ap.add_argument("--artifact-zip", help="ZIP артефакта CI пакета (берётся firmware.bin)")
    ap.add_argument("--image", help="готовый firmware.bin (без дампа/ZIP)")
    ap.add_argument("--entry", default="firmware.bin", help="имя файла образа в ZIP")
    args = ap.parse_args(argv)

    if not args.image and not args.artifact_zip:
        print("ERROR: нужен --image или --artifact-zip", file=sys.stderr)
        return 2

    rc = 0
    try:
        if args.artifact_zip:
            image = read_image_from_zip(Path(args.artifact_zip), args.entry)
            print(f"образ из ZIP {Path(args.artifact_zip).name}: {len(image)} Б, "
                  f"sha256 {sha256_bytes(image)}")
        else:
            image = Path(args.image).read_bytes()
            print(f"образ {args.image}: {len(image)} Б, sha256 {sha256_bytes(image)}")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.image and args.artifact_zip:
        if Path(args.image).read_bytes() != image:
            print("FAIL: --image не совпадает с образом из ZIP")
            rc = 1

    if args.dump:
        try:
            dump = Path(args.dump).read_bytes()
        except OSError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        cmp = compare_dump_to_image(dump, image)
        print(f"дамп {args.dump}: {cmp['dump_len']} Б, sha256 {sha256_bytes(dump)}")
        print(f"   образ в дампе (первые {cmp['image_len']} Б): "
              f"{'СОВПАДАЕТ' if cmp['identical'] else 'РАСХОДИТСЯ'} "
              f"(sha256 {cmp['dump_prefix_sha256']})")
        print(f"   хвост дампа {cmp['tail_len']} Б: "
              f"{'стирание 0xFF' if cmp['tail_erased'] else 'НЕ пустой'}")
        if not cmp["identical"]:
            print("FAIL: на плате другой образ — выводы о профиле к плате не применимы")
            rc = 1

    rep = _print_markers(image, "образа")
    if rep["verdict"] == "SYNTHETIC":
        print("FAIL: образ класса SYNTHETIC — BOAR-профиль вырезан, кампания mapcap невозможна")
        rc = 1
    elif rep["verdict"] == "INDETERMINATE":
        print("WARN: класс образа не доказан статикой — нужен `mcarm` на плате")
        rc = 1 if rc == 0 else rc

    return rc


if __name__ == "__main__":
    sys.exit(main())
