# UART raw MapCapture export

The firmware already provides the foreground command:

```text
mapcap drain
```

It drains every available `MapCaptureRecord` as `@MC:REC:...` lines. This is
intentionally a raw transport, not a qualified characterization dataset.

## Export

Capture the UART session without editing it, then run:

```bash
python tools/mapcap_uart_export.py uart.log campaign_raw/raw_records.jsonl
```

The exporter is fail-closed. A record is rejected if any field currently
emitted by firmware is missing or malformed. Non-record UART lines are ignored.

## Important separation

`raw_records.jsonl` is immutable source evidence. It must **not** be passed
directly to `map_bench_dataset.py`: the canonical `samples.jsonl` additionally
requires bench evidence (`ref_u/v/w_ma`, `margin_ticks`, `blanking_ticks`,
`adc_settled`, `scope_qualified`, `timestamp_cycles`) that is not synthesized
by this exporter.

The current firmware `mapcap drain` stream contains raw ADC values, engineering
ADC values, sequence/capture identifiers, TIM1/TIM8 CCR snapshots, ARR, trigger
revision, frame status and capture fault status. Hardware-wide configuration
such as dead-time/sample-time/signatures is collected separately in the existing
`dumpa`/`pdump` diagnostics and belongs in `manifest.json`.

This separation prevents a UART log parser from silently inventing qualification
evidence.
