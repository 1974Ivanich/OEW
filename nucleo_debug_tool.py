import tkinter as tk
from tkinter import ttk
import tkinter.filedialog as filedialog
import serial
import serial.tools.list_ports
import threading
import queue
import re
import time
import os
import shutil
import csv
import subprocess
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════
#  Константы sigrok
# ═══════════════════════════════════════════════════════════════════════
SIGROK_CLI_PATH = r"C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe"
SIGROK_DRIVER = "fx2lafw"

SALE_CH_PC0 = 0; SALE_CH_PA7 = 1; SALE_CH_PC1 = 2
SALE_CH_PB0 = 3; SALE_CH_PC2 = 4; SALE_CH_PB1 = 5
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
                print(f"[Sigrok] probe FAILED: {e}")
                self.available = False
            if tk_root is not None:
                tk_root.after(0, lambda: callback(self.available))
            else:
                callback(self.available)
        threading.Thread(target=worker, daemon=True).start()

    def capture_sync(self, digital_chs=None, analog_chs=None, duration_s=0.3, sample_rate=24_000_000):
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
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=duration_s*10+60)
            if os.path.exists(csv_path):
                return SigrokCapture(csv_path, sr_d)
            return None
        except Exception as e:
            print(f"[Sigrok] Capture error: {e}")
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
        
        self.dbg_btn.pack(side=tk.LEFT, padx=5)
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

    # _probe_direct removed (sigrok)def _update_view(self):
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
    CHANNELS = [
        ("Ch1","PC0 HIN_U1",0x01,SALE_CH_PC0), ("Ch2","PA7 LIN_U1",0x02,SALE_CH_PA7),
        ("Ch3","PC1 HIN_V1",0x04,SALE_CH_PC1), ("Ch4","PB0 LIN_V1",0x08,SALE_CH_PB0),
        ("Ch5","PC2 HIN_W1",0x10,SALE_CH_PC2), ("Ch6","PB1 LIN_W1",0x20,SALE_CH_PB1),
    ]


    def __init__(self, parent, send_fn, saleae=None):
        super().__init__(parent)
        self.send=send_fn; self.saleae=saleae
        self.columnconfigure(0,weight=1); self.columnconfigure(1,weight=1); self.columnconfigure(2,weight=1)
        self._build_channels_panel(); self._build_params_panel(); self._build_status_panel()
        self._build_saleae_panel()
        self.after(200,lambda:self.send("p?"))

    def _build_channels_panel(self):
        f=ttk.LabelFrame(self,text="Channels")
        f.grid(row=0,column=0,sticky="nsew",padx=5,pady=5)
        bf=ttk.Frame(f); bf.grid(row=0,column=0,columnspan=3,sticky="ew",pady=(0,5))
        ttk.Button(bf,text="Select All",command=self._select_all).pack(side=tk.LEFT,padx=2)
        ttk.Button(bf,text="Clear All",command=self._clear_all).pack(side=tk.LEFT,padx=2)
        self.ch_vars=[]; self.ch_indicators=[]
        for i,(nm,pn,_,_) in enumerate(self.CHANNELS):
            r=i+1; var=tk.BooleanVar(value=False); self.ch_vars.append(var)
            ttk.Checkbutton(f,variable=var,command=self._update_mask_preview).grid(row=r,column=0,sticky="w",padx=2)
            ind=tk.Label(f,text="●",fg="red",font=("Arial",14)); ind.grid(row=r,column=1,padx=4); self.ch_indicators.append(ind)
            ttk.Label(f,text=f"{nm}\n{pn}",justify=tk.LEFT).grid(row=r,column=2,sticky="w",padx=2)
        self.mask_preview=ttk.Label(f,text="mask = 0x00",foreground="gray")
        self.mask_preview.grid(row=len(self.CHANNELS)+1,column=0,columnspan=3,pady=(10,0))
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
        self.freq_label=ttk.Label(f,text="— kHz",foreground="blue",font=("Arial",10,"bold"))
        self.freq_label.grid(row=3,column=1,sticky="w",padx=5)
        bf=ttk.Frame(f); bf.grid(row=4,column=0,columnspan=2,pady=10,sticky="ew")
        ttk.Button(bf,text="▶ Start PWM",command=self._start_pwm).pack(side=tk.LEFT,padx=3)
        ttk.Button(bf,text="■ Stop PWM",command=self._stop_pwm).pack(side=tk.LEFT,padx=3)
        ttk.Button(bf,text="⟳ Refresh",command=self._refresh_status).pack(side=tk.LEFT,padx=3)
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
        self.moe_label=ttk.Label(f,text="MOE: —",font=("Arial",10,"bold"))
        self.moe_label.grid(row=5,column=0,sticky="w",padx=5,pady=2)
        self.cen_label=ttk.Label(f,text="CEN: —",font=("Arial",10,"bold"))
        self.cen_label.grid(row=6,column=0,sticky="w",padx=5,pady=2)
        f.columnconfigure(0,weight=1)

    def _build_saleae_panel(self):
        f=ttk.LabelFrame(self,text="Saleae Logic 2 — Auto Test")
        f.grid(row=1,column=0,columnspan=3,sticky="ew",padx=5,pady=5)
        self.sf=SaleaeConnectFrame(f,self.saleae,on_status_change=self._on_saleae_status)
        self.sf.pack(side=tk.LEFT,padx=5,pady=5)
        self.btn_auto=ttk.Button(f,text="⚡ Auto Test All Channels",command=self._auto_test_all)
        self.btn_auto.pack(side=tk.LEFT,padx=10,pady=5)
        self.btn_dt=ttk.Button(f,text="📏 Measure Dead-Time",command=self._measure_deadtime)
        self.btn_dt.pack(side=tk.LEFT,padx=5,pady=5)
        self._update_saleae_buttons()

    def _on_saleae_status(self,ok): self._update_saleae_buttons()
    def _update_saleae_buttons(self):
        st="normal" if (self.saleae and self.saleae.available) else "disabled"
        self.btn_auto.config(state=st); self.btn_dt.config(state=st)

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
    def _update_freq(self):
        try: 
            root=self.winfo_toplevel()
            tclk=getattr(root,'tclk',10_300_000)
            self.freq_label.config(text=f"{tclk/(self.arr_var.get()+1)/2/1000:.2f} kHz")
        except: pass
    def _start_pwm(self): self.send(f"p={self.arr_var.get()},{self.duty_var.get()},{self.dt_var.get()},{self._get_mask()}")
    def _stop_pwm(self): self.send(f"p={self.arr_var.get()},{self.duty_var.get()},{self.dt_var.get()},0")
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

    def _auto_test_all(self):
        self._log_local("Starting Auto Test...","sent")
        print("[PWM] Starting Auto Test...", flush=True)
        self.btn_auto.config(state=tk.DISABLED,text="⏳ Testing...")
        def cleanup():
            try:
                td = os.path.abspath('_saleae_tmp')
                if os.path.exists(td): shutil.rmtree(td, ignore_errors=True)
            except: pass
        def fail():
            cleanup()
            self.after(0,lambda: self._log_local("Saleae: operation failed or timed out","error"))
            self.after(0,lambda: self.btn_auto.config(state=tk.NORMAL,text="⚡ Auto Test All Channels"))
        def worker():
            if not self.saleae or not self.saleae.available:
                self.after(0,fail); return
            capture = self.saleae.capture_sync(digital_chs=[0,1,2,3,4,5], duration_s=0.5)
            if not capture:
                self.after(0,fail); return
            import threading as _t, time as _tm
            done = []
            def waiter():
                try: capture.wait(); done.append(1)
                except: pass
            _t.Thread(target=waiter,daemon=True).start()
            _tm.sleep(2.5)
            if not done:
                self.after(0,fail); return
            arr,dp=self.arr_var.get(),self.duty_var.get()
            root=self.winfo_toplevel()
            tclk=getattr(root,'tclk',10_000_000)
            ef=tclk/(arr+1)/2; ed=dp/100.0
            results=[]
            for v,(nm,pn,_,sc) in zip(self.ch_vars,self.CHANNELS):
                if not v.get(): continue
                f=self.saleae.measure_freq(capture,sc)
                d=self.saleae.measure_duty(capture,sc)
                if f is None or d is None:
                    results.append((False,f"FAIL: {pn} — no signal")); continue
                # LIN channels are complementary (inverted) - expect 100-duty%
                exp_d = ed if "HIN" in pn else (1.0 - ed)
                fe=abs(f-ef)/ef if ef>0 else 1; de=abs(d-exp_d)
                okf=fe<=0.10 and de<=0.10
                s="PASS" if okf else "FAIL"
                results.append((okf,f"{s}: {pn}  {f:.1f}Hz (exp {ef:.1f}, err {fe*100:.1f}%)  {d*100:.1f}% (exp {exp_d*100:.0f}%, err {de*100:.1f}%)"))
            cleanup()
            self.after(0,lambda: self._auto_test_done(results))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_test_done(self,results):
        self.btn_auto.config(state=tk.NORMAL,text="⚡ Auto Test All Channels")
        pc=sum(1 for ok,_ in results if ok); fc=len(results)-pc
        for ok,msg in results: self._log_local(msg,"meas" if ok else "error")
        s=f"=== Result: {pc} PASS, {fc} FAIL ==="
        self._log_local(s,"meas" if fc==0 else "error",update_status=True)

    def _measure_deadtime(self):
        if not self.saleae or not self.saleae.available: return
        self.btn_dt.config(state=tk.DISABLED,text="⏳ Measuring...")
        threading.Thread(target=self._measure_dt_worker,daemon=True).start()

    def _measure_dt_worker(self):
        try:
            td = os.path.abspath('_saleae_tmp')
            if os.path.exists(td): shutil.rmtree(td, ignore_errors=True)
        except: pass
        try:
            capture=self.saleae.capture_sync(digital_chs=[0,1,2,3,4,5],duration_s=0.5)
            if not capture:
                self.after(0,lambda: self._log_local("Saleae: capture returned None","error"))
                self.after(0,lambda: self.btn_dt.config(state=tk.NORMAL,text="📏 Measure Dead-Time"))
                return
            # Wait with timeout 3 seconds
            import threading as _thr
            def _wait():
                try: capture.wait()
                except: pass
            wt = _thr.Thread(target=_wait, daemon=True)
            wt.start()
            wt.join(timeout=3.0)
            if wt.is_alive():
                self.after(0,lambda: self._log_local("Saleae: capture timed out (3s)","error"))
                self.after(0,lambda: self.btn_dt.config(state=tk.NORMAL,text="📏 Measure Dead-Time"))
                return
        except Exception as _e:
            self.after(0,lambda: self._log_local(f"Saleae error: {_e}","error"))
            self.after(0,lambda: self.btn_dt.config(state=tk.NORMAL,text="📏 Measure Dead-Time"))
            return
        pairs=[("U",SALE_CH_PC0,SALE_CH_PA7),("V",SALE_CH_PC1,SALE_CH_PB0),("W",SALE_CH_PC2,SALE_CH_PB1)]
        results=[]
        for ph,ch,cl in pairs:
            r=self.saleae.measure_deadtime(capture,ch,cl)
            if r is None: results.append((False,f"Phase {ph}: no valid transitions"))
            else: results.append((True,f"Phase {ph}: dt_rise={r[0]:.0f} ns, dt_fall={r[1]:.0f} ns"))
        try:
            td = os.path.abspath('_saleae_tmp')
            if os.path.exists(td): shutil.rmtree(td, ignore_errors=True)
        except: pass
        self.after(0,lambda: self._measure_dt_done(results))

    def _measure_dt_done(self,results):
        self.btn_dt.config(state=tk.NORMAL,text="📏 Measure Dead-Time")
        self._log_local("=== Dead-Time Measurement ===","sent")
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
            self.moe_label.config(text=f"MOE: {'ON' if moe else 'OFF'}",foreground="green" if moe else "red")
            self.cen_label.config(text=f"CEN: {'ON' if cen else 'OFF'}",foreground="green" if cen else "red")
            self.set_indicators(cc&0x3F)

