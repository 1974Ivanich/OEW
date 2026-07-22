#!/usr/bin/env python3
"""
Measurement GUI for OEW Motor Phase Resistance Measurement
Communicates with STM32G474RE firmware via UART (USART2, 115200 baud).
Replaces CLI terminal (PuTTY/TeraTerm) with a native Tkinter window.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import sys
import re

# Try to import serial — show helpful message if missing
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print(f"ERROR: pyserial is not installed.")
    print(f"Python: {sys.executable}")
    print(f"Python path: {sys.path}")
    print(f"Install it: {sys.executable} -m pip install pyserial")
    sys.exit(1)


# ── Regex for parsing machine-readable measurement lines ──────────────
MEAS_RE = re.compile(
    r"^@MEAS:(?P<phase>[UVW]):"
    r"Vbus=(?P<vbus>\d+):"
    r"Uwnd=(?P<uwnd>\d+):"
    r"I=(?P<current>-?\d+):"
    r"R=(?P<res_ohms>\d+)\.(?P<res_mohm>\d{3})$"
)


class MeasurementGUI:
    """Main application window."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OEW Motor — Phase Resistance Measurement")
        self.root.geometry("820x600")
        self.root.minsize(640, 480)

        # Serial connection (None = disconnected)
        self.ser: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.running = False

        # Queue for thread-safe GUI updates
        self._gui_jobs: list = []
        self._poll_jobs_ms = 50

        self._build_ui()
        self._scan_ports()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self._poll_jobs_ms, self._process_gui_jobs)

    # ── UI Construction ───────────────────────────────────────────────

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
        self.connect_btn.pack(side=tk.LEFT, padx=(10, 2))

        self.status_lbl = ttk.Label(top, text="Disconnected", foreground="gray")
        self.status_lbl.pack(side=tk.LEFT, padx=10)

        # ── Middle: measurement buttons ──
        mid = ttk.Frame(self.root, padding=4)
        mid.pack(fill=tk.X)

        btn_font = ("Segoe UI", 12, "bold")
        self.meas_u_btn = ttk.Button(mid, text="Phase U", command=lambda: self._send_cmd("1"), width=12)
        self.meas_u_btn.pack(side=tk.LEFT, padx=4, pady=4)
        self.meas_v_btn = ttk.Button(mid, text="Phase V", command=lambda: self._send_cmd("2"), width=12)
        self.meas_v_btn.pack(side=tk.LEFT, padx=4, pady=4)
        self.meas_w_btn = ttk.Button(mid, text="Phase W", command=lambda: self._send_cmd("3"), width=12)
        self.meas_w_btn.pack(side=tk.LEFT, padx=4, pady=4)

        self.cycle_btn = ttk.Button(mid, text="Cycle (U→V→W)", command=lambda: self._send_cmd("Q"), width=16)
        self.cycle_btn.pack(side=tk.LEFT, padx=(20, 4), pady=4)

        self.clear_btn = ttk.Button(mid, text="Clear Log", command=self._clear_log, width=10)
        self.clear_btn.pack(side=tk.RIGHT, padx=4, pady=4)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4)

        # ── Results display ──
        display_frame = ttk.Frame(self.root, padding=4)
        display_frame.pack(fill=tk.BOTH, expand=True)

        # Left: last measurement card
        card = ttk.LabelFrame(display_frame, text="Last Measurement", padding=8)
        card.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))

        self.phase_lbl = ttk.Label(card, text="—", font=("Segoe UI", 28, "bold"))
        self.phase_lbl.pack(pady=(0, 8))

        meas_font = ("Segoe UI", 10)
        self.vbus_lbl = ttk.Label(card, text="Vbus: —", font=meas_font)
        self.vbus_lbl.pack(anchor=tk.W)
        self.uwnd_lbl = ttk.Label(card, text="Uwnd: —", font=meas_font)
        self.uwnd_lbl.pack(anchor=tk.W)
        self.current_lbl = ttk.Label(card, text="Current: —", font=meas_font)
        self.current_lbl.pack(anchor=tk.W)
        self.resistance_lbl = ttk.Label(card, text="Resistance: —", font=meas_font)
        self.resistance_lbl.pack(anchor=tk.W)

        # Right: log
        log_frame = ttk.LabelFrame(display_frame, text="Communication Log", padding=4)
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scroll.set)

        # Color tags for log
        self.log_text.tag_config("sent", foreground="#0066cc")
        self.log_text.tag_config("received", foreground="#009900")
        self.log_text.tag_config("error", foreground="#cc0000", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("meas", foreground="#660099", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("info", foreground="#888888")

        # Disable buttons initially
        self._set_meas_buttons_state(False)

    def _set_meas_buttons_state(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.meas_u_btn.config(state=state)
        self.meas_v_btn.config(state=state)
        self.meas_w_btn.config(state=state)
        self.cycle_btn.config(state=state)

    # ── COM port scanning ─────────────────────────────────────────────

    def _scan_ports(self):
        ports = serial.tools.list_ports.comports()
        items = [f"{p.device} — {p.description}" for p in sorted(ports)]
        self.port_combo["values"] = items
        if items:
            self.port_combo.current(0)
        else:
            self.port_combo.set("")

    # ── Connection management ─────────────────────────────────────────

    def _toggle_connect(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        raw = self.port_combo.get()
        if not raw:
            messagebox.showwarning("No Port", "No COM port selected. Click Scan first.")
            return
        port_name = raw.split(" —")[0]

        try:
            self.ser = serial.Serial(
                port=port_name,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
            )
        except serial.SerialException as e:
            messagebox.showerror("Connection Error", f"Cannot open {port_name}:\n{e}")
            self.ser = None
            return

        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self.running = True

        self.connect_btn.config(text="Disconnect")
        self.status_lbl.config(text=f"Connected to {port_name}", foreground="green")
        self._set_meas_buttons_state(True)

        self._log("info", f"Connected to {port_name} @ 115200 baud\n")

        # Start reader thread
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def _disconnect(self):
        self.running = False
        if self.reader_thread:
            self.reader_thread.join(timeout=1)
            self.reader_thread = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        self.connect_btn.config(text="Connect")
        self.status_lbl.config(text="Disconnected", foreground="gray")
        self._set_meas_buttons_state(False)
        self._log("info", "Disconnected\n")

    def _on_close(self):
        self._disconnect()
        self.root.destroy()

    # ── Sending commands ──────────────────────────────────────────────

    def _send_cmd(self, cmd: str):
        if not self.ser or not self.ser.is_open:
            return
        try:
            label = {"1": "Measure U", "2": "Measure V", "3": "Measure W", "Q": "Cycle (U→V→W)"}
            self._log("sent", f"> {label.get(cmd, cmd)}\n")
            self.ser.write(cmd.encode())
        except serial.SerialException as e:
            self._log("error", f"Write error: {e}\n")
            self._disconnect()

    # ── Background reader thread ──────────────────────────────────────

    def _reader_loop(self):
        buf = ""
        while self.running and self.ser and self.ser.is_open:
            try:
                data = self.ser.read(256)
                if data:
                    buf += data.decode("utf-8", errors="replace")
                    # Process line by line
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
        """Process a single received line (called from reader thread)."""
        # Check for machine-readable measurement
        m = MEAS_RE.match(line)
        if m:
            data = m.groupdict()
            # Schedule GUI update in main thread
            self._schedule_gui_job(lambda d=data: self._update_measurement(d))
            self._schedule_gui_job(lambda l=line: self._log("meas", f"  {l}\n"))
            return

        # Check for errors
        if "ERROR" in line or "error" in line:
            self._schedule_gui_job(lambda l=line: self._log("error", f"  {l}\n"))
            return

        # Normal line — log as received
        self._schedule_gui_job(lambda l=line: self._log("received", f"  {l}\n"))

    def _schedule_gui_job(self, fn):
        self._gui_jobs.append(fn)

    def _process_gui_jobs(self):
        while self._gui_jobs:
            fn = self._gui_jobs.pop(0)
            try:
                fn()
            except Exception as e:
                print(f"GUI job error: {e}", file=sys.stderr)
        self.root.after(self._poll_jobs_ms, self._process_gui_jobs)

    # ── Measurement display update ────────────────────────────────────

    def _update_measurement(self, data: dict):
        phase = data["phase"]
        vbus = data["vbus"]
        uwnd = data["uwnd"]
        cur = data["current"]
        res_ohms = data["res_ohms"]
        res_mohm = data["res_mohm"]

        self.phase_lbl.config(text=f"Phase {phase}")

        color_map = {"U": "#e74c3c", "V": "#27ae60", "W": "#2980b9"}
        self.phase_lbl.config(foreground=color_map.get(phase, "black"))

        self.vbus_lbl.config(text=f"Vbus: {vbus} mV")
        self.uwnd_lbl.config(text=f"Uwnd: {uwnd} mV")
        self.current_lbl.config(text=f"Current: {cur} mA")
        self.resistance_lbl.config(text=f"Resistance: {res_ohms}.{res_mohm} Ohm")

    def _log(self, tag: str, text: str):
        self.log_text.insert(tk.END, text, tag)
        self.log_text.see(tk.END)

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)
        # Reset last measurement display
        self.phase_lbl.config(text="—")
        self.vbus_lbl.config(text="Vbus: —")
        self.uwnd_lbl.config(text="Uwnd: —")
        self.current_lbl.config(text="Current: —")
        self.resistance_lbl.config(text="Resistance: —")

    # ── Run ───────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = MeasurementGUI()
    app.run()
