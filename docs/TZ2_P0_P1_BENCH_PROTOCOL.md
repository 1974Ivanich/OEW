# TZ-02 P0/P1 — representative-row physical bench protocol

**Status:** ready to execute on target; no physical PASS is claimed by this document.
**Purpose:** prove independent U/V/W reference provenance and timing alignment before expanding to all 12 map rows.
**Firmware baseline:** `main` `c494338` (includes the admission-identity fix `2b2efdd`). The flashed image builds as a map-capable commissioning configuration, so its sha256 differs from the production image — record the sha256 of the image actually flashed in the session report.

## 1. Scope

Run only 1–2 representative `(sector, window)` rows first. Recommended minimum:

- one interior point in a lower modulation window row;
- one point near a window/sector edge that is still unambiguously inside the certified region.

Do not use a synthetic dataset or reuse `ref_*` derived from PA0/PA1 ADC values as the independent reference.

## 2. Required independent measurement chain

The reference must be electrically independent of firmware current reconstruction. Acceptable examples are a calibrated isolated current probe/scope channel or an independently instrumented phase-current/shunt path that is not connected to PA0/PA1 reconstruction.

Record in `independent_reference.json`:

```text
qualified=true
measurement_principle=<...>
instrument_id=<...>
calibration_id=<...>
channels.U=<scope channel>
channels.V=<scope channel>
channels.W=<scope channel>
electrical_independence=true
bandwidth_hz=<...>
scale_u_ma_per_unit=<...>
scale_v_ma_per_unit=<...>
scale_w_ma_per_unit=<...>
```

A screenshot/photo alone is not sufficient provenance; the raw waveform export and instrument configuration must be retained.

## 3. Synchronization proof

For every accepted sample retain:

- PWM `CCR1/CCR2/CCR3`, ARR and sector/window;
- firmware ADC raw `idc1/idc2` and timestamp/CYCCNT;
- independent U/V/W reference waveform/sample and its timestamp;
- the trigger relationship used to align the two measurements;
- ADC settled/aperture and `margin_ticks` evidence.

Acceptance requires a deterministic alignment method. A free-running scope trace with manually chosen cursor values is **BLOCKED** unless the timing uncertainty is quantified and within the pre-approved sampling-time limit.

## 4. P0 checklist

Before energise/capture:

- [ ] flashed firmware SHA256 recorded;
- [ ] source commit recorded;
- [ ] board revision recorded;
- [ ] PWM frequency/ARR recorded;
- [ ] ADC trigger/config signature recorded;
- [ ] current calibration signature recorded;
- [ ] independent instrument ID and calibration ID recorded;
- [ ] U/V/W probe mapping recorded;
- [ ] electrical independence explicitly reviewed;
- [ ] trigger/timestamp alignment method recorded;
- [ ] VBUS operating condition and current limit recorded;
- [ ] safety/protection path verified before capture.

Failure of any provenance item => `BLOCKED`.

## 5. P1 capture

For each representative row acquire enough repeated samples to establish:

1. stable ADC readings;
2. stable independent U/V/W currents;
3. repeatable timestamp alignment;
4. non-zero excitation in both firmware observation dimensions;
5. no ADC saturation;
6. KCL quality of the **independent** reference;
7. agreement between commanded PWM vector and captured CCR/sector/window.

At least one repeated sample must be independently re-triggerable. The first run is an instrumentation qualification, not yet a map qualification.

## 6. Mandatory raw artifacts

```text
p0_p1_campaign/
  manifest.json
  independent_reference.json
  firmware_manifest.json
  samples.jsonl
  scope_raw_U.*
  scope_raw_V.*
  scope_raw_W.*
  timing_alignment.*
  operator_notes.md
```

Do not replace raw scope data with only processed `ref_*_ma` values.

## 7. P0/P1 verdict

`PASS` means only that the measurement chain is proven suitable for the next phase. It does **not** mean the current map is physically qualified.

`BLOCKED` if reference independence or synchronization cannot be demonstrated.

`FAIL` if the supposedly independent chain contradicts the expected phase currents, timing, channel mapping, or safety assumptions.

After P0/P1 PASS, expand to all 12 rows and execute P2 identification + P3 `H ↔ M` comparison.
