"""Транспорт HWT: GDB Remote Serial Protocol напрямую к отладочному серверу.

Зачем не GDB: на этом ПК оба `arm-none-eabi-gdb` собраны **без поддержки
Python** («Python scripting is not supported in this copy of GDB»), а
DDTT-сценарии — это Python на хосте. GDB здесь не нужен: всё, что требуется
(останов, аппаратные точки останова, чтение/запись памяти и регистров,
monitor-команды), уже есть в RSP — том самом протоколе, по которому OpenOCD
общается с GDB. Адреса символов берутся из ELF (`hwt_elf`), а не из DWARF.

Дополнительная выгода: пределы времени контролирует хост на уровне сокета
(DDTT-6.4-7), а прогон не зависит от сборки GDB на конкретном ПК.

Транспорт: TCP → OpenOCD (`gdb_port`, по умолчанию 3333).
"""

from __future__ import annotations

import socket
import time
from typing import Any, Dict, List, Optional, Tuple

try:  # и как пакет (tools.*), и как отдельный модуль — без двойных копий
    from .hwt_elf import ElfImage
    from .hwt_target import Backend, TargetError
except ImportError:  # pragma: no cover
    from hwt_elf import ElfImage  # type: ignore[no-redef]
    from hwt_target import Backend, TargetError  # type: ignore[no-redef]

ACK = 0x2B          # '+'
NACK = 0x2D         # '-'
INTERRUPT = b"\x03"

# Стандартные номера регистров ARMv7-M (используются, если сервер не отдал
# target.xml): r0..r12, sp=13, lr=14, pc=15, xpsr=16.
DEFAULT_REGS = {"r0": 0, "sp": 13, "lr": 14, "pc": 15, "xpsr": 16}
DEFAULT_REG_NAMES = {v: k for k, v in DEFAULT_REGS.items()}


class RspError(RuntimeError):
    """Отказ транспорта: соединение, формат пакета, отказ сервера."""


def encode_packet(data: str) -> bytes:
    body = bytearray()
    for ch in data.encode("utf-8"):
        if ch in (0x23, 0x24, 0x2A, 0x7D):  # '#', '$', '*', '}'
            body += bytes((0x7D, ch ^ 0x20))
        else:
            body.append(ch)
    checksum = sum(body) & 0xFF
    return b"$" + bytes(body) + b"#" + f"{checksum:02x}".encode()


