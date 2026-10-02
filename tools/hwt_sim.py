"""Симулированный бэкенд HWT: прогон всего конвейера без платы и без GDB.

Зачем: конвейер (блокировка отладчика, пределы времени, вердикты, отчёты
result.json/junit.xml, сверка CRC образа) обязан проверяться в CI, где нет ни
ST-Link, ни стенда. Симулятор исполняет те же сценарии через тот же `Target`,
поэтому проверяются и логика сценариев, и отчёты.

Чего симулятор НЕ делает: он не подтверждает поведение железа. Отчёт помечен
`"simulated": true`, и вердикт симулированного прогона — предварительная
проверка конвейера, а не проверка на цели (DDTT-6.7).

Значения берутся из фикстуры (по умолчанию `tests/target/sim_values.json`):

    {
      "reg":  {"TIM1_BDTR": "0x0000C800", "TIM1_CR1": 0},
      "sym":  {"fault": 0, "fault_reason": 0},
      "expr": {"$pc": "0x0800963C", "$sp": "0x20020000"}
    }

Ключи `reg` — константы из `hwt_stm32g4`: адрес берётся из сверенного модуля,
поэтому в фикстуре не дублируются числа.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import hwt_elf
import hwt_stm32g4
try:  # и как пакет (tools.*), и как отдельный модуль — без двойных копий
    from .hwt_target import Backend, TargetError
except ImportError:  # pragma: no cover
    from hwt_target import Backend, TargetError  # type: ignore[no-redef]

PERIPHERAL_BASE = 0x40000000
SHT_NOBITS = 8


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise TargetError(f"значение {value!r} не является числом")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TargetError(f"значение {value!r} не является числом")


class SimBackend(Backend):
    """Цель-заглушка: регистры и переменные — из фикстуры, Flash — из ELF."""

    def __init__(self, elf_path: str, values: Optional[Dict[str, Any]] = None,
                 hung_symbols: Optional[List[str]] = None):
        self.image = hwt_elf.ElfImage(elf_path)
        values = values or {}
        self.reg: Dict[str, Any] = dict(values.get("reg", {}))
        self.sym: Dict[str, Any] = dict(values.get("sym", {}))
        self.expr: Dict[str, Any] = dict(values.get("expr", {}))
        self.hung = set(hung_symbols or [])
        self.breakpoints: List[str] = []
        self.trace: List[str] = []
        self._addr_to_name = {
            v: k for k, v in vars(hwt_stm32g4).items()
            if isinstance(v, int) and not k.startswith("_")}

    def target_info(self) -> Dict[str, Any]:
        return {"simulated": True, "gdb": "sim", "pc": self.expr.get("$pc"),
                "sp": self.expr.get("$sp")}

    def reset_halt(self) -> None:
        self.trace.append("reset-halt")

    def break_at(self, symbol: str) -> None:
        if not self.image.has_symbol(symbol):
            raise TargetError(f"символ {symbol!r} отсутствует в образе")
        self.breakpoints.append(symbol)

    def clear_breakpoints(self) -> None:
        self.breakpoints = []

    def continue_for(self, timeout_s: float, what: str) -> bool:
        sym = self.breakpoints[-1] if self.breakpoints else ""
        if sym in self.hung:
            raise TargetError(f"симуляция незакрытой точки останова: {sym} "
                              f"не достигается ({what})")
        if not sym:
            raise TargetError("точка останова не задана")
        return True

    def stop_reason(self) -> str:
        return "симуляция: остановка на точке останова"

    # чтение ──────────────────────────────────────────────────────────────
    def evaluate(self, expr: str) -> Any:
        if expr in self.expr:
            return _to_int(self.expr[expr])
        raise TargetError(
            f"симулятор не знает выражение {expr!r}: API цели обязан отказать "
            "вместо возврата произвольного значения (DDTT-6.2-2)")

    def read_memory(self, addr: int, size: int) -> bytes:
        addr = int(addr)
        # 1. регистр периферии — по имени константы из hwt_stm32g4
        if addr >= PERIPHERAL_BASE:
            name = self._addr_to_name.get(addr)
            if name in self.reg:
                return _to_int(self.reg[name]).to_bytes(size, "little")
            raise TargetError(f"симулятор: в фикстуре нет регистра "
                              f"{name or hex(addr)} (0x{addr:08X})")
        # 2. переменная по адресу символа из образа
        for sym in self.image.symbols:
            if sym.is_defined and sym.value == addr and sym.name in self.sym:
                return _to_int(self.sym[sym.name]).to_bytes(size, "little")
        # 3. содержимое секции образа (Flash)
        for sec in self.image.sections:
            if sec.sh_type == SHT_NOBITS or sec.size == 0:
                continue
            if sec.addr <= addr < sec.addr + sec.size:
                blob = self.image.section_bytes(sec)
                off = addr - sec.addr
                return bytes(blob[off:off + size])
        raise TargetError(f"симулятор не знает память 0x{addr:08X}")

    def write_memory(self, addr: int, data: bytes) -> None:
        self.trace.append(f"write 0x{int(addr):08X} {data.hex()}")

    def set_value(self, expr: str, value: Any) -> None:
        if expr in self.sym:
            self.sym[expr] = value
        elif expr in self.expr:
            self.expr[expr] = value
        else:
            raise TargetError(f"симулятор: некуда записать {expr!r}")
        self.trace.append(f"set {expr} = {value}")

    def force_return(self, value: int) -> None:
        self.trace.append(f"force_return {value}")


def load_fixture(path: str | Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
