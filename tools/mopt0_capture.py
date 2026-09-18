#!/usr/bin/env python3
"""M-OPT-0 no-HV baseline campaign automation for the bench PC (ПК-3).

=== HONESTY CONTRACT (read before changing) ===============================
M-OPT-0 records the behaviour of the *existing* board map M0 on the physical
bench, with the DC-link physically disconnected. This tool only records what
the firmware actually emits on UART and only reports verdicts it can prove:

  * raw UART bytes are written verbatim into per-run logs; nothing is injected,
    re-ordered, prettified or reconstructed;
  * ADC/VBUS numbers are never synthesised — the no-HV gate is computed from the
    real ``a`` responses of the run;
  * a run is ``PASS`` only when the no-HV gate AND the identity evidence
    (``run_id``, ``map_id``, ``map_crc32``) are present in the real UART stream
    and the firmware/source SHAs are recorded;
  * when the firmware does not emit that identity telemetry, the run is
    ``BLOCKED_MISSING_IDENTITY`` and the campaign can never reach ``PASS`` —
    ``--identity-source folder`` only documents that the run id comes from the
    artifact folder; it does NOT substitute for firmware evidence.

The hosted counterpart of this file is ``tests/m_opt_0_nohv_test.c``; it proves
the fail-closed admission/terminal logic and is explicitly NOT this baseline.

Usage (PowerShell on ПК-3):
    py -3 tools\\mopt0_capture.py list-ports
    py -3 tools\\mopt0_capture.py run --port COM15 --campaign D:\\campaign_raw\\mopt0_<UTC> `
        --firmware-bin build\\firmware.bin --confirm-dc-link-disconnected `
        --confirm-pc4-zero --confirm-sd-high
    py -3 tools\\mopt0_capture.py verify --campaign D:\\campaign_raw\\mopt0_<UTC>
    py -3 tools\\mopt0_capture.py run --simulate identity-absent `
        --campaign D:\\campaign_raw\\mopt0_sim_absent --firmware-bin build\\firmware.bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - exercised only without pyserial
    serial = None


# ── Statistical no-HV VBUS contract (TZ_BENCH_TEST2_STATISTICAL_NOHV_GATE.md) ──
DEFAULT_VBUS_SAMPLES = 20
NOHV_RAW_VBUS_MEDIAN_MAX = 9
NOHV_RAW_VBUS_HARD_LIMIT = 200

# src/adc.c: bipolar 12-bit shunt channels are usable strictly away from rails.
ADC_RAW_SAT_LOW = 1
ADC_RAW_SAT_HIGH = 4094

DEFAULT_RUNS = 5
DEFAULT_RUN_PREFIX = "M0-R"
IDENTITY_SOURCES = ("firmware", "folder")
# The bench firmware contract (ai5/m-opt-0-telemetry): `run=<id>` (1..23 chars,
# [A-Za-z0-9_.-]) answers `@RUN:ID=<id>`; `@FOC` then carries
# run_id/map_id/map_crc32 for every sample. `{run_id}` is substituted per run.
DEFAULT_RUN_ID_COMMAND = "run={run_id}"

ADC_RAW_RE = re.compile(
    r"@ADC:I1=(?P<i1>\d+):I2=(?P<i2>\d+):Ires=(?P<ires>\d+):VBUS=(?P<raw_vbus>\d+)")
CAL_RE = re.compile(r"@ADC:CAL:offset_i1=(?P<i1>\d+):offset_i2=(?P<i2>\d+)")
CAL_FAIL = "@ADC:CAL:FAIL"
ENC_ERR_RE = re.compile(r"(?:^|:)err=(?P<err>-?\d+)(?=$|:|[\r\n])")
STATUS_RE = re.compile(
    r"@MC:STATUS:state=(?P<state>-?\d+):term=(?P<term>-?\d+)"
    r":cap=(?P<cap>\d+):frames=(?P<frames>\d+):dropped=(?P<dropped>\d+)"
    r":periods=(?P<periods>\d+):avail=(?P<avail>\d+)")
# `sysinfo` exposes the transport counters once the @FOC budget package is in the
# image: uart_drp (packets dropped) and uart_trunc (packets rejected as
# oversized). Absent on older images -> reported as NOT_REPORTED, not as a pass.
UART_DRP_RE = re.compile(r"uart_drp=(?P<v>\d+)")
UART_TRUNC_RE = re.compile(r"uart_trunc=(?P<v>\d+)")
# RM0440 (TIM1/TIM8, advanced-control timers): TIMx_BDTR bit 15 = MOE (main
# output enable), TIMx_CR1 bit 0 = CEN. The production image carries NO
# `MOE=` / `default_deny=` token: `p?` answers
# `@PWM:CR1=<dec>:CCER=<dec>:BDTR=<dec>:CNT=<dec>` (BDTR in DECIMAL) and `pdump`
# answers `@PWM:FULL:...:T1:...:BDTR=0x<hex>:CCER=0x<hex>:CR1=0x<hex>:CNT=..:T8:...`
# (BDTR in HEX, both timers). The bit decode below is the one already accepted
# for the Test №2 pre-flight (tools/bench_test2_preflight.py, packet
# ai4/bench-test2-preflight-pwm-moe): decode the bit, do not look for a marker.
TIM_BDTR_MOE_MASK = 0x8000
TIM_CR1_CEN_MASK = 0x0001
BDTR_RE = re.compile(r"\bBDTR\s*[=:]\s*(?:0x(?P<hex>[0-9A-Fa-f]+)|(?P<dec>\d+))")
CCER_RE = re.compile(r"\bCCER\s*[=:]\s*(?:0x(?P<hex>[0-9A-Fa-f]+)|(?P<dec>\d+))")
CR1_RE = re.compile(r"\bCR1\s*[=:]\s*(?:0x(?P<hex>[0-9A-Fa-f]+)|(?P<dec>\d+))")
PWM_TIMER_SECTION_RE = re.compile(r":(?=T[18]:)")
PWM_TIMER_LABEL_RE = re.compile(r"(?:^|:)T(?P<t>[18]):")
# The only answer of an image built without the mapcap command (src/cli.c:345).
CLI_UNKNOWN_RE = re.compile(r"(?:^|[\r\n])\s*unknown\s*(?=$|[\r\n])")
SYSINFO_RE = re.compile(r"@SYSINFO:(?P<body>[^\r\n]*)")

# Identity telemetry the physical baseline MUST carry. Field spellings follow
# the accepted @FOC/@RUN contract; both "@RUN:ID=<run>" and "run_id=<run>" are
# accepted so a firmware-side rename does not silently block the campaign.
RUN_ID_PATTERNS = (
    re.compile(r"@RUN:ID=(?P<v>[A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:^|[:\s])run_id=(?P<v>[A-Za-z0-9_.\-]+)"),
)
MAP_ID_PATTERNS = (
    re.compile(r"(?:^|[:\s])map_id=(?P<v>[A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:^|[:\s])map=(?P<v>[A-Za-z0-9_.\-]+)"),
)
MAP_CRC_PATTERNS = (
    re.compile(r"map_crc32=(?P<v>(?:0x)?[0-9A-Fa-f]{1,8})"),
    re.compile(r"map_crc=(?P<v>(?:0x)?[0-9A-Fa-f]{1,8})"),
)

LOG_INTEGRITY_MARKERS = ("line overflow", "@UART:TRUNC", "@UART:DROP")


class MoptError(RuntimeError):
    """Expected validation, transport or evidence failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def median(values: Sequence[int]) -> Optional[int]:
    """Integer median; lower median for even length; None when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


# ── Pure evidence parsers / evaluators (unit tested) ───────────────────────

def parse_adc_raws(texts: Sequence[str]) -> Optional[list[dict[str, int]]]:
    """Parse every ``a`` response; None when any response is malformed.

    A malformed sample is fail-closed for the whole observation: the statistical
    no-HV gate must never silently drop a sample.
    """
    parsed: list[dict[str, int]] = []
    for text in texts:
        matches = list(ADC_RAW_RE.finditer(text))
        if not matches:
            return None
        parsed.append({k: int(v) for k, v in matches[-1].groupdict().items()})
    return parsed


def evaluate_no_hv_gate(samples: Optional[Sequence[dict[str, int]]]) -> dict[str, Any]:
    """Statistical no-HV proof: median ≤ 9 counts, hard cap 200, rails clear."""
    if not samples:
        return {
            "verdict": "FAIL", "samples": 0, "raw_vbus_median": None,
            "raw_vbus_max": None,
            "reason": "no parseable ADC samples",
        }
    raw_vbus = [sample["raw_vbus"] for sample in samples]
    median_vbus = median(raw_vbus)
    max_vbus = max(raw_vbus)
    i1_usable = all(ADC_RAW_SAT_LOW < sample["i1"] < ADC_RAW_SAT_HIGH for sample in samples)
    i2_usable = all(ADC_RAW_SAT_LOW < sample["i2"] < ADC_RAW_SAT_HIGH for sample in samples)
    checks = {
        "raw_vbus_median_nohv": median_vbus is not None and median_vbus <= NOHV_RAW_VBUS_MEDIAN_MAX,
        "raw_vbus_max_hard_limit": max_vbus <= NOHV_RAW_VBUS_HARD_LIMIT,
        "i1_away_from_rails": i1_usable,
        "i2_away_from_rails": i2_usable,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "verdict": "PASS" if not failed else "FAIL",
        "samples": len(samples),
        "raw_vbus_median": median_vbus,
        "raw_vbus_max": max_vbus,
        "raw_vbus_min": min(raw_vbus),
        "contract": {
            "median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
            "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT,
            "adc_sat_low": ADC_RAW_SAT_LOW,
            "adc_sat_high": ADC_RAW_SAT_HIGH,
        },
        "checks": checks,
        "failed_checks": failed,
        "reason": None if not failed else "no-HV gate failed: " + ", ".join(failed),
    }


def extract_identity(text: str) -> dict[str, Any]:
    """Extract the firmware-emitted identity fields from a raw run log."""
    def first(patterns: Sequence[re.Pattern[str]]) -> Optional[str]:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group("v")
        return None

    identity = {
        "run_id": first(RUN_ID_PATTERNS),
        "map_id": first(MAP_ID_PATTERNS),
        "map_crc32": first(MAP_CRC_PATTERNS),
    }
    identity["missing"] = sorted(key for key, value in identity.items() if not value)
    identity["present"] = not identity["missing"]
    return identity


LOG_MARKER_RE = re.compile(r"^\[[^\]]+\]\s+(?P<label>[A-Z_]+)\s*$")

# Command → semantic evidence key used by evaluate_run(). `a` samples are
# numbered individually so the offline verifier can recompute the gate.
COMMAND_EVIDENCE_KEYS = {
    "sysinfo": "sysinfo",
    "p?": "pwm_before",
    "pdump": "pdump_before",
    "c": "calibration",
    "enc": "encoder",
    "mapcap status": "status_before",
}
RUN_ID_COMMAND_PREFIX = "run="


def evidence_key_for_command(command: str) -> Optional[str]:
    """Map an exact command to its semantic evidence key (None = not evidence)."""
    if command.startswith(RUN_ID_COMMAND_PREFIX):
        return "run_id_set"
    return COMMAND_EVIDENCE_KEYS.get(command)


def rebuild_responses_from_log(text: str) -> dict[str, str]:
    """Rebuild the command→response mapping from a raw timestamped UART log.

    The transport writes ``[<utc>] TX`` / ``[<utc>] RX`` marker lines with the
    payload on the following line(s), so the pairing is recoverable byte-exactly
    from the retained log — the offline verifier needs no side channel.
    """
    responses: dict[str, str] = {}
    state: dict[str, Any] = {"label": None, "command": None, "buffer": []}
    adc_counter = [0]

    def flush() -> None:
        label = state["label"]
        buffer = state["buffer"]
        if label == "TX" and buffer:
            state["command"] = "".join(buffer).strip()
        elif label == "RX" and buffer and state["command"] is not None:
            payload = "".join(buffer)
            command = state["command"]
            if command == "a":
                responses[f"adc_{adc_counter[0]:03d}"] = payload
                adc_counter[0] += 1
            else:
                key = evidence_key_for_command(command)
                if key is not None:
                    responses[key] = responses.get(key, "") + payload
        state["label"] = None
        state["buffer"] = []

    for line in text.splitlines(keepends=True):
        match = LOG_MARKER_RE.match(line.rstrip("\r\n"))
        if match:
            flush()
            state["label"] = match.group("label")
            continue
        if state["label"] is not None:
            state["buffer"].append(line)
    flush()
    return responses


def check_log_integrity(text: str, run_id: str) -> list[str]:
    """Detect evidence-loss markers and mismatched run labels in a raw log."""
    issues: list[str] = []
    if not text.strip():
        issues.append("log is empty")
    for marker in LOG_INTEGRITY_MARKERS:
        if marker in text:
            issues.append(f"evidence-loss marker present: {marker}")
    for match in RUN_ID_PATTERNS[0].finditer(text):
        if match.group("v") != run_id:
            issues.append(f"foreign run id in log: {match.group('v')}")
            break
    return issues


# ── Контракт приёмки по потоку @FOC ──────────────────────────────────────
# Read-only no-HV baseline требует, чтобы в КАЖДОЙ семплированной строке
# @FOC держались safety/state-поля: FAULT/FAULT_R (PROTECT_IsFault()/
# PROTECT_GetFaultReason(), main.c), RUN (FOC_IsRunning()), FAIL
# (FOC_GetStartupFailReason()), STATE (FOC_GetState()). Защёлкнутый
# PROTECT_FAULT_HARDWARE_BREAK не оставляет в логе НИЧЕГО кроме этих полей
# (ISR молчит), поэтому проверка обязана быть построчной, а не в начале/конце.
FOC_ROW_RE = re.compile(
    r"@FOC:t=(?P<t>\d+):run_id=(?P<run>[A-Za-z0-9_.\-]*):.*?"
    r"VBUS=(?P<vbus>-?\d+):STATE=(?P<state>-?\d+):.*?"
    r"FAULT=(?P<fault>-?\d+):FAULT_R=(?P<fault_r>-?\d+):FAIL=(?P<fail>\d+):"
    r"RUN=(?P<run_flag>\d+):em_stop1=(?P<es1>\d+):em_stop2=(?P<es2>\d+)")
FOC_START_RE = re.compile(r"@FOC:t=")
FOC_FIELD_RES: dict[str, re.Pattern[str]] = {
    "t": re.compile(r"@FOC:t=(\d+):"),
    "run_id": re.compile(r":run_id=([A-Za-z0-9_.\-]*)"),
    "vbus": re.compile(r":VBUS=(-?\d+)"),
    "state": re.compile(r":STATE=(-?\d+)"),
    "fault": re.compile(r":FAULT=(-?\d+)"),
    "fault_r": re.compile(r":FAULT_R=(-?\d+)"),
    "fail": re.compile(r":FAIL=(\d+)"),
    "run_flag": re.compile(r":RUN=(\d+)"),
    "es1": re.compile(r":em_stop1=(\d+)"),
    "es2": re.compile(r":em_stop2=(\d+)"),
}
RUN_ID_ACK_TOKEN = "@RUN:ID="
# Имена причин дублируют enum `ProtectFaultReason` (src/protect.h); расхождение
# ловится тестом-drift guard'ом, читающим сам заголовок.
PROTECT_FAULT_NAMES = {
    0: "NONE", 1: "OVERCURRENT", 2: "VBUS_HIGH", 3: "VBUS_LOW", 4: "ADC_OVERRUN",
    5: "ADC_QUEUE_OVERRUN", 6: "ADC_DESYNC", 7: "ADC_TIMEOUT", 8: "SAMPLE_WINDOW",
    9: "CURRENT_MAP", 10: "FRAME_COPY", 11: "CAPTURE_TIMEOUT",
    12: "CAPTURE_BUFFER_OVERFLOW", 13: "CAPTURE_LIMIT", 14: "CAPTURE_ABORT",
    15: "CAPTURE_ADC", 16: "CAPTURE_TRIGGER", 17: "CAPTURE_INTERLOCK",
    18: "HARDWARE_BREAK",
}


def _token_int(match: Optional[re.Match[str]]) -> Optional[int]:
    """Decode a `0x…` hex or a plain decimal register token."""
    if match is None:
        return None
    raw_hex = match.group("hex")
    return int(raw_hex, 16) if raw_hex is not None else int(match.group("dec"), 10)


def parse_pwm_registers(text: str) -> list[dict[str, Any]]:
    """Decode BDTR/CCER/CR1 of every timer section of a `p?`/`pdump` answer.

    `pdump` answers `@PWM:FULL:` with a T1 and a T8 section; `p?` carries TIM1
    only. BDTR is printed in DECIMAL by `p?` and in HEX by the dump — both are
    decoded. Sections without a BDTR token are dropped: nothing to decode there.
    """
    records: list[dict[str, Any]] = []
    for section in PWM_TIMER_SECTION_RE.split(text):
        bdtr_match = BDTR_RE.search(section)
        if bdtr_match is None:
            continue
        label = PWM_TIMER_LABEL_RE.search(section)
        bdtr = _token_int(bdtr_match)
        ccer = _token_int(CCER_RE.search(section))
        cr1 = _token_int(CR1_RE.search(section))
        records.append({
            "timer": f"TIM{label.group('t')}" if label else "TIM1",
            "bdtr": bdtr,
            "bdtr_hex": f"0x{bdtr:08X}" if bdtr is not None else None,
            "moe": int((bdtr & TIM_BDTR_MOE_MASK) != 0) if bdtr is not None else None,
            "ccer": ccer,
            "ccer_hex": f"0x{ccer:08X}" if ccer is not None else None,
            "cr1": cr1,
            "cen": int((cr1 & TIM_CR1_CEN_MASK) != 0) if cr1 is not None else None,
        })
    return records


def decode_pwm_state(responses: dict[str, Any]) -> dict[str, Any]:
    """PWM shutdown evidence decoded from the DIRECT `p?`/`pdump` answers only.

    Decoding is restricted to those two direct responses on purpose: a stray
    `@FAIL:PWM:MOE=…` line elsewhere in the log must never satisfy this gate.
    Fail-closed: no BDTR token -> MOE is not proven; no CCER token next to it ->
    the output state is not proven.
    """
    records: list[dict[str, Any]] = []
    sources: dict[str, list[str]] = {}
    for key in ("pdump_before", "pwm_before"):
        text = responses.get(key) or ""
        parsed = parse_pwm_registers(text) if text else []
        sources[key] = [record["timer"] for record in parsed]
        records.extend(parsed)
    timers = {record["timer"] for record in records}
    moe_known = bool(records)
    moe_low = moe_known and all(record["moe"] == 0 for record in records)
    ccer_known = bool(records) and all(record["ccer"] is not None for record in records)
    outputs_disabled = ccer_known and all(record["ccer"] == 0 for record in records)
    note = None
    if not moe_known:
        note = ("no BDTR token in the direct `p?`/`pdump` answers: MOE cannot be "
                "proven -> fail-closed")
    elif not ccer_known:
        note = ("no CCER token next to BDTR: the output state cannot be proven -> "
                "fail-closed")
    elif "TIM8" not in timers:
        note = ("only TIM1 was read by this command sequence: TIM8 MOE is not "
                "observed by the read-only pre-flight")
    return {
        "timers": records,
        "sources": sources,
        "moe_known": moe_known,
        "moe_low": moe_low,
        "ccer_known": ccer_known,
        "outputs_disabled": outputs_disabled,
        "note": note,
    }


def evaluate_mapcap_before(response: str) -> dict[str, Any]:
    """MapCapture state BEFORE the run, with an explicit applicability scope.

    A production image carries no mapcap command at all (`src/cli.c` answers
    `unknown`): the gate is then N/A with the reason recorded, and the image is
    still required to prove that no `@MC:REC:` ever appeared. A commissioning
    image (`OEW_MAP_CAPTURE=1`) answers `@MC:STATUS:` and keeps the gate
    MANDATORY. Anything else is unparseable evidence -> fail-closed FAIL.
    """
    response = response or ""
    status = parse_status(response)
    if status is not None:
        idle = (status["state"] == 0 and status["term"] == 0 and status["frames"] == 0
                and status["dropped"] == 0 and status["avail"] == 0)
        return {
            "applicability": "MANDATORY",
            "command_present": True,
            "passed": idle,
            "status": status,
            "reason": None if idle else "MapCapture is not IDLE/empty before the run",
        }
    if CLI_UNKNOWN_RE.search(response) is not None:
        return {
            "applicability": "N/A",
            "command_present": False,
            "passed": True,
            "status": None,
            "reason": ("mapcap command absent in this image (CLI answered 'unknown'): "
                       "the gate is N/A for a production image; a commissioning image "
                       "(OEW_MAP_CAPTURE=1) keeps it MANDATORY"),
        }
    return {
        "applicability": "MANDATORY",
        "command_present": None,
        "passed": False,
        "status": None,
        "reason": "mapcap status answer missing or unparseable -> fail-closed",
    }


def parse_foc_rows(log_text: str) -> dict[str, Any]:
    """Разобрать поток @FOC по сегментам строк, ПОЛЕ ЗА ПОЛЕМ.

    Сегмент — от `@FOC:t=` до следующего `@FOC:t=` или до маркера транспорта
    (`\\n[`), чтобы в него не попадал текст следующих команд. Строка, обрезанная
    границей RX-окна транспорта, теряет только ХВОСТ: прочитанные поля (в их
    числе `FAULT`/`FAULT_R`, идущие до `FAIL`/`RUN`/`em_stop`) остаются
    доказательством, а не прочитанные остаются `None` и учитываются в покрытии
    потока. `complete` — прочитаны все поля контракта (для отчёта).
    """
    rows: list[dict[str, Any]] = []
    starts = [match.start() for match in FOC_START_RE.finditer(log_text)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(log_text)
        marker = log_text.find("\n[", start)
        if marker != -1 and marker < end:
            end = marker
        segment = log_text[start:end]
        row: dict[str, Any] = {"pos": start,
                               "complete": FOC_ROW_RE.match(segment) is not None}
        for field, pattern in FOC_FIELD_RES.items():
            match = pattern.search(segment)
            if match is None:
                row[field] = None
            elif field == "run_id":
                row[field] = match.group(1)
            else:
                row[field] = int(match.group(1))
        rows.append(row)
    return {"rows": rows, "segments": len(starts)}


def scope_foc_rows(log_text: str, rows: Sequence[dict[str, Any]],
                   run_id: str) -> dict[str, Any]:
    """Отнести строки @FOC к текущему прогону по прямому ACK `@RUN:ID=<run_id>`.

    Строки ДО ACK принадлежат предыдущей сессии (id в RAM сохраняется между
    прогонами): они не приписываются текущему прогону и не участвуют в его
    identity-evidence и в его safety-гейтах.
    """
    ack_index = log_text.find(RUN_ID_ACK_TOKEN + run_id)
    before = [row for row in rows if ack_index < 0 or row["pos"] < ack_index]
    after = [row for row in rows if ack_index >= 0 and row["pos"] >= ack_index]
    # Граница RX-окна может разрезать ЗНАЧЕНИЕ run_id (напр. `run_id=M0-`), и
    # тогда идентификатор в строке — префикс ожидаемого: это своя строка прогона,
    # а не чужая. Такие строки считаются отдельно и не ломают атрибуцию.
    truncated_ids = sorted({row["run_id"] for row in after
                            if row["run_id"] is not None and row["run_id"] != run_id
                            and run_id.startswith(row["run_id"])}, key=len)
    for index, value in enumerate(truncated_ids):
        if value == "":
            truncated_ids[index] = "<cut>"
    return {
        "ack_found": ack_index >= 0,
        "rows_before_ack": before,
        "rows_after_ack": after,
        "truncated_run_ids_after_ack": truncated_ids,
        "stale_run_ids_before_ack": sorted({row["run_id"] for row in before
                                            if row["run_id"] not in (None, "")}),
        "run_ids_after_ack": sorted({row["run_id"] for row in after
                                     if row["run_id"] not in (None, "")
                                     and row["run_id"] not in truncated_ids}),
    }


def evaluate_foc_stream(log_text: str, run_id: str,
                        expected_safe_state: int = 0) -> tuple[dict[str, Any], dict[str, bool]]:
    """Сводка и построчные safety-гейты по потоку @FOC (fail-closed).

    Гейты применяются к строкам СВОЕГО прогона (после прямого ACK); без ACK
    атрибутировать нечем — гейтится весь поток. Для каждого поля берутся строки,
    где поле ПРОЧИТАНО; если поле не встретилось ни в одной строке, гейт падает
    (доказать нечем), а причина попадает в `evidence_notes`.
    """
    parsed = parse_foc_rows(log_text)
    rows = parsed["rows"]
    scope = scope_foc_rows(log_text, rows, run_id)
    gated = scope["rows_after_ack"] if scope["ack_found"] else rows
    fault_rows = [row for row in gated
                  if row["fault"] is not None and row["fault_r"] is not None
                  and (row["fault"] != 0 or row["fault_r"] != 0)]
    fault_row_any_scope = sum(
        1 for row in rows
        if row["fault"] is not None and row["fault_r"] is not None
        and (row["fault"] != 0 or row["fault_r"] != 0))

    def captured(field: str) -> list[int]:
        return [row[field] for row in gated if row[field] is not None]

    fault_scope = [row for row in gated
                   if row["fault"] is not None and row["fault_r"] is not None]
    with_safety = len(fault_scope)
    missing_fields = [field for field in ("fault", "state", "run_flag", "fail")
                      if not captured(field)]
    fault_reasons = sorted({row["fault_r"] for row in fault_rows})
    vbus = captured("vbus")
    summary = {
        "segments_total": parsed["segments"],
        "rows_complete": sum(1 for row in rows if row["complete"]),
        "gated_rows": len(gated),
        "rows_before_ack": len(scope["rows_before_ack"]),
        "rows_after_ack": len(scope["rows_after_ack"]),
        "ack_found": scope["ack_found"],
        "stale_run_ids_before_ack": scope["stale_run_ids_before_ack"],
        "run_ids_after_ack": scope["run_ids_after_ack"],
        "truncated_run_ids_after_ack": scope["truncated_run_ids_after_ack"],
        "rows_with_safety_fields": with_safety,
        "rows_without_safety_fields": len(gated) - with_safety,
        "safety_field_coverage": (round(with_safety / len(gated), 4) if gated else None),
        "fields_missing_in_all_rows": missing_fields,
        "expected_safe_state": expected_safe_state,
        "state_values_seen": sorted(set(captured("state"))),
        "run_flag_values_seen": sorted(set(captured("run_flag"))),
        "fail_values_seen": sorted(set(captured("fail"))),
        "fault_rows": len(fault_rows),
        "fault_rows_any_scope": fault_row_any_scope,
        "fault_rows_before_ack": fault_row_any_scope - len(fault_rows),
        "fault_reasons_seen": fault_reasons,
        "fault_reason_names": sorted({PROTECT_FAULT_NAMES.get(reason, f"UNKNOWN({reason})")
                                      for reason in fault_reasons}),
        "first_fault_row": ({
            "t": fault_rows[0]["t"], "run_id": fault_rows[0]["run_id"],
            "state": fault_rows[0]["state"], "fault": fault_rows[0]["fault"],
            "fault_r": fault_rows[0]["fault_r"],
            "fault_reason_name": PROTECT_FAULT_NAMES.get(
                fault_rows[0]["fault_r"], f"UNKNOWN({fault_rows[0]['fault_r']})"),
        } if fault_rows else None),
        "vbus_mv_min": min(vbus) if vbus else None,
        "vbus_mv_max": max(vbus) if vbus else None,
    }
    checks = {
        "foc_rows_present": bool(gated),
        "foc_fault_zero": bool(fault_scope) and not fault_rows,
        "foc_run_flag_zero": bool(captured("run_flag")) and all(
            value == 0 for value in captured("run_flag")),
        "foc_fail_zero": bool(captured("fail")) and all(
            value == 0 for value in captured("fail")),
        "foc_state_safe": bool(captured("state")) and all(
            value == expected_safe_state for value in captured("state")),
        "foc_identity_scoped": bool(
            scope["ack_found"]
            and any(row["run_id"] is not None for row in scope["rows_after_ack"])
            and all(row["run_id"] is None or row["run_id"] == run_id
                    or run_id.startswith(row["run_id"])
                    for row in scope["rows_after_ack"])),
    }
    return summary, checks


FOC_BLOCKED_CHECKS = ("foc_identity_scoped",)


def evaluate_run(
    run_id: str,
    log_text: str,
    responses: dict[str, Any],
    firmware_sha256: Optional[str],
    identity_source: str = "firmware",
    expected_safe_state: int = 0,
) -> dict[str, Any]:
    """Per-run verdict. Fail-closed on every missing piece of real evidence."""
    samples = parse_adc_raws([responses[key] for key in sorted(responses) if key.startswith("adc_")])
    gate = evaluate_no_hv_gate(samples)
    # Identity scoping: строки @FOC ДО прямого ACK `@RUN:ID=<run_id>` принадлежат
    # предыдущей сессии (id в RAM переживает прогоны) и не должны приписываться
    # текущему прогону — иначе stale `map_id`/`map_crc32` попадут в его evidence.
    ack_index = log_text.find(RUN_ID_ACK_TOKEN + run_id)
    # Без прямого ACK identity-evidence у прогона НЕТ (stale-строки предыдущей
    # сессии не должны её подменять): fail-closed, а не «нашли где-то в логе».
    identity = extract_identity(log_text[ack_index:] if ack_index >= 0 else "")
    if identity_source == "folder" and not identity["run_id"]:
        # Documented fallback: the run id is the artifact folder name, and the
        # summary must show that the firmware did not carry it.
        identity = dict(identity)
        identity["folder_run_id"] = run_id
        identity["run_id_origin"] = "folder"
    else:
        identity = dict(identity)
        identity["run_id_origin"] = "firmware" if identity["run_id"] else None

    calibration_ok = CAL_FAIL not in responses.get("calibration", "") and bool(
        CAL_RE.search(responses.get("calibration", "")))
    encoder = last_int(ENC_ERR_RE, responses.get("encoder", ""), "err")
    pwm_state = decode_pwm_state(responses)
    mapcap_before = evaluate_mapcap_before(responses.get("status_before", ""))
    foc_summary, foc_checks = evaluate_foc_stream(log_text, run_id, expected_safe_state)
    integrity = check_log_integrity(log_text, run_id)
    trunc_match = last_int_match(UART_TRUNC_RE, responses.get("sysinfo", ""), "v")
    drp_match = last_int_match(UART_DRP_RE, responses.get("sysinfo", ""), "v")
    uart_trunc_reported = trunc_match is not None
    # The operator's run-id command must be acknowledged by the firmware itself
    # (`@RUN:ID=<run_id>`) in the DIRECT response to that command: a stale echo
    # elsewhere in the log must never satisfy the identity gate.
    run_id_acked = f"@RUN:ID={run_id}" in responses.get("run_id_set", "")

    checks = {
        "log_integrity": not integrity,
        "calibration_ok": calibration_ok,
        "encoder_err_zero": encoder == 0,
        # Default-deny is proven by the hardware state itself: MOE cleared and
        # every channel output disabled (CCER = 0) in the DIRECT register dump.
        "default_deny_hold": bool(pwm_state["moe_low"] and pwm_state["outputs_disabled"]),
        "moe_low": bool(pwm_state["moe_low"]),
        # N/A (not PASS-by-luck) when the image has no mapcap command; MANDATORY
        # and strictly IDLE/empty for a commissioning image.
        "mapcap_idle_empty_before": bool(mapcap_before["passed"]),
        "capture_rows_absent": "@MC:REC:" not in log_text,
        "no_hv_gate": gate["verdict"] == "PASS",
        # A non-zero truncation counter means an @FOC packet was rejected as
        # oversized: the evidence stream has a hole, so the run cannot pass.
        "uart_truncation_zero": trunc_match is None or trunc_match == 0,
        "firmware_sha_present": bool(firmware_sha256),
        "run_id_ack": run_id_acked,
        "identity_run_id": bool(identity.get("run_id") or identity.get("folder_run_id")),
        "identity_map_id": bool(identity.get("map_id")),
        "identity_map_crc32": bool(identity.get("map_crc32")),
    }
    # Построчные гейты потока @FOC: защёлкнутый fault/открытое силовое состояние
    # не должно маскироваться остальными проверками.
    checks.update(foc_checks)
    failed = sorted(name for name, ok in checks.items() if not ok)
    if not failed:
        verdict = "PASS"
    elif any(name in failed for name in
             ("identity_run_id", "identity_map_id", "identity_map_crc32", "run_id_ack",
              *FOC_BLOCKED_CHECKS)):
        verdict = "BLOCKED_MISSING_IDENTITY"
    else:
        verdict = "FAIL"

    origin_note = None
    if identity.get("run_id_origin") == "folder":
        origin_note = (
            "run_id taken from the artifact folder because the firmware stream "
            "carries no @RUN:ID/run_id token; this does NOT satisfy the "
            "firmware-identity requirement")

    return {
        "run_id": run_id,
        "verdict": verdict,
        "checks": checks,
        "failed_checks": failed,
        "no_hv_gate": gate,
        "pwm_state": pwm_state,
        "foc_stream": foc_summary,
        "mapcap_before": mapcap_before,
        "mapcap_scope": {
            "applicability": mapcap_before["applicability"],
            "command_present": mapcap_before["command_present"],
            "reason": mapcap_before["reason"],
        },
        "identity": identity,
        "identity_source": identity_source,
        "identity_note": origin_note,
        "log_integrity_issues": integrity,
        "uart_health": {
            "uart_trunc": trunc_match,
            "uart_drp": drp_match,
            "reported": uart_trunc_reported,
            "note": None if uart_trunc_reported else
                    "image does not report uart_trunc/uart_drp (budget package absent)",
        },
        "firmware_sha256": firmware_sha256,
        "reasons": [gate["reason"]] if gate["reason"] else [],
        "evidence_notes": [note for note in (pwm_state["note"], mapcap_before["reason"])
                           if note],
    }


def evaluate_preflight(
    run_id: str,
    log_text: str,
    responses: dict[str, Any],
    firmware_sha256: Optional[str],
    expected_map_id: str = "M0",
    expected_safe_state: int = 0,
) -> dict[str, Any]:
    """Pre-flight verdict for the FIRST physical M0 run (read-only campaign gates).

    Requirements from the accepted checklist: the image must already report the
    checked-sender counters (`uart_trunc`), the transport must be lossless, the
    identity must come from firmware, and every no-HV/control gate must hold
    BEFORE `M0-R1` is allowed to start.

    Two gates are scoped to the image that actually runs: `moe_low` /
    `default_deny_hold` are decoded from BDTR bit 15 and CCER of the direct
    register dump (the production image has no `MOE=`/`default_deny=` token),
    and `mapcap_idle_empty_before` is N/A — with the reason recorded — when the
    image has no mapcap command at all, while a commissioning image keeps it
    MANDATORY.
    """
    base = evaluate_run(run_id, log_text, responses, firmware_sha256,
                        expected_safe_state=expected_safe_state)
    health = base["uart_health"]
    identity = base["identity"]
    checks = dict(base["checks"])
    checks["preflight_telemetry_counters_reported"] = bool(health["reported"])
    checks["preflight_uart_trunc_zero"] = bool(
        health["reported"] and health["uart_trunc"] == 0)
    checks["preflight_map_id_expected"] = identity.get("map_id") == expected_map_id
    checks["preflight_map_crc32_present"] = bool(identity.get("map_crc32"))
    checks["preflight_capture_never_started"] = "@MC:REC:" not in log_text

    failed = sorted(name for name, ok in checks.items() if not ok)
    if not failed:
        status = "PASS"
    elif any(name.startswith("preflight_map_id_expected") or name == "preflight_map_crc32_present"
             or name in ("identity_run_id", "identity_map_id", "identity_map_crc32", "run_id_ack",
                         *FOC_BLOCKED_CHECKS)
             or name in ("preflight_telemetry_counters_reported",) for name in failed):
        status = "BLOCKED"
    else:
        status = "FAIL"

    return {
        "run_id": run_id,
        "status": status,
        "checks": checks,
        "failed_checks": failed,
        "no_hv_gate": base["no_hv_gate"],
        "pwm_state": base["pwm_state"],
        "foc_stream": base["foc_stream"],
        "mapcap_scope": base["mapcap_scope"],
        "evidence_notes": base["evidence_notes"],
        "identity": identity,
        "uart_health": health,
        "firmware_sha256": firmware_sha256,
        "expected_map_id": expected_map_id,
        "note": ("pre-flight is a read-only gate: it never arms or runs MapCapture "
                 "and never energises the DC-link"),
    }


def last_int_match(pattern: re.Pattern[str], text: str, group: str) -> Optional[int]:
    """Last integer match of `pattern` in `text`, or None when absent."""
    matches = list(pattern.finditer(text))
    return int(matches[-1].group(group)) if matches else None


def parse_status(text: str) -> Optional[dict[str, int]]:
    matches = list(STATUS_RE.finditer(text))
    return {k: int(v) for k, v in matches[-1].groupdict().items()} if matches else None


def last_int(pattern: re.Pattern[str], text: str, key: str) -> Optional[int]:
    matches = list(pattern.finditer(text))
    return int(matches[-1].group(key)) if matches else None


def campaign_verdict(
    runs: Sequence[dict[str, Any]],
    firmware_sha256: Optional[str],
    source_sha: Optional[str],
) -> dict[str, Any]:
    """Campaign verdict: PASS only for complete, identity-proven, no-HV runs."""
    ids = [run["run_id"] for run in runs]
    all_pass = bool(runs) and all(run["verdict"] == "PASS" for run in runs)
    any_identity_blocked = any(run["verdict"] == "BLOCKED_MISSING_IDENTITY" for run in runs)
    map_ids = {run.get("identity", {}).get("map_id") for run in runs}
    map_crcs = {run.get("identity", {}).get("map_crc32") for run in runs}
    checks = {
        "run_count_nonzero": bool(runs),
        "run_ids_unique": len(set(ids)) == len(ids),
        "all_runs_pass": all_pass,
        "firmware_sha_present": bool(firmware_sha256),
        "source_sha_present": bool(source_sha),
        # One baseline campaign must describe ONE map: five runs with different
        # map ids or CRCs are five different measurements, not a baseline.
        "map_id_identical": len(map_ids) == 1 and None not in map_ids,
        "map_crc32_identical": len(map_crcs) == 1 and None not in map_crcs,
    }
    if all(checks.values()):
        verdict = "PASS"
    elif any_identity_blocked:
        verdict = "INCOMPLETE_IDENTITY"
    elif (not checks["all_runs_pass"] or not checks["run_ids_unique"]
          or not checks["run_count_nonzero"] or not checks["map_id_identical"]
          or not checks["map_crc32_identical"]):
        verdict = "FAIL"
    else:
        verdict = "INCOMPLETE_PROVENANCE"
    return {
        "verdict": verdict,
        "checks": checks,
        "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "run_ids": ids,
        "map_id": next(iter(map_ids)) if len(map_ids) == 1 else sorted(map_ids, key=str),
        "map_crc32": next(iter(map_crcs)) if len(map_crcs) == 1 else sorted(map_crcs, key=str),
        "runs_total": len(runs),
        "runs_pass": sum(1 for run in runs if run["verdict"] == "PASS"),
        "mopt0_complete": verdict == "PASS",
        "note": (
            "PASS here means: 5 independent no-HV runs with firmware-side identity "
            "evidence recorded. It is NOT a Stage-A, DC-link, FOC or "
            "characterization permit."
            if verdict == "PASS" else
            "campaign is not a valid M-OPT-0 baseline; evidence is retained"),
    }


# ── Transports ────────────────────────────────────────────────────────────

class SerialTransport:
    """Physical UART transport; never constructed in simulation."""

    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self.responses: dict[str, str] = {}
        self._serial: Any = None
        self._log_file: Any = None

    def open(self) -> None:
        if serial is None:
            raise MoptError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=PHYSICAL; port={self.port}; baud={self.baud}; started={utc_now()}")
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1.0)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.close()
            raise MoptError(f"Не удалось открыть {self.port} @ {self.baud}: {exc}") from exc
        time.sleep(0.25)
        self._read_quiet(0.5, 0.15, "BOOT")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def _read_quiet(self, total_timeout_s: float, quiet_s: float, label: str) -> str:
        deadline = time.monotonic() + total_timeout_s
        last_data = time.monotonic()
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            data = self._serial.read(self._serial.in_waiting or 1)
            if data:
                chunks.append(data)
                last_data = time.monotonic()
            elif chunks and time.monotonic() - last_data >= quiet_s:
                break
            else:
                time.sleep(0.02)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if text:
            self._log(label, text)
        return text

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> str:
        self.command_sequence.append(command)
        self._log("TX", command)
        self._serial.write((command + "\r\n").encode("ascii"))
        self._serial.flush()
        return self._read_quiet(total_timeout_s, quiet_s, "RX")


class SimulatedTransport:
    """Deterministic offline transport: proves orchestration, never hardware.

    ``identity-present`` models a firmware build that carries the identity
    telemetry; ``identity-absent`` models the current main (no @RUN:ID/run_id/
    map_id/map_crc32) so the fail-closed path can be exercised.
    """

    SCENARIOS = ("identity-present", "identity-absent", "nohv-violated", "run-id-rejected",
                 "preflight-ready", "preflight-commissioning", "preflight-mapcap-dirty",
                 "preflight-moe-high", "preflight-fault-latched")

    def __init__(self, scenario: str, log_path: Path, run_id: str) -> None:
        if scenario not in self.SCENARIOS:
            raise MoptError(f"Unknown simulation scenario: {scenario}")
        self.scenario = scenario
        self.run_id = run_id
        self.log_path = log_path
        self.command_sequence: list[str] = []
        self.responses: dict[str, str] = {}
        self._log_file: Any = None

    def open(self) -> None:
        self._log_file = self.log_path.open("w", encoding="utf-8", newline="\n")
        self._log("META", f"mode=SIMULATED; scenario={self.scenario}; no COM/ST-Link/DC-link")
        # No boot-time identity echo: real firmware answers the run-id command,
        # so identity evidence must come from that response and the @FOC stream.
        self._log("BOOT", "@SIM:BOOT:deterministic-mopt0-simulation\r\n")

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _log(self, label: str, text: str) -> None:
        self._log_file.write(f"[{utc_now()}] {label}\n{text}")
        if text and not text.endswith("\n"):
            self._log_file.write("\n")
        self._log_file.flush()

    def command(self, command: str, total_timeout_s: float = 1.5, quiet_s: float = 0.25) -> str:
        del total_timeout_s, quiet_s
        self.command_sequence.append(command)
        self._log("TX", command)
        raw_vbus = 120 if self.scenario == "nohv-violated" else 2
        # 0x1CC0 = the BDTR pwm.c writes (BKE|OSSR|OSSI|DTG=0xC0) with MOE
        # cleared; `preflight-moe-high` sets bit 15 to model an armed output.
        bdtr = "0x00009CC0" if self.scenario == "preflight-moe-high" else "0x00001CC0"
        bdtr_dec = str(int(bdtr, 16))
        identity_enabled = self.scenario != "identity-absent"
        # The firmware contract puts identity on the periodic @FOC line, not on
        # the @ADC sample response.
        # Реальный формат @FOC (src/telemetry_format.h): порядок полей и
        # наличие FAULT/FAULT_R/FAIL/RUN/em_stop1/em_stop2 обязательны — по ним
        # работают построчные гейты приёмки.
        fault_row = self.scenario == "preflight-fault-latched"
        identity_line = (
            f"@FOC:t=1000:run_id={self.run_id}:map_id=M0:map_crc32=1A2B3C4D:"
            f"I1=2048:I2=2048:Ires=2048:Id=0:Iq=0:Id_ref=2000:Iq_ref=0:"
            f"VBUS=201:STATE=0:SPD=0:TH=0:sector=0:window=0:CCR1=500:CCR2=500:CCR3=500:"
            f"ADC_STATUS=7:FAULT={1 if fault_row else 0}:FAULT_R={18 if fault_row else 0}:"
            "FAIL=0:RUN=0:em_stop1=1:em_stop2=1\r\n" if identity_enabled else "")
        if command.startswith("run="):
            if self.scenario == "run-id-rejected":
                response = "err: run id must be 1..23 chars [A-Za-z0-9_.-]\r\n> "
            else:
                response = f"@RUN:ID={command[4:]}\r\n> "
        elif command == "sysinfo":
            health = (":uart_drp=0:uart_trunc=0"
                      if self.scenario.startswith("preflight") else "")
            response = (f"@SYSINFO:board=OEW-G474-REV7:fw=1.0"
                        f":CLK=170000000:OVR=0:JEOS=0:TO=0:JQOVF=0{health}\r\n")
        elif command == "p?":
            # Real production format (src/cli.c:76): BDTR in DECIMAL. 7360 =
            # 0x1CC0 = BKE|OSSR|OSSI|DTG=0xC0 as pwm.c writes it, MOE cleared.
            response = f"@PWM:CR1=0:CCER=0:BDTR={bdtr_dec}:CNT=0\r\n> "
        elif command == "pdump":
            # Real production format (src/cli.c:209): @PWM:FULL, both timers.
            response = ("@PWM:FULL:SYS=170000000:CFGR=0x00000002:"
                        f"T1:PSC=0:ARR=4249:CCR=2125,2125,2125:BDTR={bdtr}:"
                        "CCER=0x00000000:CR1=0x00000060:CNT=0:"
                        f"T8:PSC=0:ARR=4249:CCR=2125,2125,2125:BDTR={bdtr}:"
                        "CCER=0x00000000:CR1=0x00000060:CNT=0\r\n> ")
        elif command == "a":
            response = f"@ADC:I1=2048:I2=2048:Ires=2048:VBUS={raw_vbus}\r\n" + identity_line
        elif command == "c":
            response = "@ADC:CAL:offset_i1=2048:offset_i2=2048:offset_ires=2048\r\n"
        elif command == "enc":
            response = "@ENC:angle=0:speed=0:period_us=897:pulse_us=670:err=0\r\n"
        elif command == "mapcap status":
            if self.scenario == "preflight-commissioning":
                response = ("@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0"
                            ":avail=0:detail=0:raw_vbus=2:vbus_mv=201:i1_ma=0:i2_ma=0:"
                            "adc_status=7:sector=0:window=0\r\n> ")
            elif self.scenario == "preflight-mapcap-dirty":
                response = ("@MC:STATUS:state=3:term=-11:cap=7:frames=4:dropped=1:periods=36"
                            ":avail=4:detail=2:raw_vbus=2:vbus_mv=201:i1_ma=0:i2_ma=0:"
                            "adc_status=7:sector=0:window=0\r\n> ")
            else:
                # Production image: no mapcap command exists, the CLI answers
                # exactly this (src/cli.c:345).
                response = "unknown\r\n> "
        elif command == "f":
            response = "@SIM:FAULT:CLEAR:unexpected\r\n"
        else:
            response = "@SIM:ERR:unknown_command\r\n"
        self.responses[command] = response
        self._log("RX", response)
        return response


# ── Run / campaign execution ──────────────────────────────────────────────

def collect_run_evidence(
    transport: Any,
    vbus_samples: int,
    run_id: str,
    run_id_command: str = DEFAULT_RUN_ID_COMMAND,
) -> dict[str, str]:
    """The single ordered evidence sequence shared by both transports.

    The run-id command comes FIRST: without a firmware-acknowledged identifier
    every later sample would be unattributable, so the rest of the sequence is
    pointless. Its command text is recorded verbatim in the log.
    """
    responses: dict[str, str] = {}
    responses["run_id_set"] = transport.command(
        run_id_command.replace("{run_id}", run_id))
    responses["sysinfo"] = transport.command("sysinfo")
    responses["pwm_before"] = transport.command("p?")
    responses["pdump_before"] = transport.command("pdump")
    for index in range(vbus_samples):
        responses[f"adc_{index:03d}"] = transport.command("a")
    responses["calibration"] = transport.command("c")
    responses["encoder"] = transport.command("enc")
    responses["status_before"] = transport.command("mapcap status")
    return responses


def execute_run(
    run_id: str,
    transport: Any,
    run_dir: Path,
    firmware_sha256: Optional[str],
    identity_source: str,
    vbus_samples: int,
    run_id_command: str = DEFAULT_RUN_ID_COMMAND,
    expected_safe_state: int = 0,
) -> dict[str, Any]:
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MoptError(f"Папка прогона уже существует, evidence неизменяем: {run_dir}") from exc
    transport.open()
    try:
        responses = collect_run_evidence(transport, vbus_samples, run_id, run_id_command)
    finally:
        transport.close()
    log_text = transport.log_path.read_text(encoding="utf-8", errors="replace")
    verdict = evaluate_run(run_id, log_text, responses, firmware_sha256, identity_source,
                           expected_safe_state=expected_safe_state)
    verdict["artifacts"] = {
        "run_dir": str(run_dir),
        "uart_log": str(transport.log_path),
        "command_sequence": list(transport.command_sequence),
    }
    write_json(run_dir / f"{run_id}.json", verdict)
    return verdict


def _execute_preflight(args: argparse.Namespace) -> int:
    """Read-only pre-flight for the first physical M0 run."""
    if args.vbus_samples < 1:
        raise MoptError("--vbus-samples должен быть >= 1.")
    firmware_sha256 = None
    if args.firmware_bin:
        firmware_path = Path(args.firmware_bin)
        if not firmware_path.is_file():
            raise MoptError(f"--firmware-bin не найден: {firmware_path}")
        firmware_sha256 = sha256_file(firmware_path)
    if not firmware_sha256:
        raise MoptError("Pre-flight требует --firmware-bin: SHA образа обязан быть "
                        "зафиксирован до прошивки и до M0-R1.")

    simulated = bool(args.simulate)
    if not simulated:
        missing = [flag for flag, present in (
            ("--confirm-dc-link-disconnected", args.confirm_dc_link_disconnected),
            ("--confirm-pc4-zero", args.confirm_pc4_zero),
            ("--confirm-sd-high", args.confirm_sd_high),
        ) if not present]
        if missing:
            raise MoptError("Pre-flight заблокирован. Укажите: " + " ".join(missing))
        if not args.port:
            raise MoptError("Укажите --port COMx (см. `run --list-ports`).")

    output_dir = Path(args.campaign) if args.campaign else Path(
        "campaign_raw") / f"mopt0_preflight_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}Z"
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MoptError(f"Папка pre-flight уже существует: {output_dir}") from exc

    if simulated:
        transport: Any = SimulatedTransport(args.simulate, output_dir / "uart.log",
                                            args.run_id)
    else:
        transport = SerialTransport(args.port, args.baud, output_dir / "uart.log")

    transport.open()
    try:
        responses = collect_run_evidence(transport, args.vbus_samples, args.run_id,
                                         args.run_id_command)
    finally:
        transport.close()
    log_text = transport.log_path.read_text(encoding="utf-8", errors="replace")
    report = evaluate_preflight(args.run_id, log_text, responses, firmware_sha256,
                                args.expected_map_id, args.expected_safe_state)
    report["mode"] = "SIMULATED" if simulated else "PHYSICAL"
    report["command_sequence"] = list(transport.command_sequence)
    report["artifacts"] = {"output_dir": str(output_dir),
                           "uart_log": str(transport.log_path),
                           "report": str(output_dir / "preflight.json")}
    report["operator_confirmations"] = {
        "dc_link_disconnected": bool(args.confirm_dc_link_disconnected),
        "pc4_zero": bool(args.confirm_pc4_zero),
        "sd_high": bool(args.confirm_sd_high),
    }
    if simulated:
        report["note"] = ("simulated pre-flight proves the orchestration only; a "
                          "physical pre-flight requires the real bench")
        if report["status"] == "PASS":
            report["status"] = "SIMULATED"
    write_json(output_dir / "preflight.json", report)
    print(json.dumps({"mode": report["mode"], "status": report["status"],
                      "run_id": args.run_id, "failed": report["failed_checks"],
                      "uart_health": report["uart_health"],
                      "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if report["status"] in ("PASS", "SIMULATED") else 1


def _execute_verify_preflight(args: argparse.Namespace) -> int:
    """Offline re-verification of a SAVED pre-flight, without touching the bench.

    An interpretation fix must not require a new physical session: the checks are
    recomputed from the retained raw `uart.log` (TX/RX markers), and the stored
    `preflight.json` is used only for its parameters (run id, expected map id,
    firmware sha, mode) — never as evidence. `verdict` is the recomputed one, and
    both raw files are pinned by hashes, so a stored verdict never stays in force
    by inertia (`status_changed`) and a recomputed FAIL/BLOCKED can never be
    replayed into a PASS.
    """
    if not args.campaign:
        raise MoptError("Укажите --campaign <папка pre-flight>.")
    preflight_dir = Path(args.campaign)
    report_path = preflight_dir / "preflight.json"
    log_path = preflight_dir / "uart.log"
    if not report_path.is_file():
        raise MoptError(f"Нет preflight.json в {preflight_dir}")
    if not log_path.is_file():
        raise MoptError(f"Нет uart.log в {preflight_dir} (raw evidence обязателен)")
    stored = json.loads(report_path.read_text(encoding="utf-8"))
    firmware_sha256 = stored.get("firmware_sha256")
    if args.firmware_bin:
        actual = sha256_file(Path(args.firmware_bin))
        if firmware_sha256 and actual != firmware_sha256:
            raise MoptError("firmware.bin не совпадает с preflight.json (SHA-256)")
        firmware_sha256 = actual

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    responses = rebuild_responses_from_log(log_text)
    run_id = stored.get("run_id") or args.run_id
    recomputed = evaluate_preflight(run_id, log_text, responses, firmware_sha256,
                                    stored.get("expected_map_id") or args.expected_map_id,
                                    int(stored.get("expected_safe_state",
                                                   args.expected_safe_state)))
    mode = stored.get("mode", "PHYSICAL")
    recomputed["mode"] = "SIMULATED" if mode != "PHYSICAL" else "PHYSICAL"

    stored_status = stored.get("status")
    # The pre-flight writer stores SIMULATED instead of the PASS the checks
    # produced: that substitution is expected and is not a change of verdict.
    comparable = "PASS" if stored_status == "SIMULATED" else stored_status
    mismatches = [] if recomputed["status"] == comparable else [
        f"stored status {stored_status} != recomputed {recomputed['status']}"]
    if recomputed["status"] == "PASS" and mode != "PHYSICAL":
        verdict = "SIMULATED"
    else:
        verdict = recomputed["status"]

    report = {
        "verified_utc": utc_now(),
        "preflight_dir": str(preflight_dir),
        "mode": mode,
        "run_id": run_id,
        "stored_status": stored_status,
        "recomputed_status": recomputed["status"],
        "verdict": verdict,
        # A changed status is a re-interpretation of the SAME evidence (an older
        # verifier version produced the stored one), not a change of evidence:
        # both raw files are hashed below.
        "status_changed": bool(mismatches),
        "mismatches": mismatches,
        "evidence": {
            "uart_log_sha256": sha256_file(log_path),
            "preflight_json_sha256": sha256_file(report_path),
        },
        "recomputed": recomputed,
        "note": ("`verdict` is the RECOMPUTED one for the retained raw uart.log; a replay "
                 "never re-runs the bench and never turns a recomputed FAIL/BLOCKED into "
                 "a PASS. `status_changed` marks that the stored verdict came from an "
                 "older interpretation and must be re-read; the raw evidence itself is "
                 "pinned by the two hashes."),
    }
    write_json(preflight_dir / "preflight_verify.json", report)
    print(json.dumps({"mode": mode, "stored": stored_status,
                      "recomputed": recomputed["status"], "verdict": verdict,
                      "status_changed": bool(mismatches),
                      "failed": recomputed["failed_checks"], "mismatches": mismatches},
                     ensure_ascii=False))
    return 0 if verdict in ("PASS", "SIMULATED") else 1


def resolve_source_sha(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def default_campaign_dir(simulated: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    kind = "mopt0_sim" if simulated else "mopt0_nohv"
    return Path("campaign_raw") / f"{kind}_{stamp}Z"


def _execute_run(args: argparse.Namespace) -> int:
    if args.runs < 1:
        raise MoptError("--runs должен быть >= 1.")
    if args.vbus_samples < 1:
        raise MoptError("--vbus-samples должен быть >= 1.")
    if args.identity_source not in IDENTITY_SOURCES:
        raise MoptError(f"--identity-source: ожидалось одно из {IDENTITY_SOURCES}.")

    firmware_sha256 = None
    if args.firmware_bin:
        firmware_path = Path(args.firmware_bin)
        if not firmware_path.is_file():
            raise MoptError(f"--firmware-bin не найден: {firmware_path}")
        firmware_sha256 = sha256_file(firmware_path)
    source_sha = resolve_source_sha(args.source_sha)

    simulated = bool(args.simulate)
    if not simulated:
        missing = [flag for flag, present in (
            ("--confirm-dc-link-disconnected", args.confirm_dc_link_disconnected),
            ("--confirm-pc4-zero", args.confirm_pc4_zero),
            ("--confirm-sd-high", args.confirm_sd_high),
        ) if not present]
        if missing:
            raise MoptError("Реальный запуск заблокирован. После физического preflight укажите: "
                            + " ".join(missing))
        if not args.port:
            raise MoptError("Для реального запуска укажите --port COMx (см. `list-ports`).")

    campaign_dir = Path(args.campaign) if args.campaign else default_campaign_dir(simulated)
    try:
        campaign_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise MoptError(
            f"Папка кампании уже существует, evidence неизменяем: {campaign_dir}") from exc

    write_json(campaign_dir / "metadata.json", {
        "started_utc": utc_now(),
        "execution": {"mode": "SIMULATED" if simulated else "PHYSICAL",
                      "scenario": args.simulate},
        "run_prefix": args.run_prefix,
        "runs_requested": args.runs,
        "vbus_samples": args.vbus_samples,
        "identity_source": args.identity_source,
        "expected_safe_state": args.expected_safe_state,
        "firmware_sha256": firmware_sha256,
        "source_sha": source_sha,
        "no_hv_contract": {
            "median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
            "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT,
            "adc_sat_low": ADC_RAW_SAT_LOW,
            "adc_sat_high": ADC_RAW_SAT_HIGH,
        },
        "arguments": {key: str(value) for key, value in vars(args).items()},
    })

    runs: list[dict[str, Any]] = []
    for index in range(1, args.runs + 1):
        run_id = f"{args.run_prefix}{index}"
        run_dir = campaign_dir / run_id
        if simulated:
            transport: Any = SimulatedTransport(args.simulate, run_dir / "uart.log", run_id)
        else:
            transport = SerialTransport(args.port, args.baud, run_dir / "uart.log")
        try:
            verdict = execute_run(run_id, transport, run_dir, firmware_sha256,
                                  args.identity_source, args.vbus_samples,
                                  args.run_id_command, args.expected_safe_state)
        except MoptError as exc:
            verdict = {"run_id": run_id, "verdict": "FAIL", "reasons": [str(exc)],
                       "checks": {}, "failed_checks": ["transport"], "no_hv_gate": None,
                       "identity": extract_identity(""), "identity_source": args.identity_source}
        runs.append(verdict)
        print(json.dumps({"run_id": run_id, "verdict": verdict["verdict"]},
                         ensure_ascii=False))

    campaign = campaign_verdict(runs, firmware_sha256, source_sha)
    if simulated:
        campaign["verdict"] = "SIMULATED" if campaign["verdict"] == "PASS" else campaign["verdict"]
        campaign["note"] = ("simulation proves orchestration only; it is never a physical "
                            "M-OPT-0 baseline")
        campaign["mopt0_complete"] = False

    summary = {
        "finished_utc": utc_now(),
        "execution": {"mode": "SIMULATED" if simulated else "PHYSICAL", "scenario": args.simulate},
        "campaign": campaign,
        "runs": runs,
        "artifacts": {"campaign_dir": str(campaign_dir),
                      "metadata": str(campaign_dir / "metadata.json"),
                      "summary": str(campaign_dir / "summary.json")},
    }
    write_json(campaign_dir / "summary.json", summary)
    print(json.dumps({"mode": summary["execution"]["mode"],
                      "campaign": campaign["verdict"],
                      "runs": campaign["runs_total"],
                      "runs_pass": campaign["runs_pass"],
                      "campaign_dir": str(campaign_dir)}, ensure_ascii=False))
    return 0 if campaign["verdict"] in ("PASS", "SIMULATED") else 1


def _execute_verify(args: argparse.Namespace) -> int:
    campaign_dir = Path(args.campaign)
    metadata_path = campaign_dir / "metadata.json"
    if not metadata_path.is_file():
        raise MoptError(f"Нет metadata.json в {campaign_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    firmware_sha256 = metadata.get("firmware_sha256")
    source_sha = metadata.get("source_sha")
    identity_source = metadata.get("identity_source", "firmware")
    vbus_samples = int(metadata.get("vbus_samples", DEFAULT_VBUS_SAMPLES))
    runs_requested = int(metadata.get("runs_requested", DEFAULT_RUNS))

    if firmware_sha256 and args.firmware_bin:
        actual = sha256_file(Path(args.firmware_bin))
        if actual != firmware_sha256:
            raise MoptError("firmware.bin не совпадает с metadata.json (SHA-256)")

    runs: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for index in range(1, runs_requested + 1):
        run_id = f"{metadata.get('run_prefix', DEFAULT_RUN_PREFIX)}{index}"
        run_dir = campaign_dir / run_id
        log_path = run_dir / "uart.log"
        if not log_path.is_file():
            mismatches.append(f"{run_id}: uart.log отсутствует")
            continue
        run_text = log_path.read_text(encoding="utf-8", errors="replace")
        responses = rebuild_responses_from_log(run_text)
        adc_chunks = [value for key, value in responses.items() if key.startswith("adc_")]
        verdict = evaluate_run(run_id, run_text, responses, firmware_sha256, identity_source,
                               expected_safe_state=int(metadata.get("expected_safe_state", 0)))
        verdict["verified_offline"] = True
        runs.append(verdict)
        stored_path = run_dir / f"{run_id}.json"
        if stored_path.is_file():
            stored = json.loads(stored_path.read_text(encoding="utf-8"))
            if stored.get("verdict") != verdict["verdict"]:
                mismatches.append(
                    f"{run_id}: stored verdict {stored.get('verdict')} != recomputed {verdict['verdict']}")
        if adc_chunks and len(adc_chunks) < vbus_samples:
            mismatches.append(f"{run_id}: {len(adc_chunks)} ADC samples < {vbus_samples}")

    campaign = campaign_verdict(runs, firmware_sha256, source_sha)
    mode = metadata.get("execution", {}).get("mode", "PHYSICAL")
    verify_verdict = "MISMATCH" if mismatches else campaign["verdict"]
    if verify_verdict == "PASS" and mode != "PHYSICAL":
        # A simulated campaign can never be verified as a physical baseline.
        verify_verdict = "SIMULATED"
    report = {
        "verified_utc": utc_now(),
        "campaign_dir": str(campaign_dir),
        "mode": mode,
        "campaign": campaign,
        "runs": runs,
        "mismatches": mismatches,
        "verify_verdict": verify_verdict,
        "note": ("offline re-verification of a simulated campaign proves the checker and "
                 "the log format only; it is not a physical M-OPT-0 baseline"
                 if verify_verdict == "SIMULATED" else None),
    }
    write_json(campaign_dir / "verify_report.json", report)
    print(json.dumps({"campaign": campaign["verdict"], "verify": verify_verdict,
                      "mismatches": mismatches}, ensure_ascii=False))
    return 0 if verify_verdict == "PASS" else 1


def handle_utility_modes(args: argparse.Namespace) -> Optional[int]:
    if args.list_ports:
        if serial is None:
            raise MoptError("pyserial не установлен. Выполните: py -3 -m pip install pyserial")
        for item in serial.tools.list_ports.comports():
            print(f"{item.device}\t{item.description}\t{item.hwid}")
        return 0
    if args.dry_run:
        print(json.dumps({
            "mode": "dry-run",
            "runs": args.runs,
            "run_ids": [f"{args.run_prefix}{i}" for i in range(1, args.runs + 1)],
            "no_hv_contract": {"median_max": NOHV_RAW_VBUS_MEDIAN_MAX,
                               "hard_limit": NOHV_RAW_VBUS_HARD_LIMIT},
            "requires_firmware_identity": ["run_id", "map_id", "map_crc32"],
            "note": "dry-run does not touch COM, ST-Link, sigrok or the bench",
        }, ensure_ascii=False, indent=2))
        return 0
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M-OPT-0 no-HV baseline campaign (5 runs) для стенда ПК-3.")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (("run", "собрать кампанию M0-R1..R5"),
                            ("verify", "перепроверить сохранённую кампанию офлайн"),
                            ("preflight", "read-only pre-flight перед первым M0-R1"),
                            ("verify-preflight", "перепроверить сохранённый pre-flight "
                                                 "офлайн (raw uart.log + preflight.json)")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--campaign", type=Path,
                          help="папка кампании (создаётся; для verify — существующая)")
        item.add_argument("--firmware-bin", help="путь к build/firmware.bin для SHA-256")
        item.add_argument("--source-sha", help="git SHA источника (иначе git rev-parse HEAD)")
        item.add_argument("--runs", type=int, default=DEFAULT_RUNS)
        item.add_argument("--run-prefix", default=DEFAULT_RUN_PREFIX)
        item.add_argument("--port", help="COM-порт UART MCU, например COM15")
        item.add_argument("--baud", type=int, default=115200)
        item.add_argument("--vbus-samples", type=int, default=DEFAULT_VBUS_SAMPLES)
        item.add_argument("--run-id-command", default=DEFAULT_RUN_ID_COMMAND,
                          help="команда установки run id; {run_id} подставляется "
                               "(default: %(default)s)")
        item.add_argument("--identity-source", choices=list(IDENTITY_SOURCES), default="firmware")
        item.add_argument("--confirm-dc-link-disconnected", action="store_true")
        item.add_argument("--confirm-pc4-zero", action="store_true")
        item.add_argument("--confirm-sd-high", action="store_true")
        item.add_argument("--simulate", choices=list(SimulatedTransport.SCENARIOS),
                          help="офлайн-прогон оркестрации; оборудование не используется")
        item.add_argument("--list-ports", action="store_true")
        item.add_argument("--dry-run", action="store_true")
        item.add_argument("--run-id", default="M0-R1",
                          help="идентификатор для pre-flight (@RUN:ID должен совпасть)")
        item.add_argument("--expected-map-id", default="M0")
        item.add_argument("--expected-safe-state", type=int, default=0,
                          help="ожидаемое значение поля STATE в @FOC (read-only "
                               "baseline: 0)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        utility = handle_utility_modes(args)
        if utility is not None:
            return utility
        if args.command == "run":
            return _execute_run(args)
        if args.command == "preflight":
            return _execute_preflight(args)
        if args.command == "verify-preflight":
            return _execute_verify_preflight(args)
        return _execute_verify(args)
    except MoptError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Прервано оператором. Fault не очищался автоматически.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