def decode_packet(payload: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(payload):
        if payload[i] == 0x7D and i + 1 < len(payload):  # экранирование
            out.append(payload[i + 1] ^ 0x20)
            i += 2
            continue
        out.append(payload[i])
        i += 1
    return out.decode("utf-8", "replace")


class RspClient:
    """Минимальный клиент RSP: то, что нужно для проверок, и ничего больше."""

    def __init__(self, host: str = "127.0.0.1", port: int = 3333,
                 timeout_s: float = 5.0, debug: Optional[List[str]] = None):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.sock: Optional[socket.socket] = None
        self.console: List[str] = []
        self._log = debug if debug is not None else []

    # соединение ─────────────────────────────────────────────────────────
    def connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout_s)
        except OSError as exc:
            raise RspError(f"нет связи с отладочным сервером "
                           f"{self.host}:{self.port}: {exc}") from exc
        self.sock.settimeout(self.timeout_s)
        try:
            self.command("qSupported")
        except RspError:
            pass  # сервер может не поддержать — не критично
        self._read_features()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # низкий уровень ─────────────────────────────────────────────────────
    def _recv_byte(self, timeout_s: Optional[float] = None) -> bytes:
        assert self.sock is not None
        if timeout_s is not None:
            self.sock.settimeout(timeout_s)
        try:
            data = self.sock.recv(1)
        except socket.timeout as exc:
            raise RspError("таймаут ожидания байта от сервера") from exc
        if not data:
            raise RspError("сервер закрыл соединение")
        return data

    def _read_packet_raw(self, timeout_s: Optional[float] = None) -> bytes:
        """Дождаться '$...#xx' и подтвердить приём."""
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RspError("таймаут ожидания пакета от сервера")
            ch = self._recv_byte(left)
            if ch == b"$":
                break
            if ch in (b"\x03",):  # сервер может сообщить о останове вне пакета
                continue
        payload = bytearray()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RspError("таймаут чтения тела пакета")
            ch = self._recv_byte(left)
            if ch == b"#":
                break
            payload += ch
        _checksum = self._recv_byte(deadline - time.monotonic())
        self._recv_byte(deadline - time.monotonic())  # второй символ CRC
        self._send_raw(b"+")
        return bytes(payload)

    def _send_raw(self, data: bytes) -> None:
        assert self.sock is not None
        self.sock.sendall(data)

    def _send_packet(self, data: str) -> None:
        assert self.sock is not None
        for _attempt in range(3):
            self.sock.sendall(encode_packet(data))
            try:
                reply = self._recv_byte()
            except RspError:
                raise
            if reply == b"+":
                return
            if reply == b"-":
                continue
            raise RspError(f"сервер ответил {reply!r} вместо подтверждения")
        raise RspError("сервер не подтвердил пакет после 3 попыток")

    def command(self, data: str, timeout_s: Optional[float] = None) -> str:
        """Отправить пакет и вернуть ответ (с обработкой O-пакетов)."""
        self._send_packet(data)
        return self._read_response(timeout_s)

    def _read_response(self, timeout_s: Optional[float] = None) -> str:
        while True:
            payload = decode_packet(self._read_packet_raw(timeout_s))
            if payload.startswith("O") and len(payload) >= 3:
                # консольный вывод сервера (monitor ...) — копим, не ответ
                self.console.append(bytes.fromhex(payload[1:])
                                    .decode("utf-8", "replace"))
                continue
            self._log.append(f"<= {payload}")
            return payload

    # регистры ───────────────────────────────────────────────────────────
    def _read_features(self) -> None:
        regs: Dict[str, int] = {}
        try:
            chunk = self.command("qXfer:features:read:target.xml:0,fff")
        except RspError:
            chunk = ""
        if chunk[:1] in ("l", "m") and len(chunk) > 1:
            import re
            xml = chunk[1:]
            for name, num in re.findall(r'<reg name="([^"]+)"[^>]*?regnum="(\d+)"',
                                        xml):
                regs[name] = int(num)
        self.regnum = dict(DEFAULT_REGS)
        self.regnum.update({k: v for k, v in regs.items() if k in
                            ("r0", "sp", "lr", "pc", "xpsr")})
        self.reg_names = {v: k for k, v in self.regnum.items()}

    def read_register(self, name: str) -> int:
        num = self.regnum.get(name.lstrip("$"))
        if num is None:
            raise RspError(f"регистр {name!r} неизвестен серверу")
        reply = self.command(f"p{num:x}")
        if not reply or reply.startswith("E") or reply.startswith("E."):
            raise RspError(f"чтение регистра {name} не удалось: {reply!r}")
        try:
            return int.from_bytes(bytes.fromhex(reply), "little")
        except ValueError as exc:
            raise RspError(f"регистр {name}: неожиданный ответ {reply!r}") from exc

    def write_register(self, name: str, value: int) -> None:
        num = self.regnum.get(name.lstrip("$"))
        if num is None:
            raise RspError(f"регистр {name!r} неизвестен серверу")
        raw = int(value).to_bytes(4, "little", signed=False).hex()
        reply = self.command(f"P{num:x}={raw}")
        if reply != "OK":
            raise RspError(f"запись регистра {name} не удалась: {reply!r}")

    # память ─────────────────────────────────────────────────────────────
    def read_memory(self, addr: int, size: int) -> bytes:
        out = bytearray()
        while len(out) < size:
            chunk = min(size - len(out), 512)
            reply = self.command(f"m{addr + len(out):x},{chunk:x}")
            if reply.startswith("E"):
                raise RspError(f"память 0x{addr:08X}+{len(out)} не читается "
                               f"({reply})")
            try:
                out += bytes.fromhex(reply)
            except ValueError as exc:
                raise RspError(f"неожиданный ответ на чтение памяти: "
                               f"{reply[:40]!r}") from exc
        return bytes(out[:size])

    def write_memory(self, addr: int, data: bytes) -> None:
        reply = self.command(f"M{addr:x},{len(data):x}:{data.hex()}")
        if reply != "OK":
            raise RspError(f"запись памяти 0x{addr:08X} не удалась: {reply!r}")

    # управление ─────────────────────────────────────────────────────────
    def monitor(self, cmd: str) -> str:
        """Monitor-команда серверу (reset halt, halt, reset run и т. п.)."""
        self.console.clear()
        reply = self.command("qRcmd," + cmd.encode().hex(), timeout_s=20.0)
        text = "".join(self.console).strip()
        if reply != "OK":
            raise RspError(f"monitor {cmd!r} отклонён: {reply!r} {text}")
        return text

    def set_hw_breakpoint(self, addr: int, kind: int = 2) -> None:
        reply = self.command(f"Z1,{addr:x},{kind:x}")
        if reply != "OK":
            raise RspError(f"аппаратная точка останова @0x{addr:08X} не "
                           f"ставится: {reply!r}")

    def clear_hw_breakpoint(self, addr: int, kind: int = 2) -> None:
        self.command(f"z1,{addr:x},{kind:x}")

    def stop_reason(self) -> str:
        return self.command("?")

    def is_halted(self) -> bool:
        try:
            return not self.command("?").startswith(("S", "T"))
        except RspError:
            return False

    def continue_with_deadline(self, seconds: float) -> Optional[str]:
        """Продолжить; вернуть stop-reply или None, если предел исчерпан.

        Предел обеспечивает хост: по истечении мы прерываем цель (0x03) и
        забираем её остановку, чтобы не оставить плату бегущей вслепую
        (DDTT-6.4-7).
        """
        self._send_packet("c")
        try:
            return self._read_response(seconds)
        except RspError:
            self.interrupt()
            return None

    def interrupt(self) -> None:
        """Прервать цель: 0x03 и вычистить возможный stop-reply."""
        if self.sock is None:
            return
        try:
            self._send_raw(INTERRUPT)
            self._read_response(1.0)
        except RspError:
            pass


