"""Regression for tools/verify_boar_geometry_dataset.py.

Проверяет, что верификатор считает ровно то, что заявлено в
docs/BOAR_DATASET_AUDIT_VERIFICATION.md: счётчики, структурные пропуски seq,
углы строк против таблицы прошивки и детекцию синтетических identity-подписей.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import verify_boar_geometry_dataset as vbd  # noqa: E402

# Вектор сектора 2 (0, 8192, -8192) при MID=500, ARR=999:
# CCR = 500 + mod*500/32768 -> 500 / 625 / 375.
DATASET = """# mini fixture
identity board=7 pwm=294 arr=999 trigger=0x4F455731 toff=0 dt=192 clk=42500000 smp=1281 res=0 acs=0x13572468 ccs=0x24681357
provenance cid=0x424F4152 dcrc=0xC3FA2C1B tb=0x20260902 qr=1 sr=1 cr=1
row sector=2 window=0 phase_a=0 phase_b=1
sample seq=7 idc1=100 idc2=200 ict=0 vbus=60000 refu=100 refv=200 refw=-300 margin=110 settled=1 scope=1
sample seq=8 idc1=100 idc2=200 ict=0 vbus=60000 refu=100 refv=200 refw=-300 margin=110 settled=1 scope=1
sample seq=11 idc1=100 idc2=200 ict=0 vbus=60000 refu=100 refv=200 refw=-300 margin=110 settled=1 scope=1
cell mu=0 mv=8192 mw=-8192 margin=110 status=1
cell mu=0 mv=8192 mw=-8192 margin=110 status=1
row sector=0 window=0 phase_a=0 phase_b=1
sample seq=20 idc1=10 idc2=20 ict=0 vbus=60000 refu=10 refv=20 refw=-30 margin=110 settled=1 scope=1
cell mu=8192 mv=0 mw=-8192 margin=110 status=1
"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "mini_dataset.txt"
    p.write_text(DATASET, encoding="utf-8")
    return p


def test_counts_and_seq_gaps(tmp_path):
    text = vbd.build_report(_write(tmp_path))
    assert "samples    : 4" in text
    assert "cells: 3" in text
    # ряд 2/0: seq 7,8,11 -> span 5, пропущено 2, один разрыв
    assert "sector 2 window 0: n= 3 seq 7..11 span=  5 missing=  2 gaps=1" in text
    assert "total missing seq numbers: 2" in text


def test_row_angles_match_firmware_table(tmp_path):
    text = vbd.build_report(_write(tmp_path))
    assert "sector 2 window 0: n= 2 angle   90.00  table   90.00  dev  +0.00 deg" in text
    assert "sector 0 window 0: n= 1 angle   30.00  table   30.00  dev  +0.00 deg" in text
    assert "worst row deviation from table angle: 0.00 deg" in text


def test_synthetic_identity_signatures_flagged(tmp_path):
    text = vbd.build_report(_write(tmp_path))
    assert "acs      dataset=0x13572468   board=0x26B9B97B   MISMATCH <- SYNTHETIC placeholder" in text
    assert "ccs      dataset=0x24681357   board=0x13552B12   MISMATCH <- SYNTHETIC placeholder" in text
    assert "trigger  dataset=0x4F455731   board=0x4F455731   OK" in text


def test_firmware_table_angles():
    expected = {0: 30.0, 1: -30.0, 2: 90.0, 3: 150.0, 4: -90.0, 5: -150.0}
    for sector, want in expected.items():
        got = vbd.angle_deg(*vbd.BOARD_MOD[sector])
        assert abs(got - want) < 1e-9, (sector, got, want)


def test_real_dataset_ols_residual_is_identically_zero():
    """refu==idc1, refv==idc2 => линейный фит точен по построению (не доказательство)."""
    dataset = Path(__file__).resolve().parent.parent / "boar_geometry_dataset.txt"
    if not dataset.exists():
        import pytest
        pytest.skip("boar_geometry_dataset.txt отсутствует")
    text = vbd.build_report(dataset)
    assert "OLS-остаток" in text
    assert "rms(refu)=0.0000 rms(refv)=0.0000 rms(refw)=0.0000 mA" in text
    assert "не является свидетельством линейности ADC" in text
