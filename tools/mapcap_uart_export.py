#!/usr/bin/env python3
"""Convert raw ``@MC:REC`` UART lines into an immutable capture dataset.

The firmware already exposes ``mapcap drain``.  This tool deliberately keeps
that stream separate from the qualified ``samples.jsonl`` format: fields that
come from scope/bench qualification are NOT fabricated here.

Usage:
    python tools/mapcap_uart_export.py uart.log campaign_raw/raw_records.jsonl

The output is JSONL and contains every field currently emitted by the firmware
mapcap drain command, plus the source line number.  Missing characterization
evidence must be added by the bench-side enrichment step before running
``map_bench_dataset.py``.

The export is fail-closed end to end: every ``@MC:REC`` line must be complete,
and the trailing ``@MC:DRAIN:records=N`` summary must be present and match the
number of parsed records — otherwise the UART log is rejected as incomplete
(a dropped line would otherwise silently shrink the raw evidence).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PREFIX = "@MC:REC:"
DRAIN_RE = re.compile(r"@MC:DRAIN:records=(\d+)")
FIELD_RE = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>[^:]*)")
INT_KEYS = {
    "cap", "seq", "raw_i1", "raw_i2", "raw_ct", "raw_vbus",
    "i1", "i2", "vbus", "arr", "trig", "status", "fault",
}
CCR_KEYS = {"ccr1", "ccr8"}


def _parse_int(text: str, key: str) -> int:
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError(f"{key}: expected integer, got {text!r}") from exc


def parse_record_line(line: str, line_no: int = 0) -> dict:
    line = line.strip()
    if PREFIX not in line:
        raise ValueError(f"line {line_no}: not an @MC:REC record")
    payload = line.split(PREFIX, 1)[1]
    fields: dict[str, object] = {}
    for match in FIELD_RE.finditer(payload):
        key = match.group("key")
        value = match.group("value")
        if key in INT_KEYS:
            fields[key] = _parse_int(value, key)
        elif key in CCR_KEYS:
            parts = value.split(",")
            if len(parts) != 3:
                raise ValueError(f"line {line_no}: {key}: expected 3 CCR values")
            fields[key] = [_parse_int(part, key) for part in parts]
        else:
            fields[key] = value

    required = {
        "cap", "seq", "raw_i1", "raw_i2", "raw_ct", "raw_vbus",
        "i1", "i2", "vbus", "ccr1", "ccr8", "arr", "trig",
        "status", "fault",
    }
    missing = sorted(required - fields.keys())
    if missing:
        raise ValueError(f"line {line_no}: missing fields: {', '.join(missing)}")
    fields["source_line"] = line_no
    return fields


def export_uart_log(input_path: str | Path, output_path: str | Path) -> int:
    """Extract all valid MC records. Returns the number of records."""
    source = Path(input_path)
    destination = Path(output_path)
    records: list[dict] = []
    drain_seen = False
    drain_total = 0
    with source.open(encoding="utf-8", errors="strict") as stream:
        for line_no, line in enumerate(stream, 1):
            if "@MC:DRAIN:" in line:
                match = DRAIN_RE.search(line)
                if not match:
                    raise ValueError(
                        f"line {line_no}: malformed @MC:DRAIN summary")
                drain_total += int(match.group(1))
                drain_seen = True
                continue
            if PREFIX not in line:
                continue
            records.append(parse_record_line(line, line_no))

    if not drain_seen:
        raise ValueError(
            "no @MC:DRAIN summary found: UART log incomplete "
            "(was `mapcap drain` run to completion?)")
    if len(records) != drain_total:
        raise ValueError(
            f"record count mismatch: {len(records)} @MC:REC line(s) parsed, "
            f"@MC:DRAIN reports {drain_total} — UART log incomplete")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    return len(records)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} UART_LOG RAW_RECORDS_JSONL", file=sys.stderr)
        return 2
    try:
        count = export_uart_log(argv[1], argv[2])
    except (OSError, ValueError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    print(f"OK: exported {count} raw MapCaptureRecord(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
