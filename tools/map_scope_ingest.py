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
import hashlib
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
BOAR_PWM_HZ = 5000                   # tclk/(2*(ARR+1)) = 10e6/2000 (D3: без двойного PSC)
BOAR_ARR = 999
BOAR_TRIGGER = 0x4F455731
BOAR_OFFSET_TICKS = 0                # scope stage fills this later
BOAR_DEADTIME = 192
BOAR_ADC_CLOCK = 42500000
BOAR_SAMPLE_X2 = 1281
BOAR_RESOLUTION = 0
BOAR_ADC_SIG = 0x26B9B97B  # live from bench (ADC regs, stable across reboots)
BOAR_CAL_SIG = 0x13552B12  # scale constants + valid=1; NO raw offsets (drift)
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

# ── Reference-association layer (TZ_PHASE_REFERENCE_IMPLEMENTATION) ──────────
# Отказ привязки обязан называть КОНКРЕТНЫЙ гейт: контроль, который «просто
# видит другое число», вырождается в тавтологию (сдвинул — пересчитал — PASS).
ASSOC_REFERENCE_VERIFIED = "reference_verified"   # sample_id + признак подтверждены
ASSOC_POSITIONAL_LEGACY = "positional_legacy"     # порядок строк; НЕ доказательство
ASSOC_SHUNT_WAIVER = "shunt_waiver"               # G0 v4: независимого референса нет
ASSOC_FEATURE_KINDS = ("la_marker", "scope_edge", "other")
ASSOC_MIN_SAMPLES_PER_ROW = 1


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
        # Относительный детерминант det/(S00*S11) в ppm (см.
        # map_measurement_solver.h): 10000 = 1%. Абсолютный гейт det/1e12
        # отвергал mA-масштаб стенда (solver-фикс 02.09.2026).
        "min_abs_determinant": 10000, "min_abs_diagonal": 100,
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


def _load_calibration(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"calibration: не удалось прочитать {path}: {exc}")
    vcc = float(data.get("vcc_mv", 5000.0))
    if vcc <= 0.0:
        raise ValueError("calibration: vcc_mv должен быть положительным")
    sensors = data.get("sensors", {})
    result = {"vcc_mv": vcc, "sensors": {}}
    for name in ("U", "V"):
        item = sensors.get(name, {})
        v0 = float(item.get("v0_mv", vcc / 2.0))
        sens = float(item.get("sens_mv_per_a", 100.0 * vcc / 5000.0))
        if sens <= 0.0:
            raise ValueError(f"calibration: sensors.{name}.sens_mv_per_a должен быть положительным")
        result["sensors"][name] = (v0, sens)
    return result


