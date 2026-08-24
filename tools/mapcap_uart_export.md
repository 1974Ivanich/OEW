# UART raw MapCapture export

The firmware already provides the foreground command:

```text
mapcap drain
```

It drains every available `MapCaptureRecord` as `@MC:REC:...` lines and closes
with a summary `@MC:DRAIN:records=N`. This is intentionally a raw transport,
not a qualified characterization dataset.

## Export

Capture the UART session without editing it, then run:

```bash
python tools/mapcap_uart_export.py uart.log campaign_raw/raw_records.jsonl
```

The exporter is fail-closed. A record is rejected if any field currently
emitted by firmware is missing or malformed. Non-record UART lines are ignored.

The export is accepted only for a complete session: the trailing
`@MC:DRAIN:records=N` summary must be present and match the number of parsed
`@MC:REC` records. A missing or mismatching summary means the UART log lost
lines — the export is rejected so the operator can re-drain while the bench is
still set up.

## Important separation

`raw_records.jsonl` is immutable source evidence. It must **not** be passed
directly to `map_bench_dataset.py`: the canonical `samples.jsonl` additionally
requires bench evidence (`ref_u/v/w_ma`, `margin_ticks`, `blanking_ticks`,
`adc_settled`, `scope_qualified`, `timestamp_cycles`) that is not synthesized
by this exporter.

The current firmware `mapcap drain` stream contains raw ADC values,
engineering shunt/Vbus values (`i1`, `i2`, `vbus`; Ires is kept as raw
`raw_ct`), sequence/capture identifiers, TIM1/TIM8 CCR snapshots, ARR, trigger
revision, frame status and capture fault status. Hardware-wide configuration
such as dead-time/sample-time/signatures is collected separately in the existing
`dumpa`/`pdump` diagnostics and belongs in `manifest.json`.

This separation prevents a UART log parser from silently inventing qualification
evidence.
