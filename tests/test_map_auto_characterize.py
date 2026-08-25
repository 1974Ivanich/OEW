from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from map_auto_characterize import _build_samples, _check_manifest, _load_reference  # noqa: E402


def manifest() -> dict:
    return {
        "format_version": 1,
        "campaign_id": "real-test",
        "board_revision": 7,
        "pwm_frequency_hz": 5000,
        "timer_arr": 999,
        "adc_trigger_id": 0x4F455731,
        "trigger_offset_ticks": 12,
        "deadtime_ticks": 68,
        "adc_clock_hz": 170000000,
        "adc_sample_cycles_x2": 257,
        "adc_resolution": 0,
        "adc_config_signature": 0x1234,
        "current_calibration_signature": 0x5678,
        "characterization_id": 1,
        "dataset_crc32": 2,
        "tool_build_id": 3,
        "qualification_revision": 1,
        "solver_revision": 4,
        "certifier_revision": 2,
        "phase_a": 0,
        "phase_b": 1,
        "startup": {"sector": 0, "window": 0, "hold_cycles": 1, "mu": 0, "mv": 0, "mw": 0},
        "qualifications": {
            "accumulator": {"min_samples": 2, "mad_limit_ma": 100, "kcl_limit_ma": 50, "min_margin_ticks": 2},
            "solver": {
                "min_samples": 2, "holdout_samples": 1, "residual_rms_limit_ma": 100,
                "residual_max_limit_ma": 200, "bias_limit_ma": 100,
                "holdout_rms_limit_ma": 100, "kcl_rms_limit_ma": 50,
                "max_condition_ratio": 100000, "min_abs_determinant": 1,
                "min_abs_diagonal": 1,
            },
            "region": {"min_valid_cells": 1, "guard_q15": 10, "min_margin_ticks": 2},
        },
    }


def raw(seq: int) -> dict:
    return {
        "cap": 1, "seq": seq, "raw_i1": 2040, "raw_i2": 2068,
        "raw_ct": 2048, "raw_vbus": 3000,
        "i1": 10 + seq, "i2": 20 + seq, "vbus": 50000,
        "ccr1": [500, 450, 550], "ccr8": [500, 450, 550],
        "arr": 999, "trig": 0x4F455731, "status": 0, "fault": 0,
        "sector": 0, "window": 0,
    }


def test_reference_requires_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "probe.csv"
    path.write_text("seq,phase_u_ma,phase_v_ma,phase_w_ma,timestamp_cycles\n1,10,-5,-5,100\n", encoding="utf-8")
    refs = _load_reference(path)
    assert refs[1]["phase_u_ma"] == 10


def test_build_samples_uses_external_reference(tmp_path: Path) -> None:
    m = manifest()
    _check_manifest(m)
    refs = {
        1: {"phase_u_ma": 100, "phase_v_ma": -50, "phase_w_ma": -50, "timestamp_cycles": 1000}
    }
    samples = _build_samples([raw(1)], refs, m)
    assert samples[0]["ref_u_ma"] == 100
    assert samples[0]["ref_v_ma"] == -50
    assert samples[0]["ref_w_ma"] == -50
    assert samples[0]["idc1_ma"] != samples[0]["ref_u_ma"]


def test_missing_external_reference_is_rejected() -> None:
    m = manifest()
    _check_manifest(m)
    try:
        _build_samples([raw(1)], {}, m)
    except ValueError as exc:
        assert "no external reference" in str(exc)
    else:
        raise AssertionError("missing external reference was accepted")


def test_kcl_error_is_rejected() -> None:
    m = manifest()
    refs = {
        1: {"phase_u_ma": 100, "phase_v_ma": -50, "phase_w_ma": 100, "timestamp_cycles": 1000}
    }
    try:
        _build_samples([raw(1)], refs, m)
    except ValueError as exc:
        assert "KCL error" in str(exc)
    else:
        raise AssertionError("invalid KCL reference was accepted")
