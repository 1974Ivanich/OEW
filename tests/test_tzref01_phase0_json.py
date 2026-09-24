"""Regression lock for the ``--phase0`` CLI path (TZ-REF-01 §4 / §10).

Why this file exists
--------------------
Revision d92d4b4 shipped ``--phase0`` with a defect that every existing test
missed: the JSON write used ``out_path``, which was **never defined** (only
``out_dir`` was). Nothing in ``tests/`` or in ``tools/scope_acs712_selftest.py``
touched the ``--phase0`` code path at all, so a green ``make test`` / 28-of-28
selftest proved nothing about it. The defect was only found by driving the CLI
offline with a stubbed scope transport, where it produced::

    NameError: name 'out_path' is not defined     (tools/scope_acs712_capture.py:732)

and no JSON at all. The root cause was a regression in the commit that claimed
to fix the *write order* of the same JSON: it moved the write after the
``zero_noise_*`` computation and dropped the line defining ``out_path``.

What is locked down here
------------------------
  * ``--phase0`` exits 0 and actually creates the JSON artifact;
  * the artifact lands in the directory given by ``--out`` (not in CWD, not in
    a default path) — ``--out`` is the documented operator contract;
  * the artifact carries the fields TZ-REF-01 §10 requires of the tool:
    ``noise_vpp_mv``, ``noise_vrms_mv``, ``zero_noise_pp_ma``,
    ``zero_noise_rms_ma`` per channel, plus ``vcc_acs712_mv`` at top level;
  * ``zero_noise_pp_ma`` / ``zero_noise_rms_ma`` are the *worst channel* of the
    batch and match ``noise_*_mv / sens_mv_per_a * 1000`` (100 mV/A nominal
    ACS712-20A), i.e. the mA conversion is applied exactly once.

Runs WITHOUT hardware and WITHOUT pyusb: the ``Scope`` transport is replaced by
a stub, exactly as the offline reproduction of the defect was done.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import scope_acs712_capture as SCOPE   # noqa: E402

SENS_MV_PER_A = 100.0        # ACS712-20A nominal sensitivity, mV/A
VCC_MV = 5000.0              # operator-measured ACS712 supply, mV
MV_PER_CODE = 20.0           # 500 mV/div, 1X probe, zero offset -> 20 mV/code

# Per-channel ripple amplitude in codes. Deliberately different per channel so
# the "worst channel sets the budget" rule is observable.
RIPPLE_CODES = {"CH1": 2.0, "CH2": 5.0, "CH3": 3.0}

_HEAD = {
    "TIMEBASE": {"SCALE": "500us", "HOFFSET": 0},
    "SAMPLE": {"FULLSCREEN": 1520, "SLOWMOVE": -1, "DATALEN": 1520,
               "SAMPLERATE": "(1MSa/s)", "TYPE": "SAMPle", "DEPMEM": "10K"},
    "CHANNEL": [
        {"NAME": ch, "DISPLAY": "ON", "COUPLING": "DC", "PROBE": "1X",
         "SCALE": "500mV", "OFFSET": 0, "FREQUENCE": 0.0}
        for ch in ("CH1", "CH2", "CH3")
    ],
}


class _StubScope:
    """Offline stand-in for the pyusb transport (no device required)."""

    def __init__(self, *a, **kw):
        self.closed = False

    def identify(self):
        return "OWON,TAO3104A,STUB0000,V3.0.0"

    def head(self):
        return _HEAD

    def waveform(self, ch):
        amp = RIPPLE_CODES[str(ch)]
        i = np.arange(int(_HEAD["SAMPLE"]["DATALEN"]), dtype=float)
        return np.rint(128.0 + amp * np.sin(i / 7.0))

    def close(self):
        self.closed = True


def _run_phase0(monkeypatch, out_dir, extra_argv=()):
    """Drive the real CLI entry point; return its exit code."""
    monkeypatch.setattr(SCOPE, "Scope", _StubScope)
    argv = [str(_TOOLS / "scope_acs712_capture.py"), "--phase0",
            "--out", str(out_dir), "--vcc-mv", str(VCC_MV)]
    argv.extend(extra_argv)
    monkeypatch.setattr(sys, "argv", argv)
    return SCOPE.main()


def test_phase0_writes_json_into_out_dir(monkeypatch, tmp_path):
    """The blocker: --phase0 must exit 0 AND leave the artifact behind."""
    out_dir = tmp_path / "phase0"
    rc = _run_phase0(monkeypatch, out_dir)

    assert rc == 0, "phase0 must report success"
    artifact = out_dir / "phase0_characterisation.json"
    assert artifact.is_file(), "phase0 wrote no JSON (out_path regression?)"
    assert json.loads(artifact.read_text(encoding="utf-8"))


def test_out_flag_controls_artifact_directory(monkeypatch, tmp_path):
    """--out is the operator contract: two runs, two directories, no CWD leak."""
    first, second = tmp_path / "a", tmp_path / "b"
    for d in (first, second):
        assert _run_phase0(monkeypatch, d) == 0

    name = "phase0_characterisation.json"
    assert (first / name).is_file() and (second / name).is_file()
    assert not (Path.cwd() / name).exists(), "artifact leaked into the CWD"
    assert not (Path.cwd() / ".tzref01_phase0").exists(), "default dir used"


def test_phase0_json_has_fields_required_by_tz_section_10(monkeypatch, tmp_path):
    """§10 mandatory fields, per channel + top level."""
    out_dir = tmp_path / "phase0"
    assert _run_phase0(monkeypatch, out_dir) == 0
    doc = json.loads((out_dir / "phase0_characterisation.json")
                     .read_text(encoding="utf-8"))

    assert doc["vcc_acs712_mv"] == VCC_MV
    chans = doc["characterisation"]
    assert set(chans) == {"CH1", "CH2", "CH3"}

    for ch, entry in chans.items():
        for field in ("noise_vpp_mv", "noise_vrms_mv",
                      "zero_noise_pp_ma", "zero_noise_rms_ma"):
            assert field in entry, "%s missing %s" % (ch, field)
        assert entry["noise_vpp_mv"] > 0.0
        assert entry["noise_vrms_mv"] > 0.0


def test_zero_noise_ma_is_worst_channel_and_converted_once(monkeypatch, tmp_path):
    """mA conversion applied exactly once, worst channel sets the budget."""
    out_dir = tmp_path / "phase0"
    assert _run_phase0(monkeypatch, out_dir) == 0
    doc = json.loads((out_dir / "phase0_characterisation.json")
                     .read_text(encoding="utf-8"))
    chans = doc["characterisation"]

    expected_pp = max(c["noise_vpp_mv"] for c in chans.values()) \
        / SENS_MV_PER_A * 1000.0
    expected_rms = max(c["noise_vrms_mv"] for c in chans.values()) \
        / SENS_MV_PER_A * 1000.0
    for entry in chans.values():
        assert entry["zero_noise_pp_ma"] == round(expected_pp, 3)
        assert entry["zero_noise_rms_ma"] == round(expected_rms, 3)

    # The noisiest stub channel is CH2 (5 codes of ripple); at 20 mV/code that
    # is 200 mVpp -> 2000 mApp. A double-applied gain would show 20x that.
    assert chans["CH2"]["noise_vpp_mv"] == pytest.approx(
        2.0 * RIPPLE_CODES["CH2"] * MV_PER_CODE, abs=MV_PER_CODE)
    assert chans["CH2"]["zero_noise_pp_ma"] < 5000.0
