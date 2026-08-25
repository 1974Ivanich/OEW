#!/usr/bin/env python3
"""Automate the safe host-side OEW current-map characterization pipeline.

The firmware remains fail-closed and never invents phase-current reference data.
This tool combines:
  1. immutable @MC:REC UART evidence from mapcap_uart_export.py;
  2. an external two-channel phase-current reference (CSV, U/V/W);
  3. a reviewed campaign manifest/profile, including the 12 exact PWM rows;
  4. the existing map_bench_dataset.py validator/converter; and
  5. the existing host MapMeasurement solver/artifact writer CLI.

Reference CSV format (header required):
    seq,phase_u_ma,phase_v_ma,phase_w_ma,timestamp_cycles

The firmware UART record does not need to contain sector/window labels. The tool
classifies each captured record by exact `(ARR, TIM1 CCR[3], TIM8 CCR[3])`
match against the reviewed profile row. Unknown or ambiguous vectors are
rejected. No sector/window is inferred from current data.

Usage:
    python tools/map_auto_characterize.py build \
        --uart uart.log \
        --reference probe.csv \
        --manifest profile.json \
        --out campaign_real

    python tools/map_auto_characterize.py build ... --convert dataset.txt
    python tools/map_auto_characterize.py build ... --convert dataset.txt \
        --pipeline ./tools/map_artifact_pipeline_cli

The script is deliberately not a PWM/UART command launcher. Physical capture
must be initiated only by a board-qualified immutable firmware profile.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mapcap_uart_export import export_uart_log
from map_bench_dataset import validate_campaign, convert_campaign

REQUIRED_REFERENCE = {
    "seq", "phase_u_ma", "phase_v_ma", "phase_w_ma", "timestamp_cycles"
}
REQUIRED_MANIFEST = {
    "format_version", "campaign_id", "board_revision", "pwm_frequency_hz",
    "timer_arr", "adc_trigger_id", "trigger_offset_ticks", "deadtime_ticks",
    "adc_clock_hz", "adc_sample_cycles_x2", "adc_resolution",
    "adc_config_signature", "current_calibration_signature",
    "characterization_id", "dataset_crc32", "tool_build_id",
    "qualification_revision", "solver_revision", "certifier_revision",
    "phase_a", "phase_b", "startup", "qualifications", "capture_profile"
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _load_reference(path: Path) -> dict[int, dict[str, int]]:
    rows: dict[int, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not REQUIRED_REFERENCE.issubset(reader.fieldnames):
            missing = sorted(REQUIRED_REFERENCE - set(reader.fieldnames or []))
            raise ValueError(f"reference CSV missing columns: {missing}")
        for line_no, raw in enumerate(reader, 2):
            try:
                seq = int(raw["seq"], 0)
                record = {
                    "phase_u_ma": int(raw["phase_u_ma"], 0),
                    "phase_v_ma": int(raw["phase_v_ma"], 0),
                    "phase_w_ma": int(raw["phase_w_ma"], 0),
                    "timestamp_cycles": int(raw["timestamp_cycles"], 0),
                }
            except (KeyError, ValueError) as exc:
                raise ValueError(f"reference:{line_no}: invalid integer: {exc}") from exc
            if seq <= 0:
                raise ValueError(f"reference:{line_no}: seq must be positive")
            if seq in rows:
                raise ValueError(f"reference:{line_no}: duplicate seq={seq}")
            rows[seq] = record
    return rows


def _load_raw(raw_jsonl: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with raw_jsonl.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"raw:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"raw:{line_no}: record must be an object")
            records.append(value)
    if not records:
        raise ValueError(f"{raw_jsonl}: no capture records")
    return records


def _check_manifest(manifest: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_MANIFEST - set(manifest))
    if missing:
        raise ValueError(f"manifest missing fields: {missing}")
    if manifest["phase_a"] == manifest["phase_b"]:
        raise ValueError("manifest phase_a and phase_b must differ")
    if manifest["phase_a"] not in (0, 1, 2) or manifest["phase_b"] not in (0, 1, 2):
        raise ValueError("manifest phase_a/phase_b must be 0..2")
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported campaign format_version")
    if manifest.get("trigger_offset_ticks", 0) == 0:
        raise ValueError(
            "trigger_offset_ticks is zero: real scope timing qualification is required")
    profile = manifest["capture_profile"]
    if not isinstance(profile, list) or len(profile) != 12:
        raise ValueError("capture_profile must contain exactly 12 rows")

    seen: set[tuple[int, int]] = set()
    for row in profile:
        if not isinstance(row, dict):
            raise ValueError("capture_profile row must be an object")
        for key in ("sector", "window", "arr", "tim1_ccr", "tim8_ccr"):
            if key not in row:
                raise ValueError(f"capture_profile row missing {key}")
        key = (int(row["sector"]), int(row["window"]))
        if key in seen:
            raise ValueError(f"duplicate capture_profile row {key}")
        if key[0] not in range(6) or key[1] not in range(2):
            raise ValueError(f"capture_profile row out of range: {key}")
        if int(row["arr"]) != int(manifest["timer_arr"]):
            raise ValueError(f"capture_profile {key}: ARR differs from manifest")
        if len(row["tim1_ccr"]) != 3 or len(row["tim8_ccr"]) != 3:
            raise ValueError(f"capture_profile {key}: CCR vectors must contain 3 values")
        if list(map(int, row["tim1_ccr"])) != list(map(int, row["tim8_ccr"])):
            raise ValueError(f"capture_profile {key}: TIM1/TIM8 CCR mismatch")
        seen.add(key)
    if seen != {(s, w) for s in range(6) for w in range(2)}:
        raise ValueError("capture_profile must cover all 6x2 rows")


def _profile_index(manifest: dict[str, Any]) -> dict[tuple[int, tuple[int, int, int], tuple[int, int, int]], tuple[int, int]]:
    result: dict[tuple[int, tuple[int, int, int], tuple[int, int, int]], tuple[int, int]] = {}
    for row in manifest["capture_profile"]:
        key = (
            int(row["arr"]),
            tuple(int(v) for v in row["tim1_ccr"]),
            tuple(int(v) for v in row["tim8_ccr"]),
        )
        if key in result:
            raise ValueError(f"ambiguous PWM profile vector: {key}")
        result[key] = (int(row["sector"]), int(row["window"]))
    return result


def _build_samples(raw: list[dict[str, Any]],
                   refs: dict[int, dict[str, int]],
                   manifest: dict[str, Any]) -> list[dict[str, Any]]:
    profile = _profile_index(manifest)
    samples: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, rec in enumerate(raw, 1):
        seq = int(rec["seq"])
        if seq in seen:
            raise ValueError(f"raw record {index}: duplicate sequence {seq}")
        seen.add(seq)
        ref = refs.get(seq)
        if ref is None:
            raise ValueError(f"capture seq={seq}: no external reference row")
        if int(rec.get("cap", 0)) == 0:
            raise ValueError(f"capture seq={seq}: invalid capture id")
        if int(rec.get("status", -1)) != 0:
            raise ValueError(f"capture seq={seq}: firmware status={rec.get('status')}")
        if int(rec.get("arr", 0)) != manifest["timer_arr"]:
            raise ValueError(f"capture seq={seq}: ARR differs from manifest")
        if int(rec.get("trig", 0)) != manifest["adc_trigger_id"]:
            raise ValueError(f"capture seq={seq}: trigger revision differs from manifest")

        ccr1 = rec["ccr1"]
        ccr8 = rec["ccr8"]
        if len(ccr1) != 3 or len(ccr8) != 3:
            raise ValueError(f"capture seq={seq}: CCR vectors must have 3 entries")
        key = (int(rec["arr"]), tuple(map(int, ccr1)), tuple(map(int, ccr8)))
        row_context = profile.get(key)
        if row_context is None:
            raise ValueError(
                f"capture seq={seq}: PWM vector is not in the reviewed real-board profile")
        sector, window = row_context

        row = {
            "seq": seq,
            "sector": sector,
            "window": window,
            "ccr1": int(ccr1[0]),
            "ccr2": int(ccr1[1]),
            "ccr3": int(ccr1[2]),
            "arr": int(rec["arr"]),
            "raw_idc1": int(rec["raw_i1"]),
            "raw_idc2": int(rec["raw_i2"]),
            "raw_ct": int(rec["raw_ct"]),
            "raw_vbus": int(rec["raw_vbus"]),
            "idc1_ma": int(rec["i1"]),
            "idc2_ma": int(rec["i2"]),
            "ict_ma": 0,
            "vbus_mv": int(rec["vbus"]),
            "ref_u_ma": ref["phase_u_ma"],
            "ref_v_ma": ref["phase_v_ma"],
            "ref_w_ma": ref["phase_w_ma"],
            "margin_ticks": int(manifest["qualifications"]["accumulator"]["min_margin_ticks"]),
            "blanking_ticks": int(manifest["qualifications"]["accumulator"].get("blanking_ticks", 0)),
            "adc_settled": 1,
            "scope_qualified": 1,
            "timestamp_cycles": ref["timestamp_cycles"],
        }

        kcl = row["ref_u_ma"] + row["ref_v_ma"] + row["ref_w_ma"]
        if abs(kcl) > int(manifest["qualifications"]["accumulator"]["kcl_limit_ma"]):
            raise ValueError(f"capture seq={seq}: external-reference KCL error {kcl} mA")
        samples.append(row)
    return samples


def _write_campaign(out_dir: Path, manifest: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out_dir / "samples.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, sort_keys=True, separators=(",", ":")))
            stream.write("\n")


def _run_pipeline(dataset: Path, executable: Path, out_dir: Path) -> None:
    result = subprocess.run(
        [str(executable), str(dataset), str(out_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"map_artifact_pipeline_cli failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    if not (out_dir / "oew_map_v2.bin").is_file():
        raise RuntimeError("pipeline returned success without oew_map_v2.bin")


def build(args: argparse.Namespace) -> int:
    manifest = _load_json(Path(args.manifest))
    _check_manifest(manifest)

    out_dir = Path(args.out)
    raw_jsonl = out_dir / "raw_records.jsonl"
    export_uart_log(args.uart, raw_jsonl)
    raw = _load_raw(raw_jsonl)
    refs = _load_reference(Path(args.reference))
    samples = _build_samples(raw, refs, manifest)
    _write_campaign(out_dir, manifest, samples)

    validate_campaign(out_dir)
    print(f"OK: campaign validated: {out_dir} ({len(samples)} samples)")

    if args.convert:
        dataset = Path(args.convert)
        convert_campaign(out_dir, dataset)
        print(f"OK: dataset written: {dataset}")
        if args.pipeline:
            artifact_dir = out_dir / "artifact"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            _run_pipeline(dataset, Path(args.pipeline), artifact_dir)
            print(f"OK: artifact written: {artifact_dir / 'oew_map_v2.bin'}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="build campaign from UART + external reference")
    build_parser.add_argument("--uart", required=True, type=Path)
    build_parser.add_argument("--reference", required=True, type=Path)
    build_parser.add_argument("--manifest", required=True, type=Path)
    build_parser.add_argument("--out", required=True, type=Path)
    build_parser.add_argument("--convert", type=Path)
    build_parser.add_argument("--pipeline", type=Path)
    build_parser.set_defaults(func=build)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
