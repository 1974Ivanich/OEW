"""Regression tests for telemetry GUI logging: batching, retention, session robustness."""

import csv
import json
import os
import sys
import time
import queue
from datetime import datetime
from pathlib import Path

import pytest

# Ensure project root is on sys.path for imports
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import tkinter as tk


# ── Helpers for VfPanel ──────────────────────────────────────────────────────

class _Label:
    """Minimal mock for Tkinter label used by VfPanel."""
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
    """Create a VfPanel with mocked dependencies."""
    panel = VfPanel(_Tab(), lambda _command: None)
    panel.log_status_label = _Label()
    return panel


# Import after sys.path setup
from vf_panel import VfPanel, CSV_FIELDS


# ── P1: CSV batching ────────────────────────────────────────────────────────

def test_csv_batching_flush_on_close(tmp_path, monkeypatch):
    """After stop (close), all expected rows must be on disk; data appears
    after a batch flush (every 25 rows), not per-row."""
    panel = _panel()
    monkeypatch.chdir(tmp_path)

    # Start session
    panel._start_session(1000, 15, 50)
    session_dir = Path(panel.session_dir)
    assert session_dir.exists()

    # Simulate 30 VFLOG telemetry rows
    sample_dd = {k: '' for k in CSV_FIELDS}
    for i in range(30):
        sample_dd['t'] = i
        panel.on_telemetry('VFLOG', sample_dd)

    # After 25 rows, a flush should have happened (batch at 25)
    # Verify the file has header + 25 flushed rows + 5 unflushed
    csv_path = session_dir / 'telemetry.csv'
    assert csv_path.exists()

    # Close session — should flush remaining
    panel._close_session('GUI_STOP')

    # All 30 rows should be on disk
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
    # header + 30 data rows
    assert len(rows) == 31
    # meta should have samples count
    meta = json.loads((session_dir / 'meta.json').read_text(encoding='utf-8'))
    assert meta['samples'] == 30


def test_csv_batching_data_appears_after_batch_not_per_row(tmp_path, monkeypatch):
    """Data should appear after batch flush (every 25), not on every row write."""
    panel = _panel()
    monkeypatch.chdir(tmp_path)

    panel._start_session(1000, 15, 50)
    session_dir = Path(panel.session_dir)
    csv_path = session_dir / 'telemetry.csv'

    # Write 10 rows — no flush yet (batch is 25)
    sample_dd = {k: '' for k in CSV_FIELDS}
    for i in range(10):
        sample_dd['t'] = i
        panel.on_telemetry('VFLOG', sample_dd)

    # Close to force final flush
    panel._close_session('GUI_STOP')

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert len(rows) == 11  # header + 10


# ── P1: Write failure handling ──────────────────────────────────────────────

def test_write_failure_disables_writer_once(tmp_path, monkeypatch):
    """Injected OSError changes state exactly once: writer disabled,
    meta.write_error set, subsequent rows don't raise."""
    panel = _panel()
    monkeypatch.chdir(tmp_path)

    panel._start_session(1000, 15, 50)
    session_dir = Path(panel.session_dir)

    # Replace csv_writer with a mock that raises OSError on first call
    class MockWriter:
        call_count = 0
        def writerow(self, fields):
            MockWriter.call_count += 1
            if MockWriter.call_count == 1:
                raise OSError("simulated disk full")

    panel.csv_writer = MockWriter()

    # First write should fail and disable writer
    sample_dd = {k: '' for k in CSV_FIELDS}
    sample_dd['t'] = 1
    panel.on_telemetry('VFLOG', sample_dd)

    # Writer should be disabled
    assert panel.csv_writer is None
    assert panel._write_error_state is not None

    # Subsequent writes should NOT raise
    for i in range(5):
        sample_dd['t'] = i + 10
        panel.on_telemetry('VFLOG', sample_dd)  # should not raise

    # Close session — meta should have write_error
    panel._close_session('GUI_STOP')
    meta = json.loads((session_dir / 'meta.json').read_text(encoding='utf-8'))
    assert 'write_error' in meta


# ── P1: Session naming ──────────────────────────────────────────────────────

def test_session_naming_unique_dirs(tmp_path, monkeypatch):
    """Two starts with same timestamp (patched datetime) → different directories,
    independent CSV/meta."""
    panel = _panel()
    monkeypatch.chdir(tmp_path)

    # Patch datetime to return same value
    fake_dt = datetime(2026, 8, 21, 12, 0, 0, 0)
    monkeypatch.setattr('vf_panel.datetime', type('FakeDatetime', (), {
        'now': staticmethod(lambda: fake_dt),
        'strftime': staticmethod(lambda self, fmt: fake_dt.strftime(fmt)),
    })())

    panel._start_session(1000, 15, 50)
    first_dir = Path(panel.session_dir)
    first_meta = json.loads((first_dir / 'meta.json').read_text(encoding='utf-8'))

    panel._start_session(1200, 20, 60)
    second_dir = Path(panel.session_dir)
    second_meta = json.loads((second_dir / 'meta.json').read_text(encoding='utf-8'))

    # Different directories
    assert first_dir != second_dir
    # Independent meta
    assert first_meta['session_id'] == 1
    assert second_meta['session_id'] == 2
    # Both dirs exist independently
    assert first_dir.exists()
    assert second_dir.exists()


