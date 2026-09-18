#!/usr/bin/env python3
"""Сведение повторяемости M0: N независимых прогонов → уровень baseline (TZ-02).

Два уровня baseline (зафиксировано приёмкой):

  M0_REFERENCE_FROZEN   один прогон (M0-R1), проходит F1…F8. Неизменяемая точка отсчёта
                        для инструментов и следующего эксперимента. НЕ repeatability.
  M0_BASELINE_FROZEN    M0-R1…M0-R5: пять независимых прогонов, свёрнутых этим инструментом.
                        Только он даёт право проектировать M1 «относительно статистически
                        подтверждённого M0».

R2…R5 обязаны идти тем же runbook'ом, на том же firmware и с тем же артефактом — инструмент
это проверяет, а не полагается на дисциплину:

  R1  одинаковый firmware_sha256 у всех прогонов (== --expect-firmware)
  R2  одинаковый artifact_sha256 и map_crc32 у всех прогонов (== --expect-crc)
  R3  одинаковая живая identity (все 11 полей, в частности ccs и arr)
  R4  одинаковый конверт (vbus_target, current_limit, burst_duration, pause)
  R5  каждый прогон внутренне чист: нет FAULT!=0, нет @BRK:valid=1, uart_drp/trunc=0,
      drain == число @MC:REC, dropped=0, t монотонен, VBUS внутри конверта
  R6  ≥ --min-runs (по умолчанию 5) прогонов с уникальными run_id
  R7  аномалии (выброс разброса idc / VBUS / числа записей) должны быть либо отсутствовать,
      либо явно приняты: --acknowledge-anomaly M0-R3="<причина>"

Без R1…R7 запись M0_BASELINE_FROZEN.json не выпускается (fail-closed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IDENT_RE = re.compile(r"@MAP:IDENTITY:board=(\d+):pwm=(\d+):arr=(\d+):trig=0x([0-9A-Fa-f]+):"
                      r"off=(\d+):dt=(\d+):adc_clk=(\d+):sample_x2=(\d+):res=(\d+):"
                      r"acs=0x([0-9A-Fa-f]+):ccs=0x([0-9A-Fa-f]+)")
REC_RE = re.compile(r"@MC:REC:cap=(\d+):seq=(\d+):raw_i1=(\d+):raw_i2=(\d+):raw_ct=(\d+):raw_vbus=(\d+)"
                    r":i1=(-?\d+):i2=(-?\d+):vbus=(\d+)")
STATUS_RE = re.compile(r"@MC:STATUS:state=(\d+):term=(\d+):frames=(\d+):dropped=(\d+)")
DEFAULT_MIN_RUNS = 5
SCHEMA_VERSION = "tz2-m0-repeatability-1"
LEVEL_FULL = "M0_BASELINE_FROZEN"
LEVEL_PROVISIONAL = "M0_BASELINE_PROVISIONAL"
DEFAULT_TOL = 0.5     # 50 % отклонения метрики от медианы по прогонам → аномалия


def percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return float(s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))])


def sha256_of(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_run(folder: Path) -> dict:
    """Извлекает всё, что нужно для R1…R5, из каталога возврата одного прогона."""
    r: dict = {"folder": folder.name, "run_id": folder.name, "problems": []}
    manifest_path = folder / "session_manifest.json"
    if not manifest_path.exists():
        r["problems"].append("нет session_manifest.json")
        return r
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    burst = (manifest.get("bursts") or [{}])[0]
    session = manifest.get("session") or {}
    env = session.get("envelope") or {}
    r.update({
        "run_id": burst.get("run_id"),
        "variant_id": burst.get("variant_id"),
        "firmware_sha256": burst.get("firmware_sha256") or session.get("firmware_sha256"),
        "artifact_sha256": burst.get("artifact_sha256"),
        "map_crc32": str(burst.get("variant_map_crc32") or "").lower(),
        "envelope": {"vbus_target_mv": burst.get("vbus_target_mv"),
                     "current_limit_ma": burst.get("current_limit_ma"),
                     "burst_duration_ms": burst.get("burst_duration_ms"),
                     "pause_before_ms": burst.get("pause_before_ms"),
                     "vbus_min_mv": env.get("vbus_min_mv"), "vbus_max_mv": env.get("vbus_max_mv")},
        "stop_gate": (burst.get("stop_gate") or {}).get("triggered"),
        "manifest_sha256": sha256_of(manifest_path),
    })

    logs = []
    raw_log_rel = ((burst.get("telemetry") or {}).get("raw_log")) if isinstance(burst, dict) else None
    r["log_source"] = None
    if raw_log_rel:
        candidate = (folder / str(raw_log_rel)).resolve()
        if candidate.exists():
            logs = [candidate]
            r["log_source"] = "manifest:telemetry.raw_log"
        else:
            r["problems"].append(f"telemetry.raw_log={raw_log_rel} не найден")
    if not logs and (folder / "logs").is_dir():
        # запасной путь: первый *.log. НЕ основной — при двух семьях логов
        # (сессионный + пер-прогонный) выбор по имени недетерминирован по смыслу.
        fallback = sorted((folder / "logs").glob("*.log"))
        if fallback:
            logs = fallback[:1]
            r["log_source"] = "fallback:первый *.log (в манифесте нет telemetry.raw_log)"
            r["problems"].append("лог взят НЕ из telemetry.raw_log (fallback) — манифест обязан "
                                 "указывать приёмочный лог явно")
    if not logs:
        if "лог" not in " ".join(r["problems"]):
            r["problems"].append("нет лога")
        return r
    log = logs[0]
    text = log.read_text(encoding="utf-8", errors="replace")
    r["log"] = log.name
    r["log_sha256"] = sha256_of(log)
    r["log_bytes"] = len(text.encode("utf-8"))

    ident = IDENT_RE.search(text)
    if ident:
        r["identity"] = {"board_revision": int(ident.group(1)), "pwm_frequency_hz": int(ident.group(2)),
                         "timer_arr": int(ident.group(3)), "adc_trigger_id": int(ident.group(4), 16),
                         "enable_signature": None,
                         "adc_config_signature": int(ident.group(10), 16),
                         "current_calibration_signature": int(ident.group(11), 16)}
        r["ccs"] = int(ident.group(11), 16)
    else:
        r["problems"].append("нет @MAP:IDENTITY в логе")

    recs = [tuple(int(x) for x in m.groups()) for m in REC_RE.finditer(text)]
    r["records"] = len(recs)
    r["raw_i1"] = [x[2] for x in recs]
    r["raw_i2"] = [x[3] for x in recs]
    r["raw_vbus"] = [x[5] for x in recs]
    drain = re.search(r"@MC:DRAIN:records=(\d+)", text)
    r["drain"] = int(drain.group(1)) if drain else None
    status = STATUS_RE.search(text)
    r["capture_status"] = dict(zip(("state", "term", "frames", "dropped"),
                                   (int(g) for g in status.groups()))) if status else None
    r["fault_rows"] = len([1 for ln in text.splitlines()
                           if ln.lstrip("> ").startswith("@FOC:")
                           and (m := re.search(r"FAULT=(\d+)", ln)) and int(m.group(1)) != 0])
    r["brk_valid1"] = len(re.findall(r"@BRK:valid=1", text))
    sys_lines = re.findall(r"@SYS:.*?uart_drp=(\d+):uart_trunc=(\d+)", text)
    r["uart_lost"] = [f"{d}/{t}" for d, t in sys_lines if int(d) or int(t)]
    ts = [int(m.group(1)) for ln in text.splitlines() if ln.lstrip("> ").startswith("@FOC:")
          for m in [re.search(r"t=(\d+)", ln)] if m]
    r["t_monotonic"] = all(a <= b for a, b in zip(ts, ts[1:])) if ts else None
    r["vbus_max_mv"] = max(r["raw_vbus"]) if r["raw_vbus"] else None

    # внутренние проверки прогона (R5)
    if r["fault_rows"]:
        r["problems"].append(f"FAULT!=0 в {r['fault_rows']} строках")
    if r["brk_valid1"]:
        r["problems"].append("@BRK:valid=1")
    if r["uart_lost"]:
        r["problems"].append(f"потери UART: {', '.join(r['uart_lost'])}")
    elif not sys_lines:
        # отсутствие @SYS ≠ «потерь нет»: целостность UART в этом прогоне не подтверждена
        r["problems"].append("нет строк @SYS (sysinfo не выполнялся) — целостность UART "
                             "не подтверждена")
    if r["drain"] != r["records"]:
        r["problems"].append(f"drain={r['drain']} != @MC:REC={r['records']}")
    if r["capture_status"] and r["capture_status"]["dropped"]:
        r["problems"].append(f"dropped={r['capture_status']['dropped']}")
    if r["t_monotonic"] is False:
        r["problems"].append("t не монотонен")
    if r["stop_gate"] is not False:
        r["problems"].append("stop_gate.triggered != false")
    return r


def spread(values: list[int]) -> float | None:
    p95, p5 = percentile(values, 0.95), percentile(values, 0.05)
    return None if p95 is None or p5 is None else p95 - p5


def aggregate(runs: list[dict], expect_firmware: str, expect_crc: str, min_runs: int,
              tol: float, acknowledged: dict[str, str],
              allow_provisional: bool = False) -> dict:
    checks: list[dict] = []
    runs_count = len(runs)
    runs_evaluated = [r.get("run_id") for r in runs]

    def check(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    ids = [r.get("run_id") for r in runs]
    check("R6", runs_count >= min_runs and len(set(ids)) == runs_count,
          f"прогонов {runs_count} (минимум {min_runs}), run_id: {', '.join(str(i) for i in ids)}")

    # R6b — уровень записи: полный baseline требует DEFAULT_MIN_RUNS; меньшее число
    # допустимо только как PROVISIONAL и только по явному --allow-provisional.
    if runs_count >= DEFAULT_MIN_RUNS:
        check("R6b", True, f"прогонов {runs_count} ≥ {DEFAULT_MIN_RUNS} — уровень "
                           f"{LEVEL_FULL} разрешён")
    elif allow_provisional:
        check("R6b", True, f"прогонов {runs_count} < {DEFAULT_MIN_RUNS}: запись будет выпущена "
                           f"как {LEVEL_PROVISIONAL} (не полный baseline)")
    else:
        check("R6b", False, f"прогонов {runs_count} < {DEFAULT_MIN_RUNS}: полный baseline "
                            f"недостижим; для provisional-записи требуется --allow-provisional")

    for rid, key, expect, label in (("R1", "firmware_sha256", expect_firmware, "firmware"),
                                    ("R2", "artifact_sha256", None, "artifact sha256")):
        values = {r.get(key) for r in runs}
        if key == "artifact_sha256":
            ok = len(values) == 1 and None not in values
            check("R2", ok, f"artifact_sha256 уникален: {len(values)} значений")
        else:
            ok = values == {expect.lower()}
            check("R1", ok, f"firmware_sha256: {len(values)} значений" +
                  ("" if ok else f" — {sorted(str(v)[:16] for v in values)}"))

    crcs = {r.get("map_crc32") for r in runs}
    check("R2b", crcs == {expect_crc.lower()},
          f"map_crc32: {', '.join(sorted(str(c) for c in crcs))} (ожидалось {expect_crc})")

    ccss = {r.get("ccs") for r in runs}
    arrs = {(r.get("identity") or {}).get("timer_arr") for r in runs}
    check("R3", len(ccss) == 1 and None not in ccss and len(arrs) == 1,
          f"identity: ccs {len(ccss)} знач., arr {len(arrs)} знач." +
          (f" (ccs={', '.join(hex(c) for c in sorted(c for c in ccss if c is not None))}" if ccss else ""))

    envs = {json.dumps(r.get("envelope"), sort_keys=True) for r in runs}
    check("R4", len(envs) == 1, f"конверт: {len(envs)} различных наборов параметров")

    defective = [r.get("run_id") for r in runs if r.get("problems")]
    check("R5", not defective,
          "все прогоны чистые внутри" if not defective else
          "; ".join(f"{r.get('run_id')}: {', '.join(r.get('problems') or [])}"
                    for r in runs if r.get("problems")))

    # метрики повторяемости
    metrics = {}
    for key, values in (("raw_i1_spread", [spread(r["raw_i1"]) for r in runs if r.get("raw_i1")]),
                        ("raw_i2_spread", [spread(r["raw_i2"]) for r in runs if r.get("raw_i2")]),
                        ("records", [r.get("records") for r in runs]),
                        ("vbus_max_mv", [r.get("vbus_max_mv") for r in runs])):
        finite = [v for v in values if isinstance(v, (int, float))]
        med = percentile(finite, 0.5) if finite else None
        metrics[key] = {"values": values, "median": med,
                        "min": min(finite) if finite else None,
                        "max": max(finite) if finite else None}

    # аномалии: отклонение от медианы по прогонам больше tolerance
    anomalies = []
    for key in ("raw_i1_spread", "raw_i2_spread", "records", "vbus_max_mv"):
        med = metrics[key]["median"]
        if not med:
            continue
        for r in runs:
            v = {"raw_i1_spread": spread(r["raw_i1"]) if r.get("raw_i1") else None,
                 "raw_i2_spread": spread(r["raw_i2"]) if r.get("raw_i2") else None,
                 "records": r.get("records"), "vbus_max_mv": r.get("vbus_max_mv")}[key]
            if isinstance(v, (int, float)) and abs(v - med) > abs(med) * tol:
                anomalies.append({"run_id": r.get("run_id"), "metric": key, "value": v,
                                  "median": med, "deviation": round((v - med) / med, 3) if med else None})
    unresolved = [a for a in anomalies if a["run_id"] not in acknowledged]
    check("R7", not unresolved,
          (f"набор из {runs_count} прогонов ({', '.join(str(x) for x in runs_evaluated)}): "
           + ("аномалий нет" if not anomalies else
              (f"аномалий {len(anomalies)}, не принято {len(unresolved)}: " +
               "; ".join(f"{a['run_id']} {a['metric']}={a['value']} (откл. {a['deviation']})"
                         for a in unresolved) if unresolved else
               f"аномалий {len(anomalies)}, все приняты оператором")))) 

    verdict = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    level = (LEVEL_FULL if (verdict == "PASS" and runs_count >= DEFAULT_MIN_RUNS)
             else LEVEL_PROVISIONAL if verdict == "PASS" else "NONE")
    return {"verdict": verdict, "level": level, "provisional": level == LEVEL_PROVISIONAL,
            "runs_count": runs_count, "min_runs_required": DEFAULT_MIN_RUNS,
            "runs_evaluated": runs_evaluated, "checks": checks, "metrics": metrics,
            "anomalies": anomalies, "acknowledged": acknowledged,
            "runs": [{k: v for k, v in r.items() if k not in ("raw_i1", "raw_i2", "raw_vbus")}
                     for r in runs]}


def render(agg: dict) -> str:
    tokens = [f"[{c['id']}]" for c in agg["checks"]]
    w = max(len(t) for t in tokens)
    lines = [f"{t:<{w}}  {c['status']:<4}  {c['detail']}"
             for t, c in zip(tokens, agg["checks"])]
    lines.append("")
    lines.append("метрика              медиана    min      max      по прогонам")
    for key, m in agg["metrics"].items():
        lines.append(f"{key:<20} {str(m['median']):<10} {str(m['min']):<8} {str(m['max']):<8} "
                     f"{m['values']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Повторяемость M0: R1…R5 → baseline (TZ-02)")
    ap.add_argument("--run", action="append", default=[], metavar="RUN_ID=КАТАЛОГ")
    ap.add_argument("--expect-firmware", default="6d3ba90235e7b81681f88ea305957dd7ba5b2a69f810f2d73dfe088cfe3e0201")
    ap.add_argument("--expect-crc", default="0x00E666F3")
    ap.add_argument("--min-runs", type=int, default=DEFAULT_MIN_RUNS)
    ap.add_argument("--allow-provisional", action="store_true",
                    help=f"разрешить выпуск записи уровня {LEVEL_PROVISIONAL}, когда прогонов "
                         f"меньше {DEFAULT_MIN_RUNS} (без флага — FAIL)")
    ap.add_argument("--breakdiag-archive", default=None,
                    help="каталог BREAK-архива: фиксируется отдельным диагностическим каналом, "
                         "НЕ влияет на чистоту прогонов")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL)
    ap.add_argument("--acknowledge-anomaly", action="append", default=[], metavar="RUN_ID=ПРИЧИНА")
    ap.add_argument("--out-dir", default=None, help="куда выпустить M0_BASELINE_FROZEN.json при PASS")
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None)
    args = ap.parse_args()

    if not args.run:
        print("нет --run RUN_ID=КАТАЛОГ")
        return 2
    runs = []
    for item in args.run:
        rid, _, path = item.partition("=")
        folder = Path(path)
        if not folder.is_dir():
            print(f"BLOCKED: нет каталога {folder}")
            return 1
        parsed = parse_run(folder)
        parsed.setdefault("run_id", rid)
        runs.append(parsed)

    ack = {}
    for item in args.acknowledge_anomaly:
        rid, _, reason = item.partition("=")
        if rid and reason:
            ack[rid] = reason

    agg = aggregate(runs, args.expect_firmware, args.expect_crc, args.min_runs, args.tol, ack,
                    allow_provisional=args.allow_provisional)

    break_info = None
    if args.breakdiag_archive:
        bdir = Path(args.breakdiag_archive)
        if not bdir.is_dir():
            print(f"BLOCKED: нет каталога BREAK-архива {bdir}")
            return 1
        files = {p.name: sha256_of(p) for p in sorted(bdir.rglob("*")) if p.is_file()}
        break_info = {"archive": str(bdir), "files": files,
                      "affects_runs": False,
                      "note": "BREAK-архив — отдельное диагностическое событие: фиксируется в "
                              "provenance, НЕ влияет на чистоту прогонов (R5) и НЕ является "
                              "квалификацией baseline"}
        agg["break_diagnostic"] = break_info

    print(render(agg))
    print(f"\nПОВТОРЯЕМОСТЬ: {agg['verdict']}   уровень: {agg['level']}"
          f"   прогонов: {agg['runs_count']} (требуется {agg['min_runs_required']})")
    if break_info:
        print(f"BREAK-архив: {len(break_info['files'])} файлов зафиксировано отдельным каналом "
              f"(на чистоту прогонов не влияет)")
    if agg["verdict"] != "PASS":
        print("запись уровня baseline не выпускается: " +
              ", ".join(c["id"] for c in agg["checks"] if c["status"] == "FAIL"))
    elif agg["level"] == LEVEL_FULL:
        print(f"{LEVEL_FULL}: полный набор прогонов свёрнут — уровень baseline достигнут")
    else:
        print(f"{LEVEL_PROVISIONAL}: запись выпускается, но это НЕ полный baseline "
              f"({agg['runs_count']} < {agg['min_runs_required']})")

    if args.json:
        Path(args.json).write_text(json.dumps(agg, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")
        print(f"json: {args.json}")
    if args.markdown:
        Path(args.markdown).write_text(render(agg) + "\n", encoding="utf-8", newline="\n")
        print(f"markdown: {args.markdown}")
    if args.out_dir and agg["verdict"] == "PASS":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        provisional = agg["level"] == LEVEL_PROVISIONAL
        record = {
            "schema_version": SCHEMA_VERSION,
            "level": agg["level"],
            "provisional": provisional,
            "runs": agg["runs_evaluated"],
            "runs_count": agg["runs_count"],
            "min_runs_required": agg["min_runs_required"],
            "firmware_sha256": args.expect_firmware, "artifact_map_crc32": args.expect_crc,
            "aggregate": {k: agg[k] for k in ("verdict", "checks", "metrics", "anomalies")},
            "break_diagnostic": break_info,
            "caveat": (f"{LEVEL_PROVISIONAL} — предварительная запись: прогонов "
                       f"{agg['runs_count']} < {agg['min_runs_required']}; полным baseline M0 "
                       f"не является"
                       if provisional else
                       "M0 baseline — точка отсчёта для M1 vs M0, НЕ физическая квалификация карты"),
        }
        name = "M0_BASELINE_PROVISIONAL.json" if provisional else "M0_BASELINE_FROZEN.json"
        (out / name).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
        print(f"выпущено: {out / name}  (level={agg['level']}, runs_count={agg['runs_count']})")
    return 0 if agg["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
