#!/usr/bin/env python3
"""Fill operator/safety-watcher signatures in an ACS712 no-HV evidence package.

This script only edits markdown files inside the campaign directory.  It does not
open serial ports, flash firmware, or energize hardware.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

PLACEHOLDER = re.compile(r"_+\w+_+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _replace_markers(text: str, operator: str, utc: str, dmm_scope_id: str) -> str:
    """Replace explicit signature markers in a markdown file."""
    text = re.sub(r"Operator:\s*_{3,}", f"Operator: {operator}", text, flags=re.IGNORECASE)
    text = re.sub(r"Оператор:\s*_{3,}", f"Оператор: {operator}", text, flags=re.IGNORECASE)
    text = re.sub(r"UTC:\s*_{3,}", f"UTC: {utc}", text, flags=re.IGNORECASE)
    text = re.sub(
        r"DMM/scope ID:\s*_{3,}",
        f"DMM/scope ID: {dmm_scope_id}",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _append_signature_block(text: str, role: str, operator: str, utc: str) -> str:
    block = f"\n\n---\n{role}: {operator}\nUTC: {utc}\n"
    if not text.endswith("\n"):
        text += "\n"
    return text + block


def _fill_file(
    path: Path,
    operator: str,
    utc: str,
    dmm_scope_id: str,
    role: str,
    append_block: bool,
) -> bool:
    if not path.is_file():
        print(f"skip: {path} not found", file=sys.stderr)
        return False
    text = path.read_text(encoding="utf-8")
    new_text = _replace_markers(text, operator, utc, dmm_scope_id)
    if append_block:
        new_text = _append_signature_block(new_text, role, operator, utc)
    if new_text == text:
        print(f"unchanged: {path}")
    else:
        path.write_text(new_text, encoding="utf-8")
        print(f"updated: {path}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path,
                        help="path to C:\\campaign_raw\\acs712_nohv_YYYYMMDDTHHMMSSZ")
    parser.add_argument("--operator", required=True,
                        help="name of the operator who performed the checkout")
    parser.add_argument("--dmm-scope-id", default="see worksheet",
                        help="DMM and oscilloscope identifiers")
    parser.add_argument("--utc", default=None,
                        help="UTC timestamp (ISO 8601); defaults to now")
    parser.add_argument("--role", default="operator",
                        help="role label for appended signature block")
    parser.add_argument("--no-append-block", action="store_true",
                        help="only replace inline markers, do not append signature block")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    campaign: Path = args.campaign.resolve()
    if not campaign.is_dir():
        print(f"error: campaign directory does not exist: {campaign}", file=sys.stderr)
        return 2

    utc = args.utc or _utc_now()
    files = {
        campaign / "dmm" / "zero_current_offsets_worksheet.md": True,
        campaign / "observations" / "wiring_check.md": False,
        campaign / "summary" / "acs712_nohv_summary.md": True,
    }

    ok = True
    for path, append_block in files.items():
        if not _fill_file(
            path,
            args.operator,
            utc,
            args.dmm_scope_id,
            args.role,
            append_block and not args.no_append_block,
        ):
            ok = False

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
