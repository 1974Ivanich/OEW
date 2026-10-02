"""ELF64-парсер и образ прошивки для HWT (только stdlib).

Зачем свой парсер: проверки на цели (DDTT) требуют от хоста трёх вещей,
которые обязаны работать без внешних зависимостей и одинаково в CI:

* контракты — наличие символов в образе до обращения к плате (DDTT-6.7-2);
* идентичность — CRC-32 загружаемых в Flash секций, чтобы доказать,
  что на цели работает именно тот образ, по которому написан сценарий
  (DDTT-6.4-3/6.4-4);
* диапазоны — адреса и размеры секций, которые нужно прочитать с цели
  через отладчик для этой сверки.

Парсер намеренно минимальный: ELF64 little-endian (Cortex-M), только
чтение .symtab/.shstrtab/.strtab и содержимого секций.
"""

from __future__ import annotations

import dataclasses
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FLASH_BASE_DEFAULT = 0x08000000
FLASH_SIZE_DEFAULT = 512 * 1024

SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4

SHN_UNDEF = 0
STB_LOCAL = 0
STB_GLOBAL = 1


class ElfError(RuntimeError):
    """Образ не читается или не является ELF64 LE."""


@dataclasses.dataclass(frozen=True)
class Section:
    index: int
    name: str
    sh_type: int
    flags: int
    addr: int
    offset: int
    size: int

    @property
    def is_alloc(self) -> bool:
        return bool(self.flags & SHF_ALLOC)

    @property
    def is_writable(self) -> bool:
        return bool(self.flags & SHF_WRITE)

    @property
    def is_code(self) -> bool:
        return bool(self.flags & SHF_EXECINSTR)

    def in_flash(self, base: int = FLASH_BASE_DEFAULT,
                 size: int = FLASH_SIZE_DEFAULT) -> bool:
        return base <= self.addr < base + size


@dataclasses.dataclass(frozen=True)
class Symbol:
    name: str
    value: int
    size: int
    binding: int
    shndx: int

    @property
    def is_defined(self) -> bool:
        return self.shndx != SHN_UNDEF

    @property
    def is_global(self) -> bool:
        return self.binding == STB_GLOBAL


