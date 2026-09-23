# OWON TAO3104A — verified USB protocol (live instrument, 2026-09-23)

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

Vertical scaling — taken from HEAD, verified against the live trace:
```python
scale  = num("100mV")        # 0.1 V/div
probe  = 1.0                 # "1X"
offset = -139                # raw offset code
v = (samples - offset) * (scale/25.0) * probe
```
8 vertical divisions, 25 codes per division (the OWON convention observed on
this instrument). With the test signal this gave pp = 0.528 V on a 0.8 V
full-scale window — consistent.

Time axis:
```python
dt = 1.0/num("(1MSa/s)")     # 1e-6 s
t  = np.arange(DATALEN) * dt
```

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

## End-to-end verification performed

Test signal on CH1, 1X probe, 100 mV/div, 500 us/div, 1 MSa/s:
- 1520 points read, codes 63..195, volts +0.808..+1.336 V, pp = 0.528 V
- time axis 0..1.519 ms (dt = 1.000 us)
- FFT peak 5.26 kHz; the scope screen showed ~1 kHz, which is the classic
  aliasing artifact of a 1 kHz source at 1 MSa/s with screen decimation — the
  *voltage* reconstruction is right, the apparent frequency is a sampling artifact.
  (For real waveform shape work, sample at >=10x the signal frequency.)
- CH2 (10X, 200 mV/div) read back a flat 63.6 mV with pp = 0.4 mV — matches a
  quiescent input at that scale.
- CH3/CH4 DISPLAY=OFF -> all-zero codes.
- Instrument stayed alive and responsive after every read.

## Practical notes

- `RUNSTATUS` was `TRIG` while triggering on the live signal.
- Reading HEAD+4x CH takes <1 s total. No need to stop the acquisition first
  for screen data, but `DATATYPE` is `"SCREEN"` (1520 displayed points), not
  the deep memory — `SAMPLE.DEPMEM` says 10K available.
- After a command the scope answers back with the prompt; use it as the terminator,
  not a fixed sleep. HEAD/waveform replies carry the 4-byte length prefix and no prompt.
