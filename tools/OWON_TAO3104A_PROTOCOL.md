# OWON TAO3104A — verified USB protocol (live instrument, 2026-09-23)

Revision 2026-10-05: the **vertical model, the time axis and the measurement
commands** were re-verified against the live instrument and the 2026-09-26
results were partly refuted — see §"Vertical (revised)" and the 2026-10-05
addendum in §"End-to-end verification". Superseded text is kept and marked.

Instrument: `OWON,TAO3104A,2306027,V3.0.0` (MODEL 510401102)
Driver on this PC: **libusb-win32** (class 04: libusb devices), NOT NI-VISA.
=> `pyvisa` cannot open it (`VI_ERROR_LIBRARY_NFOUND`). Use raw bulk via pyusb.

## Transport (verified working)

USB 2.0, VID 0x5345, PID 0x1234, SN 2306027.
1 config / 1 interface ("Bulk Data Interface"):
  - class 0x05, subclass 0x06, protocol 0x50  (USBTMC-ish, but see below)
  - EP 0x03 OUT, bulk, 512 B
  - EP 0x81 IN , bulk, 512 B
Self-powered, 500 mA.

IMPORTANT: it does NOT behave like a USBTMC device (no 12-byte DEV_DEP_MSG header,
no MsgID/EOM). It is **plain text over raw bulk endpoints**:
  - write command + "\r\n" to EP 0x03
  - read from EP 0x81 until the device prompt `"->\n"` arrives
  - every response ends with the prompt `"->\n"` (0x2D 0x3E 0x0A)
No set_configuration needed on first open, but `d.set_configuration()` + explicit
`claim_interface` is the reliable sequence after a USB reset.

## Verified commands

| Command | Result |
|---|---|
| `*IDN?` | `OWON,TAO3104A,2306027,V3.0.0->\n` |
| `*STB?` | **no reply** (unlike the 2CH SCPI doc) |
| `*ESR?` | **no reply** |
| `*OPC?` | **no reply** |
| `:ACQuire:MODE?` | `SAMPle->\n` |
| `:ACQuire:AVERage:NUM?` | `4->\n` |
| `:TRIGger:TYPE?` | `SINGle->\n` |
| `:TRIGger:SINGle:MODE?` | `EDGE->\n` |
| `:TRIGger:SINGle:EDGE:SOURce?` | `CH1->\n` |
| `:HORIzontal:SCALe?` | `20us->\n` |
| `:DATA:WAVE:SCREen:HEAD?` | **works** — 1247 B JSON + 4-byte length prefix |
| `:DATA:WAVE:SCREen:CH1..4?` | **works** — 3040 B waveform + 4-byte length prefix |

### Commands that exist in OWON's 2CH SCPI PDF but NOT on this scope

These all return **nothing** (empty read, scope stays alive):
`:ACQuire:DEPMEM?`, `:CHANnel:CH1:SCALe?`, `:CHANnel:CH1:COUPling?`,
`:MEASUrement:ALL?`, `*STB?`, `*ESR?`, `*OPC?`.
=> the 4CH firmware implements only a subset. Every command must be probed, not assumed.

### SCPI command form is case-sensitive and uses FULL keyword (verified)

OWON PC software (`com.owon.uppersoft.hdoscilloscope_1.6.33.jar`, V3.0.0) and
the Android companion app `OSC3000_1.3.8.apk` both send `:DATA:WAVE:SCREEN:HEAD?`
and `:DATA:WAVE:SCREEN:CH1..4?` with the **full keyword** `SCREEN`, never
`SCREen`. The scope firmware is NOT case-folding / prefix-truncating; using
`:SCREen` returns an empty reply because the command simply doesn't exist.
Confirmed end-to-end:
- `:DATA:WAVE:SCREen:HEAD?` -> 0 bytes (silently dropped)
- `:DATA:WAVE:SCREEN:HEAD?` -> 1252-byte JSON reply

