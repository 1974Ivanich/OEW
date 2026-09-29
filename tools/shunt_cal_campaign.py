#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shunt_cal_campaign.py — автоматизированный measurement loop калибровки тракта
шунт→ADC инверторов (TZ_CURRENT_SHUNT_CALIBRATION.md + TZ_SHUNT_CAL_CAMPAIGN_PC3.md).

Роли приборов (принципиально):
  * TAO3104A — ЭТАЛОННЫЙ измерительный прибор: сырой осциллографический шунт
    даёт I_ref = V_shunt / R_shunt;
  * STM32 raw_i1/raw_i2 — НЕИЗВЕСТНАЯ измеряемая величина (не «подтверждение»).

Окна raw берутся штатными командами прошивки `ci 0` (zero) / `ci 1` / `ci 2`
(256 сэмплов, avg/min/max — acquisition без подгонки); телеметрия @ADC —
в provenance. Waveform TAO по CH1/CH2 сохраняется целиком (CSV t/volts/code),
коэффициент всегда можно пересчитать независимо.

Capability/readback gate: SET-команды SCPI на V3.0.0 подтверждённо игнорируются
(tools/OWON_TAO3104A_PROTOCOL.md), поэтому инструмент НЕ «настраивает» прибор,
а верифицирует настройку оператора readback'ом: *IDN?, HEAD (DATALEN, IDN,
наличие/включённость CH1/CH2, ожидаемые SCALE/PROBE/COUPLING в допуске),
пробное чтение waveform обоих каналов. Любой отказ → вердикт
SCOPE_CONTROL_BLOCKED, кампания не начинается — ложного результата быть не может.

Коэффициенты прошивки этим инструментом НЕ меняются: fit (offset/sign/gain,
R², зависимость остатка от тока) делает tools/ci_fit.py по выгруженному CSV.

Использование:
  py -3 tools/shunt_cal_campaign.py campaign --out SHUNTCAL --port COM4
       [--points 500,1000,1500,2000,2500,3000] [--rshunt 0.03]
       [--expect-scale-ch1 100mV --expect-probe-ch1 1X ...]
  (без --port — только осциллограф, raw-окна оператор снимает отдельно)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import re
import statistics
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

# Верифицированная константа «код 0 В» прибора (SN 2306027, V3.0.0);
# держится синхронно с tools/tao3104a_cap.py CODE_0V.
CODE_0V = 305.5

SCOPE_CONTROL_BLOCKED = "SCOPE_CONTROL_BLOCKED"
DEFAULT_SCALE_TOL_PCT = 15.0


class CampaignError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


