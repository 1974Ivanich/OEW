#!/usr/bin/env python3
"""V/f Control panel for nucleo_debug_tool.py.

Integration in PWMTab.__init__ (after _build_saleae_panel):
    self.vf_panel = VfPanel(self, self.send, self.saleae)

Telemetry in _on_line (add after FOC handler):
    if p == "VF": self.tab_pwm.vf_panel.on_telemetry(p, dd)
    elif p == "ENC": self.tab_pwm.vf_panel.on_telemetry(p, dd)
    elif p == "VFLOG": self.tab_pwm.vf_panel.on_telemetry(p, dd)

См. TZ_VF_DATA_LOGGING.md — единый онлайн-лог V/f-сессии (UART @VFLOG +
синхронный захват логического анализатора), логи пишутся в logs/vf_session_*/.
"""

import tkinter as tk
from tkinter import ttk
import os
import csv
import json
import shutil
import threading
from datetime import datetime


# Порядок полей должен совпадать с форматом @VFLOG в main.c (TIM6_DAC_IRQHandler)
CSV_FIELDS = ['t', 'target', 'meas', 'fe', 'fslip', 'vmag', 'theta',
              'du', 'dv', 'dw', 'i1', 'i2', 'ires', 'vbus',
              'eangle', 'espeed', 'eerr', 'fault', 'drp']

# Длительность захвата логического анализатора при старте V/f-сессии.
# ОГРАНИЧЕНО аппаратным буфером клона FX2 (~1.286M сэмплов): при 8 МГц это
# ~0.16 сек максимум — больший запрос роняет sigrok-cli (exit 1). 0.15 сек
# достаточно для главной задачи захвата: поймать фронт sync-триггера PB6
# (D13) для привязки UART-телеметрии к оси sigrok. Полная разгонная рампа
# (2 сек) через sigrok НЕ захватывается — для неё есть UART-телеметрия
# (vflog, 50 Гц) в telemetry.csv сессии.
SIGROK_VF_CAPTURE_S = 0.15

# Аппаратный sync-триггер (см. TRIG_High()/TRIG_Low() в main.c, пин PB6 —
# освобождён после удаления SPI2 CS). Фронт PB6 подаётся ровно в момент
# VFC_Start(), одновременно с этим MCU публикует sys_tick_ms в @TRIG:tick=...
# 16-канальный sigrok: D0..D11 — все ШИМ-сигналы (12 шт., оба инвертора),
# PB6 (триггер) подключён отдельным щупом на D13.
TRIGGER_SIGROK_CHANNEL = 13


