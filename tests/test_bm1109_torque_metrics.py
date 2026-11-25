"""Исполняемые метрики V0.4: c_n(h), T_min(h), h_max, валидация, provenance."""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bm1109_doe1 as doe  # noqa: E402
import bm1109_torque_metrics as tm  # noqa: E402

EPS_W = 7.2e-6      # psi_delta_scale = 1.440e-3 Вб (P2-N1 ROM) * 1e-3 * 5 А
A6 = 5e-3           # T_peak = 90 мН·м
C6_FROZEN = 0.004107264756438633     # c_6(0.5°), доля


@pytest.mark.implementation
def test_c6_equals_closed_form():
    x = 6 * tm.P * math.radians(0.5)
    assert tm.c_n(0.5) == pytest.approx(1.0 - math.sin(x) / x, abs=1e-15)
    assert tm.c_n(0.5) == pytest.approx(C6_FROZEN, abs=1e-12)


@pytest.mark.physics
def test_c6_matches_analytic_central_difference_of_cogging_term():
    """Множитель n*p подтверждается численно: FD/exact = 1 - c_n(h)."""
    a6, h, th = 1.0, 0.5, 0.37
    k = 6 * tm.P
    hr = math.radians(h)
    fd = (a6 * math.cos(k * (th + hr)) - a6 * math.cos(k * (th - hr))) / (2 * hr)
    exact = -k * a6 * math.sin(k * th)
    assert fd / exact == pytest.approx(1.0 - tm.c_n(h), abs=1e-14)


@pytest.mark.implementation
def test_t_min_at_frozen_step():
    assert tm.t_min(0.5, EPS_W) == pytest.approx(0.1400129, abs=5e-7)


@pytest.mark.implementation
def test_applicability_reference_minimum():
    info = tm.applicability(EPS_W)
    assert info["t_min_frozen_Nm"] == pytest.approx(0.1400129, abs=5e-7)
    assert info["h_tmin_opt_deg"] == pytest.approx(0.450534, abs=1e-4)
    assert info["t_min_opt_Nm"] == pytest.approx(0.1373928, abs=5e-7)
    assert info["h_max_deg"] == pytest.approx(0.780871, abs=1e-6)
    assert info["c_frozen"] == pytest.approx(C6_FROZEN, abs=1e-12)
    # справочный минимум не может требовать большей амплитуды, чем замороженная точка
    assert info["t_min_opt_Nm"] < info["t_min_frozen_Nm"]


@pytest.mark.implementation
def test_h_max_is_one_percent_root_and_area_is_empty_past_it():
    h = tm.h_max_c1()
    assert tm.c_n(h) == pytest.approx(0.01, abs=1e-9)
    assert tm.t_min(h + 1e-6, EPS_W) == float("inf")
    assert tm.t_min(0.78, EPS_W) > tm.t_min(0.5, EPS_W)


@pytest.mark.implementation
def test_units_fractions_vs_newton_metres():
    model = doe.Synthetic(pole_pairs=tm.P, a_cog=A6, phi_cog=tm.PHI)
    r = tm.torque_metrics(model, EPS_W, 0.5, A6)
    assert r["e_trunc"] == pytest.approx(0.0041072648, abs=1e-8)   # доля
    assert r["e_corner"] == pytest.approx(0.0091673, abs=1e-6)
    assert r["worst_case"] == pytest.approx(r["e_trunc"] + r["e_corner"], abs=1e-12)
    assert tm.verdict(r) == "FAIL"
    assert tm.t_min(0.5, EPS_W) == pytest.approx(0.14, abs=5e-3)    # Н·м, не доля


@pytest.mark.implementation
@pytest.mark.parametrize("bad_h", [0.0, -0.5, 10.0, 180.0])
def test_c_n_rejects_out_of_range_step(bad_h):
    with pytest.raises(ValueError):
        tm.c_n(bad_h)
    with pytest.raises(ValueError):
        tm.t_min(bad_h, EPS_W)


@pytest.mark.implementation
@pytest.mark.parametrize("bad_eps", [0.0, -1e-6, float("nan"), float("inf")])
def test_t_min_rejects_invalid_budget(bad_eps):
    with pytest.raises(ValueError):
        tm.t_min(0.5, bad_eps)
    with pytest.raises(ValueError):
        tm.applicability(bad_eps)


@pytest.mark.implementation
def test_public_entries_reject_non_synthetic_model():
    for call in (lambda: tm.scan_h_minimum(object(), EPS_W, A6),
                 lambda: tm.refine_minimum(object(), EPS_W, A6, 0.5),
                 lambda: tm.torque_metrics(object(), EPS_W, 0.5, A6),
                 lambda: tm.worst_case_at(object(), EPS_W, A6, 0.5)):
        with pytest.raises(TypeError):
            call()


@pytest.mark.implementation
def test_public_functions_keep_their_docstrings():
    for fn in (tm.torque_metrics, tm.scan_h_minimum, tm.refine_minimum,
               tm.worst_case_at, tm.c_n, tm.t_min, tm.h_max_c1, tm.applicability):
        assert (fn.__doc__ or "").strip(), fn.__name__
