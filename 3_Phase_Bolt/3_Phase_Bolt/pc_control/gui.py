# GUI for 3-phase motor control (Pure FOC + OEW Phase Shift + Field Weakening)
import csv
import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import numpy as np
except ImportError:
    np = None

from serial_link import MotorSerialLink


class MotorControlApp:
    UPDATE_MS = 100
    MAX_PLOT_POINTS = 500

    # Получаем директорию скрипта, а не текущую рабочую директорию
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, root):
        self.CAPTURE_DIR = os.path.join(self.SCRIPT_DIR, "captures")
        self.root = root
        self.root.title("3-Phase Motor Control (Pure FOC)")
        self.link = MotorSerialLink()
        self.link.on_text = self._on_text_line
        self.link.on_burst_ready = self._on_burst_ready
        self.link.on_flashlog_ready = self._on_flashlog_ready
        self.link.on_test_ready = self._process_test_results
        self.link.on_dtc_info = self._on_dtc_info
        self._last_dtc_info = None
        self._build_ui()
        self._refresh_ports()

        # Continuous CSV logger state
        self._csv_file = None
        self._csv_writer = None
        self._csv_path = None
        self._last_logged_sample = None  # dedup: skip writing same sample twice

        self._schedule_update()

    def _build_ui(self):
        self._build_connection_bar()
        self._build_control_panel()
        self._build_plots()

    def _build_connection_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)
        ttk.Label(bar, text="COM:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, width=12)
        self.port_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Refresh", command=self._refresh_ports).pack(side=tk.LEFT)
        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=4)
        self.status_lbl = tk.Label(bar, text="Disconnected", fg="gray")
        self.status_lbl.pack(side=tk.LEFT, padx=8)

    def _build_control_panel(self):
        panel = ttk.LabelFrame(self.root, text="Control")
        panel.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)

        row1 = ttk.Frame(panel)
        row1.pack(fill=tk.X, padx=4, pady=2)
        self.start_btn = ttk.Button(row1, text="START", command=self._cmd_start)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(row1, text="STOP", command=self._cmd_stop)
        self.stop_btn.pack(side=tk.LEFT, padx=4)
        self.stream_var = tk.IntVar(value=0)
        ttk.Checkbutton(row1, text="Stream", variable=self.stream_var,
                        command=self._cmd_stream_toggle).pack(side=tk.LEFT, padx=8)
        ttk.Button(row1, text="ZSC Autotune", command=self._cmd_zsc).pack(side=tk.LEFT, padx=4)
        ttk.Button(row1, text="Fault Clear", command=self._cmd_faultclr).pack(side=tk.LEFT, padx=4)
        ttk.Button(row1, text="Calib Reset", command=self._cmd_calib).pack(side=tk.LEFT, padx=4)
        self.fault_lbl = tk.Label(row1, text=" OK ", bg="green", fg="white")
        self.fault_lbl.pack(side=tk.LEFT, padx=6)

        row2 = ttk.Frame(panel)
        row2.pack(fill=tk.X, padx=4, pady=2)
        self._add_slider_row(row2, "Speed (Hz)", "speed", 0, 400, 50, 1, self._cmd_speed)
        self._add_slider_row(row2, "Power", "power", 0.0, 0.98, 0.5, 0.01, self._cmd_power)

        row3 = ttk.Frame(panel)
        row3.pack(fill=tk.X, padx=4, pady=2)
        self._add_slider_row(row3, "I target (A)", "current", 0.0, 4.75, 2.0, 0.05, self._cmd_current)

        # ---- Data capture & Auto-tune row ----
        row4 = ttk.Frame(panel)
        row4.pack(fill=tk.X, padx=4, pady=2)
        self.capture_btn = ttk.Button(row4, text="Capture Burst", command=self._cmd_capture)
        self.capture_btn.pack(side=tk.LEFT, padx=4)
        
        # НОВАЯ КНОПКА АВТОНАСТРОЙКИ
        self.tune_btn = ttk.Button(row4, text="Auto-Tune I-Loop", command=self._cmd_auto_tune)
        self.tune_btn.pack(side=tk.LEFT, padx=4)

        self.cap_status = tk.Label(row4, text="idle", fg="gray", width=24, anchor=tk.W)
        self.cap_status.pack(side=tk.LEFT, padx=4)

        ttk.Label(row4, text="Vdc:").pack(side=tk.LEFT)
        self.vdc_var = tk.DoubleVar(value=24.0)
        ttk.Entry(row4, textvariable=self.vdc_var, width=5).pack(side=tk.LEFT, padx=2)
        self.test_btn = ttk.Button(row4, text="Test L/R", command=self._cmd_test_l)
        self.test_btn.pack(side=tk.LEFT, padx=4)

        self.log_btn = ttk.Button(row4, text="Start CSV Log", command=self._cmd_toggle_log)
        self.log_btn.pack(side=tk.LEFT, padx=4)
        self.log_status = tk.Label(row4, text="logging off", fg="gray")
        self.log_status.pack(side=tk.LEFT, padx=4)

        # ---- Flash Black Box row ----
        row_flash = ttk.Frame(panel)
        row_flash.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(row_flash, text="Dump Flash", command=self._cmd_dump_flash).pack(side=tk.LEFT, padx=4)
        ttk.Button(row_flash, text="Clear Flash", command=self._cmd_clear_flash).pack(side=tk.LEFT, padx=4)
        self.flash_status = tk.Label(row_flash, text="flash: idle", fg="gray", width=30, anchor=tk.W)
        self.flash_status.pack(side=tk.LEFT, padx=4)

        # ---- Diagnostics row ----
        row5 = ttk.Frame(panel)
        row5.pack(fill=tk.X, padx=4, pady=2)
        self.diag_lbl = tk.Label(
            row5, text="ISR: -  calib: -  rA1:- rB1:- rC1:- rA2:- rB2:- rC2:-",
            font=("Consolas", 9), fg="darkblue", anchor=tk.W)
        self.diag_lbl.pack(side=tk.LEFT, fill=tk.X)

        # ---- Adaptive Dead-Time Compensator (DTC) control panel ----
        # Two rows: top = enable toggles + manual Kdt entry + reset/info,
        #           bottom = live state label (Kdt / corr / enorm / flags)
        dtc_frame = ttk.LabelFrame(panel, text="Adaptive Dead-Time Compensator (DTC)")
        dtc_frame.pack(fill=tk.X, padx=4, pady=2)

        dtc_row1 = ttk.Frame(dtc_frame)
        dtc_row1.pack(fill=tk.X, padx=4, pady=2)

        # Adaptation enable toggle (dtc on/off)
        self.dtc_adapt_var = tk.IntVar(value=1)
        ttk.Checkbutton(dtc_row1, text="Adapt",
                        variable=self.dtc_adapt_var,
                        command=self._cmd_dtc_adapt_toggle).pack(side=tk.LEFT, padx=2)
        # Compensation enable toggle (dtccomp on/off) — Kdt applied to duty
        self.dtc_comp_var = tk.IntVar(value=1)
        ttk.Checkbutton(dtc_row1, text="Compensate",
                        variable=self.dtc_comp_var,
                        command=self._cmd_dtc_comp_toggle).pack(side=tk.LEFT, padx=2)

        # Manual Kdt entry (DK<val>)
        ttk.Label(dtc_row1, text="Kdt:").pack(side=tk.LEFT, padx=(8, 0))
        self.dtc_kdt_var = tk.DoubleVar(value=20.0)
        ttk.Entry(dtc_row1, textvariable=self.dtc_kdt_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(dtc_row1, text="Set Kdt", width=8,
                   command=self._cmd_dtc_set_kdt).pack(side=tk.LEFT, padx=2)

        # Sign flip button — toggles between +1 and -1
        self.dtc_sign_var = tk.IntVar(value=1)
        ttk.Button(dtc_row1, text="Flip Sign",
                   command=self._cmd_dtc_flip_sign).pack(side=tk.LEFT, padx=2)
        self.dtc_sign_lbl = tk.Label(dtc_row1, text="sign=+1", width=8, fg="darkblue")
        self.dtc_sign_lbl.pack(side=tk.LEFT, padx=2)

        # Reset and Info buttons
        ttk.Button(dtc_row1, text="Reset", command=self._cmd_dtc_reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(dtc_row1, text="Info", command=self._cmd_dtc_info).pack(side=tk.LEFT, padx=2)

        # Live DTC state label (updated in _update_plots from JSON stream)
        dtc_row2 = ttk.Frame(dtc_frame)
        dtc_row2.pack(fill=tk.X, padx=4, pady=2)
        self.dtc_state_lbl = tk.Label(
            dtc_row2,
            text="Kdt:-  corr:-  enorm:-  flags:----  (no data — run 'Info')",
            font=("Consolas", 9), fg="darkviolet", anchor=tk.W)
        self.dtc_state_lbl.pack(side=tk.LEFT, fill=tk.X)

        # ---- Experiment marker row ----
        row6 = ttk.Frame(panel)
        row6.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(row6, text="Exp:").pack(side=tk.LEFT)
        self.exp_note_var = tk.StringVar(value="")
        ttk.Entry(row6, textvariable=self.exp_note_var, width=40).pack(side=tk.LEFT, padx=4)
        ttk.Button(row6, text="Stats", command=self._cmd_auto_stats).pack(side=tk.LEFT, padx=4)
        self.stats_lbl = tk.Label(row6, text="", font=("Consolas", 9), fg="darkgreen", anchor=tk.W)
        self.stats_lbl.pack(side=tk.LEFT, padx=4)

    def _add_slider_row(self, parent, label, key, lo, hi, init, step, cmd):
        ttk.Label(parent, text=label, width=14).pack(side=tk.LEFT)
        var = tk.DoubleVar(value=init)
        setattr(self, key + "_var", var)
        ent = ttk.Entry(parent, textvariable=var, width=8)
        ent.pack(side=tk.LEFT, padx=2)
        ent.bind("<Return>", lambda e: self._entry_to_slider(key, lo, hi, cmd))
        ttk.Button(parent, text="Set", width=4,
                   command=lambda: self._entry_to_slider(key, lo, hi, cmd)).pack(side=tk.LEFT, padx=2)
        s = ttk.Scale(parent, from_=lo, to=hi, orient=tk.HORIZONTAL, length=200,
                      variable=var, command=lambda v: cmd(float(v)))
        s.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)

    def _entry_to_slider(self, key, lo, hi, cmd):
        var = getattr(self, key + "_var")
        try:
            v = float(var.get())
        except Exception:
            return
        v = max(lo, min(hi, v))
        var.set(v)
        cmd(v)

    def _build_plots(self):
        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.ax_ph = self.fig.add_subplot(2, 1, 1)
        self.ax_sum = self.fig.add_subplot(2, 1, 2)
        self.ax_ph.set_title("Phase currents (A)")
        self.ax_ph.set_ylabel("I, A")
        self.ax_ph.grid(True, alpha=0.3)
        self.ax_sum.set_title("Total current |I| (A)")
        self.ax_sum.set_xlabel("samples")
        self.ax_sum.set_ylabel("|I|, A")
        self.ax_sum.grid(True, alpha=0.3)
        self.l_ia, = self.ax_ph.plot([], [], color="red", label="Ia")
        self.l_ib, = self.ax_ph.plot([], [], color="goldenrod", label="Ib")
        self.l_ic, = self.ax_ph.plot([], [], color="blue", label="Ic")
        self.ax_ph.legend(loc="upper right", fontsize=8)
        self.l_sum, = self.ax_sum.plot([], [], color="darkgreen", label="|I|sum")
        self.ax_sum.legend(loc="upper right", fontsize=8)
        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # ---- connection ----

    def _refresh_ports(self):
        ports = MotorSerialLink.list_ports()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _toggle_connect(self):
        if self.link.is_open:
            self.link.close()
            self.connect_btn.config(text="Connect")
            self.status_lbl.config(text="Disconnected", fg="gray")
        else:
            port = self.port_var.get()
            if not port:
                messagebox.showerror("Error", "Select COM port")
                return
            try:
                self.link.port = port
                self.link.open()
            except Exception as e:
                messagebox.showerror("Connect error", str(e))
                return
            self.connect_btn.config(text="Disconnect")
            self.status_lbl.config(text="Connected: " + port, fg="green")

    # ---- text event hook ----

    def _on_text_line(self, text):
        pass

    # ---- command handlers ----

    def _cmd_calib(self):
        if not self.link.is_open: return messagebox.showerror("Error", "Not connected")
        self.link.calib()

    def _cmd_test_l(self):
        if not self.link.is_open: return messagebox.showerror("Error", "Not connected")
        if not messagebox.askyesno("Test Warning", "Убедитесь, что ротор ЗАФИКСИРОВАН!\nПродолжить?"): return
        self.cap_status.config(text="Testing L...", fg="orange")
        self.link.test_inductance()

    def _process_test_results(self, raw_data):
        try:
            self.root.after(0, lambda: self._calculate_motor_params(raw_data))
        except Exception: pass

    def _calculate_motor_params(self, raw_data):
        try:
            currents_ma = []
            currents_b_ma = []
            currents_c_ma = []
            currents_a2_ma = []
            currents_b2_ma = []
            for line in raw_data:
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        currents_ma.append(int(parts[1]))
                        if len(parts) >= 3:
                            currents_b_ma.append(int(parts[2]))
                        if len(parts) >= 4:
                            currents_c_ma.append(int(parts[3]))
                        if len(parts) >= 5:
                            currents_a2_ma.append(int(parts[4]))
                        if len(parts) >= 6:
                            currents_b2_ma.append(int(parts[5]))
                    except ValueError:
                        continue

            n = len(currents_ma)
            if n < 15:
                self.cap_status.config(text="Test failed (no data)", fg="red")
                return

            # Диагностика полярности датчиков:
            # Этап 1 (A+/B-): ожидается ia > 0, ib < 0
            # Этап 2 (C+):    ожидается ic > 0
            a_inverted = sum(currents_ma[5:]) < 0
            b_inverted = bool(currents_b_ma) and sum(currents_b_ma[5:]) > 0
            c_inverted = bool(currents_c_ma) and sum(currents_c_ma[5:]) < 0
            inverted = []
            if a_inverted: inverted.append("A")
            if b_inverted: inverted.append("B")
            if c_inverted: inverted.append("C")
            sign_note = ""
            if inverted:
                sign_note = f"(ИНВЕРСИЯ датчиков тока: фазы {', '.join(inverted)}!)\n"
            if a_inverted:
                currents_ma = [-v for v in currents_ma]

            start_idx = 2
            end_idx = 10
            dt = (end_idx - start_idx) * 0.0001

            i_start = currents_ma[start_idx]
            i_end = currents_ma[end_idx]
            di_dt = ((i_end - i_start) / 1000.0) / dt

            Vdc = self.vdc_var.get()
            V_applied = Vdc * 0.2  # per-phase voltage (OEW doubling included)

            # Компенсация падения на IGBT: ток течёт через 2 ключа (Vce_sat ≈ 1.8V каждый)
            V_ce_total = 2.0 * 1.8  # 3.6 В теряется на ключах
            V_applied_eff = max(V_applied - V_ce_total / 2.0, 0.1)  # per-phase effective
            V_total_eff = 2.0 * V_applied_eff

            if di_dt <= 0:
                self.cap_status.config(text="Test failed (di/dt<=0)", fg="red")
                return

            # Тест прикладывает V_total к двум фазам последовательно (A+ B-).
            # L_total = V_total_eff / di_dt, L_phase = L_total / 2
            L_henry = V_applied_eff / di_dt  # per-phase inductance
            L_mH = L_henry * 1000.0

            # Установившийся ток: если tau = L/R > длительности теста,
            # ток не успевает установиться. Экстраполируем I_ss по трём
            # равноотстоящим точкам экспоненты: I_ss=(i2^2-i1*i3)/(2*i2-i1-i3)
            last = n - 3
            step = (last - 5) // 2
            i1 = currents_ma[5] / 1000.0
            i2 = currents_ma[5 + step] / 1000.0
            i3 = currents_ma[5 + 2 * step] / 1000.0
            denom = 2.0 * i2 - i1 - i3
            settled = abs(i3 - i2) < abs(i3) * 0.05 if i3 != 0 else False
            if settled or abs(denom) < 1e-6:
                I_final = i3
            else:
                I_ss = (i2 * i2 - i1 * i3) / denom
                # Санитарная проверка: I_ss должен быть >= i3 и разумным
                I_final = I_ss if (i3 < I_ss < i3 * 20.0) else i3

            # R_est = V_total_eff / I = phase-to-phase resistance (как мультиметром)
            R_est = V_total_eff / I_final if I_final > 0.05 else 0

            # --- РАСЧЕТ ПАРАМЕТРОВ ДЛЯ FOC НАБЛЮДАТЕЛЯ ---
            Rs_est = R_est / 2.0
            Rr_est = R_est / 2.0
            # testL измеряет полную индуктивность фазы (DC step, низкая частота).
            # Это Ls напрямую, а не индуктивность рассеяния.
            # Lm = Ls - L_leakage. Для типового асинхронника L_leakage ~ 5-10% от Ls.
            Ls_est = L_henry
            Lr_est = Ls_est
            Lm_est = Ls_est * 0.9  # 10% запас на рассеяние
            
            # Отправляем параметры в МК
            self.link.set_motor_params(Rs_est, Rr_est, Ls_est, Lr_est, Lm_est)
            # ----------------------------------------------------

            Kp = (500.0 * L_henry) / Vdc
            Ki = (500.0 * R_est) / Vdc if R_est > 0 else 20.0

            I_max = max(2.0, I_final * 2.5)
            I_rms = max(1.0, I_final * 1.5)
            dt_limit = max(1.5, di_dt * 0.0001)

            try:
                # Отправляем также параметры регулятора тока
                self.link.apply_motor_params(Kp, Ki, I_max, I_rms, dt_limit)
                apply_status = "Параметры успешно применены к МК!"
            except Exception as e:
                apply_status = f"Ошибка отправки: {e}"

            self.cap_status.config(text=f"L={L_mH:.1f}mH R={R_est:.1f}Ohm", fg="blue")

            # Сырые сэмплы для диагностики (~20 точек, в мА)
            dump_step = max(1, n // 20)
            raw_dump = ",".join(str(v) for v in currents_ma[::dump_step])
            raw_dump_b = ",".join(str(v) for v in currents_b_ma[::dump_step]) if currents_b_ma else "нет данных"
            raw_dump_c = ",".join(str(v) for v in currents_c_ma[::dump_step]) if currents_c_ma else "нет данных"
            raw_dump_a2 = ",".join(str(v) for v in currents_a2_ma[::dump_step]) if currents_a2_ma else "нет данных"
            raw_dump_b2 = ",".join(str(v) for v in currents_b2_ma[::dump_step]) if currents_b2_ma else "нет данных"

            # Обновляем текст в окне, чтобы видеть, что ушло в FOC
            result_text = (
                f"=== РЕЗУЛЬТАТЫ ТЕСТА ===\n"
                f"{sign_note}"
                f"Индуктивность (L_phase): {L_mH:.2f} мГн\n"
                f"Сопротивление (R phase-to-phase): {R_est:.2f} Ом\n"
                f"Ток теста (I_final/I_ss): {I_final:.2f} А\n"
                f"Сэмплы ia(t), мА (кажд. {dump_step}-й): {raw_dump}\n"
                f"Сэмплы ib(t), мА (этап 1, B-): {raw_dump_b}\n"
                f"Сэмплы ic(t), мА (этап 2, C+): {raw_dump_c}\n"
                f"Сэмплы ia2(t), мА (ADC2 inv1 A): {raw_dump_a2}\n"
                f"Сэмплы ib2(t), мА (ADC2 inv1 B): {raw_dump_b2}\n\n"
                f"=== ПАРАМЕТРЫ ДЛЯ FOC (ОТПРАВЛЕНЫ) ===\n"
                f"Rs = {Rs_est:.4f} Ом\n"
                f"Rr = {Rr_est:.4f} Ом\n"
                f"Ls = {Ls_est*1000:.2f} мГн\n"
                f"Lr = {Lr_est*1000:.2f} мГн\n"
                f"Lm = {Lm_est*1000:.2f} мГн\n\n"
                f"=== НАСТРОЙКИ ПИ-РЕГУЛЯТОРА ===\n"
                f"PI_KP  = {Kp:.4f}\n"
                f"PI_KI  = {Ki:.2f}\n\n"
                f"Статус: {apply_status}\n"
            )

            top = tk.Toplevel(self.root)
            top.title("Auto-Tuning завершен")
            top.geometry("400x400")

            txt = tk.Text(top, wrap=tk.WORD, font=("Consolas", 11))
            txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            txt.insert(tk.END, result_text)

            def copy_to_clipboard():
                self.root.clipboard_clear()
                self.root.clipboard_append(result_text)

            btn = ttk.Button(top, text="Копировать в буфер обмена", command=copy_to_clipboard)
            btn.pack(pady=5)
        except Exception as e:
            self.cap_status.config(text=f"Calc Err: {e}", fg="red")

    # ---- burst capture ----

    def _cmd_capture(self):
        if not self.link.is_open: return messagebox.showerror("Error", "Not connected")
        self.cap_status.config(text="arming...", fg="orange")
        self.link.capture()

    def _on_burst_ready(self, samples, isr_hz):
        try: self.root.after(0, lambda: self._save_burst(samples, isr_hz))
        except Exception: pass

    def _save_burst(self, samples, isr_hz):
        n = len(samples)
        if n == 0:
            self.cap_status.config(text="empty capture", fg="red"); return
        # Auto-triggered burst (fault/FOC_LOST) — don't create file, just update status
        if self.link.burst_auto:
            self.cap_status.config(text="auto-burst %d pts (not saved)" % n, fg="orange")
            return
        try:
            os.makedirs(self.CAPTURE_DIR, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self.CAPTURE_DIR, "burst_%s.csv" % stamp)
            latest = self.link.latest
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["# 3_Phase burst capture", stamp, "# isr_hz", "%.3f" % isr_hz,
                            "# trig_idx", self.link.burst_trig_idx,
                            "# decim", self.link.burst_decim,
                            "# auto", self.link.burst_auto])
                # Write motor params and PI gains if available
                bp = self.link.burst_params
                if bp:
                    w.writerow(["# params", "Rs=%.4f" % bp.get("Rs",0), "Rr=%.4f" % bp.get("Rr",0),
                                "Ls=%.5f" % bp.get("Ls",0), "Lr=%.5f" % bp.get("Lr",0),
                                "Lm=%.5f" % bp.get("Lm",0), "pp=%.1f" % bp.get("pp",0),
                                "KP_id=%.3f" % bp.get("KP_id",0), "KI_id=%.1f" % bp.get("KI_id",0),
                                "KP_iq=%.3f" % bp.get("KP_iq",0), "KI_iq=%.1f" % bp.get("KI_iq",0),
                                "KP_spd=%.3f" % bp.get("KP_spd",0), "KI_spd=%.1f" % bp.get("KI_spd",0),
                                # DTC params (added in current firmware)
                                "Kdt=%.2f" % bp.get("Kdt",0), "mu=%.2f" % bp.get("mu",0),
                                "sign_flip=%.0f" % bp.get("sign_flip",1),
                                "en=%d" % int(bp.get("en",0)), "cmp=%d" % int(bp.get("cmp",0))])
                w.writerow(["idx", "ts", "ia1", "ib1", "ic1", "ia2", "ib2", "ic2", "izs", "ang",
                            "id", "iq", "id_t", "iq_t", "vd", "vq", "vd_raw", "vq_raw",
                            "wr", "slip", "psi", "te",
                            "pi_id_i", "pi_iq_i", "pi_spd_i", "sat_flags",
                            "e_alpha", "e_beta", "tgt_spd", "theta_jit",
                            "diq_dt", "dwr_dt", "p_elec", "q_elec",
                            "kdt", "dtc_corr",
                            "d1a", "d1b", "d1c", "d2a", "d2b", "d2c"])
                for s in samples:
                    w.writerow([s.idx, s.ts, s.ia1, s.ib1, s.ic1, s.ia2, s.ib2, s.ic2, s.izs, s.ang,
                                s.id, s.iq, s.id_t, s.iq_t, s.vd, s.vq, s.vd_raw, s.vq_raw,
                                s.wr, s.slip, s.psi, s.te,
                                s.pi_id_i, s.pi_iq_i, s.pi_spd_i, s.sat_flags,
                                s.e_alpha, s.e_beta, s.tgt_spd, s.theta_jit,
                                s.diq_dt, s.dwr_dt, s.p_elec, s.q_elec,
                                s.kdt, s.dtc_corr,
                                s.d1a, s.d1b, s.d1c, s.d2a, s.d2b, s.d2c])
            self.cap_status.config(text="saved %d pts (%.0f Hz)" % (n, isr_hz), fg="green")
        except Exception as e:
            self.cap_status.config(text="Err: %s" % str(e)[:40], fg="red")

    # ---- flash black box ----

    def _cmd_dump_flash(self):
        if not self.link.is_open: return messagebox.showerror("Error", "Not connected")
        self.flash_status.config(text="flash: requesting...", fg="orange")
        self.link.dump_flash()

    def _cmd_clear_flash(self):
        if not self.link.is_open: return messagebox.showerror("Error", "Not connected")
        if not messagebox.askyesno("Clear Flash", "Erase all flash black box records?\nThis cannot be undone."):
            return
        self.flash_status.config(text="flash: clearing...", fg="orange")
        self.link.clear_flash()
        self.root.after(2000, lambda: self.flash_status.config(text="flash: cleared", fg="green"))

    def _on_flashlog_ready(self, samples):
        try: self.root.after(0, lambda: self._save_flashlog(samples))
        except Exception: pass

    def _save_flashlog(self, samples):
        n = len(samples)
        if n == 0:
            self.flash_status.config(text="flash: empty", fg="red")
            return
        try:
            os.makedirs(self.CAPTURE_DIR, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(self.CAPTURE_DIR, "flashlog_%s.csv" % stamp)
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["# 3_Phase flash black box", stamp, "# records", n])
                w.writerow(["ts", "id", "iq", "vd", "vq", "wr", "slip", "te", "psi",
                            "sat", "foc", "p", "q", "ea", "eb", "kdt", "fault"])
                for s in samples:
                    w.writerow([s.ts, s.id, s.iq, s.vd, s.vq, s.wr, s.slip, s.te, s.psi,
                                s.sat, s.foc, s.p, s.q, s.ea, s.eb, s.kdt, s.fault])
            self.flash_status.config(text="flash: saved %d records" % n, fg="green")
        except Exception as e:
            self.flash_status.config(text="flash: Err %s" % str(e)[:30], fg="red")

    # ---- НОВОЕ: АВТОНАСТРОЙКА КОНТУРА ТОКА ----

    def _cmd_auto_tune(self):
        if not self.link.is_open:
            return messagebox.showerror("Error", "Not connected")
        if np is None:
            return messagebox.showerror("Error", "Требуется библиотека numpy. Установите: pip install numpy")
        if not messagebox.askyesno("Auto-Tune Current Loop",
                                   "Запустить автонастройку контура тока?\n\n"
                                   "Алгоритм:\n"
                                   "1. Калибровка датчиков\n"
                                   "2. Запуск на 20 Гц / 0.5 А\n"
                                   "3. Скачок тока до 2.0 А\n"
                                   "4. Анализ реакции и расчет KP/KI\n"
                                   "5. Применение параметров\n\n"
                                   "Убедитесь, что ротор свободен (двигатель будет вращаться)!"):
            return

        self.tune_btn.config(state=tk.DISABLED)
        self.cap_status.config(text="Auto-Tune: Init...", fg="orange")

        # Перехватываем callback Burst-данных
        self._original_burst_callback = self.link.on_burst_ready
        self.link.on_burst_ready = self._process_tune_burst

        # Запускаем последовательность в отдельном потоке
        threading.Thread(target=self._run_tune_sequence, daemon=True).start()

    def _run_tune_sequence(self):
        """Выполняется в фоновом потоке, чтобы не заморозить GUI"""
        try:
            self.link.stop()
            time.sleep(0.5)
            self.link.fault_clear()
            time.sleep(0.2)
            self.link.calib()
            time.sleep(2.0)  # Ожидание калибровки (2000 точек)

            # Стартовые параметры
            self.link.set_speed(20.0)  # 20 Гц
            self.link.set_current(0.5)  # 0.5 А
            self.link.start()
            time.sleep(3.0)  # Ожидание выхода на режим

            # Скачок тока и захват данных
            self.cap_status.config(text="Auto-Tune: Step & Capture...", fg="orange")
            self.link.set_current(2.0)   # STEP!
            self.link.capture()          # CAPTURE 512 points (100ms)
            
            # Ждем, пока сработает _process_tune_burst
            # (Таймаут 5 секунд)
            for _ in range(50):
                time.sleep(0.1)
                if self.link.on_burst_ready == self._original_burst_callback:
                    break
            
            # Если таймаут, возвращаем кнопку
            if self.link.on_burst_ready != self._original_burst_callback:
                self.link.on_burst_ready = self._original_burst_callback
                self.cap_status.config(text="Auto-Tune: Timeout", fg="red")
                self.tune_btn.config(state=tk.NORMAL)
                self.link.stop()

        except Exception as e:
            self.cap_status.config(text=f"Tune Error: {e}", fg="red")
            self.tune_btn.config(state=tk.NORMAL)
            self.link.on_burst_ready = self._original_burst_callback

    def _process_tune_burst(self, samples, isr_hz):
        """Перехватывает данные захвата для анализа автонастройки"""
        # Возвращаем оригинальный callback
        self.link.on_burst_ready = self._original_burst_callback
        # Отправляем в главный поток для обработки
        self.root.after(0, lambda: self._analyze_current_response(samples, isr_hz))

    def _analyze_current_response(self, samples, isr_hz):
        """Анализ реакции на скачок и расчет новых KP/KI"""
        try:
            self.cap_status.config(text="Auto-Tune: Analyzing...", fg="orange")
            self.link.stop() # Останавливаем мотор сразу после захвата

            if len(samples) < 20:
                self.cap_status.config(text="Auto-Tune: No data", fg="red")
                return

            # Извлекаем фазные токи в Амперах (в данных они в мА)
            ia = np.array([s.ia1 for s in samples], dtype=float) / 1000.0
            ib = np.array([s.ib1 for s in samples], dtype=float) / 1000.0
            ic = np.array([s.ic1 for s in samples], dtype=float) / 1000.0

            # Вычисляем RMS тока для каждого сэмпла
            # (это эквивалентно Irms, который контроллер пытается стабилизировать)
            irms = np.sqrt((ia**2 + ib**2 + ic**2) / 3.0)

            dt = 1.0 / isr_hz
            t = np.arange(len(irms)) * dt

            # 1. Определяем начальный и конечный уровни
            # До скачка (первые 10 сэмплов = 2мс) и после (последние 50 сэмплов)
            i0 = np.mean(irms[:10])
            ifinal = np.mean(irms[-50:])
            delta_i = ifinal - i0

            if delta_i < 0.1:
                self.cap_status.config(text="Auto-Tune: Step too small", fg="red")
                return

            # 2. Ищем точку максимального наклона (inflection point)
            deriv = np.diff(irms) / dt
            inflection_idx = np.argmax(deriv)
            slope = deriv[inflection_idx]

            if slope <= 0:
                self.cap_status.config(text="Auto-Tune: Invalid slope", fg="red")
                return

            # 3. Вычисляем L (Dead time) и Tau (Time constant)
            # Метод касательной: проводим линию от точки перегиба
            t_inflection = t[inflection_idx]
            
            # L: время от начала до пересечения касательной с уровнем i0
            # y = i0 + slope * (t - t_inflection) -> при y=i0, t = t_inflection
            L = t_inflection
            
            # Tau: время от L до достижения уровня ifinal
            # ifinal = i0 + slope * Tau -> Tau = delta_i / slope
            tau = delta_i / slope

            # 4. Коэффициент усиления объекта K
            delta_target = 2.0 - 0.5  # Было 0.5А, стало 2.0А
            K = delta_i / delta_target

            # 5. Расчет по Ziegler-Nichols (для ПИ-регулятора)
            # Формулы для процесса 1-го порядка с запаздыванием
            Kp_new = 0.9 * (tau / (K * L))
            Ti = 3.33 * L
            Ki_new = Kp_new / Ti

            # Ограничиваем разумными пределами для безопасности
            Kp_new = max(0.001, min(1.0, Kp_new))
            Ki_new = max(1.0, min(500.0, Ki_new))

            # 6. Отправляем новые параметры в МК
            self.link.send("KP" + repr(float(Kp_new)))
            time.sleep(0.05)
            self.link.send("KI" + repr(float(Ki_new)))

            self.cap_status.config(text=f"Tuned! Kp={Kp_new:.3f} Ki={Ki_new:.1f}", fg="green")

            result_text = (
                f"=== АВТОНАСТРОЙКА КОНТУРА ТОКА ===\n"
                f"Базовый ток (I0): {i0:.2f} A\n"
                f"Конечный ток (If): {ifinal:.2f} A\n"
                f"Приращение (dI): {delta_i:.2f} A\n\n"
                f"Мертвое время (L): {L*1000:.2f} мс\n"
                f"Постоянная времени (Tau): {tau*1000:.2f} мс\n"
                f"Коэффициент объекта (K): {K:.3f}\n\n"
                f"=== РАССЧИТАННЫЕ ПАРАМЕТРЫ ПИ ===\n"
                f"KP = {Kp_new:.4f}\n"
                f"KI = {Ki_new:.2f}\n\n"
                f"Параметры отправлены в микроконтроллер."
            )

            messagebox.showinfo("Auto-Tune Success", result_text)

        except Exception as e:
            self.cap_status.config(text=f"Analyze Err: {e}", fg="red")
        finally:
            self.tune_btn.config(state=tk.NORMAL)

    # ---- continuous CSV logging ----

    def _cmd_toggle_log(self):
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None
            self.log_btn.config(text="Start CSV Log")
            self.log_status.config(text="stopped: %s" % os.path.basename(self._csv_path), fg="gray")
            return
        self._start_auto_log()

    def _start_auto_log(self):
        """Automatically create CSV file for telemetry logging."""
        if self._csv_file is not None:
            self._csv_file.close()
        os.makedirs(self.CAPTURE_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv_path = os.path.join(self.CAPTURE_DIR, "run_%s.csv" % stamp)
        self._csv_file = open(self._csv_path, "w", newline="")
        self._csv_writer = csv.writer(self._csv_file)
        self._last_logged_sample = None  # reset dedup tracker for new log
        # Experiment marker + motor params from latest stream sample
        note = self.exp_note_var.get().strip()
        if note:
            self._csv_writer.writerow(["# experiment", note])
        s = self.link.latest
        if s:
            self._csv_writer.writerow(["# foc_state", s.foc_state, "isr_hz", "%.1f" % s.isr_hz,
                                        "isr_us_avg", s.isr_us_avg, "isr_us_max", s.isr_us_max])
        self._csv_writer.writerow([
            "ts", "Vdc", "ia", "ib", "ic", "ia2", "ib2", "ic2", "izs", "irms",
            "freq", "target_freq", "power", "target_current", "zsc_integ",
            "fault", "raw_a1", "raw_b1", "raw_c1", "raw_a2", "raw_b2", "raw_c2", "isr_ticks", "calibrated",
            "Id", "Iq", "Vd", "Vq", "Vd_raw", "Vq_raw", "Wr_rad_s", "Theta_rad",
            "mcu_ts", "IdT", "IqT", "slip_hz", "psi_wb", "Te_nm", "isr_hz", "tx_dropped", "run", "foc_state",
            "sat", "pi_id_i", "pi_iq_i", "pi_spd_i", "isr_us_avg", "isr_us_max",
            "P_elec", "Q_elec", "E_alpha", "E_beta",
            "Kdt", "Dtc_corr", "Dtc_enorm", "Dtc_en", "Dtc_cmp", "Dtc_conv", "Dtc_hold",
        ])
        self._csv_file.flush()
        self.log_btn.config(text="Stop CSV Log")
        self.log_status.config(text="AUTO-log: %s" % os.path.basename(self._csv_path), fg="green")

    # ---- command buttons ----

    def _cmd_start(self):
        self.link.start()
        # Auto-enable stream so the run-log gets data immediately
        self.stream_var.set(1)
        self.link.stream_on()
        self._start_auto_log()
    def _cmd_stop(self):
        self.link.stop()
        # Auto-disable stream on stop
        self.stream_var.set(0)
        self.link.stream_off()
    def _cmd_stream_toggle(self):
        if self.stream_var.get(): self.link.stream_on()
        else: self.link.stream_off()
    def _cmd_zsc(self): self.link.zsc_atune()
    def _cmd_faultclr(self): self.link.fault_clear()
    def _cmd_speed(self, v): self.link.set_speed(v)
    def _cmd_power(self, v): self.link.set_power(v)
    def _cmd_current(self, v): self.link.set_current(v)

    # ---- DTC command handlers ----

    def _cmd_dtc_adapt_toggle(self):
        if not self.link.is_open:
            messagebox.showerror("Error", "Not connected")
            self.dtc_adapt_var.set(1)  # revert checkbox
            return
        if self.dtc_adapt_var.get():
            self.link.dtc_adapt_on()
        else:
            self.link.dtc_adapt_off()

    def _cmd_dtc_comp_toggle(self):
        if not self.link.is_open:
            messagebox.showerror("Error", "Not connected")
            self.dtc_comp_var.set(1)  # revert checkbox
            return
        if self.dtc_comp_var.get():
            self.link.dtc_comp_on()
        else:
            self.link.dtc_comp_off()

    def _cmd_dtc_set_kdt(self):
        if not self.link.is_open:
            return messagebox.showerror("Error", "Not connected")
        try:
            v = float(self.dtc_kdt_var.get())
        except Exception:
            return messagebox.showerror("Error", "Invalid Kdt value")
        self.link.dtc_set_kdt(v)
        # Refresh info to confirm
        self.link.dtc_info()

    def _cmd_dtc_flip_sign(self):
        if not self.link.is_open:
            return messagebox.showerror("Error", "Not connected")
        # Toggle sign locally, then send to MCU
        new_sign = -1 if self.dtc_sign_var.get() >= 0 else 1
        self.dtc_sign_var.set(new_sign)
        self.dtc_sign_lbl.config(text="sign=%+d" % new_sign)
        self.link.dtc_set_sign(new_sign)
        # Reset corr/converged on the MCU side via dtcreset? No — DS already
        # resets corr_lpf and converged flags in main.c.

    def _cmd_dtc_reset(self):
        if not self.link.is_open:
            return messagebox.showerror("Error", "Not connected")
        if not messagebox.askyesno("DTC Reset",
                                   "Сбросить DTC к значениям по умолчанию?\n"
                                   "Kdt вернётся к стартовому значению, "
                                   "адаптация начнётся заново."):
            return
        self.link.dtc_reset()
        # Refresh info after a short delay
        self.root.after(200, self.link.dtc_info)

    def _cmd_dtc_info(self):
        if not self.link.is_open:
            return messagebox.showerror("Error", "Not connected")
        self.link.dtc_info()

    def _on_dtc_info(self, dtc):
        """Called from reader thread when $DTC line is received.
        Marshal into main thread for safe Tk access."""
        try:
            self.root.after(0, lambda: self._show_dtc_info(dtc))
        except Exception:
            pass

    def _show_dtc_info(self, dtc):
        """Update Kdt entry and state label from full DTC snapshot."""
        self.dtc_kdt_var.set(float(dtc.get("Kdt", 0.0)))
        sign = int(dtc.get("sign", 1))
        self.dtc_sign_var.set(sign)
        self.dtc_sign_lbl.config(text="sign=%+d" % sign)
        self.dtc_adapt_var.set(int(dtc.get("en", 0)))
        self.dtc_comp_var.set(int(dtc.get("cmp", 0)))
        self._update_dtc_state_label(dtc)
        # Also reflect into the persistent state used by _update_plots
        self._last_dtc_info = dtc

    def _update_dtc_state_label(self, dtc=None):
        """Refresh the live DTC state label. Falls back to latest stream sample
        if no $DTC snapshot is available."""
        if dtc is None:
            dtc = getattr(self, "_last_dtc_info", None)
            # Prefer live stream values (more recent than $DTC snapshot)
            s = self.link.latest
            if s is not None:
                flags = "%d%d%d%d" % (s.dtc_enabled, s.dtc_compensate,
                                       s.dtc_converged, s.dtc_hold)
                self.dtc_state_lbl.config(
                    text="Kdt:%.1f  corr:%+.5f  enorm:%.4f  flags:%s  (live)" % (
                        s.kdt, s.dtc_corr, s.dtc_enorm, flags))
                return
            if dtc is None:
                self.dtc_state_lbl.config(
                    text="Kdt:-  corr:-  enorm:-  flags:----  (no data — run 'Info')")
                return
        # Full snapshot from $DTC
        flags = "%d%d%d%d" % (int(dtc.get("en", 0)), int(dtc.get("cmp", 0)),
                               int(dtc.get("conv", 0)), int(dtc.get("hold", 0)))
        self.dtc_state_lbl.config(
            text="Kdt:%.1f  corr:%+.5f  enorm:%.4f  flags:%s  adapt:%ds  conv:%ds" % (
                float(dtc.get("Kdt", 0.0)),
                float(dtc.get("corr", 0.0)),
                float(dtc.get("enorm", 0.0)),
                flags,
                int(dtc.get("adapt_ticks", 0)) // 5000,  # ticks -> seconds (5 kHz)
                int(dtc.get("conv_ticks", 0)) // 5000))

    # ---- periodic UI update ----

    def _schedule_update(self):
        self._update_plots()
        self._drain_log()
        self._update_capture_status()
        self.root.after(self.UPDATE_MS, self._schedule_update)

    def _drain_log(self):
        if self._csv_writer is None: return
        hist = list(self.link.history)
        if not hist: return
        s = hist[-1]
        if s is self._last_logged_sample:
            return  # no new data from MCU — skip duplicate row
        self._last_logged_sample = s
        self._csv_writer.writerow([
            "%.3f" % s.ts, self.vdc_var.get(), s.ia, s.ib, s.ic, s.ia2, s.ib2, s.ic2, s.izs, s.irms,
            s.freq, s.target_freq, s.power, s.target_current, s.zsc_integ,
            s.fault, s.raw_a1, s.raw_b1, s.raw_c1, s.raw_a2, s.raw_b2, s.raw_c2, s.isr_ticks, s.calibrated,
            s.id, s.iq, s.vd, s.vq, s.vd_raw, s.vq_raw, s.wr, s.theta,
            s.mcu_ts, s.id_t, s.iq_t, s.slip, s.psi, s.te, s.isr_hz, s.txd, s.run, s.foc_state,
            s.sat, s.pi_id_i, s.pi_iq_i, s.pi_spd_i, s.isr_us_avg, s.isr_us_max,
            s.p_elec, s.q_elec, s.e_alpha, s.e_beta,
            s.kdt, s.dtc_corr, s.dtc_enorm,
            s.dtc_enabled, s.dtc_compensate, s.dtc_converged, s.dtc_hold,
        ])
        self._csv_file.flush()

    def _update_capture_status(self):
        if not self.link.burst_ready and self.link.burst_progress > 0:
            self.cap_status.config(text="rx %d%%" % int(self.link.burst_progress * 100), fg="orange")
        # Flash log status from latest telemetry
        s = self.link.latest
        if s and hasattr(s, 'flg_recs'):
            if s.flg_full:
                self.flash_status.config(text="flash: FULL %d recs" % s.flg_recs, fg="red")
            elif s.flg_en:
                self.flash_status.config(text="flash: %d recs, %d pages" % (s.flg_recs, s.flg_pages), fg="green")
            else:
                self.flash_status.config(text="flash: off (%d recs)" % s.flg_recs, fg="gray")

    def _cmd_auto_stats(self):
        """Compute automatic spinup statistics from recent history."""
        hist = list(self.link.history)
        if len(hist) < 20:
            self.stats_lbl.config(text="not enough data")
            return
        speeds = [h.freq for h in hist]
        iqs = [h.iq for h in hist]
        ids = [h.id for h in hist]
        slips = [abs(h.slip) for h in hist]
        sats = sum(1 for h in hist if h.sat > 0)
        ts0 = hist[0].ts
        ts_list = [h.ts - ts0 for h in hist]
        if np is not None:
            spd = np.array(speeds)
            t = np.array(ts_list)
            target = spd[-1] if abs(spd[-1]) > 0.1 else max(abs(spd.max()), abs(spd.min()))
            if target == 0:
                self.stats_lbl.config(text="target=0, skip")
                return
            sign = 1.0 if target > 0 else -1.0
            signed = sign * spd
            tgt_abs = abs(target)
            # Rise time: 10% to 90% of target
            i10 = np.argmax(signed >= 0.1 * tgt_abs) if np.any(signed >= 0.1 * tgt_abs) else 0
            i90 = np.argmax(signed >= 0.9 * tgt_abs) if np.any(signed >= 0.9 * tgt_abs) else len(signed)-1
            rise_time = t[i90] - t[i10] if i90 > i10 else 0.0
            # Overshoot
            peak = signed.max()
            overshoot = (peak - tgt_abs) / tgt_abs * 100.0 if tgt_abs > 0 else 0.0
            # Settling time: last time outside ±5% band
            band = 0.05 * tgt_abs
            outside = np.where(np.abs(signed - tgt_abs) > band)[0]
            settle = t[outside[-1]] if len(outside) > 0 else 0.0
            # Std of Iq and Id (last 50% of data)
            n_half = len(iqs) // 2
            iq_std = float(np.std(iqs[n_half:]))
            id_std = float(np.std(ids[n_half:]))
            txt = "rise=%.2fs  OS=%.1f%%  settle=%.2fs  Iq_std=%.3f  Id_std=%.3f  maxIq=%.1f  maxSlip=%.1f  sat=%d" % (
                rise_time, overshoot, settle, iq_std, id_std,
                max(abs(v) for v in iqs), max(slips), sats)
        else:
            txt = "maxIq=%.1f  maxSlip=%.1f  sat=%d  (install numpy for full stats)" % (
                max(abs(v) for v in iqs), max(slips), sats)
        self.stats_lbl.config(text=txt)

    def _update_plots(self):
        if self.link.fault:
            self.fault_lbl.config(text="FAULT", bg="red")
        else:
            self.fault_lbl.config(text=" OK ", bg="green")
        hist = list(self.link.history)
        if not hist: return
        n = len(hist)
        if n > self.MAX_PLOT_POINTS:
            hist = hist[-self.MAX_PLOT_POINTS:]
            n = len(hist)
        xs = list(range(n))
        ia = [s.ia for s in hist]; ib = [s.ib for s in hist]; ic = [s.ic for s in hist]
        isum = [((s.ia ** 2 + s.ib ** 2 + s.ic ** 2) ** 0.5) for s in hist]
        self.l_ia.set_data(xs, ia); self.l_ib.set_data(xs, ib); self.l_ic.set_data(xs, ic)
        self.l_sum.set_data(xs, isum)

        s = hist[-1]
        self.diag_lbl.config(text="ISR ticks:%d  calib:%d  rA1:%d rB1:%d rC1:%d rA2:%d rB2:%d rC2:%d"
                             % (s.isr_ticks, s.calibrated, s.raw_a1, s.raw_b1, s.raw_c1, s.raw_a2, s.raw_b2, s.raw_c2))
        # Refresh the live DTC state label from the latest stream sample
        # (this gives ~20 Hz updates between $DTC snapshots)
        self._update_dtc_state_label()
        for ax, data in ((self.ax_ph, ia + ib + ic), (self.ax_sum, isum)):
            ax.set_xlim(0, n - 1)
            if data:
                lo = min(data); hi = max(data)
                if lo == hi: lo -= 0.1; hi += 0.1
                pad = (hi - lo) * 0.1
                ax.set_ylim(lo - pad, hi + pad)
        self.canvas.draw_idle()

    def on_close(self):
        if self._csv_file is not None: self._csv_file.close()
        self.link.close()


def run():
    root = tk.Tk()
    app = MotorControlApp(root)

    def _close():
        app.on_close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _close)
    root.mainloop()