class VfPanel:
    def __init__(self, parent, send_fn, saleae=None):
        self.send = send_fn
        self.saleae = saleae
        self.tab = parent            # PWMTab — для доступа к CHANNELS/CHANNELS_INV2
        self.session_dir = None
        self.csv_fp = None
        self.csv_writer = None
        self.vflog_count = 0
        self.trigger_tick_ms = None   # sys_tick_ms в момент фронта PB6 (из @TRIG)
        self.trigger_edge_ns = None   # положение этого фронта в оси захвата sigrok
        self._capture_ready = threading.Event()

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
        self.log_status_label = ttk.Label(frm, text="Log: idle", anchor="w", foreground="gray")
        self.log_status_label.grid(row=4, column=0, columnspan=6, sticky="ew", padx=2)

        frm.columnconfigure(5, weight=1)

    def _vf_start(self):
        try:
            rpm = int(self.vf_target_var.get())
            boost = int(self.vf_boost_var.get())
            rated = int(self.vf_rated_var.get())
        except (tk.TclError, ValueError):
            return
        rpm = max(-5000, min(5000, rpm))
        self.trigger_tick_ms = None
        self.trigger_edge_ns = None
        self._start_session(rpm, boost, rated)
        capture_started = self.saleae is not None and getattr(self.saleae, 'available', False)
        if capture_started:
            self._capture_ready.clear()
            threading.Thread(target=self._capture_sigrok, daemon=True).start()
            if not self._capture_ready.wait(timeout=5.0):
                print("[VfPanel] sigrok capture did not become ready within 5 s; V/f start cancelled")
                self._close_session("SIGROK_NOT_READY")
                return
        # Ревью GUI-13: параметры применяются ДО старта (порядок команд в
        # UART FIFO сохраняется) — раньше vfk= уходил только из Spinbox-
        # callback'а, асимметрично и без гарантии применения к этому старту.
        self.send(f"vfk={boost},{rated}")
        self.send(f"vf={rpm}")   # MCU поднимет PB6 и пришлёт @TRIG:tick=... сразу после этого

    def _vf_stop(self):
        self.send("vf=0")
        self._close_session()

    def _start_session(self, target_rpm, boost_pct, rated_hz):
        self._close_session()  # на случай, если предыдущая сессия не была закрыта
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = os.path.join("logs", f"vf_session_{ts}")
        try:
            os.makedirs(self.session_dir, exist_ok=True)
            self.csv_fp = open(os.path.join(self.session_dir, "telemetry.csv"),
                                "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_fp)
            self.csv_writer.writerow(CSV_FIELDS)
            meta = {
                "target_rpm": target_rpm, "boost_pct": boost_pct, "rated_hz": rated_hz,
                "start_time": datetime.now().isoformat(),
                "sigrok_capture_s": SIGROK_VF_CAPTURE_S,
            }
            with open(os.path.join(self.session_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            self.vflog_count = 0
            self.log_status_label.config(
                text=f"Log: {os.path.basename(self.session_dir)} (0 pts)", foreground="green")
        except OSError as e:
            self.session_dir = None
            self.csv_fp = None
            self.csv_writer = None
            self.log_status_label.config(text=f"Log: FAILED ({e})", foreground="red")

    def _close_session(self, reason=""):
        if self.csv_fp is not None:
            try:
                self.csv_fp.close()
            except OSError:
                pass
            if self.session_dir:
                # Ревью GUI-14: итог сессии — end_time/end_reason/samples.
                try:
                    mp = os.path.join(self.session_dir, "meta.json")
                    with open(mp, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["end_time"] = datetime.now().isoformat()
                    meta["end_reason"] = reason or "GUI_STOP"
                    meta["samples"] = self.vflog_count
                    with open(mp, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                except (OSError, ValueError):
                    pass
                self.log_status_label.config(
                    text=f"Log: {os.path.basename(self.session_dir)} saved ({self.vflog_count} pts)",
                    foreground="blue")
        self.csv_fp = None
        self.csv_writer = None

    def on_stopped(self, line):
        """Ревью GUI-14: V/f остановлен прошивкой (fault/remote) — закрыть
        CSV-сессию и показать reason. Вызывается из main GUI thread."""
        reason = line.split("REASON=", 1)[1].strip() if "REASON=" in line else "UNKNOWN"
        self._close_session(reason)
        self.vf_status_label.config(text=f"V/f stopped by MCU: {reason}", foreground="red")

    def _capture_sigrok(self):
        """Захват логического анализатора синхронно со стартом V/f (фоновый поток —
        capture_sync блокирующий). Копирует digital.csv в папку сессии и находит
        фронт аппаратного sync-триггера (PB6, см. TRIGGER_SIGROK_CHANNEL) для
        точной привязки к UART-телеметрии (@TRIG:tick=...)."""
        session_dir = self.session_dir  # снимок на случай смены сессии во время захвата
        try:
            chs = [c[3] for c in (self.tab.CHANNELS + self.tab.CHANNELS_INV2) if c[3] != -1]
            if TRIGGER_SIGROK_CHANNEL not in chs:
                chs.append(TRIGGER_SIGROK_CHANNEL)
            cap = self.saleae.capture_sync(digital_chs=chs, duration_s=SIGROK_VF_CAPTURE_S,
                                            sample_rate=8_000_000,
                                            ready_event=self._capture_ready)
            if cap is not None and hasattr(cap, 'csv_path') and session_dir:
                shutil.copy(cap.csv_path, os.path.join(session_dir, "digital.csv"))
                transitions = self.saleae.get_transitions(cap, TRIGGER_SIGROK_CHANNEL)
                rising = [t for t, v in transitions if v == 1]
                if rising:
                    self.trigger_edge_ns = rising[0]
                    self.tab.after(0, self._finalize_sync)  # Tkinter — только из GUI-потока
                else:
                    print("[VfPanel] sync trigger edge NOT found on channel "
                          f"D{TRIGGER_SIGROK_CHANNEL} — проверьте физическое "
                          "подключение щупа к PB6")
        except (OSError, AttributeError) as e:
            self._capture_ready.set()
            print(f"[VfPanel] sigrok capture error: {e}")

    def _finalize_sync(self):
        """Как только известны И trigger_tick_ms (из @TRIG по UART), И
        trigger_edge_ns (из захвата sigrok) — дописываем точную привязку
        времени UART↔sigrok в meta.json текущей сессии."""
        if self.trigger_tick_ms is None or self.trigger_edge_ns is None:
            return
        if not self.session_dir:
            return
        meta_path = os.path.join(self.session_dir, "meta.json")
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            meta = {}
        meta["sync"] = {
            "trigger_tick_ms": self.trigger_tick_ms,       # sys_tick_ms (MCU) в момент фронта PB6
            "trigger_edge_ns": self.trigger_edge_ns,       # positions в оси sigrok-захвата (нс от начала capture)
            "trigger_sigrok_channel": TRIGGER_SIGROK_CHANNEL,
            "formula": ("sigrok_time_ns(row) = trigger_edge_ns + "
                        "(row.t_ms - trigger_tick_ms) * 1e6"),
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            self.log_status_label.config(
                text=f"Log: {os.path.basename(self.session_dir)} — hw sync OK "
                     f"(t0={self.trigger_tick_ms}ms)")
        except OSError as e:
            print(f"[VfPanel] meta.json sync write error: {e}")

    def _vf_params_changed(self, _event=None):
        try:
            boost = int(self.vf_boost_var.get())
            rated = int(self.vf_rated_var.get())
        except (tk.TclError, ValueError):
            return
        self.send(f"vfk={boost},{rated}")

    def on_telemetry(self, prefix, dd):
        if prefix == "TRIG":
            self.trigger_tick_ms = dd.get('tick')
            self._finalize_sync()  # main thread — безопасно трогать Tkinter напрямую
        elif prefix == "VF":
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
        elif prefix == "VFLOG":
            if self.csv_writer is not None:
                try:
                    self.csv_writer.writerow([dd.get(k, '') for k in CSV_FIELDS])
                    self.csv_fp.flush()  # защита от потери данных при аварийном стопе
                    self.vflog_count += 1
                    if self.vflog_count % 25 == 0:  # не дёргать GUI на каждой строке (до 100 Гц)
                        self.log_status_label.config(
                            text=f"Log: {os.path.basename(self.session_dir)} ({self.vflog_count} pts)")
                except OSError:
                    pass
