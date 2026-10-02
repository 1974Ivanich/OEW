"""Shunt-channel injection check on the PC-3 bench (01.10.2026) - LOW VOLTAGE.

Purpose (operator decision 01.10.2026): qualify the SCALE and SIGN of the two
DC-link shunt channels (I1 = PA0/ADC1_IN1, I2 = PA1/ADC2_IN2) with a KNOWN
stimulus, because the `al` aperture only proves chain/timing (its DC-link signal
is ~9 % of the phase current - 63/281 mA - i.e. below any reference SNR budget).
Procedure and stimulus table: docs/INJECTION_PLAN_20261001.md (published copy
of the PC-3 `ci733/INJECTION_PLAN_20261001.md`).

Channel design scale (src/adc.h, confirmed in this image):
  ADC_DC_SHUNT_UV_PER_A = 63000  -> 63 mV at the pin per 1 A
  LSB  = 3300/4095 = 0.80586 mV -> 12.7915 mA per count
  full scale = +/-2048 counts   -> +/-26.2 A (+/-1.290 V at the pin)
  1 A <=> 63.0 mV <=> 78.18 counts;  100 mA <=> 6.3 mV <=> 7.8 counts

SAFETY (fail-closed, checked before ANY readback):
  * the DC-link MUST be off and bled, and that is proven with a MEASURED
    offset - never assumed. `cv` (PWM off) must answer
    `@ADC:CV:OK:offset_vbus=N`; otherwise (firmware refuses rc=-2 "VBUS not at
    zero", ADC busy, old image without `cv`) the run is fail-closed. Then
    median(raw_vbus - N) and max(raw_vbus - N) over `--windows` reads must stay
    within VBUS_RESID_MAX_RAW / VBUS_RESID_MAX_PEAK and the implied bus voltage
    below VBUS_MAX_MV. A silent `offset = 0` is forbidden;
  * units: `a` answers with RAW COUNTS (`@ADC:I1=..:I2=..:Ires=..:VBUS=<raw>`) -
    5000 mV is about 50 counts, so raw counts are never compared against a mV
    threshold. mV comes only from an offset-aware source (`ci 0` field
    `vbus_mv`, or `(raw - offset) * VBUS_MV_PER_COUNT` here);
  * MOE=0 and CCER=0 in both timers, @BRK:valid=0, FAULT=0, raw in 1..4094;
  * this tool NEVER energises: it sends only `a`, `a?`, `c`, `cv`, `dump`,
    `dump8`, `breakdiag`, `sysinfo`, `ci 0/1/2` (read-only / PWM-off commands);
  * it never sends `al`, `1`, `0`, `vf=`, motor parameters or a PWM config.

Usage (interactive: the operator sets the source at each point):
  py -3 bench_inject_check.py --point=0.25:i1 --point=-0.25:i1 --point=1.0:i1
                             [--point=2.0:i2] [--tol-pct 5] [--windows 3]
                             [--source-tol-pct 1.0] [--log <path>]
                             [--preflight-only] [--auto]

  --point=<A>:<chan>  requested magnitude in A-equivalent and the channel the
                      stimulus is applied to (i1 | i2 | both). The operator
                      applies it either as that real current through the shunt
                      with an isolated low-compliance 4-wire source (variant A -
                      see the plan for why a plain CC supply is forbidden there)
                      or as a voltage across the shunt Kelvin points (variant B):
                      30 mV per 1 A there (0.03 Ohm x 1 A) - NOT 63 mV/A, since
                      63 mV/A is the pin voltage after the x2.1 gain.
  --auto              do not prompt; read each point immediately (only when the
                      source is already set and stable).

Verdict discipline (docs/ACCEPTANCE_LESSONS.md 27/28, TZ2_P0_P1_BENCH_PROTOCOL
1): a point whose SNR is below 3 is printed as "scale qualification NOT
demonstrated (reference SNR < 3)" and never turns into a scale verdict; the
source tolerance is recorded so a >1 % source cannot silently pass as a formal
scale qualification.
"""
import re
import sys
import time

# pyserial is imported lazily inside Bench(): the offline regression
# (tests/test_bench_inject_check.py) and the CI `pytest tests` run must work
# without a serial stack (same convention as tools/shunt_cal_campaign.py).

PORT = "COM4"
BAUD = 115200
LOG = r"C:\campaign_raw\pc3_foc_bench_20260930\inject_log.txt"

