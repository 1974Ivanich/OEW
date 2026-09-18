#!/usr/bin/env python3
"""Заморозка baseline после первого M0 burst (TZ-02, шаг c).

Цепочка зафиксирована приёмкой:

    raw UART → manifest completion → offline verify → map_variant_compare.py
             → independent audit → M0 baseline frozen → только затем проектирование M1

Инструмент выполняет последний шаг: проверяет целостность всей цепочки и выпускает
`M0_BASELINE_FROZEN.json` (запись с пинами) + пишет RETURN_SHA256.txt последним файлом.
Fail-closed: без PASS по каждому пункту запись не выпускается, M1 не проектируется.

Проверки (ID в отчёте):
  F1  манифест валиден, baseline burst определён
  F2  gate G0..G9 дал вердикт PASS
  F3  raw-лог: есть @RUN:ID ACK, нет @BRK:valid=1, нет строк FAULT!=0, uart_drp/trunc=0,
      t монотонно, нет @MAP:LOAD:FAIL, покрытие safety-полей >= порога
  F4  артефакт: sha256 файла == манифесту; CRC совпадает с rebase-манифестом
  F5  identity: файл живой identity == значениям манифеста (11 полей)
  F6  манифест burst'а: stop_gate не сработал, map_load_ok, run_id_ack
  F7  независимый аудит зафиксирован (--audit-by, опционально --audit-file)
  F8  comparator (если передан): sha256 записи и baseline CRC == базовому CRC burst'а

После выпуска: --patch-manifest проставляет `baseline_frozen` в не-baseline burst'ы сессии,
без чего валидатор манифеста даёт `BLOCKED [B16]`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_session_manifest import BASELINE_VARIANT_ID, validate  # noqa: E402

SCHEMA_VERSION = "tz2-m0-freeze-1"
CAVEAT = ("M0 baseline — экспериментальная точка отсчёта для M1 vs M0; не доказывает корректность "
          "карты, реконструкции фазных токов, линейности ADC и физическую пригодность OEW map")
MIN_SAFETY_COVERAGE = 0.5


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_foc_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        s = line.lstrip("> ").strip()
        if not s.startswith("@FOC:"):
            continue
        f = {}
        for part in s.split(":"):
            if "=" in part:
                k, _, v = part.partition("=")
                f[k] = v
        rows.append(f)
    return rows


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, cid: str, ok: bool, detail: str) -> None:
        self.rows.append((cid, "PASS" if ok else "FAIL", detail))

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    def render(self) -> str:
        w = max(len(r[0]) for r in self.rows) if self.rows else 2
        return "\n".join(f"{i:<{w}}  {s:<4}  {d}" for i, s, d in self.rows)


def baseline_burst(manifest: dict, run_id: str | None) -> dict | None:
    bursts = [b for b in (manifest.get("bursts") or []) if isinstance(b, dict)]
    if run_id:
        return next((b for b in bursts if b.get("run_id") == run_id), None)
    return next((b for b in bursts if b.get("variant_id") == BASELINE_VARIANT_ID), None)


def run_checks(manifest: dict, bundle: Path, *, gate: Path | None, comparator: Path | None,
               audit_by: str | None, audit_file: Path | None, run_id: str | None) -> tuple[Checks, dict]:
    c = Checks()
    session = manifest.get("session") or {}
    b = baseline_burst(manifest, run_id)
    problems = validate(manifest)
    if b is None:
        c.add("F1", False, "baseline burst не найден")
        return c, {}
    # B16 относится к продвижению сессии (M1 не проектируется до freeze), а не к самой заморозке:
    # при заморозке такие нарушения показываются как INFO и закрываются --patch-manifest.
    b16 = [p for p in problems.items if p.startswith("[B16]")]
    rest = [p for p in problems.items if not p.startswith("[B16]")]
    c.add("F1", not rest, "манифест валиден, baseline burst "
          f"{b.get('run_id')} ({b.get('variant_id')})" if not rest
          else f"нарушений {len(rest)}: " + "; ".join(rest[:3]))
    if b16:
        c.rows.append(("F1b", "INFO",
                       f"не-baseline burst'ов без baseline_frozen: {len(b16)} — "
                       f"закрывается --patch-manifest после заморозки"))
    tel = b.get("telemetry") or {}

    # F2 — gate
    verdict = None
    if gate and gate.exists():
        try:
            report = json.loads(gate.read_text(encoding="utf-8-sig"))
            verdict = report.get("verdict")
        except json.JSONDecodeError as exc:
            verdict = f"не разобран: {exc}"
        c.add("F2", verdict == "PASS", f"gate {gate.name}: verdict={verdict}")
    else:
        c.add("F2", False, "gate-отчёт не передан (--gate) — цепочка G0..G9 не подтверждена")

    # F3 — raw-лог
    log_rel = tel.get("raw_log")
    log_path = (bundle / log_rel) if log_rel else None
    log_sha = None
    if log_path and log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        log_sha = sha256_of(log_path)
        rows = parse_foc_rows(text)
        fault_rows = [r for r in rows if str(r.get("FAULT", "")).isdigit() and int(r["FAULT"]) != 0]
        with_fault_field = [r for r in rows if "FAULT" in r]
        coverage = (len(with_fault_field) / len(rows)) if rows else 0.0
        ts = [int(r["t"]) for r in rows if str(r.get("t", "")).isdigit()]
        monotonic = all(x <= y for x, y in zip(ts, ts[1:])) if ts else False
        m_sys_all = re.findall(r"@SYS:.*uart_drp=(\d+):uart_trunc=(\d+)", text)
        details = []
        ok = True
        if not rows:
            ok, details = False, ["нет строк @FOC"]
        if fault_rows:
            ok = False
            details.append(f"FAULT!=0 в {len(fault_rows)} строках")
        if re.search(r"@BRK:valid=1", text):
            ok = False
            details.append("@BRK:valid=1")
        if not m_sys_all or any(int(drp) or int(trunc) for drp, trunc in m_sys_all):
            ok = False
            details.append("uart_drp/uart_trunc не нулевые или нет @SYS")
        if not monotonic:
            ok = False
            details.append("t не монотонен")
        if "@RUN:ID" not in text:
            ok = False
            details.append("нет @RUN:ID ACK")
        if "@MAP:LOAD:FAIL" in text:
            ok = False
            details.append("@MAP:LOAD:FAIL")
        if coverage < MIN_SAFETY_COVERAGE:
            ok = False
            details.append(f"покрытие safety-полей {coverage:.3f} < {MIN_SAFETY_COVERAGE}")
        c.add("F3", ok, f"лог {log_path.name}: строк {len(rows)}, safety-покрытие {coverage:.3f}"
              if ok else "; ".join(details))
    else:
        c.add("F3", False, f"raw-лог {log_rel!r} не найден")
        rows, coverage, monotonic = [], 0.0, False

    # F4 — артефакт
    art_rel = tel.get("artifact_file")
    art_path = (bundle / art_rel) if art_rel else None
    declared = str(b.get("artifact_sha256") or "").lower()
    art_sha = None
    if art_path and art_path.exists():
        art_sha = sha256_of(art_path)
        c.add("F4", art_sha == declared, f"{art_path.name}: {art_sha[:16]}…" +
              ("" if art_sha == declared else " != манифеста"))
    else:
        c.add("F4", False, f"артефакт {art_rel!r} не найден")

    # F5 — identity
    ident_rel = tel.get("identity_file")
    ident_path = (bundle / ident_rel) if ident_rel else None
    manifest_live = (session.get("live_identity", {}) or {}).get("values") or {}
    ident_ok = False
    detail = "файл живой identity не найден"
    if ident_path and ident_path.exists():
        found = {}
        for line in ident_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if re.fullmatch(r"(0x[0-9A-Fa-f]+|\d+)", v):
                    found[k] = int(v, 16) if v.startswith("0x") else int(v)
        mismatched = [k for k, v in manifest_live.items() if k in found and int(v) != found[k]]
        ident_ok = len(manifest_live) == 11 and not mismatched
        detail = f"11 полей, расхождений {len(mismatched)}" + (f" ({', '.join(mismatched)})" if mismatched else "")
    c.add("F5", ident_ok, detail)

    # F6 — состояние burst'а
    sg = b.get("stop_gate") or {}
    c.add("F6", sg.get("triggered") is False and b.get("map_load_ok") is True
          and b.get("run_id_ack") is True,
          f"stop_gate.triggered={sg.get('triggered')}, map_load_ok={b.get('map_load_ok')}, "
          f"run_id_ack={b.get('run_id_ack')}")

    # F7 — независимый аудит
    audit_sha = None
    if audit_file and audit_file.exists():
        audit_sha = sha256_of(audit_file)
    c.add("F7", bool(audit_by and audit_by.strip()),
          f"аудит: {audit_by or '— не указан (--audit-by)'}" +
          (f", файл {audit_file.name} {audit_sha[:16]}…" if audit_sha else ""))

    # F8 — comparator (если есть)
    cmp_sha = cmp_baseline_crc = None
    if comparator and comparator.exists():
        cmp_sha = sha256_of(comparator)
        try:
            cmp_report = json.loads(comparator.read_text(encoding="utf-8-sig"))
            cmp_baseline_crc = (cmp_report.get("metrics", {}).get(cmp_report.get("baseline", "M0"), {})
                                .get("map_identity", {}).get("value", {}).get("map_crc32") or [None])[0]
        except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
            cmp_baseline_crc = None
        ok = cmp_baseline_crc is not None and \
            str(cmp_baseline_crc).lower() == str(b.get("base_map_crc32") or "").lower()
        c.add("F8", ok, f"comparator {comparator.name}: baseline CRC {cmp_baseline_crc} "
                        f"vs base {b.get('base_map_crc32')}")
    else:
        c.rows.append(("F8", "INFO", "comparator не передан — M0 замораживается как единственный вариант"))

    record = {
        "schema_version": SCHEMA_VERSION,
        "frozen_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": session.get("session_id"),
        "run_id": b.get("run_id"),
        "variant_id": b.get("variant_id"),
        "firmware_sha256": session.get("firmware_sha256"),
        "identity": manifest_live,
        "artifact": {"file": art_rel, "sha256": art_sha,
                     "map_id": b.get("variant_map_id"), "map_crc32": b.get("variant_map_crc32")},
        "raw_log": {"file": log_rel, "sha256": log_sha,
                    "rows": len(rows), "safety_coverage": round(coverage, 4),
                    "t_monotonic": bool(monotonic)},
        "gate_report": {"file": str(gate.name) if gate else None,
                        "sha256": sha256_of(gate) if gate and gate.exists() else None,
                        "verdict": verdict},
        "comparator": {"file": comparator.name if comparator else None, "sha256": cmp_sha,
                       "baseline_crc32": cmp_baseline_crc},
        "audit": {"independent": bool(audit_by), "by": audit_by,
                  "file": audit_file.name if audit_file else None, "sha256": audit_sha},
        "checks": {i: s for i, s, _ in c.rows},
        "caveat": CAVEAT,
    }
    return c, record


def patch_manifest(manifest_path: Path, freeze_path: Path, freeze_sha: str, record: dict,
                   run_ids: list[str]) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    patched = 0
    for b in manifest.get("bursts") or []:
        if not isinstance(b, dict):
            continue
        if b.get("variant_id") == BASELINE_VARIANT_ID:
            continue
        if run_ids and b.get("run_id") not in run_ids:
            continue
        b["baseline_frozen"] = {"file": freeze_path.name, "sha256": freeze_sha,
                                "baseline_crc32": record["artifact"].get("map_crc32")}
        patched += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    return patched


def main() -> int:
    ap = argparse.ArgumentParser(description="Заморозка M0 baseline (TZ-02)")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--gate", default=None, help="gate.json от preflight_m0_burst.py")
    ap.add_argument("--comparator", default=None, help="report.json от map_variant_compare.py")
    ap.add_argument("--audit-by", default=None, help="кто провёл независимый аудит")
    ap.add_argument("--audit-file", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", default=None, help="путь M0_BASELINE_FROZEN.json")
    ap.add_argument("--return-manifest", default=None, help="путь RETURN_SHA256.txt")
    ap.add_argument("--patch-manifest", default=None,
                    help="проставить baseline_frozen в не-baseline burst'ы этого манифеста")
    ap.add_argument("--patch-run-ids", default=None, help="ограничить патч списком run_id через запятую")
    args = ap.parse_args()

    mp, bundle = Path(args.manifest), Path(args.bundle)
    if not mp.exists() or not bundle.exists():
        print("BLOCKED: нет манифеста или каталога пакета")
        return 1
    manifest = json.loads(mp.read_text(encoding="utf-8-sig"))
    checks, record = run_checks(manifest, bundle, gate=Path(args.gate) if args.gate else None,
                                comparator=Path(args.comparator) if args.comparator else None,
                                audit_by=args.audit_by,
                                audit_file=Path(args.audit_file) if args.audit_file else None,
                                run_id=args.run_id)
    print(checks.render())
    if checks.failed or not record:
        print(f"\nBLOCKED: M0 baseline НЕ заморожен — не пройдено: "
              f"{', '.join(r[0] for r in checks.failed)}")
        return 1

    out = Path(args.out) if args.out else bundle / "M0_BASELINE_FROZEN.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    freeze_sha = sha256_of(out)
    print(f"\nFREEZE OK: {out.name} sha256={freeze_sha}")
    print(f"ОГРАНИЧЕНИЕ: {CAVEAT}")

    if args.patch_manifest:
        run_ids = [x.strip() for x in (args.patch_run_ids or "").split(",") if x.strip()]
        n = patch_manifest(Path(args.patch_manifest), out, freeze_sha, record, run_ids)
        print(f"baseline_frozen проставлен в {n} burst(ах) файла {Path(args.patch_manifest).name}")

    if args.return_manifest:
        rm = Path(args.return_manifest)
        entries = []
        for p in sorted(bundle.rglob("*")):
            if p.is_file() and p.name != rm.name:
                entries.append(f"{sha256_of(p)}  {p.relative_to(bundle).as_posix()}")
        entries.append(f"{freeze_sha}  {out.relative_to(bundle).as_posix()}")
        rm.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
        print(f"RETURN_SHA256.txt: {len(entries)} записей (записан последним)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
