#!/usr/bin/env python3
"""Gate G0..G9 перед первым M0 burst сессии M-подбора (TZ-02, шаг c).

Проверяет ровно тот список, что зафиксирован приёмкой, в порядке G0..G8 и печатает вердикт G9.
Fail-closed: любой FAIL (или отсутствие доказательства) → burst не разрешён.

  G0  правильный firmware SHA (образ на диске == session.firmware_sha256)
  G1  mapcap identity с текущей платы (файл есть, 11 полей, совпадают с манифестом)
  G2  сформирован authoritative M0-rebased (rebase-манифест: identity_rebased=true,
      coefficients_changed=false, crc32.after == CRC burst'а)
  G3  artifact SHA/CRC проверены (sha256 файла == burst.artifact_sha256; при --dump-cmd — CRC из dump)
  G4  safety-owner approval заполнен
  G5  оба оператора указаны и различаются
  G6  session_manifest валиден (правила S1..S5, B1..B15)
  G7  --bundle: файлы и sha256 на месте (C1, C2)
  G8  preflight PASS: @SYS (uart_drp/trunc=0), @PWM:CR1 (CCER=0), pdump (ARR == живой),
      @ENC err=0, @MAP:IDENTITY, @MAP:LOAD:OK, нет @BRK:valid=1 / @FAULT
  G9  вердикт: M0 burst разрешён (печатается только при полном PASS)

ВАЖНО (граница результата): M0 burst здесь — не квалификация карты. Он создаёт
экспериментальную baseline-точку для последующего M1 vs M0; физическую пригодность
реконструкции токов и OEW-карты он автоматически не доказывает.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_session_manifest import check_bundle, validate  # noqa: E402

CAVEAT = ("M0 burst создаёт experimental baseline point для M1 vs M0 и НЕ является "
          "квалификацией карты/реконструкции токов")

IDENT_RE = re.compile(
    r"@MAP:IDENTITY:board=(\d+):pwm=(\d+):arr=(\d+):trig=0x([0-9A-Fa-f]+):off=(\d+):dt=(\d+):"
    r"adc_clk=(\d+):sample_x2=(\d+):res=(\d+):acs=0x([0-9A-Fa-f]+):ccs=0x([0-9A-Fa-f]+)")
IDENT_KEYS = ("board_revision", "pwm_frequency_hz", "timer_arr", "adc_trigger_id",
              "trigger_offset_ticks", "deadtime_ticks", "adc_clock_hz", "adc_sample_cycles_x2",
              "adc_resolution", "adc_config_signature", "current_calibration_signature")
LIVE_KEYS = {"board_revision": "board_revision", "pwm_frequency_hz": "pwm_frequency_hz",
             "timer_arr": "timer_arr", "adc_trigger_id": "adc_trigger_id",
             "trigger_offset_ticks": "trigger_offset_ticks", "deadtime_ticks": "deadtime_ticks",
             "adc_clock_hz": "adc_clock_hz", "adc_sample_cycles_x2": "adc_sample_cycles_x2",
             "adc_resolution": "adc_resolution", "adc_config_signature": "adc_config_signature",
             "current_calibration_signature": "current_calibration_signature"}
LIVE_ALIASES = {"board": "board_revision", "pwm": "pwm_frequency_hz", "arr": "timer_arr",
                "trig": "adc_trigger_id", "off": "trigger_offset_ticks", "dt": "deadtime_ticks",
                "adc_clk": "adc_clock_hz", "sample_x2": "adc_sample_cycles_x2",
                "res": "adc_resolution", "acs": "adc_config_signature", "ccs": "current_calibration_signature"}


class Gate:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []   # id, status, detail

    def add(self, gid: str, ok: bool, detail: str) -> None:
        self.rows.append((gid, "PASS" if ok else "FAIL", detail))

    def add_info(self, gid: str, detail: str) -> None:
        self.rows.append((gid, "INFO", detail))

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    def render(self) -> str:
        w = max(len(r[0]) for r in self.rows)
        lines = [f"{r[0]:<{w}}  {r[1]:<4}  {r[2]}" for r in self.rows]
        return "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_identity_text(text: str) -> dict[str, int] | None:
    """Либо строка @MAP:IDENTITY из лога, либо live.txt (key=value)."""
    m = IDENT_RE.search(text)
    if m:
        vals = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4), 16),
                int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8)),
                int(m.group(9)), int(m.group(10), 16), int(m.group(11), 16)]
        return dict(zip(IDENT_KEYS, vals))
    found: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        name = LIVE_KEYS.get(k) or LIVE_ALIASES.get(k)
        if name and re.fullmatch(r"(0x[0-9A-Fa-f]+|\d+)", v):
            found[name] = int(v, 16) if v.startswith("0x") else int(v)
    return found if len(found) == 11 else (found or None)


def baseline_burst(manifest: dict) -> dict | None:
    for b in manifest.get("bursts") or []:
        if isinstance(b, dict) and b.get("variant_id") == "M0-rebased":
            return b
    return None


def run_gates(manifest: dict, bundle: Path, *, firmware: Path | None, rebase_manifest: Path | None,
              dump_cmd: str | None, run_id: str | None, variant_id: str | None = None) -> Gate:
    g = Gate()
    session = manifest.get("session") or {}
    b = baseline_burst(manifest)
    if variant_id:
        b = next((x for x in (manifest.get("bursts") or [])
                  if isinstance(x, dict) and x.get("variant_id") == variant_id), b)
    if b is None:
        g.add("B1", False, "в манифесте нет burst'а для проверяемого варианта")
        return g
    if run_id and b.get("run_id") != run_id:
        b = next((x for x in manifest.get("bursts", []) if x.get("run_id") == run_id), b)
    tel = b.get("telemetry") or {}

    # G0 — firmware
    fw_path = firmware
    if fw_path is None and session.get("firmware_image"):
        cand = bundle / str(session["firmware_image"])
        fw_path = cand if cand.exists() else None
    expected = str(session.get("firmware_sha256") or "").lower()
    if fw_path and fw_path.exists():
        actual = _sha256(fw_path)
        g.add("G0", actual == expected,
              f"образ {fw_path.name}: {actual[:16]}…" +
              ("" if actual == expected else f" != ожидаемого {expected[:16]}…"))
    else:
        g.add("G0", False, "образ firmware не найден (--firmware или session.firmware_image)")

    # G1 — живая identity
    ident_rel = tel.get("identity_file")
    ident_path = (bundle / ident_rel) if ident_rel else None
    live_from_file = None
    if ident_path and ident_path.exists():
        live_from_file = parse_identity_text(ident_path.read_text(encoding="utf-8", errors="replace"))
    manifest_live = {k: v for k, v in (session.get("live_identity", {}).get("values") or {}).items()
                     if k in IDENT_KEYS}
    if live_from_file and len(live_from_file) == 11:
        mismatched = [k for k in IDENT_KEYS if k in manifest_live and manifest_live[k] != live_from_file[k]]
        ok = len(manifest_live) == 11 and not mismatched
        g.add("G1", ok, f"11 полей сняты; расхождений с манифестом: {len(mismatched)}" +
              (f" ({', '.join(mismatched)})" if mismatched else ""))
    else:
        g.add("G1", False, f"живая identity не извлечена из {ident_rel!r} "
                           f"(нужны все 11 полей: @MAP:IDENTITY или live.txt)")

    # G2 — authoritative M0-rebased
    reb_path = rebase_manifest
    if reb_path is None:
        for cand in (bundle / "M0_rebased.json", bundle / "rebase_manifest.json"):
            if cand.exists():
                reb_path = cand
                break
    if reb_path and reb_path.exists():
        try:
            reb = json.loads(reb_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            reb = None
            g.add("G2", False, f"{reb_path.name} не разобран: {exc}")
        if reb is not None:
            crc_after = str((reb.get("crc32") or {}).get("after") or "").lower()
            checks = (reb.get("operation") == "identity_rebase",
                      reb.get("identity_rebased") is True,
                      reb.get("coefficients_changed") is False,
                      crc_after == str(b.get("variant_map_crc32") or "").lower())
            g.add("G2", all(checks),
                  f"identity_rebase={reb.get('operation') == 'identity_rebase'}, "
                  f"coefficients_changed={reb.get('coefficients_changed')}, "
                  f"changes={reb.get('identity_change_count')}, "
                  f"crc32.after={crc_after or '—'} vs burst {b.get('variant_map_crc32')}")
    else:
        g.add("G2", False, "rebase-манифест не найден (--rebase-manifest или M0_rebased.json в пакете)")

    # G2b — для не-baseline вариантов: baseline обязан быть заморожен (M1 не проектируется до freeze)
    if str(b.get("variant_id") or "") != "M0-rebased":
        bf = b.get("baseline_frozen") or {}
        if not isinstance(bf, dict) or not bf.get("file") or not bf.get("sha256"):
            g.add("G2b", False, "нет baseline_frozen (M0_BASELINE_FROZEN.json + sha256) — "
                                "M1 не проектируется до заморозки baseline")
        else:
            freeze_path = bundle / str(bf["file"])
            if not freeze_path.exists():
                g.add("G2b", False, f"файл {bf['file']} отсутствует в пакете")
            else:
                actual = _sha256(freeze_path)
                try:
                    rec = json.loads(freeze_path.read_text(encoding="utf-8-sig"))
                    rec_crc = str(((rec.get("artifact") or {}).get("map_crc32")) or "").lower()
                except json.JSONDecodeError as exc:
                    actual, rec_crc = f"не разобран: {exc}", ""
                expected = str(bf.get("sha256") or "").lower()
                base_crc = str(b.get("base_map_crc32") or "").lower()
                ok = actual == expected and rec_crc == base_crc
                g.add("G2b", ok, f"{bf['file']}: sha256 {str(actual)[:16]}…"
                                 f"{'' if actual == expected else ' != манифеста'}; "
                                 f"baseline CRC {rec_crc or '—'} vs base {b.get('base_map_crc32')}")

    # G3 — artifact SHA/CRC
    art_rel = tel.get("artifact_file")
    art_path = (bundle / art_rel) if art_rel else None
    if art_path and art_path.exists():
        actual = _sha256(art_path)
        declared = str(b.get("artifact_sha256") or "").lower()
        g.add("G3", actual == declared,
              f"{art_path.name}: {actual[:16]}…" + ("" if actual == declared else " != манифеста"))
    else:
        g.add("G3", False, f"артефакт {art_rel!r} не найден в пакете")
    if dump_cmd and art_path and art_path.exists():
        proc = subprocess.run(f"{dump_cmd} {art_path}".split() if isinstance(dump_cmd, str) else dump_cmd,
                              capture_output=True, text=True)
        m = re.search(r"crc32\s*:\s*(0x[0-9a-fA-F]{8})", proc.stdout)
        declared = str(b.get("variant_map_crc32") or "").lower()
        if m:
            g.add("G3b", m.group(1).lower() == declared,
                  f"dump CRC {m.group(1)} vs манифест {b.get('variant_map_crc32')}")
        else:
            g.add("G3b", False, f"dump не дал CRC (rc={proc.returncode}): {proc.stdout.strip()[:80]}")

    # G4/G5 — утверждение и операторы
    approval = str((session.get("envelope") or {}).get("safety_owner_approval") or "")
    g.add("G4", bool(approval.strip()), f"safety_owner_approval: {approval or '— не заполнено'}")
    op1, op2 = session.get("operator1"), session.get("operator2")
    g.add("G5", bool(op1) and bool(op2) and op1 != op2, f"operator1={op1 or '—'} operator2={op2 or '—'}")

    # G6 — манифест
    problems = validate(manifest)
    g.add("G6", not problems,
          "манифест валиден" if not problems else f"нарушений {len(problems.items)}: "
          + "; ".join(problems.items[:3]))

    # G7 — пакет
    bundle_problems = check_bundle(manifest, bundle)
    g.add("G7", not bundle_problems,
          "файлы и sha256 на месте" if not bundle_problems else
          f"нарушений {len(bundle_problems.items)}: " + "; ".join(bundle_problems.items[:3]))

    # G8 — preflight по логу
    log_rel = tel.get("raw_log")
    log_path = (bundle / log_rel) if log_rel else None
    if not log_path or not log_path.exists():
        g.add("G8", False, f"raw-лог {log_rel!r} не найден")
        return g
    text = log_path.read_text(encoding="utf-8", errors="replace")
    pre = text.split("@RUN:ID", 1)[0]        # только pre-burst часть
    missing: list[str] = []
    details: list[str] = []
    m_sys = re.search(r"@SYS:.*uart_drp=(\d+):uart_trunc=(\d+)", pre)
    if not m_sys:
        missing.append("@SYS (sysinfo)")
    elif int(m_sys.group(1)) or int(m_sys.group(2)):
        missing.append(f"uart_drp={m_sys.group(1)}/uart_trunc={m_sys.group(2)} != 0")
    else:
        details.append("uart_drp=0/trunc=0")
    m_p = re.search(r"@PWM:CR1=(\d+):CCER=(\d+):BDTR=(\d+):CNT=(\d+)", pre)
    if not m_p:
        missing.append("@PWM:CR1 (p?)")
    elif int(m_p.group(2)) != 0:
        missing.append(f"p? CCER={m_p.group(2)} != 0 (не idle)")
    else:
        details.append("CCER=0")
    # pdump печатает @PWM:FULL:…T1:PSC=…:ARR=… (cli.c:209-212); @PWM:DUMP: — это ответ
    # команды `dump` (cli.c:194-200). Принимаем оба, но ARR сверяем с живым (identity).
    m_dump = re.search(r"@PWM:FULL:.*?:T1:PSC=\d+:ARR=(\d+)", pre) or \
        re.search(r"@PWM:DUMP:PSC=(\d+):ARR=(\d+)", pre)
    live_arr = (session.get("live_identity", {}).get("values") or {}).get("timer_arr")
    if not m_dump:
        missing.append("@PWM:FULL (pdump) / @PWM:DUMP (dump)")
    else:
        arr = m_dump.group(m_dump.lastindex) if m_dump.lastindex else None
        if arr is None:
            missing.append("pdump без ARR")
        elif live_arr is not None and int(arr) != int(live_arr):
            missing.append(f"pdump ARR={arr} != живой {live_arr}")
        else:
            details.append(f"pdump ARR={arr} == живой" if "@PWM:FULL" in m_dump.group(0)
                           else f"dump ARR={arr} == живой")
    m_enc = re.search(r"@ENC:.*err=(\d+)", pre)
    if not m_enc:
        missing.append("@ENC (enc)")
    elif int(m_enc.group(1)) != 0:
        missing.append(f"enc err={m_enc.group(1)} != 0")
    else:
        details.append("enc err=0")
    if "@MAP:IDENTITY:" not in pre and not (ident_path and ident_path.exists()):
        missing.append("@MAP:IDENTITY")
    if "@MAP:LOAD:OK" not in pre:
        missing.append("@MAP:LOAD:OK")
    if re.search(r"@BRK:valid=1", pre):
        missing.append("@BRK:valid=1 (латч активен)")
    if re.search(r"@FAULT:|FAULT_R=1[0-9]", pre):
        missing.append("признак @FAULT в preflight")
    g.add("G8", not missing, ("preflight чисто: " + ", ".join(details)) if not missing
          else "не выполнено: " + "; ".join(missing))
    return g


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate G0..G9 перед первым M0 burst (TZ-02)")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--firmware", default=None)
    ap.add_argument("--rebase-manifest", default=None)
    ap.add_argument("--dump-cmd", default=None, help="команда для map_variant_cli --dump (опционально)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--variant-id", default=None,
                    help="вариант сессии (по умолчанию — baseline M0-rebased); для не-baseline "
                         "добавляется гейт G2b (замороженный baseline)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    mp, bp = Path(args.manifest), Path(args.bundle)
    if not mp.exists():
        print(f"BLOCKED: нет манифеста {mp}")
        return 1
    manifest = json.loads(mp.read_text(encoding="utf-8-sig"))
    if not bp.exists():
        print(f"BLOCKED: нет каталога пакета {bp}")
        return 1

    gate = run_gates(manifest, bp,
                     firmware=Path(args.firmware) if args.firmware else None,
                     rebase_manifest=Path(args.rebase_manifest) if args.rebase_manifest else None,
                     dump_cmd=args.dump_cmd, run_id=args.run_id, variant_id=args.variant_id)

    print(gate.render())
    report = {"gates": [{"id": i, "status": s, "detail": d} for i, s, d in gate.rows],
              "caveat": CAVEAT}
    if gate.failed:
        print(f"\nG9: M0 burst НЕ разрешён — не пройдено: "
              f"{', '.join(r[0] for r in gate.failed)}")
        report["verdict"] = "BLOCKED"
    else:
        print("\nG9: M0 burst разрешён (все гейты PASS)")
        print(f"ОГРАНИЧЕНИЕ: {CAVEAT}")
        report["verdict"] = "PASS"
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json: {args.json}")
    return 1 if gate.failed else 0


if __name__ == "__main__":
    sys.exit(main())
