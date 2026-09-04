#!/usr/bin/env python3
"""Create a deterministic ZIP archive of a completed BOAR grid campaign.

The archive is byte-reproducible for the same evidence: entries are sorted,
compression is fixed (zipfile.ZIP_DEFLATED), and timestamps are clamped to
1980-01-01 (ZIP minimum). A JSON receipt is written next to the ZIP with the
SHA-256 of the archive and an inventory of every file.

The tool never touches hardware. It expects the campaign structure produced by
map_scope_ingest.py: logs/, scope/, calibration/acs712_calibration.json, and
campaign/manifest.json + samples.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ARCHIVE_SCHEMA = "boar-campaign-archive-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _deterministic_zip(source: Path, zip_path: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    files: list[Path] = sorted(
        p for p in source.rglob("*") if p.is_file() and not p.is_symlink()
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(source).as_posix()
            digest = _sha256_file(path)
            size = path.stat().st_size
            inventory.append({
                "path": rel,
                "size_bytes": size,
                "sha256": digest,
            })
            info = zipfile.ZipInfo(filename=rel, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as fh:
                zf.writestr(info, fh.read())

    return inventory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root",
        type=Path,
        required=True,
        help="completed campaign directory (with campaign/manifest.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output zip path (default: campaign-root.zip)",
    )
    parser.add_argument(
        "--no-receipt",
        action="store_true",
        help="do not write JSON receipt next to the zip",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source: Path = args.campaign_root.resolve()

    manifest = source / "campaign" / "manifest.json"
    samples = source / "campaign" / "samples.jsonl"
    if not manifest.is_file() or not samples.is_file():
        print(f"error: {source / 'campaign'} does not contain manifest.json + "
              f"samples.jsonl — run ingest first", file=sys.stderr)
        return 2

    zip_path: Path = args.out if args.out else source.parent / f"{source.name}.zip"
    zip_path = zip_path.resolve()
    receipt_path = zip_path.with_suffix(".receipt.json")

    inventory = _deterministic_zip(source, zip_path)
    archive_sha = _sha256_file(zip_path)

    receipt = {
        "schema": ARCHIVE_SCHEMA,
        "campaign_root": source.name,
        "archive_path": str(zip_path),
        "archive_sha256": archive_sha,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": inventory,
    }

    if not args.no_receipt:
        receipt_path.write_text(
            _canonical_json(receipt).decode("utf-8") + "\n",
            encoding="utf-8",
        )

    print(f"archive: {zip_path}")
    print(f"sha256 : {archive_sha}")
    print(f"files  : {len(inventory)}")
    if not args.no_receipt:
        print(f"receipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
