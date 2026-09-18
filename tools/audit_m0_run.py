#!/usr/bin/env python3
"""Аудит лога прогона M0 (TZ-02) — два режима, разделённые по смыслу.

  --mode session   полный сессионный лог (как возврат R1): preflight-команды и их порядок,
                   `c` до identity, mapload с CRC, @RUN:ID, защита, окно VBUS в мВ,
                   факт запуска FOC. Коды правил A1…A9.
  --mode repeat    пер-прогонный лог повторной серии (как R2…R5): identity той же живой
                   конфигурации, @RUN:ID, arm/run rc=0, 8 записей + drain + frames + dropped,
                   отсутствие FAULT/BREAK, чистота UART, монотонность t, VBUS внутри конверта.
                   Коды правил B1…B9.

Режимы не смешиваются: пер-прогонный лог по построению не содержит preflight-команд и mapload,
а сессионный — не обязан быть «одним прогоном». Один инструмент — два контракта.

Использование:
    python tools/audit_m0_run.py <log> --mode session
    python tools/audit_m0_run.py <log> --mode repeat --run-id M0-R2 [--expect-records 8]
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
LOAD_RE = re.compile(r"@MAP:LOAD:OK:crc=(0x[0-9A-Fa-f]{8}):cid=(0x[0-9A-Fa-f]{8})")
REC_RE = re.compile(r"@MC:REC:cap=(\d+):seq=(\d+):raw_i1=(\d+):raw_i2=(\d+):raw_ct=(\d+):"
                    r"raw_vbus=(\d+)")
STATUS_RE = re.compile(r"@MC:STATUS:state=(\d+):term=(\d+)(?::cap=(\d+))?:frames=(\d+):dropped=(\d+)")
CMD_RE = re.compile(r">>>\s*([^\r\n]*)")
FOC_T_RE = re.compile(r"@FOC:t=(\d+)")
SYS_RE = re.compile(r"@SYS:.*?uart_drp=(\d+):uart_trunc=(\d+)")


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.lines = [ln.rstrip("\r") for ln in self.text.split("\n")]
        self.cmds = [m.group(1).strip() for ln in self.lines for m in [CMD_RE.search(ln)] if m]
        self.fault_rows = [ln for ln in self.lines if ln.lstrip("> ").startswith("@FOC:")
                           and (m := re.search(r"FAULT=(\d+)", ln)) and int(m.group(1)) != 0]
        self.brk_valid1 = len(re.findall(r"@BRK:valid=1", self.text))
        self.brk_lines = re.findall(r"@BRK[^\r\n]*", self.text)
        self.uart = SYS_RE.findall(self.text)
        self.ts = [int(m.group(1)) for ln in self.lines for m in [FOC_T_RE.search(ln)] if m]
        self.uart_lost = [f"{d}/{t}" for d, t in self.uart if int(d) or int(t)]

    def monotonic(self) -> bool | None:
        return all(a <= b for a, b in zip(self.ts, self.ts[1:])) if self.ts else None


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, cid: str, ok: bool, detail: str) -> None:
        self.rows.append((cid, "PASS" if ok else "FAIL", detail))

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    def render(self) -> str:
        tokens = [f"[{cid}]" for cid, _, _ in self.rows]
        w = max((len(t) for t in tokens), default=4)
        return "\n".join(f"{t:<{w}}  {s:<4}  {d}" for t, (_, s, d) in zip(tokens, self.rows))


def identity_of(text: str) -> dict | None:
    m = IDENT_RE.search(text)
    if not m:
        return None
    return {"board_revision": int(m.group(1)), "pwm_frequency_hz": int(m.group(2)),
            "timer_arr": int(m.group(3)), "adc_trigger_id": int(m.group(4), 16),
            "deadtime_ticks": int(m.group(6)), "adc_clock_hz": int(m.group(7)),
            "adc_sample_cycles_x2": int(m.group(8)),
            "adc_config_signature": int(m.group(10), 16),
            "current_calibration_signature": int(m.group(11), 16)}


def audit_session(log: Log, expect_crc: str, expect_cid: str, expect_ccs: int,
                  vbus_min: int, vbus_max: int) -> tuple[Checks, dict]:
    c, out = Checks(), {}
    need = ["sysinfo", "p?", "pdump", "enc", "c", "mapcap identity"]
    present = [cmd for cmd in need if cmd in log.cmds]
    c_idx = log.cmds.index("c") if "c" in log.cmds else -1
    id_idx = log.cmds.index("mapcap identity") if "mapcap identity" in log.cmds else -1
    c.add("A1", len(present) == len(need) and 0 <= c_idx < id_idx,
          f"команды: {', '.join(present)}" +
          ("" if 0 <= c_idx < id_idx else "; порядок `c`→identity нарушен"))

    ident = identity_of(log.text)
    if ident:
        out["identity"] = ident
        c.add("A2", ident["current_calibration_signature"] == expect_ccs,
              f"11 полей, ccs=0x{ident['current_calibration_signature']:08X}")
        cal = bool(re.search(r"@ADC:(CAL|STATUS):", log.text))
        c.add("A2b", cal and ident["current_calibration_signature"] == expect_ccs,
              "ответ калибровки есть" if cal else "нет ответа калибровки (@ADC:CAL/@ADC:STATUS)")
    else:
        c.add("A2", False, "нет @MAP:IDENTITY с 11 полями")

    lm = LOAD_RE.search(log.text)
    c.add("A3", bool(lm) and lm.group(1).lower() == expect_crc.lower()
          and lm.group(2).lower() == expect_cid.lower(),
          f"@MAP:LOAD:OK {lm.group(1) if lm else '—'} cid={lm.group(2) if lm else '—'}")

    rm = re.search(r"@RUN:ID=(\S+)", log.text)
    c.add("A4", bool(rm), f"@RUN:ID={rm.group(1) if rm else '—'}")

    recs = len(REC_RE.findall(log.text))
    drain = re.search(r"@MC:DRAIN:records=(\d+)", log.text)
    status = STATUS_RE.search(log.text)
    ok5 = recs > 0 and drain is not None and int(drain.group(1)) == recs
    c.add("A5", ok5, f"@MC:REC={recs}, drain={drain.group(1) if drain else '—'}"
          + (f", status state={status.group(1)}/dropped={status.group(5)}" if status else ""))

    last_p = re.findall(r"@PWM:CR1=(\d+):CCER=(\d+):BDTR=(\d+):CNT=(\d+)", log.text)
    ccer = int(last_p[-1][1]) if last_p else None
    c.add("A6", not log.fault_rows and log.brk_valid1 == 0 and ccer == 0,
          f"FAULT!=0 — {len(log.fault_rows)}; @BRK:valid=1 — {log.brk_valid1}; CCER(end)={ccer}")
    c.add("A7", bool(log.uart) and not log.uart_lost,
          f"строк @SYS: {len(log.uart)}, потерь: {len(log.uart_lost)}")

    vbus = [int(x) for x in re.findall(r"VBUS=(\d+)", log.text)]
    inwin = [v for v in vbus if vbus_min <= v <= vbus_max]
    c.add("A8", log.monotonic() is not False and bool(inwin),
          f"t-строк {len(log.ts)}, монотонен: {log.monotonic()}; VBUS в конверте: {len(inwin)}"
          + (f", max={max(vbus)}" if vbus else ""))
    run_rows = [1 for ln in log.lines if ln.lstrip("> ").startswith("@FOC:")
                and (m := re.search(r"RUN=(\d+)", ln)) and int(m.group(1)) != 0]
    c.add("A9", not run_rows, f"FOC run-строк: {len(run_rows)} (capture-сессия)")
    return c, out


def audit_repeat(log: Log, run_id: str | None, expect_ccs: int, expect_records: int,
                 vbus_min: int, vbus_max: int, raw_scale_mv: int) -> tuple[Checks, dict]:
    c, out = Checks(), {}
    ident = identity_of(log.text)
    if ident:
        out["identity"] = ident
        ccs = ident["current_calibration_signature"]
        c.add("B1", ccs == expect_ccs,
              f"identity 11 полей, ccs=0x{ccs:08X}, arr={ident['timer_arr']}")
    else:
        c.add("B1", False, "нет @MAP:IDENTITY с 11 полями")

    rid = re.search(r"@RUN:ID=(\S+)", log.text)
    ok2 = bool(rid) and (run_id is None or rid.group(1) == run_id)
    c.add("B2", ok2, f"@RUN:ID={rid.group(1) if rid else '—'}"
          + ("" if ok2 else f" (ожидался {run_id})"))

    arm = re.search(r"@MC:ARM:cap=(\d+):rc=(-?\d+):offsets_valid=(\d+)", log.text)
    c.add("B3", bool(arm) and arm.group(2) == "0",
          f"@MC:ARM cap={arm.group(1) if arm else '—'} rc={arm.group(2) if arm else '—'} "
          f"offsets_valid={arm.group(3) if arm else '—'}")
    out["cap"] = int(arm.group(1)) if arm else None

    run = re.search(r"@MC:RUN:rc=(-?\d+)", log.text)
    c.add("B4", bool(run) and run.group(1) == "0", f"@MC:RUN rc={run.group(1) if run else '—'}")

    recs = len(REC_RE.findall(log.text))
    drain = re.search(r"@MC:DRAIN:records=(\d+)", log.text)
    status = STATUS_RE.search(log.text)
    frames = int(status.group(4)) if status else None
    dropped = int(status.group(5)) if status else None
    ok5 = (recs == expect_records and drain is not None and int(drain.group(1)) == recs
           and frames == expect_records and dropped == 0)
    c.add("B5", ok5, f"@MC:REC={recs}, drain={drain.group(1) if drain else '—'}, "
                     f"frames={frames}, dropped={dropped} (ожидалось {expect_records})")

    c.add("B6", not log.fault_rows and log.brk_valid1 == 0,
          f"FAULT!=0 — {len(log.fault_rows)}; @BRK:valid=1 — {log.brk_valid1}")
    c.add("B7", bool(log.uart) and not log.uart_lost,
          f"строк @SYS: {len(log.uart)}, потерь: {len(log.uart_lost)}")
    c.add("B8", log.monotonic() is not False, f"t-строк {len(log.ts)}, монотонен: {log.monotonic()}")

    raw = [int(m.group(6)) for m in REC_RE.finditer(log.text)]
    mv = [r * raw_scale_mv for r in raw]
    inwin = [v for v in mv if vbus_min <= v <= vbus_max]
    out["raw_vbus"] = raw
    c.add("B9", bool(mv) and len(inwin) == len(mv),
          f"raw_vbus {min(raw) if raw else '—'}…{max(raw) if raw else '—'} (≈"
          f"{min(mv) if mv else '—'}…{max(mv) if mv else '—'} мВ при шкале {raw_scale_mv}) — "
          f"в конверте [{vbus_min};{vbus_max}]: {'да' if mv and len(inwin) == len(mv) else 'НЕТ'}")
    return c, out


def main() -> int:
    ap = argparse.ArgumentParser(description="Аудит лога прогона M0 (session | repeat)")
    ap.add_argument("log")
    ap.add_argument("--mode", choices=("session", "repeat"), required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--expect-crc", default="0x00E666F3")
    ap.add_argument("--expect-cid", default="0x424F4152")
    ap.add_argument("--expect-ccs", default="0x13552B12")
    ap.add_argument("--expect-records", type=int, default=8)
    ap.add_argument("--vbus-min-mv", type=int, default=24000)
    ap.add_argument("--vbus-max-mv", type=int, default=36000)
    ap.add_argument("--raw-vbus-scale-mv", type=int, default=100,
                    help="мВ на единицу raw_vbus (по факту R1: raw 305 ≈ 30521 мВ)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"BLOCKED: нет файла {path}")
        return 1
    log = Log(path)
    if args.mode == "session":
        checks, extra = audit_session(log, args.expect_crc, args.expect_cid,
                                      int(args.expect_ccs, 0), args.vbus_min_mv, args.vbus_max_mv)
    else:
        checks, extra = audit_repeat(log, args.run_id, int(args.expect_ccs, 0),
                                     args.expect_records, args.vbus_min_mv, args.vbus_max_mv,
                                     args.raw_vbus_scale_mv)

    print(f"AUDIT[{args.mode}] {path.name}: {path.stat().st_size} Б, строк {len(log.lines)}")
    print(checks.render())
    verdict = "PASS" if not checks.failed else "FAIL"
    print(f"\nАУДИТ: {verdict}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"mode": args.mode, "log": str(path), "verdict": verdict,
             "checks": [{"id": i, "status": s, "detail": d} for i, s, d in checks.rows],
             **extra}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"json: {args.json}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