def num(s: Any) -> float:
    """SI-количество прибора ('100mV', '500us', '(1MSa/s)'); без суффикса — число."""
    if s is None:
        return 1.0
    s = str(s).strip()
    if not s:
        return 1.0
    m = re.match(r"^\s*\(?\s*([0-9.]+)\s*([kKmMuUnNgG]?)", s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return 1.0
    v = float(m.group(1))
    u = m.group(2)
    return v * {"k": 1e3, "K": 1e3, "m": 1e-3, "u": 1e-6,
                "n": 1e-9, "M": 1e6, "G": 1e9}.get(u, 1.0)


def codes_to_volts(codes: Sequence[float], meta: dict) -> list:
    """Коды 8-бит SCREEN -> вольты (модель tao3104a_cap.to_volts, без numpy)."""
    scale = num(meta.get("SCALE"))
    probe = 1.0 if str(meta.get("PROBE", "1X")).strip().upper() == "1X" else 10.0
    off = float(meta.get("OFFSET", 0))
    k = (scale / 25.0) * probe
    return [(float(c) - off - CODE_0V) * k for c in codes]


def chan_meta(head: dict, ch: str) -> dict:
    for c in head.get("CHANNEL", []):
        if c.get("NAME") == ch:
            return c
    raise KeyError(ch)


def chan_field(head: dict, ch: str, key: str):
    try:
        return chan_meta(head, ch).get(key)
    except KeyError:
        return None


# ---------------------------------------------------------------- transports
class ScopeAdapter:
    """Адаптер реального TAO3104A (lazy import: pyusb/numpy только на стенде)."""

    def __init__(self) -> None:
        import tao3104a_cap as tao  # pyusb/numpy нужны только здесь

        self.sc = tao.Scope()

    def identify(self) -> str:
        return self.sc.identify()

    def head(self) -> dict:
        return self.sc.head()

    def waveform_codes(self, ch: str) -> list:
        return list(self.sc.waveform(ch, mode="SCREEN"))

    def close(self) -> None:
        self.sc.close()


CI_KV_RE = re.compile(r"([a-z0-9_]+)=(-?\d+)")


class Stm32Adapter:
    """UART STM32: телеметрия @ADC и raw-окна ci 0/1/2 + полный лог сессии."""

    ADC_RE = re.compile(r"@ADC:I1=(-?\d+):I2=(-?\d+):Ires=(-?\d+):VBUS=(-?\d+)")

    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        import serial  # pyserial нужен только на стенде

        self.log_f = open(log_path, "a", encoding="utf-8", newline="")
        self.log_f.write("[META] port=%s baud=%d started=%s\n" % (port, baud, utc_now()))
        self.ser = serial.Serial(port, baud, timeout=0.05, write_timeout=1.0)

    def _transact(self, cmd: str, wait_s: float) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r\n").encode())
        t0 = time.time()
        buf = b""
        while time.time() - t0 < wait_s:
            chunk = self.ser.read(512)
            if chunk:
                buf += chunk
                self.log_f.write(chunk.decode(errors="replace"))
                self.log_f.flush()
                if b"> " in chunk:
                    break
        return buf.decode(errors="replace")

    def read_adc(self, wait_s: float = 1.5) -> Optional[dict]:
        m = self.ADC_RE.search(self._transact("a", wait_s))
        if not m:
            return None
        return {"i1_ma": int(m.group(1)), "i2_ma": int(m.group(2)),
                "ires_ma": int(m.group(3)), "vbus_mv": int(m.group(4))}

    def ci_window(self, ch: int, wait_s: float = 3.0) -> Optional[dict]:
        """ci 0 -> zero (оба канала), ci 1/ci 2 -> raw-окно канала. None = отказ."""
        text = self._transact("ci %d" % ch, wait_s)
        if "@CI:FAIL" in text:
            return None
        kind = "ZERO" if ch == 0 else "PT%d" % ch
        m = re.search(r"@CI:%s:(.*)" % kind, text)
        if not m:
            return None
        kv = {k: int(v) for k, v in CI_KV_RE.findall(m.group(1))}
        return kv or None

    def close(self) -> None:
        try:
            self.ser.close()
        finally:
            self.log_f.close()


# ---------------------------------------------------------------- gate
def run_capability_gate(sc, expect: dict) -> dict:
    """Readback-верификация настройки прибора (SET-команды на V3.0.0 игнорируются).

    expect-ключи (все необязательны): scale_ch1/ch2 ('100mV'),
    probe_ch1/ch2 ('1X'|'10X'), coupling_ch1/ch2 ('DC'),
    scale_tolerance_pct (default 15), allow_display_off (bool).
    """
    steps: list = []

    def step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    def blocked(reason: str) -> dict:
        return {"verdict": SCOPE_CONTROL_BLOCKED, "blocked_reason": reason,
                "steps": steps}

    # 1. *IDN?
    try:
        idn = sc.identify()
    except Exception as e:  # noqa: BLE001 — гейт обязан пережить любой отказ прибора
        step("idn", False, "exception: %s" % e)
        return blocked("idn_no_reply")
    step("idn", True, idn)
    if "TAO3104A" not in idn:
        return blocked("idn_unexpected_instrument")

    # 2. HEAD: JSON + length prefix — сам факт читаемости = capability acquisition
    try:
        head = sc.head()
    except Exception as e:  # noqa: BLE001
        step("head", False, "exception: %s" % e)
        return blocked("head_read_failed")
    datalen = int(head.get("SAMPLE", {}).get("DATALEN", 0))
    step("head_datalen", datalen > 0, str(datalen))
    if datalen <= 0:
        return blocked("head_datalen_zero")
    if str(head.get("IDN", "")) != idn:
        step("head_idn_match", False,
             "IDN=%r head.IDN=%r" % (idn, head.get("IDN")))
        return blocked("head_idn_mismatch")
    step("head_idn_match", True)

    # 3. CH1/CH2 в HEAD: наличие, DISPLAY=ON (если не разрешено иное)
    names = {c.get("NAME") for c in head.get("CHANNEL", [])}
    tol = float(expect.get("scale_tolerance_pct", DEFAULT_SCALE_TOL_PCT))
    channels = tuple(expect.get("channels", ("CH1", "CH2")))
    for ch in channels:
        if ch not in names:
            step("%s_present" % ch, False)
            return blocked("%s_absent_in_head" % ch)
        step("%s_present" % ch, True)
        display = str(chan_field(head, ch, "DISPLAY") or "ON").upper()
        if display != "ON" and not expect.get("allow_display_off"):
            step("%s_display_on" % ch, False, "DISPLAY=%s" % display)
            return blocked("%s_display_off" % ch)
        step("%s_display_on" % ch, True)

    # 4. Readback ожиданий настройки (шкалу/щуп ставит человек — сверяем в допуске)
    for ch in channels:
        key = ch.lower()
        meta = chan_meta(head, ch)
        want_scale = expect.get("scale_%s" % key)
        if want_scale:
            got, want = num(meta.get("SCALE")), num(want_scale)
            okv = abs(got - want) <= max(want, 1e-12) * (tol / 100.0)
            step("%s_scale_readback" % ch, okv,
                 "expect=%s got=%s (tol %.0f%%)" % (
                     want_scale, meta.get("SCALE"), tol))
            if not okv:
                return blocked("%s_scale_mismatch" % ch)
        want_probe = expect.get("probe_%s" % key)
        if want_probe:
            okp = str(meta.get("PROBE", "")).strip().upper() == \
                str(want_probe).strip().upper()
            step("%s_probe_readback" % ch, okp,
                 "expect=%s got=%s" % (want_probe, meta.get("PROBE")))
            if not okp:
                return blocked("%s_probe_mismatch" % ch)
        want_coup = expect.get("coupling_%s" % key)
        if want_coup:
            okc = str(meta.get("COUPLING", "")).strip().upper() == \
                str(want_coup).strip().upper()
            step("%s_coupling_readback" % ch, okc,
                 "expect=%s got=%s" % (want_coup, meta.get("COUPLING")))
            if not okc:
                return blocked("%s_coupling_mismatch" % ch)

    # 5. Пробное чтение waveform: полный DATALEN и ненулевые данные
    for ch in channels:
        try:
            codes = sc.waveform_codes(ch)
        except Exception as e:  # noqa: BLE001
            step("%s_waveform_probe" % ch, False, "exception: %s" % e)
            return blocked("%s_waveform_read_failed" % ch)
        if len(codes) != datalen:
            step("%s_waveform_probe" % ch, False,
                 "len=%d want=%d" % (len(codes), datalen))
            return blocked("%s_waveform_len_mismatch" % ch)
        if not any(c != 0 for c in codes):
            step("%s_waveform_probe" % ch, False, "all-zero codes")
            return blocked("%s_waveform_all_zero" % ch)
        step("%s_waveform_probe" % ch, True, "len=%d" % len(codes))

    return {"verdict": "PASS", "blocked_reason": None, "steps": steps,
            "idn": idn, "head": head}


# ---------------------------------------------------------------- stats/records
def window_stats_volts(volts: Sequence[float]) -> dict:
    mv = [v * 1000.0 for v in volts]
    return {"n": len(mv),
            "mean_mv": round(statistics.fmean(mv), 4),
            "median_mv": round(statistics.median(mv), 4),
            "rms_mv": round(math.sqrt(sum(x * x for x in mv) / len(mv)), 4),
            "pp_mv": round(max(mv) - min(mv), 4) if mv else 0.0}


def build_record(point: str, rshunt: float, head: dict,
                 codes1: Sequence[float], codes2: Sequence[float],
                 zero: Optional[dict], ci1: Optional[dict], ci2: Optional[dict],
                 telemetry: Optional[dict]) -> dict:
    meta1, meta2 = chan_meta(head, "CH1"), chan_meta(head, "CH2")
    v1 = codes_to_volts(codes1, meta1)
    v2 = codes_to_volts(codes2, meta2)
    s1, s2 = window_stats_volts(v1), window_stats_volts(v2)
    i1_ref = round(abs(s1["mean_mv"]) / rshunt, 3)  # мВ / Ом = мА
    i2_ref = round(abs(s2["mean_mv"]) / rshunt, 3)
    rec: dict = {
        "point": point,
        "timestamp": utc_now(),
        "rshunt_ohm": rshunt,
        "scope": {
            "ch1": dict(s1, i_ref_ma=i1_ref,
                        config={k: meta1.get(k) for k in
                                ("SCALE", "PROBE", "COUPLING", "OFFSET", "INVERSE")}),
            "ch2": dict(s2, i_ref_ma=i2_ref,
                        config={k: meta2.get(k) for k in
                                ("SCALE", "PROBE", "COUPLING", "OFFSET", "INVERSE")}),
            "timebase_scale": head.get("TIMEBASE", {}).get("SCALE"),
            "datalen": head.get("SAMPLE", {}).get("DATALEN"),
        },
        "stm32": {
            "zero_window": zero,          # @CI:ZERO (raw_i1_*/raw_i2_*)
            "raw_i1_window": ci1,         # @CI:PT1 (avg/min/max, 256 сэмплов)
            "raw_i2_window": ci2,         # @CI:PT2
            "telemetry": telemetry,       # @ADC (мгновенный кадр, в provenance)
        },
    }
    # Модельная проверка (advisory, avg окна): raw ≈ offset + sign·gain·I_ref
    try:
        model = {}
        for chn, ref, win, zkey in (("ch1", i1_ref, ci1, "raw_i1_avg"),
                                    ("ch2", i2_ref, ci2, "raw_i2_avg")):
            if win and ref > 0:
                raw = float(win.get(zkey, 0))
                if zero:
                    z = float(zero.get(zkey.replace("avg", "avg"), 0))
                    gain = (raw - z) / (ref / 1000.0)
                else:
                    gain = raw / (ref / 1000.0)
                model[chn] = {"gain_raw_per_a_advisory": round(gain, 2)}
        if model:
            rec["model_check"] = {
                "advisory": model,
                "note": "pre-fit only; вердикт делает ci_fit.py по всем точкам"}
    except Exception:  # noqa: BLE001 — advisory не должен ломать запись
        pass
    return rec


def save_waveform_csv(out: Path, name: str, head: dict, codes: Sequence[float],
                      meta: dict) -> Path:
    p = out / name
    n = len(codes)
    dt = 10.0 * num(head.get("TIMEBASE", {}).get("SCALE")) / n
    volts = codes_to_volts(codes, meta)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "volts", "code"])
        for i, (c, v) in enumerate(zip(codes, volts)):
            w.writerow(["%.9g" % (i * dt), "%.9g" % v, "%d" % c])
    return p


