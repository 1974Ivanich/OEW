"""Мок-сервер GDB RSP для тестов HWT: цель без железа, но по-настоящему.

Зачем: конвейер проверок должен исполняться в CI целиком — идентичность цели,
сверка CRC образа, сценарии, вердикты, отчёты, — но без ST-Link. Этот сервер
говорит по тому же протоколу, что OpenOCD, и отдаёт данные из настоящего ELF:
Flash — из секций образа, регистры и переменные — из заданных значений,
аппаратная точка останова останавливает цель на заданном адресе.

Чего он не подтверждает: поведение реального кремния. Это проверка
инструмента и сценариев, а не платы.
"""

from __future__ import annotations

import socketserver
import threading
from typing import Dict, List, Optional, Tuple

from tools import hwt_stm32g4
from tools.hwt_elf import ElfImage
from tools.hwt_rsp import decode_packet, encode_packet

TARGET_XML = (
    '<?xml version="1.0"?><target version="1.0"><feature name="org.gnu.gdb.'
    'arm.core"><reg name="r0" regnum="0" bitsize="32" type="int32"/>'
    '<reg name="r1" regnum="1" bitsize="32" type="int32"/>'
    '<reg name="sp" regnum="13" bitsize="32" type="data_ptr"/>'
    '<reg name="lr" regnum="14" bitsize="32" type="int32"/>'
    '<reg name="pc" regnum="15" bitsize="32" type="code_ptr"/>'
    '<reg name="xpsr" regnum="16" bitsize="32" type="int32"/>'
    "</feature></target>")


class TargetState:
    """Состояние «цели»: память, регистры, точки останова."""

    def __init__(self, elf_path: str, device_id: int = 0x469,
                 flash_size_kb: int = 512,
                 symbols: Optional[Dict[str, int]] = None,
                 registers: Optional[Dict[str, int]] = None,
                 corrupt_section: Optional[str] = None):
        self.image = ElfImage(elf_path)
        self.memory: Dict[int, int] = {}
        self._load_flash(corrupt_section)
        self.memory[hwt_stm32g4.DBGMCU_IDCODE] = 0
        self._write(hwt_stm32g4.DBGMCU_IDCODE, device_id)
        self._write(hwt_stm32g4.FLASH_SIZE_REG, flash_size_kb)
        self.registers: Dict[str, int] = {
            "pc": self.image.find_symbol("main").value,
            "sp": hwt_stm32g4.RAM_BASE + hwt_stm32g4.RAM_SIZE,
            "lr": 0, "xpsr": 0x01000000,
        }
        self.registers.update(registers or {})
        for name, value in (symbols or {}).items():
            sym = self.image.find_symbol(name)
            if sym is None:
                raise ValueError(f"в образе нет символа {name!r}")
            self._write(sym.value, value, 4)
        self.breakpoints: List[int] = []
        self.halted = True
        self.hang = False           # не останавливаться на continue
        self.console: List[str] = []

    # память ─────────────────────────────────────────────────────────────
    def _load_flash(self, corrupt_section: Optional[str]) -> None:
        for sec in self.image.flash_sections():
            data = self.image.section_bytes(sec)
            if corrupt_section == sec.name and data:
                data = bytes([data[0] ^ 0xFF]) + data[1:]
            for i, byte in enumerate(data):
                self.memory[sec.addr + i] = byte

    def _write(self, addr: int, value: int, size: int = 4) -> None:
        for i in range(size):
            self.memory[addr + i] = (value >> (8 * i)) & 0xFF

    def read(self, addr: int, size: int) -> bytes:
        return bytes(self.memory.get(addr + i, 0x00) & 0xFF for i in range(size))

    def write(self, addr: int, data: bytes) -> None:
        for i, byte in enumerate(data):
            self.memory[addr + i] = byte

    # управление ─────────────────────────────────────────────────────────
    def hit(self) -> bool:
        """Попался ли pc в точку останова (модель аппаратного break)."""
        if self.hang:
            return False
        if self.breakpoints and self.registers["pc"] in self.breakpoints:
            self.halted = True
            return True
        return False


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        state: TargetState = self.server.state  # type: ignore[attr-defined]
        buffer = bytearray()
        while True:
            try:
                data = self.request.recv(4096)
            except OSError:
                return
            if not data:
                return
            buffer += data
            # одиночный 0x03 = прерывание цели
            while buffer and buffer[0] == 0x03:
                del buffer[0]
                state.halted = True
                self.request.sendall(encode_packet("T02thread:1;"))
            while b"#" in buffer and b"$" in buffer:
                start = buffer.index(b"$")
                end = buffer.index(b"#", start)
                if end + 2 >= len(buffer):
                    break
                payload = bytes(buffer[start + 1:end])
                del buffer[:end + 3]
                request = decode_packet(payload)
                self.request.sendall(b"+")
                if request == "qSupported":
                    self._send("PacketSize=1000;qXfer:features:read+")
                    continue
                self._serve(state, request)

    def _send(self, payload: str) -> None:
        self.request.sendall(encode_packet(payload))

    def _serve(self, state: TargetState, request: str) -> None:
        if request.startswith("qXfer:features:read:target.xml"):
            self._send("l" + TARGET_XML)
        elif request.startswith("qRcmd,"):
            cmd = bytes.fromhex(request[6:]).decode("utf-8", "replace")
            state.console.clear()
            if cmd == "reset halt":
                state.registers["pc"] = state.image.find_symbol("main").value
                state.halted = True
            elif cmd == "halt":
                state.halted = True
            elif cmd == "reset run":
                state.halted = False
            else:
                self._send("E01")
                return
            state.console.append(f"{cmd}: ok\n")
            self._send("O" + state.console[-1].encode().hex())
            self._send("OK")
        elif request.startswith("p"):
            name = {0: "r0", 13: "sp", 14: "lr", 15: "pc", 16: "xpsr"}.get(
                int(request[1:], 16), "r0")
            value = state.registers.get(name, 0)
            self._send(value.to_bytes(4, "little", signed=False).hex())
        elif request.startswith("P"):
            num, _, raw = request[1:].partition("=")
            name = {0: "r0", 13: "sp", 14: "lr", 15: "pc", 16: "xpsr"}.get(
                int(num, 16))
            if name is None:
                self._send("E01")
                return
            state.registers[name] = int.from_bytes(bytes.fromhex(raw), "little")
            self._send("OK")
        elif request.startswith("m"):
            addr, _, size = request[1:].partition(",")
            self._send(state.read(int(addr, 16), int(size, 16)).hex())
        elif request.startswith("M"):
            head, _, raw = request[1:].partition(":")
            addr, _, _size = head.partition(",")
            state.write(int(addr, 16), bytes.fromhex(raw))
            self._send("OK")
        elif request.startswith("Z1,"):
            _z, addr, _kind = request.split(",")
            state.breakpoints.append(int(addr, 16))
            self._send("OK")
        elif request.startswith("z1,"):
            _z, addr, _kind = request.split(",")
            try:
                state.breakpoints.remove(int(addr, 16))
            except ValueError:
                pass
            self._send("OK")
        elif request == "?":
            self._send("T05thread:1;" if state.halted else "S00")
        elif request in ("c", "s"):
            if state.hit():
                self._send("T05thread:1;")
                return
            if state.hang:
                return  # цель «убежала»: ответа не будет — это таймаут хоста
            # иначе: считаем, что после continue цель остановилась на входе
            state.halted = True
            self._send("T05thread:1;")
        else:
            self._send("")


