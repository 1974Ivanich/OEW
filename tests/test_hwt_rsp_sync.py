"""Ресинхронизация RSP-клиента и проверки при чтении регистров.

Отдельный файл сделан намеренно: `tests/test_hwt_rsp.py` пропускается целиком по
`skipif(not FIRMWARE.exists())`, поэтому добавленные туда тесты не выполняются ни на
одном прогоне без собранного образа — то есть защита от этих регрессий оставалась бы
инертной. Здесь железо и сборка не нужны: пир скриптуется на `socket.socketpair()`,
поэтому тесты идут всегда.

Проверяемые дефекты (оба найдены на стенде через `pc=0x00000000` при реальном
`0x0800cdc0` и ответ `b'K'` вместо подтверждения):

1. `read_register` не проверял, остановлена ли цель, и принимал любой ответ как
   значение регистра.
2. При таймауте хвост пакета оставался в сокете, и следующая команда читала `K`
   из `$OK#9a` как подтверждение — поток после этого смещён, ответы разъезжаются.
"""

from __future__ import annotations

import socket
import threading

import pytest

from tools.hwt_rsp import RspClient, RspError, encode_packet


def _scripted_client(replies):
    """Клиент на socketpair; на каждый принятый пакет пир шлёт replies[i]."""
    mine, peer = socket.socketpair()

    def serve() -> None:
        buf = b""
        for raw in replies:
            while b"#" not in buf or len(buf) < buf.index(b"#") + 3:
                buf += peer.recv(4096)
            buf = buf[buf.index(b"#") + 3:]
            peer.sendall(raw)

    threading.Thread(target=serve, daemon=True).start()
    client = RspClient("127.0.0.1", 0, timeout_s=2.0)
    client.sock = mine
    mine.settimeout(2.0)
    client.regnum = {"pc": 15}
    return client, peer


def test_is_halted_true_on_stop_reply() -> None:
    """Остановленная цель отвечает S/T, и предикат обязан сказать True."""
    client, peer = _scripted_client([b"+" + encode_packet("S05")])
    try:
        assert client.is_halted() is True
    finally:
        client.close()
        peer.close()


def test_read_register_rejects_non_8_hex() -> None:
    """Ответ не из восьми hex-цифр значением регистра не считается."""
    client, peer = _scripted_client([b"+" + encode_packet("T05"),
                                     b"+" + encode_packet("1234")])
    try:
        with pytest.raises(RspError):
            client.read_register("pc")
    finally:
        client.close()
        peer.close()


def test_stale_tail_before_ack_does_not_break_next_command() -> None:
    """Хвост прерванного пакета ('K#9a') не должен ломать следующую команду."""
    client, peer = _scripted_client([b"K#9a+" + encode_packet("OK")])
    try:
        assert client.command("qC") == "OK"
    finally:
        client.close()
        peer.close()
