"""Regression contracts for GUI P0/P1 fixes.

The tests avoid creating Tk windows; they exercise only serial framing, parser,
queue, state and capture ownership contracts through lightweight fakes.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time

from foc_control_gui import FOCControlGUI, TELEMETRY_RE
from measurement_gui import MeasurementGUI
from nucleo_debug_tool import PWMTab, SaleaeHelper

ROOT = Path(__file__).resolve().parents[1]


class _Serial:
    is_open = True

    def __init__(self):
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)


class _Widget:
    def __init__(self):
        self.calls = []

    def config(self, **kwargs):
        self.calls.append(kwargs)


class _Root:
    def __init__(self):
        self.after_calls = []

    def after(self, delay_ms, callback):
        self.after_calls.append((delay_ms, callback))


class _Var:
    def __init__(self, value):
        self.value = value
        self.reads = 0

    def get(self):
        self.reads += 1
        return self.value


def test_measurement_command_uses_uart_line_terminator():
    gui = object.__new__(MeasurementGUI)
    gui.ser = _Serial()
    gui._log = lambda *_args, **_kwargs: None
    gui._disconnect = lambda: None

    MeasurementGUI._send_cmd(gui, "1")

    assert gui.ser.writes == [b"1\r\n"]


def _foc_format_fields() -> tuple[str, list]:
    """Формат строки @FOC и имена её полей — из прошивки, не из литералов теста.

    Литерал в тесте и регексп в GUI «съезжают» вместе: именно так панель
    телеметрии GUI перестала разбирать строку, которую шлёт main.c, — образец в
    тесте был написан под старый формат, регексп под него же, и оба оставались
    зелёными, пока формат в src/telemetry_format.h менялся. Поэтому и образец, и
    ожидаемый набор полей берутся из единственного источника истины формата.
    """
    text = (ROOT / "src" / "telemetry_format.h").read_text(encoding="utf-8")
    body = text.split("FOC_TELEMETRY_FMT", 1)[1].split("#endif", 1)[0]
    fmt = "".join(re.findall(r'"([^"]*)"', body))
    return fmt, re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=", fmt)


def _sample_foc_line(fmt: str) -> str:
    """Собрать строку ровно по формату прошивки: подставить значения по типу."""
    fixed = {"t": "123456789", "run_id": "M0-R1", "map_crc32": "0A1B2C3D"}
    out = []
    for segment in fmt.rstrip("\r\n").split(":"):
        name, sep, spec = segment.partition("=")
        if not sep or "%" not in spec:
            out.append(segment)          # литеральное значение (map_id=M0)
        elif name in fixed:
            out.append(f"{name}={fixed[name]}")
        elif name == "RUN":
            out.append("RUN=1")          # регексп требует ровно 0 или 1
        elif spec.endswith("u"):
            out.append(f"{name}=1")
        else:
            out.append(f"{name}=-7")     # знак должен переживать разбор
    return ":".join(out)


def _foc_line(**overrides) -> str:
    """Строка @FOC по формату прошивки с подменёнными полями (RUN, STATE, …)."""
    fmt, _fields = _foc_format_fields()
    out = []
    for segment in _sample_foc_line(fmt).split(":"):
        name, sep, _value = segment.partition("=")
        if sep and name.upper() in overrides:
            out.append(f"{name}={overrides[name.upper()]}")
        else:
            out.append(segment)
    return ":".join(out)


def test_foc_regex_matches_production_telemetry_and_ires_field():
    fmt, fields = _foc_format_fields()
    line = _sample_foc_line(fmt)

    match = TELEMETRY_RE.fullmatch(line)

    assert match is not None, (
        "регексп GUI не разбирает строку, которую формирует FOC_TELEMETRY_FMT "
        f"(src/telemetry_format.h):\n{line}")
    # Имена групп в GUI короткие (SPD -> speed, TH -> theta), поэтому сверяется
    # ПОКРЫТИЕ: каждая группа обязана соответствовать полю формата, и наоборот.
    renamed = {"SPD": "speed", "TH": "theta", "FAULT_R": "fault_reason"}
    expected = {renamed.get(name, name.lower()) for name in fields}
    assert set(match.groupdict()) == expected, (
        "набор групп регекспа и полей формата прошивки разошлись: "
        f"только в регекспе {sorted(set(match.groupdict()) - expected)}, "
        f"только в формате {sorted(expected - set(match.groupdict()))}")
    assert match.group("i1") == "-7"
    assert match.group("i2") == "-7"
    assert match.group("ires") == "-7"
    assert match.group("run") == "1"
    assert match.group("slip_hz") == "-7"
    assert match.group("obs") == "-7"


def test_foc_state_changes_only_after_actual_telemetry_confirmation():
    gui = object.__new__(FOCControlGUI)
    gui.ser = _Serial()
    gui.foc_active = False
    gui._log = lambda *_args, **_kwargs: None
    gui._scan_btn_state = lambda: None
    gui._schedule_gui_job = lambda callback: callback()
    gui.lbl_i1 = _Widget()
    gui.lbl_i2 = _Widget()
    gui.lbl_in = _Widget()
    gui.lbl_vbus = _Widget()
    gui.lbl_status = _Widget()
    gui.lbl_slip = _Widget()

    FOCControlGUI._start_foc(gui)
    assert gui.foc_active is False

    FOCControlGUI._on_line(gui, _foc_line(RUN=1, STATE=3))
    assert gui.foc_active is True

    FOCControlGUI._on_line(
        gui, _foc_line(RUN=0, STATE=0, FAULT=1, FAULT_R=7))
    assert gui.foc_active is False


def test_foc_gui_job_queue_executes_job_added_during_drain():
    gui = object.__new__(FOCControlGUI)
    gui._gui_jobs = FOCControlGUI._new_gui_job_queue()
    gui.root = _Root()
    gui._poll_jobs_ms = 50
    events = []

    def first_job():
        events.append("first")
        gui._schedule_gui_job(lambda: events.append("late"))

    gui._schedule_gui_job(first_job)
    FOCControlGUI._process_gui_jobs(gui)

    assert events == ["first", "late"]
    assert len(gui.root.after_calls) == 1


def test_pwm_snapshot_reads_tk_variables_before_worker_starts():
    tab = object.__new__(PWMTab)
    tab.ch_vars = [_Var(True), _Var(False)]
    tab.ch_vars2 = [_Var(False)]
    tab.CHANNELS = [("U", "U", 0x01, 0), ("V", "V", 0x04, 1)]
    tab.CHANNELS_INV2 = [("W", "W", 0x02, 2)]
    tab.arr_var = _Var(99)
    tab.duty_var = _Var(15)
    tab.dt_var = _Var(1500)
    tab.winfo_toplevel = lambda: type("Root", (), {"tclk": 10_000_000})()

    snapshot = PWMTab._snapshot_auto_test_config(tab, 1)

    assert snapshot["mask"] == 0x03
    assert snapshot["arr"] == 99
    assert snapshot["duty"] == 15
    assert snapshot["deadtime_ns"] == 1500
    reads_at_snapshot = sum(var.reads for var in [*tab.ch_vars, *tab.ch_vars2, tab.arr_var, tab.duty_var, tab.dt_var])
    assert reads_at_snapshot > 0

    class _Saleae:
        @staticmethod
        def measure_freq(_capture, _channel):
            return 50_000.0

        @staticmethod
        def measure_duty(_capture, _channel):
            return 0.50

    tab.saleae = _Saleae()
    tab.winfo_toplevel = lambda: (_ for _ in ()).throw(AssertionError("Tk access in worker"))
    PWMTab._evaluate_auto_test(tab, 1, snapshot, object())


def test_saleae_capture_paths_are_unique_and_released(tmp_path, monkeypatch):
    helper = SaleaeHelper()
    helper.available = True
    helper._tmp_dir = str(tmp_path / "captures")

    def fake_run(command, **_kwargs):
        output_path = Path(command[command.index("-o") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("0,1\n", encoding="utf-8")
        return None

    monkeypatch.setattr("nucleo_debug_tool.subprocess.run", fake_run)

    first = helper.capture_sync(digital_chs=[0], duration_s=0.001)
    second = helper.capture_sync(digital_chs=[1], duration_s=0.001)

    assert first is not None and second is not None
    assert first.csv_path != second.csv_path
    assert Path(first.csv_path).exists()
    assert Path(second.csv_path).exists()

    helper.release_capture(first)
    assert not Path(first.csv_path).parent.exists()
    assert Path(second.csv_path).exists()

    helper.release_capture(second)
    assert not Path(second.csv_path).parent.exists()


def test_foc_start_fail_rc_is_signed_no_false_success_and_repair():
    # @FOC:START:FAIL:rc=-6 must NOT be mistaken for success, must preserve
    # the signed -6, and must not block a later successful RUN.
    gui = object.__new__(FOCControlGUI)
    gui.ser = _Serial()
    gui.foc_active = False
    gui._schedule_gui_job = lambda callback: callback()
    gui._scan_btn_state = lambda: None
    gui.lbl_i1 = _Widget()
    gui.lbl_i2 = _Widget()
    gui.lbl_in = _Widget()
    gui.lbl_vbus = _Widget()
    gui.lbl_status = _Widget()
    gui.lbl_slip = _Widget()
    log = []
    gui._log = lambda tag, text: log.append((tag, text))

    fail_line = (
        "@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified "
        "-3=calib -4=arm -5=pwm_enable -6=params_out_of_range)"
    )

    FOCControlGUI._on_line(gui, fail_line)
    assert gui.foc_active is False              # no false success
    assert any("-6" in text for _tag, text in log)   # signed -6 preserved

    # repair: a subsequent successful RUN must set active (no stale-error block)
    run_line = _foc_line(RUN=1, STATE=3)
    FOCControlGUI._on_line(gui, run_line)
    assert gui.foc_active is True               # repair path


def test_saleae_capture_lock_serializes_concurrent_cli_invocations(tmp_path, monkeypatch):
    helper = SaleaeHelper()
    helper.available = True
    helper._tmp_dir = str(tmp_path / "captures")
    active = 0
    maximum = 0
    guard = threading.Lock()

    def fake_run(command, **_kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        output_path = Path(command[command.index("-o") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("0,1\n", encoding="utf-8")
        with guard:
            active -= 1
        return None

    monkeypatch.setattr("nucleo_debug_tool.subprocess.run", fake_run)
    captures = []

    def capture(channel):
        captures.append(helper.capture_sync(digital_chs=[channel], duration_s=0.001))

    threads = [threading.Thread(target=capture, args=(channel,)) for channel in (0, 1)]
    for worker in threads:
        worker.start()
    for worker in threads:
        worker.join()

    assert maximum == 1
    assert all(capture is not None for capture in captures)
    for capture in captures:
        helper.release_capture(capture)


def test_pole_pairs_agree_across_firmware_gui_and_json():
    """P1: the layers spelled "pole pairs" with four different numbers.

    The spinbox is labelled pairs but held 6 - the pole count from the nameplate
    ("6 poles"), not pairs. p enters the electrical angle as a multiplier, so 6
    where the firmware uses 3 doubles theta_e and the rotor looks like it lost
    sync. p is a nameplate fact, not something to measure, so every layer must
    carry the same 3.

    "Same number" is not enough while a layer can still pass 0 through: a comma
    default (``get('p', 3)``) only covers a missing key, so a literal 0 - what
    the firmware reported before main.c mirrored FOC's default after
    Autotune_Init - travels on as 0. The CLI ignores a7=0 (``if (a7 > 0)``),
    but echoes it back in @MP:OK, so the hole survives into the next JSON.
    Hence the fallback must be keyed on the value, not on the key's presence.
    """
    import json
    import re

    root = Path(__file__).resolve().parents[1]
    firmware = (root / "src" / "foc.c").read_text(encoding="utf-8")
    fw_pp = int(re.search(r"#define FOC_DEFAULT_POLE_PAIRS\s+(\d+)", firmware).group(1))

    gui = (root / "foc_control_gui.py").read_text(encoding="utf-8")
    tool = (root / "nucleo_debug_tool.py").read_text(encoding="utf-8")

    spin = int(re.search(r"pole_pairs_var = tk\.IntVar\(value=(\d+)\)", gui).group(1))

    # mp= from the GUI, mp= from the autotune tab, and the JSON writer.
    fallbacks = re.findall(r"p\.get\('(?:pole_pairs|p)'\)\s*or\s*(\d+)", gui + tool)
    assert len(fallbacks) >= 3, "a layer lost its pole-pairs fallback"
    assert set(fallbacks) == {str(fw_pp)}
    # and nothing may go back to a key-only default that lets a 0 through
    assert not re.search(r"p\.get\('(?:pole_pairs|p)',\s*\d+\)", gui + tool)

    params = json.loads((root / "autotune_params.json").read_text(encoding="utf-8"))

    assert fw_pp == 3, "nameplate says 6 poles = 3 pairs"
    assert spin == fw_pp, "spinbox is labelled pairs, must hold pairs"
    # 0 means "not read" - for a nameplate value that is a hole, not a value
    assert params["pole_pairs"] == fw_pp
