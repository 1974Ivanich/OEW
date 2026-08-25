# Automated real-board OEW characterization

## Purpose

The production firmware remains fail-closed. A real board profile is only a
reviewed allow-list for **safe PWM/ADC capture**; it never contains guessed
`m00/m01/m10/m11` coefficients. Reconstruction coefficients are created only
from the external phase-current reference by the existing host solver.

The implemented host flow is:

```text
STM32 MapCapture
      |
      | @MC:REC UART evidence
      v
map_auto_characterize.py
      |                    ^
      |                    |
      |              external probe CSV
      v
manifest.json + samples.jsonl
      |
      v
map_bench_dataset.py
      |
      v
dataset.txt
      |
      v
map_artifact_pipeline_cli
      |
      +--> oew_map_v2.bin
      +--> oew_map_v2.json
```

## External reference

The reference file must contain:

```text
seq,phase_u_ma,phase_v_ma,phase_w_ma,timestamp_cycles
```

`seq` is the firmware `AdcFrame.sequence`. The tool rejects missing or duplicate
reference samples, nonzero firmware capture status, ARR/trigger mismatches,
TIM1/TIM8 CCR mismatches, and KCL errors.

The phase currents are never reconstructed from `I1`, `I2` or `IN`. `phase_u/v/w`
are independent external measurements. Two phase probes are sufficient because
`phase_u + phase_v + phase_w = 0` is checked explicitly.

## Real profile requirements

`src/map_real_board_profile.*` defines the separated board-capture profile.
It contains:

- live hardware/ADC/PWM identity;
- twelve `(sector, window)` capture rows;
- fixed CCR vectors;
- current/VBUS/timeout limits;
- scope-qualified timing status and minimum margin;
- an explicit flag that external reference data is required.

It deliberately contains **no reconstruction coefficients**.

The profile must be complete before a physical campaign. In particular,
`trigger_offset_ticks` must be nonzero and backed by oscilloscope evidence; the
firmware currently keeps it at zero until that qualification is complete.

## Recommended first campaign

1. Run ADC offset/noise calibration with PWM disabled.
2. Verify current-sense polarity for both R26 channels.
3. Scope-qualify TRGO → ADC aperture and record `trigger_offset_ticks` and
   minimum settled margin.
4. Create the immutable 12-row board profile from the measured live identity.
5. Run the bounded MapCapture rows with the bridge interlocks active.
6. Export `@MC:REC` records with `mapcap_uart_export.py`.
7. Acquire two external phase-current channels and save the reference CSV.
8. Run `map_auto_characterize.py`; it rejects incomplete alignment/evidence.
9. Review the generated `oew_map_v2.json` provenance before deployment.
10. Only then load the canonical artifact through a separately reviewed
    commissioning/deployment path; never enter `m00..m11` manually.

## Example

```bash
python tools/map_auto_characterize.py build \
  --uart campaign_raw/uart.log \
  --reference campaign_raw/probe.csv \
  --manifest campaign_raw/manifest.json \
  --out campaign_real \
  --convert campaign_real/dataset.txt \
  --pipeline ./tools/map_artifact_pipeline_cli
```

A successful run produces `campaign_real/oew_map_v2.bin` under `artifact/` and
fails closed on any missing row/evidence/provenance condition.