def parse_scope_csv(path: Path, expected_rows: int = 16,
                    calibration: dict | None = None,
                    strict: bool = False) -> list[dict]:
    """Parse legacy mA or ACS712 mV CSV, selected from its header.

    strict=True — reference-режим: обязательны `sample_id`, `assoc_feature`,
    `assoc_feature_kind`; порядковое совпадение `pulse` привязкой НЕ считается.
    legacy (strict=False) сохранён для воспроизведения уже принятых кампаний."""
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
        fields = set(reader.fieldnames or [])
        has_ma = any(k in fields for k in ("ref_u_ma", "ref_v_ma", "ref_w_ma"))
        has_mv = any(k in fields for k in ("ref_u_mv", "ref_v_mv", "ref_w_mv"))
        if has_ma and has_mv:
            raise ValueError(f"{path.name}: смешение ref_*_ma и ref_*_mv запрещено")
        if not has_ma and not has_mv:
            raise ValueError(f"{path.name}: отсутствуют ref_*_ma или ref_*_mv")
        mode = "mv" if has_mv else "ma"
        if strict:
            missing = [k for k in ("sample_id", "assoc_feature",
                                   "assoc_feature_kind") if k not in fields]
            if missing:
                raise ValueError(
                    f"{path.name}: reference-режим требует колонки {missing} — "
                    f"порядковая привязка (pulse) доказательством не является")
        if mode == "mv" and calibration is None:
            raise ValueError(f"{path.name}: mV-режим требует --calib FILE")
        if mode == "ma" and not {"ref_u_ma", "ref_v_ma"}.issubset(fields):
            raise ValueError(f"{path.name}: нужны ref_u_ma и ref_v_ma")
        if mode == "mv" and not {"ref_u_mv", "ref_v_mv"}.issubset(fields):
            raise ValueError(f"{path.name}: нужны ref_u_mv и ref_v_mv")

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
            if not strict and pulse != i:
                raise ValueError(f"{path.name}: row {i}: pulse={pulse} != {i}")

            def fnum(key: str):
                v = (row.get(key) or "").strip()
                if v == "":
                    return None
                try:
                    return float(v)
                except ValueError:
                    raise ValueError(f"{path.name}: row {i}: {key}={v!r} не число")

            sample_id = num("sample_id") if strict else None
            if strict and sample_id is None:
                raise ValueError(
                    f"{path.name}: row {i}: нет sample_id "
                    f"(гейт sample_id_coverage)")
            assoc_feature = (row.get("assoc_feature") or "").strip() if strict else ""
            if strict and assoc_feature == "":
                raise ValueError(
                    f"{path.name}: row {i}: пустой assoc_feature "
                    f"(гейт assoc_feature_identity)")
            assoc_kind = (row.get("assoc_feature_kind") or "").strip() if strict else ""
            if strict and assoc_kind not in ASSOC_FEATURE_KINDS:
                raise ValueError(
                    f"{path.name}: row {i}: assoc_feature_kind={assoc_kind!r} "
                    f"не из {ASSOC_FEATURE_KINDS}")
            ref_u_mv = ref_v_mv = ref_w_mv = None
            if mode == "ma":
                ref_u, ref_v = num("ref_u_ma"), num("ref_v_ma")
                ref_w = num("ref_w_ma")
            else:
                ref_u_mv, ref_v_mv = num("ref_u_mv"), num("ref_v_mv")
                if ref_u_mv is None or ref_v_mv is None:
                    raise ValueError(f"{path.name}: row {i}: ref_u/v_mv обязательны")
                u0, us = calibration["sensors"]["U"]
                v0, vs = calibration["sensors"]["V"]
                ref_u = int(round((ref_u_mv - u0) / us * 1000.0))
                ref_v = int(round((ref_v_mv - v0) / vs * 1000.0))
                ref_w_mv = num("ref_w_mv")
                ref_w = ref_w_mv
                if ref_w is not None:
                    w0, ws = calibration["sensors"].get("W", (calibration["vcc_mv"] / 2.0, 100.0 * calibration["vcc_mv"] / 5000.0))
                    ref_w = int(round((ref_w - w0) / ws * 1000.0))
            if ref_u is None or ref_v is None:
                raise ValueError(f"{path.name}: row {i}: ref_u/ref_v не заполнены")
            if ref_w is None:
                ref_w = -(ref_u + ref_v)
            qualified = num("scope_qualified", 0)
            margin = num("margin_ticks", BOAR_MARGIN)
            blanking = num("blanking_ticks", BOAR_BLANKING)
            rows.append({"pulse": pulse, "ref_u_ma": ref_u, "ref_v_ma": ref_v,
                         "ref_w_ma": ref_w, "margin_ticks": margin,
                         "blanking_ticks": blanking, "scope_qualified": qualified,
                         "sample_id": sample_id, "assoc_feature": assoc_feature,
                         "assoc_feature_kind": assoc_kind,
                         "dt_us": fnum("dt_us"),
                         "ref_u_mv": ref_u_mv, "ref_v_mv": ref_v_mv,
                         "ref_w_mv": ref_w_mv,
                         "note": (row.get("note") or "").strip()})
    if len(rows) != expected_rows:
        raise ValueError(f"{path.name}: expected {expected_rows} rows, got {len(rows)}")
    return rows


class AssocReject(ValueError):
    """Отказ слоя привязки С НАЗВАНИЕМ гейта (E4 требует именно гейт, не «числа не сошлись»).

    Наследник ValueError: для вызывающего это обычный REJECT данных, но с полем gate.
    """

    def __init__(self, gate: str, message: str):
        super().__init__(message)
        self.gate = gate
        self.message = message


class ExpectedReject(Exception):
    """Негативный контроль E4: умышленный сдвиг привязки ОБЯЗАН быть отклонён."""