class MockRspServer:
    """TCP-сервер RSP на свободном порту; запускается в отдельном потоке."""

    def __init__(self, state: TargetState):
        self.state = state
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def __enter__(self) -> "MockRspServer":
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.shutdown()
        self.server.server_close()


def static_scenario_values() -> Dict[str, int]:
    """Исправное состояние покоя: то, что проверяют сценарии tests/target."""
    return {"fault": 0, "fault_reason": 0}


def quiet_register_values() -> Dict[str, int]:
    return {}


def peripheral_ok(state: TargetState) -> None:
    """Прописать периферию в исправном состоянии (MOE снят, CEN=0, BDTR ок)."""
    ok_bdtr = (hwt_stm32g4.TIM_BDTR_BKE | hwt_stm32g4.TIM_BDTR_OSSR |
               hwt_stm32g4.TIM_BDTR_OSSI)
    state._write(hwt_stm32g4.TIM1_BDTR, ok_bdtr)
    state._write(hwt_stm32g4.TIM8_BDTR, ok_bdtr)
    state._write(hwt_stm32g4.TIM1_CR1, 0)
    state._write(hwt_stm32g4.TIM8_CR1, 0)
    state._write(hwt_stm32g4.TIM1_SR, 0)
    state._write(hwt_stm32g4.TIM8_SR, 0)
    state._write(hwt_stm32g4.SCB_CFSR, 0)
    state._write(hwt_stm32g4.SCB_HFSR, 0)


def make_target(elf_path: str, corrupt_section: Optional[str] = None,
                hang: bool = False, device_id: int = 0x469,
                flash_size_kb: int = 512) -> TargetState:
    """Готовая «исправная цель» для позитивных тестов."""
    state = TargetState(elf_path, symbols=static_scenario_values(),
                        corrupt_section=corrupt_section, device_id=device_id,
                        flash_size_kb=flash_size_kb)
    peripheral_ok(state)
    state.hang = hang
    return state
