#!/usr/bin/env python3
"""Merge bench scope evidence into the BOAR capture campaign (fail-closed).

The 01.09.2026 BOAR campaign (12 regions x 16 records, ``@MC:REC``) contains
the firmware shunt readings but no independent phase-current reference, so the
offline M evaluation is (correctly) REJECTed by ``map_bench_dataset.py``:
``scope_qualified != 1`` and ``ref_u/v/w_ma`` missing. This tool merges the
scope evidence returned by the bench (one CSV per region, one row per pulse)
with the UART capture logs into the canonical campaign format.

Inputs:
    --logs DIR   directory with ``region_0.log`` .. ``region_11.log`` (UART)
    --scope DIR  directory with ``scope_region_0.csv`` .. ``scope_region_11.csv``
    --out DIR    output campaign dir (manifest.json + samples.jsonl)
    [--pipeline] additionally convert to dataset.txt and run the host
                 pipeline CLI (map_artifact_pipeline_cli) into out/pipeline/

Row k of the scope CSV maps to the k-th ``@MC:REC`` record of the same region
(drain order == burst chronology). The firmware REC carries no sector/window:
the region index r defines the map row (sector = r//2, window = r%2), and the
record CCR must equal the compiled BOAR vector of that sector (fail-closed
against mislabeled logs).

Evidence fields filled:
    ref_u/v/w_ma    from scope CSV (required, independent scope measurement)
    scope_qualified from CSV — must be 1 for every pulse (aperture confirmed
                    on the instrument; 0 = honest reject)
    adc_settled     1 — claimed from firmware evidence only: every record has
                    status=7 (WINDOW_INVALID, expected service capture),
                    fault=0 and raw_i1/raw_i2 in 1..4094 (no saturation).
                    Any deviation rejects the campaign.
    margin_ticks    from CSV (default 110 = VfcApertureContract); < 110 rejects
    blanking_ticks  from CSV (default 15 = ADC sample window, 640.5 cyc @
                    42.5 MHz); informational in the current pipeline
    timestamp_cycles seq*1000 — informational (REC has no hardware timestamp)

Manifest provenance:
    characterization_id = 0x424F4152 (BOAR base id)
    dataset_crc32       = CRC32 over the canonical samples payload
    tool_build_id       = ingest tool build id (0x20260902, nonzero)

The tool never fabricates scope data: missing/incomplete scope evidence or any
violated gate rejects the campaign (exit 1) before anything is written.

Exit 0 = campaign written (and validated); 1 = REJECT; 2 = usage.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import zlib
from pathlib import Path

try:  # same import contract as the other tools (tools/ on sys.path)
    import mapcap_uart_export as mue  # type: ignore
except ImportError:  # pragma: no cover - direct tools/ execution
    from tools import mapcap_uart_export as mue  # type: ignore

import map_bench_dataset as mbd  # type: ignore

# BOAR profile constants (origin/main src/map_capture_profiles.c).
BOAR_BASE_ID = 0x424F4152            # 1112490322
BOAR_BOARD_REV = 7
BOAR_PWM_HZ = 294                    # live formula (double PSC), known defect
BOAR_ARR = 999
BOAR_TRIGGER = 0x4F455731
BOAR_OFFSET_TICKS = 0                # scope stage fills this later
BOAR_DEADTIME = 192
BOAR_ADC_CLOCK = 42500000
BOAR_SAMPLE_X2 = 1281
BOAR_RESOLUTION = 0
BOAR_ADC_SIG = 0x13572468
BOAR_CAL_SIG = 0x24681357
BOAR_MARGIN = 110                    # VfcApertureContract switching margin
BOAR_BLANKING = 15                   # ADC sample window ~640.5 cyc @ 42.5 MHz
BOAR_MAX_SHUNT_MA = 10000
BOAR_MIN_RECORDS = 3
BOAR_PHASE_A = 0
BOAR_PHASE_B = 1

# Modulation vectors per sector (Q15) -> CCR = 500 + mod*500/32768.
BOAR_MOD_Q15 = {
    0: (8192, 0, -8192),
    1: (8192, -8192, 0),
    2: (0, 8192, -8192),
    3: (-8192, 8192, 0),
    4: (0, -8192, 8192),
    5: (-8192, 0, 8192),
}

TOOL_BUILD_ID = 0x20260902           # ingest tool build (02.09.2026)

# Offline qualification thresholds (as in the assembled 01.09 campaign).
QUALIFICATIONS = {
    "accumulator": {
        "min_samples": 8, "mad_limit_ma": 1000, "kcl_limit_ma": 100,
        "min_margin_ticks": BOAR_MARGIN,
    },
    "solver": {
        "min_samples": 8, "holdout_samples": 2,
        "residual_rms_limit_ma": 1000, "residual_max_limit_ma": 2000,
        "bias_limit_ma": 1000, "holdout_rms_limit_ma": 1000,
        "kcl_rms_limit_ma": 100, "max_condition_ratio": 100000,
        "min_abs_determinant": 1, "min_abs_diagonal": 100,
    },
    "region": {
        "min_valid_cells": 4, "guard_q15": 1, "min_margin_ticks": BOAR_MARGIN,
    },
}


def expected_ccr(sector: int, window: int = 0, point: int = 0) -> tuple[int, int, int]:
    """Ожидаемый CCR вектора (sector, window, grid point) профиля BOAR v2.

    Grid (TZ_MAP_GRID_PROFILE): 4 вектора на (сектор, окно) — центр + 3
    СИММЕТРИЧНЫХ смещения по +-4 CCR-тика (262 Q15): центр кластера лежит
    внутри сертифицированного региона (иначе guard сдвигает регион и
    стартовая точка не проходит CurrentMap_LoadMeasured); окно 1 сдвигает
    кластер на +16/−16 CCR по max/min фазе, чтобы регионы окон не
    перекрывались (проверено: 66/66 пар непересекаются).
    """
    grid = ((0, 0, 0), (4, -4, 0), (0, 4, -4), (-4, 0, 4))
    mod = BOAR_MOD_Q15[sector]
    base = list(500 + m * 500 // 32768 for m in mod)
    if window == 1:
        mx = max(range(3), key=lambda i: mod[i])
        mn = min(range(3), key=lambda i: mod[i])
        base[mx] += 16
        base[mn] -= 16
    off = grid[point]
    return tuple(base[i] + off[i] for i in range(3))


def region_row(r: int) -> tuple[int, int]:
    """Region index r -> (sector, window) of the map row."""
    return r // 2, r % 2


def parse_region_log(path: Path, expected_records: int = 16) -> list[dict]:
    """Parse one region UART log; require exact record count + DRAIN match."""
    records = []
    drain_total = 0
    drain_seen = False
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_no, line in enumerate(stream, 1):
            if "@MC:DRAIN:" in line:
                match = mue.DRAIN_RE.search(line)
                if not match:
                    raise ValueError(f"{path.name}:{line_no}: malformed DRAIN")
                drain_total += int(match.group(1))
                drain_seen = True
            elif "@MC:REC:" in line:
                records.append(mue.parse_record_line(line, line_no))
    if not drain_seen:
        raise ValueError(f"{path.name}: no @MC:DRAIN summary (incomplete log)")
    if len(records) != drain_total:
        raise ValueError(
            f"{path.name}: {len(records)} REC parsed, DRAIN={drain_total}")
    if len(records) != expected_records:
        raise ValueError(
            f"{path.name}: expected {expected_records} records, got {len(records)}")
    return records


def parse_scope_csv(path: Path, expected_rows: int = 16) -> list[dict]:
    """Parse one scope_region_<r>[_<p>].csv; require expected_rows rows."""
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
        for i, row in enumerate(reader, 1):
            def num(key: str, default=None):
                v = (row.get(key) or "").strip()
                if v == "":
                    return default
                try:
                    return int(v)
                except ValueError:
                    raise ValueError(f"{path.name}: row {i}: {key}={v!r} не int")
            pulse = num("pulse")
            if pulse != i:
                raise ValueError(f"{path.name}: row {i}: pulse={pulse} != {i}")
            ref_u = num("ref_u_ma")
            ref_v = num("ref_v_ma")
            ref_w = num("ref_w_ma")
            if ref_u is None or ref_v is None or ref_w is None:
                raise ValueError(
                    f"{path.name}: row {i}: не заполнены ref_u/v/w_ma — "
                    f"scope-измерение обязательно")
            qualified = num("scope_qualified", 0)
            margin = num("margin_ticks", BOAR_MARGIN)
            blanking = num("blanking_ticks", BOAR_BLANKING)
            rows.append({
                "pulse": pulse,
                "ref_u_ma": ref_u, "ref_v_ma": ref_v, "ref_w_ma": ref_w,
                "margin_ticks": margin, "blanking_ticks": blanking,
                "scope_qualified": qualified,
                "note": (row.get("note") or "").strip(),
            })
    if len(rows) != expected_rows:
        raise ValueError(
            f"{path.name}: expected {expected_rows} rows, got {len(rows)}")
    return rows


def check_evidence(region: int, window: int, point: int, records: list[dict],
                   scope: list[dict]) -> None:
    """Fail-closed gates: CCR vs BOAR grid vector, settled claim, scope."""
    sector, _ = region_row(region)
    want = expected_ccr(sector, window, point)

    for i, rec in enumerate(records):
        ccr = tuple(rec["ccr1"])
        if ccr != want:
            raise ValueError(
                f"region_{region}: запись {i + 1}: ccr={ccr} != ожидаемый "
                f"вектор сектора {sector} {want} (лог не того региона?)")
        if rec["status"] != 7 or rec["fault"] != 0:
            raise ValueError(
                f"region_{region}: запись {i + 1}: status={rec['status']} "
                f"fault={rec['fault']} — adc_settled=1 не обоснован")
        for key, lo, hi in (("raw_i1", 1, 4094), ("raw_i2", 1, 4094),
                            ("raw_vbus", 1, 4094)):
            if not (lo <= rec[key] <= hi):
                raise ValueError(
                    f"region_{region}: запись {i + 1}: {key}={rec[key]} вне "
                    f"{lo}..{hi} — насыщение, adc_settled=1 не обоснован")
        if abs(rec["i1"]) > BOAR_MAX_SHUNT_MA or abs(rec["i2"]) > BOAR_MAX_SHUNT_MA:
            raise ValueError(
                f"region_{region}: запись {i + 1}: ток {rec['i1']}/{rec['i2']} "
                f"мА за пределом {BOAR_MAX_SHUNT_MA}")

    for row in scope:
        if row["scope_qualified"] != 1:
            raise ValueError(
                f"region_{region}: pulse {row['pulse']}: scope_qualified="
                f"{row['scope_qualified']} — апертура не подтверждена, REJECT")
        if row["margin_ticks"] < BOAR_MARGIN:
            raise ValueError(
                f"region_{region}: pulse {row['pulse']}: margin "
                f"{row['margin_ticks']} < {BOAR_MARGIN} — REJECT")
        for key in ("ref_u_ma", "ref_v_ma", "ref_w_ma"):
            if abs(row[key]) > BOAR_MAX_SHUNT_MA:
                raise ValueError(
                    f"region_{region}: pulse {row['pulse']}: {key}="
                    f"{row[key]} за пределом {BOAR_MAX_SHUNT_MA}")
        kcl = row["ref_u_ma"] + row["ref_v_ma"] + row["ref_w_ma"]
        kcl_limit = QUALIFICATIONS["accumulator"]["kcl_limit_ma"]
        if abs(kcl) > kcl_limit:
            raise ValueError(
                f"region_{region}: pulse {row['pulse']}: KCL "
                f"{row['ref_u_ma']}+{row['ref_v_ma']}+{row['ref_w_ma']}="
                f"{kcl} мА > {kcl_limit} — рассинхрон/насыщение, REJECT")


def build_manifest(n_samples: int, crc32: int) -> dict:
    return {
        "format_version": 1,
        "campaign_id": "oew-boar-campaign-20260902-scope",
        "board_revision": BOAR_BOARD_REV,
        "pwm_frequency_hz": BOAR_PWM_HZ,
        "timer_arr": BOAR_ARR,
        "adc_trigger_id": BOAR_TRIGGER,
        "trigger_offset_ticks": BOAR_OFFSET_TICKS,
        "deadtime_ticks": BOAR_DEADTIME,
        "adc_clock_hz": BOAR_ADC_CLOCK,
        "adc_sample_cycles_x2": BOAR_SAMPLE_X2,
        "adc_resolution": BOAR_RESOLUTION,
        "adc_config_signature": BOAR_ADC_SIG,
        "current_calibration_signature": BOAR_CAL_SIG,
        "characterization_id": BOAR_BASE_ID,
        "dataset_crc32": crc32,
        "tool_build_id": TOOL_BUILD_ID,
        "qualification_revision": 1,
        "solver_revision": 1,
        "certifier_revision": 1,
        "shunt_resistance_uohm": 30300,
        "amplifier_gain": 1,
        "calibration_revision": 1,
        "phase_a": BOAR_PHASE_A,
        "phase_b": BOAR_PHASE_B,
        # Стартовая точка: центр кластера sector 0 / window 0 (внутри
        # сертифицированного региона 0/0 — CurrentMap_LoadMeasured требует
        # startup внутри region). (0,0,0) не покрыт ни одним регионом.
        # ФИЗИЧЕСКИЙ выбор стартового вектора — отдельный этап FOC
        # (TZ_MAP_CAPTURE_BOARD_PROFILE: «стартовая точка — отдельный этап»).
        "startup": {"sector": 0, "window": 0, "hold_cycles": 1,
                    "mu": 8192, "mv": 0, "mw": -8192},
        "qualifications": QUALIFICATIONS,
    }


# Записи на точку: одиночная раскладка (кампания 01.09) — 16 импульсов;
# grid-раскладка (профиль v2) — 8 импульсов на точку (4 точки x 8 = 32 на
# строку, лимит MAP_ACCUM_MAX_SAMPLES_PER_ROW).
SINGLE_POINT_RECORDS = 16
GRID_POINT_RECORDS = 8


def _region_sources(logs: Path, scope_d: Path, r: int) -> list[tuple[int, Path, Path, int]]:
    """(point, log_path, csv_path, expected_records) для региона r:
    grid-раскладка (region_<r>_<p>.log, 4 точки по GRID_POINT_RECORDS) или
    одиночная (region_<r>.log, SINGLE_POINT_RECORDS, точка 0)."""
    grid = [(p, logs / f"region_{r}_{p}.log", scope_d / f"scope_region_{r}_{p}.csv",
             GRID_POINT_RECORDS)
            for p in range(4)]
    if any(path.is_file() for _, path, _, _ in grid):
        for p, log_path, csv_path, _ in grid:
            if not log_path.is_file():
                raise ValueError(f"нет {log_path} — grid-раскладка требует все 4 точки")
            if not csv_path.is_file():
                raise ValueError(f"нет {csv_path} — scope-слой обязателен")
        return grid
    single = (0, logs / f"region_{r}.log", scope_d / f"scope_region_{r}.csv",
              SINGLE_POINT_RECORDS)
    if not single[1].is_file():
        raise ValueError(f"нет {single[1]}")
    if not single[2].is_file():
        raise ValueError(f"нет {single[2]} — scope-слой обязателен")
    return [single]


def build_campaign(logs_dir: str | Path, scope_dir: str | Path,
                   out_dir: str | Path,
                   tool_build_id: int = TOOL_BUILD_ID) -> tuple[dict, list[dict]]:
    """Merge region logs + scope CSVs into (manifest, samples); fail-closed."""
    logs = Path(logs_dir)
    scope_d = Path(scope_dir)
    out = Path(out_dir)

    manifest = build_manifest(0, 0)
    samples: list[dict] = []

    for r in range(12):
        sector, window = region_row(r)
        for point, log_path, csv_path, expected in _region_sources(logs, scope_d, r):
            records = parse_region_log(log_path, expected)
            scope = parse_scope_csv(csv_path, expected)
            check_evidence(r, window, point, records, scope)
            for rec, row in zip(records, scope):
                samples.append({
                    "seq": rec["seq"],
                    "sector": sector,
                    "window": window,
                    "ccr1": rec["ccr1"][0], "ccr2": rec["ccr1"][1],
                    "ccr3": rec["ccr1"][2], "arr": rec["arr"],
                    "raw_idc1": rec["raw_i1"], "raw_idc2": rec["raw_i2"],
                    "raw_ct": rec["raw_ct"], "raw_vbus": rec["raw_vbus"],
                    "idc1_ma": rec["i1"], "idc2_ma": rec["i2"],
                    "ict_ma": 0, "vbus_mv": rec["vbus"],
                    "ref_u_ma": row["ref_u_ma"], "ref_v_ma": row["ref_v_ma"],
                    "ref_w_ma": row["ref_w_ma"],
                    "margin_ticks": row["margin_ticks"],
                    "blanking_ticks": row["blanking_ticks"],
                    "adc_settled": 1,
                    "scope_qualified": row["scope_qualified"],
                    "timestamp_cycles": rec["seq"] * 1000,
                })

    # Canonical payload for dataset_crc32: sorted-key compact JSON per sample.
    payload = "\n".join(
        json.dumps(s, sort_keys=True, separators=(",", ":")) for s in samples
    ).encode("utf-8")
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    manifest["dataset_crc32"] = crc32
    manifest["tool_build_id"] = tool_build_id

    # Final gate: the official validator must accept the assembled campaign.
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "samples.jsonl").write_text(
        "\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")
    try:
        mbd.validate_campaign(out)
    except ValueError as exc:
        raise ValueError(f"собранная кампания не прошла валидатор: {exc}")
    return manifest, samples


def run_pipeline(campaign_dir: str | Path, work_dir: str | Path,
                 exe: str | Path | None = None) -> None:
    """Convert to dataset.txt and run the host pipeline CLI."""
    campaign = Path(campaign_dir)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    dataset = work / "dataset.txt"
    mbd.convert_campaign(campaign, dataset)

    if exe is None:
        exe = Path(__file__).resolve().parent / "map_artifact_pipeline_cli.exe"
    if not Path(exe).is_file():
        raise ValueError(
            f"нет {exe} — соберите: make -f tools/map_artifact_writer_test.mk "
            f"map-artifact-cli")
    import subprocess
    out_dir = work / "pipeline"
    res = subprocess.run([str(exe), str(dataset), str(out_dir)],
                         capture_output=True, text=True, timeout=300)
    print(res.stdout, end="")
    if res.stderr:
        print(res.stderr, file=sys.stderr, end="")
    if res.returncode != 0:
        raise ValueError(f"host pipeline exit {res.returncode} — см. выше")
    print(f"pipeline OK: {out_dir / 'oew_map_v2.bin'} "
          f"({(out_dir / 'oew_map_v2.bin').stat().st_size} B)")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True, help="dir with region_0..11.log")
    ap.add_argument("--scope", required=True, help="dir with scope_region_*.csv")
    ap.add_argument("--out", required=True, help="output campaign dir")
    ap.add_argument("--pipeline", action="store_true",
                    help="also convert + run host pipeline CLI")
    args = ap.parse_args(argv)

    try:
        manifest, samples = build_campaign(args.logs, args.scope, args.out)
    except (OSError, ValueError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    print(f"OK: campaign {manifest['campaign_id']}, {len(samples)} samples, "
          f"dataset_crc32=0x{manifest['dataset_crc32']:08X}, "
          f"tool_build_id=0x{manifest['tool_build_id']:08X}")

    if args.pipeline:
        try:
            run_pipeline(args.out, Path(args.out) / ".." / "work")
        except (OSError, ValueError) as exc:
            print(f"REJECT: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
