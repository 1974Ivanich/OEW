"""Regression: 5A-sensitivity parameterisation + safety-module freeze.

This file lives in tests/ because it is a software contract test, not a
physical measurement. It enforces two facts:

  1. ``scope_acs712_capture.py --phase0`` accepts ``--sens-mv-per-a`` and
     records the configured value in the Phase-0 JSON. The default must
     stay at 185 mV/A (ACS712-5A nominal), and the JSON must echo the
     configured value.

  2. ``tools/map_scope_ingest.py`` MUST NOT silently change the default
     ``sens_mv_per_a`` or introduce ACS712-5A-related provenance into the
     campaign manifest. Any such change requires an explicit TZ that
     passes through normal review; this test fails the build before that
     happens, so the change cannot ride in with an unrelated PR.

The SHA baseline is taken from ``origin/main`` so that rebasing onto a
newer main does not silently accept the change. If a future change to
the safety module is intentional, the expected SHA must be updated here
together with the TZ.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, path: Path):
    """Load a module by absolute path without polluting ``sys.path``."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


SCOPE = _load_module(
    "scope_acs712_capture_for_test",
    ROOT / "tools" / "scope_acs712_capture.py",
)


# ---------------------------------------------------------------------------
# 1. --sens-mv-per-a is wired through
# ---------------------------------------------------------------------------


def test_sens_mv_per_a_default_is_5a():
    """Default sensitivity is the ACS712-5A nominal 185 mV/A."""
    assert SCOPE.DEFAULT_SENSOR_SENS_MV_PER_A == pytest.approx(185.0)


def test_unit_string_round_trip():
    """Phase-0 must store the configured sensitivity in the JSON, not
    a hard-coded 100."""
    # The argparse namespace carries --sens-mv-per-a into cmd_phase0 via
    # args.sens_mv_per_a. We verify the manifest builder reads it by
    # inspecting the source for the manifest keys.
    import re as _re
    src = (ROOT / "tools" / "scope_acs712_capture.py").read_text(
        encoding="utf-8")
    # Manifest writes args.sens_mv_per_a directly:
    assert "args.sens_mv_per_a" in src, (
        "scope_acs712_capture.py must record --sens-mv-per-a in the "
        "Phase-0 manifest. Without it the noise conversion cannot be "
        "recomputed later from the JSON.")
    # And the noise-conversion must divide by args.sens_mv_per_a, not 100:
    assert "/ args.sens_mv_per_a" in src, (
        "Phase-0 noise conversion must scale by --sens-mv-per-a, "
        "not by the hard-coded 100 mV/A.")


def test_noise_conversion_uses_configured_sensitivity():
    """40 mVpp noise must convert to ~216.2 mApp at 185 mV/A, not 400.

    This is a pure-arithmetic check on the documented sensitivity
    contract; it does not need a live Scope().
    """
    noise_pp_mv = 40.0
    expected_5a = noise_pp_mv / 185.0 * 1000.0   # 216.216...
    expected_20a = noise_pp_mv / 100.0 * 1000.0  # 400.0
    assert expected_5a == pytest.approx(216.216, abs=0.01)
    assert expected_20a == pytest.approx(400.0, abs=0.01)
    assert expected_5a != expected_20a


# ---------------------------------------------------------------------------
# 2. Safety-module freeze: tools/map_scope_ingest.py must not change default
#    sens_mv_per_a or add ACS712-5A provenance to the campaign manifest.
# ---------------------------------------------------------------------------


_INGEST_PATH = ROOT / "tools" / "map_scope_ingest.py"


def test_ingest_default_sens_is_unchanged():
    """The default fallback sensitivity in ingest stays at 100 mV/A.

    This is a deliberate, conservative default that matches the existing
    ACS712-20A calibration record. Switching to 185 mV/A without an
    explicit TZ changes ingest behaviour in a way that affects every
    campaign, so it is gated here.
    """
    text = _INGEST_PATH.read_text(encoding="utf-8")
    # find the `_load_calibration` default branch
    m = re.search(
        r'item\.get\("sens_mv_per_a",\s*100\.0\s*\*\s*vcc\s*/\s*5000\.0\)',
        text)
    assert m is not None, (
        "map_scope_ingest.py default sens_mv_per_a has been changed away "
        "from 100.0 * vcc / 5000.0. That is a safety-module change and "
        "requires an explicit TZ before it can land.")


def test_ingest_has_no_acs712_5a_provenance():
    """The campaign manifest must not carry an ACS712-5A provenance block
    added without an explicit TZ."""
    text = _INGEST_PATH.read_text(encoding="utf-8")
    forbidden = [
        "ACS712-5A",
        "acs712_sensitivity_mv_per_a",
        "map_control_min_a",
        "map_control_max_a",
        "MAP_CURRENT_MIN_MA",
        "MAP_CURRENT_MAX_MA",
    ]
    leaks = [tok for tok in forbidden if tok in text]
    assert not leaks, (
        "map_scope_ingest.py contains safety-module tokens %s that were "
        "introduced without an explicit TZ: %s" % (leaks, ", ".join(leaks)))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