def dataset_crc32(out: Path, names: Sequence[str]) -> str:
    crc = 0
    for name in sorted(names):
        with open(out / name, "rb") as f:
            crc = zlib.crc32(f.read(), crc)
    return "%08x" % (crc & 0xFFFFFFFF)


# ---------------------------------------------------------------- campaign
def run_campaign(sc, stm32, out: Path, opts: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "schema": "shunt-cal-campaign-v1",
        "started": utc_now(),
        "phase": "GATE",
        "verdict": None,
        "verdict_reason": None,
        "reference_note": "TAO3104A raw shunt waveform = reference; STM32 raw = unknown",
        "rshunt_ohm": opts["rshunt"],
        "points_requested": opts["points"],
        "records": [],
        "waveform_files": [],
        "firmware_sha256": opts.get("firmware_sha256"),
        "map_crc32": opts.get("map_crc32"),
        "host": {"python": platform.python_version(), "node": platform.node()},
        "code_0v": CODE_0V,
    }

    gate = run_capability_gate(sc, opts["expect"])
    report["capability_gate"] = gate
    report["scope_identity"] = {"idn": gate.get("idn"),
                                "model": (gate.get("head") or {}).get("MODEL")}
    if gate["verdict"] != "PASS":
        report["phase"] = "BLOCKED"
        report["verdict"] = SCOPE_CONTROL_BLOCKED
        report["verdict_reason"] = gate["blocked_reason"]
        report["finished"] = utc_now()
        write_json(out / "campaign_report.json", report)
        print("GATE BLOCKED: %s — кампания не начиналась, записей нет"
              % gate["blocked_reason"])
        for s in gate["steps"]:
            print("  %-22s %s %s" % (s["step"], "OK " if s["ok"] else "ERR",
                                     s["detail"]))
        return report
    print("Capability gate: PASS (%d шагов readback)" % len(gate["steps"]))

    head = gate["head"]
    meta1, meta2 = chan_meta(head, "CH1"), chan_meta(head, "CH2")

    def capture(point: str) -> None:
        c1 = sc.waveform_codes("CH1")
        c2 = sc.waveform_codes("CH2")
        zero = ci1 = ci2 = None
        tel = None
        if stm32 is not None:
            if point == "0A":
                zero = stm32.ci_window(0)
                if zero is None:
                    print("  ВНИМАНИЕ: ci 0 не ответил — zero-окна нет в записи")
            else:
                ci1 = stm32.ci_window(1)
                ci2 = stm32.ci_window(2)
                if ci1 is None or ci2 is None:
                    print("  ВНИМАНИЕ: окно ci не ответило (ci1=%s ci2=%s)"
                          % (ci1 is not None, ci2 is not None))
            tel = stm32.read_adc()
        safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", point)
        w1 = save_waveform_csv(out, "scope_CH1_%s.csv" % safe, head, c1, meta1)
        w2 = save_waveform_csv(out, "scope_CH2_%s.csv" % safe, head, c2, meta2)
        report["waveform_files"] += [w1.name, w2.name]
        rec = build_record(point, opts["rshunt"], head, c1, c2,
                           zero, ci1, ci2, tel)
        report["records"].append(rec)
        write_json(out / "campaign_report.json", report)  # crash-resilient
        print("  point %-8s I_ref1=%9.2f мА I_ref2=%9.2f мА" % (
            point, rec["scope"]["ch1"]["i_ref_ma"],
            rec["scope"]["ch2"]["i_ref_ma"]))

    print("Фаза A: PWM OFF, тока нет (zero). Enter — снять точку, s — пропустить.")
    try:
        a = input("> ")
    except EOFError:
        a = "s"
    if a.strip().lower() != "s":
        capture("0A")
    else:
        report["phase_a_skipped"] = True

    for label in opts["points"]:
        print("Фаза B: подайте и стабилизируйте точку %s. "
              "Enter — снять, s — пропустить." % label)
        try:
            b = input("> ")
        except EOFError:
            b = "s"
        if b.strip().lower() != "s":
            capture(label)

    if not report["records"]:
        report["phase"] = "EMPTY"
        report["verdict"] = "NO_RECORDS"
        report["verdict_reason"] = "все точки пропущены"
    else:
        report["phase"] = "DONE"
        report["verdict"] = "RECORDED"
        report["verdict_reason"] = (
            "%d записей; fit — tools/ci_fit.py по CSV waveform"
            % len(report["records"]))
    report["finished"] = utc_now()
    # CRC по неизменяемым артефактам (отчёт дописывает себя после CRC)
    names = list(report["waveform_files"])
    if (out / "uart_session.log").exists():
        names.append("uart_session.log")
    report["dataset_crc32"] = dataset_crc32(out, names)
    report["dataset_crc32_scope"] = (
        "waveform CSV + uart_session.log; campaign_report.json исключён "
        "(перезаписывает себя после подсчёта)")
    write_json(out / "campaign_report.json", report)
    print("Verdict: %s (%s)" % (report["verdict"], report["verdict_reason"]))
    print("dataset_crc32=%s" % report["dataset_crc32"])
    return report


