import tkinter as tk
from tkinter import ttk
import tkinter.filedialog as filedialog
import serial
import serial.tools.list_ports
import threading
import queue
import re
from telem_parser import parse_params as parse_telemetry_params, parse_curve as parse_telemetry_curve
import time
import os
import shutil
import csv
import subprocess
import json
from datetime import datetime
from vf_panel import VfPanel

# ═══════════════════════════════════════════════════════════════════════
#  Константы sigrok
# ═══════════════════════════════════════════════════════════════════════
SIGROK_DRIVER = "fx2lafw"

# Документированный путь к sigrok-cli (см. PROJECT_OVERVIEW.md). Если файла
# по нему нет, _resolve_sigrok_cli() подберёт альтернативу:
#   env SIGROK_CLI_PATH → документированный путь → tools/sigrok-cli (репо) → PATH.
SIGROK_CLI_PATH_DEFAULT = r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe"


def _resolve_sigrok_cli():
    """Найти sigrok-cli.exe (первый существующий кандидат, иначе PATH)."""
    candidates = []
    env_path = os.environ.get("SIGROK_CLI_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.append(SIGROK_CLI_PATH_DEFAULT)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "tools", "sigrok-cli", "sigrok-cli.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("sigrok-cli") or shutil.which("sigrok-cli.exe") or SIGROK_CLI_PATH_DEFAULT


SIGROK_CLI_PATH = _resolve_sigrok_cli()

SALE_CH_PC0 = 0; SALE_CH_PC1 = 1; SALE_CH_PA7 = 2
SALE_CH_PB0 = 3; SALE_CH_PC11 = 4; SALE_CH_PC10 = 5
SALE_CH_PC7 = 6; SALE_CH_PC6 = 7
SALEAE_PKG_AVAILABLE = False
automation = None

TLM_PREFIX_RE = re.compile(r"^@(\w+):(.*)$")
TLM_KV_RE = re.compile(r"(\w+)=(-?\d+)")

class SigrokCapture:
    """Класс-заглушка для имитации объекта capture из Saleae SDK."""
    def __init__(self, csv_path, samplerate):
        self.csv_path = csv_path
        self.samplerate = samplerate
    def wait(self):
        pass

# ═══════════════════════════════════════════════════════════════════════
#  SaleaeHelper  — полностью переписан на sigrok-cli
# ═══════════════════════════════════════════════════════════════════════

class SaleaeHelper:
    """Обёртка над sigrok-cli (замена Saleae Logic 2 Automation API)."""

    def __init__(self):
        self.available = False
        self._last_probe_time = 0
        self._tr_cache = {}
        self._tmp_dir = os.path.abspath('_sigrok_tmp')

    def _log_err(self, text):
        """Зафиксировать ошибку sigrok/saleae в файл logs/sigrok_errors.log + консоль."""
        try:
            os.makedirs("logs", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join("logs", "sigrok_errors.log"), "a", encoding="utf-8") as f:
                f.write(f"{ts} {text}\n")
        except Exception:
            pass
        print(f"[Sigrok] {text}")

    def _clean_tmp(self):
        if os.path.exists(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        os.makedirs(self._tmp_dir, exist_ok=True)

    def probe_async(self, callback, tk_root=None, force=False):
        if not force and self.available and (time.time() - self._last_probe_time) < 30:
            if tk_root: tk_root.after(0, lambda: callback(True))
            else: callback(True)
            return

        def worker():
            try:
                # Закрываем Saleae Logic 2, если он запущен
                if os.name == 'nt':
                    subprocess.run('taskkill /F /IM Logic.exe', shell=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run('taskkill /F /IM "Saleae Logic.exe"', shell=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(0.5)
                result = subprocess.run([SIGROK_CLI_PATH, "--driver", SIGROK_DRIVER, "--scan"],
                                        capture_output=True, text=True, timeout=5)
                output = result.stdout + result.stderr
                print(f"[Sigrok] Scan: {SIGROK_DRIVER in output and 'No devices' not in output}")
                if SIGROK_DRIVER in output and "No devices" not in output:
                    self.available = True
                    self._last_probe_time = time.time()
                else:
                    self.available = False
            except Exception as e:
                self._log_err(f"probe FAILED: {e}")
                if isinstance(e, FileNotFoundError):
                    self._log_err("  └─ Причина: sigrok-cli.exe не найден. Проверь SIGROK_CLI_PATH "
                                  f"({SIGROK_CLI_PATH}) или установи sigrok-cli: https://sigrok.org/wiki/Downloads")
                elif isinstance(e, subprocess.TimeoutExpired):
                    self._log_err("  └─ Причина: таймаут сканирования sigrok — устройство не отвечает")
                else:
                    self._log_err(f"  └─ Причина: {e}")
                self.available = False
            if tk_root is not None:
                tk_root.after(0, lambda: callback(self.available))
            else:
                callback(self.available)
        threading.Thread(target=worker, daemon=True).start()

    def capture_sync(self, digital_chs=None, analog_chs=None, duration_s=0.3, sample_rate=24_000_000,
                     ready_event=None):
        if not self.available:
            return None
        self._clean_tmp()
        if sample_rate >= 1_000_000:
            sr_str = f"{sample_rate // 1_000_000}m"
        elif sample_rate >= 1_000:
            sr_str = f"{sample_rate // 1_000}k"
        else:
            sr_str = str(sample_rate)
        if digital_chs and len(digital_chs) > 0:
            ch_str = ",".join(f"D{ch}" for ch in digital_chs)
            csv_path = os.path.join(self._tmp_dir, "digital.csv")
            sr_d = min(sample_rate, 8_000_000)  # fx2lafw max practical rate
            actual_rate = sr_d
            sr_str = f"{sr_d // 1_000_000}m"
            cmd = [SIGROK_CLI_PATH, "--driver", SIGROK_DRIVER,
                   "--config", f"samplerate={sr_str}",
                   "--channels", ch_str,
                   "--time", str(int(duration_s * 1000)),
                   "-O", "csv", "-o", csv_path]
        elif analog_chs and len(analog_chs) > 0:
            ch_str = ",".join(f"A{ch}" for ch in analog_chs)
            csv_path = os.path.join(self._tmp_dir, "analog.csv")
            sr_a = min(sample_rate, 1_000_000)
            actual_rate = sr_a
            sr_str = f"{sr_a // 1_000}k"
            cmd = [SIGROK_CLI_PATH, "--driver", SIGROK_DRIVER,
                   "--config", f"samplerate={sr_str}",
                   "--channels", ch_str,
                   "--time", str(int(duration_s * 1000)),
                   "-O", "csv", "-o", csv_path]
        else:
            return None
        try:
            print(f"[Sigrok] {' '.join(cmd)}")
            # Сигнализируем после подготовки команды и непосредственно перед
            # запуском sigrok-cli; вызывающий поток только после этого может
            # отправлять vf= и формировать аппаратный фронт PB6.
            if ready_event is not None:
                ready_event.set()
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=duration_s*10+60)
            if os.path.exists(csv_path):
                # GUI-05: sr_d не существует в analog-ветке (UnboundLocalError)
                return SigrokCapture(csv_path, actual_rate)
            return None
        except Exception as e:
            if ready_event is not None:
                ready_event.set()
            self._log_err(f"Capture error: {e}")
            return None

    def get_transitions(self, capture, channel_idx):
        if not hasattr(capture, 'csv_path'):
            return []
        cid = id(capture)
        cache = self._tr_cache.get(cid)
        if cache is not None:
            return cache.get(channel_idx, [])
        t_per_sample_ns = 1e9 / capture.samplerate
        cache = {ch: [] for ch in range(16)}
        last_vals = {}
        sidx = 0
        try:
            with open(capture.csv_path, 'r', errors='ignore') as f:
                for line in f:
                    if line.startswith(';') or not line.strip() or 'logic' in line or 'Time' in line:
                        continue
                    vals = line.strip().split(',')
                    tn = sidx * t_per_sample_ns
                    sidx += 1
                    for ci, vs in enumerate(vals):
                        if ci >= 16: break
                        try:
                            v = int(float(vs))
                        except ValueError:
                            continue
                        if last_vals.get(ci) != v:
                            cache[ci].append((tn, v))
                            last_vals[ci] = v
        except Exception as e:
            print(f"[Sigrok] CSV parse error: {e}")
        self._tr_cache = {cid: cache}
        return cache.get(channel_idx, [])

    def _export_digital_csv(self, capture, tmp_dir):
        return capture.csv_path if hasattr(capture, 'csv_path') else None

    def _export_csv(self, capture, is_analog, channel_idx, tmp_dir):
        return capture.csv_path if hasattr(capture, 'csv_path') else None

    def measure_freq(self, capture, ch):
        tr = self.get_transitions(capture, ch)
        rising = [t for t, v in tr if v == 1]
        if len(rising) < 2: return None
        periods = [rising[i+1]-rising[i] for i in range(len(rising)-1)]
        if not periods: return None
        avg = sum(periods)/len(periods)
        return 1e9/avg if avg > 0 else None

    def measure_duty(self, capture, ch):
        tr = self.get_transitions(capture, ch)
        if len(tr) < 2: return None
        high = total = 0.0
        for i in range(len(tr)-1):
            dt = tr[i+1][0]-tr[i][0]
            if tr[i][1] == 1: high += dt
            total += dt
        return high/total if total > 0 else None

    def measure_deadtime(self, capture, ch_high, ch_low):
        tr_h = self.get_transitions(capture, ch_high)
        tr_l = self.get_transitions(capture, ch_low)
        if len(tr_h) < 3 or len(tr_l) < 3:
            return None
        rises_h = [t for t, v in tr_h if v == 1]
        if len(rises_h) < 3: return None
        period = (rises_h[-1] - rises_h[0]) / (len(rises_h) - 1) * 2
        events = [(t, 'H', v) for t, v in tr_h] + [(t, 'L', v) for t, v in tr_l]
        events.sort(key=lambda x: x[0])
        dt_hf, dt_lf = [], []
        st_h, st_l = None, None
        dt_start, dt_type = None, None
        for t, ch, v in events:
            if ch == 'H':
                if st_h is not None and st_h == 1 and v == 0 and st_l is not None and st_l == 0:
                    dt_start, dt_type = t, 'H'
                st_h = v
            else:
                if st_l is not None and st_l == 1 and v == 0 and st_h is not None and st_h == 0:
                    dt_start, dt_type = t, 'L'
                st_l = v
            if dt_start is not None:
                if (dt_type == 'H' and ch == 'L' and v == 1) or (dt_type == 'L' and ch == 'H' and v == 1):
                    gap = t - dt_start
                    if gap < period * 0.25:
                        if dt_type == 'H': dt_hf.append(gap)
                        else: dt_lf.append(gap)
                    dt_start, dt_type = None, None
        if not dt_hf or not dt_lf:
            return None
        return (sum(dt_hf)/len(dt_hf), sum(dt_lf)/len(dt_lf))

    def measure_voltage(self, analog_channel, duration_s=0.3):
        if not self.available: return None
        capture = self.capture_sync(analog_chs=[analog_channel], duration_s=duration_s, sample_rate=1_000_000)
        if not capture: return None
        volts = []
        try:
            with open(capture.csv_path, 'r', errors='ignore') as f:
                for line in f:
                    if line.startswith(';') or line.startswith('voltage') or not line.strip():
                        continue
                    try:
                        parts = line.strip().split(',')
                        v = parts[0] if len(parts) == 1 else parts[analog_channel]
                        volts.append(float(v))
                    except ValueError:
                        continue
        except Exception as e:
            print(f"[Sigrok] Analog parse error: {e}")
        finally:
            if os.path.exists(self._tmp_dir): shutil.rmtree(self._tmp_dir, ignore_errors=True)
        return sum(volts)/len(volts) if volts else None
class SaleaeConnectFrame(ttk.Frame):
    def __init__(self, parent, saleae, on_status_change=None):
        super().__init__(parent)
        self.saleae = saleae
        self.on_status_change = on_status_change
        self.btn = ttk.Button(self, text="🔌 Sigrok", command=self._probe)
        self.btn.pack(side=tk.LEFT, padx=5)
        self.indicator = tk.Label(self, text="●", fg="gray", font=("Arial", 14))
        self.indicator.pack(side=tk.LEFT, padx=5)
        self.status_label = ttk.Label(self, text="Not checked", foreground="gray")
        self.status_label.pack(side=tk.LEFT, padx=5)
        self._update_view()

    def _probe(self):
        self.btn.config(state=tk.DISABLED, text="⏳")
        self.saleae.probe_async(self._probe_done, tk_root=self, force=True)

    def _probe_done(self, ok):
        self.btn.config(state=tk.NORMAL, text="🔌 Sigrok")
        self._update_view()
        if self.on_status_change: self.on_status_change(ok)

    # _probe_direct removed (sigrok)

    def _update_view(self):
        if self.saleae.available:
            self.indicator.config(fg="green")
            self.status_label.config(text="Sigrok Ready", foreground="green")
        else:
            self.indicator.config(fg="red")
            self.status_label.config(text="No Sigrok device", foreground="red")

# ═══════════════════════════════════════════════════════════════════════
#  PWMTab
# ═══════════════════════════════════════════════════════════════════════

class PWMTab(ttk.Frame):
    # Разводка щупов sigrok (16-канальный анализатор, обновлено после
    # перехода на PWM-энкодер): D0..D11 — все 12 ШИМ-сигналов (оба
    # инвертора, включая W-фазы, которые раньше не были подключены),
    # D13 — аппаратный sync-триггер PB6 (см. vf_panel.py TRIGGER_SIGROK_CHANNEL).
    # Подключение подтверждено пользователем: Ch1..Ch12 → D0..D11
    # последовательно, по порядку этого списка.
    # Битовая маска = схема прошивки (src/pwm.c PWM_SetMask):
    #   0x01=CC1E(PC0), 0x02=CC1NE(PA7), 0x04=CC2E(PC1),
    #   0x08=CC2NE(PB0), 0x10=CC3E(PC2), 0x20=CC3NE(PB1)
    # D-индекс = физический канал sigrok.
    CHANNELS = [
        ("Ch1","PC0 HIN_U1",0x01,0),  # CC1E,  D0
        ("Ch2","PC1 HIN_V1",0x04,1),  # CC2E,  D1
        ("Ch3","PA7 LIN_U1",0x02,2),  # CC1NE, D2
        ("Ch4","PB0 LIN_V1",0x08,3),  # CC2NE, D3
        ("Ch5","PC2 HIN_W1",0x10,4),  # CC3E,  D4
        ("Ch6","PB1 LIN_W1",0x20,5),  # CC3NE, D5
    ]

    CHANNELS_INV2 = [
        ("Ch7","PC11 LIN_V2",0x08,6),  # CC2NE, D6
        ("Ch8","PC10 LIN_U2",0x02,7),  # CC1NE, D7
        ("Ch9","PC7 HIN_V2",0x04,8),   # CC2E,  D8
        ("Ch10","PC6 HIN_U2",0x01,9),  # CC1E,  D9
        ("Ch11","PC8 HIN_W2",0x10,10), # CC3E,  D10
        ("Ch12","PC12 LIN_W2",0x20,11),# CC3NE, D11
    ]

    def __init__(self, parent, send_fn, saleae=None):
        super().__init__(parent)
        self.send=send_fn; self.saleae=saleae
        self.columnconfigure(0,weight=1); self.columnconfigure(1,weight=1); self.columnconfigure(2,weight=1)
        self._build_channels_panel(); self._build_params_panel(); self._build_status_panel()
        self._build_saleae_panel()
        self.vf_panel = VfPanel(self, self.send, self.saleae)
        self.vf_panel.build(self)
        self.after(200,lambda:self.send("p?"))

    def _build_channels_panel(self):
        f=ttk.LabelFrame(self,text="Channels")
        f.grid(row=0,column=0,sticky="nsew",padx=5,pady=5)
        
        # --- Inverter 1 ---
        bf=ttk.Frame(f); bf.grid(row=0,column=0,columnspan=3,sticky="ew",pady=(0,5))
        ttk.Label(bf,text="TIM1 (Inv1):",font=("Arial",9,"bold")).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf,text="Select All",command=self._select_all).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf,text="Clear All",command=self._clear_all).pack(side=tk.LEFT,padx=2)
        self.ch_vars=[]; self.ch_indicators=[]
        for i,(nm,pn,_,_) in enumerate(self.CHANNELS):
            r=i+1; var=tk.BooleanVar(value=False); self.ch_vars.append(var)
            ttk.Checkbutton(f,variable=var,command=self._update_mask_preview).grid(row=r,column=0,sticky="w",padx=2)
            ind=tk.Label(f,text="\u25cf",fg="red",font=("Arial",14)); ind.grid(row=r,column=1,padx=4); self.ch_indicators.append(ind)
            ttk.Label(f,text=f"{nm}\n{pn}",justify=tk.LEFT).grid(row=r,column=2,sticky="w",padx=2)
            
        # --- Inverter 2 ---
        r_sep = len(self.CHANNELS) + 1
        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r_sep, column=0, columnspan=3, sticky="ew", pady=5)
        
        bf2=ttk.Frame(f); bf2.grid(row=r_sep+1,column=0,columnspan=3,sticky="ew",pady=(0,5))
        ttk.Label(bf2,text="TIM8 (Inv2):",font=("Arial",9,"bold")).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf2,text="Select All",command=self._select_all_inv2).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf2,text="Clear All",command=self._clear_all_inv2).pack(side=tk.LEFT,padx=2)
        
        self.ch_vars2=[]; self.ch_indicators2=[]
        for i,(nm,pn,_,_) in enumerate(self.CHANNELS_INV2):
            r=r_sep+i+2; var=tk.BooleanVar(value=False); self.ch_vars2.append(var)
            ttk.Checkbutton(f,variable=var,command=self._update_mask_preview).grid(row=r,column=0,sticky="w",padx=2)
            ind=tk.Label(f,text="\u25cf",fg="red",font=("Arial",14)); ind.grid(row=r,column=1,padx=4); self.ch_indicators2.append(ind)
            ttk.Label(f,text=f"{nm}\n{pn}",justify=tk.LEFT).grid(row=r,column=2,sticky="w",padx=2)
            
        self.mask_preview=ttk.Label(f,text="mask = 0x00",foreground="gray")
        self.mask_preview.grid(row=r_sep+len(self.CHANNELS_INV2)+2,column=0,columnspan=3,pady=(10,0))
        f.columnconfigure(2,weight=1)

    def _build_params_panel(self):
        f=ttk.LabelFrame(self,text="Parameters")
        f.grid(row=0,column=1,sticky="nsew",padx=5,pady=5)
        ttk.Label(f,text="ARR (period):").grid(row=0,column=0,sticky="w",padx=5,pady=3)
        self.arr_var=tk.IntVar(value=99)
        self.arr_spin=ttk.Spinbox(f,from_=20,to=500,textvariable=self.arr_var,width=8,command=self._update_freq)
        self.arr_spin.grid(row=0,column=1,sticky="w",padx=5)
        self.arr_spin.bind("<KeyRelease>",lambda e:self._update_freq())
        ttk.Label(f,text="Duty (%):").grid(row=1,column=0,sticky="w",padx=5,pady=3)
        self.duty_var=tk.IntVar(value=15)
        ttk.Spinbox(f,from_=1,to=99,textvariable=self.duty_var,width=8).grid(row=1,column=1,sticky="w",padx=5)
        ttk.Label(f,text="Dead-time (ns):").grid(row=2,column=0,sticky="w",padx=5,pady=3)
        self.dt_var=tk.IntVar(value=1500)
        ttk.Spinbox(f,from_=0,to=3500,increment=50,textvariable=self.dt_var,width=8).grid(row=2,column=1,sticky="w",padx=5)
        ttk.Label(f,text="Frequency:").grid(row=3,column=0,sticky="w",padx=5,pady=(10,3))
        self.freq_label=ttk.Label(f,text="\u2014 kHz",foreground="blue",font=("Arial",10,"bold"))
        self.freq_label.grid(row=3,column=1,sticky="w",padx=5)
        bf=ttk.Frame(f); bf.grid(row=4,column=0,columnspan=2,pady=10,sticky="ew")
        ttk.Button(bf,text="\u25b6 Start PWM",command=self._start_pwm).pack(side=tk.LEFT,padx=3)
        ttk.Button(bf,text="\u25a0 Stop PWM",command=self._stop_pwm).pack(side=tk.LEFT,padx=3)
        ttk.Button(bf,text="\u27f3 Refresh",command=self._refresh_status).pack(side=tk.LEFT,padx=3)
        ttk.Button(bf,text="\U0001f50d PDump",command=lambda:self.send("pdump")).pack(side=tk.LEFT,padx=3)
        f.columnconfigure(1,weight=1); self._update_freq()

    def _build_status_panel(self):
        f=ttk.LabelFrame(self,text="Status")
        f.grid(row=0,column=2,sticky="nsew",padx=5,pady=5)
        self.cr1_label=ttk.Label(f,text="CR1: 0x----",font=("Consolas",10))
        self.cr1_label.grid(row=0,column=0,sticky="w",padx=5,pady=2)
        self.ccer_label=ttk.Label(f,text="CCER: 0x----",font=("Consolas",10))
        self.ccer_label.grid(row=1,column=0,sticky="w",padx=5,pady=2)
        self.bdtr_label=ttk.Label(f,text="BDTR: 0x----",font=("Consolas",10))
        self.bdtr_label.grid(row=2,column=0,sticky="w",padx=5,pady=2)
        self.cnt_label=ttk.Label(f,text="CNT: ----",font=("Consolas",10))
        self.cnt_label.grid(row=3,column=0,sticky="w",padx=5,pady=2)
        ttk.Separator(f,orient=tk.HORIZONTAL).grid(row=4,column=0,sticky="ew",pady=10,padx=5)
        self.moe_label=ttk.Label(f,text="MOE: \u2014",font=("Arial",10,"bold"))
        self.moe_label.grid(row=5,column=0,sticky="w",padx=5,pady=2)
        self.cen_label=ttk.Label(f,text="CEN: \u2014",font=("Arial",10,"bold"))
        self.cen_label.grid(row=6,column=0,sticky="w",padx=5,pady=2)
        f.columnconfigure(0,weight=1)

    def _build_saleae_panel(self):
        f=ttk.LabelFrame(self,text="Sigrok \u2014 Auto Test")
        f.grid(row=1,column=0,columnspan=3,sticky="ew",padx=5,pady=5)
        self.sf=SaleaeConnectFrame(f,self.saleae,on_status_change=self._on_saleae_status)
        self.sf.pack(side=tk.LEFT,padx=5,pady=5)
        
        # Inv1 Buttons
        f1 = ttk.Frame(f); f1.pack(side=tk.LEFT, padx=10)
        ttk.Label(f1, text="Inverter 1:").pack(side=tk.LEFT)
        self.btn_auto=ttk.Button(f1,text="\u26a1 Auto Test",command=self._auto_test_all)
        self.btn_auto.pack(side=tk.LEFT,padx=5,pady=5)
        self.btn_dt=ttk.Button(f1,text="\ud83d\udccf Dead-Time",command=self._measure_deadtime)
        self.btn_dt.pack(side=tk.LEFT,padx=5,pady=5)
        
        # Inv2 Buttons
        f2 = ttk.Frame(f); f2.pack(side=tk.LEFT, padx=10)
        ttk.Label(f2, text="Inverter 2:").pack(side=tk.LEFT)
        self.btn_auto2=ttk.Button(f2,text="\u26a1 Auto Test",command=self._auto_test_all_inv2)
        self.btn_auto2.pack(side=tk.LEFT,padx=5,pady=5)
        self.btn_dt2=ttk.Button(f2,text="\ud83d\udccf Dead-Time",command=self._measure_deadtime_inv2)
        self.btn_dt2.pack(side=tk.LEFT,padx=5,pady=5)
        
        self._update_saleae_buttons()

    def _on_saleae_status(self,ok): self._update_saleae_buttons()
    def _update_saleae_buttons(self):
        st="normal" if (self.saleae and self.saleae.available) else "disabled"
        self.btn_auto.config(state=st); self.btn_dt.config(state=st)
        self.btn_auto2.config(state=st); self.btn_dt2.config(state=st)

    def _get_mask(self):
        m=0
        for v,(_,_,b,_) in zip(self.ch_vars,self.CHANNELS):
            if v.get(): m|=b
        return m
    def _update_mask_preview(self): self.mask_preview.config(text=f"mask = 0x{self._get_mask():02X}")
    
    def _select_all(self):
        for v in self.ch_vars: v.set(True)
        self._update_mask_preview()
    def _clear_all(self):
        for v in self.ch_vars: v.set(False)
        self._update_mask_preview()
        
    def _select_all_inv2(self):
        for v in self.ch_vars2: v.set(True)
        self._update_mask_preview()   # GUI-12
    def _clear_all_inv2(self):
        for v in self.ch_vars2: v.set(False)
        self._update_mask_preview()   # GUI-12

    def _update_freq(self):
        try: 
            root=self.winfo_toplevel()
            tclk=getattr(root,'tclk',10_300_000)
            self.freq_label.config(text=f"{tclk/(self.arr_var.get()+1)/2/1000:.2f} kHz")
        except: pass
    def _start_pwm(self):
        # OEW: маска из галочек дополняется до полных пар (HIN+LIN) — голый HIN
        # оставляет узел второго инвертора плавающим, ток через обмотку не течёт.
        m = self._get_mask()
        if m & 0x03: m |= 0x03
        if m & 0x0C: m |= 0x0C
        if m & 0x30: m |= 0x30
        self.send(f"p={self.arr_var.get()},{self.duty_var.get()},{self.dt_var.get()},{m}")
    def _stop_pwm(self):
        # Ревью GUI-01: p=...,0 в прошивке превращается в маску 0x3F (ВСЕ
        # каналы + CEN/MOE) — «stop» включал бы debug-PWM. Безопасный стоп —
        # команда '0' (FOC_Stop → PWM_Disable: CEN/MOE off, CCR=mid, EN LOW).
        self.send("0")
    def _refresh_status(self): self.send("p?")
    def set_indicators(self, m):
        for ind,(_,_,b,_) in zip(self.ch_indicators,self.CHANNELS): ind.config(fg="green" if m&b else "red")
    def _log_local(self,text,tag="received",update_status=False):
        print(f"[PWM] {text}")
        try:
            root=self.winfo_toplevel()
            fn=lambda t=text,g=tag: root._log(t,g) if hasattr(root,'_log') else None
            if threading.current_thread() is threading.main_thread():
                fn()
            else:
                root.after(0,fn)
            if update_status:
                fn2=lambda t=text: root._set_status(t) if hasattr(root,'_set_status') else None
                if threading.current_thread() is threading.main_thread():
                    fn2()
                else:
                    root.after(0,fn2)
        except Exception as e:
            print(f"[_log_local ERROR] {text} (error={e})")

    # --- Auto Test Inv 1 ---
    def _auto_test_all(self):
        self._log_local("Starting Auto Test Inv1...","sent")
        self.btn_auto.config(state=tk.DISABLED,text="\u23f3 Testing...")
        def fail():
            self.after(0,lambda: self._log_local("Sigrok: Inv1 operation failed or timed out","error"))
            self.after(0,lambda: self.btn_auto.config(state=tk.NORMAL,text="\u26a1 Auto Test"))
        def worker():
            if not self.saleae or not self.saleae.available:
                self.after(0,fail); return
            # Включить PWM с маской из отмеченных галочек (иначе захватываем старый режим)
            # OEW (open-end winding): обмотка фазы X между узлом Inv1 и узлом Inv2.
            # Ток течёт только при ПОЛНОЙ паре: HIN одного инвертора + LIN другого.
            # Маска одна на оба таймера → если отмечен любой канал фазы, включаем
            # оба бита фазы (HIN+LIN): 0x01↔0x02, 0x04↔0x08, 0x10↔0x20.
            # Голый HIN без LIN оставляет узел второго инвертора плавающим → тока нет.
            mask1 = self._get_mask()
            if mask1 & 0x03: mask1 |= 0x03
            if mask1 & 0x0C: mask1 |= 0x0C
            if mask1 & 0x30: mask1 |= 0x30
            self.send(f"p={self.arr_var.get()},{self.duty_var.get()},{self.dt_var.get()},{mask1}")
            time.sleep(0.3)
            capture = self.saleae.capture_sync(digital_chs=list(range(8)), duration_s=0.5)
            if not capture:
                self.after(0,fail); return
            # ... rest of worker
            arr,dp=self.arr_var.get(),self.duty_var.get()
            root=self.winfo_toplevel()
            tclk=getattr(root,'tclk',10_000_000)
            ef=tclk/(arr+1)/2; ed=dp/100.0
            # OEW: duty распределяется между инверторами как в FOC:
            # d1 = 50+duty/2 (Inv1), d2 = 50−duty/2 (Inv2); dead-time режет оба фронта.
            # Inv1 (TIM1, mode 1): HIN = 0.5+ed/2 − dt, LIN = 0.5−ed/2 − dt.
            period_ns = 2*(arr+1)/tclk*1e9
            dt_pct = self.dt_var.get()/period_ns
            exp_hin1 = 0.5 + ed/2 - dt_pct
            exp_lin1 = 0.5 - ed/2 - dt_pct
            results=[]
            for v,(nm,pn,_,sc) in zip(self.ch_vars,self.CHANNELS):
                if not v.get(): continue
                f=self.saleae.measure_freq(capture,sc)
                d=self.saleae.measure_duty(capture,sc)
                if f is None or d is None:
                    results.append((False,f"FAIL: {pn} \u2014 no signal")); continue
                exp_d = exp_hin1 if "HIN" in pn else exp_lin1
                fe=abs(f-ef)/ef if ef>0 else 1; de=abs(d-exp_d)
                okf=fe<=0.10 and de<=0.10
                s="PASS" if okf else "FAIL"
                results.append((okf,f"{s}: {pn}  {f:.1f}Hz (exp {ef:.1f}, err {fe*100:.1f}%)  {d*100:.1f}% (exp {exp_d*100:.1f}%, err {de*100:.1f}%)"))
            self.after(0,lambda: self._auto_test_done(results))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_test_done(self,results):
        self.btn_auto.config(state=tk.NORMAL,text="\u26a1 Auto Test")
        pc=sum(1 for ok,_ in results if ok); fc=len(results)-pc
        for ok,msg in results: self._log_local(msg,"meas" if ok else "error")
        s=f"=== Result Inv1: {pc} PASS, {fc} FAIL ==="
        self._log_local(s,"meas" if fc==0 else "error",update_status=True)

    # --- Auto Test Inv 2 ---
    def _auto_test_all_inv2(self):
        self._log_local("Starting Auto Test Inv2...","sent")
        self.btn_auto2.config(state=tk.DISABLED,text="\u23f3 Testing...")
        def fail():
            self.after(0,lambda: self._log_local("Sigrok: Inv2 operation failed or timed out","error"))
            self.after(0,lambda: self.btn_auto2.config(state=tk.NORMAL,text="\u26a1 Auto Test"))
        def worker():
            if not self.saleae or not self.saleae.available:
                self.after(0,fail); return
            # Включить PWM с маской из отмеченных галочек Inv2 (иначе захватываем старый режим)
            # OEW: обмотка фазы X между узлом Inv1 и узлом Inv2. Ток течёт только при
            # ПОЛНОЙ паре (HIN+LIN). Маска одна на оба таймера → если отмечен любой
            # канал фазы, включаем оба бита фазы. Голый CCxNE (без CCxE) даёт нештатный
            # сигнал (100 кГц, без инверсии) — поэтому пары обязательны.
            mask2 = 0
            for v,(nm,pn,b,sc) in zip(self.ch_vars2,self.CHANNELS_INV2):
                if v.get(): mask2 |= b
            if mask2 & 0x03: mask2 |= 0x03
            if mask2 & 0x0C: mask2 |= 0x0C
            if mask2 & 0x30: mask2 |= 0x30
            self.send(f"p={self.arr_var.get()},{self.duty_var.get()},{self.dt_var.get()},{mask2}")
            time.sleep(0.3)
            capture = self.saleae.capture_sync(digital_chs=list(range(6, 12)), duration_s=0.5)
            if not capture:
                self.after(0,fail); return
            arr,dp=self.arr_var.get(),self.duty_var.get()
            root=self.winfo_toplevel()
            tclk=getattr(root,'tclk',10_000_000)
            ef=tclk/(arr+1)/2; ed=dp/100.0
            # OEW: duty распределяется между инверторами как в FOC:
            # d1 = 50+duty/2 (Inv1), d2 = 50−duty/2 (Inv2); dead-time режет оба фронта.
            # Inv2 (TIM8, mode 1): HIN = 0.5−ed/2 − dt, LIN = 0.5+ed/2 − dt.
            period_ns = 2*(arr+1)/tclk*1e9
            dt_pct = self.dt_var.get()/period_ns
            exp_hin2 = 0.5 - ed/2 - dt_pct
            exp_lin2 = 0.5 + ed/2 - dt_pct
            results=[]
            for v,(nm,pn,_,sc) in zip(self.ch_vars2,self.CHANNELS_INV2):
                if not v.get(): continue
                f=self.saleae.measure_freq(capture,sc)
                d=self.saleae.measure_duty(capture,sc)
                if f is None or d is None:
                    results.append((False,f"FAIL: {pn} \u2014 no signal")); continue
                exp_d = exp_hin2 if "HIN" in pn else exp_lin2
                fe=abs(f-ef)/ef if ef>0 else 1; de=abs(d-exp_d)
                okf=fe<=0.10 and de<=0.10
                s="PASS" if okf else "FAIL"
                results.append((okf,f"{s}: {pn}  {f:.1f}Hz (exp {ef:.1f}, err {fe*100:.1f}%)  {d*100:.1f}% (exp {exp_d*100:.1f}%, err {de*100:.1f}%)"))
            self.after(0,lambda: self._auto_test_done_inv2(results))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_test_done_inv2(self,results):
        self.btn_auto2.config(state=tk.NORMAL,text="\u26a1 Auto Test")
        pc=sum(1 for ok,_ in results if ok); fc=len(results)-pc
        for ok,msg in results: self._log_local(msg,"meas" if ok else "error")
        s=f"=== Result Inv2: {pc} PASS, {fc} FAIL ==="
        self._log_local(s,"meas" if fc==0 else "error",update_status=True)

    # --- Dead-Time Inv 1 ---
    def _measure_deadtime(self):
        if not self.saleae or not self.saleae.available: return
        self.btn_dt.config(state=tk.DISABLED,text="\u23f3 Measuring...")
        threading.Thread(target=self._measure_dt_worker,daemon=True).start()

    def _measure_dt_worker(self):
        try:
            capture=self.saleae.capture_sync(digital_chs=list(range(8)),duration_s=0.5)
            if not capture:
                self.after(0,lambda: self._log_local("Sigrok: Inv1 capture returned None","error"))
                self.after(0,lambda: self.btn_dt.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time"))
                return
        except Exception as _e:
            error_text = str(_e)
            self.after(0,lambda: self._log_local(f"Sigrok error: {error_text}","error"))
            self.after(0,lambda: self.btn_dt.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time"))
            return
        pairs=[("U",SALE_CH_PC0,SALE_CH_PA7),("V",SALE_CH_PC1,SALE_CH_PB0)]  # W (PC2/PB1) не подключен
        results=[]
        for ph,ch,cl in pairs:
            r=self.saleae.measure_deadtime(capture,ch,cl)
            if r is None: results.append((False,f"Phase {ph} (Inv1): no valid transitions"))
            else: results.append((True,f"Phase {ph} (Inv1): dt_rise={r[0]:.0f} ns, dt_fall={r[1]:.0f} ns"))
        self.after(0,lambda: self._measure_dt_done(results))

    def _measure_dt_done(self,results):
        self.btn_dt.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time")
        self._log_local("=== Dead-Time Measurement Inv1 ===","sent")
        for ok,m in results: self._log_local(m,"meas" if ok else "error")

    # --- Dead-Time Inv 2 ---
    def _measure_deadtime_inv2(self):
        if not self.saleae or not self.saleae.available: return
        self.btn_dt2.config(state=tk.DISABLED,text="\u23f3 Measuring...")
        threading.Thread(target=self._measure_dt_worker_inv2,daemon=True).start()

    def _measure_dt_worker_inv2(self):
        try:
            capture=self.saleae.capture_sync(digital_chs=list(range(6, 12)),duration_s=0.5)
            if not capture:
                self.after(0,lambda: self._log_local("Sigrok: Inv2 capture returned None","error"))
                self.after(0,lambda: self.btn_dt2.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time"))
                return
        except Exception as _e:
            error_text = str(_e)
            self.after(0,lambda: self._log_local(f"Sigrok error: {error_text}","error"))
            self.after(0,lambda: self.btn_dt2.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time"))
            return
        
        pairs=[("U",9,7),("V",8,6)]  # HIN/LIN Inv2: D9/D7 (PC6/PC10), D8/D6 (PC7/PC11); W не подключен
        results=[]
        for ph,ch,cl in pairs:
            r=self.saleae.measure_deadtime(capture,ch,cl)
            if r is None: results.append((False,f"Phase {ph} (Inv2): no valid transitions"))
            else: results.append((True,f"Phase {ph} (Inv2): dt_rise={r[0]:.0f} ns, dt_fall={r[1]:.0f} ns"))
        self.after(0,lambda: self._measure_dt_done_inv2(results))

    def _measure_dt_done_inv2(self,results):
        self.btn_dt2.config(state=tk.NORMAL,text="\ud83d\udccf Dead-Time")
        self._log_local("=== Dead-Time Measurement Inv2 ===","sent")
        for ok,m in results: self._log_local(m,"meas" if ok else "error")

    def on_telemetry(self,prefix,data):
        if prefix!="PWM": return
        if "arr" in data and "duty" in data and "dt" in data:
            try:
                if data.get("arr")!=self.arr_var.get(): self.arr_var.set(data["arr"])
                if data.get("duty")!=self.duty_var.get(): self.duty_var.set(data["duty"])
                self._update_freq()
            except: pass
            return
        if "CR1" in data and "CCER" in data and "BDTR" in data and "CNT" in data:
            c1,cc,bd,cn=data["CR1"],data["CCER"],data["BDTR"],data["CNT"]
            self.cr1_label.config(text=f"CR1:  0x{c1:04X}")
            self.ccer_label.config(text=f"CCER: 0x{cc:04X}")
            self.bdtr_label.config(text=f"BDTR: 0x{bd:04X}")
            self.cnt_label.config(text=f"CNT:  {cn}")
            moe=(bd>>15)&1; cen=c1&1
            self.moe_label.config(text="MOE: ON" if moe else "MOE: OFF",foreground="green" if moe else "red")
            self.cen_label.config(text="CEN: ON" if cen else "CEN: OFF",foreground="green" if cen else "red")
            self.set_indicators(cc&0x3F)
class ADCTab(ttk.Frame):
    def __init__(self,parent,send_fn,saleae=None):
        super().__init__(parent); self.send=send_fn; self.saleae=saleae; self.data_buffer=[]; self._build_ui()
    def _build_ui(self):
        ctrl=ttk.LabelFrame(self,text="ADC Control")
        ctrl.pack(fill=tk.X,padx=5,pady=5)
        ttk.Button(ctrl,text="📸 Single Read",command=lambda:self.send("a")).pack(side=tk.LEFT,padx=5,pady=5)
        ttk.Label(ctrl,text="Stream (ms):").pack(side=tk.LEFT,padx=5)
        self.sv=tk.IntVar(value=100)
        ttk.Spinbox(ctrl,from_=50,to=1000,textvariable=self.sv,width=6).pack(side=tk.LEFT)
        ttk.Button(ctrl,text="▶ Start",command=self._start_stream).pack(side=tk.LEFT,padx=5)
        ttk.Button(ctrl,text="■ Stop",command=lambda:self.send("a=0")).pack(side=tk.LEFT,padx=5)
        ttk.Button(ctrl,text="⚙ Calibrate",command=lambda:self.send("c")).pack(side=tk.LEFT,padx=5)
        ttk.Button(ctrl,text="⟳ Status",command=lambda:self.send("a?")).pack(side=tk.LEFT,padx=5)
        ttk.Button(ctrl,text="💾 Save CSV",command=self._save_csv).pack(side=tk.LEFT,padx=5)
        vf=ttk.LabelFrame(self,text="Current Values")
        vf.pack(fill=tk.X,padx=5,pady=5)
        self.l_i1=ttk.Label(vf,text="I1: — raw",font=("Consolas",11)); self.l_i1.grid(row=0,column=0,padx=10,pady=5,sticky="w")
        self.l_i2=ttk.Label(vf,text="I2: — raw",font=("Consolas",11)); self.l_i2.grid(row=0,column=1,padx=10,pady=5,sticky="w")
        self.l_in=ttk.Label(vf,text="Ires: — raw",font=("Consolas",11)); self.l_in.grid(row=1,column=0,padx=10,pady=5,sticky="w")
        self.l_vb=ttk.Label(vf,text="VBUS: — raw",font=("Consolas",11)); self.l_vb.grid(row=1,column=1,padx=10,pady=5,sticky="w")
        sf=ttk.LabelFrame(self,text="Saleae — ADC Capture")
        sf.pack(fill=tk.X,padx=5,pady=5)
        self.sf=SaleaeConnectFrame(sf,self.saleae,on_status_change=self._on_saleae_status)
        self.sf.pack(side=tk.LEFT,padx=5,pady=5)
        self.bc=ttk.Button(sf,text="📊 Capture ADC Waveform",command=self._capture_adc)
        self.bc.pack(side=tk.LEFT,padx=10,pady=5)
        self._update_saleae_buttons()

    def _on_saleae_status(self,ok): self._update_saleae_buttons()
    def _update_saleae_buttons(self):
        st="normal" if (self.saleae and self.saleae.available) else "disabled"
        self.bc.config(state=st)
    def _start_stream(self):
        try: self.send(f"a={self.sv.get()}")
        except: pass

    def _capture_adc(self):
        if not self.saleae or not self.saleae.available: return
        self.bc.config(state=tk.DISABLED,text="⏳ Capturing...")
        threading.Thread(target=self._capture_adc_worker,daemon=True).start()

    def _capture_adc_worker(self):
        results=[]
        for ci in range(4):
            v=self.saleae.measure_voltage(ci,0.3)
            if v is None: results.append((False,f"Analog ch{ci}: no data"))
            else: results.append((True,f"Analog ch{ci}: {v:.4f} V"))
        self.after(0,lambda: self._capture_adc_done(results))

    def _capture_adc_done(self,results):
        self.bc.config(state=tk.NORMAL,text="📊 Capture ADC Waveform")
        self._log_local("=== ADC Waveform Capture ===","sent")
        for ok,m in results: self._log_local(m,"meas" if ok else "error")

    def _save_csv(self):
        if not self.data_buffer:
            self._log_local("No data to save","error"); self._set_status("No data to save"); return
        fp=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")],title="Save ADC data")
        if not fp: return
        try:
            with open(fp,'w',newline='') as f:
                w=csv.writer(f); w.writerow(['timestamp','I1_raw','I2_raw','Ires_raw','VBUS_raw']); w.writerows(self.data_buffer)
            self._log_local(f"Saved {len(self.data_buffer)} rows","meas"); self._set_status(f"ADC data saved: {len(self.data_buffer)} rows")
        except Exception as e: self._log_local(f"Save failed: {e}","error")
    def _log_local(self,text,tag="received"):
        print(f"[DBG] {text}")
        try:
            root=self.winfo_toplevel()
            fn=lambda t=text,g=tag: root._log(t,g) if hasattr(root,'_log') else None
            if threading.current_thread() is threading.main_thread():
                fn()
            else:
                root.after(0,fn)
        except Exception as e:
            print(f"[_log_local ERROR] {text} (error={e})")
    def _set_status(self,text):
        r=self.winfo_toplevel()
        if hasattr(r,'_set_status'): r._set_status(text)

    def on_telemetry(self,prefix,data):
        if prefix!="ADC": return
        if "I1" in data: self.l_i1.config(text=f"I1: {data['I1']} raw")
        if "I2" in data: self.l_i2.config(text=f"I2: {data['I2']} raw")
        if "Ires" in data: self.l_in.config(text=f"Ires: {data['Ires']} raw")
        if "VBUS" in data: self.l_vb.config(text=f"VBUS: {data['VBUS']} raw")
        if all(k in data for k in ['I1','I2','Ires','VBUS']):
            ts=datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            self.data_buffer.append([ts,data['I1'],data['I2'],data['Ires'],data['VBUS']])
            if len(self.data_buffer)>10000: self.data_buffer=self.data_buffer[-10000:]

# ═══════════════════════════════════════════════════════════════════════
#  FOCTab
# ═══════════════════════════════════════════════════════════════════════

class FOCTab(ttk.Frame):
    def __init__(self,parent,send_fn,saleae=None):
        super().__init__(parent); self.send=send_fn; self.saleae=saleae; self.last_data={}; self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0,weight=1); self.columnconfigure(1,weight=1); self.columnconfigure(2,weight=1)
        self._build_control_panel(); self._build_telemetry_panel(); self._build_saleae_panel()

    def _build_control_panel(self):
        f=ttk.LabelFrame(self,text="Control"); f.grid(row=0,column=0,sticky="nsew",padx=5,pady=5)
        bf=ttk.Frame(f); bf.pack(fill=tk.X,padx=5,pady=5)
        ttk.Button(bf,text="▶ START FOC",command=lambda:self.send("1")).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf,text="■ STOP FOC",command=lambda:self.send("0")).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf,text="Clear Fault",command=lambda:self.send("f")).pack(side=tk.LEFT,padx=2)
        ttk.Label(f,text="Speed Control:",font=("Arial",10,"bold")).pack(pady=(10,2))
        self.spv=tk.IntVar(value=0)
        self.sps=tk.Scale(f,from_=-3000,to=3000,orient=tk.HORIZONTAL,resolution=50,length=300,variable=self.spv,command=self._on_sp)
        self.sps.pack(padx=5,pady=2); self.sps.bind("<ButtonRelease-1>",self._on_sp_rel)
        self.spl=ttk.Label(f,text="Speed: 0 RPM",font=("Consolas",10)); self.spl.pack(pady=2)
        ttk.Separator(f,orient=tk.HORIZONTAL).pack(fill=tk.X,pady=10,padx=5)
        self.stl=ttk.Label(f,text="Status: STOPPED",font=("Arial",11,"bold"),foreground="red"); self.stl.pack(pady=5)
        ttk.Label(f,text="Id_ref (mA):").pack(pady=(10,2))
        self.idv=tk.IntVar(value=0); ttk.Spinbox(f,from_=-5000,to=5000,textvariable=self.idv,width=10).pack(pady=2)
        ttk.Label(f,text="Iq_ref (mA):").pack(pady=(10,2))
        self.iqv=tk.IntVar(value=0); ttk.Spinbox(f,from_=-5000,to=5000,textvariable=self.iqv,width=10).pack(pady=2)
        ttk.Button(f,text="Apply Id/Iq",command=self._apply_iq).pack(pady=5)
        ttk.Label(f,text="VDC nominal (V):").pack(pady=(10,2))
        self.vdcv=tk.IntVar(value=150)
        ttk.Spinbox(f,from_=10,to=400,textvariable=self.vdcv,width=10).pack(pady=2)
        ttk.Button(f,text="Set VDC",command=self._set_vdc).pack(pady=2)
        ttk.Button(f,text="💾 Save CSV",command=self._save_csv).pack(pady=5)

    def _build_telemetry_panel(self):
        f=ttk.LabelFrame(self,text="Telemetry"); f.grid(row=0,column=1,sticky="nsew",padx=5,pady=5)
        self.l_i1=ttk.Label(f,text="I1: — raw",font=("Consolas",10)); self.l_i1.grid(row=0,column=0,padx=10,pady=5,sticky="w")
        self.l_i2=ttk.Label(f,text="I2: — raw",font=("Consolas",10)); self.l_i2.grid(row=0,column=1,padx=10,pady=5,sticky="w")
        self.l_in=ttk.Label(f,text="Ires: — raw",font=("Consolas",10)); self.l_in.grid(row=1,column=0,padx=10,pady=5,sticky="w")
        self.l_vb=ttk.Label(f,text="VBUS: — raw",font=("Consolas",10)); self.l_vb.grid(row=1,column=1,padx=10,pady=5,sticky="w")
        self.l_sp=ttk.Label(f,text="Speed: — RPM",font=("Consolas",10)); self.l_sp.grid(row=2,column=0,padx=10,pady=5,sticky="w")
        self.l_th=ttk.Label(f,text="Theta: — rad",font=("Consolas",10)); self.l_th.grid(row=2,column=1,padx=10,pady=5,sticky="w")
        f.columnconfigure(0,weight=1); f.columnconfigure(1,weight=1)

    def _build_saleae_panel(self):
        f=ttk.LabelFrame(self,text="Saleae — FOC Capture"); f.grid(row=1,column=0,columnspan=3,sticky="ew",padx=5,pady=5)
        self.sf=SaleaeConnectFrame(f,self.saleae,on_status_change=self._on_saleae_status)
        self.sf.pack(side=tk.LEFT,padx=5,pady=5)
        self.bc=ttk.Button(f,text="📊 Capture FOC Waveforms",command=self._capture_foc)
        self.bc.pack(side=tk.LEFT,padx=10,pady=5); self._update_saleae_buttons()

    def _on_sp(self,v):
        try: self.spl.config(text=f"Speed: {int(float(v))} RPM")
        except: pass
    def _on_sp_rel(self,e):
        try: self.send(f"s={self.spv.get()}")
        except: pass
    def _apply_iq(self): self.send(f"i={self.idv.get()},{self.iqv.get()}")
    def _set_vdc(self):
        """Номинал шины для PI-расчётов (модульный оптимум kp~1/Vdc).
        Прошивка отвечает @VDC:OK — фактический Vbus виден в телеметрии."""
        try: v = int(self.vdcv.get())
        except (tk.TclError, ValueError): return
        if not (10 <= v <= 400):
            print(f"[FOC] VDC out of range: {v}"); return
        self.send(f"vdc={v}")
        print(f"[FOC] VDC nominal set to {v} V")
    def _on_saleae_status(self,ok): self._update_saleae_buttons()
    def _update_saleae_buttons(self):
        st="normal" if (self.saleae and self.saleae.available) else "disabled"
        self.bc.config(state=st)

    def _capture_foc(self):
        if not self.saleae or not self.saleae.available: return
        self.bc.config(state=tk.DISABLED,text="⏳ Capturing...")
        threading.Thread(target=self._capture_foc_worker,daemon=True).start()

    def _capture_foc_worker(self):
        try:
            td = os.path.abspath('_saleae_tmp')
            if os.path.exists(td): shutil.rmtree(td, ignore_errors=True)
        except: pass
        try:
            capture=self.saleae.capture_sync(digital_chs=[0,1,2,3,4,5,6,7],duration_s=0.5)
            if not capture:
                self.after(0,lambda: self._log_local("Saleae: capture returned None","error"))
                self.after(0,lambda: self.bc.config(state=tk.NORMAL,text="📊 Capture FOC Waveforms"))
                return
            import threading as _thr
            def _wait():
                try: capture.wait()
                except: pass
            wt = _thr.Thread(target=_wait, daemon=True)
            wt.start()
            wt.join(timeout=3.0)
            if wt.is_alive():
                self.after(0,lambda: self._log_local("Saleae: capture timed out (3s)","error"))
                self.after(0,lambda: self.bc.config(state=tk.NORMAL,text="📊 Capture FOC Waveforms"))
                return
        except Exception as _e:
            error_text = str(_e)
            self.after(0,lambda: self._log_local(f"Saleae error: {error_text}","error"))
            self.after(0,lambda: self.bc.config(state=tk.NORMAL,text="📊 Capture FOC Waveforms"))
            return
        results=[]
        for ci in range(8):
            f=self.saleae.measure_freq(capture,ci)
            d=self.saleae.measure_duty(capture,ci)
            if f is None or d is None: results.append((False,f"Ch{ci}: no signal"))
            else: results.append((True,f"Ch{ci}: {f:.1f}Hz, {d*100:.1f}%"))
        for ci in range(4):
            v=self.saleae.measure_voltage(ci,0.3)
            if v is None: results.append((False,f"A{ci}: no data"))
            else: results.append((True,f"A{ci}: {v:.4f}V"))
        try:
            td = os.path.abspath('_saleae_tmp')
            if os.path.exists(td): shutil.rmtree(td, ignore_errors=True)
        except: pass
        self.after(0,lambda: self._capture_foc_done(results))

    def _capture_foc_done(self,results):
        self.bc.config(state=tk.NORMAL,text="📊 Capture FOC Waveforms")
        self._log_local("=== FOC Waveform Capture ===","sent")
        for ok,m in results: self._log_local(m,"meas" if ok else "error")

    def _save_csv(self):
        if not self.last_data:
            self._log_local("No data to save","error"); self._set_status("No data to save"); return
        fp=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")],title="Save FOC data")
        if not fp: return
        try:
            with open(fp,'w',newline='') as f:
                w=csv.writer(f); w.writerow(['timestamp','I1','I2','IN','VBUS','Speed','Theta'])
                w.writerow([datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                    self.last_data.get('I1',''),self.last_data.get('I2',''),self.last_data.get('IN',''),
                    self.last_data.get('VBUS',''),self.last_data.get('Speed',''),self.last_data.get('Theta','')])
            self._log_local(f"Saved FOC snapshot","meas"); self._set_status("FOC data saved")
        except Exception as e: self._log_local(f"Save failed: {e}","error")
    def _log_local(self,text,tag="received"):
        print(f"[DBG] {text}")
        try:
            root=self.winfo_toplevel()
            fn=lambda t=text,g=tag: root._log(t,g) if hasattr(root,'_log') else None
            if threading.current_thread() is threading.main_thread():
                fn()
            else:
                root.after(0,fn)
        except Exception as e:
            print(f"[_log_local ERROR] {text} (error={e})")
    def _set_status(self,text):
        r=self.winfo_toplevel()
        if hasattr(r,'_set_status'): r._set_status(text)

    def on_telemetry(self,prefix,data):
        if prefix!="FOC": return
        self.last_data.update(data)
        if "I1" in data: self.l_i1.config(text=f"I1: {data['I1']} raw")
        if "I2" in data: self.l_i2.config(text=f"I2: {data['I2']} raw")
        if "Ires" in data: self.l_in.config(text=f"Ires: {data['Ires']} raw")
        if "VBUS" in data: self.l_vb.config(text=f"VBUS: {data['VBUS']} raw")
        if "Speed" in data: self.l_sp.config(text=f"Speed: {data['Speed']} RPM")
        if "Theta" in data: self.l_th.config(text=f"Theta: {data['Theta']/1000:.3f} rad")
        run=data.get("RUN",1 if data.get("Speed",0)>0 else 0)
        if run: self.stl.config(text="Status: RUNNING",foreground="green")
        else: self.stl.config(text="Status: STOPPED",foreground="red")

# ═══════════════════════════════════════════════════════════════════════
#  NucleoDebugTool
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
#  AutoTuneTab
# ═══════════════════════════════════════════════════════════════════════

AT_PARAM_NAMES = ["Rs","Ls","Isat","Rr","Lm","Tr","Ke","p","J"]
AT_PARAM_UNITS = ["mΩ","uH","mA","mΩ","uH","us","mV/rpm","","10^-6 kg*m^2"]
AT_CURVE_RE = re.compile(r"I=(-?\d+),L=(-?\d+)")
AT_PROG_RE      = re.compile(r"^@IDLE:PROG=(\d+)/(\d+):D=(\d+):I=(-?\d+):L=(-?\d+):REP=(\d+)/(\d+)")
AT_STAT_RE      = re.compile(r"^@AT:STAT:Rs_COUNT=(\d+):Rs_MED_mOhm=(-?\d+):Rs_MIN_mOhm=(-?\d+):Rs_MAX_mOhm=(-?\d+):Rs_SPREAD_PCT=(-?\d+):Ls_COUNT=(\d+):Ls_MED_uH=(-?\d+):Ls_MIN_uH=(-?\d+):Ls_MAX_uH=(-?\d+):Ls_SPREAD_PCT=(-?\d+):Isat_mA=(-?\d+)")
AT_PAIR_RE      = re.compile(r"^@AT:PAIR:(\d):Rs=(-?\d+):Ls=(-?\d+)")
AT_PAIR_PHASES  = ("A", "B", "C")   # pair_idx 0/1/2 → фаза U/V/W (документация: A/B/C)
AT_SCOPE_RE     = re.compile(r"^@SCOPE:T=(-?\d+):I=(-?\d+)")
AT_OEW_PROG_RE  = re.compile(r"^@AT:OEW:PROG=(\d+)/(\d+):D=(\d+):I=(-?\d+):L=(-?\d+)")
AT_RR_PROG_RE   = re.compile(r"^@AT:RR:PROG=(-?\d+)/(-?\d+):I=(-?\d+)")
AT_NOLOAD_RE    = re.compile(r"^@AT:NOLOAD:OK:Irms=(-?\d+):Z=(-?\d+):Ltotal=(-?\d+):Lm=(-?\d+):Lr=(-?\d+):Tr=(-?\d+)")
AT_PI_RE        = re.compile(r"^@AT:PI:BW=(-?\d+):Kp=(-?\d+):Ki=(-?\d+):Ls=(-?\d+):Rs=(-?\d+)")
AT_LSPOS_RE     = re.compile(r"^@AT:LSPOS:OK:MEDIAN=(-?\d+):MIN=(-?\d+):MAX=(-?\d+):SPREAD=(-?\d+)%")
AT_CH_DETECT_RE = re.compile(r"^@AT:CH_DETECT:OK:CH=(\d+):I=(-?\d+):SIGN=(-?\d+)")


class AutoTuneTab(ttk.Frame):
    _CMD_TIMEOUT = 60.0

    def __init__(self, parent, send_fn, saleae=None):
        super().__init__(parent)
        self.send = send_fn
        self.saleae = saleae
        self._pending_cmd = None
        self._pending_btn = None
        self._pending_after_id = None
        self._pending_orig_text = ""
        self._params = {}
        self._stats = {}
        self._pairs = {}
        self._curve_points = []; self._scope_points = []
        self._auto_apply = tk.BooleanVar(value=False)
        self._last_kp = 0
        self._last_ki = 0
        self._last_lsig = 0
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1); self.columnconfigure(1, weight=1); self.columnconfigure(2, weight=1)
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        sf = ttk.LabelFrame(left, text="Static ID (stationary)")
        sf.pack(fill=tk.X, padx=2, pady=2)
        self.btn_idle   = ttk.Button(sf, text="\u25b6 Rs/Ls/Isat (5x)", command=lambda: self._run_cmd("idle",  self.btn_idle))
        self.btn_iv     = ttk.Button(sf, text="\u25b6 Multi-point Rs (I-V)", command=lambda: self._run_cmd("iv",    self.btn_iv))
        self.btn_pairs  = ttk.Button(sf, text="\u25b6 All pairs AB/BC/CA", command=lambda: self._run_cmd("pairs",  self.btn_pairs))
        self.btn_ch     = ttk.Button(sf, text="\u25b6 Detect channel", command=lambda: self._run_cmd("ch",     self.btn_ch))
        self.btn_curve  = ttk.Button(sf, text="\U0001f4ca Show curve", command=lambda: self._run_cmd("curve",  self.btn_curve))
        self.btn_oew    = ttk.Button(sf, text="\u25b6 Ls OEW (both inv)", command=lambda: self._run_cmd("oew",    self.btn_oew))
        self.btn_lspos  = ttk.Button(sf, text="\u25b6 Ls vs position (6x)", command=lambda: self._run_cmd("lspos",  self.btn_lspos))
        self.btn_scope  = ttk.Button(sf, text="\U0001f4ca Scope (100 pts)", command=lambda: self._run_cmd("scope",  self.btn_scope))
        for b in (self.btn_idle, self.btn_iv, self.btn_pairs, self.btn_ch, self.btn_curve, self.btn_oew, self.btn_lspos, self.btn_scope):
            b.pack(fill=tk.X, padx=6, pady=3)
        rf = ttk.LabelFrame(left, text="Rotational ID")
        rf.pack(fill=tk.X, padx=2, pady=2)
        self.btn_rr     = ttk.Button(rf, text="\u25b6 Rr (5 Hz, locked)", command=lambda: self._run_cmd("rr",     self.btn_rr))
        self.btn_noload = ttk.Button(rf, text="\u25b6 Lm/Lr (V/f, free)", command=lambda: self._run_cmd("noload", self.btn_noload))
        self.btn_irot    = ttk.Button(rf, text="\u25b6 Rotate + measure", command=lambda: self._run_cmd("irot",    self.btn_irot))
        self.btn_inertia = ttk.Button(rf, text="\u2699 Measure J", command=lambda: self._run_cmd("inertia", self.btn_inertia))
        for b in (self.btn_rr, self.btn_noload, self.btn_irot, self.btn_inertia):
            b.pack(fill=tk.X, padx=6, pady=3)
        cf = ttk.Frame(left)
        cf.pack(fill=tk.X, padx=2, pady=2)
        self.btn_abort  = ttk.Button(cf, text="\u26d4 Abort", command=self._do_abort)
        self.btn_params = ttk.Button(cf, text="\U0001f4cb Params", command=lambda: self.send("params"))
        self.btn_stats  = ttk.Button(cf, text="\U0001f4c8 Stats", command=lambda: self.send("stats"))
        self.btn_export = ttk.Button(cf, text="\U0001f4be Export CSV", command=self._export_csv)
        for b in (self.btn_abort, self.btn_params, self.btn_stats, self.btn_export):
            b.pack(fill=tk.X, padx=6, pady=3)

        center = ttk.Frame(self)
        center.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        pf = ttk.LabelFrame(center, text="Progress")
        pf.pack(fill=tk.X, padx=2, pady=2)
        self.progress = ttk.Progressbar(pf, mode="determinate", maximum=50)
        self.progress.pack(fill=tk.X, padx=6, pady=6)
        self.prog_lbl = ttk.Label(pf, text="\u2014", font=("Consolas", 9))
        self.prog_lbl.pack(padx=6, pady=(0, 6))
        res_f = ttk.LabelFrame(center, text="Measured Parameters")
        res_f.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        pf_inner = ttk.Frame(res_f)
        pf_inner.pack(fill=tk.X, padx=6, pady=6)
        pf_inner.columnconfigure(1, weight=1)
        self._param_labels = {}
        for idx, name in enumerate(AT_PARAM_NAMES):
            unit = AT_PARAM_UNITS[idx]
            ttk.Label(pf_inner, text=f"{name}:", font=("Consolas", 10)).grid(row=idx, column=0, sticky="w", padx=(0, 6), pady=1)
            lbl = ttk.Label(pf_inner, text="\u2014" + (f"  ({unit})" if unit else ""), font=("Consolas", 10), foreground="#333")
            lbl.grid(row=idx, column=1, sticky="w", pady=1)
            self._param_labels[name] = (lbl, unit)
        apply_f = ttk.Frame(res_f)
        apply_f.pack(fill=tk.X, padx=8, pady=(4, 8))
        self.btn_apply = ttk.Button(apply_f, text="\u27a1 Apply to FOC", command=self._apply_to_foc)
        self.btn_apply.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Checkbutton(apply_f, text="Auto-apply", variable=self._auto_apply).pack(side=tk.LEFT)

        right = ttk.Frame(self)
        right.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        exp_f = ttk.LabelFrame(right, text="Expected (from datasheet)")
        exp_f.pack(fill=tk.X, padx=2, pady=2)
        self._exp_vars = {}
        for name, unit in [("Rs", "m\u03a9"), ("Ls", "\u00b5H"), ("Isat", "mA")]:
            row = ttk.Frame(exp_f); row.pack(fill=tk.X, padx=6, pady=2)
            ttk.Label(row, text=f"{name}:", width=6).pack(side=tk.LEFT)
            var = tk.StringVar(value="0")
            ttk.Entry(row, textvariable=var, width=8).pack(side=tk.LEFT, padx=2)
            ttk.Label(row, text=unit).pack(side=tk.LEFT)
            self._exp_vars[name] = var
        ttk.Button(exp_f, text="Validate", command=self._validate_params).pack(pady=4)
        val_f = ttk.LabelFrame(right, text="Validation")
        val_f.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.val_text = tk.Text(val_f, height=8, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        self.val_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        pif = ttk.LabelFrame(right, text="PI Regulator Calc")
        pif.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(pif, text="BW (Hz):").pack(side=tk.LEFT, padx=4)
        self.pi_bw_var = tk.StringVar(value="800")
        ttk.Entry(pif, textvariable=self.pi_bw_var, width=6).pack(side=tk.LEFT, padx=2)
        self.btn_pi = ttk.Button(pif, text="Calc Kp/Ki", command=self._cmd_pi)
        self.btn_pi.pack(side=tk.LEFT, padx=4)
        self.pi_lbl = ttk.Label(pif, text="Kp=-- Ki=--", font=("Consolas", 9))
        self.pi_lbl.pack(side=tk.LEFT, padx=8)
        fwf = ttk.LabelFrame(right, text="FW Base Speed")
        fwf.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(fwf, text="rpm:").pack(side=tk.LEFT, padx=4)
        self.fw_base_var = tk.StringVar(value="1000")
        ttk.Entry(fwf, textvariable=self.fw_base_var, width=7).pack(side=tk.LEFT, padx=2)
        self.btn_fwbase = ttk.Button(fwf, text="Set", command=self._cmd_fwbase)
        self.btn_fwbase.pack(side=tk.LEFT, padx=4)

        bottom = ttk.Frame(self)
        bottom.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
        curve_f = ttk.LabelFrame(bottom, text="Saturation Curve Ls(I)")
        curve_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
        self.curve_text = tk.Text(curve_f, height=8, state=tk.DISABLED, font=("Consolas", 10), wrap=tk.NONE)
        csb = ttk.Scrollbar(curve_f, orient=tk.VERTICAL, command=self.curve_text.yview)
        self.curve_text.configure(yscrollcommand=csb.set)
        self.curve_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        csb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=6)
        self.curve_text.config(state=tk.NORMAL)
        self.curve_text.insert(tk.END, "       I (mA)     Ls (uH)\n")
        self.curve_text.insert(tk.END, "-" * 24 + "\n")
        self.curve_text.config(state=tk.DISABLED)
        plot_f = ttk.LabelFrame(bottom, text="Ls(I) plot")
        plot_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
        self.plot_canvas = tk.Canvas(plot_f, bg="white", height=200)
        self.plot_canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _do_abort(self):
        self.send("abort")
        self._log_local("[AT] Abort requested", "error")
        self._log_local("  └─ Причина: прервано пользователем — измерение остановлено, PWM отключён", "error")

    _AT_CMDS_NEEDING_FAULT_CLEAR = {"idle", "iv", "pairs", "ch", "oew", "lspos", "scope", "rr", "noload", "irot", "inertia"}

    def _run_cmd(self, cmd_name, btn):
        if self._pending_cmd is not None:
            self._log_local("[AT] Busy \u2014 wait or send 'abort'", "error")
            return
        # Ревью GUI-08: БЕЗ авто-'f' — GUI не должен самовольно снимать
        # fault latch (safety state). При активном fault прошивка сама
        # ответит @AT:ERROR:FAULT (AT_SafetyCheck) — GUI покажет причину,
        # пользователь сбросит явно кнопкой Clear.
        self.send(cmd_name)
        self._pending_cmd = cmd_name
        self._pending_btn = btn
        self._pending_orig_text = btn.cget("text")
        btn.config(text="\u23f3 Working...", state=tk.DISABLED)
        self._log_local(f"[AT] Sent '{cmd_name}', waiting...", "sent")
        self._pending_after_id = self.after(int(self._CMD_TIMEOUT * 1000), lambda: self._on_timeout(cmd_name))

    def _on_timeout(self, cmd_name):
        if self._pending_cmd != cmd_name: return
        self._log_local(f"[AT] Timeout ({self._CMD_TIMEOUT}s) for '{cmd_name}'", "error")
        self._reset_btn()

    def _reset_btn(self):
        if self._pending_after_id is not None:
            self.after_cancel(self._pending_after_id)
            self._pending_after_id = None
        if self._pending_btn is not None:
            self._pending_btn.config(text=self._pending_orig_text, state=tk.NORMAL)
        self._pending_cmd = None
        self._pending_btn = None
        self.progress["value"] = 0
        self.prog_lbl.config(text="\u2014")

    def on_line(self, line):
        m = AT_PROG_RE.match(line)
        if m:
            cur, total = int(m.group(1)), int(m.group(2))
            duty, I, L = int(m.group(3)), int(m.group(4)), int(m.group(5))
            rep, rep_tot = int(m.group(6)), int(m.group(7))
            self.progress["maximum"] = total
            self.progress["value"]   = cur
            self.prog_lbl.config(text=f"D={duty}%  I={I} mA  L={L} uH  (rep {rep}/{rep_tot})")
            return True
        m = AT_STAT_RE.match(line)
        if m:
            self._stats["Rs"]   = (int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
            self._stats["Ls"]   = (int(m.group(7)), int(m.group(8)), int(m.group(9)), int(m.group(10)))
            self._stats["Isat"] = (int(m.group(11)), 0, 0, 0)
            return True
        m = AT_PAIR_RE.match(line)
        if m:
            idx = int(m.group(1))
            name = AT_PAIR_PHASES[idx] if idx < len(AT_PAIR_PHASES) else str(idx)
            self._pairs[name] = (int(m.group(2)), int(m.group(3)), 0)
            return True
        m = AT_SCOPE_RE.match(line)
        if m: self._scope_points.append((int(m.group(1)), int(m.group(2)))); return True
        if line == "@SCOPE:START": self._scope_points = []; return True
        if line == "@SCOPE:DONE" or line.startswith("@SCOPE:RESULT"):
            if self._scope_points: self._log_local(f"[AT] Scope: {len(self._scope_points)} pts","tlm"); self._draw_scope()
            if self._pending_cmd == "scope": self._reset_btn()
            return True
        m = AT_OEW_PROG_RE.match(line)
        if m:
            self.progress["maximum"]=int(m.group(2)); self.progress["value"]=int(m.group(1))
            self.prog_lbl.config(text=f"OEW: D={m.group(3)}% I={m.group(4)}mA L={m.group(5)}uH"); return True
        m = AT_RR_PROG_RE.match(line)
        if m:
            self.progress["maximum"]=int(m.group(2)); self.progress["value"]=int(m.group(1))
            self.prog_lbl.config(text=f"Rr: {m.group(3)}mA"); return True
        m = AT_NOLOAD_RE.match(line)
        if m: self._log_local(f"[AT] NoLoad: Lm={m.group(4)}uH Lr={m.group(5)}uH Tr={m.group(6)}us","tlm"); return True
        m = AT_PI_RE.match(line)
        if m:
            self._last_kp = int(m.group(2))
            self._last_ki = int(m.group(3))
            self.pi_lbl.config(text=f"Kp={self._last_kp} Ki={self._last_ki} (x1e-3)")
            self._log_local(f"[AT] PI: bw={m.group(1)}Hz","tlm"); return True
        m = AT_LSPOS_RE.match(line)
        if m: self._log_local(f"[AT] Ls pos: med={m.group(1)} min={m.group(2)} max={m.group(3)} spread={m.group(4)}%","tlm"); return True
        if line.startswith("@AT:NOLOAD:RAMP:F="):
            for p in line.split(":"):
                if p.startswith("F="): self.prog_lbl.config(text=f"V/f ramp: {p[2:]} Hz")
            return True
        if line.startswith("@AT:LSPOS:WAIT:"):
            for p in line.split(":"):
                if p.startswith("POS="): self.prog_lbl.config(text=f"Turn rotor! Position {p[4:]}")
            return True
        m = AT_CH_DETECT_RE.match(line)
        if m:
            ch_names = {0: "?", 1: "I1", 2: "I2", 3: "Ires"}
            ch = ch_names.get(int(m.group(1)), "?")
            self._log_local(f"[AT] Channel: {ch}, I={m.group(2)} mA", "tlm")
            return True
        if line.startswith("@MP:OK"):
            # Новые поля: Kp/Ki из модульного оптимума, Lsig — Lσ компенсации
            m = re.search(r"Kp=(-?\d+):Ki=(-?\d+):Lsig=(-?\d+)", line)
            if m:
                self._last_kp = int(m.group(1))
                self._last_ki = int(m.group(2))
                self._last_lsig = int(m.group(3))
            self._log_local("[AT] Motor params applied to FOC \u2713", "tlm")
            # GUI-10: подтверждено firmware — теперь можно сохранить JSON.
            pj = getattr(self, '_pending_json', None)
            if pj is not None:
                self._write_json(pj)
                self._pending_json = None
            return True
        if line.startswith("@MP:ERROR"):
            self._log_local(f"[AT] Apply failed: {line}", "error")
            self._pending_json = None   # GUI-10: не сохранять rejected
            return True
        if line.startswith("@PI:APPLIED"):
            m = re.search(r"Kp=(-?\d+):Ki=(-?\d+)", line)
            if m:
                self._last_kp = int(m.group(1))
                self._last_ki = int(m.group(2))
            self._log_local(f"[AT] PI gains applied: {line}", "tlm")
            return True
        if line.startswith("@PI:ERROR"):
            self._log_local(f"[AT] PI apply error: {line}", "error")
            self._log_local(at_error_cause(line), "error")
            return True
        if line.startswith("@AT:PARAMS:") or line.startswith("@PARAMS:"):
            self._parse_params(line)
            return True
        if line.startswith("@IDLE:CURVE:"):
            self._parse_curve(line)
            if self._pending_cmd == "curve": self._reset_btn()
            return True
        if line.startswith("@AT:ERROR"):
            self._log_local(f"[AT] Error: {line}", "error")
            self._log_local(at_error_cause(line), "error")
            if self._pending_cmd is not None: self._reset_btn()
            return True
        for prefix, cmd in [("@IDLE:DONE", "idle"), ("@IDLE:OK", "idle"),
                            ("@IROT:DONE", "irot"), ("@IROT:OK", "irot"),  # GUI-07: реальный wire
                            ("@INERTIA:DONE", "inertia"),
                            ("@AT:CH:OK", "ch"), ("@AT:IV:OK", "iv"),
                            ("@AT:RS_IV:OK", "iv"), ("@AT:PAIRS:RESULT_OK", "pairs"),
                            ("@AT:PAIRS:OK", "pairs"),
                            ("@AT:OEW:RESULT_OK", "oew"), ("@AT:OEW:OK", "oew"),
                            ("@AT:RR:RESULT_OK", "rr"), ("@AT:RR:OK", "rr"),
                            ("@AT:NOLOAD:RESULT_OK", "noload"),
                            ("@AT:LSPOS:RESULT_OK", "lspos")]:
            if line.startswith(prefix):
                if self._pending_cmd == cmd:
                    self._log_local(f"[AT] {cmd} completed", "tlm")
                    self._reset_btn()
                    if cmd == "idle":
                        self._validate_params()
                        if self._auto_apply.get() and self._params.get('Rs', 0) > 0:
                            self._apply_to_foc()
                return True
        for prefix, cmd in [("@IDLE:ERROR", "idle"), ("@IDLE:FAIL", "idle"),
                            ("@IROT:ERROR", "irot"), ("@INERTIA:ERROR", "inertia"),
                            ("@AT:CH_DETECT:ERROR", "ch"), ("@AT:CH:FAIL", "ch"),
                            ("@AT:RS_IV:ERROR", "iv"), ("@AT:IV:FAIL", "iv"),
                            ("@AT:PAIRS:ERROR", "pairs"), ("@AT:PAIRS:RESULT_FAIL", "pairs"),
                            ("@IDLE:ABORTED", "idle"),
                            ("@AT:OEW:ERROR", "oew"), ("@AT:OEW:ABORTED", "oew"),
                            ("@AT:OEW:RESULT_FAIL", "oew"),
                            ("@AT:RR:ERROR", "rr"), ("@AT:RR:ABORTED", "rr"),
                            ("@AT:RR:RESULT_FAIL", "rr"),
                            ("@AT:NOLOAD:ABORTED", "noload"),
                            ("@AT:NOLOAD:RESULT_FAIL", "noload"),
                            ("@AT:LSPOS:ERROR", "lspos"), ("@AT:LSPOS:ABORTED", "lspos"),
                            ("@AT:LSPOS:RESULT_FAIL", "lspos")]:
            if line.startswith(prefix):
                if self._pending_cmd == cmd or self._pending_cmd is None:
                    self._log_local(f"[AT] Error: {line}", "error")
                    self._log_local(at_error_cause(line), "error")
                    self._reset_btn()
                return True
        return False

    def _parse_params(self, line):
        parsed = parse_telemetry_params(line)
        if not parsed:
            return
        self._params = parsed
        for name, (lbl, unit) in self._param_labels.items():
            if name in self._params:
                lbl.config(text=f"{self._params[name]}{f'  ({unit})' if unit else ''}")
            else:
                lbl.config(text="\u2014" + (f"  ({unit})" if unit else ""))

    def _parse_curve(self, line):
        parsed = parse_telemetry_curve(line)
        if not parsed:
            return
        self._curve_points = parsed["points"]
        self.curve_text.config(state=tk.NORMAL)
        self.curve_text.delete("3.0", tk.END)
        for i_val, l_val in self._curve_points:
            self.curve_text.insert(tk.END, f"{i_val:>10}  {l_val:>10}\n")
        self.curve_text.config(state=tk.DISABLED)
        self._draw_curve()

    def _draw_curve(self):
        c = self.plot_canvas
        c.delete("all")
        if not self._curve_points: return
        w = c.winfo_width() or 300
        h = c.winfo_height() or 200
        margin = 30
        I_vals = [p[0] for p in self._curve_points]
        L_vals = [p[1] for p in self._curve_points]
        I_min, I_max = min(I_vals), max(I_vals)
        L_min, L_max = min(L_vals), max(L_vals)
        if I_max == I_min: I_max = I_min + 1
        if L_max == L_min: L_max = L_min + 1
        def x(i): return margin + (i - I_min) * (w - 2 * margin) / (I_max - I_min)
        def y(l): return h - margin - (l - L_min) * (h - 2 * margin) / (L_max - L_min)
        c.create_line(margin, margin, margin, h - margin, w - margin, h - margin)
        pts = []
        for i_val, l_val in self._curve_points:
            pts.extend([x(i_val), y(l_val)])
        if len(pts) >= 4:
            c.create_line(*pts, fill="blue", width=2)
            for i_val, l_val in self._curve_points:
                cx, cy = x(i_val), y(l_val)
                c.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill="blue")

    def _apply_to_foc(self):
        """Отправить измеренные параметры в FOC (команда mp=)."""
        p = self._params
        if not p or 'Rs' not in p or 'Ls' not in p:
            self._log_local("[AT] No params \u2014 run idle/iv first", "error")
            return
        rs = p.get('Rs', 0); ls = p.get('Ls', 0)
        rr = p.get('Rr', 0); lm = p.get('Lm', 0)
        tr = p.get('Tr', 0); ke = p.get('Ke', 0)
        pp = p.get('p', 6);  j  = p.get('J', 0)   # default 6 = FOC_DEFAULT_POLE_PAIRS (firmware)
        self.send(f"mp={rs},{ls},{rr},{lm},{tr},{ke},{pp},{j}")
        self._log_local(f"[AT] Apply to FOC: Rs={rs}m\u03a9 Ls={ls}\u00b5H p={pp}", "sent")
        # Ревью GUI-10: JSON пишется ТОЛЬКО после @MP:OK — иначе файл
        # сохранит параметры, которые firmware не применила.
        self._pending_json = p

    def _write_json(self, p):
        """Записать autotune_params.json для foc_control_gui.py."""
        data = {
            "Rs_mOhm": p.get('Rs', 0), "Ls_uH": p.get('Ls', 0),
            "Rr_mOhm": p.get('Rr', 0), "Lm_uH": p.get('Lm', 0),
            "Tr_us": p.get('Tr', 0), "Ke_mV_rpm": p.get('Ke', 0),
            "pole_pairs": p.get('p', 6), "J_kg_m2_x1e6": p.get('J', 0),
            "Kp": getattr(self, '_last_kp', 0), "Ki": getattr(self, '_last_ki', 0),
            "timestamp": datetime.now().isoformat()
        }
        try:
            path = os.path.join(os.getcwd(), "autotune_params.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._log_local(f"[AT] Saved {os.path.basename(path)}", "meas")
        except Exception as e:
            self._log_local(f"[AT] JSON save error: {e}", "error")

    def _cmd_pi(self):
        try: bw = int(self.pi_bw_var.get())
        except ValueError: bw = 800
        self.send(f"pi={bw}")
        self._log_local(f"[AT] PI calc bw={bw} Hz", "sent")

    def _cmd_fwbase(self):
        """FW-01: базовая скорость ослабления поля (speed gate)."""
        try: v = int(self.fw_base_var.get())
        except ValueError: v = 0
        if not (100 <= v <= 5000):
            self._log_local("[FW] base speed must be 100..5000 rpm", "error")
            return
        self.send(f"fwbase={v}")
        self._log_local(f"[FW] base speed set to {v} rpm", "sent")

    def _validate_params(self):
        self.val_text.config(state=tk.NORMAL)
        self.val_text.delete("1.0", tk.END)
        lines = []
        all_ok = True
        for name, var in self._exp_vars.items():
            try: exp = int(var.get())
            except ValueError: exp = 0
            if exp <= 0:
                lines.append(f"{name}: no expected value set"); continue
            if name not in self._params:
                lines.append(f"{name}: not measured yet"); all_ok = False; continue
            got = self._params[name]
            err = abs(got - exp) / exp * 100
            status = "OK" if err <= 20 else "WARN"
            if err > 20: all_ok = False
            lines.append(f"{name}: got {got}, expected {exp}, err {err:.1f}% \u2014 {status}")
            if name in self._stats:
                _, mn, mx, sp = self._stats[name]
                if sp > 15:
                    lines.append(f"  \u26a0 spread {sp}% (min={mn}, max={mx})")
                    all_ok = False
        if len(self._curve_points) >= 3:
            Ls = [p[1] for p in self._curve_points]
            if Ls[0] < Ls[-1]:
                lines.append("Curve: Ls grows with I \u2014 possibly ADC noise")
                all_ok = False
        if len(self._pairs) == 3:
            Rs_vals = [v[0] for v in self._pairs.values()]
            Rs_mean = sum(Rs_vals) / 3
            Rs_spread = (max(Rs_vals) - min(Rs_vals)) / Rs_mean * 100 if Rs_mean > 0 else 0
            if Rs_spread > 10:
                lines.append(f"Phase asymmetry: {Rs_spread:.1f}% \u26a0")
                all_ok = False
        lines.append("=== PASS ===" if all_ok else "=== WARN ===")
        self.val_text.insert(tk.END, "\n".join(lines))
        self.val_text.config(state=tk.DISABLED)

    def _export_csv(self):
        if not self._params and not self._curve_points:
            self._log_local("[AT] No data to export", "error"); return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
            title="Export results", initialfile=f"autotune_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        if not path: return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["# Auto-Tune", datetime.now().isoformat()])
                w.writerow(["parameter","value","unit","median","min","max","spread%"])
                for idx, name in enumerate(AT_PARAM_NAMES):
                    row = [name, self._params.get(name, ""), AT_PARAM_UNITS[idx]]
                    row += list(self._stats[name]) if name in self._stats else ["","","",""]
                    w.writerow(row)
                w.writerow([])
                w.writerow(["pair","Rs_mOhm","Ls_uH","Isat_mA"])
                for pair, vals in self._pairs.items():
                    w.writerow([pair, *vals])
                w.writerow([])
                w.writerow(["I_mA","Ls_uH"])
                for i_val, l_val in self._curve_points:
                    w.writerow([i_val, l_val])
            self._log_local(f"[AT] Exported {path}", "meas")
        except Exception as e:
            self._log_local(f"[AT] Export error: {e}", "error")

    def _draw_scope(self):
        c = self.plot_canvas; c.delete("all")
        if not self._scope_points: return
        w = c.winfo_width() or 300; h = c.winfo_height() or 200; m = 30
        ts = [p[0] for p in self._scope_points]; Is = [p[1] for p in self._scope_points]
        tm, tM = min(ts), max(ts); im, iM = min(Is), max(Is)
        if tM == tm: tM += 1; im = iM; iM += 1
        def x(t): return m + (t - tm) * (w - 2*m) / (tM - tm)
        def y(i): return h - m - (i - im) * (h - 2*m) / (iM - im)
        c.create_line(m, m, m, h-m, w-m, h-m)
        pts = []
        for t, i in self._scope_points: pts.extend([x(t), y(i)])
        if len(pts) >= 4: c.create_line(*pts, fill="red", width=2)

    def _log_local(self, text, tag="received"):
        print(f"[AT] {text}")
        try:
            root = self.winfo_toplevel()
            fn = lambda t=text, g=tag: root._log(t, g) if hasattr(root,'_log') else None
            if threading.current_thread() is threading.main_thread(): fn()
            else: root.after(0, fn)
        except Exception as e:
            print(f"[_log_local ERROR] {text} (error={e})")

    def on_telemetry(self, prefix, data): pass
class NucleoDebugTool:
    def __init__(self):
        self.root=tk.Tk()
        self.root.title("Nucleo Debug Tool")
        self.root.geometry("950x750")
        self.root.minsize(800,600)
        self.ser=None; self.reader_thread=None; self.stop_event=threading.Event()
        self.rx_queue=queue.Queue(maxsize=4096)  # GUI-11: bounded
        self._build_ui(); self._scan_ports(); self._process_queue()
        # Прикрепить _log/_set_status к tk.Tk (winfo_toplevel возвращает tk.Tk, не NucleoDebugTool)
        self.root._log = self._log
        self.root._set_status = self._set_status

    def _build_ui(self):
        cf=ttk.Frame(self.root); cf.pack(fill=tk.X,padx=5,pady=5)
        self.port_combo=ttk.Combobox(cf,state="readonly",width=20); self.port_combo.pack(side=tk.LEFT,padx=5)
        ttk.Button(cf,text="Scan",command=self._scan_ports).pack(side=tk.LEFT,padx=5)
        self.btn_conn=ttk.Button(cf,text="Connect",command=self._toggle_connect); self.btn_conn.pack(side=tk.LEFT,padx=5)
        self.status_ind=tk.Label(cf,text="●",fg="red",font=("Arial",16)); self.status_ind.pack(side=tk.LEFT,padx=15)
        self.status_lbl=tk.Label(cf,text="Disconnected",fg="gray"); self.status_lbl.pack(side=tk.LEFT)
        ttk.Button(cf,text="💾 Save Log",command=self._save_log).pack(side=tk.RIGHT,padx=5)

        self.saleae=SaleaeHelper()
        # Авто-probe Saleae через 1 сек после старта
        self.root.after(1000, lambda: self._auto_probe_saleae())
        self.notebook=ttk.Notebook(self.root); self.notebook.pack(fill=tk.BOTH,expand=True,padx=5,pady=5)
        self.tab_pwm=PWMTab(self.notebook,self._send,saleae=self.saleae)
        self.tab_adc=ADCTab(self.notebook,self._send,saleae=self.saleae)
        self.tab_foc=FOCTab(self.notebook,self._send,saleae=self.saleae)
        self.tab_at=AutoTuneTab(self.notebook,self._send,saleae=self.saleae)
        self.notebook.add(self.tab_pwm,text="1. PWM Test")
        self.notebook.add(self.tab_adc,text="2. ADC Test")
        self.notebook.add(self.tab_foc,text="3. FOC Full")
        self.notebook.add(self.tab_at,text="4. Auto-Tune")

        lf=ttk.Frame(self.root); lf.pack(fill=tk.BOTH,expand=True,padx=5,pady=5)
        self.log_text=tk.Text(lf,wrap=tk.WORD,state=tk.DISABLED,font=("Consolas",10))
        self.log_text.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        sb=ttk.Scrollbar(lf,command=self.log_text.yview); sb.pack(side=tk.RIGHT,fill=tk.Y)
        self.log_text.config(yscrollcommand=sb.set)
        for t,c in [("sent","blue"),("received","black"),("tlm","green"),("error","red"),("meas","purple")]:
            self.log_text.tag_configure(t,foreground=c)
        self.log_text.bind("<Control-c>",self._copy_log); self.log_text.bind("<Escape>",self._clear_log)
        self.statusbar=ttk.Label(self.root,text="Ready",relief=tk.SUNKEN,anchor=tk.W)
        self.statusbar.pack(fill=tk.X,side=tk.BOTTOM)

    def _auto_probe_saleae(self):
        """Автоматическая проверка Saleae при старте."""
        for tab in [self.tab_pwm, self.tab_adc, self.tab_foc]:
            sf = getattr(tab, 'sf', None) or getattr(tab, 'saleae_frame', None)
            if sf and hasattr(sf, '_probe'):
                sf._probe()
                break

    def _scan_ports(self):
        ps=serial.tools.list_ports.comports()
        ns=[f"{p.device} - {p.description}" for p in ps]
        self.port_combo['values']=ns
        if not ns: self.port_combo.set("No ports found")
        else: self.port_combo.current(0)

    def _toggle_connect(self):
        if self.ser and self.ser.is_open: self._disconnect()
        else: self._connect()

    def _connect(self):
        v=self.port_combo.get()
        if not v or v=="No ports found": self._log("No port selected","error"); return
        pn=v.split(' - ')[0]
        try:
            self.ser=serial.Serial(pn,115200,timeout=0.1); self.stop_event.clear()
            self.reader_thread=threading.Thread(target=self._reader_loop,daemon=True)
            self.reader_thread.start()
            self.btn_conn.config(text="Disconnect"); self.status_ind.config(fg="green")
            self.status_lbl.config(text="Connected"); self._log(f"Connected to {pn}","received"); self._set_status(f"Connected to {pn}")
            self.root.after(500,lambda: self._send("sysinfo"))
        except Exception as e: self._log(f"Connection failed: {e}","error"); self._set_status("Connection failed")

    def _disconnect(self):
        self.stop_event.set()
        if self.ser and self.ser.is_open: self.ser.close()
        # Ревью GUI-03: join ДО замены self.ser — иначе старый reader переживает
        # reconnect и читает НОВЫЙ порт параллельно с новым reader.
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=2.0)
            self.reader_thread = None
        self.ser=None; self.btn_conn.config(text="Connect"); self.status_ind.config(fg="red")
        self.status_lbl.config(text="Disconnected"); self._log("Disconnected","received"); self._set_status("Disconnected")

    def _reader_loop(self):
        buf=""
        while not self.stop_event.is_set():
            try:
                if self.ser and self.ser.in_waiting>0:
                    buf += self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    # Ревью GUI-04: поток без '\n' не должен расти бесконечно.
                    if len(buf) > 8192:
                        self.rx_queue.put("[RX desync: buffer overflow, resynced]")
                        buf = buf[-1024:]   # хвост — возможное начало строки
                    while '\n' in buf:
                        l, buf = buf.split('\n', 1)
                        l = l.strip()
                        if l.startswith('> '): l = l[2:].strip()
                        if l:
                            # Ревью GUI-11: bounded queue — при переполнении
                            # вытесняем старейшую строку (телеметрия устаревает).
                            try:
                                self.rx_queue.put_nowait(l)
                            except queue.Full:
                                try: self.rx_queue.get_nowait()
                                except queue.Empty: pass
                                try: self.rx_queue.put_nowait(l)
                                except queue.Full: pass
                else:
                    time.sleep(0.01)
            except Exception as e:
                self.rx_queue.put(f"[reader thread died: {e}]")
                break

    def _process_queue(self):
        try:
            while True:
                line = self.rx_queue.get_nowait()
                try: self._on_line(line)
                except Exception as e:
                    self._log(f"[GUI parse error: {e!r} on line: {line!r}]","error")
        except queue.Empty: pass
        finally:
            self.root.after(50,self._process_queue)

    def _on_line(self,line):
        print(f"[UART] {line}")
        self._log(line,"received")
        # Ревью GUI-14: V/f принудительно остановлен прошивкой — закрыть сессию.
        if line.startswith("@VF:STOPPED"):
            self.vf_panel.on_stopped(line)
            return
        # AutoTuneTab handles @IDLE:*, @PARAMS:*, @IROT:*, @INERTIA:*
        if self.tab_at.on_line(line):
            return
        m=TLM_PREFIX_RE.match(line)
        if m:
            p=m.group(1); pl=m.group(2); kv=TLM_KV_RE.findall(pl)
            if kv:
                dd={k:int(v) for k,v in kv}
                if p=="PWM": self.tab_pwm.on_telemetry(p,dd)
                elif p=="ADC": self.tab_adc.on_telemetry(p,dd)
                elif p=="FOC": self.tab_foc.on_telemetry(p,dd)
                elif p=="VF": self.tab_pwm.vf_panel.on_telemetry(p,dd)
                elif p=="ENC": self.tab_pwm.vf_panel.on_telemetry(p,dd)
                elif p=="VFLOG": self.tab_pwm.vf_panel.on_telemetry(p,dd)
                elif p=="TRIG": self.tab_pwm.vf_panel.on_telemetry(p,dd)

    def _send(self,cmd):
        if self.ser and self.ser.is_open:
            try:
                print(f"[UART] >>> {cmd}")
                self.ser.write((str(cmd).strip()+"\r\n").encode()); self._log(f">>> {cmd}","sent")
            except Exception as e: self._log(f"Send error: {e}","error")
        else: self._log("Not connected","error")

    def _log(self,text,tag="received"):
        try:
            self.log_text.config(state=tk.NORMAL); self.log_text.insert(tk.END,text+"\n",tag)
            self.log_text.see(tk.END); self.log_text.config(state=tk.DISABLED)
            self._log_to_file(text, tag)
        except Exception as e:
            print(f"[LOG ERROR] {text} (error={e})")

    def _log_to_file(self, text, tag="received"):
        """Дублировать строку лога в файл logs/test_log_<дата>.log (авто-фиксация)."""
        try:
            if not hasattr(self, "_log_fh") or self._log_fh is None or self._log_fh.closed:
                os.makedirs("logs", exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._log_fh = open(os.path.join("logs", f"test_log_{stamp}.log"), "a", encoding="utf-8")
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._log_fh.write(f"{ts} [{tag}] {text}\n")
            self._log_fh.flush()
        except Exception as e:
            print(f"[LOG FILE ERROR] {text} (error={e})")

    def _close_log_file(self):
        try:
            if hasattr(self, "_log_fh") and self._log_fh and not self._log_fh.closed:
                self._log_fh.close()
        except Exception:
            pass

    def _copy_log(self,e=None):
        try:
            s=self.log_text.get("sel.first","sel.last")
            if s: self.root.clipboard_clear(); self.root.clipboard_append(s); self._set_status("Copied")
        except: pass

    def _clear_log(self,e=None):
        self.log_text.config(state=tk.NORMAL); self.log_text.delete("1.0",tk.END)
        self.log_text.config(state=tk.DISABLED); self._set_status("Log cleared")

    def _save_log(self):
        """Сохранить содержимое лога в текстовый файл."""
        path = os.path.join(os.getcwd(), 'pwm_test_log.txt')
        try:
            content = self.log_text.get("1.0", tk.END)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            self._set_status(f"Log saved to {os.path.basename(path)}")
        except Exception as e:
            self._log(f"Save failed: {e}", "error")

    def _set_status(self,text):
        try: self.statusbar.config(text=text)
        except: pass

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW",self._on_closing); self.root.mainloop()

    def _on_closing(self):
        self._close_log_file(); self._disconnect(); self.root.destroy()

from at_errors import AT_ERROR_CAUSES, at_error_cause

if __name__=="__main__":
    app=NucleoDebugTool(); app.run()
