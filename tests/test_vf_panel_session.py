"""Regression tests for V/f session identity and asynchronous sigrok snapshots."""

import json
from pathlib import Path

from vf_panel import VfPanel


class _Label:
    def __init__(self):
        self.calls = []

    def config(self, **kwargs):
        self.calls.append(kwargs)


class _Tab:
    CHANNELS = []
    CHANNELS_INV2 = []

    def __init__(self):
        self.callbacks = []

    def after(self, _delay_ms, callback):
        self.callbacks.append(callback)


def _panel():
    panel = VfPanel(_Tab(), lambda _command: None)
    panel.log_status_label = _Label()
    return panel


def _write_meta(directory: Path, session_id: int):
    directory.mkdir()
    (directory / "meta.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8"
    )


def _read_meta(directory: Path):
    return json.loads((directory / "meta.json").read_text(encoding="utf-8"))


def test_stale_capture_callback_cannot_update_new_session(tmp_path):
    panel = _panel()
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _write_meta(old_dir, 1)
    _write_meta(new_dir, 2)

    panel._active_session_id = 2
    panel.session_dir = str(new_dir)
    panel.trigger_tick_ms = 1234

    panel._capture_sync_ready(1, str(old_dir), 987654)

    assert panel.trigger_edge_ns is None
    assert "sync" not in _read_meta(old_dir)
    assert "sync" not in _read_meta(new_dir)


def test_current_capture_callback_writes_only_matching_session(tmp_path):
    panel = _panel()
    session_dir = tmp_path / "current"
    _write_meta(session_dir, 7)

    panel._active_session_id = 7
    panel.session_dir = str(session_dir)
    panel.trigger_tick_ms = 4321

    panel._capture_sync_ready(7, str(session_dir), 24680)

    meta = _read_meta(session_dir)
    assert panel.trigger_edge_ns == 24680
    assert meta["session_id"] == 7
    assert meta["sync"]["trigger_tick_ms"] == 4321
    assert meta["sync"]["trigger_edge_ns"] == 24680


def test_meta_identity_mismatch_rejects_even_matching_live_fields(tmp_path):
    panel = _panel()
    session_dir = tmp_path / "reused-directory"
    _write_meta(session_dir, 4)

    panel._active_session_id = 8
    panel.session_dir = str(session_dir)
    panel.trigger_tick_ms = 99

    panel._capture_sync_ready(8, str(session_dir), 77)

    meta = _read_meta(session_dir)
    assert panel.trigger_edge_ns == 77
    assert meta == {"session_id": 4}


def test_worker_callback_captures_session_values_before_gui_dispatch(tmp_path):
    class _Capture:
        def __init__(self, csv_path):
            self.csv_path = str(csv_path)

    class _Saleae:
        def __init__(self, capture):
            self.capture = capture

        def capture_sync(self, *, ready_event, **_kwargs):
            ready_event.set()
            return self.capture

        def get_transitions(self, _capture, _channel):
            return [(0, 0), (13579, 1)]

    panel = _panel()
    old_dir = tmp_path / "worker-old"
    new_dir = tmp_path / "worker-new"
    _write_meta(old_dir, 10)
    _write_meta(new_dir, 11)
    source_csv = tmp_path / "source.csv"
    source_csv.write_text("sample", encoding="utf-8")
    panel.saleae = _Saleae(_Capture(source_csv))
    panel._active_session_id = 10
    panel.session_dir = str(old_dir)

    import threading

    ready_event = threading.Event()
    panel._capture_sigrok(10, str(old_dir), ready_event)
    assert ready_event.is_set()
    assert len(panel.tab.callbacks) == 1
    assert (old_dir / "digital.csv").read_text(encoding="utf-8") == "sample"

    panel._active_session_id = 11
    panel.session_dir = str(new_dir)
    panel.trigger_tick_ms = 55
    panel.tab.callbacks.pop()()

    assert panel.trigger_edge_ns is None
    assert "sync" not in _read_meta(old_dir)
    assert "sync" not in _read_meta(new_dir)


def test_current_capture_before_trigger_is_finalized_when_trigger_arrives(tmp_path):
    panel = _panel()
    session_dir = tmp_path / "capture-before-trigger"
    _write_meta(session_dir, 12)

    panel._active_session_id = 12
    panel.session_dir = str(session_dir)

    panel._capture_sync_ready(12, str(session_dir), 54321)
    assert "sync" not in _read_meta(session_dir)

    panel.on_telemetry("TRIG", {"tick": 678})

    meta = _read_meta(session_dir)
    assert meta["sync"]["trigger_tick_ms"] == 678
    assert meta["sync"]["trigger_edge_ns"] == 54321


def test_start_session_assigns_monotonic_identity_to_metadata(tmp_path, monkeypatch):
    panel = _panel()
    monkeypatch.chdir(tmp_path)

    panel._start_session(1000, 15, 50)
    first_dir = Path(panel.session_dir)
    first_meta = _read_meta(first_dir)

    panel._start_session(1200, 20, 60)
    second_meta = _read_meta(Path(panel.session_dir))

    assert first_meta["session_id"] == 1
    assert second_meta["session_id"] == 2
    assert panel._active_session_id == 2
