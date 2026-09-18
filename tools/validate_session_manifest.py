#!/usr/bin/env python3
"""Валидатор операторского манифеста сессии M-подбора (TZ-02, шаг c).

Машинно-проверяемая часть протокола `docs/TZ2_M_TUNING_SESSION.md`.
Fail-closed: любое нарушение правила → rc=1 и явный список нарушений с ID правила.

Правила (ID фигурируют в протоколе и в отчётах сессии):

  S1  схема и версия манифеста
  S2  сессия: id, дата, оба оператора
  S3  firmware_sha256 сессии задан и валиден
  S4  утверждение safety-owner (id/дата/подпись-ссылка) записано
  S5  живая identity зафиксирована вместе с файлом-источником
  B1  в сессии есть ровно один baseline-вариант (M0-rebased)
  B2  у каждого burst уникальный run_id
  B3  обязательные поля burst заполнены и в правильном формате
  B4  firmware_sha256 совпадает у ВСЕХ burst'ов и с сессией (неизменность firmware)
  B5  base_map_id/base_map_crc32 одинаковы у всех burst'ов (одна база сравнения)
  B6  baseline: variant_map_crc32 == base_map_crc32, variant_id == M0-rebased
  B7  не-baseline: variant_map_crc32 != base_map_crc32 и уникален (меняется только M)
  B8  одинаковые конверт и процедура у всех burst'ов (vbus_target, current_limit,
      burst_duration, preflight/postflight-набор) — «изменяется только M»
  B9  пауза перед burst >= утверждённого минимума
  B10 параметры burst внутри утверждённого envelope
  B11 map load подтверждён (@MAP:LOAD:OK) и run_id ACK получен
  B12 stop-gate не сработал (иначе burst недействителен)
  B13 если был BREAK — snapshot архивирован, >= 2 чтений, с sha256
  B14 не-baseline burst требует анализа предыдущего варианта (причинная цепочка)
  B15 timestamps валидны и start < end
  B16 не-baseline burst требует замороженного baseline (`baseline_frozen`: file + sha256);
      M1 не проектируется, пока M0 не зафиксирован как baseline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "tz2-session-manifest-1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CRC_RE = re.compile(r"^0x[0-9a-fA-F]{8}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})$")

# Утверждённый конверт первой energize-сессии (PROPOSED, требует подписи safety-owner)
ENVELOPE = {"vbus_min_mv": 24000, "vbus_max_mv": 36000,
            "current_limit_ma": 1000, "burst_max_ms": 1000, "pause_min_ms": 5000}

BASELINE_VARIANT_ID = "M0-rebased"

SESSION_REQUIRED = ("session_id", "date_utc", "operator1", "operator2",
                    "firmware_sha256", "envelope", "live_identity")
ENVELOPE_REQUIRED = ("vbus_min_mv", "vbus_max_mv", "current_limit_ma",
                     "burst_max_ms", "pause_min_ms", "safety_owner_approval")
BURST_REQUIRED = ("run_id", "variant_id", "base_map_id", "base_map_crc32", "variant_map_id",
                  "variant_map_crc32", "artifact_sha256", "firmware_sha256",
                  "timestamp_start", "timestamp_end", "vbus_target_mv", "current_limit_ma",
                  "burst_duration_ms", "pause_before_ms", "operator1", "operator2",
                  "telemetry", "map_load_ok", "run_id_ack", "stop_gate")
TELEMETRY_REQUIRED = ("raw_log", "preflight", "postflight", "identity_file", "artifact_file")
PREFLIGHT_REQUIRED = ("sysinfo", "p?", "pdump", "enc", "mapcap identity")


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, rule: str, msg: str) -> None:
        self.items.append(f"[{rule}] {msg}")

    def __bool__(self) -> bool:
        return bool(self.items)


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate(manifest: dict) -> Problems:
    p = Problems()
    if not isinstance(manifest, dict):
        p.add("S1", "манифест не является объектом JSON")
        return p
    if manifest.get("schema_version") != SCHEMA_VERSION:
        p.add("S1", f"schema_version: ожидается {SCHEMA_VERSION!r}, получено "
                    f"{manifest.get('schema_version')!r}")

    s = manifest.get("session") or {}
    if not isinstance(s, dict):
        p.add("S2", "секция session отсутствует или не объект")
        s = {}
    for key in SESSION_REQUIRED:
        if key not in s or s.get(key) in (None, "", [], {}):
            p.add("S2", f"session.{key}: обязательное поле не заполнено")
    for key in ("operator1", "operator2"):
        if s.get(key) and not isinstance(s[key], str):
            p.add("S2", f"session.{key}: ожидается строка")
    if s.get("operator1") and s.get("operator2") and s["operator1"] == s["operator2"]:
        p.add("S2", "session: требуется второй оператор (operator2 совпадает с operator1)")

    fw = s.get("firmware_sha256")
    if not fw or not SHA256_RE.match(str(fw)):
        p.add("S3", "session.firmware_sha256: ожидается 64 hex-символа")
    if s.get("date_utc") and not TS_RE.match(str(s["date_utc"])):
        p.add("S2", "session.date_utc: ожидается ISO-8601 (UTC)")

    env = s.get("envelope") or {}
    if not isinstance(env, dict):
        p.add("S4", "session.envelope отсутствует или не объект")
        env = {}
    for key in ENVELOPE_REQUIRED:
        if key not in env or env.get(key) in (None, "", []):
            p.add("S4", f"session.envelope.{key}: не заполнено")
    if not env.get("safety_owner_approval"):
        p.add("S4", "session.envelope.safety_owner_approval: без утверждения safety-owner "
                    "сессия не запускается (PROPOSED EXPERIMENTAL ENVELOPE)")

    li = s.get("live_identity") or {}
    if not isinstance(li, dict) or not li:
        p.add("S5", "session.live_identity: не зафиксирована живая identity")
    else:
        for key in ("source", "captured_utc", "file", "values"):
            if not li.get(key):
                p.add("S5", f"session.live_identity.{key}: не заполнено")

    bursts = manifest.get("bursts")
    if not isinstance(bursts, list) or not bursts:
        p.add("B1", "bursts: список пуст")
        return p

    baseline = [b for b in bursts
                if isinstance(b, dict) and b.get("variant_id") == BASELINE_VARIANT_ID]
    if len(baseline) != 1:
        p.add("B1", f"в сессии должен быть ровно один baseline {BASELINE_VARIANT_ID}, "
                    f"найдено {len(baseline)}")

    seen_run_ids: set[str] = set()
    base_ref = None
    variant_crcs: dict[str, str] = {}
    proc_signature = None

    for i, b in enumerate(bursts):
        tag = f"burst[{i}]"
        if not isinstance(b, dict):
            p.add("B3", f"{tag}: не объект")
            continue
        rid = b.get("run_id")
        tag = f"{tag}({rid or 'без run_id'})"
        for key in BURST_REQUIRED:
            if key not in b or b.get(key) in (None, ""):
                p.add("B3", f"{tag}: поле {key} не заполнено")
        if rid:
            if rid in seen_run_ids:
                p.add("B2", f"{tag}: run_id повторяется")
            seen_run_ids.add(str(rid))

        for key in ("artifact_sha256", "firmware_sha256"):
            v = b.get(key)
            if v and not SHA256_RE.match(str(v)):
                p.add("B3", f"{tag}: {key} не является sha256 (64 hex)")
        for key in ("base_map_crc32", "variant_map_crc32"):
            v = b.get(key)
            if v and not CRC_RE.match(str(v)):
                p.add("B3", f"{tag}: {key} не в формате 0xXXXXXXXX")
        for key in ("timestamp_start", "timestamp_end"):
            v = b.get(key)
            if v and not TS_RE.match(str(v)):
                p.add("B15", f"{tag}: {key} не ISO-8601")
        if b.get("timestamp_start") and b.get("timestamp_end"):
            try:
                t0 = datetime.fromisoformat(str(b["timestamp_start"]).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(b["timestamp_end"]).replace("Z", "+00:00"))
                if not t0 < t1:
                    p.add("B15", f"{tag}: timestamp_start должен быть раньше timestamp_end")
            except ValueError:
                pass

        if fw and b.get("firmware_sha256") and b["firmware_sha256"] != fw:
            p.add("B4", f"{tag}: firmware_sha256 отличается от сессионного — firmware обязан быть "
                        f"неизменным между вариантами")

        if base_ref is None and b.get("base_map_crc32"):
            base_ref = (b.get("base_map_id"), b.get("base_map_crc32"))
        elif base_ref and b.get("base_map_crc32"):
            if (b.get("base_map_id"), b.get("base_map_crc32")) != base_ref:
                p.add("B5", f"{tag}: base_map_id/base_map_crc32 отличаются от остальных — "
                            f"база сравнения обязана быть общей")

        if b.get("variant_id") == BASELINE_VARIANT_ID:
            if b.get("variant_map_crc32") != b.get("base_map_crc32"):
                p.add("B6", f"{tag}: у baseline ({BASELINE_VARIANT_ID}) variant_map_crc32 обязан "
                            f"совпадать с base_map_crc32")
        else:
            if b.get("variant_map_crc32") and b["variant_map_crc32"] == b.get("base_map_crc32"):
                p.add("B7", f"{tag}: вариант не отличается от базовой карты (CRC совпал)")
            c = b.get("variant_map_crc32")
            if c:
                if c in variant_crcs:
                    p.add("B7", f"{tag}: variant_map_crc32 совпадает с {variant_crcs[c]} — "
                                f"варианты обязаны различаться")
                else:
                    variant_crcs[c] = str(rid)

        tel = b.get("telemetry") or {}
        if not isinstance(tel, dict):
            p.add("B8", f"{tag}: telemetry не объект")
            tel = {}
        for key in TELEMETRY_REQUIRED:
            if not tel.get(key):
                p.add("B8", f"{tag}: telemetry.{key} не заполнено")
        for key in ("preflight", "postflight"):
            lst = tel.get(key)
            if isinstance(lst, list):
                missing = [x for x in PREFLIGHT_REQUIRED if x not in lst]
                if missing and key == "preflight":
                    p.add("B8", f"{tag}: telemetry.preflight без обязательных команд: {missing}")

        sig = (b.get("vbus_target_mv"), b.get("current_limit_ma"), b.get("burst_duration_ms"),
               tuple(tel.get("preflight") or ()), tuple(tel.get("postflight") or ()))
        if proc_signature is None:
            proc_signature = sig
        elif sig != proc_signature and not any(x is None for x in sig):
            p.add("B8", f"{tag}: конверт/процедура отличаются от остальных burst'ов — "
                        f"между вариантами изменяется только M")

        pause = b.get("pause_before_ms")
        if pause is not None and _is_pos_int(pause) is False and not isinstance(pause, int):
            p.add("B9", f"{tag}: pause_before_ms должен быть целым")
        if isinstance(pause, int) and not isinstance(pause, bool):
            if pause < int(env.get("pause_min_ms") or ENVELOPE["pause_min_ms"]):
                p.add("B9", f"{tag}: pause_before_ms={pause} < минимума "
                            f"{env.get('pause_min_ms', ENVELOPE['pause_min_ms'])}")
        vt = b.get("vbus_target_mv")
        if isinstance(vt, int) and not isinstance(vt, bool):
            lo = int(env.get("vbus_min_mv") or ENVELOPE["vbus_min_mv"])
            hi = int(env.get("vbus_max_mv") or ENVELOPE["vbus_max_mv"])
            if not lo <= vt <= hi:
                p.add("B10", f"{tag}: vbus_target_mv={vt} вне конверта [{lo};{hi}]")
        for key in ("current_limit_ma", "burst_duration_ms"):
            v = b.get(key)
            lim = (env.get("current_limit_ma") if key == "current_limit_ma"
                   else env.get("burst_max_ms")) or ENVELOPE[
                       "current_limit_ma" if key == "current_limit_ma" else "burst_max_ms"]
            if isinstance(v, int) and not isinstance(v, bool) and v > int(lim):
                p.add("B10", f"{tag}: {key}={v} превышает утверждённый предел {lim}")

        if b.get("map_load_ok") is not True:
            p.add("B11", f"{tag}: map_load_ok != true (нет подтверждения @MAP:LOAD:OK)")
        if b.get("run_id_ack") is not True:
            p.add("B11", f"{tag}: run_id_ack != true (нет @RUN:ID ACK)")

        sg = b.get("stop_gate") or {}
        if not isinstance(sg, dict) or sg.get("triggered") is not False:
            p.add("B12", f"{tag}: stop-gate сработал (или не заполнен) — burst недействителен, "
                         f"требуется перезапись")

        brk = b.get("break_snapshot_archived")
        if brk:
            if not isinstance(brk, dict) or not brk.get("file") or not brk.get("sha256"):
                p.add("B13", f"{tag}: BREAK-снимок архивирован не полностью (нужны file и sha256)")
            elif not SHA256_RE.match(str(brk.get("sha256"))):
                p.add("B13", f"{tag}: break_snapshot_archived.sha256 не sha256")
            elif int(brk.get("readings") or 0) < 2:
                p.add("B13", f"{tag}: BREAK-снимок обязан иметь >= 2 чтений до reset/clear")

        if b.get("variant_id") != BASELINE_VARIANT_ID:
            pv = b.get("previous_variant_comparison") or {}
            if not isinstance(pv, dict) or pv.get("analyzed") is not True:
                p.add("B14", f"{tag}: нет анализа предыдущего варианта — запрещено идти "
                             f"M1 → M2 → M3 без промежуточного сравнения")
            if isinstance(pv, dict) and pv.get("variant_id") == b.get("variant_id"):
                p.add("B14", f"{tag}: previous_variant_comparison ссылается на сам вариант")
            bf = b.get("baseline_frozen") or {}
            if not isinstance(bf, dict) or not bf.get("file") or not bf.get("sha256"):
                p.add("B16", f"{tag}: нет записи о замороженном baseline (baseline_frozen: "
                             f"file + sha256) — M1 не проектируется до M0 baseline frozen")
            elif not SHA256_RE.match(str(bf.get("sha256"))):
                p.add("B16", f"{tag}: baseline_frozen.sha256 не sha256")
            elif bf.get("baseline_crc32") and b.get("base_map_crc32") and \
                    str(bf["baseline_crc32"]).lower() != str(b["base_map_crc32"]).lower():
                p.add("B16", f"{tag}: baseline_frozen.baseline_crc32="
                             f"{bf['baseline_crc32']} не совпадает с base_map_crc32="
                             f"{b['base_map_crc32']}")

    # B14 (продолжение): предыдущий вариант должен быть реально предыдущим по списку
    order = [b.get("variant_id") for b in bursts if isinstance(b, dict)]
    for i, b in enumerate(bursts):
        if not isinstance(b, dict) or b.get("variant_id") == BASELINE_VARIANT_ID or i == 0:
            continue
        pv = b.get("previous_variant_comparison") or {}
        if isinstance(pv, dict) and pv.get("variant_id") and pv["variant_id"] != order[i - 1]:
            p.add("B14", f"burst[{i}]({b.get('run_id')}): анализ относится к "
                         f"{pv['variant_id']}, а предыдущим по протоколу был {order[i - 1]}")
    return p


def check_bundle(manifest: dict, bundle_dir: Path) -> Problems:
    p = Problems()
    for i, b in enumerate(manifest.get("bursts") or []):
        if not isinstance(b, dict):
            continue
        tel = b.get("telemetry") or {}
        for key in ("raw_log", "identity_file", "artifact_file"):
            rel = tel.get(key)
            if not rel:
                continue
            if not (bundle_dir / rel).exists():
                p.add("C1", f"burst[{i}]({b.get('run_id')}): нет файла telemetry.{key} = {rel}")
        brk = b.get("break_snapshot_archived")
        if isinstance(brk, dict) and brk.get("file"):
            if not (bundle_dir / brk["file"]).exists():
                p.add("C1", f"burst[{i}]({b.get('run_id')}): нет файла BREAK-снимка {brk['file']}")
        for key in ("artifact_file", "identity_file"):
            rel = tel.get(key)
            if rel:
                path = bundle_dir / rel
                if path.exists() and key == "artifact_file":
                    import hashlib
                    h = hashlib.sha256(path.read_bytes()).hexdigest()
                    if b.get("artifact_sha256") and h != b["artifact_sha256"]:
                        p.add("C2", f"burst[{i}]({b.get('run_id')}): sha256 артефакта на диске "
                                    f"{h[:16]}… не совпал с манифестом")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Валидатор манифеста сессии M-подбора (TZ-02)")
    ap.add_argument("manifest", help="session_manifest.json")
    ap.add_argument("--bundle", default=None, help="каталог с файлами сессии (проверка наличия)")
    args = ap.parse_args()

    path = Path(args.manifest)
    if not path.exists():
        print(f"BLOCKED: нет файла {path}", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        print(f"BLOCKED: манифест не разобран как JSON: {exc}", file=sys.stderr)
        return 1

    problems = validate(manifest)
    if args.bundle:
        problems.items.extend(check_bundle(manifest, Path(args.bundle)).items)

    if problems:
        print(f"BLOCKED: нарушений {len(problems.items)}")
        for item in problems.items:
            print(f"  {item}")
        return 1

    n = len(manifest.get("bursts") or [])
    print(f"GATE PASS: манифест валиден (bursts={n}, baseline={BASELINE_VARIANT_ID}, "
          f"firmware={str(manifest['session']['firmware_sha256'])[:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