# ---------------------------------------------------------------- CLI
def build_argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Автоматизированная кампания калибровки шунтов")
    sub = p.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("campaign", help="полная кампания (gate + фазы A/B)")
    c.add_argument("--out", required=True, help="каталог результатов")
    c.add_argument("--port", default=None, help="COM-порт STM32, например COM4")
    c.add_argument("--baud", type=int, default=115200)
    c.add_argument("--points", default="500,1000,1500,2000,2500,3000",
                   help="метки точек (мА) по схеме создания тока оператора")
    c.add_argument("--rshunt", type=float, default=0.03, help="шунт, Ом")
    c.add_argument("--expect-scale-ch1", default=None)
    c.add_argument("--expect-scale-ch2", default=None)
    c.add_argument("--expect-probe-ch1", default=None)
    c.add_argument("--expect-probe-ch2", default=None)
    c.add_argument("--expect-coupling-ch1", default=None)
    c.add_argument("--expect-coupling-ch2", default=None)
    c.add_argument("--scale-tolerance-pct", type=float, default=DEFAULT_SCALE_TOL_PCT)
    c.add_argument("--allow-display-off", action="store_true")
    c.add_argument("--firmware-sha256", default=None)
    c.add_argument("--map-crc32", default=None)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argparse().parse_args(argv)
    out = Path(args.out)
    if args.mode == "campaign":
        expect = {
            "scale_ch1": args.expect_scale_ch1, "scale_ch2": args.expect_scale_ch2,
            "probe_ch1": args.expect_probe_ch1, "probe_ch2": args.expect_probe_ch2,
            "coupling_ch1": args.expect_coupling_ch1,
            "coupling_ch2": args.expect_coupling_ch2,
            "scale_tolerance_pct": args.scale_tolerance_pct,
            "allow_display_off": args.allow_display_off,
        }
        opts = {"expect": expect, "rshunt": args.rshunt,
                "points": [x.strip() for x in args.points.split(",") if x.strip()],
                "firmware_sha256": args.firmware_sha256,
                "map_crc32": args.map_crc32}
        try:
            sc = ScopeAdapter()
        except Exception as e:  # noqa: BLE001 — прибор недоступен: валидный BLOCKED
            print("SCOPE_CONTROL_BLOCKED: TAO3104A недоступен (%s)" % e)
            report = {"schema": "shunt-cal-campaign-v1", "phase": "BLOCKED",
                      "verdict": SCOPE_CONTROL_BLOCKED,
                      "verdict_reason": "scope_open_failed",
                      "capability_gate": {"verdict": SCOPE_CONTROL_BLOCKED,
                                          "steps": [{"step": "usb_open", "ok": False,
                                                     "detail": str(e)}]},
                      "records": [], "started": utc_now(), "finished": utc_now()}
            out.mkdir(parents=True, exist_ok=True)
            write_json(out / "campaign_report.json", report)
            return 3
        stm32 = None
        try:
            if args.port:
                stm32 = Stm32Adapter(args.port, args.baud, out / "uart_session.log")
            report = run_campaign(sc, stm32, out, opts)
        finally:
            try:
                sc.close()
            except Exception:  # noqa: BLE001
                pass
            if stm32 is not None:
                stm32.close()
        return 0 if report["verdict"] == "RECORDED" else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
