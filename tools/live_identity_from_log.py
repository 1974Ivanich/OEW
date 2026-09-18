#!/usr/bin/env python3
"""Живая identity платы → live.txt для map_variant_cli (TZ-02).

Берёт из лога сессии строку, которую печатает команда `mapcap identity`
(main.c: формат @MAP:IDENTITY:...), и переводит её в пары key=value,
которые понимают `--check-live-identity` и `--rebase-identity`.

Использование:
    python tools/live_identity_from_log.py <log> [live.txt]

Fail-closed: строки нет, строка не разобрана или в ней есть нулевые поля →
код выхода 1, файл не пишется (результат не должен выглядеть пригодным).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# порядок и имена — как в main.c (mapcap identity)
FIELDS = [
    ("board", "board_revision", "u"),
    ("pwm", "pwm_frequency_hz", "u"),
    ("arr", "timer_arr", "u"),
    ("trig", "adc_trigger_id", "x"),
    ("off", "trigger_offset_ticks", "u"),
    ("dt", "deadtime_ticks", "u"),
    ("adc_clk", "adc_clock_hz", "u"),
    ("sample_x2", "adc_sample_cycles_x2", "u"),
    ("res", "adc_resolution", "u"),
    ("acs", "adc_config_signature", "x"),
    ("ccs", "current_calibration_signature", "x"),
]
REQUIRED_NONZERO = {
    "board_revision", "pwm_frequency_hz", "timer_arr", "adc_trigger_id",
    "adc_clock_hz", "adc_sample_cycles_x2", "deadtime_ticks",
    "adc_config_signature", "current_calibration_signature",
}


def parse_identity(log_text: str) -> dict[str, int]:
    if "MAP:IDENTITY:FAIL" in log_text:
        raise ValueError("плата ответила @MAP:IDENTITY:FAIL — живая identity не собрана")
    m = re.search(r"@MAP:IDENTITY:([^\r\n>]+)", log_text)
    if not m:
        raise ValueError("строка @MAP:IDENTITY:… не найдена (запустите `mapcap identity` на ПК-3)")
    parts = {}
    for chunk in m.group(1).split(":"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            parts[k.strip()] = v.strip()
    out: dict[str, int] = {}
    missing = []
    for src, dst, kind in FIELDS:
        if src not in parts:
            missing.append(src)
            continue
        raw = parts[src]
        base = 16 if kind == "x" else 10
        try:
            out[dst] = int(raw, base)
        except ValueError as exc:
            raise ValueError(f"поле {src}={raw!r} не разобрано как число: {exc}") from exc
    if missing:
        raise ValueError(f"в строке identity нет полей: {', '.join(missing)}")
    zero = sorted(k for k in REQUIRED_NONZERO if out.get(k, 0) == 0)
    if zero:
        raise ValueError(f"нулевые поля живой identity: {', '.join(zero)} — маппинг запрещён (fail-closed)")
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: python tools/live_identity_from_log.py <log> [live.txt]", file=sys.stderr)
        return 2
    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(f"нет файла: {log_path}", file=sys.stderr)
        return 2
    try:
        ident = parse_identity(log_path.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1

    lines = [f"{k}={v}" for k, v in ident.items()]
    text = "\n".join(lines) + "\n"
    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(text, encoding="utf-8", newline="\n")
        print(f"live identity записана: {sys.argv[2]}")
    for line in lines:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