def _load_assoc_map(path: str | Path) -> dict[int, tuple[str, str]]:
    """CSV `seq,assoc_feature[,assoc_feature_kind]` — внешняя карта привязки.

    Снимается на том же прогоне (например, с ЛА) и делает привязку проверяемой:
    ingest сверяет признак reference-строки с картой ПО ТОЖДЕСТВУ, а не по
    корреляции и не по порядку строк.
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"assoc-map: нет файла {p}")
    result: dict[int, tuple[str, str]] = {}
    with p.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(row for row in stream
                                if not row.lstrip().startswith("#"))
        fields = set(reader.fieldnames or [])
        if not {"seq", "assoc_feature"}.issubset(fields):
            raise ValueError(f"{p.name}: нужны колонки seq и assoc_feature")
        for i, row in enumerate(reader, 1):
            raw_seq = (row.get("seq") or "").strip()
            feature = (row.get("assoc_feature") or "").strip()
            kind = (row.get("assoc_feature_kind") or "other").strip() or "other"
            if raw_seq == "" or feature == "":
                raise ValueError(f"{p.name}: row {i}: seq/assoc_feature не заполнены")
            try:
                seq = int(raw_seq)
            except ValueError:
                raise ValueError(f"{p.name}: row {i}: seq={raw_seq!r} не int")
            if seq in result:
                raise ValueError(f"{p.name}: row {i}: seq={seq} дублируется "
                                 f"(гейт sample_id_unique)")
            if kind not in ASSOC_FEATURE_KINDS:
                raise ValueError(f"{p.name}: row {i}: assoc_feature_kind={kind!r} "
                                 f"не из {ASSOC_FEATURE_KINDS}")
            result[seq] = (feature, kind)
    if not result:
        raise ValueError(f"{p.name}: пустая карта привязки")
    return result


def _assoc_join(region: int, records: list[dict], scope: list[dict],
                shift: int = 0) -> list[dict]:
    """Запись -> reference-строка ПО `sample_id` (не по порядку строк)."""
    by_id: dict[int, dict] = {}
    for row in scope:
        sid = row.get("sample_id")
        if sid is None:
            raise AssocReject("sample_id_coverage",
                              f"region_{region}: reference-строка "
                              f"pulse={row.get('pulse')}: нет sample_id")
        if sid in by_id:
            raise AssocReject("sample_id_unique",
                              f"region_{region}: sample_id={sid} дублируется "
                              f"в reference")
        by_id[sid] = row
    pairs, used = [], set()
    for rec in records:
        seq = rec["seq"]
        row = by_id.get(seq + shift)
        if row is None:
            raise AssocReject(
                "sample_id_coverage",
                f"region_{region}: seq={seq} (shift={shift:+d}): нет reference-строки "
                f"с sample_id={seq + shift}")
        used.add(seq + shift)
        pairs.append(row)
    unused = sorted(set(by_id) - used)
    if unused:
        raise AssocReject("sample_id_coverage",
                          f"region_{region}: reference-строки не отнесены ни к одной "
                          f"записи: sample_id={unused}")
    return pairs


def check_association(region: int, records: list[dict], pairs: list[dict],
                      assoc_map: dict[int, tuple[str, str]] | None,
                      shift: int = 0,
                      dt_window_us: tuple[float, float] | None = None,
                      saturation_margin_mv: float | None = None,
                      vcc_mv: float | None = None) -> None:
    """Гейты привязки/синхронности reference — fail-closed, каждый отказ назван."""
    if saturation_margin_mv is not None and not any(
            row.get(k) is not None for row in pairs
            for k in ("ref_u_mv", "ref_v_mv", "ref_w_mv")):
        raise ValueError(
            "saturation: E5 требует mV-режима reference (ref_*_mv) — в legacy mA-режиме "
            "проверка упора невыполнима, и молча пропускать её запрещено")
    for rec, row in zip(records, pairs):
        seq = rec["seq"]
        shown_id = seq + shift
        if assoc_map is not None:
            entry = assoc_map.get(seq)
            if entry is None:
                raise AssocReject("sample_id_coverage",
                                  f"region_{region}: seq={seq} отсутствует в assoc-map")
            feature, kind = entry
            row_kind = row.get("assoc_feature_kind")
            if row_kind not in (None, "", kind):
                raise AssocReject(
                    "assoc_feature_identity",
                    f"region_{region}: seq={seq}: тип признака reference={row_kind!r} "
                    f"!= объявленного {kind!r}")
            if (row.get("assoc_feature") or "") != feature:
                raise AssocReject(
                    "assoc_feature_identity",
                    f"region_{region}: seq={seq} (sample_id={shown_id}): признак "
                    f"reference={(row.get('assoc_feature') or '')!r} != карты "
                    f"{feature!r} (kind={kind})")
        if dt_window_us is not None:
            dt = row.get("dt_us")
            if dt is None:
                raise AssocReject("dt_window",
                                  f"region_{region}: seq={seq}: нет dt_us при "
                                  f"объявленном окне {dt_window_us}")
            lo, hi = dt_window_us
            if not (lo <= dt <= hi):
                raise AssocReject("dt_window",
                                  f"region_{region}: seq={seq}: dt_us={dt} вне окна "
                                  f"[{lo}, {hi}]")
        if saturation_margin_mv is not None:
            if vcc_mv is None:
                raise ValueError("saturation: нужен vcc_mv (--calib)")
            for key in ("ref_u_mv", "ref_v_mv", "ref_w_mv"):
                value = row.get(key)
                if value is None:
                    continue
                if not (saturation_margin_mv <= value <= vcc_mv - saturation_margin_mv):
                    raise AssocReject(
                        "reference_saturation",
                        f"region_{region}: seq={seq}: {key}={value} мВ вне "
                        f"[{saturation_margin_mv}, {vcc_mv - saturation_margin_mv}] — "
                        f"датчик в упоре/отключён")


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


def _region_sources(logs: Path, scope_d: Path, r: int,
                    scope_waiver: bool = False
                    ) -> list[tuple[int, Path, Path | None, int]]:
    """(point, log_path, csv_path, expected_records) для региона r:
    grid-раскладка (region_<r>_<p>.log, 4 точки по GRID_POINT_RECORDS) или
    одиночная (region_<r>.log, SINGLE_POINT_RECORDS, точка 0).

    With scope_waiver=True, csv_path is None (scope CSV not required)."""
    grid_logs = [(p, logs / f"region_{r}_{p}.log",
                  scope_d / f"scope_region_{r}_{p}.csv" if not scope_waiver else None,
                  GRID_POINT_RECORDS)
                 for p in range(4)]
    if any((logs / f"region_{r}_{p}.log").is_file() for p in range(4)):
        for p, log_path, csv_path, _ in grid_logs:
            if not log_path.is_file():
                raise ValueError(f"нет {log_path} — grid-раскладка требует все 4 точки")
            if csv_path is not None and not csv_path.is_file():
                raise ValueError(f"нет {csv_path} — scope-слой обязателен")
        return grid_logs
    single_log = logs / f"region_{r}.log"
    single_csv = scope_d / f"scope_region_{r}.csv" if not scope_waiver else None
    if not single_log.is_file():
        raise ValueError(f"нет {single_log}")
    if single_csv is not None and not single_csv.is_file():
        raise ValueError(f"нет {single_csv} — scope-слой обязателен")
    return [(0, single_log, single_csv, SINGLE_POINT_RECORDS)]


def _shunt_ref_rows(records: list[dict]) -> list[dict]:
    """Synthesize scope-equivalent rows from shunt ADC data (G0 v4 waiver).

    Under the scope waiver, the STM32G474 ADC1+ADC2 shunt data is authoritative.
    ref_u_ma = idc1_ma (shunt 1, phase U)
    ref_v_ma = idc2_ma (shunt 2, phase V)
    ref_w_ma = -(i1 + i2) (KCL: sum of 3 phase currents = 0)
    scope_qualified = 1 (shunt ADC is the qualified evidence per G0 v4)
    margin_ticks = BOAR_MARGIN (hardware aperture, same as profile)
    blanking_ticks = BOAR_BLANKING (ADC sample window)
    """
    rows = []
    for i, rec in enumerate(records):
        ref_u = rec["i1"]
        ref_v = rec["i2"]
        ref_w = -(ref_u + ref_v)
        rows.append({
            "pulse": i + 1,
            "ref_u_ma": ref_u,
            "ref_v_ma": ref_v,
            "ref_w_ma": ref_w,
            "margin_ticks": BOAR_MARGIN,
            "blanking_ticks": BOAR_BLANKING,
            "scope_qualified": 1,
            "note": "shunt_adc_waiver",
        })
    return rows


def build_campaign(logs_dir: str | Path, scope_dir: str | Path,
                   out_dir: str | Path,
                   tool_build_id: int = TOOL_BUILD_ID,
                   calib_file: str | Path | None = None,
                   scope_waiver: bool = False,
                   assoc_map_file: str | Path | None = None,
                   strict_association: bool = False,
                   dt_window_us: tuple[float, float] | None = None,
                   saturation_margin_mv: float | None = None,
                   neg_shift: int = 0) -> tuple[dict, list[dict]]:
    """Merge region logs + scope CSVs into (manifest, samples); fail-closed.

    scope_waiver=True (G0 v4): scope CSVs не требуются, независимого референса НЕТ
    (ref_u_ma = i1, ref_v_ma = i2, ref_w_ma = -(i1+i2)) — это НЕ привязка, а
    самоподтверждение, и так и штампуется в манифесте (association.mode).

    strict_association=True: привязка только по `sample_id == frame.sequence` и
    по признаку из assoc-map (тождество). Порядок строк доказательством не является.

    neg_shift != 0: негативный контроль E4 — умышленный сдвиг привязки обязан быть
    отклонён для КАЖДОГО региона; иначе прогон считается несостоявшимся (fail-closed).
    """
    logs = Path(logs_dir)
    scope_d = Path(scope_dir) if not scope_waiver else Path(".")
    out = Path(out_dir)

    manifest = build_manifest(0, 0)
    calibration = _load_calibration(calib_file) if calib_file is not None else None
    samples: list[dict] = []

    if strict_association and scope_waiver:
        raise ValueError("strict_association и scope_waiver несовместимы: "
                         "waiver-режим независимого референса не имеет")
    assoc_map = _load_assoc_map(assoc_map_file) if assoc_map_file else None
    if strict_association and assoc_map is None:
        raise ValueError("reference-режим требует --assoc-map FILE "
                         "(карту соответствия seq -> признак)")
    vcc_mv = calibration["vcc_mv"] if calibration else None
    neg_shift_gates: list[str] = []
    neg_shift_unrejected: list[int] = []

    for r in range(12):
        sector, window = region_row(r)
        for point, log_path, csv_path, expected in _region_sources(
                logs, scope_d, r, scope_waiver=scope_waiver):
            records = parse_region_log(log_path, expected)
            if scope_waiver:
                scope = _shunt_ref_rows(records)
            else:
                scope = parse_scope_csv(csv_path, expected, calibration,
                                        strict=strict_association)
            check_evidence(r, window, point, records, scope)
            if strict_association:
                try:
                    pairs = _assoc_join(r, records, scope, shift=neg_shift)
                    check_association(r, records, pairs, assoc_map, shift=neg_shift,
                                      dt_window_us=dt_window_us,
                                      saturation_margin_mv=saturation_margin_mv,
                                      vcc_mv=vcc_mv)
                except AssocReject as exc:
                    if neg_shift:
                        neg_shift_gates.append(exc.gate)
                        continue
                    raise ValueError(f"{exc.message} (гейт {exc.gate})") from exc
                if neg_shift:
                    neg_shift_unrejected.append(r)
                    continue
            else:
                pairs = scope
            for rec, row in zip(records, pairs):
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
                    "sample_id": rec["seq"],
                    "assoc_feature": row.get("assoc_feature") or "",
                })

    # Canonical payload for dataset_crc32: sorted-key compact JSON per sample.
    payload = "\n".join(
        json.dumps(s, sort_keys=True, separators=(",", ":")) for s in samples
    ).encode("utf-8")
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    manifest["dataset_crc32"] = crc32
    manifest["tool_build_id"] = tool_build_id

    if scope_waiver:
        assoc_mode = ASSOC_SHUNT_WAIVER
    elif strict_association:
        assoc_mode = ASSOC_REFERENCE_VERIFIED
    else:
        assoc_mode = ASSOC_POSITIONAL_LEGACY
    assoc_map_sha = (hashlib.sha256(Path(assoc_map_file).read_bytes()).hexdigest()
                     if assoc_map_file else None)
    manifest["association"] = {
        "mode": assoc_mode,
        "independent_reference": assoc_mode == ASSOC_REFERENCE_VERIFIED,
        "sample_id_field": "sample_id" if strict_association else None,
        "assoc_map_sha256": assoc_map_sha,
        "dt_window_us": list(dt_window_us) if dt_window_us else None,
        "saturation_margin_mv": saturation_margin_mv,
        "neg_shift_gates": neg_shift_gates,
        "neg_shift_unrejected_regions": neg_shift_unrejected,
    }
    if neg_shift:
        if neg_shift_unrejected:
            raise ValueError(
                f"E4 НЕ ПРОЙДЕН: сдвиг {neg_shift:+d} НЕ отклонён в регионах "
                f"{neg_shift_unrejected} — привязка считается недоказанной")
        if not neg_shift_gates:
            raise ValueError(f"E4 НЕ ПРОЙДЕН: сдвиг {neg_shift:+d} не проверен")
        print(f"E4 PASS: сдвиг {neg_shift:+d} отклонён во ВСЕХ регионах, "
              f"гейты: {sorted(set(neg_shift_gates))}")
        return manifest, samples

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
    ap.add_argument("--scope", default=None,
                    help="dir with scope_region_*.csv (not required with --scope-waiver)")
    ap.add_argument("--out", required=True, help="output campaign dir")
    ap.add_argument("--calib", help="ACS712 calibration JSON (required for mV CSV)")
    ap.add_argument("--assoc-map",
                    help="CSV seq,assoc_feature[,assoc_feature_kind] — карта привязки")
    ap.add_argument("--allow-legacy-order-association", action="store_true",
                    help="ЯВНО разрешить порядковую привязку: режим штампа "
                         "positional_legacy, доказательством не является")
    ap.add_argument("--dt-window-us", nargs=2, type=float, metavar=("MIN", "MAX"),
                    help="объявленное окно Δt (мкс) для reference-режима")
    ap.add_argument("--no-dt-window", action="store_true",
                    help="явно отказаться от окна Δt (в манифесте null, E3 не предъявляется)")
    ap.add_argument("--saturation-margin-mv", type=float, default=None,
                    help="E5: reference обязан лежать в [margin, vcc-margin] мВ")
    ap.add_argument("--neg-shift", type=int, default=0, choices=(0, 1, -1),
                    help="E4: самопроверка — сдвиг привязки ±1 sample; PASS только если отклонён")
    ap.add_argument("--scope-waiver", action="store_true",
                    help="G0 v4: use shunt ADC as authoritative reference (no scope CSV)")
    ap.add_argument("--pipeline", action="store_true",
                    help="also convert + run host pipeline CLI")
    args = ap.parse_args(argv)

    if not args.scope_waiver and args.scope is None:
        ap.error("--scope is required unless --scope-waiver is given")

    strict = not args.scope_waiver and not args.allow_legacy_order_association
    if strict and not args.assoc_map:
        ap.error("reference-режим требует --assoc-map FILE "
                 "(или явный --allow-legacy-order-association)")
    if strict and args.dt_window_us is None and not args.no_dt_window:
        ap.error("reference-режим требует --dt-window-us MIN MAX "
                 "(или явный --no-dt-window)")

    try:
        manifest, samples = build_campaign(
            args.logs, args.scope or ".", args.out,
            calib_file=args.calib, scope_waiver=args.scope_waiver,
            assoc_map_file=args.assoc_map, strict_association=strict,
            dt_window_us=tuple(args.dt_window_us) if args.dt_window_us else None,
            saturation_margin_mv=args.saturation_margin_mv,
            neg_shift=args.neg_shift)
    except (OSError, ValueError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    print(f"OK: campaign {manifest['campaign_id']}, {len(samples)} samples, "
          f"dataset_crc32=0x{manifest['dataset_crc32']:08X}, "
          f"tool_build_id=0x{manifest['tool_build_id']:08X}, "
          f"association={manifest['association']['mode']}")

    if args.pipeline:
        try:
            run_pipeline(args.out, Path(args.out) / ".." / "work")
        except (OSError, ValueError) as exc:
            print(f"REJECT: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