# ═══════════════════════════════════════════════════════════════════════
#  ADCTab
# ═══════════════════════════════════════════════════════════════════════

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
        self.l_in=ttk.Label(vf,text="IN: — raw",font=("Consolas",11)); self.l_in.grid(row=1,column=0,padx=10,pady=5,sticky="w")
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
                w=csv.writer(f); w.writerow(['timestamp','I1_raw','I2_raw','IN_raw','VBUS_raw']); w.writerows(self.data_buffer)
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
        if "IN" in data: self.l_in.config(text=f"IN: {data['IN']} raw")
        if "VBUS" in data: self.l_vb.config(text=f"VBUS: {data['VBUS']} raw")
        if all(k in data for k in ['I1','I2','IN','VBUS']):
            ts=datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            self.data_buffer.append([ts,data['I1'],data['I2'],data['IN'],data['VBUS']])
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
        ttk.Button(f,text="💾 Save CSV",command=self._save_csv).pack(pady=5)

    def _build_telemetry_panel(self):
        f=ttk.LabelFrame(self,text="Telemetry"); f.grid(row=0,column=1,sticky="nsew",padx=5,pady=5)
        self.l_i1=ttk.Label(f,text="I1: — raw",font=("Consolas",10)); self.l_i1.grid(row=0,column=0,padx=10,pady=5,sticky="w")
        self.l_i2=ttk.Label(f,text="I2: — raw",font=("Consolas",10)); self.l_i2.grid(row=0,column=1,padx=10,pady=5,sticky="w")
        self.l_in=ttk.Label(f,text="IN: — raw",font=("Consolas",10)); self.l_in.grid(row=1,column=0,padx=10,pady=5,sticky="w")
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
            capture=self.saleae.capture_sync(digital_chs=[0,1,2,3,4,5],duration_s=0.5)
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
            self.after(0,lambda: self._log_local(f"Saleae error: {_e}","error"))
            self.after(0,lambda: self.bc.config(state=tk.NORMAL,text="📊 Capture FOC Waveforms"))
            return
        results=[]
        for ci in range(6):
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
        if "IN" in data: self.l_in.config(text=f"IN: {data['IN']} raw")
        if "VBUS" in data: self.l_vb.config(text=f"VBUS: {data['VBUS']} raw")
        if "Speed" in data: self.l_sp.config(text=f"Speed: {data['Speed']} RPM")
        if "Theta" in data: self.l_th.config(text=f"Theta: {data['Theta']/1000:.3f} rad")
        run=data.get("RUN",1 if data.get("Speed",0)>0 else 0)
        if run: self.stl.config(text="Status: RUNNING",foreground="green")
        else: self.stl.config(text="Status: STOPPED",foreground="red")

