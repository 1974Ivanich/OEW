# Motor serial link - UART communication with STM32 controller
import json
import threading
import time
from collections import deque

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


class StreamSample:
    """One row from the steady-state JSON telemetry stream (~20 Hz)."""
    __slots__ = (
        "ia", "ib", "ic", "ia2", "ib2", "ic2",
        "izs", "irms", "freq", "target_freq",
        "power", "target_current", "zsc_integ",
        "fault", "ts",
        "raw_a1", "raw_b1", "raw_c1", "raw_a2", "raw_b2", "raw_c2",
        "isr_ticks", "calibrated",
        "id", "iq", "vd", "vq", "wr", "theta",
        "mcu_ts", "id_t", "iq_t", "slip", "psi", "te",
        "isr_hz", "txd", "run", "foc_state",
        "sat", "vd_raw", "vq_raw",
        "pi_id_i", "pi_iq_i", "pi_spd_i",
        "isr_us_avg", "isr_us_max",
        "p_elec", "q_elec", "e_alpha", "e_beta",
        "flg_recs", "flg_pages", "flg_en", "flg_full",
        "kdt", "dtc_corr", "dtc_enorm",
        "dtc_enabled", "dtc_compensate", "dtc_converged", "dtc_hold",
    )

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0.0))


class BurstSample:
    """One row from an ISR-rate burst capture window.
    New firmware format (40 cols): idx,ts,ia1..ic2,izs,ang,id,iq,idT,iqT,
    vd,vq,vd_raw,vq_raw,wr,slip,psi,te,pi_id_i,pi_iq_i,pi_spd_i,sat_flags,
    e_alpha,e_beta,tgt_spd,theta_jit,diq_dt,dwr_dt,p_elec,q_elec,
    d1a..d2c. Legacy 32/26/15-col formats still supported."""
    __slots__ = ("idx", "ts", "ia1", "ib1", "ic1", "ia2", "ib2", "ic2",
                 "izs", "ang",
                 "id", "iq", "id_t", "iq_t", "vd", "vq",
                 "vd_raw", "vq_raw",
                 "wr", "slip", "psi", "te",
                 "pi_id_i", "pi_iq_i", "pi_spd_i", "sat_flags",
                 "e_alpha", "e_beta", "tgt_spd", "theta_jit",
                 "diq_dt", "dwr_dt", "p_elec", "q_elec",
                 "d1a", "d1b", "d1c", "d2a", "d2b", "d2c")

    def __init__(self, idx=0, **kw):
        self.idx = idx
        for k in self.__slots__[1:]:
            setattr(self, k, kw.get(k, 0))


class FlashLogSnap:
    """One row from flash black box dump ($FLASHLOG..$END).
    Fields: ts,id,iq,vd,vq,wr,slip,te,psi,sat,foc,p,q,ea,eb,kdt,fault
    All values are fixed-point scaled (x1000/x100/x10) as in firmware."""
    __slots__ = ("ts", "id", "iq", "vd", "vq", "wr", "slip", "te", "psi",
                 "sat", "foc", "p", "q", "ea", "eb", "kdt", "fault")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k, 0))


