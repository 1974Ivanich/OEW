"""Тесты транспорта HWT (RSP) и границ API цели без железа."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock_rsp_server import MockRspServer, make_target
from tools import hwt_stm32g4
from tools.hwt_elf import ElfImage
from tools.hwt_rsp import (RspBackend, RspClient, RspError, decode_packet,
                          encode_packet)
from tools.hwt_target import CaseMeta, Target, TargetError

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "build" / "firmware.elf"

pytestmark = pytest.mark.skipif(not FIRMWARE.exists(),
                                reason="нужна сборка (make) для образа ELF")


def _client(port: int, timeout_s: float = 3.0) -> RspClient:
    client = RspClient("127.0.0.1", port, timeout_s=timeout_s)
    client.connect()
    return client


def test_packet_roundtrip_and_escaping() -> None:
    for text in ("qSupported", "m8000000,4", "qRcmd,72657365742068616c74",
                 "weird#$}*chars"):
        raw = encode_packet(text)
        payload = raw[1:raw.index(b"#")]
        assert decode_packet(payload) == text, text
    assert encode_packet("OK") == b"$OK#9a"


def test_client_reads_memory_registers_and_breakpoints() -> None:
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        client = _client(srv.port)
        try:
            assert client.read_memory(hwt_stm32g4.DBGMCU_IDCODE, 2) == \
                b"\x69\x04"
            assert client.read_register("pc") == srv.state.registers["pc"]
            assert client.monitor("reset halt")
            client.set_hw_breakpoint(0x08000000)
            assert 0x08000000 in srv.state.breakpoints
            client.clear_hw_breakpoint(0x08000000)
            assert 0x08000000 not in srv.state.breakpoints
        finally:
            client.close()


def test_backend_reads_word_by_symbol_and_address() -> None:
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        client = _client(srv.port)
        try:
            image = ElfImage(FIRMWARE)
            target = Target(RspBackend(client, image, "reset-halt"),
                            CaseMeta(id="X", function="x"), image=image)
            assert target.word_at_symbol("fault") == 0
            assert target.read_u32(hwt_stm32g4.TIM1_BDTR) == (
                hwt_stm32g4.TIM_BDTR_BKE | hwt_stm32g4.TIM_BDTR_OSSR |
                hwt_stm32g4.TIM_BDTR_OSSI)
        finally:
            client.close()


def test_backend_refuses_symbolic_expressions_and_force_return() -> None:
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        client = _client(srv.port)
        try:
            backend = RspBackend(client, ElfImage(FIRMWARE), "reset-halt")
            for expr in ("TIM1->BDTR", "g_motor_params.Rs_mOhm", "vfc.f_e_hz"):
                with pytest.raises(TargetError):
                    backend.evaluate(expr)
            assert backend.evaluate("$pc") == srv.state.registers["pc"]
            with pytest.raises(TargetError):
                backend.force_return(0)
        finally:
            client.close()


def test_continue_without_stop_is_timeout_and_target_is_halted() -> None:
    with MockRspServer(make_target(str(FIRMWARE), hang=True)) as srv:
        client = _client(srv.port)
        try:
            assert client.continue_with_deadline(1.0) is None
            assert client.stop_reason().startswith(("T", "S"))
        finally:
            client.close()


def test_reach_reports_timeout_as_error() -> None:
    with MockRspServer(make_target(str(FIRMWARE), hang=True)) as srv:
        client = _client(srv.port)
        try:
            image = ElfImage(FIRMWARE)
            target = Target(RspBackend(client, image, "reset-halt"),
                            CaseMeta(id="X", function="x", timeout_s=2.0,
                                     contracts=("main",)), image=image)
            with pytest.raises(TargetError) as exc:
                target.reach("main")
            assert "не дождались" in str(exc.value)
        finally:
            client.close()


def test_missing_symbol_is_contract_error() -> None:
    with MockRspServer(make_target(str(FIRMWARE))) as srv:
        client = _client(srv.port)
        try:
            backend = RspBackend(client, ElfImage(FIRMWARE), "reset-halt")
            with pytest.raises(TargetError):
                backend.break_at("нет_такого_символа")
        finally:
            client.close()


def test_connection_refused_is_clear_error() -> None:
    client = RspClient("127.0.0.1", 1, timeout_s=0.5)  # закрытый порт
    with pytest.raises(RspError):
        client.connect()