MA_PER_COUNT = 12.7915          # 0.805861 mV / 63 mV per A (at the ADC pin)
MV_PER_A = 63.0                 # ADC_DC_SHUNT_UV_PER_A / 1000 - the PIN voltage
SHUNT_MV_PER_A = 30.0           # 0.03 Ohm x 1 A - what the KELVIN points see
COUNTS_PER_A = 1000.0 / MA_PER_COUNT
ENVELOPE_A = 3.0                # operator: PSU 60 V / 3 A
MIN_POINT_A = 0.05
SNR_MIN = 3.0                   # ACCEPTANCE_LESSONS 27/28
DEFAULT_TOL_PCT = 5.0           # go/no-go; a formal scale verdict needs <= 1 %

# VBUS no-HV gate (offset-aware). Firmware constants, src/adc.h: VREF 3300 mV,
# MAX_CODE 4095, ADC_VBUS_DIVIDER 125, ADC_VBUS_OFFSET_MAX_RAW 40 counts,
# ADC_OFFSET_SAMPLES 256. 1 count = 100.73 mV of bus voltage.
VBUS_VREF_MV = 3300.0
VBUS_MAX_CODE = 4095.0
VBUS_DIVIDER = 125.0
VBUS_MV_PER_COUNT = VBUS_VREF_MV * VBUS_DIVIDER / VBUS_MAX_CODE
VBUS_MAX_MV = 5000              # hard precondition: DC-link off and bled
VBUS_RESID_MAX_RAW = 9          # median(raw_vbus - offset), counts (f3ebb36)
VBUS_RESID_MAX_PEAK = 200       # max(raw_vbus - offset), counts (f3ebb36)


def vbus_mv_from_raw(raw, offset):
    """Same conversion as the firmware ADC_VbusMvFromRaw (clamped at 0)."""
    delta = (float(raw) - float(offset)) * VBUS_MV_PER_COUNT
    return int(delta) if delta > 0.0 else 0


def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def int_prefix(text):
    """Leading integer of a field that may carry a trailing comment.

    The firmware answers e.g. `@ADC:CV:OK:offset_vbus=22 (raw at 0 V)`, so a
    plain isdigit() on the captured field would reject the live line.
    """
    m = re.match(r"\s*(-?\d+)", text or "")
    return int(m.group(1)) if m else None


def field(line, key):
    """Value of ':key=' inside a telemetry line (CR/LF/prompt excluded)."""
    m = re.search(r"(?:^|:)%s=([^:\r\n]*)" % re.escape(key), line)
    return m.group(1) if m else ""


def hexfield(line, key):
    m = re.search(r"(?:^|:)%s=0x([0-9A-Fa-f]+)" % re.escape(key), line)
    return int(m.group(1), 16) if m else None


def tagged_line(text, tag):
    lines = [l.strip() for l in text.replace("\r", "\n").split("\n")
             if l.strip().startswith(tag)]
    return lines[-1] if lines else ""


def tagged_field(text, tag, key):
    return field(tagged_line(text, tag), key)


class Bench:
    def __init__(self):
        import serial   # pyserial: only needed on the bench, not for host tests
        self.ser = serial.Serial(PORT, BAUD, timeout=0.2)
        time.sleep(0.6)
        self.ser.reset_input_buffer()
        self.log = []

    def read_for(self, seconds, markers=()):
        end = time.time() + seconds
        buf = ""
        while time.time() < end:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk.decode("cp1251", errors="replace")
                if any(m in buf for m in markers):
                    break
        return buf

    def send(self, line):
        self.ser.reset_input_buffer()
        self.ser.write((line + "\r\n").encode("ascii"))

    def cmd(self, line, timeout=3.0):
        """Send one CLI command and wait for the '> ' prompt."""
        self.send(line)
        resp = self.read_for(timeout, markers=("> ",))
        self.say("$ %s" % line)
        for l in resp.replace("\r", "\n").split("\n"):
            ls = l.strip()
            if ls and not ls.startswith("@FOC:t=") and ls != ">":
                self.say("    " + ls)
        return resp

    def foc_lines(self, text):
        return [l.strip() for l in text.replace("\r", "\n").split("\n")
                if l.strip().startswith("@FOC:t=") and "em_stop2=" in l]

    def say(self, text):
        print(text)
        self.log.append(text)