All other SCPI keywords are likewise case-sensitive and require full spelling:
`ACQUire`, `TRIGger`, `CHANnel`, `HORIzontal`, `DATA`, `WAVE`, `SCREEN`,
`DEPMEM`, `SINGle`, `EDGe`, `LEVel`, `SOURce`, `MODE`, `SWEEp`, `HEAD`, `BMP`,
`All`, `TYPE`, `COUPling`, `HoldOff`, `LLevel`, `ULevel`, `SIGN`, `LineNum`,
`Sweep`, `Sync`, `System`, `Time`, `polarity`. The PC JAR only uses
`ACQUire:Mode` (mixed case), so case rules are not strict.

### SET commands (verified IGNORED on V3.0.0)

Every SET-form of an SCPI command I tried returned nothing and had NO effect,
even after a power cycle. Verified single-shot with one SET + one readback:

| SET command | Readback after |
|---|---|
| `TRIGger:SINGle:EDGE:SOURce CH2` | `SOURce?` still CH1 (unchanged) |
| `TRIGger:SINGle:EDGE:SOURce EXT` | `SOURce?` still CH1 |
| `TRIGger:SINGle:EDGE:SOURce EXTERNAL` | `SOURce?` still CH1 |
| `TRIGger:SINGle:EDGE:SOURce AUX` | `SOURce?` still CH1 |
| `TRIGger:SINGle:EDGE:SOURce LINE` | `SOURce?` still CH1 |
| `TRIGger:SINGle:SWEEp SINGle/AUTO` | no effect |
| `AUTOset ON` | no effect |
| `RUN` / `STOP` | no effect |

=> **You cannot change trigger source, sweep mode, or trigger coupling from USB
on this firmware.** Only the front panel can configure the scope. The query
forms of `:ACQuire:MODE?`, `:TRIGger:SINGle:MODE?`, `:TRIGger:TYPE?`,
`:TRIGger:STATUS?`, `:HORIzontal:SCALe?`, `:CHANNEL[].SCALE/OFFSET/COUPLING/PROBE`
DO work and report the current front-panel state — useful as a sanity check,
not as a control path.

### Side effect to watch out for (caused one frozen scope mid-session)

Running several SET commands in a row sometimes froze the scope so that even
`*IDN?` and `:DATA:WAVE:SCREen:HEAD?` went silent until power-cycle. I could
not reproduce it deterministically, but the trigger-table shows several
commands that come back empty after that point. The user-controlled fallback is
to power-cycle the scope and stay on query-only commands from then on.
=> **the campaign tool only ever issues query commands.** No SETs, by design.

### Commands used by the tao3104a_mapcapture package (all BROKEN)

`SCPI_SEQUENCE.md` sends these and expects `pyvisa`:

| Package command | Reality |
|---|---|
| `:WAV:SOUR CH1` | **does not exist** → empty reply |
| `:WAV:FORM BYTE` | **does not exist** → empty reply |
| `:WAV:PRE?` | **does not exist** → empty reply |
| `:WAV:DATA?` | **does not exists** → empty reply |
| `:SING` | **does not exist** → empty reply |
| `:SYST:ERR?` | **does not exist** → empty reply |
| `*IDN?` / `*STB?` | `*IDN?` works, `*STB?` returns nothing |

Additionally the package cannot open the device at all: it uses `pyvisa`, but the
installed driver is libusb-win32, so `pyvisa.ResourceManager()` fails with
`VI_ERROR_LIBRARY_NFOUND (-1073807202)` before any command is sent.

## HEAD response (verified JSON, `:DATA:WAVE:SCREen:HEAD?`)

