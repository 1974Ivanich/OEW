#!/usr/bin/env python3
"""Bench campaign dataset: validator + converter.

Канонический формат стендовой кампании (manifest.json + samples.jsonl,
см. tools/map_bench_dataset.md). Модуль:

  * validate_campaign(dir)   — проверяет схему, evidence-гейты, полноту строк;
  * convert_campaign(dir, out) — генерирует dataset.txt для host-конвейера
    (CLI map_artifact_pipeline_cli), выводя ячейки регионов из сэмплов
    (modulation-точка из ccr1/2/3 + arr по формуле MapMeasurement_CcrToQ15);

CLI:
  python tools/map_bench_dataset.py <campaign_dir> [out.txt]

Exit 0 = валидно и сконвертировано; 1 = ошибки валидации (в stderr).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FORMAT_VERSION = 1
SECTORS = 6
WINDOWS = 2

# Поля identity, которые обязаны быть ненулевыми (fail-closed, как в writer).
IDENTITY_NONZERO = (
    "board_revision", "pwm_frequency_hz", "timer_arr", "adc_trigger_id",
    "adc_clock_hz", "adc_sample_cycles_x2",
    "adc_config_signature", "current_calibration_signature",
)
PROVENANCE_NONZERO = (
    "characterization_id", "dataset_crc32", "tool_build_id",
    "qualification_revision", "solver_revision", "certifier_revision",
)
SAMPLE_KEYS = (
    "seq", "sector", "window", "ccr1", "ccr2", "ccr3", "arr",
    "raw_idc1", "raw_idc2", "raw_ct", "raw_vbus",
    "idc1_ma", "idc2_ma", "ict_ma", "vbus_mv",
    "ref_u_ma", "ref_v_ma", "ref_w_ma",
    "margin_ticks", "blanking_ticks", "adc_settled", "scope_qualified",
    "timestamp_cycles",
)


def q15_from_ccr(ccr: int, arr: int) -> int:
    """Зеркало MapMeasurement_CcrToQ15 (src/map_measurement_accumulator.c)."""
    if arr == 0:
        return 0
    mid = (arr + 1) // 2
    if mid <= 0:
        return 0
    num = (ccr - mid) * 32768
    if num >= 0:
        value = (num + mid // 2) // mid
    else:
        value = (num - mid // 2) // mid
    return max(-32768, min(32767, value))


def _errors_manifest(m: dict) -> list[str]:
    errs = []
    if m.get("format_version") != FORMAT_VERSION:
        errs.append(f"format_version: ожидается {FORMAT_VERSION}")
    for key in IDENTITY_NONZERO:
        if not isinstance(m.get(key), int) or m[key] == 0:
            errs.append(f"identity.{key}: обязано быть ненулевым целым")
    for key in PROVENANCE_NONZERO:
        if not isinstance(m.get(key), int) or m[key] == 0:
            errs.append(f"provenance.{key}: обязано быть ненулевым целым")
    for key in ("phase_a", "phase_b"):
        if not isinstance(m.get(key), int) or not 0 <= m[key] < 3:
            errs.append(f"phase_{key[-1]}: вне диапазона 0..2")
    if m.get("phase_a") == m.get("phase_b"):
        errs.append("phase_a == phase_b: шунты должны быть разными фазами")
    st = m.get("startup")
    if not isinstance(st, dict):
        errs.append("startup: отсутствует")
    else:
        if not 0 <= st.get("sector", -1) < SECTORS:
            errs.append("startup.sector: вне диапазона")
        if not 0 <= st.get("window", -1) < WINDOWS:
            errs.append("startup.window: вне диапазона")
        if not isinstance(st.get("hold_cycles"), int) or st.get("hold_cycles", 0) == 0:
            errs.append("startup.hold_cycles: обязано быть ненулевым")
        for k in ("mu", "mv", "mw"):
            if not isinstance(st.get(k), int):
                errs.append(f"startup.{k}: отсутствует")
    q = m.get("qualifications")
    if not isinstance(q, dict):
        errs.append("qualifications: отсутствует")
    else:
        for group, required in (
            ("accumulator", ("min_samples", "mad_limit_ma", "kcl_limit_ma",
                             "min_margin_ticks")),
            ("solver", ("min_samples", "holdout_samples",
                        "residual_rms_limit_ma", "residual_max_limit_ma",
                        "bias_limit_ma", "holdout_rms_limit_ma",
                        "kcl_rms_limit_ma", "max_condition_ratio",
                        "min_abs_determinant", "min_abs_diagonal")),
            ("region", ("min_valid_cells", "guard_q15", "min_margin_ticks")),
        ):
            g = q.get(group)
            if not isinstance(g, dict):
                errs.append(f"qualifications.{group}: отсутствует")
                continue
            for key in required:
                if not isinstance(g.get(key), int):
                    errs.append(f"qualifications.{group}.{key}: отсутствует")
    return errs


def _errors_samples(samples: list[dict], m: dict) -> list[str]:
    errs = []
    seen_rows: set[tuple[int, int]] = set()

    for i, s in enumerate(samples):
        where = f"samples[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{where}: не объект JSON")
            continue
        missing = [k for k in SAMPLE_KEYS if k not in s]
        if missing:
            errs.append(f"{where}: отсутствуют поля {missing}")
            continue
        row = (s["sector"], s["window"])
        if not (0 <= row[0] < SECTORS and 0 <= row[1] < WINDOWS):
            errs.append(f"{where}: sector/window вне диапазона")
            continue
        seen_rows.add(row)

        # Evidence-гейты (зеркало MapCharacterizationAdapter). Дубликаты seq,
        # KCL, condition и регионы проверяет C-конвейер (см. REJECT-матрицу).
        if s.get("adc_settled") != 1:
            errs.append(f"{where} row {row}: adc_settled!=1 — REJECT")
        if s.get("scope_qualified") != 1:
            errs.append(f"{where} row {row}: scope_qualified!=1 — REJECT")
        if not isinstance(s.get("margin_ticks"), int) or s.get("margin_ticks", 0) <= 0:
            errs.append(f"{where} row {row}: margin_ticks<=0 — REJECT")

    for sector in range(SECTORS):
        for window in range(WINDOWS):
            if (sector, window) not in seen_rows:
                errs.append(f"строка {sector}/{window}: нет сэмплов")
    return errs


def validate_campaign(campaign_dir: str | Path) -> tuple[dict, list[dict]]:
    """Возвращает (manifest, samples); кидает ValueError со списком ошибок."""
    d = Path(campaign_dir)
    manifest_path = d / "manifest.json"
    samples_path = d / "samples.jsonl"
    if not manifest_path.is_file():
        raise ValueError(f"нет {manifest_path}")
    if not samples_path.is_file():
        raise ValueError(f"нет {samples_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = []
    with samples_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"samples.jsonl:{line_no}: не JSON: {exc}") from exc
    errs = _errors_manifest(manifest) + _errors_samples(samples, manifest)
    if errs:
        raise ValueError("; ".join(errs))
    return manifest, samples


def convert_campaign(campaign_dir: str | Path, out_txt: str | Path) -> None:
    """Валидирует кампанию и пишет dataset.txt для map_artifact_pipeline_cli."""
    manifest, samples = validate_campaign(campaign_dir)
    m = manifest
    st = m["startup"]
    q = m["qualifications"]
    aq, sq, rq = q["accumulator"], q["solver"], q["region"]

    lines = [
        f"# campaign {m.get('campaign_id', '?')} (converted by map_bench_dataset.py)",
        f"identity board={m['board_revision']} pwm={m['pwm_frequency_hz']} "
        f"arr={m['timer_arr']} trigger=0x{m['adc_trigger_id']:08X} "
        f"toff={m['trigger_offset_ticks']} dt={m['deadtime_ticks']} "
        f"clk={m['adc_clock_hz']} smp={m['adc_sample_cycles_x2']} "
        f"res={m['adc_resolution']} acs=0x{m['adc_config_signature']:08X} "
        f"ccs=0x{m['current_calibration_signature']:08X}",
        f"provenance cid=0x{m['characterization_id']:08X} "
        f"dcrc=0x{m['dataset_crc32']:08X} tb=0x{m['tool_build_id']:08X} "
        f"qr={m['qualification_revision']} sr={m['solver_revision']} "
        f"cr={m['certifier_revision']}",
        f"startup sector={st['sector']} window={st['window']} "
        f"hold={st['hold_cycles']} mu={st['mu']} mv={st['mv']} mw={st['mw']}",
        f"accumq min={aq['min_samples']} mad={aq['mad_limit_ma']} "
        f"kcl={aq['kcl_limit_ma']} margin={aq['min_margin_ticks']}",
        f"solverq min={sq['min_samples']} holdout={sq['holdout_samples']} "
        f"rms={sq['residual_rms_limit_ma']} max={sq['residual_max_limit_ma']} "
        f"bias={sq['bias_limit_ma']} hrms={sq['holdout_rms_limit_ma']} "
        f"kclrms={sq['kcl_rms_limit_ma']} cond={sq['max_condition_ratio']} "
        f"det={sq['min_abs_determinant']} diag={sq['min_abs_diagonal']}",
        f"regionq min={rq['min_valid_cells']} guard={rq['guard_q15']} "
        f"margin={rq['min_margin_ticks']}",
    ]

    by_row: dict[tuple[int, int], list[dict]] = {}
    for s in samples:
        by_row.setdefault((s["sector"], s["window"]), []).append(s)

    for sector in range(SECTORS):
        for window in range(WINDOWS):
            row_samples = sorted(
                by_row.get((sector, window), []), key=lambda s: s["seq"])
            lines.append(
                f"row sector={sector} window={window} "
                f"phase_a={m['phase_a']} phase_b={m['phase_b']}")
            for s in row_samples:
                lines.append(
                    f"sample seq={s['seq']} idc1={s['idc1_ma']} "
                    f"idc2={s['idc2_ma']} ict={s['ict_ma']} "
                    f"vbus={s['vbus_mv']} refu={s['ref_u_ma']} "
                    f"refv={s['ref_v_ma']} refw={s['ref_w_ma']} "
                    f"margin={s['margin_ticks']} settled={s['adc_settled']} "
                    f"scope={s['scope_qualified']}")
            for s in row_samples:
                mu = q15_from_ccr(s["ccr1"], s["arr"])
                mv = q15_from_ccr(s["ccr2"], s["arr"])
                mw = q15_from_ccr(s["ccr3"], s["arr"])
                lines.append(
                    f"cell mu={mu} mv={mv} mw={mw} "
                    f"margin={s['margin_ticks']} status=1")

    Path(out_txt).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 3:
        print("usage: map_bench_dataset.py <campaign_dir> [out.txt]",
              file=sys.stderr)
        return 2
    try:
        manifest, samples = validate_campaign(argv[1])
    except ValueError as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    out = argv[2] if len(argv) == 3 else "dataset.txt"
    convert_campaign(argv[1], out)
    print(f"OK: campaign {manifest.get('campaign_id')}, "
          f"{len(samples)} samples -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