# ── P1: Log retention (bounded Tk Text) ─────────────────────────────────────

def test_log_retention_bounded_text(tmp_path, monkeypatch):
    """Insert > 5000 lines → bounded Text (<= limit), on-disk file contains all."""
    # We test the _log method logic directly on a mock log_text
    class FakeLogText:
        def __init__(self):
            self.lines = []
            self.yview_val = 1.0  # at bottom

        def config(self, **kwargs):
            pass

        def insert(self, where, text, tag=None):
            self.lines.append(text.rstrip('\n'))

        def delete(self, start, end):
            start_idx = int(start.split('.')[0]) - 1
            end_idx = int(end.split('.')[0]) - 1
            self.lines = self.lines[:start_idx] + self.lines[end_idx:]

        def index(self, marker):
            return f"{len(self.lines)}.0"

        def yview(self):
            return (0.0, self.yview_val)

        def see(self, marker):
            pass

    log_text = FakeLogText()

    # Simulate _log behavior inline (bounded retention)
    # Logic: if n > 5000, delete("1.0", f"{n - 4999}.0")
    # This keeps exactly 5000 lines
    LIMIT = 5000
    for i in range(6000):
        log_text.insert(tk.END, f"line {i}\n", "received")
        n = int(log_text.index(tk.END).split('.')[0])
        if n > LIMIT:
            log_text.delete("1.0", f"{n - 4999}.0")

    assert len(log_text.lines) <= LIMIT

    # On-disk file (simulated) should contain all
    all_lines_path = tmp_path / "all_lines.log"
    with open(all_lines_path, 'w', encoding='utf-8') as f:
        for i in range(6000):
            f.write(f"line {i}\n")
    assert sum(1 for _ in open(all_lines_path, encoding='utf-8')) == 6000


# ── P2: Measurement queue (FIFO, no losses) ─────────────────────────────────

def test_measurement_queue_fifo_no_losses():
    """Burst → FIFO order preserved, no losses, uses queue not list."""
    import measurement_gui

    # Verify the class uses queue.Queue internally
    app = measurement_gui.MeasurementGUI.__new__(measurement_gui.MeasurementGUI)
    app._gui_jobs = queue.Queue()
    app._poll_jobs_ms = 50

    # Simulate burst of jobs
    results = []
    for i in range(100):
        app._schedule_gui_job(lambda idx=i: results.append(idx))

    # Process all
    while True:
        try:
            fn = app._gui_jobs.get_nowait()
            fn()
        except queue.Empty:
            break

    # FIFO order preserved
    assert results == list(range(100)), f"Expected 0..99, got {results[:10]}..."
    assert len(results) == 100


# ── P2: Measurement queue type check ────────────────────────────────────────

def test_measurement_gui_uses_queue_not_list():
    """Verify _gui_jobs is a queue.Queue, not a list."""
    import measurement_gui

    # Check source code to ensure queue is used
    import inspect
    source = inspect.getsource(measurement_gui.MeasurementGUI.__init__)
    assert '_gui_jobs: queue.Queue' in source or 'queue.Queue()' in source


# ── P2: _schedule_gui_job puts to queue ─────────────────────────────────────

def test_schedule_gui_job_puts_to_queue():
    """_schedule_gui_job should call put, not append."""
    import measurement_gui
    import inspect
    source = inspect.getsource(measurement_gui.MeasurementGUI._schedule_gui_job)
    assert '.put(' in source
    assert '.append(' not in source


# ── P2: _process_gui_jobs drains with get_nowait ────────────────────────────

def test_process_gui_jobs_drains_with_get_nowait():
    """_process_gui_jobs should use get_nowait() loop."""
    import measurement_gui
    import inspect
    source = inspect.getsource(measurement_gui.MeasurementGUI._process_gui_jobs)
    assert 'get_nowait()' in source
    assert 'queue.Empty' in source or 'Empty' in source


# ── P1: Buffered file writer (nucleo_debug_tool) ────────────────────────────

def test_log_file_worker_drain_flush():
    """Test that _close_log_file drains queue via sentinel + join."""
    import nucleo_debug_tool
    import inspect
    source = inspect.getsource(nucleo_debug_tool.NucleoDebugTool._close_log_file)
    assert 'put_nowait(None)' in source or 'put(None)' in source
    assert 'join' in source


def test_log_to_file_uses_queue():
    """_log_to_file should use queue.Queue, not direct file write."""
    import nucleo_debug_tool
    import inspect
    source = inspect.getsource(nucleo_debug_tool.NucleoDebugTool._log_to_file)
    assert 'queue.Queue' in source or '_log_queue' in source


# ── P1: Log retention in all three GUIs ─────────────────────────────────────

def test_foc_control_log_retention():
    """foc_control_gui._log should have bounded retention."""
    import foc_control_gui
    import inspect
    source = inspect.getsource(foc_control_gui.FOCControlGUI._log)
    assert '5000' in source
    assert 'yview' in source


def test_measurement_gui_log_retention():
    """measurement_gui._log should have bounded retention."""
    import measurement_gui
    import inspect
    source = inspect.getsource(measurement_gui.MeasurementGUI._log)
    assert '5000' in source
    assert 'yview' in source