# ═══════════════════════════════════════════════════════════════════════
#  NucleoDebugTool
# ═══════════════════════════════════════════════════════════════════════

class NucleoDebugTool:
    def __init__(self):
        self.root=tk.Tk()
        self.root.title("Nucleo Debug Tool")
        self.root.geometry("950x750")
        self.root.minsize(800,600)
        self.ser=None; self.reader_thread=None; self.stop_event=threading.Event(); self.rx_queue=queue.Queue()
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
        self.notebook.add(self.tab_pwm,text="1. PWM Test")
        self.notebook.add(self.tab_adc,text="2. ADC Test")
        self.notebook.add(self.tab_foc,text="3. FOC Full")

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
        self.ser=None; self.btn_conn.config(text="Connect"); self.status_ind.config(fg="red")
        self.status_lbl.config(text="Disconnected"); self._log("Disconnected","received"); self._set_status("Disconnected")

    def _reader_loop(self):
        buf=""
        while not self.stop_event.is_set():
            try:
                if self.ser and self.ser.in_waiting>0:
                    buf += self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    while '\n' in buf:
                        l, buf = buf.split('\n', 1)
                        l = l.strip()
                        if l:
                            self.rx_queue.put(l)
                else:
                    time.sleep(0.01)
            except Exception as e:
                self.rx_queue.put(f"[reader thread died: {e}]")
                break

    def _process_queue(self):
        try:
            while True: self._on_line(self.rx_queue.get_nowait())
        except queue.Empty: pass
        self.root.after(50,self._process_queue)

    def _on_line(self,line):
        self._log(line,"received")
        m=TLM_PREFIX_RE.match(line)
        if m:
            p=m.group(1); pl=m.group(2); kv=TLM_KV_RE.findall(pl)
            if kv:
                dd={k:int(v) for k,v in kv}
                if p=="PWM": self.tab_pwm.on_telemetry(p,dd)
                elif p=="ADC": self.tab_adc.on_telemetry(p,dd)
                elif p=="FOC": self.tab_foc.on_telemetry(p,dd)

    def _send(self,cmd):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((str(cmd).strip()+"\r\n").encode()); self._log(f">>> {cmd}","sent")
            except Exception as e: self._log(f"Send error: {e}","error")
        else: self._log("Not connected","error")

    def _log(self,text,tag="received"):
        try:
            self.log_text.config(state=tk.NORMAL); self.log_text.insert(tk.END,text+"\n",tag)
            self.log_text.see(tk.END); self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"[LOG ERROR] {text} (error={e})")

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
        self._disconnect(); self.root.destroy()

if __name__=="__main__":
    app=NucleoDebugTool(); app.run()