Framing: 4-byte little-endian length prefix, then that many bytes of JSON,
then NO prompt for HEAD (the prompt is on query answers only).
Captured head (test signal 1 kHz on CH1):
```json
{"TIMEBASE":{"SCALE":"500us","HOFFSET":0},
 "SAMPLE":{"FULLSCREEN":1520,"SLOWMOVE":-1,"DATALEN":1520,"SAMPLERATE":"(1MSa/s)","TYPE":"SAMPle","DEPMEM":"10K"},
 "CHANNEL":[{"NAME":"CH1","DISPLAY":"ON","Current_Rate":10000.0,"Current_Ratio":0.15625,
             "Measure_Current_Switch":"OFF","COUPLING":"DC","PROBE":"1X","SCALE":"100mV",
             "OFFSET":-139,"FREQUENCE":1000.12802,"INVERSE":"OFF"}, ... CH2..CH4 ...],
 "DATATYPE":"SCREEN","RUNSTATUS":"TRIG",
 "IDN":"OWON,TAO3104A,2306027,V3.0.0","MODEL":"510401102",
 "Trig":{"Mode":"SINGle","Type":"EDGE","Items":{"Channel":"CH1","Level":"47.0mV","Edge":"FALL","Coupling":"DC","HoldOff":"100ns"},"Sweep":"AUTO"}}
```

Fields that matter for capture:
- `SAMPLE.DATALEN` = 1520 (points per channel)
- `SAMPLE.FULLSCREEN` = 1520
- `SAMPLE.SAMPLERATE` = "(1MSa/s)" — parse the number in parens
- `TIMEBASE.SCALE` = "500us", `TIMEBASE.HOFFSET` = offset in samples
- per channel: `SCALE` ("100mV"), `OFFSET` (raw ADC offset, e.g. -139),
  `PROBE` ("1X"), `DISPLAY` ("ON"/"OFF"), `INVERSE`

## Waveform response (verified with a real signal on CH1)

`:DATA:WAVE:SCREen:CH1?` -> framing:
4-byte LE length prefix = 3040, then 3040 bytes of payload, NO prompt.

Payload layout (this is the important part — it is NOT what OWON's own 2CH
SCPI PDF says):

- 3040 bytes = 1520 sample slots * 2 bytes
- **sample = the ODD byte (1st of each pair)**; the EVEN byte (0th) is **always 0**
  Verified on a live signal: even bytes all 0x00, odd bytes vary 63..195.
  So it is an 8-bit ADC code stored in a 16-bit little-endian container with the
  low byte zeroed, i.e. `code = word >> 8`.
- count: DATALEN = 1520 samples = 3040 bytes, exactly matching the length prefix.
- The 4CH TAO3104A is 14-bit class hardware, but the SCREEN data path delivers
  8-bit codes (0..255). Deep memory is a different path.

So:
```python
ln      = int.from_bytes(resp[:4], 'little')      # 3040
raw     = resp[4:4+ln]                            # 3040 bytes
samples = np.frombuffer(raw, '<u2') >> 8          # 1520 values, 0..255
```

## Vertical — revised 2026-10-05 (the instrument is the reference)

```python
scale = unit(meta['SCALE'])      # V/div, e.g. "500mV"
probe = 1.0 if meta['PROBE'].upper() == '1X' else 10.0
k     = scale / 25.0 * probe     # ~25 codes per division
z     = code_low - meas['min']/k # zero-volt code, calibrated PER FRAME
v     = (codes - z) * k
```

Only two numbers are needed, and the instrument supplies both: the volts-per-code
that its own PKPK confirms (a 0…5.08 V square at 1 V/div spans 128 codes = 5.12 V,
i.e. 25 codes/div) and a zero code anchored on its own MIN per frame.

