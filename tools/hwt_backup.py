#!/usr/bin/env python3
"""Резервная копия Flash перед экспериментом: дамп + Intel HEX + SHA-256.

Зачем: перед рискованным опытом на стенде нужно сохранить то, что реально
лежит в микроконтроллере (версию, калибровки, байты опций-зависимого
состояния), а не то, что лежит на диске. Дамп читается через SWD, рядом
кладётся читаемый HEX и контрольные суммы.

Команды:

    python tools/hwt_backup.py                      # снять дамп с платы
    python tools/hwt_backup.py --from-file x.bin    # оффлайн: hex + суммы
    python tools/hwt_backup.py --verify x.bin       # сверить с SHA256SUMS.txt

Коды выхода: 0 — успех, 1 — расхождение при --verify, 2 — отказ
инфраструктуры (нет отладчика/файла/инструмента). Только чтение: дамп ничего
не стирает и не пишет.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

WIN_PROGRAMMER_DEFAULT = (r"C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin"
                          r"\STM32_Programmer_CLI.exe")
SUMS_NAME = "SHA256SUMS.txt"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_programmer(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) \
            else None
    if Path(WIN_PROGRAMMER_DEFAULT).exists():
        return WIN_PROGRAMMER_DEFAULT
    return shutil.which("STM32_Programmer_CLI")


def find_objcopy() -> Optional[str]:
    for name in ("arm-none-eabi-objcopy", "objcopy"):
        found = shutil.which(name)
        if found:
            return found
    fallback = (r"C:\ST\STM32CubeCLT_1.22.0\GNU-tools-for-STM32\bin"
                r"\arm-none-eabi-objcopy.exe")
    return fallback if Path(fallback).exists() else None


def dump_flash(programmer: str, base: int, size: int, out_bin: Path
               ) -> Tuple[bool, str]:
    cmd = [programmer, "-c", "port=SWD", "mode=UR",
           "-u", hex(base), hex(size), str(out_bin)]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         errors="replace", timeout=300)
    ok = res.returncode == 0 and out_bin.exists() and out_bin.stat().st_size > 0
    log = (res.stdout or "") + (res.stderr or "")
    tail = "\n".join(log.strip().splitlines()[-6:])
    return ok, tail


def to_ihex(objcopy: str, bin_path: Path, hex_path: Path, base: int
            ) -> Tuple[bool, str]:
    cmd = [objcopy, "-I", "binary", "-O", "ihex",
           "--change-addresses", hex(base), str(bin_path), str(hex_path)]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         errors="replace", timeout=120)
    return res.returncode == 0 and hex_path.exists(), \
        (res.stderr or "").strip()


def write_sums(path: Path, files: Sequence[Path], note: str = "") -> None:
    lines = ["# SHA-256 дампов Flash (HWT backup)",
             f"# создано: {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    if note:
        lines.append(f"# {note}")
    for f in files:
        lines.append(f"{sha256_file(f)}  {f.name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_sums(path: Path) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            out.append((parts[0], parts[1].lstrip("*").strip()))
    return out


def verify(path: Path, sums: Path) -> int:
    if not path.exists():
        print(f"[backup] файла нет: {path}")
        return 2
    if not sums.exists():
        print(f"[backup] нет {sums} — не с чем сверять")
        return 2
    entries = {name: digest for digest, name in read_sums(sums)}
    name = path.name
    if name not in entries:
        print(f"[backup] {name} нет в {sums.name}")
        return 2
    actual = sha256_file(path)
    if actual == entries[name]:
        print(f"[backup] OK: {name}\n  sha256 {actual}")
        return 0
    print(f"[backup] РАСХОЖДЕНИЕ: {name}\n  ожидалось {entries[name]}\n"
          f"  получено  {actual}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="hwt_backup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="backup", help="каталог дампов")
    p.add_argument("--base", default="0x08000000", help="начало Flash")
    p.add_argument("--size", default="0x80000", help="размер дампа (512K)")
    p.add_argument("--stamp", default=None, help="метка (по умолчанию — время)")
    p.add_argument("--from-file", default=None,
                   help="оффлайн: взять готовый .bin вместо дампа с платы")
    p.add_argument("--verify", default=None, help="сверить файл с SHA256SUMS.txt")
    p.add_argument("--programmer", default=None)
    p.add_argument("--no-hex", action="store_true", help="не делать Intel HEX")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    if args.verify:
        return verify(Path(args.verify), out_dir / SUMS_NAME)

    base, size = int(args.base, 0), int(args.size, 0)
    stamp = args.stamp or time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / f"flash_{stamp}.bin"
    note = f"base={hex(base)} size={hex(size)}"

    if args.from_file:
        src = Path(args.from_file)
        if not src.exists():
            print(f"[backup] файла нет: {src}")
            return 2
        shutil.copyfile(src, bin_path)
        note += f" source={src} (оффлайн, без платы)"
        print(f"[backup] взят готовый дамп {src} → {bin_path}")
    else:
        programmer = find_programmer(args.programmer)
        if not programmer:
            print("[backup] STM32_Programmer_CLI не найден, а дамп без него "
                  "невозможен (см. --from-file)")
            return 2
        print(f"[backup] читаю {hex(size)} Б с {hex(base)} через SWD...")
        ok, log = dump_flash(programmer, base, size, bin_path)
        if not ok:
            print("[backup] дамп не снят (отладчик подключён? питание?):")
            print(log)
            return 2
        note += " прочитано с цели по SWD"

    made: List[Path] = [bin_path]
    if not args.no_hex:
        objcopy = find_objcopy()
        if objcopy:
            hex_path = bin_path.with_suffix(".hex")
            ok, err = to_ihex(objcopy, bin_path, hex_path, base)
            if ok:
                made.append(hex_path)
            else:
                print(f"[backup] Intel HEX не сделан: {err} "
                      "(дамп и суммы сохранены)")
        else:
            print("[backup] arm-none-eabi-objcopy не найден — HEX пропущен")

    write_sums(out_dir / SUMS_NAME, made, note)
    print(f"[backup] готово: {out_dir} / {SUMS_NAME}")
    for f in made:
        print(f"  {f.name}  {f.stat().st_size} Б  "
              f"sha256 {sha256_file(f)}")
    print("[backup] сверить на ПК без sha256sum: "
          f"python tools/hwt_backup.py --verify {bin_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