class RspBackend(Backend):
    """API цели поверх RSP: сценарий видит `Target`, а не OpenOCD."""

    def __init__(self, client: RspClient, image: ElfImage, state: str,
                 timeout_s: float = 10.0):
        self.client = client
        self.image = image
        self.state = state
        self.timeout_s = timeout_s
        self.breakpoints: List[Tuple[int, int]] = []
        self.last_stop: str = ""
        self._info: Dict[str, Any] = {}

    # соединение/состояние ───────────────────────────────────────────────
    def connect(self) -> None:
        self.client.connect()

    def target_info(self) -> Dict[str, Any]:
        if not self._info:
            self._info = {
                "transport": "rsp",
                "server": f"{self.client.host}:{self.client.port}",
                "state": self.state,
                "pc": self._reg("pc"),
                "sp": self._reg("sp"),
            }
        return dict(self._info)

    def _reg(self, name: str) -> Optional[int]:
        try:
            return self.client.read_register(name)
        except RspError:
            return None

    def reset_halt(self) -> None:
        cmd = "reset halt" if self.state == "reset-halt" else "halt"
        try:
            self.client.monitor(cmd)
        except RspError as exc:
            raise TargetError(f"цель не останавливается ({cmd}): {exc}") from exc
        self.last_stop = cmd

    # точки останова ─────────────────────────────────────────────────────
    def symbol_address(self, symbol: str) -> int:
        sym = self.image.find_symbol(symbol) if self.image else None
        if sym is None:
            raise TargetError(f"символ {symbol!r} отсутствует в образе — "
                              "контракт сценария нарушен (DDTT-6.7-2)")
        return sym.value

    def break_at(self, symbol: str) -> None:
        addr = self.symbol_address(symbol)
        try:
            self.client.set_hw_breakpoint(addr)
        except RspError as exc:
            raise TargetError(f"точка останова {symbol} (@0x{addr:08X}) не "
                              f"ставится: {exc}") from exc
        self.breakpoints.append((addr, 2))

    def clear_breakpoints(self) -> None:
        for addr, kind in self.breakpoints:
            try:
                self.client.clear_hw_breakpoint(addr, kind)
            except RspError:
                pass
        self.breakpoints = []

    def continue_for(self, timeout_s: float, what: str) -> bool:
        """Продолжить до остановки; предел обеспечивает хост (DDTT-6.4-7)."""
        stop = self.client.continue_with_deadline(timeout_s)
        if stop is None:
            raise TargetError(f"не дождались остановки ({what}) за "
                              f"{timeout_s:.1f} c — цель прервана, "
                              "предел времени исчерпан")
        self.last_stop = stop
        pc = self._reg("pc")
        if self.breakpoints and pc is not None:
            addr = self.breakpoints[-1][0]
            if pc not in (addr, addr + 2) and "T05" not in stop:
                raise TargetError(
                    f"остановка ({what}) не в ожидаемой точке: pc=0x{pc:08X}, "
                    f"ожидалась 0x{addr:08X} (stop={stop})")
        return True

    def stop_reason(self) -> str:
        return self.last_stop or "остановка"

    # чтение/запись ──────────────────────────────────────────────────────
    def evaluate(self, expr: str) -> Any:
        name = expr.strip()
        if name.startswith("$"):
            value = self._reg(name[1:])
            if value is None:
                raise TargetError(f"регистр {name} не читается транспортом")
            return value
        raise TargetError(
            f"выражение {expr!r} транспортом RSP не поддерживается: символы и "
            "поля структур читаются по адресу из ELF "
            "(Target.read_u32 / Target.word_at_symbol) — API цели обязан "
            "отказать, а не вернуть произвольное значение (DDTT-6.2-2)")

    def read_memory(self, addr: int, size: int) -> bytes:
        return self.client.read_memory(int(addr), int(size))

    def write_memory(self, addr: int, data: bytes) -> None:
        self.client.write_memory(int(addr), data)

    def set_value(self, expr: str, value: Any) -> None:
        name = expr.strip()
        if name.startswith("$"):
            self.client.write_register(name[1:], int(value))
            return
        raise TargetError(f"запись {expr!r} транспортом RSP не поддерживается "
                          "(только регистры ядра)")

    def force_return(self, value: int) -> None:
        raise TargetError(
            "принудительный возврат из функции требует символической отладки "
            "(GDB с Python/DWARF) и в RSP-транспорте недоступен. Сценарий "
            "обязан отказать, а не имитировать инъекцию (DDTT-6.2-4)")
