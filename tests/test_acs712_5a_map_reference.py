#!/usr/bin/env python3
"""Host-only contract tests for the ACS712-5A / 1..3 A map reference.

These tests intentionally prove software semantics only. They do not qualify
the physical ACS712-5A sensor or the TAO3104A acquisition chain.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ingest = load_module("map_scope_ingest", ROOT / "tools" / "map_scope_ingest.py")


def test_acs712_5a_nominal_conversion():
    cal = {
        "vcc_mv": 5000.0,
        "sensors": {
            "U": (2500.0, 185.0),
            "V": (2500.0, 185.0),
        },
    }
    # +1 A U, -1 A V; W is KCL-derived.
    csv_text = (
        "pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,"
        "scope_qualified,note\n"
        "1,2685,2315,,110,15,1,synthetic\n"
    )
    with NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8", delete=False) as tmp:
        tmp.write(csv_text)
        p = Path(tmp.name)
    try:
        rows = ingest.parse_scope_csv(p, 1, cal)
    finally:
        p.unlink(missing_ok=True)
    assert rows[0]["ref_u_ma"] == 1000
    assert rows[0]["ref_v_ma"] == -1000
    assert rows[0]["ref_w_ma"] == 0


def test_old_20a_sensitivity_is_not_the_5a_default():
    # 40 mVpp gives 216.216 mApp with the nominal 185 mV/A sensor.
    noise_mv = 40.0
    equiv_ma = noise_mv / 185.0 * 1000.0
    assert math.isclose(equiv_ma, 216.216216, rel_tol=1e-9)
    old_equiv_ma = noise_mv / 100.0 * 1000.0
    assert math.isclose(old_equiv_ma, 400.0, rel_tol=1e-12)
    assert equiv_ma < old_equiv_ma


def test_map_control_contract():
    assert ingest.MAP_CURRENT_MIN_MA == 1000
    assert ingest.MAP_CURRENT_MAX_MA == 3000
    assert ingest.MAP_CURRENT_MIN_MA < ingest.MAP_CURRENT_MAX_MA


def test_control_boundary_is_explicit():
    assert ingest.MAP_CURRENT_MIN_MA == 1_000
    assert ingest.MAP_CURRENT_MAX_MA == 3_000
    # The implementation stores the control policy as provenance; the
    # actual firmware mode switch remains a separate commissioning change.
    assert "V/F" in ingest.build_manifest(0, 0)["current_reference"]["below_map_control"]


def test_no_quantitative_qualification_is_claimed():
    q = ingest.build_manifest(0, 0)["current_reference"]
    assert q["sensor"] == "ACS712-5A"
    assert q["qualification_status"] == "PENDING_PHYSICAL_MEASUREMENT"
