import json
import subprocess
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "map_geometry_interpolation_check.py"


def run_case(tmp_path, edge_delta):
    center = [
        {"idc1": 100, "idc2": 0, "refa": 200, "refb": -100},
        {"idc1": 0, "idc2": 100, "refa": 100, "refb": 300},
        {"idc1": 80, "idc2": 40, "refa": 200, "refb": 40},
        {"idc1": -60, "idc2": 90, "refa": -30, "refb": 210},
    ]
    edge = [
        {"idc1": 150, "idc2": -20, "refa": 280 + edge_delta, "refb": -210},
        {"idc1": -120, "idc2": 130, "refa": -110, "refb": 270 + edge_delta},
    ]
    payload = {"residual_rms_limit_ma": 1.0, "rows": [{
        "sector": 0, "window": 0,
        "center_samples": center,
        "edge_samples": edge,
    }]}
    path = tmp_path / "case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(path)],
                          capture_output=True, text=True)


def test_interpolation_passes_for_linear_model(tmp_path):
    result = run_case(tmp_path, 0)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS" in result.stdout


def test_interpolation_rejects_excess_edge_error(tmp_path):
    result = run_case(tmp_path, 10)
    assert result.returncode == 1, result.stderr + result.stdout
    assert "FAIL" in result.stdout
