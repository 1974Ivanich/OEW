#!/usr/bin/env python3
"""Offline fail-closed archive and SHA-256 inventory for Test №2 campaign evidence.

This tool never opens serial devices, invokes sigrok/ST-Link, flashes firmware or
controls any physical equipment.  It reads a completed campaign directory and
writes a deterministic ZIP plus a separate receipt outside that directory.
Archive PASS proves evidence-package integrity, not physical Test №2 PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Sequence


ARCHIVE_SCHEMA = "test2-campaign-archive-v1"
MANIFEST_NAME = "campaign_manifest.json"
G0_GATE = "HIL_TEST2_G0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

REQUIRED_EVIDENCE: Mapping[str, tuple[str, ...]] = {
    "g0_approval": ("g0_approval.json",),
    "g0_manifest": ("diagnostic_build_manifest.json",),
    "g0_build_log": ("diagnostic_build.log",),
    "g0_summary": ("g0_check_summary.json", "g0/g0_check_summary.json"),
    "execution_metadata": ("metadata.json",),
    "execution_summary": ("summary.json",),
    "uart_log": ("uart.log",),
    "sigrok_csv": ("sigrok_digital.csv",),
    "sigrok_stdout": ("sigrok_stdout.log",),
    "sigrok_stderr": ("sigrok_stderr.log",),
}


@dataclass
class Check:
    check_id: str
    result: str
    expected: Any
    actual: Any
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "result": self.result,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class Recorder:
    checks: list[Check] = field(default_factory=list)

    def add(self, check_id: str, expected: Any, actual: Any, passed: bool,
            detail: str) -> bool:
        self.checks.append(Check(
            check_id=check_id,
            result="PASS" if passed else "FAIL",
            expected=expected,
            actual=actual,
            detail=detail,
        ))
        return passed

    @property
    def passed(self) -> bool:
        return all(check.result == "PASS" for check in self.checks)


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    size_bytes: int
    sha256: str
    source: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _tree_digest(entries: Iterable[InventoryEntry]) -> str:
    inventory = [entry.as_dict() for entry in entries]
    return hashlib.sha256(_canonical_json(inventory)).hexdigest()


def _inside(parent: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _safe_relative_text(path: str) -> bool:
    if not path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and all(part not in ("", ".", "..") for part in pure.parts)


def _read_json(path: Path, recorder: Recorder, check_id: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        recorder.add(check_id, "valid JSON object", type(exc).__name__, False,
                     "Evidence JSON is unreadable or malformed.")
        return None
    is_object = isinstance(parsed, dict)
    recorder.add(check_id, "JSON object", type(parsed).__name__, is_object,
                 "Evidence JSON root must be an object.")
    return parsed if is_object else None


def _resolve_required(campaign: Path, recorder: Recorder) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for logical_name, candidates in REQUIRED_EVIDENCE.items():
        valid: list[str] = []
        invalid: list[str] = []
        for relative in candidates:
            candidate = campaign / relative
            if candidate.exists() or candidate.is_symlink():
                if candidate.is_symlink() or not candidate.is_file():
                    invalid.append(relative)
                else:
                    valid.append(relative)
        one_match = len(valid) == 1 and not invalid
        recorder.add("required." + logical_name, "exactly one regular file", {
            "candidates": list(candidates), "present_regular": valid, "invalid": invalid,
        }, one_match, "Required evidence cannot be missing, ambiguous or a symlink.")
        if one_match:
            resolved[logical_name] = valid[0]
    return resolved


def _walk_regular_files(campaign: Path, recorder: Recorder) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    root = campaign.resolve()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        retained_dirs: list[str] = []
        for name in dirnames:
            candidate = directory_path / name
            is_link = candidate.is_symlink()
            recorder.add("tree.dir." + candidate.relative_to(root).as_posix(), "not symlink",
                         "symlink" if is_link else "directory", not is_link,
                         "Symlink directories are rejected to prevent evidence substitution.")
            if not is_link:
                retained_dirs.append(name)
        dirnames[:] = retained_dirs
        for name in filenames:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                recorder.add("tree.file." + relative, "regular file", "symlink", False,
                             "Symlink evidence is rejected.")
                continue
            if not candidate.is_file():
                recorder.add("tree.file." + relative, "regular file", "non-regular", False,
                             "Only regular files may enter the archive.")
                continue
            try:
                size = candidate.stat().st_size
                digest = _sha256_path(candidate)
            except OSError as exc:
                recorder.add("tree.file." + relative, "readable regular file", type(exc).__name__,
                             False, "Evidence file could not be read and hashed.")
                continue
            recorder.add("tree.file." + relative, "readable regular file", size, True,
                         "Evidence file was read and hashed.")
            entries.append(InventoryEntry(relative, size, digest, candidate))
    entries.sort(key=lambda item: item.path)
    return entries


def _check_g0_summary(campaign: Path, required: Mapping[str, str], recorder: Recorder) -> None:
    relative = required.get("g0_summary")
    if relative is None:
        recorder.add("g0.summary", "exactly one G0 summary", None, False,
                     "G0 summary cannot be checked because required evidence is unresolved.")
        return
    summary = _read_json(campaign / relative, recorder, "g0.summary.json")
    if summary is None:
        return
    recorder.add("g0.gate", G0_GATE, summary.get("gate"), summary.get("gate") == G0_GATE,
                 "Archive acceptance requires the Test №2 Hard Gate G0 summary.")
    recorder.add("g0.verdict", "PASS", summary.get("verdict"), summary.get("verdict") == "PASS",
                 "A campaign may have a test FAIL, but accepted archive requires G0 PASS.")


def _validate_campaign(campaign: Path) -> tuple[Recorder, dict[str, str], list[InventoryEntry]]:
    recorder = Recorder()
    campaign = campaign.resolve()
    recorder.add("campaign.directory", "existing directory", str(campaign), campaign.is_dir(),
                 "Campaign root must exist.")
    if not campaign.is_dir():
        return recorder, {}, []
    required = _resolve_required(campaign, recorder)
    _check_g0_summary(campaign, required, recorder)
    entries = _walk_regular_files(campaign, recorder)
    recorder.add("tree.nonempty", "at least one regular file", len(entries), bool(entries),
                 "A campaign archive cannot be empty.")
    return recorder, required, entries


def _manifest(campaign: Path, required: Mapping[str, str], entries: list[InventoryEntry]) -> dict[str, Any]:
    return {
        "schema": ARCHIVE_SCHEMA,
        "campaign_id": campaign.name,
        "required_evidence": dict(sorted(required.items())),
        "files": [entry.as_dict() for entry in entries],
        "tree_sha256": _tree_digest(entries),
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _write_deterministic_zip(path: Path, entries: list[InventoryEntry], manifest_bytes: bytes) -> None:
    members: list[tuple[str, Optional[Path], Optional[bytes]]] = [
        (entry.path, entry.source, None) for entry in entries
    ]
    members.append((MANIFEST_NAME, None, manifest_bytes))
    members.sort(key=lambda item: item[0])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, strict_timestamps=True) as archive:
        for name, source_path, content in members:
            if source_path is not None:
                with source_path.open("rb") as source:
                    content = source.read()
            assert content is not None
            archive.writestr(_zip_info(name), content)


def _safe_zip_members(archive: zipfile.ZipFile, recorder: Recorder) -> list[str]:
    names = archive.namelist()
    duplicates = sorted({name for name in names if names.count(name) > 1})
    recorder.add("zip.duplicates", "none", duplicates, not duplicates,
                 "Duplicate ZIP members are rejected.")
    safe = True
    for name in names:
        valid = _safe_relative_text(name)
        recorder.add("zip.member." + name, "safe relative POSIX path", name, valid,
                     "Archive members cannot be absolute, use backslashes or traversal.")
        safe = safe and valid
    recorder.add("zip.members", "all safe", len(names), safe,
                 "Every ZIP member must have a canonical relative path.")
    return names


def _verify_zip(path: Path, expected_archive_hash: Optional[str],
                recorder: Recorder) -> Optional[dict[str, Any]]:
    exists = path.is_file()
    recorder.add("archive.present", "existing regular ZIP", str(path), exists,
                 "Archive must be written before verification.")
    if not exists:
        return None
    actual_archive_hash = _sha256_path(path)
    recorder.add("archive.sha256", expected_archive_hash or "self", actual_archive_hash,
                 expected_archive_hash is None or actual_archive_hash == expected_archive_hash,
                 "Archive bytes must match the receipt SHA-256 when verifying.")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            corrupt = archive.testzip()
            recorder.add("zip.crc", "no CRC error", corrupt, corrupt is None,
                         "ZIP CRC verification must succeed.")
            names = _safe_zip_members(archive, recorder)
            manifest_count = names.count(MANIFEST_NAME)
            recorder.add("manifest.member", "exactly one " + MANIFEST_NAME, manifest_count,
                         manifest_count == 1, "Archive requires exactly one internal manifest.")
            if manifest_count != 1:
                return None
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                recorder.add("manifest.json", "valid JSON object", type(exc).__name__, False,
                             "Internal manifest is unreadable or malformed.")
                return None
            is_object = isinstance(manifest, dict)
            recorder.add("manifest.json", "JSON object", type(manifest).__name__, is_object,
                         "Internal manifest root must be an object.")
            if not is_object:
                return None
            recorder.add("manifest.schema", ARCHIVE_SCHEMA, manifest.get("schema"),
                         manifest.get("schema") == ARCHIVE_SCHEMA,
                         "Internal manifest schema must match this archive format.")
            files = manifest.get("files")
            is_list = isinstance(files, list)
            recorder.add("manifest.files", "list", type(files).__name__, is_list,
                         "Internal manifest must inventory all evidence members.")
            if not is_list:
                return manifest
            inventory_paths = [item.get("path") for item in files if isinstance(item, Mapping)]
            valid_items = len(inventory_paths) == len(files) and all(
                isinstance(path_value, str) and _safe_relative_text(path_value)
                for path_value in inventory_paths)
            recorder.add("manifest.paths", "safe file paths", inventory_paths, valid_items,
                         "Manifest entries must have safe paths.")
            sorted_paths = inventory_paths == sorted(inventory_paths)
            recorder.add("manifest.order", "lexicographic paths", inventory_paths, sorted_paths,
                         "Manifest inventory order must be deterministic.")
            archive_files = [name for name in names if name != MANIFEST_NAME]
            recorder.add("manifest.coverage", sorted(archive_files), inventory_paths,
                         sorted(archive_files) == inventory_paths,
                         "Manifest must cover every and only evidence ZIP member.")
            actual_entries: list[InventoryEntry] = []
            for item in files:
                if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                    continue
                name = item["path"]
                try:
                    content = archive.read(name)
                except KeyError:
                    recorder.add("manifest.file." + name, "archive member", None, False,
                                 "Manifest references a missing member.")
                    continue
                actual_sha = hashlib.sha256(content).hexdigest()
                actual_size = len(content)
                expected_sha = item.get("sha256")
                expected_size = item.get("size_bytes")
                recorder.add("manifest.file." + name + ".sha256", expected_sha, actual_sha,
                             isinstance(expected_sha, str) and expected_sha == actual_sha,
                             "Evidence member hash must match manifest.")
                recorder.add("manifest.file." + name + ".size", expected_size, actual_size,
                             expected_size == actual_size,
                             "Evidence member size must match manifest.")
                actual_entries.append(InventoryEntry(name, actual_size, actual_sha, path))
            expected_tree = manifest.get("tree_sha256")
            actual_tree = _tree_digest(actual_entries)
            recorder.add("manifest.tree_sha256", expected_tree, actual_tree,
                         isinstance(expected_tree, str) and expected_tree == actual_tree,
                         "Tree SHA-256 must bind the complete ordered evidence inventory.")
            return manifest
    except (OSError, zipfile.BadZipFile) as exc:
        recorder.add("zip.open", "readable ZIP", type(exc).__name__, False,
                     "Archive cannot be opened or is malformed.")
        return None


def _receipt(campaign: Path, archive: Path, manifest_bytes: Optional[bytes],
             recorder: Recorder, stage: str) -> dict[str, Any]:
    return {
        "schema": "test2-campaign-archive-receipt-v1",
        "stage": stage,
        "verdict": "PASS" if recorder.passed else "FAIL",
        "campaign_id": campaign.name,
        "campaign": str(campaign),
        "archive": str(archive),
        "archive_sha256": _sha256_path(archive) if archive.is_file() else None,
        "internal_manifest_sha256": (
            hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes is not None else None
        ),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "checks": [check.as_dict() for check in recorder.checks],
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(payload, sort_keys=True, indent=2,
                                     ensure_ascii=False).encode("utf-8") + b"\n")
    temporary.replace(path)


def archive_campaign(campaign: Path, output_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    campaign = campaign.resolve()
    output_dir = output_dir.resolve()
    if _inside(campaign, output_dir) or _inside(output_dir, campaign):
        raise ValueError("--campaign and --output-dir must be disjoint directories")
    if not campaign.is_dir():
        raise ValueError("--campaign must be an existing directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise ValueError("--output-dir must be a directory")

    archive_path = output_dir / (campaign.name + ".evidence.zip")
    receipt_path = output_dir / (campaign.name + ".archive_receipt.json")
    if archive_path.exists() or receipt_path.exists():
        raise ValueError("archive or receipt already exists; choose a new --output-dir")

    recorder, required, entries = _validate_campaign(campaign)
    manifest_data = _manifest(campaign, required, entries)
    manifest_bytes = _canonical_json(manifest_data)
    temporary_archive = archive_path.with_suffix(archive_path.suffix + ".tmp")
    try:
        _write_deterministic_zip(temporary_archive, entries, manifest_bytes)
        temporary_archive.replace(archive_path)
    except OSError as exc:
        recorder.add("archive.write", "writable archive", type(exc).__name__, False,
                     "Archive could not be written.")
        try:
            temporary_archive.unlink(missing_ok=True)
        except OSError:
            pass

    _verify_zip(archive_path, None, recorder)
    receipt = _receipt(campaign, archive_path, manifest_bytes, recorder, "archive")
    _write_json_atomic(receipt_path, receipt)
    return receipt, archive_path, receipt_path


def verify_archive(archive_path: Path, receipt_path: Path) -> dict[str, Any]:
    recorder = Recorder()
    receipt = _read_json(receipt_path, recorder, "receipt.json")
    expected_hash: Optional[str] = None
    campaign_id = archive_path.stem
    if receipt is not None:
        recorder.add("receipt.schema", "test2-campaign-archive-receipt-v1", receipt.get("schema"),
                     receipt.get("schema") == "test2-campaign-archive-receipt-v1",
                     "Receipt schema must be explicit.")
        recorder.add("receipt.verdict", "PASS", receipt.get("verdict"),
                     receipt.get("verdict") == "PASS",
                     "Only an accepted archive receipt may verify as PASS.")
        expected_hash = receipt.get("archive_sha256")
        recorder.add("receipt.archive_sha256.format", "64 lowercase hex", expected_hash,
                     isinstance(expected_hash, str) and SHA256_RE.fullmatch(expected_hash) is not None,
                     "Receipt archive SHA-256 must be valid.")
        campaign_id = receipt.get("campaign_id") if isinstance(receipt.get("campaign_id"), str) else campaign_id
    else:
        recorder.add("receipt.contract", "valid receipt", None, False,
                     "Archive cannot be accepted without a valid receipt.")
    manifest = _verify_zip(archive_path, expected_hash, recorder)
    if receipt is not None and manifest is not None:
        recorder.add("receipt.campaign_id", receipt.get("campaign_id"), manifest.get("campaign_id"),
                     receipt.get("campaign_id") == manifest.get("campaign_id"),
                     "Receipt and internal manifest must identify the same campaign.")
        manifest_bytes: Optional[bytes] = None
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                manifest_bytes = archive.read(MANIFEST_NAME)
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
        actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes else None
        recorder.add("receipt.internal_manifest_sha256", receipt.get("internal_manifest_sha256"),
                     actual_manifest_hash,
                     isinstance(receipt.get("internal_manifest_sha256"), str) and
                     receipt.get("internal_manifest_sha256") == actual_manifest_hash,
                     "Receipt must bind the exact internal manifest bytes.")
    return {
        "schema": "test2-campaign-archive-verify-v1",
        "stage": "verify",
        "verdict": "PASS" if recorder.passed else "FAIL",
        "campaign_id": campaign_id,
        "archive": str(archive_path),
        "receipt": str(receipt_path),
        "checks": [check.as_dict() for check in recorder.checks],
    }


def _print_result(prefix: str, verdict: str, receipt_path: Optional[Path]) -> None:
    print(prefix + "=" + verdict)
    if receipt_path is not None:
        print("receipt=" + str(receipt_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    archive = commands.add_parser("archive", help="archive one completed campaign")
    archive.add_argument("--campaign", required=True, type=Path)
    archive.add_argument("--output-dir", required=True, type=Path)
    verify = commands.add_parser("verify", help="verify a prior archive and receipt")
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "archive":
            receipt, archive_path, receipt_path = archive_campaign(
                args.campaign, args.output_dir)
            _print_result("ARCHIVE", receipt["verdict"], receipt_path)
            print("archive=" + str(archive_path))
            return 0 if receipt["verdict"] == "PASS" else 2
        result = verify_archive(args.archive.resolve(), args.receipt.resolve())
        _print_result("ARCHIVE_VERIFY", result["verdict"], None)
        return 0 if result["verdict"] == "PASS" else 2
    except (OSError, ValueError) as exc:
        print("ARCHIVE=FAIL")
        print("error=" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