def parse_points(argv, fails):
    """--point=<A>:<chan> -> [(amps, chan)]; the sign is the stimulus direction."""
    pts = []
    for a in argv:
        if not a.startswith("--point="):
            continue
        spec = a.split("=", 1)[1]
        if ":" not in spec:
            fails.append("--point=%s: expected <A>:<i1|i2|both>" % spec)
            continue
        amps_s, chan = spec.split(":", 1)
        chan = chan.strip().lower()
        try:
            amps = float(amps_s)
        except ValueError:
            fails.append("--point=%s: not a number" % spec)
            continue
        if chan not in ("i1", "i2", "both"):
            fails.append("--point=%s: channel must be i1|i2|both" % spec)
            continue
        pts.append((amps, chan))
    return pts


def ci_window(b, window, n):
    """`ci <window>` n times -> per channel (avg, min, max counts) + vbus_mv."""
    avg = {"i1": [], "i2": []}
    lo = {"i1": [], "i2": []}
    hi = {"i1": [], "i2": []}
    vbus = []
    for _ in range(n):
        resp = b.cmd("ci %d" % window, 3.0)
        tag = "@CI:ZERO:" if window == 0 else "@CI:PT%d:" % window
        v = tagged_field(resp, tag, "vbus_mv")
        if v.lstrip("-").isdigit():
            vbus.append(int(v))          # firmware offset-aware mV, not counts
        for k in ("i1", "i2"):
            v = tagged_field(resp, tag, "raw_%s_avg" % k)
            if v.lstrip("-").isdigit():
                avg[k].append(int(v))
            v = tagged_field(resp, tag, "raw_%s_min" % k)
            if v.lstrip("-").isdigit():
                lo[k].append(int(v))
            v = tagged_field(resp, tag, "raw_%s_max" % k)
            if v.lstrip("-").isdigit():
                hi[k].append(int(v))
    return avg, lo, hi, vbus



