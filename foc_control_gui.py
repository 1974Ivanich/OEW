#!/usr/bin/env python
"""
OEW Motor FOC Control GUI
Управление асинхронным двигателем в схеме Open-End Winding.
Команды через UART (115200).
"""

import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import queue
import sys
import re
import os

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("=" * 60)
    print("  ERROR: pyserial not installed.")
    print("  Install: pip install pyserial")
    print("=" * 60)
    sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────────
BAUD = 115200
TELEMETRY_RE = re.compile(
    r"^@FOC:I1=(?P<i1>-?\d+):I2=(?P<i2>-?\d+):Ires=(?P<ires>-?\d+):"
    r"VBUS=(?P<vbus>-?\d+):STATE=(?P<state>\d+):SPD=(?P<speed>-?\d+):"
    r"TH=(?P<theta>-?\d+):FAULT=(?P<fault>-?\d+):FAULT_R=(?P<fault_reason>-?\d+):"
    r"FAIL=(?P<fail>-?\d+):RUN=(?P<run>[01])$"
)
MEAS_RE = re.compile(r"@MEAS:[UWV]:Vbus=(\d+):Uwnd=(\d+):I=(-?\d+):R=([\d.]+)")

# ── Main Application ──────────────────────────────────────────────────────────

class FOCControlGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OEW Motor FOC Control")
        self.root.geometry("800x600")
        self.root.minsize(640, 480)

        self.ser: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.running = False
        self.foc_active = False
        self._motor_params = {}
        self._gui_jobs = self._new_gui_job_queue()
        self._poll_jobs_ms = 50

        # Telemetry data
        self.tlm_i1 = 0
        self.tlm_i2 = 0
        self.tlm_in = 0
        self.tlm_vbus = 0
        self.tlm_speed = 0
        self.tlm_theta = 0

        self._build_ui()
        self._scan_ports()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self._poll_jobs_ms, self._process_gui_jobs)

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top frame: COM port controls ──
        top = ttk.Frame(self.root, padding=4)
        top.pack(fill=tk.X)

        ttk.Label(top, text="COM Port:").pack(side=tk.LEFT, padx=2)
        self.port_combo = ttk.Combobox(top, width=14, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=2)
        self.scan_btn = ttk.Button(top, text="Scan", command=self._scan_ports, width=6)
        self.scan_btn.pack(side=tk.LEFT, padx=2)

        self.connect_btn = ttk.Button(top, text="Connect", command=self._toggle_connect, width=10)
        self.connect_btn.pack(side=tk.LEFT, padx=4)

        # ── Control frame ──
        ctrl = ttk.LabelFrame(self.root, text="FOC Control", padding=8)
        ctrl.pack(fill=tk.X, padx=6, pady=4)

        # Speed control
        speed_frame = ttk.Frame(ctrl)
        speed_frame.pack(fill=tk.X, pady=2)
        ttk.Label(speed_frame, text="Speed (RPM):", width=12).pack(side=tk.LEFT)
        self.speed_var = tk.IntVar(value=500)
        self.speed_spin = ttk.Spinbox(speed_frame, from_=-3000, to=3000, increment=100,
                                       textvariable=self.speed_var, width=8)
        self.speed_spin.pack(side=tk.LEFT, padx=4)
        ttk.Button(speed_frame, text="Set", command=self._set_speed, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(speed_frame, text="Stop", command=self._stop, width=5).pack(side=tk.LEFT, padx=2)

        # Pole pairs control (только при остановленном FOC, диапазон 1..24)
        pp_frame = ttk.Frame(ctrl)
        pp_frame.pack(fill=tk.X, pady=2)
        ttk.Label(pp_frame, text="Pole pairs:", width=12).pack(side=tk.LEFT)
        self.pole_pairs_var = tk.IntVar(value=6)  # FOC_DEFAULT_POLE_PAIRS (firmware)
        self.pole_pairs_spin = ttk.Spinbox(pp_frame, from_=1, to=24, increment=1,
                                           textvariable=self.pole_pairs_var, width=8)
        self.pole_pairs_spin.pack(side=tk.LEFT, padx=4)
        self.pole_pairs_btn = ttk.Button(pp_frame, text="Set", command=self._set_pole_pairs, width=5)
        self.pole_pairs_btn.pack(side=tk.LEFT, padx=2)

        # ── Motor params from autotune (tz_foc_params) ───────────────
        mp_frame = ttk.LabelFrame(ctrl, text="Motor Params (autotuned)", padding=4)
        mp_frame.pack(fill=tk.X, pady=4)
        self.mp_lbl = ttk.Label(mp_frame, text="No params loaded",
                                font=("Consolas", 9), foreground="#666")
        self.mp_lbl.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        ttk.Button(mp_frame, text="Load JSON", width=9,
                   command=self._load_json).pack(side=tk.LEFT, padx=2)
        self.mp_apply_btn = ttk.Button(mp_frame, text="Apply", width=6,
                   command=self._apply_json, state=tk.DISABLED)
        self.mp_apply_btn.pack(side=tk.LEFT, padx=2)

        # Start / Stop buttons
        btn_frame = ttk.Frame(ctrl)
        btn_frame.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(btn_frame, text="▶  START FOC", command=self._start_foc, width=20)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(btn_frame, text="■  STOP FOC", command=self._stop_foc, width=20, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        # ── Telemetry display ──
        tlm = ttk.LabelFrame(self.root, text="Telemetry", padding=8)
        tlm.pack(fill=tk.X, padx=6, pady=4)

        grid = ttk.Frame(tlm)
        grid.pack(fill=tk.X)

        ttk.Label(grid, text="I1 (mA):", font=("Consolas", 10)).grid(row=0, column=0, sticky=tk.W, padx=4)
        self.lbl_i1 = ttk.Label(grid, text="0", font=("Consolas", 12, "bold"), foreground="#0066cc")
        self.lbl_i1.grid(row=0, column=1, sticky=tk.W, padx=4)

        ttk.Label(grid, text="I2 (mA):", font=("Consolas", 10)).grid(row=0, column=2, sticky=tk.W, padx=4)
        self.lbl_i2 = ttk.Label(grid, text="0", font=("Consolas", 12, "bold"), foreground="#0066cc")
        self.lbl_i2.grid(row=0, column=3, sticky=tk.W, padx=4)

        ttk.Label(grid, text="Vbus (V):", font=("Consolas", 10)).grid(row=0, column=4, sticky=tk.W, padx=4)
        self.lbl_vbus = ttk.Label(grid, text="0.0", font=("Consolas", 12, "bold"), foreground="#cc6600")
        self.lbl_vbus.grid(row=0, column=5, sticky=tk.W, padx=4)

        ttk.Label(grid, text="IN (mA):", font=("Consolas", 10)).grid(row=0, column=6, sticky=tk.W, padx=4)
        self.lbl_in = ttk.Label(grid, text="0", font=("Consolas", 12, "bold"), foreground="#0066cc")
        self.lbl_in.grid(row=0, column=7, sticky=tk.W, padx=4)

        ttk.Label(grid, text="Speed:", font=("Consolas", 10)).grid(row=1, column=0, sticky=tk.W, padx=4)
        self.lbl_speed = ttk.Label(grid, text="0 RPM", font=("Consolas", 12, "bold"), foreground="#009900")
        self.lbl_speed.grid(row=1, column=1, sticky=tk.W, padx=4)

        ttk.Label(grid, text="Status:", font=("Consolas", 10)).grid(row=1, column=2, sticky=tk.W, padx=4)
        self.lbl_status = ttk.Label(grid, text="STOPPED", font=("Consolas", 12, "bold"), foreground="#ff0000")
        self.lbl_status.grid(row=1, column=3, sticky=tk.W, padx=4, columnspan=2)

        # ── Log ──
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # Color tags
        self.log_text.tag_configure("sent", foreground="#888888")
        self.log_text.tag_configure("received", foreground="#000000")
        self.log_text.tag_configure("meas", foreground="#006600")
        self.log_text.tag_configure("error", foreground="#ff0000", font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("tlm", foreground="#0033aa")

    # ── Serial port ──────────────────────────────────────────────────────

    def _scan_ports(self):
        ports = serial.tools.list_ports.comports()
        items = [f"{p.device} - {p.description}" for p in ports]
        if not items:
            items = ["No ports found"]
        self.port_combo["values"] = items
        if items:
            self.port_combo.current(0)

    def _get_port_name(self) -> str | None:
        val = self.port_combo.get()
        if not val or val == "No ports found":
            return None
        return val.split(" - ")[0]

    def _toggle_connect(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self._get_port_name()
        if not port:
            self._log("error", "No COM port selected\n")
            return
        try:
            self.ser = serial.Serial(port, BAUD, timeout=0.1)
            self.running = True
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self.reader_thread.start()
            self.connect_btn.config(text="Disconnect")
            self._log("received", f"Connected to {port}\n")
            self._scan_btn_state()
        except serial.SerialException as e:
            messagebox.showerror("Connection Error", str(e))
            self._log("error", f"Connection failed: {e}\n")

    def _disconnect(self):
        self.running = False
        if self.reader_thread:
            self.reader_thread.join(timeout=1)
        if self.ser and self.ser.is_open:
            port = self.ser.port
            self.ser.close()
            self._log("received", f"Disconnected from {port}\n")
        self.ser = None
        self.connect_btn.config(text="Connect")
        self._scan_btn_state()

    def _on_close(self):
        self._disconnect()
        self.root.destroy()

    @staticmethod
    def _new_gui_job_queue():
        """Создать явную потокобезопасную очередь GUI callbacks."""
        return queue.Queue()

    # ── Send commands ────────────────────────────────────────────────────

    def _send(self, cmd: str):
        if not self.ser or not self.ser.is_open:
            return
        try:
            # Прошивка (UART_ReadLine) исполняет команду только по терминатору '\n'
            if not cmd.endswith("\n"):
                cmd += "\n"
            self.ser.write(cmd.encode())
        except serial.SerialException:
            self._log("error", "Write error, disconnecting\n")
            self._disconnect()

    def _start_foc(self):
        self._send("1")
        self._log("sent", "START FOC requested; waiting for RUN telemetry\n")

    def _stop_foc(self):
        self._send("0")
        self._log("sent", "STOP FOC requested; waiting for RUN telemetry\n")

    def _set_speed(self):
        speed = self.speed_var.get()
        self._send(f"s={speed}")
        self._log("sent", f"SET SPEED {speed} RPM\n")

    def _stop(self):
        self._send("s=0")
        self._log("sent", "SET SPEED 0 RPM\n")

    def _load_json(self):
        """Прочитать autotune_params.json (из nucleo_debug_tool.py)."""
        path = os.path.join(os.getcwd(), "autotune_params.json")
        if not os.path.exists(path):
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")],
                                             title="Select autotune_params.json")
            if not path:
                self._log("error", "autotune_params.json not found\n")
                return
        try:
            with open(path, encoding="utf-8") as f:
                self._motor_params = json.load(f)
            p = self._motor_params
            self.mp_lbl.config(
                text=f"Rs={p.get('Rs_mOhm')}m\u03a9  Ls={p.get('Ls_uH')}\u00b5H  "
                     f"Rr={p.get('Rr_mOhm')}  p={p.get('pole_pairs')}  "
                     f"Kp={p.get('Kp')} Ki={p.get('Ki')}",
                foreground="#006600")
            self.mp_apply_btn.config(state=tk.NORMAL)
            self._log("received", f"Loaded motor params: {os.path.basename(path)}\n")
        except Exception as e:
            self._log("error", f"JSON load error: {e}\n")

    def _apply_json(self):
        """Отправить mp=... и piapply в прошивку."""
        if not self._motor_params:
            self._log("error", "Load JSON first\n")
            return
        if self.foc_active:
            self._log("error", "Stop FOC before applying params\n")
            return
        p = self._motor_params
        self._send(f"mp={p.get('Rs_mOhm',0)},{p.get('Ls_uH',0)},"
                   f"{p.get('Rr_mOhm',0)},{p.get('Lm_uH',0)},"
                   f"{p.get('Tr_us',0)},{p.get('Ke_mV_rpm',0)},"
                   f"{p.get('pole_pairs',4)},{p.get('J_kg_m2_x1e6',0)}")
        self._log("sent", "Applied motor params (mp=...)\n")
        if p.get('Kp', 0) > 0 or p.get('Ki', 0) > 0:
            self._send("piapply")
            self._log("sent", "Applied PI gains (piapply)\n")

    def _set_pole_pairs(self):
        if self.foc_active:
            self._log("error", "Stop FOC before changing pole pairs\n")
            return
        try:
            pp = int(self.pole_pairs_var.get())
        except (tk.TclError, ValueError):
            self._log("error", "Invalid pole pairs value\n")
            return
        if not 1 <= pp <= 24:
            self._log("error", "Pole pairs must be 1..24\n")
            return
        self._send(f"pp={pp}")
        self._log("sent", f"SET POLE PAIRS {pp}\n")

    # ── UI state ─────────────────────────────────────────────────────────

    def _scan_btn_state(self):
        connected = self.ser is not None and self.ser.is_open
        state = tk.NORMAL if connected else tk.DISABLED
        self.start_btn.config(state=state)
        self.stop_btn.config(state=state)
        self.speed_spin.config(state=state)
        # Пары полюсов можно менять только при остановленном FOC
        pp_state = tk.NORMAL if (connected and not self.foc_active) else tk.DISABLED
        self.pole_pairs_spin.config(state=pp_state)
        self.pole_pairs_btn.config(state=pp_state)

    # ── Background reader ────────────────────────────────────────────────

    def _reader_loop(self):
        buf = ""
        while self.running and self.ser and self.ser.is_open:
            try:
                data = self.ser.read(512)
                if data:
                    buf += data.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip("\r")
                        if line:
                            self._on_line(line)
                else:
                    time.sleep(0.01)
            except serial.SerialException:
                if self.running:
                    self._schedule_gui_job(self._disconnect)
                    self._schedule_gui_job(lambda: self._log("error", "Connection lost!\n"))
                break
            except Exception as e:
                self._schedule_gui_job(lambda e=e: self._log("error", f"Read error: {e}\n"))
                break

    def _on_line(self, line: str):
        # Check telemetry
        if line.startswith("@MP:OK"):
            self._schedule_gui_job(lambda l=line: self._log("meas", f"  {l}\n"))
            return
        if line.startswith("@MP:ERROR") or line.startswith("@PI:ERROR"):
            self._schedule_gui_job(lambda l=line: self._log("error", f"  {l}\n"))
            return
        if line.startswith("@PI:APPLIED"):
            self._schedule_gui_job(lambda l=line: self._log("meas", f"  {l}\n"))
            return
        m = TELEMETRY_RE.fullmatch(line)
        if m:
            telemetry = {name: int(value) for name, value in m.groupdict().items()}
            self._schedule_gui_job(lambda data=telemetry: self._update_telemetry(data))
            self._schedule_gui_job(lambda l=line: self._log("tlm", f"  {l}\n"))
            return

        # Check measurement
        m = MEAS_RE.match(line)
        if m:
            self._schedule_gui_job(lambda l=line: self._log("meas", f"  {l}\n"))
            return

        # Errors
        if "ERROR" in line or "error" in line:
            self._schedule_gui_job(lambda l=line: self._log("error", f"  {l}\n"))
            return

        # Normal
        self._schedule_gui_job(lambda l=line: self._log("received", f"  {l}\n"))

    def _update_telemetry(self, telemetry):
        self.tlm_i1 = telemetry["i1"]
        self.tlm_i2 = telemetry["i2"]
        self.tlm_in = telemetry["ires"]
        self.tlm_vbus = telemetry["vbus"]
        self.tlm_speed = telemetry["speed"]
        self.tlm_theta = telemetry["theta"]
        self.foc_active = bool(telemetry["run"])

        self.lbl_i1.config(text=str(self.tlm_i1))
        self.lbl_i2.config(text=str(self.tlm_i2))
        vbus_v = self.tlm_vbus / 1000.0
        self.lbl_in.config(text=str(self.tlm_in))
        self.lbl_vbus.config(text=f"{vbus_v:.1f}")
        status_text = "RUNNING" if self.foc_active else "STOPPED"
        status_color = "#009900" if self.foc_active else "#ff0000"
        self.lbl_status.config(text=status_text, foreground=status_color)
        self._scan_btn_state()

    # ── GUI job queue ────────────────────────────────────────────────────

    def _schedule_gui_job(self, fn):
        self._gui_jobs.put(fn)

    def _process_gui_jobs(self):
        while True:
            try:
                fn = self._gui_jobs.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                print(f"GUI job error: {e}", file=sys.stderr)
        self.root.after(self._poll_jobs_ms, self._process_gui_jobs)

    # ── Log ──────────────────────────────────────────────────────────────

    def _log(self, tag: str, text: str):
        try:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, text + "\n", tag)
            # bounded retention
            try:
                n = int(self.log_text.index(tk.END).split('.')[0])
                if n > 5000:
                    self.log_text.delete("1.0", f"{n - 5000}.0")
            except (ValueError, tk.TclError):
                pass
            try:
                if float(self.log_text.yview()[1]) >= 0.999:
                    self.log_text.see(tk.END)
            except (ValueError, tk.TclError):
                pass
            self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"[LOG ERROR] {text} (error={e})")

    def run(self):
        self.root.mainloop()


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = FOCControlGUI()
    app.run()
