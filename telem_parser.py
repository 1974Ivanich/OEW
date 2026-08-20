"""Pure parsers for OEW firmware telemetry lines.

The firmware uses a stable ``@PREFIX:key=value:key=value`` wire format.
These functions deliberately do not import Tkinter, serial, or GUI modules.
"""
from __future__ import annotations

import re
from typing import Any

VFLOG_FIELDS = (
    "t", "target", "meas", "fe", "fslip", "vmag", "theta",
    "du", "dv", "dw", "i1", "i2", "ires", "vbus", "eangle",
    "espeed", "eerr", "fault", "drp",
)

_INT_RE = re.compile(r"^-?\d+$")


def _payload(line: str, prefixes: tuple[str, ...]) -> str | None:
    if not isinstance(line, str):
        return None
    value = line.strip()
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):].split("\r", 1)[0].split("\n", 1)[0]
    return None


def _parse_pairs(payload: str) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    if not payload:
        return None
    for part in payload.split(":"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if not key:
            continue
        if _INT_RE.fullmatch(value):
            result[key] = int(value)
        else:
            result[key] = value
    return result or None


def parse_vflog(line: str) -> dict[str, Any] | None:
    """Parse ``@VFLOG`` and return all 19 fields, missing fields as None."""
    payload = _payload(line, ("@VFLOG:",))
    if payload is None:
        return None
    values = _parse_pairs(payload)
    if values is None:
        return None
    return {field: values.get(field) for field in VFLOG_FIELDS}


def parse_foc(line: str) -> dict[str, Any] | None:
    payload = _payload(line, ("@FOC:",))
    return None if payload is None else _parse_pairs(payload)


def parse_enc(line: str) -> dict[str, Any] | None:
    payload = _payload(line, ("@ENC:",))
    return None if payload is None else _parse_pairs(payload)


def parse_params(line: str) -> dict[str, Any] | None:
    payload = _payload(line, ("@AT:PARAMS:", "@PARAMS:"))
    values = None if payload is None else _parse_pairs(payload)
    if values is None:
        return None
    field_names = {
        "Rs_mOhm": "Rs", "Ls_uH": "Ls", "Isat_mA": "Isat",
        "Rr_mOhm": "Rr", "Lm_uH": "Lm", "Tr_us": "Tr",
        "Ke_mV_rpm": "Ke", "J_x1e6": "J",
    }
    return {field_names.get(key, key): value for key, value in values.items()}


def parse_curve(line: str) -> dict[str, Any] | None:
    payload = _payload(line, ("@IDLE:CURVE:",))
    if payload is None:
        return None
    points = []
    for item in payload.split(":"):
        match = re.fullmatch(r"I=(-?\d+),L=(-?\d+)", item)
        if match:
            points.append((int(match.group(1)), int(match.group(2))))
    return {"points": points} if points else None
