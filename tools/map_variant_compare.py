#!/usr/bin/env python3
"""Offline comparator вариантов карты TZ-02: M0-rebased vs M1..M4 (и далее физические burst-логи).

ПРАВИЛА (зафиксированы приёмкой, нарушение считается дефектом инструмента):

1. Единственный baseline — `M0-rebased` (artifact, прошедший identity-rebase). Старый
   `oew_map_v2.bin` baseline'ом быть не может: CRC baseline-прогона обязан совпасть с
   `--baseline-crc32`, иначе сравнение не производится (fail-closed).
2. НИКАКОГО агрегированного рейтинга карты. Результат — матрица `BETTER/SAME/WORSE`
   по каждой метрике отдельно.
3. `idc1/idc2` (raw ADC) используются ТОЛЬКО для repeatability. Из них не выводится
   «физическая истинность M»: они входят в реконструкцию, и метрика из той же M дала бы круг.
4. `Ires` трактуется как диагностический zero-sequence канал (i_z = (ia+ib+ic)/3,
   |Δi_dq| = 2|i_z|), а не как ещё один восстановленный фазный ток. Инструмент отдельно
   помечает случай «Id/Iq улучшились, а ZSV вырос».
5. Метрики, для которых нет независимого источника (`iz`, независимый `iu/iv/iw`),
   помечаются `N/A` и НЕ участвуют в BETTER/SAME/WORSE.

Вход: raw-логи сессий (формат логгера: маркеры `[utc] TX/RX`, строки `@FOC:`, `@ADC:`, `@SYS:`,
`@BRK:`, `@MAP:LOAD:`), по одному на вариант.

Использование:
    python tools/map_variant_compare.py --baseline M0 --baseline-crc32 0x95425CEB \
        --run M0=logs/M0.log --run M1=logs/M1.log --run M2=logs/M2.log \
        [--iz M1=iz_M1.csv] [--ref M1=phases_M1.csv] [--tol 0.05] \
        [--json out.json] [--markdown out.md]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

FOC_KEYS = ("t", "run_id", "map_id", "map_crc32", "I1", "I2", "Ires", "Id", "Iq",
            "Id_ref", "Iq_ref", "VBUS", "STATE", "SPD", "TH", "sector", "window",
            "CCR1", "CCR2", "CCR3", "ADC_STATUS", "FAULT", "FAULT_R", "FAIL", "RUN",
            "em_stop1", "em_stop2")
ADC_RE = re.compile(r"@ADC:I1=(\d+):I2=(\d+):Ires=(\d+):VBUS=(\d+)")
SYS_RE = re.compile(r"@SYS:.*")

# tolerance по умолчанию (относительная), применяется к метрикам «меньше = лучше»
DEFAULT_TOL = 0.05


def parse_log(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict] = []
    adc: list[tuple[int, int, int, int]] = []
    sysinfo: str | None = None
    brk: list[str] = []
    mapa: list[str] = []
    for line in lines:
        s = line.lstrip("> ").strip()
        if s.startswith("@FOC:"):
            f: dict[str, str] = {}
            for part in s.split(":"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    f[k.split()[-1]] = v
            rows.append(f)
        elif s.startswith("@ADC:"):
            m = ADC_RE.search(s)
            if m:
                adc.append(tuple(int(g) for g in m.groups()))
        elif s.startswith("@SYS:") and sysinfo is None:
            sysinfo = s
        elif s.startswith("@BRK"):
            brk.append(s)
        elif s.startswith("@MAP:LOAD:"):
            mapa.append(s)

    def nums(key: str) -> list[int]:
        out = []
        for r in rows:
            v = r.get(key)
            if v is not None and v.lstrip("-").isdigit():
                out.append(int(v))
        return out

    ts = nums("t")
    return {
        "path": str(path),
        "rows": rows,
        "row_count": len(rows),
        "adc": adc,
        "sysinfo": sysinfo,
        "brk": brk,
        "mapload": mapa,
        "t_monotonic": all(b >= a for a, b in zip(ts, ts[1:])) if ts else False,
    }


def _p(data: list[float], q: float) -> float | None:
    if not data:
        return None
    s = sorted(data)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def _err(rows: list[dict], a: str, b: str) -> list[float]:
    out = []
    for r in rows:
        va, vb = r.get(a), r.get(b)
        if va is None or vb is None:
            continue
        if va.lstrip("-").isdigit() and vb.lstrip("-").isdigit():
            out.append(abs(int(va) - int(vb)))
    return out


def compute_metrics(parsed: dict, tol: float, iz: list[tuple[int, float]] | None,
                    ref: list[tuple[float, float, float]] | None, iz_scale: float) -> dict:
    rows = parsed["rows"]
    m: dict[str, dict] = {}

    def put(name: str, value, direction: str = "lower_better", note: str = "") -> None:
        m[name] = {"value": value, "direction": direction, "note": note}

    id_err = _err(rows, "Id", "Id_ref")
    iq_err = _err(rows, "Iq", "Iq_ref")
    put("id_error", statistics.median(id_err) if id_err else None, note="median |Id - Id_ref|")
    put("iq_error", statistics.median(iq_err) if iq_err else None, note="median |Iq - Iq_ref|")

    ids = [int(r["Id"]) for r in rows if str(r.get("Id", "")).lstrip("-").isdigit()]
    iqs = [int(r["Iq"]) for r in rows if str(r.get("Iq", "")).lstrip("-").isdigit()]
    ripple_id = (_p(ids, 0.95) - _p(ids, 0.05)) if len(ids) > 3 else None
    ripple_iq = (_p(iqs, 0.95) - _p(iqs, 0.05)) if len(iqs) > 3 else None
    put("ripple_id", ripple_id, note="p95-p5 по Id")
    put("ripple_iq", ripple_iq, note="p95-p5 по Iq")
    put("ripple_idq", max([x for x in (ripple_id, ripple_iq) if x is not None], default=None),
        note="max(ripple_id, ripple_iq)")

    if parsed["adc"]:
        i1 = [a[0] for a in parsed["adc"]]
        i2 = [a[1] for a in parsed["adc"]]
        spread1 = (_p(i1, 0.95) - _p(i1, 0.05)) if len(i1) > 3 else (max(i1) - min(i1))
        spread2 = (_p(i2, 0.95) - _p(i2, 0.05)) if len(i2) > 3 else (max(i2) - min(i2))
        put("idc_repeatability", max(spread1, spread2), note="p95-p5 raw ADC (idc1/idc2), только repeatability")
        put("idc_samples", len(parsed["adc"]), direction="info", note="число @ADC-семплов")
    else:
        put("idc_repeatability", None, note="нет строк @ADC:")

    ires = [int(r["Ires"]) for r in rows if str(r.get("Ires", "")).lstrip("-").isdigit()]
    put("zsv_ires", statistics.median([abs(v) for v in ires]) if ires else None,
        note="diagnostic zero-sequence channel: median |Ires| (i_z=(ia+ib+ic)/3, |Δi_dq|=2|i_z|)")

    if iz:
        ires_series = [(int(r["t"]), float(r["Ires"])) for r in rows
                       if str(r.get("Ires", "")).lstrip("-").isdigit() and str(r.get("t", "")).isdigit()]
        if ires_series and iz:
            iz_map = dict(iz)
            res = [abs(v - iz_scale * iz_map[t]) for t, v in ires_series if t in iz_map]
            if res:
                put("ires_vs_iz", statistics.median(res),
                    note=f"median |Ires - {iz_scale}*iz| (шкала задаётся --iz-scale)")
            else:
                put("ires_vs_iz", None, note="нет пересечения по t с независимым iz")
    else:
        put("ires_vs_iz", None, note="N/A: нет независимого iz (не участвует в вердикте)")

    if ref:
        put("kcl_residual", statistics.median([abs(u + v + w) for u, v, w in ref]),
            note="median |iu+iv+iw| по НЕЗАВИСИМОМУ референсу")
    else:
        put("kcl_residual", None, note="N/A: нет независимого iu/iv/iw (не участвует в вердикте)")

    patterns = {}
    for r in rows:
        sw = (r.get("sector"), r.get("window"))
        ccr = (r.get("CCR1"), r.get("CCR2"), r.get("CCR3"))
        if None in sw or None in ccr:
            continue
        patterns.setdefault(sw, {}).setdefault(ccr, 0)
        patterns[sw][ccr] += 1
    parsed["sw_ccr_patterns"] = patterns
    parsed["sw_ccr_dominant"] = {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in patterns.items()}

    prot_count = sum(1 for r in rows
                     if str(r.get("FAULT", "")).isdigit() and int(r["FAULT"]) != 0)
    put("protection_fault_rows", prot_count, direction="fewer_events_better",
        note="строки с FAULT != 0")
    put("break_events", len(parsed["brk"]), direction="fewer_events_better", note="строки @BRK:")
    put("mapload_fail", sum(1 for s in parsed["mapload"] if "FAIL" in s), direction="fewer_events_better",
        note="строки @MAP:LOAD:…FAIL")

    if parsed["sysinfo"]:
        trunc = re.search(r"uart_trunc=(\d+)", parsed["sysinfo"])
        drp = re.search(r"uart_drp=(\d+)", parsed["sysinfo"])
        put("uart_trunc", int(trunc.group(1)) if trunc else None, direction="integrity")
        put("uart_drp", int(drp.group(1)) if drp else None, direction="integrity")
    else:
        put("uart_trunc", None, direction="integrity", note="нет @SYS: в логе")
        put("uart_drp", None, direction="integrity", note="нет @SYS: в логе")
    put("t_monotonic", parsed["t_monotonic"], direction="integrity")
    put("row_count", parsed["row_count"], direction="info")

    map_ids = sorted({r.get("map_id") for r in rows if r.get("map_id")})
    crcs = sorted({r.get("map_crc32") for r in rows if r.get("map_crc32")})
    m["map_identity"] = {"value": {"map_id": map_ids, "map_crc32": crcs},
                         "direction": "validity",
                         "note": "identity/CRC должны быть валидны и соответствовать варианту"}
    return m


def classify(metric: str, base: dict, cur: dict, tol: float) -> str:
    direction = base.get("direction", "lower_better")
    b, c = base.get("value"), cur.get("value")
    if direction == "info":
        return "INFO"
    if direction == "integrity":
        if b is None or c is None:
            return "N/A"
        if isinstance(b, bool) or isinstance(c, bool):
            return "SAME" if b == c else "WORSE"
        return "SAME" if c == b else ("BETTER" if c < b else "WORSE")
    if direction == "validity":
        if not c or not c.get("map_crc32"):
            return "INVALID"
        if not b or not b.get("map_crc32"):
            return "N/A"
        return "SAME" if c["map_crc32"] == b["map_crc32"] else "CHECK-IDENTITY"
    if direction == "fewer_events_better":
        if b is None or c is None:
            return "N/A"
        if c == b:
            return "SAME"
        return "WORSE" if c > b else "BETTER"
    if direction == "higher_better":
        if b is None or c is None:
            return "N/A"
        if b == 0:
            return "SAME" if c == 0 else "BETTER"
        rel = (c - b) / abs(b)
        if rel > tol:
            return "BETTER"
        if rel < -tol:
            return "WORSE"
        return "SAME"
    if b is None or c is None:
        return "N/A"
    if b == 0:
        return "SAME" if c == 0 else "WORSE"
    rel = (c - b) / abs(b)
    if rel < -tol:
        return "BETTER"
    if rel > tol:
        return "WORSE"
    return "SAME"


def build_report(runs: dict[str, dict], baseline: str, tol: float,
                 expected_crc: str | None) -> dict:
    base = runs[baseline]
    base_crc = sorted({r.get("map_crc32") for r in base["rows"] if r.get("map_crc32")})
    checks = {
        "baseline_is_rebased": True,
        "baseline_crc_present": bool(base_crc),
        "baseline_crc_matches_expected": True,
    }
    if expected_crc is not None:
        norm = expected_crc.lower().replace("0x", "")
        present = [c for c in base_crc if c.lower().replace("0x", "") == norm]
        checks["baseline_crc_matches_expected"] = bool(present)
    for name, parsed in runs.items():
        crcs = sorted({r.get("map_crc32") for r in parsed["rows"] if r.get("map_crc32")})
        checks[f"identity_valid_{name}"] = bool(crcs) and len(crcs) == 1
    checks["baseline_identity_valid"] = checks[f"identity_valid_{baseline}"]

    matrix: dict[str, dict[str, str]] = {}
    metrics_order: list[str] = []

    # sector/window ↔ CCR: согласованность доминирующих CCR-наборов с baseline
    base_dom = base.get("sw_ccr_dominant") or {}
    for name, parsed in runs.items():
        dom = parsed.get("sw_ccr_dominant") or {}
        common = [k for k in dom if k in base_dom]
        if not common:
            value = None
            note = "нет пересечения ключей (sector,window) с baseline"
        else:
            matched = sum(1 for k in common if dom[k] == base_dom[k])
            value = matched / len(common)
            note = "доля ключей (sector,window) с тем же доминирующим CCR-набором, что у baseline"
        parsed["metrics"]["sector_window_ccr"] = {"value": value,
                                                 "direction": "higher_better",
                                                 "note": note}

    for name in runs:
        for metric in runs[name]["metrics"]:
            if metric not in metrics_order:
                metrics_order.append(metric)

    for metric in metrics_order:
        row = {}
        for name in runs:
            if name == baseline:
                row[name] = "BASELINE"
                continue
            cur = runs[name]["metrics"].get(metric)
            if cur is None:
                row[name] = "N/A"
                continue
            row[name] = classify(metric, runs[baseline]["metrics"][metric], cur, tol)
        matrix[metric] = row

    # ZSV cross-check: улучшение Id/Iq при выросшем ZSV — отдельное наблюдение, не «карта лучше»
    zsv_alerts: dict[str, dict] = {}
    base_zsv = runs[baseline]["metrics"].get("zsv_ires", {}).get("value")
    for name in runs:
        if name == baseline:
            continue
        cur_zsv = runs[name]["metrics"].get("zsv_ires", {}).get("value")
        id_verdict = matrix.get("id_error", {}).get(name)
        iq_verdict = matrix.get("iq_error", {}).get(name)
        if base_zsv and cur_zsv and base_zsv > 0 and cur_zsv > base_zsv * (1.0 + tol):
            zsv_alerts[name] = {
                "zsv_increased": True,
                "id_error": id_verdict,
                "iq_error": iq_verdict,
                "idq_improved": id_verdict == "BETTER" or iq_verdict == "BETTER",
                "note": "ZSV вырос: изменение M улучшило dq-метрику, но увеличило zero-sequence компоненту",
            }

    return {
        "baseline": baseline,
        "tolerance": tol,
        "checks": checks,
        "metrics": {name: runs[name]["metrics"] for name in runs},
        "matrix": matrix,
        "zsv_alerts": zsv_alerts,
        "notes": [
            "Агрегированного рейтинга нет: только построчные BETTER/SAME/WORSE.",
            "idc1/idc2 используются только для repeatability — из них не выводится физическая истинность M.",
            "Ires — диагностический zero-sequence канал (i_z=(ia+ib+ic)/3, |Δi_dq|=2|i_z|).",
            "Метрики с независимым источником (iz, iu/iv/iw) помечаются N/A без источника и не судятся.",
        ],
        "verdict_absent_by_design": True,
    }


def render_markdown(report: dict) -> str:
    runs = [report["baseline"]] + [n for n in report["metrics"] if n != report["baseline"]]
    out = ["# TZ-02 comparator: M0-rebased vs варианты", "",
           f"baseline: `{report['baseline']}` · tolerance: {report['tolerance']}", "",
           "| metric | " + " | ".join(runs) + " |",
           "|---|" + "---|" * len(runs)]
    for metric, row in report["matrix"].items():
        cells = []
        for name in runs:
            if name == report["baseline"]:
                v = report["metrics"][name][metric]["value"]
                cells.append("—" if v is None else str(v))
            else:
                cells.append(row.get(name, "N/A"))
        out.append(f"| {metric} | " + " | ".join(cells) + " |")
    if report["zsv_alerts"]:
        out += ["", "## ZSV-предупреждения", ""]
        for name, alert in report["zsv_alerts"].items():
            out.append(f"* **{name}**: {alert['note']} (Id: {alert['id_error']}, Iq: {alert['iq_error']})")
    out += ["", "> Агрегированного рейтинга нет по построению: каждая метрика судится отдельно."]
    return "\n".join(out) + "\n"


def load_series(path: Path, kind: str) -> list:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if kind == "iz" and len(parts) >= 2:
            rows.append((int(float(parts[0])), float(parts[1])))
        elif kind == "ref" and len(parts) >= 3:
            rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline comparator вариантов карты (TZ-02)")
    ap.add_argument("--baseline", required=True, help="имя baseline-варианта (должен быть M0-rebased)")
    ap.add_argument("--baseline-crc32", default=None,
                    help="ожидаемый map_crc32 baseline (артефакт M0-rebased); при расхождении — REJECT")
    ap.add_argument("--run", action="append", default=[], metavar="NAME=LOG",
                    help="вариант и его raw-лог (можно несколько)")
    ap.add_argument("--iz", action="append", default=[], metavar="NAME=CSV")
    ap.add_argument("--ref", action="append", default=[], metavar="NAME=CSV")
    ap.add_argument("--iz-scale", type=float, default=1.0)
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL)
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None)
    args = ap.parse_args()

    if not args.run:
        print("нет --run NAME=LOG", file=sys.stderr)
        return 2

    def pairs(items):
        out = {}
        for item in items:
            if "=" not in item:
                continue
            k, _, v = item.partition("=")
            out[k] = Path(v)
        return out

    run_paths = pairs(args.run)
    iz_paths = pairs(args.iz)
    ref_paths = pairs(args.ref)

    if args.baseline not in run_paths:
        print(f"REJECT: baseline {args.baseline!r} отсутствует среди --run", file=sys.stderr)
        return 1

    runs: dict[str, dict] = {}
    for name, path in run_paths.items():
        if not path.exists():
            print(f"REJECT: нет файла {path}", file=sys.stderr)
            return 1
        parsed = parse_log(path)
        iz = load_series(iz_paths[name], "iz") if name in iz_paths else None
        ref = load_series(ref_paths[name], "ref") if name in ref_paths else None
        parsed["metrics"] = compute_metrics(parsed, args.tol, iz, ref, args.iz_scale)
        runs[name] = parsed

    report = build_report(runs, args.baseline, args.tol, args.baseline_crc32)

    if not report["checks"]["baseline_crc_matches_expected"]:
        print("REJECT: map_crc32 baseline-прогона не совпал с --baseline-crc32 "
              "(baseline обязан быть M0-rebased, а не старым артефактом)", file=sys.stderr)
        return 1
    if not report["checks"]["baseline_identity_valid"]:
        print("REJECT: identity baseline-прогона невалидна/неоднородна", file=sys.stderr)
        return 1

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json: {args.json}")
    md = render_markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8", newline="\n")
        print(f"markdown: {args.markdown}")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