`CODE_0V = 305.5` (2026-09-26, ONE configuration: 500 mV/div, 1X, OFFSET=-100,
code 222 = +0.330 V) is **not** global — it does not even fit in the 8-bit code
range 0..255. On the live 2026-10-05 frame (1 V/div, 1X, OFFSET=-126, 0…+5.08 V
square, instrument MIN=-40.00 mV / MAX=+5.08 V / PKPK=5.120 V) it places 0 V at
code `OFFSET + 305.5 = 179.5` while the frame's own low plateau sits at code 64:
the frame then decodes to −4.66…+0.62 V instead of −0.04…+5.08 V — a **+4.66 V
(117 code) error**, purely from the constant. The tool keeps `CODE_0V` only as a
flagged fallback (`zero_code_source='legacy_constant'`) for frames whose
measurements cannot be read; those frames fail selfcheck, and `--strict` refuses
to write them silently.

The old model (`v = (codes − offset − CODE_0V)·scale/25`, "off by +6.11 V without
the constant") is superseded: the constant itself was the local fit.

## Time axis — revised 2026-10-05

A SCREEN frame is **peak-detect pixel pairs, not a uniform time series**:

- 760 pixels × 2 samples = 1520 samples; pixel p owns samples `[2p, 2p+1]` =
  `[max, min]` of that pixel's dwell. Live check: 745 of 760 pixels have
  `min == max` (only the 15 transition pixels differ), so it cannot be treated as
  1520 uniformly spaced samples.
- `px_dt = TIMEBASE.SCALE / 50` (50 px per division) = **10 µs at 500 µs/div**.
- `t[i] = (i // 2) * px_dt` — both samples of a pixel carry the pixel's time.
- Full frame span = 760 × 10 µs = **7.6 ms** (10 divisions).

Anchors from the same live session (1 kHz, 0…5 V square on CH1):

| Evidence | Value |
|---|---|
| `:MEASUrement:CH1:FREQuency?` | `F : 1.000KHz` |
| `:MEASUrement:CH1:PERiod?` | `T : 1.000ms` |
| SCREEN repetition | 1 period = 200 samples = 100 pixels ⇒ 1 ms / 100 px = 10 µs/px |
| DEPMEM HEAD, same session | `FULLSCREEN = 7600` samples @ 1 MSa/s = 7.6 ms ✔ |
| DEPMEM frame | half-periods 499–501 samples @ 1 µs ⇒ 1.000 ms ⇒ 1.000 kHz ✔ |

The superseded `dt = 10*TIMEBASE.SCALE/DATALEN = 3.289 µs` (alleged "5 ms"
window) is **refuted**: on it the same 1 kHz period measures 658 µs, and the tool
reported ~1.5–1.6 kHz. That 1.52× factor is exactly the artifact, not the signal.
`SAMPLE.SAMPLERATE` in a SCREEN HEAD describes the 10 kSa acquisition memory, so
on SCREEN never use `1/sr` (it would give a 1.5 ms window).

## Unit parser (needed — the scope returns "100mV", "500us", "(1MSa/s)")

MUST include the MEGA prefix; the scope literally emits `1MSa/s` where a naive
lowercase-only parser turns 1 M into 1 milli and the time axis comes out a
billion times too slow:
```python
def num(s):
    m = re.match(r'^\s*([0-9.]+)\s*([kKmMuUnNgG]?)', s.strip().lstrip('('))
    v = float(m.group(1)); u = m.group(2)
    f = {'k':1e3,'K':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'M':1e6,'G':1e9}
    return v * f.get(u, 1.0)
```

Revision 2026-10-05 — the shipped parser is `unit()` in `tools/tao3104a_cap.py`
and differs from the snippet above in three ways the instrument forced:

- **Signs.** `:MEASUrement:CH1:MIN?` answers `-40.00mV`; a parser that drops `-`
  turns the low level into +40 mV. Verified cases: `'100mV'→0.1`, `'1.00V'→1.0`,
  `'500us'→5e-4`, `'-40.00mV'→-0.04`, `'(1MSa/s)'→1e6`, `'(50kSa/s)'→5e4`,
  `'1.000KHz'→1e3`, bare `'50.29267'` (HEAD `FREQUENCE`)→50.29267.
- **KHz/MHz.** Frequency replies are suffixed `KHz` — with the lowercase-only
  table `1.000KHz` would come out as 1.000 (Hz), i.e. 1000× off. The shipped
  table is `{k,K:1e3, m:1e-3, u:1e-6, n:1e-9, M:1e6, G:1e9}`, so `K` is kilo and
  `M` is mega in both branches.
- **Sa/s first.** The SAMPLERATE label `(1MSa/s)` is parsed by its own regex
  before the generic one, so `1 M` never becomes 1 milli.

## Instrument measurements — `:MEASUrement:CH1:*?` (verified 2026-10-05, V3.0.0)

Read-only, prompt-terminated, signed values, `KHz` suffix (raw replies):

| Query | Reply |
|---|---|
| `:MEASUrement:CH1:PKPK?` | `... -> Vpp : 5.120V->|` |
| `:MEASUrement:CH1:MAX?` | `... -> Ma : 5.080V->|` |
| `:MEASUrement:CH1:MIN?` | `... -> Mi : -40.00mV->|` |
| `:MEASUrement:CH1:PERiod?` | `... -> T : 1.000ms->|` |
| `:MEASUrement:CH1:FREQuency?` | `... -> F : 1.000KHz->|` |
| `:MEASUrement:CH1:VPP?` | **no reply** |
| `:MEASUrement:CH1:MEAN?` | **no reply** |
| `:MEASUrement:CH1:RMS?` | **no reply** |
| `:TRIGger:SINGle:EDGe:LEVel?` | `2.54V->|` |
| `:HORIzontal:SCALe?` | `500us->|` |

The value is the field after the LAST `:` of the segment that follows the first
`->` (echo/answer separator), which is what `measure_reply_value()` extracts.

These readings are the tool's anchor (`--measure`, `Client.measure()`): decode
the frame, then compare tool-vs-instrument MIN/MAX/PKPK and refuse to pass a
frame that disagrees by more than `--tol-codes` (default 3). Measurements
describe the **signal, not the buffer**, so they are valid for SCREEN and DEPMEM
alike and are the only trustworthy amplitude/frequency source on this firmware.

## Deep memory — `:DATA:WAVE:DEPMEM:*` (verified working 2026-10-05)

`:DATA:WAVE:DEPMEM:HEAD?` → JSON with `DATATYPE: "WAVEDEPMEM"` (the command name
says DEPMEM, the field does not — accept both), `SAMPLE.DATALEN = 10000`,
`SAMPLE.SAMPLERATE = "(1MSa/s)"`, plus `SAMPLE.FULLSCREEN = 7600` and
`SAMPLE.SCREENOFFSET = 1200` (the screen window inside the memory).

`:DATA:WAVE:DEPMEM:CH1?` → 19998 bytes = **9999 samples** (one less than DATALEN)
in the same 16-bit container, `code = word >> 8`. `dt = 1/SAMPLERATE = 1 µs`
exactly, so this is the uniform frame to use for timing (a 1.000 ms period is
1000 samples). `--depmem` selects it; channels with `DISPLAY=OFF` were not
verified on this path.

## End-to-end verification performed

First run (2026-09, CH1 1X probe, 100 mV/div, 500 us/div, before the
calibration): the codes, the peak-to-peak and the liveness observations stay
valid, the **absolute volts and the time axis are superseded** — and so is the
"calibrated model" column itself, both in its absolute volts (its `CODE_0V` was
a local fit) and in its 3.289 µs dt — see the 2026-10-05 addendum below.

| quantity | old model | calibrated model |
|---|---|---|
| 1520 points, codes 63..195 | +0.808 .. +1.336 V | -0.414 .. +0.114 V |
| pp | 0.528 V | 0.528 V (pp does not depend on CODE_0V) |
| time axis (1520 pts) | 0 .. 1.519 ms (dt = 1.000 us) | 0 .. 4.996 ms (dt = 3.289 us) |

The old volts are exactly +1.222 V high (CODE_0V × scale/25 = 305.5 × 0.004).

- CH2 (10X, 200 mV/div) read back flat, pp = 0.4 mV — a quiescent input.
- CH3/CH4 DISPLAY=OFF -> all-zero codes.
- FFT peak was 5.26 kHz on the old (too fast) axis; on the calibrated axis the
  same bin lands at ~1.6 kHz, i.e. much closer to the ~1 kHz the screen showed.
  Consistency check only — the residual is screen decimation.

Re-verified after the fix (2026-09-27, same instrument, 500 mV/div, OFFSET=-100,
1X, constant +0.330 V input):
- code 222 -> **+0.3300 V** calibrated, vs +6.4400 V on the old model
  (+6.1100 V error). The scope's own 330 mV trigger marker sits on that trace.
- CH2 (500 mV/div, OFFSET=-95): +0.2891 V, pp 20 mV.
- SCREEN frame: 1520 points, dt = 3.289473e-6 s (window 4.9967 ms), while
  `SAMPLE.SAMPLERATE` still reports `(1MSa/s)` — 3.3x away from 1/dt.
- Instrument stayed alive and responsive after every read.

### Addendum 2026-10-05 (SN 2306027, V3.0.0) — what the instrument itself says

Signal: 50 %-duty square, logic 0 V / +5 V, on CH1 (the probe/div settings
differ between the two captures below; both are the same 1 V/div range at the
probe tip, hence identical codes).

| Quantity | instrument | old/superseded model | shipped model |
|---|---|---|---|
| frequency | `F : 1.000KHz`, `T : 1.000ms`, DEPMEM half-periods 499–501 µs | 1.5–1.6 kHz (from SCREEN + 3.289 µs) | 1.000 kHz (DEPMEM 1 µs / instrument) |
| SCREEN frame span | 7.6 ms (DEPMEM `FULLSCREEN`=7600 @ 1 MSa/s) | 5.0 ms | 7.6 ms = 760 px × 10 µs |
| Vpp | `PKPK` = 5.120 V | 5.28 V (buffer min..max incl. spikes) | 5.12–5.20 V (plateaus 64/194 × 40 mV) |
| levels | `MAX` +5.080 V, `MIN` −0.040 V | −4.66…+0.62 V (constant +4.66 V error) | −0.04…+5.08 V, `zero_code` from the frame's own MIN |
| SCREEN frame | 1520 samples = 760 px × [max, min], 745/760 px with min==max | 1520 uniform samples | pair-packed, `t=(i//2)·px_dt` |

Artefacts of that session: `build/scope_snapshot_20261005_181320/` (SCREEN +
`scope_measure_scope.txt`) and `build/scope_depmem_20261005_200514/`
(`truth.txt` + clean 9999-sample DEPMEM CSV); the write-up that started the fix
is in `build/scope_snapshot_20261005_181320/CORRECTION_amplitude_frequency.md`.

Tool-side contract after the fix: the CSV columns are unchanged
(`sample_index, time_s, chN_v`), `time_s` follows the real geometry, and every
capture writes `<out>.head.json` + `<out>.measure.json` (frame layout, per-channel
`zero_code` + source, scale, and the tool-vs-instrument selfcheck verdict);
`--strict` makes a non-PASS selfcheck a non-zero exit with no silent success.

## Practical notes

- `RUNSTATUS` was `TRIG` while triggering on the live signal.
- Reading HEAD+4x CH takes <1 s total. No need to stop the acquisition first
  for screen data, but `DATATYPE` is `"SCREEN"` (1520 displayed points), not
  the deep memory — `SAMPLE.DEPMEM` says 10K available.
- After a command the scope answers back with the prompt; use it as the terminator,
  not a fixed sleep. HEAD/waveform replies carry the 4-byte length prefix and no prompt.
