"""Тесты ELF-разбора HWT: символы, контракты, CRC загружаемых секций."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from tools.hwt_elf import (FLASH_BASE_DEFAULT, ElfError, ElfImage, SHF_ALLOC,
                           SHF_WRITE, SHT_PROGBITS, SHT_STRTAB, SHT_SYMTAB)

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "build" / "firmware.elf"


def _build_elf32(sections, symbols) -> bytes:
    """Минимальный ELF32 LE: достаточно для проверки парсера и CRC.

    sections: [(name, sh_type, flags, addr, data)]
    symbols:  [(name, value)]
    """
    names = [""] + [s[0] for s in sections] + [".shstrtab", ".symtab",
                                               ".strtab"]
    shstr = bytearray()
    name_off = {}
    for n in names:
        name_off.setdefault(n, len(shstr))
        shstr += n.encode() + b"\x00"
    strtab = bytearray(b"\x00")
    sym_entries = bytearray(b"\x00" * 16)
    for sname, svalue in symbols:
        off = len(strtab)
        strtab += sname.encode() + b"\x00"
        sym_entries += struct.pack("<IIIBBH", off, svalue, 0, 0x10, 0, 1)

    blobs = [s[4] for s in sections]
    header_size = 52
    # раскладка: [elfhdr][данные секций][shstrtab][symtab][strtab][shdrs]
    offsets = []
    cur = header_size
    body = bytearray()
    for data in blobs:
        offsets.append(cur)
        body += data
        cur += len(data)
    shstr_off = cur
    body += shstr
    cur += len(shstr)
    symtab_off = cur
    body += sym_entries
    cur += len(sym_entries)
    strtab_off = cur
    body += strtab
    cur += len(strtab)
    shoff = cur

    shdrs = bytearray(b"\x00" * 40)
    all_secs = list(sections) + [(".shstrtab", SHT_STRTAB, 0, 0, bytes(shstr)),
                                 (".symtab", SHT_SYMTAB, 0, 0,
                                  bytes(sym_entries)),
                                 (".strtab", SHT_STRTAB, 0, 0,
                                  bytes(strtab))]
    offs = offsets + [shstr_off, symtab_off, strtab_off]
    for i, (sname, stype, flags, addr, data) in enumerate(all_secs):
        link = 0
        if stype == SHT_SYMTAB:
            link = len(all_secs)  # индекс .strtab (последняя в all_secs)
        shdrs += struct.pack("<IIIIIIIIII", name_off[sname], stype, flags,
                             addr, offs[i], len(data), link, 0, 4, 0)

    ehdr = struct.pack("<16sHHIIIIIHHHHHH",
                       b"\x7fELF\x01\x01\x01" + b"\x00" * 9,
                       2, 40, 1, 0, 0, shoff, 0, 52, 0, 0, 40,
                       len(all_secs) + 1, len(sections) + 1)
    return bytes(ehdr) + bytes(body) + bytes(shdrs)


def test_synthetic_elf32_symbols_and_crc(tmp_path: Path) -> None:
    text = bytes(range(256)) * 4
    data = b"\xAA" * 64
    blob = _build_elf32(
        [(".text", SHT_PROGBITS, SHF_ALLOC, FLASH_BASE_DEFAULT, text),
         (".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 0x20000000, data)],
        [("main", FLASH_BASE_DEFAULT), ("fault", 0x20000000)])
    path = tmp_path / "synthetic.elf"
    path.write_bytes(blob)

    image = ElfImage(path)
    assert image.has_symbol("main")
    assert image.find_symbol("fault").value == 0x20000000
    assert not image.has_symbol("нет_такого_символа")

    flash = image.flash_sections()
    assert [s.name for s in flash] == [".text"], \
        "writable-секция .data не должна попадать в сверку образа"

    crc, total, per = image.image_crc32()
    assert total == len(text)
    assert crc == zlib.crc32(text)
    assert per[0][0] == ".text"


def test_rejects_non_elf(tmp_path: Path) -> None:
    path = tmp_path / "not_elf.bin"
    path.write_bytes(b"NEVERELF" + b"\x00" * 64)
    with pytest.raises(ElfError):
        ElfImage(path)


@pytest.mark.skipif(not FIRMWARE.exists(), reason="нужна сборка (make)")
def test_real_firmware_contract_symbols() -> None:
    image = ElfImage(FIRMWARE)
    for name in ("main", "CLI_ProcessLine", "fault", "fault_reason"):
        assert image.has_symbol(name), f"нет символа {name} в образе"
    flash = image.flash_sections()
    names = [s.name for s in flash]
    assert ".text" in names and ".isr_vector" in names
    assert ".data" not in names, "секция .data меняется в рантайме"
    crc, total, _per = image.image_crc32()
    assert total > 1024 and crc != 0
    # повторный расчёт обязан совпадать: CRC детерминирован
    assert image.image_crc32()[0] == crc
