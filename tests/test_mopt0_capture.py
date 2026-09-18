"""Tests for the M-OPT-0 no-HV baseline campaign tool (tools/mopt0_capture.py).

These tests pin the honesty contract of the tool:
  * the no-HV gate is statistical and fail-closed on malformed samples;
  * a run without firmware-side identity evidence (run_id/map_id/map_crc32) is
    BLOCKED and can never make the campaign PASS — that is the current main
    firmware state, so this is a regression guard, not a hypothetical;
  * simulated campaigns are labelled SIMULATED and are never verified as a
    physical baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import mopt0_capture as mopt


def adc_frame(i1: int = 2048, i2: int = 2048, ires: int = 2048, vbus: int = 2) -> str:
    return f"@ADC:I1={i1}:I2={i2}:Ires={ires}:VBUS={vbus}\r\n"


# ── no-HV gate ────────────────────────────────────────────────────────────

def test_no_hv_gate_accepts_quiet_bus() -> None:
    samples = mopt.parse_adc_raws([adc_frame(vbus=value) for value in (0, 1, 2, 3, 4)])
    gate = mopt.evaluate_no_hv_gate(samples)
    assert gate["verdict"] == "PASS"
    assert gate["raw_vbus_median"] == 2
    assert gate["raw_vbus_max"] == 4
    assert gate["samples"] == 5


def test_no_hv_gate_rejects_median_violation() -> None:
    samples = mopt.parse_adc_raws([adc_frame(vbus=value) for value in (30, 40, 50)])
    gate = mopt.evaluate_no_hv_gate(samples)
    assert gate["verdict"] == "FAIL"
    assert "raw_vbus_median_nohv" in gate["failed_checks"]


def test_no_hv_gate_rejects_single_gross_excursion() -> None:
    # Median stays low, but the hard cap guards against a gross anomaly.
    samples = mopt.parse_adc_raws([adc_frame(vbus=value) for value in (1, 2, 3, 400)])
    gate = mopt.evaluate_no_hv_gate(samples)
    assert gate["verdict"] == "FAIL"
    assert "raw_vbus_max_hard_limit" in gate["failed_checks"]


def test_no_hv_gate_rejects_rail_saturation() -> None:
    samples = mopt.parse_adc_raws([adc_frame(i1=0), adc_frame(i2=4095)])
    gate = mopt.evaluate_no_hv_gate(samples)
    assert gate["verdict"] == "FAIL"
    assert set(gate["failed_checks"]) >= {"i1_away_from_rails", "i2_away_from_rails"}


def test_no_hv_gate_fails_closed_on_malformed_sample() -> None:
    assert mopt.parse_adc_raws(["@ADC:I1=1:I2=2\r\n"]) is None
    gate = mopt.evaluate_no_hv_gate(None)
    assert gate["verdict"] == "FAIL"
    assert gate["samples"] == 0


# ── identity evidence ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "@RUN:ID=M0-R1\r\n",
    "@FOC:t=1000:run_id=M0-R1:VBUS=2\r\n",
])
def test_identity_accepts_both_run_id_spellings(text: str) -> None:
    identity = mopt.extract_identity(text + ":map_id=M0:map_crc32=0x1A2B3C4D\r\n")
    assert identity["run_id"] == "M0-R1"
    assert identity["map_id"] == "M0"
    assert identity["map_crc32"] == "0x1A2B3C4D"
    assert identity["present"] is True


def test_identity_reports_every_missing_field() -> None:
    identity = mopt.extract_identity("@ADC:I1=2048:I2=2048:Ires=2048:VBUS=2\r\n")
    assert identity["present"] is False
    assert identity["missing"] == ["map_crc32", "map_id", "run_id"]


def test_log_integrity_flags_loss_markers_and_foreign_run() -> None:
    assert mopt.check_log_integrity("", "M0-R1") == ["log is empty"]
    assert mopt.check_log_integrity("line overflow\r\n", "M0-R1") == [
        "evidence-loss marker present: line overflow"]
    issues = mopt.check_log_integrity("@RUN:ID=M0-R2\r\n", "M0-R1")
    assert issues == ["foreign run id in log: M0-R2"]


# ── per-run verdict ───────────────────────────────────────────────────────

def good_log(run_id: str, with_identity: bool) -> str:
    text = f"@SYSINFO:board=OEW-G474-REV7\r\n"
    if with_identity:
        text += f"@RUN:ID={run_id}\r\n"
    text += ("@PWM:default_deny=1:MOE=0\r\n"
             "@ADC:I1=2048:I2=2048:Ires=2048:VBUS=2\r\n"
             "@ADC:CAL:offset_i1=2048:offset_i2=2048\r\n"
             "@ENC:angle=0:speed=0:err=0\r\n"
             "@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0\r\n")
    if with_identity:
        text += ":map_id=M0:map_crc32=0x1A2B3C4D\r\n"
    return text


def good_responses(ack: bool = True) -> dict[str, str]:
    return {
        "run_id_set": (
            "@RUN:ID=M0-R1\r\n> " if ack
            else "err: run id must be 1..23 chars [A-Za-z0-9_.-]\r\n> "),
        "sysinfo": "@SYSINFO:board=OEW-G474-REV7\r\n",
        "pwm_before": "@PWM:default_deny=1:MOE=0\r\n",
        "pdump_before": "@PWMD:TIM1:CR1=0x0000:MOE=0\r\n",
        "calibration": "@ADC:CAL:offset_i1=2048:offset_i2=2048\r\n",
        "encoder": "@ENC:angle=0:speed=0:err=0\r\n",
        "status_before": "@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0\r\n",
        **{f"adc_{index:03d}": adc_frame() for index in range(3)},
    }


def test_current_firmware_state_is_blocked_not_passed() -> None:
    """main.c emits no @RUN:ID/run_id/map_id/map_crc32: the run must be BLOCKED."""
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=False),
                                good_responses(ack=False), "f" * 64)
    assert verdict["verdict"] == "BLOCKED_MISSING_IDENTITY"
    assert verdict["no_hv_gate"]["verdict"] == "PASS"
    assert set(verdict["failed_checks"]) >= {
        "run_id_ack", "identity_run_id", "identity_map_id", "identity_map_crc32"}


def test_run_passes_only_with_identity_and_no_hv_gate() -> None:
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                                good_responses(), "f" * 64)
    assert verdict["verdict"] == "PASS"
    assert verdict["failed_checks"] == []


def test_run_fails_when_no_hv_gate_fails() -> None:
    responses = good_responses()
    responses["adc_000"] = adc_frame(vbus=400)
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                                responses, "f" * 64)
    assert verdict["verdict"] == "FAIL"
    assert "no_hv_gate" in verdict["failed_checks"]


def test_capture_rows_are_never_expected_in_a_baseline_run() -> None:
    log = good_log("M0-R1", with_identity=True) + "@MC:REC:cap=1:seq=1\r\n"
    verdict = mopt.evaluate_run("M0-R1", log, good_responses(), "f" * 64)
    assert verdict["verdict"] == "FAIL"
    assert "capture_rows_absent" in verdict["failed_checks"]


# ── campaign verdict ──────────────────────────────────────────────────────

def run_verdict(run_id: str, verdict: str,
                map_id: str = "M0", map_crc32: str = "1A2B3C4D") -> dict:
    """Minimal per-run verdict with the identity a real PASS run always has."""
    return {"run_id": run_id, "verdict": verdict,
            "identity": {"map_id": map_id, "map_crc32": map_crc32}}


def test_campaign_requires_unique_runs_and_provenance() -> None:
    runs = [run_verdict(f"M0-R{i}", "PASS") for i in range(1, 6)]
    assert mopt.campaign_verdict(runs, "f" * 64, "a" * 40)["verdict"] == "PASS"
    assert mopt.campaign_verdict(runs, "f" * 64, None)["verdict"] == "INCOMPLETE_PROVENANCE"
    duplicated = [run_verdict("M0-R1", "PASS")] * 5
    assert mopt.campaign_verdict(duplicated, "f" * 64, "a" * 40)["verdict"] == "FAIL"


def test_campaign_with_missing_firmware_identity_is_incomplete() -> None:
    runs = [run_verdict(f"M0-R{i}", "BLOCKED_MISSING_IDENTITY") for i in range(1, 6)]
    campaign = mopt.campaign_verdict(runs, "f" * 64, "a" * 40)
    assert campaign["verdict"] == "INCOMPLETE_IDENTITY"
    assert campaign["mopt0_complete"] is False


# ── offline log reconstruction ────────────────────────────────────────────

def test_rebuild_responses_from_log_round_trip(tmp_path: Path) -> None:
    transport = mopt.SimulatedTransport("identity-present", tmp_path / "uart.log", "M0-R3")
    transport.open()
    for command in ("run=M0-R3", "sysinfo", "p?", "pdump", "a", "a", "c", "enc",
                    "mapcap status"):
        transport.command(command)
    transport.close()

    responses = mopt.rebuild_responses_from_log(
        (tmp_path / "uart.log").read_text(encoding="utf-8"))
    assert responses["run_id_set"].startswith("@RUN:ID=M0-R3")
    assert responses["sysinfo"].startswith("@SYSINFO")
    assert "default_deny=1" in responses["pwm_before"]
    assert "MOE=0" in responses["pdump_before"]
    assert "@ADC:CAL:" in responses["calibration"]
    assert responses["encoder"].startswith("@ENC:")
    assert responses["status_before"].startswith("@MC:STATUS:")
    adc = [value for key, value in responses.items() if key.startswith("adc_")]
    assert len(adc) == 2
    assert all("@ADC:I1=" in value for value in adc)


# ── end-to-end simulation ─────────────────────────────────────────────────

def run_sim(tmp_path: Path, scenario: str, runs: int = 5) -> tuple[int, dict, Path]:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00\x01\x02\x03")
    campaign_dir = tmp_path / f"campaign_{scenario}"
    rc = mopt.main([
        "run",
        "--simulate", scenario,
        "--campaign", str(campaign_dir),
        "--firmware-bin", str(firmware),
        "--source-sha", "b" * 40,
        "--runs", str(runs),
    ])
    summary = json.loads((campaign_dir / "summary.json").read_text(encoding="utf-8"))
    return rc, summary, campaign_dir


def test_simulation_with_identity_records_real_artifacts(tmp_path: Path) -> None:
    rc, summary, campaign_dir = run_sim(tmp_path, "identity-present")
    assert rc == 0
    assert summary["execution"]["mode"] == "SIMULATED"
    assert summary["campaign"]["verdict"] == "SIMULATED"
    assert summary["campaign"]["mopt0_complete"] is False
    for index in range(1, 6):
        run_dir = campaign_dir / f"M0-R{index}"
        assert (run_dir / "uart.log").is_file()
        verdict = json.loads((run_dir / f"M0-R{index}.json").read_text(encoding="utf-8"))
        assert verdict["verdict"] == "PASS"
        assert verdict["identity"]["run_id"] == f"M0-R{index}"


def test_simulation_without_identity_matches_current_firmware(tmp_path: Path) -> None:
    rc, summary, campaign_dir = run_sim(tmp_path, "identity-absent")
    assert rc == 1
    assert summary["campaign"]["verdict"] == "INCOMPLETE_IDENTITY"
    for index in range(1, 6):
        verdict = json.loads((campaign_dir / f"M0-R{index}" / f"M0-R{index}.json")
                             .read_text(encoding="utf-8"))
        assert verdict["verdict"] == "BLOCKED_MISSING_IDENTITY"
        assert verdict["no_hv_gate"]["verdict"] == "PASS"


def test_simulation_no_hv_violation_fails_the_run(tmp_path: Path) -> None:
    rc, summary, _ = run_sim(tmp_path, "nohv-violated")
    assert rc == 1
    assert summary["campaign"]["verdict"] == "FAIL"
    assert summary["runs"][0]["no_hv_gate"]["verdict"] == "FAIL"


def test_physical_run_requires_safety_confirmations(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00")
    campaign_dir = tmp_path / "must_not_exist"
    rc = mopt.main([
        "run", "--port", "COM99", "--campaign", str(campaign_dir),
        "--firmware-bin", str(firmware),
    ])
    assert rc == 2
    assert not campaign_dir.exists()


def test_physical_run_requires_port(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00")
    rc = mopt.main([
        "run", "--campaign", str(tmp_path / "must_not_exist"),
        "--firmware-bin", str(firmware),
        "--confirm-dc-link-disconnected", "--confirm-pc4-zero", "--confirm-sd-high",
    ])
    assert rc == 2
    assert not (tmp_path / "must_not_exist").exists()


def test_run_refuses_to_overwrite_an_existing_campaign(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00")
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    rc = mopt.main([
        "run", "--simulate", "identity-present", "--campaign", str(campaign_dir),
        "--firmware-bin", str(firmware), "--source-sha", "b" * 40,
    ])
    assert rc == 2


# ── offline verification ──────────────────────────────────────────────────

def test_verify_simulated_campaign_is_never_a_pass(tmp_path: Path) -> None:
    _, _, campaign_dir = run_sim(tmp_path, "identity-present")
    rc = mopt.main(["verify", "--campaign", str(campaign_dir)])
    assert rc == 1
    report = json.loads((campaign_dir / "verify_report.json").read_text(encoding="utf-8"))
    assert report["verify_verdict"] == "SIMULATED"
    assert report["mismatches"] == []
    assert report["note"]


def test_verify_physical_campaign_with_identity_passes(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00\x01")
    sha = mopt.sha256_file(firmware)
    campaign_dir = tmp_path / "physical"
    campaign_dir.mkdir()
    mopt.write_json(campaign_dir / "metadata.json", {
        "execution": {"mode": "PHYSICAL", "scenario": None},
        "run_prefix": "M0-R", "runs_requested": 5, "vbus_samples": 3,
        "identity_source": "firmware",
        "firmware_sha256": sha, "source_sha": "c" * 40,
    })
    for index in range(1, 6):
        run_id = f"M0-R{index}"
        run_dir = campaign_dir / run_id
        run_dir.mkdir()
        transport = mopt.SimulatedTransport("identity-present", run_dir / "uart.log", run_id)
        transport.open()
        responses = mopt.collect_run_evidence(transport, 3, run_id)
        transport.close()
        log_text = (run_dir / "uart.log").read_text(encoding="utf-8")
        verdict = mopt.evaluate_run(run_id, log_text, responses, sha, "firmware")
        mopt.write_json(run_dir / f"{run_id}.json", verdict)

    rc = mopt.main(["verify", "--campaign", str(campaign_dir)])
    assert rc == 0
    report = json.loads((campaign_dir / "verify_report.json").read_text(encoding="utf-8"))
    assert report["verify_verdict"] == "PASS"
    assert report["campaign"]["runs_pass"] == 5


def test_verify_detects_a_tampered_log(tmp_path: Path) -> None:
    _, _, campaign_dir = run_sim(tmp_path, "identity-present")
    victim = campaign_dir / "M0-R2" / "uart.log"
    victim.write_text(victim.read_text(encoding="utf-8") + "line overflow\r\n", encoding="utf-8")
    rc = mopt.main(["verify", "--campaign", str(campaign_dir)])
    assert rc == 1
    report = json.loads((campaign_dir / "verify_report.json").read_text(encoding="utf-8"))
    assert report["verify_verdict"] == "MISMATCH"
    assert any("M0-R2" in item for item in report["mismatches"])


# ── run-id contract (command first, firmware acknowledgement required) ────

def test_run_id_command_is_sent_first_and_substituted(tmp_path: Path) -> None:
    transport = mopt.SimulatedTransport("identity-present", tmp_path / "uart.log", "M0-R7")
    transport.open()
    responses = mopt.collect_run_evidence(transport, 2, "M0-R7")
    transport.close()
    assert transport.command_sequence[0] == "run=M0-R7"
    assert responses["run_id_set"].startswith("@RUN:ID=M0-R7")


def test_run_id_command_template_is_overridable(tmp_path: Path) -> None:
    transport = mopt.SimulatedTransport("identity-present", tmp_path / "uart.log", "M0-R2")
    transport.open()
    mopt.collect_run_evidence(transport, 1, "M0-R2", run_id_command="setid {run_id}")
    transport.close()
    assert transport.command_sequence[0] == "setid M0-R2"


def test_rejected_run_id_blocks_the_run() -> None:
    """Firmware refusing the identifier must block, even with identity in @FOC."""
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                               good_responses(ack=False), "f" * 64)
    assert verdict["verdict"] == "BLOCKED_MISSING_IDENTITY"
    assert "run_id_ack" in verdict["failed_checks"]


def test_simulation_run_id_rejection_is_incomplete(tmp_path: Path) -> None:
    rc, summary, campaign_dir = run_sim(tmp_path, "run-id-rejected")
    assert rc == 1
    assert summary["campaign"]["verdict"] == "INCOMPLETE_IDENTITY"
    verdict = json.loads((campaign_dir / "M0-R1" / "M0-R1.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "BLOCKED_MISSING_IDENTITY"
    assert "run_id_ack" in verdict["failed_checks"]


# ── bench-side truncation counter and cross-run identity consistency ───────

def test_observed_uart_truncation_fails_the_run() -> None:
    """A non-zero uart_trunc means an @FOC packet was rejected as oversized."""
    responses = good_responses()
    responses["sysinfo"] = ("@SYS:CLK=170000000:PSC=169:TCLK=170000000:PLLCFGR=0x1234:"
                            "OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=3:uart_trunc=1\r\n")
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                                responses, "f" * 64)
    assert verdict["verdict"] == "FAIL"
    assert "uart_truncation_zero" in verdict["failed_checks"]
    assert verdict["uart_health"]["uart_trunc"] == 1
    assert verdict["uart_health"]["reported"] is True


def test_clean_uart_counters_pass_the_run() -> None:
    responses = good_responses()
    responses["sysinfo"] = ("@SYS:CLK=170000000:PSC=169:TCLK=170000000:PLLCFGR=0x1234:"
                            "OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=0:uart_trunc=0\r\n")
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                                responses, "f" * 64)
    assert verdict["verdict"] == "PASS"
    assert verdict["uart_health"] == {"uart_trunc": 0, "uart_drp": 0,
                                      "reported": True, "note": None}


def test_image_without_uart_counters_is_reported_not_silently_passed() -> None:
    """Old images do not report the counters: record that fact explicitly."""
    verdict = mopt.evaluate_run("M0-R1", good_log("M0-R1", with_identity=True),
                                good_responses(), "f" * 64)
    assert verdict["verdict"] == "PASS"
    assert verdict["uart_health"]["reported"] is False
    assert "budget package absent" in verdict["uart_health"]["note"]


def test_campaign_rejects_inconsistent_map_identity() -> None:
    runs = [{"run_id": f"M0-R{i}", "verdict": "PASS",
             "identity": {"map_id": "M0", "map_crc32": "1A2B3C4D"}} for i in range(1, 6)]
    campaign = mopt.campaign_verdict(runs, "f" * 64, "a" * 40)
    assert campaign["verdict"] == "PASS"
    assert campaign["map_id"] == "M0"
    assert campaign["map_crc32"] == "1A2B3C4D"

    runs[4]["identity"]["map_crc32"] = "DEADBEEF"
    campaign = mopt.campaign_verdict(runs, "f" * 64, "a" * 40)
    assert campaign["verdict"] == "FAIL"
    assert "map_crc32_identical" in campaign["failed_checks"]

    runs[4]["identity"]["map_crc32"] = "1A2B3C4D"
    runs[3]["identity"]["map_id"] = "M1"
    campaign = mopt.campaign_verdict(runs, "f" * 64, "a" * 40)
    assert campaign["verdict"] == "FAIL"
    assert "map_id_identical" in campaign["failed_checks"]


# ── PC-3 pre-flight (read-only gate before the first M0-R1) ────────────────

def preflight_responses(trunc: str = "0", drp: str = "0") -> dict[str, str]:
    responses = good_responses()
    responses["sysinfo"] = ("@SYSINFO:board=OEW-G474-REV7:fw=1.0:CLK=170000000:"
                            f"OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp={drp}:uart_trunc={trunc}\r\n")
    return responses


def test_preflight_passes_on_a_ready_image() -> None:
    report = mopt.evaluate_preflight("M0-R1", good_log("M0-R1", with_identity=True),
                                    preflight_responses(), "c" * 64)
    assert report["status"] == "PASS"
    assert report["failed_checks"] == []
    assert report["uart_health"]["uart_trunc"] == 0
    assert report["identity"]["map_id"] == "M0"


def test_preflight_blocks_image_without_telemetry_counters() -> None:
    """An image without uart_trunc cannot prove the checked sender is in use."""
    report = mopt.evaluate_preflight("M0-R1", good_log("M0-R1", with_identity=True),
                                    good_responses(), "c" * 64)
    assert report["status"] == "BLOCKED"
    assert "preflight_telemetry_counters_reported" in report["failed_checks"]


def test_preflight_fails_when_the_transport_dropped_packets() -> None:
    report = mopt.evaluate_preflight("M0-R1", good_log("M0-R1", with_identity=True),
                                    preflight_responses(trunc="2", drp="9"), "c" * 64)
    assert report["status"] == "FAIL"
    assert "preflight_uart_trunc_zero" in report["failed_checks"]


def test_preflight_blocks_foreign_map_identity() -> None:
    log = good_log("M0-R1", with_identity=True).replace("map_id=M0", "map_id=M1")
    report = mopt.evaluate_preflight("M0-R1", log, preflight_responses(), "c" * 64)
    assert report["status"] == "BLOCKED"
    assert "preflight_map_id_expected" in report["failed_checks"]


def test_preflight_blocks_missing_firmware_identity() -> None:
    # The run-id command was acknowledged, but no @FOC identity ever appeared.
    report = mopt.evaluate_preflight("M0-R1", good_log("M0-R1", with_identity=False),
                                    preflight_responses(trunc="0"), "c" * 64)
    assert report["status"] == "BLOCKED"
    assert {"identity_map_id", "identity_map_crc32",
            "preflight_map_id_expected"} <= set(report["failed_checks"])


def test_preflight_blocks_when_run_id_is_not_acknowledged() -> None:
    report = mopt.evaluate_preflight("M0-R1", good_log("M0-R1", with_identity=True),
                                    good_responses(ack=False), "c" * 64)
    assert report["status"] == "BLOCKED"
    assert "run_id_ack" in report["failed_checks"]


def test_preflight_simulated_run_is_never_physical(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00\x01")
    campaign_dir = tmp_path / "preflight"
    rc = mopt.main([
        "preflight", "--simulate", "preflight-ready", "--run-id", "M0-R1",
        "--firmware-bin", str(firmware), "--campaign", str(campaign_dir),
    ])
    assert rc == 0
    report = json.loads((campaign_dir / "preflight.json").read_text(encoding="utf-8"))
    assert report["status"] == "SIMULATED"
    assert report["mode"] == "SIMULATED"
    assert "physical pre-flight requires the real bench" in report["note"]
    assert report["command_sequence"][0] == "run=M0-R1"


def test_preflight_requires_firmware_bin(tmp_path: Path) -> None:
    rc = mopt.main(["preflight", "--simulate", "preflight-ready",
                    "--campaign", str(tmp_path / "must_not_exist")])
    assert rc == 2
    assert not (tmp_path / "must_not_exist").exists()


def test_preflight_physical_requires_confirmations_and_port(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00")
    rc = mopt.main(["preflight", "--firmware-bin", str(firmware),
                    "--campaign", str(tmp_path / "must_not_exist")])
    assert rc == 2
    assert not (tmp_path / "must_not_exist").exists()


def test_preflight_refuses_to_overwrite_existing_directory(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00")
    campaign_dir = tmp_path / "exists"
    campaign_dir.mkdir()
    rc = mopt.main(["preflight", "--simulate", "preflight-ready",
                    "--firmware-bin", str(firmware), "--campaign", str(campaign_dir)])
    assert rc == 2
