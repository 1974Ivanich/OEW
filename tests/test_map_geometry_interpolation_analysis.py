from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import map_geometry_interpolation_analysis as analysis  # noqa: E402

DEMO = TOOLS / "campaign_demo"


def sample(ccr1: int, ccr2: int, ccr3: int) -> dict:
    return {"ccr1": ccr1, "ccr2": ccr2, "ccr3": ccr3, "arr": 5000}


def test_modulation_metric_uses_max_absolute_phase():
    assert analysis.modulation_q15(sample(2500, 2500, 2500)) == 0
    assert analysis.modulation_q15(sample(3250, 2500, 1750)) > 0


def test_window_boundary_10000_belongs_to_window_one():
    # A synthetic row is not used for PASS; the predicate is checked by the
    # observable deterministic boundary in analyze_row.
    row = [
        {**sample(5000 + 10000 * 5001 // 32768, 5000, 5000 - 10000 * 5001 // 32768),
         "idc1_ma": 1, "idc2_ma": 2, "ref_u_ma": 3, "ref_v_ma": 4,
         "ref_w_ma": -7},
    ]
    result = analysis.analyze_row(row, 0, 1, 0, 1000, 2000)
    assert result["status"] == "FAIL"
    assert "window" in result["reason"]


def test_campaign_demo_is_not_accepted_as_geometry_evidence():
    result = analysis.analyze_campaign(DEMO)
    assert result["status"] in {"FAIL", "BLOCKED"}
    assert result["failed_rows"] or result["blocked_rows"]


def test_report_is_json_serializable():
    result = analysis.analyze_campaign(DEMO)
    json.dumps(result)
