"""Self-test for tools/sigrok_lib.py using the demo driver (no hardware)."""

from pathlib import Path

import pytest

from tools.sigrok_lib import (
    SigrokError,
    SigrokSession,
    capture_fixed,
    find_sigrok_cli,
    scan_devices,
)


def _has_demo() -> bool:
    try:
        return any(d.driver == "demo" for d in scan_devices())
    except Exception:  # pragma: no cover - external tool missing
        return False


@pytest.mark.skipif(not _has_demo(), reason="sigrok-cli or demo driver not available")
class TestSigrokLibDemo:
    def test_capture_and_parse(self, tmp_path: Path) -> None:
        out = tmp_path / "demo_capture.sr"
        result = capture_fixed(
            out,
            duration_s=0.1,
            channels=["D0", "D1", "D2"],
            rate_hz=1_000_000,
            driver="demo",
        )
        assert result.output_path.exists()
        assert result.samples_captured > 0

        session = result.session
        assert session.samplerate == 1_000_000
        assert session.duration > 0.09
        assert session.unitsize == 1
        assert "D0" in session.channel_to_bit
        assert "D1" in session.channel_to_bit
        assert "D2" in session.channel_to_bit

        for ch in ["D0", "D1", "D2"]:
            trans = session.channel_transitions(ch)
            assert isinstance(trans, list)

    def test_first_edge_not_found(self, tmp_path: Path) -> None:
        out = tmp_path / "demo_capture.sr"
        result = capture_fixed(
            out,
            duration_s=0.05,
            channels=["D0"],
            rate_hz=100_000,
            driver="demo",
        )
        session = result.session
        edge = session.first_edge("D0", edge="falling")
        # Demo driver may or may not produce an edge; the API must not crash.
        assert edge is None or (isinstance(edge, tuple) and len(edge) == 2)


class TestSigrokSessionLoad:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises((FileNotFoundError, SigrokError)):
            SigrokSession.load(tmp_path / "nonexistent.sr")
