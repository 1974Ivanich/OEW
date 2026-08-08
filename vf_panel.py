#!/usr/bin/env python3
"""V/f Control panel for nucleo_debug_tool.py.

Integration in PWMTab.__init__ (after _build_saleae_panel):
    self._build_vf_panel()

Telemetry in _on_line (add after FOC handler):
    if p == "VF": self.tab_pwm.vf_panel.on_telemetry(p, dd)
    elif p == "ENC": self.tab_pwm.vf_panel.on_telemetry(p, dd)
"""

import tkinter as tk
from tkinter import ttk


class VfPanel:
    def __init__(self, parent, send_fn):
        self.send = send_fn

    def build(self, parent_frame):
        frm = ttk.LabelFrame(parent_frame, text="V/f Control (AS5048A, closed-loop)")
        frm.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)

        ttk.Label(frm, text="Target RPM:").grid(row=0, column=0, sticky="e", padx=2, pady=2)
        self.vf_target_var = tk.IntVar(value=1500)
        ttk.Spinbox(frm, from_=-5000, to=5000, increment=100, width=8,
                    textvariable=self.vf_target_var).grid(row=0, column=1, padx=2)

        ttk.Label(frm, text="V/f Boost (%):").grid(row=0, column=2, sticky="e", padx=2)
        self.vf_boost_var = tk.IntVar(value=15)
        sp_boost = ttk.Spinbox(frm, from_=0, to=30, width=5,
                               textvariable=self.vf_boost_var,
                               command=self._vf_params_changed)
        sp_boost.grid(row=0, column=3, padx=2)
        sp_boost.bind("<FocusOut>", self._vf_params_changed)

        ttk.Label(frm, text="Rated Freq (Hz):").grid(row=0, column=4, sticky="e", padx=2)
        self.vf_rated_var = tk.IntVar(value=50)
        sp_rated = ttk.Spinbox(frm, from_=10, to=400, width=5,
                               textvariable=self.vf_rated_var,
                               command=self._vf_params_changed)
        sp_rated.grid(row=0, column=5, padx=2)
        sp_rated.bind("<FocusOut>", self._vf_params_changed)

        ttk.Button(frm, text="Start V/f", command=self._vf_start)\
            .grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=4)
        ttk.Button(frm, text="Stop V/f", command=self._vf_stop)\
            .grid(row=1, column=2, columnspan=2, sticky="ew", padx=2, pady=4)
        ttk.Button(frm, text="Encoder", command=lambda: self.send("enc"))\
            .grid(row=1, column=4, columnspan=2, sticky="ew", padx=2, pady=4)

        self.vf_status_label = ttk.Label(frm, text="V/f: stopped", anchor="w")
        self.vf_status_label.grid(row=2, column=0, columnspan=6, sticky="ew", padx=2)
        self.enc_angle_label = ttk.Label(frm, text="Angle: ---", anchor="w")
        self.enc_angle_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=2)
        self.enc_speed_label = ttk.Label(frm, text="Speed: ---", anchor="w")
        self.enc_speed_label.grid(row=3, column=3, columnspan=3, sticky="ew", padx=2)

        frm.columnconfigure(5, weight=1)

    def _vf_start(self):
        try:
            rpm = int(self.vf_target_var.get())
        except (tk.TclError, ValueError):
            return
        rpm = max(-5000, min(5000, rpm))
        self.send(f"vf={rpm}")

    def _vf_stop(self):
        self.send("vf=0")

    def _vf_params_changed(self, _event=None):
        try:
            boost = int(self.vf_boost_var.get())
            rated = int(self.vf_rated_var.get())
        except (tk.TclError, ValueError):
            return
        self.send(f"vfk={boost},{rated}")

    def on_telemetry(self, prefix, dd):
        if prefix == "VF":
            self.vf_status_label.config(
                text=(f"Target: {dd.get('target','?')} rpm | "
                      f"Meas: {dd.get('meas','?')} rpm | "
                      f"fe: {dd.get('fe','?')} Hz | "
                      f"fslip: {dd.get('fslip','?')} Hz | "
                      f"Vmag: {dd.get('vmag','?')}%"))
        elif prefix == "ENC":
            angle = dd.get('angle', 0)
            try:
                angle_deg = angle * 360.0 / 16384.0
            except (TypeError, ValueError):
                angle_deg = 0.0
            self.enc_angle_label.config(text=f"Angle: {angle_deg:.1f} deg (raw {angle})")
            self.enc_speed_label.config(text=f"Speed: {dd.get('speed','?')} rpm (err {dd.get('err','?')})")
