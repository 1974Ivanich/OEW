"""Тесты валидатора операторского манифеста сессии M-подбора (TZ-02, шаг c).

Проверяют, что протокол сессии действительно машинно-проверяем: каждое правило
(S1..S5, B1..B15, C1..C2) имеет негативный тест, а пустой шаблон блокируется.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from validate_session_manifest import SCHEMA_VERSION, check_bundle, validate  # noqa: E402

TOOL = ROOT / "tools" / "validate_session_manifest.py"
TEMPLATE = ROOT / "tools" / "session_manifest_template.json"
FW = "6d3ba90235e7b81681f88ea305957dd7ba5b2a69f810f2d73dfe088cfe3e0201"
ART = "a" * 64
CRC_M0 = "0x95425CEB"
CRC_M1 = "0x11223344"


def burst(variant: str, run_id: str, *, base_crc: str = CRC_M0, variant_crc: str | None = None,
          pause: int = 6000, vbus: int = 30000, prev: str | None = None) -> dict:
    if variant_crc is None:
        variant_crc = CRC_M0 if variant == "M0-rebased" else CRC_M1
    b = {
        "run_id": run_id,
        "variant_id": variant,
        "base_map_id": "M0-rebased",
        "base_map_crc32": base_crc,
        "variant_map_id": variant,
        "variant_map_crc32": variant_crc,
        "artifact_sha256": ART,
        "firmware_sha256": FW,
        "timestamp_start": "2026-09-19T06:00:00Z",
        "timestamp_end": "2026-09-19T06:00:02Z",
        "vbus_target_mv": vbus,
        "current_limit_ma": 1000,
        "burst_duration_ms": 800,
        "pause_before_ms": pause,
        "operator1": "A",
        "operator2": "B",
        "telemetry": {
            "raw_log": f"logs/{run_id}.log",
            "identity_file": "identity.txt",
            "artifact_file": f"artifacts/{variant}.bin",
            "preflight": ["sysinfo", "p?", "pdump", "enc", "mapcap identity"],
            "postflight": ["sysinfo", "p?", "pdump", "breakdiag"],
        },
        "map_load_ok": True,
        "run_id_ack": True,
        "stop_gate": {"triggered": False, "reason": None},
        "break_snapshot_archived": None,
    }
    if variant != "M0-rebased":
        b["previous_variant_comparison"] = {
            "variant_id": prev or "M0-rebased",
            "report": "reports/M0_vs_prev.json",
            "analyzed": True,
        }
        b["baseline_frozen"] = {"file": "M0_BASELINE_FROZEN.json", "sha256": "f" * 64,
                                "baseline_crc32": CRC_M0}
    return b


def manifest(bursts: list[dict] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "session": {
            "session_id": "tz2-m-tuning-20260919",
            "date_utc": "2026-09-19T06:00:00Z",
            "operator1": "A",
            "operator2": "B",
            "firmware_sha256": FW,
            "firmware_image": "firmware_tz2_map_capable_6d3ba902.bin",
            "envelope": {"vbus_min_mv": 24000, "vbus_max_mv": 36000, "current_limit_ma": 1000,
                         "burst_max_ms": 1000, "pause_min_ms": 5000,
                         "safety_owner_approval": "TZ-REF-01 / 2026-09-19 / safety-owner"},
            "live_identity": {"source": "@MAP:IDENTITY", "captured_utc": "2026-09-19T05:55:00Z",
                              "file": "identity.txt",
                              "values": {"board_revision": 7, "pwm_frequency_hz": 5000,
                                         "timer_arr": 999, "adc_trigger_id": 1330468145,
                                         "trigger_offset_ticks": 0, "deadtime_ticks": 192,
                                         "adc_clock_hz": 42500000, "adc_sample_cycles_x2": 1281,
                                         "adc_resolution": 0, "adc_config_signature": 649705851,
                                         "current_calibration_signature": 3918514988}},
        },
        "bursts": bursts if bursts is not None else [burst("M0-rebased", "M0-R1")],
    }


def run_cli(tmp_path: Path, data: dict, bundle: Path | None = None) -> subprocess.CompletedProcess:
    p = tmp_path / "session_manifest.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, str(TOOL), str(p)]
    if bundle:
        cmd += ["--bundle", str(bundle)]
    return subprocess.run(cmd, capture_output=True, text=True)


def rules(problems) -> set[str]:
    return {item.split("]")[0].lstrip("[") for item in problems.items}


def test_empty_template_is_blocked() -> None:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    problems = validate(data)
    assert problems, "пустой шаблон обязан блокироваться"
    assert {"S2", "S4", "B3", "B11"} <= rules(problems)


def test_valid_single_baseline_burst_passes() -> None:
    assert not validate(manifest())


def test_valid_sequence_m0_m1_passes() -> None:
    assert not validate(manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")]))


def test_schema_version_enforced() -> None:
    m = manifest()
    m["schema_version"] = "v2"
    assert "S1" in rules(validate(m))


def test_missing_safety_owner_approval_blocks() -> None:
    m = manifest()
    m["session"]["envelope"]["safety_owner_approval"] = ""
    assert "S4" in rules(validate(m))


def test_same_operator_blocks() -> None:
    m = manifest()
    m["session"]["operator2"] = m["session"]["operator1"]
    assert "S2" in rules(validate(m))


def test_firmware_must_be_identical_across_bursts() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    m["bursts"][1]["firmware_sha256"] = "b" * 64
    assert "B4" in rules(validate(m))


def test_base_map_must_be_common() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1", base_crc="0xDEADBEEF")])
    assert "B5" in rules(validate(m))


def test_baseline_crc_must_equal_base_crc() -> None:
    m = manifest([burst("M0-rebased", "M0-R1", variant_crc=CRC_M1)])
    assert "B6" in rules(validate(m))


def test_duplicate_variant_crc_blocked() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"),
                  burst("M1", "M1-R1", variant_crc=CRC_M1),
                  burst("M2", "M2-R1", variant_crc=CRC_M1, prev="M1")])
    assert "B7" in rules(validate(m))


def test_duplicate_run_id_blocked() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M0-R1")])
    assert "B2" in rules(validate(m))


def test_envelope_violations_blocked() -> None:
    for kwargs, rule in (({"vbus": 40000}, "B10"), ({"vbus": 10000}, "B10"),
                         ({"pause": 1000}, "B9")):
        m = manifest([burst("M0-rebased", "M0-R1", **kwargs)])
        assert rule in rules(validate(m)), (kwargs, rule)


def test_procedure_must_be_identical_only_m_changes() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    m["bursts"][1]["burst_duration_ms"] = 900
    assert "B8" in rules(validate(m))


def test_protection_and_load_gates() -> None:
    m = manifest()
    m["bursts"][0]["map_load_ok"] = False
    assert "B11" in rules(validate(m))
    m = manifest()
    m["bursts"][0]["stop_gate"] = {"triggered": True, "reason": "FAULT!=0"}
    assert "B12" in rules(validate(m))


def test_break_snapshot_requires_two_readings() -> None:
    m = manifest()
    m["bursts"][0]["break_snapshot_archived"] = {"file": "brk.txt", "sha256": "c" * 64,
                                                "readings": 1}
    assert "B13" in rules(validate(m))
    m["bursts"][0]["break_snapshot_archived"]["readings"] = 2
    assert "B13" not in rules(validate(m))


def test_analysis_of_previous_variant_required() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    del m["bursts"][1]["previous_variant_comparison"]
    assert "B14" in rules(validate(m))
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    m["bursts"][1]["previous_variant_comparison"]["analyzed"] = False
    assert "B14" in rules(validate(m))


def test_previous_variant_must_be_the_preceding_one() -> None:
    m = manifest([burst("M0-rebased", "M0-R1"),
                  burst("M1", "M1-R1", variant_crc=CRC_M1),
                  burst("M2", "M2-R1", variant_crc="0x55667788", prev="M0-rebased")])
    assert "B14" in rules(validate(m))


def test_timestamps_and_sha_format() -> None:
    m = manifest()
    m["bursts"][0]["timestamp_end"] = "2026-09-19T05:00:00Z"
    assert "B15" in rules(validate(m))
    m = manifest()
    m["bursts"][0]["artifact_sha256"] = "zz"
    assert "B3" in rules(validate(m))


def test_cli_and_bundle_checks(tmp_path: Path) -> None:
    m = manifest([burst("M0-rebased", "M0-R1")])
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "M0-R1.log").write_text("log", encoding="utf-8")
    (tmp_path / "identity.txt").write_text("id", encoding="utf-8")
    (tmp_path / "artifacts").mkdir()
    artifact = tmp_path / "artifacts" / "M0-rebased.bin"
    artifact.write_bytes(b"map")
    import hashlib
    m["bursts"][0]["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    proc = run_cli(tmp_path, m, bundle=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "GATE PASS" in proc.stdout

    (tmp_path / "artifacts" / "M0-rebased.bin").unlink()
    proc = run_cli(tmp_path, m, bundle=tmp_path)
    assert proc.returncode == 1
    assert "[C1]" in proc.stdout

    problems = check_bundle(m, tmp_path)
    assert "C1" in rules(problems)


def test_bundle_detects_artifact_sha_mismatch(tmp_path: Path) -> None:
    m = manifest()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "M0-R1.log").write_text("log", encoding="utf-8")
    (tmp_path / "identity.txt").write_text("id", encoding="utf-8")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "M0-rebased.bin").write_bytes(b"map")
    problems = check_bundle(m, tmp_path)
    assert "C2" in rules(problems), "sha256 артефакта на диске обязан сверяться с манифестом"


def test_baseline_must_be_frozen_before_next_variant() -> None:
    """B16: M1 не проектируется, пока M0 baseline не заморожен."""
    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    del m["bursts"][1]["baseline_frozen"]
    rules_found = rules(validate(m))
    assert "B16" in rules_found

    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    m["bursts"][1]["baseline_frozen"]["sha256"] = "not-a-sha"
    assert "B16" in rules(validate(m))

    m = manifest([burst("M0-rebased", "M0-R1"), burst("M1", "M1-R1")])
    m["bursts"][1]["baseline_frozen"]["baseline_crc32"] = "0xDEADBEEF"
    assert "B16" in rules(validate(m))

    assert "B16" not in rules(validate(manifest([burst("M0-rebased", "M0-R1"),
                                                burst("M1", "M1-R1")])))


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, str(TOOL), str(tmp_path / "nope.json")],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert "BLOCKED" in proc.stderr