def main():
    kv = dict(a.split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    flags = [a for a in sys.argv[1:] if a.startswith("--") and "=" not in a]
    log_path = kv.get("--log", LOG)
    tol_pct = float(kv.get("--tol-pct", DEFAULT_TOL_PCT))
    src_tol = float(kv.get("--source-tol-pct", 1.0))
    windows = int(kv.get("--windows", 3))
    auto = "--auto" in flags
    pre_only = "--preflight-only" in flags

    fails, warns, inconc = [], [], []
    pts = parse_points(sys.argv[1:], fails)
    for amps, _chan in pts:
        if abs(amps) > ENVELOPE_A:
            fails.append("point %.3f A exceeds the declared envelope (%.1f A, PSU "
                         "60 V / 3 A)" % (amps, ENVELOPE_A))
        elif abs(amps) < MIN_POINT_A:
            fails.append("point %.3f A is below %.2f A: the reading must sit several "
                         "LSB above zero" % (amps, MIN_POINT_A))
    if not pts and not pre_only:
        fails.append("no --point given: pass --point=<A>:<i1|i2|both> for every "
                     "injection level, or --preflight-only for the baseline only")
    if fails:
        for f in fails:
            print("REFUSED: " + f)
        return 1

    b = Bench()
    results = []
    try:
        # ---- preflight: block on anything energised -------------------------
        b.say("[inj] low-voltage injection check - the DC-link MUST be off and bled")
        b.say("[inj] operator inputs: tol=%.1f %% source_tol=%.1f %% windows=%d "
              "interactive=%s" % (tol_pct, src_tol, windows, not auto))
        b.say("[inj] stimulus scale: %.2f mV and %.1f counts per 1 A-equivalent"
              % (MV_PER_A, COUNTS_PER_A))
        d1 = b.cmd("dump", 2.0)
        d8 = b.cmd("dump8", 2.0)
        brk = b.cmd("breakdiag", 2.0)
        a0 = b.cmd("a", 2.0)
        cvd = b.cmd("cv", 3.0)              # offset-aware no-HV precondition
        b.cmd("sysinfo", 2.0)
        st = b.foc_lines(b.read_for(1.2))
        st = st[-1] if st else ""

        moe1 = (hexfield(d1, "BDTR") or 0) & 0x8000
        moe8 = (hexfield(d8, "BDTR") or 0) & 0x8000
        ccer1 = hexfield(d1, "CCER") or 0
        ccer8 = hexfield(d8, "CCER") or 0
        raw_vbus = int(tagged_field(a0, "@ADC:", "VBUS") or -1)
        raw1 = int(tagged_field(a0, "@ADC:", "I1") or -1)
        raw2 = int(tagged_field(a0, "@ADC:", "I2") or -1)
        off_vbus = None
        if "@ADC:CV:OK:" in cvd:
            v = int_prefix(tagged_field(cvd, "@ADC:CV:OK:", "offset_vbus"))
            if v is not None:
                off_vbus = v
        b.say("[pre] MOE1=%d MOE8=%d CCER1=0x%08X CCER8=0x%08X valid=%s "
              "VBUS raw=%d (cv offset=%s) raw(I1,I2)=%d,%d FAULT=%s"
              % (1 if moe1 else 0, 1 if moe8 else 0, ccer1, ccer8,
                 field(brk, "valid"), raw_vbus,
                 "none" if off_vbus is None else str(off_vbus), raw1, raw2,
                 field(st, "FAULT")))
        if moe1 or moe8:
            fails.append("MOE != 0 (PWM not off) - injection is forbidden")
        if ccer1 or ccer8:
            fails.append("CCER != 0 (outputs enabled) - injection is forbidden")
        if field(brk, "valid") == "1":
            fails.append("@BRK valid=1 (hardware-break latch) - triage first")
        if field(st, "FAULT") not in ("", "0"):
            fails.append("FAULT=%s - clear it first (HV off, documented 'f')"
                         % field(st, "FAULT"))
        # offset-aware no-HV gate: `cv` must have measured the VBUS offset on a
        # 0 V input. Raw counts are NEVER compared against a mV threshold.
        if raw_vbus < 0:
            fails.append("no @ADC reply - the bled DC-link cannot be proven")
        elif off_vbus is None:
            fails.append("no @ADC:CV:OK - the VBUS offset was not measured "
                         "(refused: PWM on / ADC busy / input not at 0 V rc=-2, "
                         "or the image has no 'cv'); a silent offset = 0 is "
                         "forbidden, so the bled DC-link cannot be proven")
        elif not fails:
            resid = []
            for _ in range(max(1, windows)):
                ans = b.cmd("a", 2.0)
                r = tagged_field(ans, "@ADC:", "VBUS")
                if r.lstrip("-").isdigit():
                    resid.append(int(r) - off_vbus)
            if not resid:
                fails.append("no @ADC reply while re-reading VBUS after 'cv'")
            else:
                med = median(resid)
                peak = max(resid)
                vb = vbus_mv_from_raw(off_vbus + med, off_vbus)
                b.say("[pre] VBUS offset=%d counts, residual median %+.1f / max %d "
                      "counts (gate <= %d / <= %d) -> %d mV (gate %d mV)"
                      % (off_vbus, med, peak, VBUS_RESID_MAX_RAW,
                         VBUS_RESID_MAX_PEAK, vb, VBUS_MAX_MV))
                if med > VBUS_RESID_MAX_RAW or peak > VBUS_RESID_MAX_PEAK \
                        or vb >= VBUS_MAX_MV:
                    fails.append("VBUS is NOT bled: offset-aware residual median "
                                 "%+.1f / max %d counts (gate %d / %d), %d mV "
                                 "(gate %d mV) - the injection procedure runs on "
                                 "a de-energised bus only"
                                 % (med, peak, VBUS_RESID_MAX_RAW,
                                    VBUS_RESID_MAX_PEAK, vb, VBUS_MAX_MV))
        for raw, name in ((raw1, "I1"), (raw2, "I2")):
            if raw >= 0 and not (1 < raw < 4094):
                fails.append("%s raw=%d outside 1..4094 (saturation/bad sample)"
                             % (name, raw))
        if fails:
            b.say("[inj] PREFLIGHT BLOCKED: " + "; ".join(fails))
            return 1


        # ---- zero baseline (stimulus OFF) -----------------------------------
        cal = b.cmd("c", 3.0)                   # offsets, inverter off
        b.say("[pre] firmware 'c' offsets: i1=%s i2=%s ires=%s"
              % (tagged_field(cal, "@ADC:CAL:", "offset_i1"),
                 tagged_field(cal, "@ADC:CAL:", "offset_i2"),
                 tagged_field(cal, "@ADC:CAL:", "offset_ires")))
        zavg, zlo, zhi, zvbus = ci_window(b, 0, windows)
        if not zavg["i1"] or not zavg["i2"]:
            fails.append("no @CI:ZERO reply in the baseline - nothing was injected")
            return 1
        if zvbus and max(zvbus) >= VBUS_MAX_MV:
            # Independent offset-aware cross-check: the firmware's own mV.
            fails.append("firmware 'ci 0' reports VBUS %d mV >= %d mV - the "
                         "DC-link is NOT bled" % (max(zvbus), VBUS_MAX_MV))
            return 1
        b.say("[pre] firmware 'ci 0' vbus_mv (offset-aware): %s"
              % ((", ".join(str(v) for v in zvbus)) if zvbus else "absent"))
        zero = {k: sum(v) / float(len(v)) for k, v in zavg.items()}
        zspan = {k: (max(zavg[k]) - min(zavg[k])) if len(zavg[k]) > 1 else 0
                 for k in ("i1", "i2")}
        b.say("[zero] stimulus OFF: I1 %.2f counts (%+.1f mA, avg-span %d) | I2 %.2f "
              "counts (%+.1f mA, avg-span %d) | per-sample p-p I1 %d I2 %d counts"
              % (zero["i1"], (zero["i1"] - 2048) * MA_PER_COUNT, zspan["i1"],
                 zero["i2"], (zero["i2"] - 2048) * MA_PER_COUNT, zspan["i2"],
                 (max(zhi["i1"]) - min(zlo["i1"])) if zhi["i1"] else -1,
                 (max(zhi["i2"]) - min(zlo["i2"])) if zhi["i2"] else -1))
        if pre_only:
            b.say("[inj] MODE: preflight-only - no injection point measured")
            return 0

        # ---- injection points ----------------------------------------------
        for amps, chan in pts:
            b.say("")
            b.say("[point] %+.3f A-equiv on %s -> shunt (Kelvin) %+.2f mV, pin %+.2f "
                  "mV, %+.1f counts"
                  % (amps, chan, amps * SHUNT_MV_PER_A, amps * MV_PER_A,
                     amps * COUNTS_PER_A))
            if not auto:
                print("    SET the source: %.3f A through the shunt (variant A) or "
                      "%+.2f mV across the Kelvin points (variant B = 30 mV/A, "
                      "negative = reversed leads); the pin then reads %+.2f mV. "
                      "DC-link still off - press Enter..."
                      % (amps, amps * SHUNT_MV_PER_A, amps * MV_PER_A))
                try:
                    input()
                except EOFError:
                    b.say("[point] no console input - use --auto when the source is set")
                    fails.append("interactive input unavailable and --auto not given")
                    break
            d1 = b.cmd("dump", 1.5)             # fail-closed re-check per point
            if (hexfield(d1, "CCER") or 0) != 0 or ((hexfield(d1, "BDTR") or 0) & 0x8000):
                fails.append("CCER/MOE became active during the session - abort")
                break
            avg, lo, hi, _vbus = ci_window(b, 0, windows)
            if not avg["i1"] or not avg["i2"]:
                fails.append("no @CI:ZERO reply at %+.3f A" % amps)
                break
            row = {"amps": amps, "chan": chan}
            for k in ("i1", "i2"):
                mean = sum(avg[k]) / float(len(avg[k]))
                d = {"counts": mean, "delta": mean - zero[k],
                     "ma": (mean - zero[k]) * MA_PER_COUNT,
                     "avg_span": (max(avg[k]) - min(avg[k])) if len(avg[k]) > 1 else 0,
                     "pp": (max(hi[k]) - min(lo[k])) if hi[k] else -1}
                exp = amps * 1000.0
                d["slope"] = d["ma"] / exp if exp else 0.0
                d["snr"] = abs(d["delta"]) / float(max(1, d["avg_span"]))
                row[k] = d
            results.append(row)
            b.say("[point] I1 %+.1f mA (%+.2f counts, avg-span %d, SNR %.1f) | I2 %+.1f "
                  "mA (%+.2f counts, avg-span %d, SNR %.1f)"
                  % (row["i1"]["ma"], row["i1"]["delta"], row["i1"]["avg_span"],
                     row["i1"]["snr"], row["i2"]["ma"], row["i2"]["delta"],
                     row["i2"]["avg_span"], row["i2"]["snr"]))
            if chan == "i1" and row["i1"]["snr"] >= SNR_MIN \
                    and abs(row["i2"]["delta"]) > 3.0:
                warns.append("I2 moved %.2f counts while injecting into I1 - check "
                             "channel crosstalk / the injection loop"
                             % row["i2"]["delta"])
            if chan == "i2" and row["i2"]["snr"] >= SNR_MIN \
                    and abs(row["i1"]["delta"]) > 3.0:
                warns.append("I1 moved %.2f counts while injecting into I2 - check "
                             "channel crosstalk / the injection loop"
                             % row["i1"]["delta"])


        # ---- remove the stimulus, verify the channels returned to zero ------
        if results:
            if not auto:
                print("    REMOVE the source (stimulus OFF), then press Enter...")
                try:
                    input()
                except EOFError:
                    pass
            z2, _, _, _ = ci_window(b, 0, windows)
            if z2["i1"] and z2["i2"]:
                back = {k: sum(v) / float(len(v)) for k, v in z2.items()}
                for k in ("i1", "i2"):
                    b.say("[post] %s back to %+.2f counts (zero was %.2f, residual "
                          "%+.2f counts)" % (k.upper(), back[k] - zero[k], zero[k],
                                             back[k] - zero[k]))
                    if abs(back[k] - zero[k]) > 2.0:
                        fails.append("post-mortem: %s did not return to zero (%.2f "
                                     "counts off) - sticky offset or residue"
                                     % (k.upper(), back[k] - zero[k]))

        # ---- verdict --------------------------------------------------------
        if results:
            b.say("")
            b.say("| point A-eq | chan | I1 mA | I1 slope | I1 SNR | I2 mA | I2 slope | I2 SNR |")
            b.say("|---|---|---|---|---|---|---|---|")
            for r in results:
                b.say("| %+.3f | %s | %+.1f | %.3f | %.1f | %+.1f | %.3f | %.1f |"
                      % (r["amps"], r["chan"], r["i1"]["ma"], r["i1"]["slope"],
                         r["i1"]["snr"], r["i2"]["ma"], r["i2"]["slope"], r["i2"]["snr"]))
            for r in results:
                for k in ("i1", "i2"):
                    if r["chan"] not in (k, "both"):
                        continue        # non-target channel = control, not a verdict
                    d = r[k]
                    if d["snr"] < SNR_MIN:
                        inconc.append("%s at %+.3f A: SNR %.1f < %.1f - scale "
                                      "qualification NOT demonstrated (reference "
                                      "SNR < 3)" % (k.upper(), r["amps"], d["snr"],
                                                    SNR_MIN))
                        continue
                    dev = abs(d["slope"] - 1.0) * 100.0
                    if dev > tol_pct:
                        fails.append("%s at %+.3f A: slope %.3f is %.1f %% off the "
                                     "63 mV/A design scale (tol %.1f %%)"
                                     % (k.upper(), r["amps"], d["slope"], dev, tol_pct))
            for k in ("i1", "i2"):
                ser = [(r["amps"], r[k]["slope"]) for r in results
                       if r[k]["snr"] >= SNR_MIN and r["chan"] in (k, "both")]
                pos = [s for a, s in ser if a > 0]
                neg = [s for a, s in ser if a < 0]
                if pos and neg:
                    mp, mn = sum(pos) / len(pos), sum(neg) / len(neg)
                    b.say("[sym] %s slope on +points %.3f, on -points %.3f -> symmetry "
                          "%.1f %%" % (k.upper(), mp, mn,
                                       100.0 * abs(abs(mp) - abs(mn)) / max(1e-9, abs(mp))))
                if len(ser) >= 2:
                    b.say("[lin] %s: %s" % (k.upper(), " | ".join(
                        "%.0f mA-eq -> slope %.3f" % (abs(a) * 1000.0, s) for a, s in ser)))
            b.say("[ref] reference uncertainty (TZ2_P0_P1_BENCH_PROTOCOL 1): source "
                  "tolerance %.1f %% ; zero avg-span I1 %d / I2 %d counts over %d "
                  "repeated windows" % (src_tol, zspan["i1"], zspan["i2"], windows))
            if src_tol > 1.0:
                warns.append("source tolerance %.1f %% > 1 %%: this run can decide "
                             "go/no-go, linearity and symmetry, but not a formal scale "
                             "verdict (TZ2 protocol 1)" % src_tol)
    finally:
        try:
            b.ser.close()
        except Exception:
            pass
        for w in warns:
            b.say("[warn] " + w)
        for n in inconc:
            b.say("[inconclusive] " + n)
        if fails:
            verdict = "FAIL -> " + "; ".join(fails)
        elif inconc:
            verdict = "INCONCLUSIVE -> " + "; ".join(inconc)
        elif results:
            verdict = ("SCALE QUALIFIED per point (|slope-1| <= %.1f %%, SNR >= %.1f, "
                       "zero restored after removal)" % (tol_pct, SNR_MIN))
        else:
            verdict = "preflight-only (baseline captured, nothing injected)"
        b.say("\nINJECTION OBSERVATION: " + verdict)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(b.log) + "\n")
        print("log -> %s" % log_path)
    return 1 if fails else (2 if inconc else 0)


if __name__ == "__main__":
    sys.exit(main())