class MotorSerialLink:
    DEFAULT_BAUDRATE = 921600
    MAX_HISTORY = 1000

    def __init__(self, port=None, baudrate=DEFAULT_BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self._ser = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self.history = deque(maxlen=self.MAX_HISTORY)
        self.latest = None
        self.fault = False
        self.fault_reason = ""
        self.stream_enabled = False
        self.last_text_lines = deque(maxlen=20)
        self.on_text = None

        # Burst capture state
        self._burst_lock = threading.Lock()
        self.burst_samples = []          # list[BurstSample] once fully received
        self.burst_isr_hz = 0.0
        self.burst_decim = 1             # decimation factor from header
        self.burst_auto = 0              # auto-trigger flag from header
        self.burst_ready = False         # True when $END received
        self.burst_progress = 0.0        # 0..1 during dump
        self._burst_raw = []             # accumulates CSV lines during $BURST..$END
        self._burst_total = 0
        self._burst_active = False
        # Burst parameters from $PARAMS line (if present)
        self.burst_params = {}
        self._burst_params_raw = None
        self.on_burst_ready = None       # callback(burst_samples, isr_hz) when done

        # Event log state ($EVENTS..$END block)
        self.events = []                 # list[(mcu_ts:int, name:str, v0, v1, v2)]
        self._events_raw = []
        self._events_active = False
        self.on_events = None            # callback(events) when block complete

        # Flash log state ($FLASHLOG..$END block)
        self.flashlog_samples = []       # list[FlashLogSnap] once fully received
        self.flashlog_ready = False
        self.flashlog_count = 0
        self._flashlog_raw = []
        self._flashlog_active = False
        self.on_flashlog_ready = None    # callback(samples) when done

        # Auto-tune test state
        self._test_raw = []
        self.test_active = False
        self.on_test_ready = None

        # DTC info callback
        self.on_dtc_info = None

    @staticmethod
    def list_ports():
        if serial is None:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    @property
    def is_open(self):
        return self._ser is not None and self._ser.is_open

    def open(self):
        if serial is None:
            raise RuntimeError("pyserial not installed")
        if self.is_open:
            return
        self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def close(self):
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        with self._lock:
            self.history.clear()
            self.latest = None
            self.fault = False
            self.stream_enabled = False

    def _reader_loop(self):
        buf = b""
        while not self._stop_evt.is_set():
            try:
                chunk = self._ser.read(4096)
            except Exception:
                break
            if not chunk:
                continue
            buf += chunk
            
            # Защита от переполнения буфера, если МК слать данные без \n
            if len(buf) > 4096:
                buf = buf[-2048:] 
            
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip(b"\r ")
                try:
                    text = line.decode("ascii", errors="replace")
                except Exception:
                    continue
                if text:
                    self._handle_line(text)

    def _handle_line(self, text):
        if text.startswith("$PARAMS,"):
            self._parse_burst_params(text)
        elif text.startswith("$BURST,"):
            self._burst_start(text)
        elif text.startswith("$EVENTS,"):
            self._events_active = True
            self._events_raw = []
        elif text.startswith("$FLASHLOG,"):
            self._flashlog_active = True
            self._flashlog_raw = []
            try:
                self.flashlog_count = int(text.split(",")[1])
            except (IndexError, ValueError):
                self.flashlog_count = 0
        elif text.startswith("$END"):
            if self._events_active:
                self._events_finish()
            elif self._flashlog_active:
                self._flashlog_finish()
            else:
                self._burst_finish()
        elif text.startswith("$TESTL_BEGIN"):
            self.test_active = True
            self._test_raw = []
        elif text.startswith("$TESTL_END"):
            self.test_active = False
            if self.on_test_ready:
                try:
                    self.on_test_ready(self._test_raw)
                except Exception:
                    pass
        elif text.startswith("$DTC,"):
            self._parse_dtc_info(text)
        elif self.test_active:
            self._test_raw.append(text)
        elif self._events_active:
            self._events_raw.append(text)
        elif self._flashlog_active:
            # Skip header line (ts,id,iq,...), accumulate data rows
            if not text.startswith("ts,"):
                self._flashlog_raw.append(text)
        elif self._burst_active:
            # Still inside burst block — accumulate CSV data rows
            self._burst_accumulate(text)
        elif text.startswith("{"):
            self._parse_json(text)
        else:
            self._handle_text(text)

    # ---- steady-state JSON telemetry ----

    def _parse_dtc_info(self, text):
        """Parse $DTC,Kdt=12.34,mu=60.00,sign=1.00,corr=0.00123,enorm=0.0456,en=1,cmp=1,conv=0,hold=1,..."""
        try:
            dtc = {}
            parts = text.split(",")
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    k = k.strip()
                    try:
                        dtc[k] = float(v)
                    except ValueError:
                        dtc[k] = v
            if self.on_dtc_info:
                try:
                    self.on_dtc_info(dtc)
                except Exception:
                    pass
        except Exception:
            pass

    def _parse_json(self, text):
        try:
            d = json.loads(text)
        except Exception:
            return
        try:
            s = StreamSample(
                ia=float(d.get("Ia1", 0.0)),
                ib=float(d.get("Ib1", 0.0)),
                ic=float(d.get("Ic1", 0.0)),
                ia2=float(d.get("Ia2", 0.0)),
                ib2=float(d.get("Ib2", 0.0)),
                ic2=float(d.get("Ic2", 0.0)),
                izs=float(d.get("Izs", 0.0)),
                irms=float(d.get("Irms", 0.0)),
                freq=float(d.get("f", 0.0)),
                target_freq=float(d.get("tgF", 0.0)),
                power=float(d.get("P", 0.0)),
                target_current=float(d.get("tgI", 0.0)),
                zsc_integ=float(d.get("zint", 0.0)),
                fault=int(d.get("flt", 0)),
                raw_a1=int(d.get("rA1", 0)),
                raw_b1=int(d.get("rB1", 0)),
                raw_c1=int(d.get("rC1", 0)),
                raw_a2=int(d.get("rA2", 0)),
                raw_b2=int(d.get("rB2", 0)),
                raw_c2=int(d.get("rC2", 0)),
                isr_ticks=int(d.get("ts", d.get("ticks", 0))),
                calibrated=int(d.get("cal", 0)),
                id=float(d.get("Id", 0.0)),
                iq=float(d.get("Iq", 0.0)),
                vd=float(d.get("Vd", 0.0)),
                vq=float(d.get("Vq", 0.0)),
                wr=float(d.get("Wr", 0.0)),
                theta=float(d.get("Th", 0.0)),
                ts=time.time(),
                mcu_ts=int(d.get("ts", 0)),
                id_t=float(d.get("IdT", 0.0)),
                iq_t=float(d.get("IqT", 0.0)),
                slip=float(d.get("slip", 0.0)),
                psi=float(d.get("psi", 0.0)),
                te=float(d.get("Te", 0.0)),
                isr_hz=float(d.get("ISR_Hz", 0.0)),
                txd=int(d.get("txd", 0)),
                run=int(d.get("run", 0)),
                foc_state=int(d.get("foc", 0)),
                sat=int(d.get("sat", 0)),
                vd_raw=float(d.get("VdR", 0.0)),
                vq_raw=float(d.get("VqR", 0.0)),
                pi_id_i=float(d.get("Iid", 0.0)),
                pi_iq_i=float(d.get("Iiq", 0.0)),
                pi_spd_i=float(d.get("Isp", 0.0)),
                isr_us_avg=int(d.get("isu", 0)),
                isr_us_max=int(d.get("isum", 0)),
                p_elec=float(d.get("Pe", 0.0)),
                q_elec=float(d.get("Qe", 0.0)),
                e_alpha=float(d.get("Ea", 0.0)),
                e_beta=float(d.get("Eb", 0.0)),
            )
            # DTC live fields from JSON stream
            s.kdt = float(d.get("Kdt", 0.0))
            s.dtc_corr = float(d.get("Dcorr", 0.0))
            s.dtc_enorm = float(d.get("Den", 0.0))
            dflags = str(d.get("Dflags", "0000"))
            if len(dflags) >= 4:
                s.dtc_enabled = int(dflags[0])
                s.dtc_compensate = int(dflags[1])
                s.dtc_converged = int(dflags[2])
                s.dtc_hold = int(dflags[3])
            # Parse flash log status: "flg":"recs/pages/en/full"
            flg = d.get("flg", "0/0/0/0")
            if isinstance(flg, str):
                flg_parts = flg.split("/")
                s.flg_recs = int(flg_parts[0]) if len(flg_parts) > 0 else 0
                s.flg_pages = int(flg_parts[1]) if len(flg_parts) > 1 else 0
                s.flg_en = int(flg_parts[2]) if len(flg_parts) > 2 else 0
                s.flg_full = int(flg_parts[3]) if len(flg_parts) > 3 else 0
        except Exception:
            return
        with self._lock:
            self.latest = s
            self.history.append(s)

    # ---- burst capture protocol ----

    def _parse_burst_params(self, text):
        """Parse $PARAMS,Rs=..,Rr=..,... line and store into burst_params dict."""
        parts = text[len("$PARAMS,"):].strip().split(",")
        params = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.strip()] = v.strip()
        with self._burst_lock:
            self.burst_params = params

    def _burst_start(self, header):
        """$BURST,<n>,<isr_hz>[,<trig_idx>[,<auto>[,<decim>]]] — begins a burst data block."""
        with self._burst_lock:
            self._burst_raw = []
            self.burst_samples = []
            self.burst_ready = False
            self.burst_progress = 0.0
            self._burst_active = True
            try:
                parts = header.split(",")
                self._burst_total = int(parts[1])
                self.burst_isr_hz = float(parts[2])
                self.burst_trig_idx = int(parts[3]) if len(parts) > 3 else 0
                self.burst_auto = int(parts[4]) if len(parts) > 4 else 0
                self.burst_decim = int(parts[5]) if len(parts) > 5 else 1
            except (IndexError, ValueError):
                self._burst_total = 0
                self.burst_isr_hz = 0.0
                self.burst_trig_idx = 0
                self.burst_auto = 0
                self.burst_decim = 1

    def _burst_accumulate(self, text):
        """Accumulate one CSV row inside a burst block."""
        with self._burst_lock:
            self._burst_raw.append(text)
            if self._burst_total > 0:
                self.burst_progress = min(len(self._burst_raw) / self._burst_total, 1.0)

    def _burst_finish(self):
        """$END — burst dump complete, parse all rows into BurstSample list."""
        samples = []
        with self._burst_lock:
            raw = list(self._burst_raw)
            total = self._burst_total
            isr_hz = self.burst_isr_hz
            self._burst_raw = []
            self._burst_active = False
        for line in raw:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 15:
                continue
            try:
                if len(parts) >= 40:  # new format with observer residuals + power + derivatives
                    samples.append(BurstSample(
                        idx=int(parts[0]), ts=int(parts[1]),
                        ia1=int(parts[2]), ib1=int(parts[3]), ic1=int(parts[4]),
                        ia2=int(parts[5]), ib2=int(parts[6]), ic2=int(parts[7]),
                        izs=int(parts[8]), ang=int(parts[9]),
                        id=int(parts[10]), iq=int(parts[11]),
                        id_t=int(parts[12]), iq_t=int(parts[13]),
                        vd=int(parts[14]), vq=int(parts[15]),
                        vd_raw=int(parts[16]), vq_raw=int(parts[17]),
                        wr=int(parts[18]), slip=int(parts[19]),
                        psi=int(parts[20]), te=int(parts[21]),
                        pi_id_i=int(parts[22]), pi_iq_i=int(parts[23]),
                        pi_spd_i=int(parts[24]), sat_flags=int(parts[25]),
                        e_alpha=int(parts[26]), e_beta=int(parts[27]),
                        tgt_spd=int(parts[28]), theta_jit=int(parts[29]),
                        diq_dt=int(parts[30]), dwr_dt=int(parts[31]),
                        p_elec=int(parts[32]), q_elec=int(parts[33]),
                        d1a=int(parts[34]), d1b=int(parts[35]), d1c=int(parts[36]),
                        d2a=int(parts[37]), d2b=int(parts[38]), d2c=int(parts[39]),
                    ))
                elif len(parts) >= 32:  # intermediate format with vd_raw/vq_raw/PI integrators/sat_flags
                    samples.append(BurstSample(
                        idx=int(parts[0]), ts=int(parts[1]),
                        ia1=int(parts[2]), ib1=int(parts[3]), ic1=int(parts[4]),
                        ia2=int(parts[5]), ib2=int(parts[6]), ic2=int(parts[7]),
                        izs=int(parts[8]), ang=int(parts[9]),
                        id=int(parts[10]), iq=int(parts[11]),
                        id_t=int(parts[12]), iq_t=int(parts[13]),
                        vd=int(parts[14]), vq=int(parts[15]),
                        vd_raw=int(parts[16]), vq_raw=int(parts[17]),
                        wr=int(parts[18]), slip=int(parts[19]),
                        psi=int(parts[20]), te=int(parts[21]),
                        pi_id_i=int(parts[22]), pi_iq_i=int(parts[23]),
                        pi_spd_i=int(parts[24]), sat_flags=int(parts[25]),
                        d1a=int(parts[26]), d1b=int(parts[27]), d1c=int(parts[28]),
                        d2a=int(parts[29]), d2b=int(parts[30]), d2c=int(parts[31]),
                    ))
                elif len(parts) >= 26:  # intermediate format with FOC internals
                    samples.append(BurstSample(
                        idx=int(parts[0]), ts=int(parts[1]),
                        ia1=int(parts[2]), ib1=int(parts[3]), ic1=int(parts[4]),
                        ia2=int(parts[5]), ib2=int(parts[6]), ic2=int(parts[7]),
                        izs=int(parts[8]), ang=int(parts[9]),
                        id=int(parts[10]), iq=int(parts[11]),
                        id_t=int(parts[12]), iq_t=int(parts[13]),
                        vd=int(parts[14]), vq=int(parts[15]),
                        wr=int(parts[16]), slip=int(parts[17]),
                        psi=int(parts[18]), te=int(parts[19]),
                        d1a=int(parts[20]), d1b=int(parts[21]), d1c=int(parts[22]),
                        d2a=int(parts[23]), d2b=int(parts[24]), d2c=int(parts[25]),
                    ))
                else:  # legacy 15-column format
                    samples.append(BurstSample(
                        idx=int(parts[0]),
                        ia1=int(parts[1]), ib1=int(parts[2]), ic1=int(parts[3]),
                        ia2=int(parts[4]), ib2=int(parts[5]), ic2=int(parts[6]),
                        izs=int(parts[7]),
                        ang=int(parts[8]),
                        d1a=int(parts[9]), d1b=int(parts[10]), d1c=int(parts[11]),
                        d2a=int(parts[12]), d2b=int(parts[13]), d2c=int(parts[14]),
                    ))
            except ValueError:
                continue
        with self._burst_lock:
            self.burst_samples = samples
            self.burst_ready = True
            self.burst_progress = 1.0
        if self.on_burst_ready:
            try:
                self.on_burst_ready(samples, isr_hz)
            except Exception:
                pass

    # ---- MCU event log ($EVENTS..$END) ----

    def _events_finish(self):
        raw = list(self._events_raw)
        self._events_raw = []
        self._events_active = False
        events = []
        for line in raw:
            parts = line.strip().split(",")
            if len(parts) < 5:
                continue
            try:
                events.append((int(parts[0]), parts[1],
                               float(parts[2]), float(parts[3]), float(parts[4])))
            except ValueError:
                continue
        self.events = events
        if self.on_events:
            try:
                self.on_events(events)
            except Exception:
                pass

    def get_events(self):
        """Request the MCU event log; result arrives via on_events / self.events."""
        return self.send("events")

    # ---- flash black box log ($FLASHLOG..$END) ----

    def _flashlog_finish(self):
        raw = list(self._flashlog_raw)
        self._flashlog_raw = []
        self._flashlog_active = False
        samples = []
        for line in raw:
            parts = line.strip().split(",")
            if len(parts) < 17:
                continue
            try:
                samples.append(FlashLogSnap(
                    ts=int(parts[0]),
                    id=int(parts[1]), iq=int(parts[2]),
                    vd=int(parts[3]), vq=int(parts[4]),
                    wr=int(parts[5]), slip=int(parts[6]),
                    te=int(parts[7]), psi=int(parts[8]),
                    sat=int(parts[9]), foc=int(parts[10]),
                    p=int(parts[11]), q=int(parts[12]),
                    ea=int(parts[13]), eb=int(parts[14]),
                    kdt=int(parts[15]), fault=int(parts[16]),
                ))
            except ValueError:
                continue
        self.flashlog_samples = samples
        self.flashlog_ready = True
        if self.on_flashlog_ready:
            try:
                self.on_flashlog_ready(samples)
            except Exception:
                pass

    def dump_flash(self):
        """Request flash black box dump; result via on_flashlog_ready / self.flashlog_samples."""
        return self.send("dumpflash")

    def clear_flash(self):
        """Erase all flash black box records."""
        return self.send("clearflash")

    # ---- text event handling ----

    def _handle_text(self, text):
        up = text.upper()
        self.last_text_lines.append(text)
        if self.on_text:
            try:
                self.on_text(text)
            except Exception:
                pass
        if "STOPPED" in up or "FAULT" in up:
            with self._lock:
                self.fault = True
                self.fault_reason = text
        elif "STARTED" in up or "FAULT CLEARED" in up:
            with self._lock:
                self.fault = False
                self.fault_reason = ""
        elif "STREAM ON" in up:
            with self._lock:
                self.stream_enabled = True
        elif "STREAM OFF" in up:
            with self._lock:
                self.stream_enabled = False

    # ---- command helpers ----

    def send(self, cmd):
        if not self.is_open:
            return False
        data = (cmd + "\r\n").encode("ascii")
        try:
            self._ser.write(data)
            return True
        except Exception:
            return False

    def start(self):
        return self.send("start")

    def stop(self):
        return self.send("stop")

    def stream_on(self):
        ok = self.send("stream on")
        if ok:
            with self._lock:
                self.stream_enabled = True
        return ok

    def stream_off(self):
        ok = self.send("stream off")
        if ok:
            with self._lock:
                self.stream_enabled = False
        return ok

    def set_speed(self, value):
        v = max(-400.0, min(400.0, float(value)))
        return self.send("s" + repr(v))

    def set_power(self, value):
        v = max(0.0, min(0.98, float(value)))
        return self.send("p" + repr(v))

    def set_current(self, value):
        v = max(0.0, min(4.75, float(value)))
        return self.send("i" + repr(v))

    def set_motor_params(self, rs, rr, ls, lr, lm):
        cmd = f'MP{rs},{rr},{ls},{lr},{lm}'
        return self.send(cmd)

    def fault_clear(self):
        ok = self.send("faultclr")
        if ok:
            with self._lock:
                self.fault = False
                self.fault_reason = ""
        return ok

    def zsc_atune(self):
        return self.send("zscatune")

    def calib(self):
        return self.send("calib")

    def apply_motor_params(self, kp, ki, imax, irms, didt):
        """Отправляет рассчитанные параметры на МК для применения на лету"""
        self.send("KP" + repr(kp))
        time.sleep(0.02)
        self.send("KI" + repr(ki))
        time.sleep(0.02)
        self.send("IM" + repr(imax))
        time.sleep(0.02)
        self.send("IR" + repr(irms))
        time.sleep(0.02)
        self.send("DT" + repr(didt))
        time.sleep(0.02)
        
    def capture(self):
        """Arm burst capture on the MCU. Data arrives asynchronously;
        check burst_ready or use on_burst_ready callback."""
        with self._burst_lock:
            self.burst_ready = False
            self.burst_samples = []
            self._burst_raw = []
            self.burst_progress = 0.0
        return self.send("capture")

    def test_inductance(self):
        with self._burst_lock:
            self.burst_ready = False
            self.burst_samples = []
            self._burst_raw = []
            self.burst_progress = 0.0
        return self.send("testL")

    def test_resistance(self):
        return self.send("testR")

    # ---- DTC command helpers ----

    def dtc_info(self):
        return self.send("dtcinfo")

    def dtc_adapt_on(self):
        return self.send("dtc on")

    def dtc_adapt_off(self):
        return self.send("dtc off")

    def dtc_comp_on(self):
        return self.send("dtccomp on")

    def dtc_comp_off(self):
        return self.send("dtccomp off")

    def dtc_set_kdt(self, val):
        return self.send("DK" + repr(float(val)))

    def dtc_set_mu(self, val):
        return self.send("DM" + repr(float(val)))

    def dtc_set_sign(self, val):
        return self.send("DS" + repr(int(val)))

    def dtc_reset(self):
        return self.send("dtcreset")