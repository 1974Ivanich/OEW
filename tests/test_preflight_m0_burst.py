"""Тесты gate G0..G9 перед первым M0 burst (TZ-02, шаг c).

Каждый гейт имеет негативный тест: правильный firmware, живая identity, authoritative
M0-rebased, SHA/CRC артефакта, утверждение safety-owner, оба оператора, валидный манифест,
пакет файлов, preflight по логу.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from preflight_m0_burst import CAVEAT, parse_identity_text, run_gates  # noqa: E402

TOOL = ROOT / "tools" / "preflight_m0_burst.py"
LIVE = {"board_revision": 7, "pwm_frequency_hz": 5000, "timer_arr": 999,
        "adc_trigger_id": 1329944369, "trigger_offset_ticks": 0, "deadtime_ticks": 192,
        "adc_clock_hz": 42500000, "adc_sample_cycles_x2": 1281, "adc_resolution": 0,
        "adc_config_signature": 649705851, "current_calibration_signature": 3918514988}
CRC_M0 = "0x95425CEB"

PREFLIGHT_LOG = "\n".join([
    "[2026-09-19T06:00:00Z] META", "[2026-09-19T06:00:01Z] BOOT",
    "@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x4150:OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=0:uart_trunc=0",
    "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0",
    "@PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:CR2=0x00000000:CCER=0x00000000",
    "@ENC:angle=0:speed=0:period_us=0:pulse_us=0:err=0",
    "@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:"
    "sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0xE98FCB2C",
    "@MAP:LOAD:OK",
])


def build_bundle(tmp: Path, *, log_text: str = PREFLIGHT_LOG, manifest_mut=None,
                 rebase_mut=None) -> tuple[Path, dict]:
    bundle = tmp / "session"
    (bundle / "logs").mkdir(parents=True)
    (bundle / "artifacts").mkdir(parents=True)
    fw = bundle / "firmware_tz2.bin"
    fw.write_bytes(b"firmware-image")
    fw_sha = hashlib.sha256(fw.read_bytes()).hexdigest()
    artifact = bundle / "artifacts" / "M0-rebased.bin"
    artifact.write_bytes(b"map-artifact-497")
    art_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (bundle / "identity.txt").write_text(
        "\n".join(f"{k}={v}" for k, v in LIVE.items()) + "\n", encoding="utf-8")
    (bundle / "logs" / "M0-R1.log").write_text(log_text + "\n", encoding="utf-8")

    rebase = {"variant": "M0-rebased", "operation": "identity_rebase",
              "source_dataset": {"characterization_id": "0x424F4152",
                                 "dataset_crc32": "0xC3FA2C1B"},
              "identity_rebased": True, "coefficients_changed": False,
              "identity_change_count": 3,
              "crc32": {"before": "0x0A8A8DAB", "after": CRC_M0}}
    if rebase_mut:
        rebase_mut(rebase)
    (bundle / "M0_rebased.json").write_text(json.dumps(rebase, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

    manifest = {
        "schema_version": "tz2-session-manifest-1",
        "session": {"session_id": "tz2-m-tuning-20260919", "date_utc": "2026-09-19T06:00:00Z",
                    "operator1": "A", "operator2": "B", "firmware_sha256": fw_sha,
                    "firmware_image": "firmware_tz2.bin",
                    "envelope": {"vbus_min_mv": 24000, "vbus_max_mv": 36000,
                                 "current_limit_ma": 1000, "burst_max_ms": 1000,
                                 "pause_min_ms": 5000,
                                 "safety_owner_approval": "TZ-REF-01 / 2026-09-19"},
                    "live_identity": {"source": "@MAP:IDENTITY",
                                      "captured_utc": "2026-09-19T05:55:00Z",
                                      "file": "identity.txt", "values": dict(LIVE)}},
        "bursts": [{
            "run_id": "M0-R1", "variant_id": "M0-rebased", "base_map_id": "M0-rebased",
            "base_map_crc32": CRC_M0, "variant_map_id": "M0-rebased",
            "variant_map_crc32": CRC_M0, "artifact_sha256": art_sha, "firmware_sha256": fw_sha,
            "timestamp_start": "2026-09-19T06:01:00Z", "timestamp_end": "2026-09-19T06:01:02Z",
            "vbus_target_mv": 30000, "current_limit_ma": 1000, "burst_duration_ms": 800,
            "pause_before_ms": 6000, "operator1": "A", "operator2": "B",
            "telemetry": {"raw_log": "logs/M0-R1.log", "identity_file": "identity.txt",
                          "artifact_file": "artifacts/M0-rebased.bin",
                          "preflight": ["sysinfo", "p?", "pdump", "enc", "mapcap identity"],
                          "postflight": ["sysinfo", "p?", "pdump", "breakdiag"]},
            "map_load_ok": True, "run_id_ack": True,
            "stop_gate": {"triggered": False, "reason": None},
            "break_snapshot_archived": None}],
    }
    if manifest_mut:
        manifest_mut(manifest)
    return bundle, manifest


def status_of(gate, gid: str) -> str | None:
    for i, s, _ in gate.rows:
        if i == gid:
            return s
    return None


def test_all_gates_pass(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(tmp_path)
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert not gate.failed, gate.render()
    assert status_of(gate, "G0") == "PASS"
    assert status_of(gate, "G8") == "PASS"


def test_firmware_sha_mismatch_fails_g0(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["session"].update({"firmware_sha256": "0" * 64}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G0") == "FAIL"


def test_identity_mismatch_fails_g1(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["session"]["live_identity"]["values"].update(
            {"pwm_frequency_hz": 294}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G1") == "FAIL"
    detail = next(d for i, s, d in gate.rows if i == "G1")
    assert "pwm_frequency_hz" in detail


def test_missing_identity_file_fails_g1(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(tmp_path)
    (bundle / "identity.txt").unlink()
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G1") == "FAIL"


def test_rebase_manifest_required(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(tmp_path)
    (bundle / "M0_rebased.json").unlink()
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G2") == "FAIL"


def test_coefficients_changed_fails_g2(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, rebase_mut=lambda r: r.update({"coefficients_changed": True}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G2") == "FAIL"


def test_rebase_crc_must_match_burst(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, rebase_mut=lambda r: r["crc32"].update({"after": "0xDEADBEEF"}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G2") == "FAIL"


def test_artifact_sha_mismatch_fails_g3(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["bursts"][0].update({"artifact_sha256": "a" * 64}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G3") == "FAIL"


def test_missing_approval_fails_g4(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["session"]["envelope"].update(
            {"safety_owner_approval": ""}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G4") == "FAIL"


def test_same_operator_fails_g5(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["session"].update({"operator2": "A"}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G5") == "FAIL"


def test_invalid_manifest_fails_g6(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(
        tmp_path, manifest_mut=lambda m: m["bursts"][0].update({"stop_gate": {"triggered": True}}))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G6") == "FAIL"


def test_missing_log_fails_g7_and_g8(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(tmp_path)
    (bundle / "logs" / "M0-R1.log").unlink()
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G7") == "FAIL"
    assert status_of(gate, "G8") == "FAIL"


@pytest.mark.parametrize("mutation,item", [
    (lambda t: t.replace("uart_drp=0:uart_trunc=0", "uart_drp=2:uart_trunc=0"), "uart_drp"),
    (lambda t: t.replace("CCER=0:BDTR=7360", "CCER=3:BDTR=7360"), "CCER"),
    (lambda t: t.replace("ARR=999", "ARR=994"), "ARR"),
    (lambda t: t.replace("err=0", "err=5"), "err"),
    (lambda t: t + "\n@BRK:valid=1:seq=1:src=TIM8", "@BRK"),
    (lambda t: t.replace("@SYS:", "@XX:"), "@SYS"),
    (lambda t: t.replace("@MAP:LOAD:OK", "@MAP:LOAD:FAIL:CRC"), "@MAP:LOAD:OK"),
])
def test_preflight_defects_fail_g8(tmp_path: Path, mutation, item: str) -> None:
    bundle, manifest = build_bundle(tmp_path, log_text=mutation(PREFLIGHT_LOG))
    gate = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None)
    assert status_of(gate, "G8") == "FAIL", item
    detail = next(d for i, s, d in gate.rows if i == "G8")
    assert item in detail


def test_cli_reports_blocked_and_caveat_on_pass(tmp_path: Path) -> None:
    bundle, manifest = build_bundle(tmp_path)
    mp = bundle / "session_manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), "--manifest", str(mp),
                           "--bundle", str(bundle), "--json", str(bundle / "gate.json")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "G9: M0 burst разрешён" in proc.stdout
    assert CAVEAT in proc.stdout
    report = json.loads((bundle / "gate.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["caveat"] == CAVEAT

    broken, manifest2 = build_bundle(tmp_path / "second",
                                     manifest_mut=lambda m: m["session"].update({"operator2": "A"}))
    mp2 = broken / "session_manifest.json"
    mp2.write_text(json.dumps(manifest2, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), "--manifest", str(mp2), "--bundle", str(broken)],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert "G9: M0 burst НЕ разрешён" in proc.stdout


def test_identity_parser_handles_log_and_live_txt() -> None:
    from_log = parse_identity_text(PREFLIGHT_LOG)
    from_txt = parse_identity_text("\n".join(f"{k}={v}" for k, v in LIVE.items()))
    assert from_log == from_txt == LIVE
    assert parse_identity_text("@MAP:IDENTITY:board=7:pwm=5000") is None or \
        len(parse_identity_text("@MAP:IDENTITY:board=7:pwm=5000")) < 11
