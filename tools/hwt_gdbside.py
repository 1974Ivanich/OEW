"""Сторона GDB для HWT: то, что исполняется внутри отладчика на хосте.

Запускается не напрямую, а отладчиком:

    arm-none-eabi-gdb -q -batch -nx \
        -ex "set confirm off" -x tools/hwt_gdbside.py build/firmware.elf

Режим задаётся переменной окружения HWT_MODE:

* `probe`  — узнать идентичность цели (DEV_ID, размер Flash), ничего не менять;
* `image`  — прочитать с цели секции образа и записать дампы для сверки CRC;
* `case`   — выполнить один сценарий (`HWT_CASE`) с API цели.

Прошивка **не записывается**: HWT проверяет, а не программирует. Запись —
отдельная явная операция (`make flash`).

Предел времени сценария контролирует хост: он запускает этот процесс и
завершает его по deadline (DDTT-6.4-7). Внутри GDB отдельного таймера нет —
это осознанно: асинхронный `continue` в batch-режиме не воспроизводится
одинаково на разных сборках GDB, а честный внешний предел есть всегда.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import gdb  # type: ignore[import-not-found]  # доступен только внутри GDB

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (HERE, os.environ.get("HWT_TOOLS", "")):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

from hwt_target import (  # noqa: E402  (импорт после правки sys.path)
    Backend, CaseResult, Target, TargetError, exit_code_for, load_scenario,
    run_case, VERDICT_ERROR,
)
import hwt_elf  # noqa: E402


def _write_payload(out_path: str, payload: Dict[str, Any]) -> None:
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    else:  # pragma: no cover - отчёт всегда пишется в файл из раннера
        print(json.dumps(payload, ensure_ascii=False, indent=2))

# Адреса регистров STM32G4 (RM0440): идентификация кристалла и размер Flash.
DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FFF75E0


class GdbBackend(Backend):
    """Транспорт через GDB RSP. Сценарий видит только `Target`."""

    def __init__(self, port: int, state: str):
        self.port = port
        self.state = state
        self.last_stop: str = ""
        self.stopped: bool = False
        self._info: Dict[str, Any] = {}
        self._connect()

    # соединение ─────────────────────────────────────────────────────────
    def _connect(self) -> None:
        try:
            gdb.execute(f"target remote localhost:{self.port}", to_string=True)
        except gdb.error as exc:
            raise TargetError(f"нет связи с отладочным сервером на порту "
                              f"{self.port}: {exc}") from exc
        try:
            gdb.execute("set confirm off", to_string=True)
            gdb.execute("monitor reset halt", to_string=True)
        except gdb.error as exc:
            raise TargetError(f"цель не останавливается: {exc}") from exc
        self.stopped = True

    def target_info(self) -> Dict[str, Any]:
        if not self._info:
            self._info = {
                "state": self.state,
                "gdb": _gdb_version(),
                "pc": self._safe_eval("$pc"),
                "sp": self._safe_eval("$sp"),
            }
        return dict(self._info)

    def _safe_eval(self, expr: str) -> Any:
        try:
            return _to_plain(gdb.parse_and_eval(expr))
        except Exception:
            return None

    # состояние цели ─────────────────────────────────────────────────────
    def reset_halt(self) -> None:
        cmd = "monitor reset halt" if self.state == "reset-halt" \
            else "monitor halt"
        try:
            gdb.execute(cmd, to_string=True)
        except gdb.error as exc:
            raise TargetError(f"не удалось остановить цель ({cmd}): {exc}") \
                from exc
        self.stopped = True
        self.last_stop = cmd

    def load_symbols(self, elf_path: str) -> None:
        try:
            gdb.execute(f'file "{elf_path}"', to_string=True)
        except gdb.error as exc:
            raise TargetError(f"символы образа не загружены: {exc}") from exc

    # точки останова ─────────────────────────────────────────────────────
    def break_at(self, symbol: str) -> None:
        addr = self.symbol_address(symbol)
        try:
            # Аппаратная точка останова: не патчит Flash и не срывает тайминги
            # так, как программная (DDTT-6.2-3).
            gdb.execute(f"hbreak *0x{addr:08X}", to_string=True)
        except gdb.error as exc:
            raise TargetError(f"точка останова {symbol} (@0x{addr:08X}) не "
                              f"ставится: {exc}") from exc

    def clear_breakpoints(self) -> None:
        try:
            gdb.execute("delete breakpoints", to_string=True)
        except gdb.error:
            pass

    def symbol_address(self, symbol: str) -> int:
        try:
            return int(gdb.parse_and_eval(f"(unsigned int)&{symbol}"))
        except gdb.error as exc:
            raise TargetError(f"символ {symbol!r} не найден в отладочной "
                              f"информации образа: {exc}") from exc

    def continue_for(self, timeout_s: float, what: str) -> bool:
        """Продолжить до остановки. Внешний предел — на хосте.

        `timeout_s` не используется внутри GDB, но остаётся в подписи: хост
        считает от него общий deadline процесса.
        """
        try:
            gdb.execute("continue", to_string=True)
        except gdb.error as exc:
            raise TargetError(f"остановка не достигнута ({what}): {exc}") \
                from exc
        return True

    def stop_reason(self) -> str:
        return self.last_stop or "остановка получена"

    # чтение ──────────────────────────────────────────────────────────────
    def evaluate(self, expr: str) -> Any:
        val = gdb.parse_and_eval(expr)
        return _to_plain(val)

    def read_memory(self, addr: int, size: int) -> bytes:
        return gdb.selected_inferior().read_memory(addr, size).tobytes()

    def force_return(self, value: int) -> None:
        try:
            gdb.execute(f"return ({value})", to_string=True)
        except gdb.error as exc:
            raise TargetError(f"принудительный возврат невозможен: {exc}") \
                from exc

    def set_value(self, expr: str, value: Any) -> None:
        try:
            gdb.parse_and_eval(f"({expr}) = ({value})")
        except gdb.error as exc:
            raise TargetError(f"запись {expr} невозможна: {exc}") from exc

    def write_memory(self, addr: int, data: bytes) -> None:
        gdb.selected_inferior().write_memory(addr, data, len(data))


def _to_plain(value: Any) -> Any:
    """gdb.Value → int/str; нечитаемое значение — исключение, а не догадка."""
    try:
        return int(value)
    except Exception:
        pass
    try:
        return str(value)
    except Exception as exc:  # pragma: no cover
        raise TargetError(f"значение недоступно: {exc}") from exc


def _gdb_version() -> str:
    try:
        return gdb.VERSION
    except Exception:  # pragma: no cover
        return "unknown"


# ── режимы ────────────────────────────────────────────────────────────────

def _read_u16(backend: GdbBackend, addr: int) -> Optional[int]:
    try:
        raw = backend.read_memory(addr, 2)
    except Exception:
        return None
    return int.from_bytes(raw, "little")


def mode_probe(backend: GdbBackend) -> Dict[str, Any]:
    idcode = _read_u16(backend, DBGMCU_IDCODE)
    flash_kb = _read_u16(backend, FLASH_SIZE_REG)
    return {
        "device_id": None if idcode is None else idcode & 0x0FFF,
        "revision_id": None if idcode is None else (idcode >> 16) & 0xFFFF,
        "flash_size_kb": flash_kb,
        "gdb": _gdb_version(),
    }


def mode_image(backend: GdbBackend, elf_path: str, out_dir: str) -> Dict[str, Any]:
    base = int(os.environ.get("HWT_FLASH_BASE", str(hwt_elf.FLASH_BASE_DEFAULT)))
    size = int(os.environ.get("HWT_FLASH_SIZE", str(hwt_elf.FLASH_SIZE_DEFAULT)))
    image = hwt_elf.ElfImage(elf_path)
    os.makedirs(out_dir, exist_ok=True)
    dumps: List[Dict[str, Any]] = []
    for sec in image.flash_sections(base, size):
        path = os.path.join(out_dir, sec.name.strip(".") + ".bin")
        data = backend.read_memory(sec.addr, sec.size)
        with open(path, "wb") as fh:
            fh.write(data)
        dumps.append({"section": sec.name, "addr": sec.addr, "size": sec.size,
                      "file": path})
    return {"dumps": dumps}


def mode_case(backend: GdbBackend, args: Dict[str, Any]) -> CaseResult:
    scenario = args["scenario"]
    case_id = args["case_id"]
    cases = load_scenario(scenario)
    if case_id not in cases:
        return CaseResult(id=case_id, verdict=VERDICT_ERROR,
                          error=f"сценарий {case_id} не найден в {scenario}")
    meta, func = cases[case_id]
    timeout_s = float(args.get("timeout_s") or meta.timeout_s)
    image = None
    if args.get("elf"):
        image = hwt_elf.ElfImage(args["elf"])
    target = Target(backend, meta,
                    allow_injections=bool(args.get("allow_injections")),
                    image=image,
                    deadline=time.monotonic() + timeout_s)
    return run_case(meta, func, target)


def main() -> int:
    mode = os.environ.get("HWT_MODE", "case")
    out_path = os.environ.get("HWT_OUT", "")
    elf_path = os.environ.get("HWT_ELF", "")
    port = int(os.environ.get("HWT_GDB_PORT", "3333"))
    state = os.environ.get("HWT_STATE", "reset-halt")

    backend = GdbBackend(port, state)
    if elf_path:
        backend.load_symbols(elf_path)

    payload: Dict[str, Any]
    if mode == "probe":
        payload = {"mode": "probe", "probe": mode_probe(backend)}
        code = 0
    elif mode == "image":
        payload = {"mode": "image", "image": mode_image(
            backend, elf_path, os.environ.get("HWT_DUMP_DIR", "hwt_dumps"))}
        code = 0
    elif mode == "recover":
        # DDTT-6.4-8: при любом исходе попытаться вернуть цель в работу.
        try:
            gdb.execute("monitor reset run", to_string=True)
            payload = {"mode": "recover", "recovered": True}
            code = 0
        except gdb.error as exc:
            payload = {"mode": "recover", "recovered": False,
                       "error": str(exc)}
            code = 2
        _write_payload(out_path, payload)
        return code
    else:
        result = mode_case(backend, {
            "scenario": os.environ.get("HWT_SCENARIO", ""),
            "case_id": os.environ.get("HWT_CASE", ""),
            "timeout_s": os.environ.get("HWT_TIMEOUT_S"),
            "allow_injections": os.environ.get("HWT_ALLOW_INJECTIONS") == "1",
        })
        payload = {"mode": "case", "result": result.to_dict()}
        code = exit_code_for([result])

    if out_path:
        _write_payload(out_path, payload)
    else:  # pragma: no cover
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    try:
        gdb.execute("monitor reset run", to_string=True)
    except gdb.error:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