class ElfImage:
    """Прошивка как образ: секции, символы, CRC загружаемого содержимого."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.data = self.path.read_bytes()
        except OSError as exc:  # pragma: no cover - зависит от ФС
            raise ElfError(f"образ не читается: {self.path}: {exc}") from exc
        self._parse()

    # ── разбор ────────────────────────────────────────────────────────────
    def _parse(self) -> None:
        d = self.data
        if len(d) < 52 or d[:4] != b"\x7fELF":
            raise ElfError(f"не ELF: {self.path}")
        if d[4] not in (1, 2):
            raise ElfError(f"неизвестный EI_CLASS={d[4]}")
        if d[5] != 1:
            raise ElfError(f"только little-endian (EI_DATA=1), получено {d[5]}")
        self.is64 = d[4] == 2
        # Cortex-M — ELF32 (EI_CLASS=1); ELF64 поддержан для полноты.
        if self.is64:
            e_shoff, = struct.unpack_from("<Q", d, 0x28)
            e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x3A)
            sh_off_fmt = "<IIQQQQ"
            sh_entsize_min = 64
            sym_fmt, sym_size = "<IBBHQQ", 24
        else:
            e_shoff, = struct.unpack_from("<I", d, 0x20)
            e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", d, 0x2E)
            sh_off_fmt = "<IIIIII"
            sh_entsize_min = 40
            sym_fmt, sym_size = "<IIIBBH", 16

        if e_shoff == 0 or e_shnum == 0:
            raise ElfError(f"нет таблицы секций: {self.path}")
        if e_shentsize < sh_entsize_min:
            raise ElfError(f"неверный e_shentsize={e_shentsize}")

        raw: List[Tuple[int, int, int, int, int, int, int]] = []
        for i in range(e_shnum):
            off = e_shoff + i * e_shentsize
            if off + sh_entsize_min > len(d):
                raise ElfError(f"секция {i} выходит за файл")
            (sh_name, sh_type, sh_flags, sh_addr, sh_offset,
             sh_size) = struct.unpack_from(sh_off_fmt, d, off)
            raw.append((sh_name, sh_type, sh_flags, sh_addr, sh_offset,
                        sh_size, i))

        if e_shstrndx >= e_shnum:
            raise ElfError("e_shstrndx вне таблицы секций")
        shstr_off, shstr_size = raw[e_shstrndx][4], raw[e_shstrndx][5]
        shstr = d[shstr_off:shstr_off + shstr_size]

        self.sections: List[Section] = []
        for sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, idx in raw:
            self.sections.append(Section(
                index=idx, name=_cstr(shstr, sh_name), sh_type=sh_type,
                flags=sh_flags, addr=sh_addr, offset=sh_offset, size=sh_size))

        self.symbols: List[Symbol] = []
        for sec in self.sections:
            if sec.sh_type != SHT_SYMTAB or sec.size == 0:
                continue
            # sh_link указывает на строковую таблицу символов
            link_pos = e_shoff + sec.index * e_shentsize + (0x28 if self.is64
                                                            else 0x18)
            sh_link = struct.unpack_from("<I", d, link_pos)[0]
            if sh_link >= len(self.sections):
                continue
            str_sec = self.sections[sh_link]
            strs = d[str_sec.offset:str_sec.offset + str_sec.size]
            count = sec.size // sym_size
            for j in range(count):
                off = sec.offset + j * sym_size
                if off + sym_size > len(d):
                    break
                st_name, st_value, st_size, st_info, _other, st_shndx = \
                    struct.unpack_from(sym_fmt, d, off)
                if st_name == 0:
                    continue
                self.symbols.append(Symbol(
                    name=_cstr(strs, st_name), value=st_value, size=st_size,
                    binding=(st_info >> 4) & 0xF, shndx=st_shndx))

        self._by_name: Dict[str, List[Symbol]] = {}
        for sym in self.symbols:
            self._by_name.setdefault(sym.name, []).append(sym)

    # ── символы ───────────────────────────────────────────────────────────
    def find_symbol(self, name: str) -> Optional[Symbol]:
        """Определённый символ по имени; неопределённые (импорты) — не считаются."""
        for sym in self._by_name.get(name, ()):
            if sym.is_defined:
                return sym
        return None

    def has_symbol(self, name: str) -> bool:
        return self.find_symbol(name) is not None

    def symbol_names(self) -> List[str]:
        return sorted({s.name for s in self.symbols if s.is_defined})

    # ── содержимое секций ─────────────────────────────────────────────────
    def section_bytes(self, sec: Section) -> bytes:
        if sec.sh_type == SHT_NOBITS:
            return b"\x00" * sec.size
        return self.data[sec.offset:sec.offset + sec.size]

    def flash_sections(self, base: int = FLASH_BASE_DEFAULT,
                       size: int = FLASH_SIZE_DEFAULT) -> List[Section]:
        """Секции, которые лежат в Flash и никогда не меняются в рантайме.

        Отсекаются writable-секции (.data): в рантайме их содержимое
        изменяется, сравнение с образом было бы ложным.
        """
        out = [s for s in self.sections
               if s.sh_type == SHT_PROGBITS and s.size > 0
               and s.is_alloc and not s.is_writable and s.in_flash(base, size)]
        return sorted(out, key=lambda s: s.addr)

    def image_crc32(self, base: int = FLASH_BASE_DEFAULT,
                    size: int = FLASH_SIZE_DEFAULT
                    ) -> Tuple[int, int, List[Tuple[str, int, int, int]]]:
        """CRC-32 загружаемого содержимого Flash.

        Возвращает (общий CRC, всего байт, [(секция, адрес, размер, CRC)]).
        Общий CRC считается по склейке секций в порядке адресов — так же
        его считает хост после чтения тех же диапазонов с цели.
        """
        blob = bytearray()
        per: List[Tuple[str, int, int, int]] = []
        for sec in self.flash_sections(base, size):
            chunk = self.section_bytes(sec)
            per.append((sec.name, sec.addr, sec.size, zlib.crc32(chunk)))
            blob += chunk
        return zlib.crc32(bytes(blob)), len(blob), per


def _cstr(table: bytes, off: int) -> str:
    if off >= len(table):
        return ""
    end = table.find(b"\x00", off)
    if end < 0:
        end = len(table)
    return table[off:end].decode("utf-8", "replace")
