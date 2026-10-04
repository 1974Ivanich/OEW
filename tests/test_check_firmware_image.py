"""Регрессионные тесты проверки класса прошитого образа — без железа.

Закрывают дефект docs/ACCEPTANCE_LESSONS.md §25.12 на уровне инструментов:

1. образ «commissioning»-артефакта CI (SYNT+HOST_TEST) обязан опознаваться как
   SYNTHETIC (BOAR-профиль вырезан) — на этом образе кампания mapcap невозможна;
2. класс BOARD (BOAR доступен) опознаётся по маркерам rev-7 reference;
3. дамп Flash доказывается против образа из ZIP побайтово (и хвост — стирание);
4. скрипт сборки кампанийного образа fail-closed: SYNT/HOST_TEST отвергаются
   ДО запуска make.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.build_campaign_image import main as build_main
from tools.check_firmware_image import (
    BOARD_MARKERS,
    SYNTHETIC_MARKERS,
    compare_dump_to_image,
    main,
    marker_report,
    read_image_from_zip,
)

REPO = Path(__file__).resolve().parent.parent
AL_PKG_ZIP = REPO / "docs" / "pc3_package" / "pc3_al_pkg_v2" / "firmware" / "ci733" / "artifact_ci733.zip"
AL_PKG_IMAGE_SHA256 = "351dbbb1aeadb900d00bf7b06926c4c11cdcb4619fff6c49a28232e5c3b63158"


def _board_class_blob() -> bytes:
    """Минимальный блок с маркерами BOARD-ветки (SYNT-маркеров нет)."""
    blob = b"\x00" * 16
    for pat in BOARD_MARKERS.values():
        blob += pat
    return blob


def test_package_artifact_is_synthetic_class() -> None:
    """Образ из пакета pc3_al_pkg_v2 — класса SYNTHETIC: BOAR-профиль вырезан (§25.12)."""
    assert AL_PKG_ZIP.is_file(), f"нет артефакта пакета: {AL_PKG_ZIP}"
    image = read_image_from_zip(AL_PKG_ZIP)
    assert hashlib.sha256(image).hexdigest() == AL_PKG_IMAGE_SHA256

    rep = marker_report(image)
    assert rep["verdict"] == "SYNTHETIC"
    assert all(hits > 0 for hits in rep["synthetic"].values())
    assert all(hits == 0 for hits in rep["board"].values())


def test_board_class_blob_is_board_verdict() -> None:
    rep = marker_report(_board_class_blob())
    assert rep["verdict"] == "BOARD"
    assert all(hits > 0 for hits in rep["board"].values())
    assert all(hits == 0 for hits in rep["synthetic"].values())


def test_dump_identity_and_erased_tail() -> None:
    image = b"".join(SYNTHETIC_MARKERS.values()) + b"payload"
    dump = image + b"\xff" * 32
    cmp = compare_dump_to_image(dump, image)
    assert cmp["identical"] is True
    assert cmp["tail_erased"] is True
    assert cmp["dump_prefix_sha256"] == cmp["image_sha256"]

    corrupted = bytearray(dump)
    corrupted[0] ^= 0x01
    assert compare_dump_to_image(bytes(corrupted), image)["identical"] is False
    assert compare_dump_to_image(image + b"\x00" * 32, image)["tail_erased"] is False


def test_cli_exit_codes(tmp_path: Path) -> None:
    board = tmp_path / "board.bin"
    board.write_bytes(_board_class_blob())
    assert main(["--image", str(board)]) == 0

    synth = tmp_path / "synth.bin"
    synth.write_bytes(b"".join(SYNTHETIC_MARKERS.values()))
    assert main(["--image", str(synth)]) == 1

    assert main([]) == 2
    assert main(["--image", str(tmp_path / "missing.bin")]) == 2

    dump = tmp_path / "dump.bin"
    dump.write_bytes(b"other" + b"\xff" * 8)
    assert main(["--dump", str(dump), "--image", str(board)]) == 1


def test_cli_dump_vs_zip_identity(tmp_path: Path) -> None:
    """Дамп = образ из ZIP + стирание: идентичность доказана, класс SYNTHETIC => FAIL."""
    image = read_image_from_zip(AL_PKG_ZIP)
    dump = tmp_path / "dump.bin"
    dump.write_bytes(image + b"\xff" * 64)
    assert main(["--dump", str(dump), "--artifact-zip", str(AL_PKG_ZIP)]) == 1


def test_zip_entry_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_image_from_zip(AL_PKG_ZIP, "no_such_file.bin")


def test_build_campaign_refuses_synthetic_defines() -> None:
    """Компанийный образ не должен собираться с SYNT/HOST_TEST (fail-closed до make)."""
    assert build_main(["--define", "OEW_HOST_TEST=1"]) == 2
    assert build_main(["--define", "OEW_MAP_SYNTHETIC_PROFILE=1"]) == 2


def test_build_campaign_accepts_board_image_without_make(tmp_path: Path) -> None:
    image = tmp_path / "firmware.bin"
    image.write_bytes(_board_class_blob())
    assert build_main(["--image", str(image)]) == 0
    assert (tmp_path / "firmware.provenance.json").is_file()
