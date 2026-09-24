"""F4 regression: the Phase-0 acceptance noise floor is an ACS712-reference
quantity, not "the noisiest channel the scope happened to capture".

Contract locked here (TZ-REF-01 §10, decision of 2026-09-24):

    ACS712 quantitative reference:
        U + V            = mandatory, DISPLAY=ON required
        marker           = diagnostic only, excluded from the floor
        OFF mandatory    = FAIL (never silently dropped)
        OFF/other        = excluded
        noise floor      = max(U_pp, V_pp)

Before this change the tool took ``max()`` over *every* characterised channel,
so a marker sitting on a coarse V/div (2 V/div = 80 mV per code, 2 LSB of
quantisation read as "noise") could set the acceptance floor, and a
DISPLAY=OFF reference channel was simply averaged into the same statistics
instead of failing the run.

Runs offline: the ``Scope`` transport is replaced by a stub whose per-channel
ripple and DISPLAY state are set by the test.
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

MV_PER_CODE = 20.0        # 500 mV/div, 1X probe, zero offset
NAMES = ("CH1", "CH2", "CH3")
U_CH, V_CH, MARKER_CH = "CH1", "CH2", "CH3"


def _square(pp_codes, n=1520):
    """Deterministic waveform with an exact peak-to-peak of pp_codes codes."""
    lo = 128 - pp_codes // 2
    hi = lo + pp_codes
    half = n // 2
    return np.array([lo] * half + [hi] * (n - half), dtype=np.int64)


class _StubScope:
    """Per-channel pp (codes) and DISPLAY flags come from the class."""

    pp = {U_CH: 5, V_CH: 8, MARKER_CH: 100}
    display = {U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"}

    def __init__(self, *a, **kw):
        pass

    def identify(self):
        return "OWON,TAO3104A,STUB0000,V3.0.0"

    def head(self):
        return {
            "TIMEBASE": {"SCALE": "500us", "HOFFSET": 0},
            "SAMPLE": {"FULLSCREEN": 1520, "DATALEN": 1520,
                       "SAMPLERATE": "(1MSa/s)", "TYPE": "SAMPle", "DEPMEM": "10K"},
            "CHANNEL": [
                {"NAME": ch, "DISPLAY": self.display[ch], "COUPLING": "DC",
                 "PROBE": "1X", "SCALE": "500mV", "OFFSET": 0}
                for ch in NAMES
            ],
        }

    def waveform(self, ch):
        return _square(self.pp[str(ch)])

    def close(self):
        pass


def _run(monkeypatch, out_dir, display=None, pp=None):
    if display is not None:
        monkeypatch.setattr(_StubScope, "display", dict(display), raising=False)
    if pp is not None:
        monkeypatch.setattr(_StubScope, "pp", dict(pp), raising=False)
    monkeypatch.setattr(SCOPE, "Scope", _StubScope)
    monkeypatch.setattr(sys, "argv", [
        str(_TOOLS / "scope_acs712_capture.py"), "--phase0",
        "--out", str(out_dir), "--vcc-mv", "5110",
        "--u-ch", U_CH, "--v-ch", V_CH, "--sync-ch", MARKER_CH,
    ])
    return SCOPE.main()


def _json(out_dir):
    return json.loads((out_dir / "phase0_characterisation.json")
                      .read_text(encoding="utf-8"))


def test_floor_is_max_of_u_and_v_and_ignores_noisy_marker(monkeypatch, tmp_path):
    """100 mVpp (U) / 160 mVpp (V) / 2000 mVpp (marker, DISPLAY=OFF) -> 160 mVpp."""
    out_dir = tmp_path / "phase0"
    rc = _run(monkeypatch, out_dir,
              display={U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"},
              pp={U_CH: 5, V_CH: 8, MARKER_CH: 100})
    assert rc == 0
    doc = _json(out_dir)
    ch = doc["characterisation"]

    assert ch[U_CH]["noise_vpp_mv"] == 100.0
    assert ch[V_CH]["noise_vpp_mv"] == 160.0
    assert ch[MARKER_CH]["noise_vpp_mv"] == 2000.0      # diagnostic, kept

    assert doc["noise_floor_pp_mv"] == 160.0            # not 2000.0
    assert doc["noise_floor_pp_mv"] == max(ch[U_CH]["noise_vpp_mv"],
                                           ch[V_CH]["noise_vpp_mv"])
    assert doc["noise_floor_source_channels"] == [U_CH, V_CH]
    assert doc["noise_floor_excluded_channels"] == [MARKER_CH]
    assert doc["zero_noise_pp_ma"] == 1600.0
    assert doc["channel_display"] == {U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"}


def test_floor_does_not_depend_on_marker_noise(monkeypatch, tmp_path):
    """Same U/V, wildly different marker noise -> identical acceptance floor."""
    floors = []
    for i, marker_pp in enumerate((2, 100, 240)):
        out_dir = tmp_path / f"run{i}"
        rc = _run(monkeypatch, out_dir,
                  display={U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"},
                  pp={U_CH: 5, V_CH: 8, MARKER_CH: marker_pp})
        assert rc == 0
        floors.append(_json(out_dir)["noise_floor_pp_mv"])
    assert floors == [160.0, 160.0, 160.0]


def test_off_reference_channel_is_a_hard_fail(monkeypatch, tmp_path, capsys):
    """A missing mandatory ACS712 channel must FAIL, not be excluded silently."""
    for off_ch in (U_CH, V_CH):
        out_dir = tmp_path / f"off_{off_ch}"
        display = {U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"}
        display[off_ch] = "OFF"
        rc = _run(monkeypatch, out_dir, display=display)
        err = capsys.readouterr().err
        assert rc != 0, "DISPLAY=OFF reference channel must not pass"
        assert off_ch in err
        assert not (out_dir / "phase0_characterisation.json").exists(), \
            "no acceptance artifact may be produced by a failed run"


def test_budget_per_channel_cannot_inflate_floor(monkeypatch, tmp_path):
    """max() over per-channel zero_noise_ma must equal the U/V floor."""
    out_dir = tmp_path / "phase0"
    assert _run(monkeypatch, out_dir,
                display={U_CH: "ON", V_CH: "ON", MARKER_CH: "OFF"},
                pp={U_CH: 5, V_CH: 8, MARKER_CH: 100}) == 0
    doc = _json(out_dir)
    per_channel = [d["zero_noise_pp_ma"] for d in doc["characterisation"].values()]
    assert set(per_channel) == {doc["zero_noise_pp_ma"]} == {1600.0}
