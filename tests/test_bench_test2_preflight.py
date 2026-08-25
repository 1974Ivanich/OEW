"""Offline regression tests for the fail-closed Test №2 CLI pre-flight validator."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "bench_test2_preflight.py"
SPEC = importlib.util.spec_from_file_location("bench_test2_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PREFLIGHT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def valid_scope() -> dict:
    return {
        "target": "physical-nohv-diagnostic",
        "test": "MAPCAP_TEST2",
        "allows_oew_host_test": True,
        "allows_physical_nohv_execution": True,
        "diagnostic_only": True,
        "forbids_stage_a": True,
        "forbids_production": True,
        "forbids_dc_link": True,
        "forbids_foc": True,
        "forbids_vf": True,
        "forbids_autotune": True,
    }


def valid_responses() -> dict[str, str]:
    return {
        "sysinfo": "@SYSINFO:build=diagnostic\r\n> ",
        "p?": "@PWM:MOE=0\r\n> ",
        "pdump": "@PWM:default_deny=1\r\n> ",
        "a": "@ADC:I1=2048:I2=2047:Ires=2048:VBUS=2\r\n> ",
        "c": "@ADC:CAL:offset_i1=2048:offset_i2=2047:offset_ires=2048\r\n> ",
        "enc": "@ENC:angle=0:speed=0:err=0\r\n> ",
        "mapcap status": (
            "@MC:STATUS:state=0:term=0:cap=7:frames=0:dropped=0:periods=1:avail=0:"
            "detail=0:raw_vbus=2:vbus_mv=201:i1_ma=0:i2_ma=0:adc_status=7:sector=0:window=0\r\n> "
        ),
    }


def write_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "test2_nohv_20260825T120000Z"
    campaign.mkdir()
    write_json(campaign / "g0_approval.json", {
        "decision": "APPROVED",
        "scope": valid_scope(),
    })
    write_json(campaign / "diagnostic_build_manifest.json", {
        "target": "physical-nohv-diagnostic",
        "test": "MAPCAP_TEST2",
        "defines_complete": True,
        "defines": dict(PREFLIGHT.REQUIRED_DEFINES),
    })
    (campaign / "diagnostic_build.log").write_text("diagnostic build\n", encoding="utf-8")
    write_json(campaign / "g0_check_summary.json", {"gate": "HIL_TEST2_G0", "verdict": "PASS"})
    return campaign


def parse_args(campaign: Path, *extra: str):
    required = [
        "--campaign", str(campaign),
        "--confirm-dc-link-disconnected",
        "--confirm-pc4-zero",
        "--confirm-sd-high",
        "--confirm-sigrok-connected",
    ]
    return PREFLIGHT.build_parser().parse_args([*required, *extra])


def failed_ids(summary: dict) -> set[str]:
    return {item["id"] for item in summary["checks"] if item["result"] == "FAIL"}


def test_offline_mode_never_constructs_real_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = write_campaign(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("real UART must not be opened in offline mode")

    monkeypatch.setattr(PREFLIGHT, "observe_uart", forbidden)
    summary, output = PREFLIGHT.run_preflight(parse_args(campaign, "--offline"))

    assert summary["verdict"] == "FAIL"
    assert output == campaign / "preflight_summary.json"
    assert "uart.physical_identity" in failed_ids(summary)
    assert "sigrok.scan" in failed_ids(summary)
    assert output.is_file()


def test_valid_mocked_real_preflight_passes_with_safe_sequence(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = write_campaign(tmp_path)
    observed: list[str] = []

    def fake_observe(port: str, baud: int, log_path: Path, vbus_samples: int):
        assert port == "COM77"
        assert baud == 115200
        responses = valid_responses()
        a_response = responses["a"]
        responses["a"] = [a_response] * vbus_samples
        sequence = []
        for command in PREFLIGHT.SAFE_UART_COMMANDS:
            if command == "a":
                sequence.extend(["a"] * vbus_samples)
            else:
                sequence.append(command)
        log_path.write_text("mock UART evidence\n", encoding="utf-8")
        observed.extend(sequence)
        return responses, sequence

    def fake_find(cli: str | None) -> str:
        assert cli == "sigrok-cli.exe"
        return cli

    def fake_scan(cli: str, output_path: Path):
        output_path.write_text("1 device found\n", encoding="utf-8")
        return True, "1 device found\n", [cli, "--scan"]

    monkeypatch.setattr(PREFLIGHT, "observe_uart", fake_observe)
    monkeypatch.setattr(PREFLIGHT._CAPTURE, "find_sigrok_cli", fake_find)
    monkeypatch.setattr(PREFLIGHT, "scan_sigrok", fake_scan)

    summary, output = PREFLIGHT.run_preflight(parse_args(
        campaign, "--port", "COM77", "--scan-sigrok", "--sigrok-cli", "sigrok-cli.exe"))

    assert summary["verdict"] == "PASS"
    assert summary["mode"] == "UART"
    expected_sequence = (list(PREFLIGHT.SAFE_UART_COMMANDS[:3])
                         + ["a"] * PREFLIGHT._CAPTURE.DEFAULT_VBUS_SAMPLES
                         + list(PREFLIGHT.SAFE_UART_COMMANDS[4:]))
    assert observed == expected_sequence
    assert not any(command.startswith("mcarm") or command in PREFLIGHT.FORBIDDEN_UART_COMMANDS
                   for command in summary["uart"]["command_sequence"])
    assert output is not None and output.is_file()
    assert not (campaign / "preflight_summary.json.tmp").exists()
    assert (campaign / "preflight_uart.log").is_file()
    assert (campaign / "preflight_sigrok_scan.log").is_file()


def test_missing_confirmation_blocks_real_uart_before_open(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = write_campaign(tmp_path)
    called = False

    def fake_observe(*args, **kwargs):
        nonlocal called
        called = True
        return valid_responses(), list(PREFLIGHT.SAFE_UART_COMMANDS)

    monkeypatch.setattr(PREFLIGHT, "observe_uart", fake_observe)
    args = PREFLIGHT.build_parser().parse_args([
        "--campaign", str(campaign), "--port", "COM77",
        "--confirm-dc-link-disconnected", "--confirm-pc4-zero", "--confirm-sd-high",
    ])
    summary, _ = PREFLIGHT.run_preflight(args)

    assert summary["verdict"] == "FAIL"
    assert not called
    assert "operator.sigrok_connected" in failed_ids(summary)
    assert "uart.real_mode_preconditions" in failed_ids(summary)


def test_g0_failure_blocks_real_uart_before_open(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = write_campaign(tmp_path)
    write_json(campaign / "g0_check_summary.json", {"gate": "HIL_TEST2_G0", "verdict": "FAIL"})

    monkeypatch.setattr(PREFLIGHT, "observe_uart", lambda *args: pytest.fail("UART must stay closed"))
    summary, _ = PREFLIGHT.run_preflight(parse_args(campaign, "--port", "COM77"))

    assert summary["verdict"] == "FAIL"
    assert "g0.summary.verdict" in failed_ids(summary)
    assert "uart.real_mode_preconditions" in failed_ids(summary)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda campaign: (campaign / "g0_approval.json").unlink(), "g0.file.g0_approval.json"),
        (lambda campaign: _set_scope(campaign, "forbids_stage_a", False), "g0.approval.scope.forbids_stage_a"),
        (lambda campaign: _set_define(campaign, "OEW_HOST_TEST", "0"), "g0.manifest.define.OEW_HOST_TEST"),
        (lambda campaign: (campaign / "g0_check_summary.json").write_text("{bad", encoding="utf-8"), "g0.summary.json"),
        (lambda campaign: write_json(campaign / "g0" / "g0_check_summary.json", {"gate": "HIL_TEST2_G0", "verdict": "PASS"}), "g0.summary.path"),
    ],
)
def test_each_critical_g0_defect_fails_closed(tmp_path: Path, mutate, expected: str) -> None:
    campaign = write_campaign(tmp_path)
    mutate(campaign)

    summary, _ = PREFLIGHT.run_preflight(parse_args(campaign, "--offline"))

    assert summary["verdict"] == "FAIL"
    assert expected in failed_ids(summary)


def test_transcript_parses_but_never_grants_physical_go(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    transcript = tmp_path / "responses.json"
    write_json(transcript, {"responses": valid_responses()})

    summary, _ = PREFLIGHT.run_preflight(parse_args(campaign, "--offline", "--uart-transcript", str(transcript)))

    assert summary["verdict"] == "FAIL"
    assert "uart.physical_identity" in failed_ids(summary)
    assert all(check["id"] != "uart.adc" or check["result"] == "PASS" for check in summary["checks"])


@pytest.mark.parametrize(
    ("command", "response", "expected"),
    [
        ("p?", "@PWM:MOE=1\n", "uart.pwm_off.p?"),
        ("a", "@ADC:I1=2048:I2=2048:Ires=2048:VBUS=10\n", "uart.adc"),
        ("c", "@ADC:CAL:FAIL\n", "uart.calibration"),
        ("enc", "@ENC:err=-1\n", "uart.encoder"),
        ("mapcap status", "@MC:STATUS:state=1:term=0\n", "uart.mapcap_idle"),
        ("sysinfo", "@CLI:ERR:nope\n", "uart.response.sysinfo"),
    ],
)
def test_unsafe_uart_observations_fail_closed(command: str, response: str, expected: str) -> None:
    recorder = PREFLIGHT.Recorder()
    responses = valid_responses()
    responses[command] = response

    PREFLIGHT.validate_uart_responses(responses, recorder, "TEST")

    assert not recorder.passed
    assert expected in {check.check_id for check in recorder.checks if not check.passed}


def test_a_list_with_noise_passes_uart_adc() -> None:
    recorder = PREFLIGHT.Recorder()
    responses = valid_responses()
    noisy = ["@ADC:I1=2048:I2=2047:Ires=2048:VBUS=2\r\n> "] * PREFLIGHT._CAPTURE.DEFAULT_VBUS_SAMPLES
    noisy[2] = "@ADC:I1=2048:I2=2047:Ires=2048:VBUS=61\r\n> "
    noisy[7] = "@ADC:I1=2048:I2=2047:Ires=2048:VBUS=18\r\n> "
    responses["a"] = noisy

    PREFLIGHT.validate_uart_responses(responses, recorder, "TEST")

    failures = {check.check_id for check in recorder.checks if not check.passed}
    assert "uart.adc" not in failures


def test_a_list_max_above_hard_limit_fails() -> None:
    recorder = PREFLIGHT.Recorder()
    responses = valid_responses()
    spiky = ["@ADC:I1=2048:I2=2047:Ires=2048:VBUS=2\r\n> "] * PREFLIGHT._CAPTURE.DEFAULT_VBUS_SAMPLES
    spiky[0] = "@ADC:I1=2048:I2=2047:Ires=2048:VBUS=250\r\n> "
    responses["a"] = spiky

    PREFLIGHT.validate_uart_responses(responses, recorder, "TEST")

    failures = {check.check_id for check in recorder.checks if not check.passed}
    assert "uart.adc" in failures


def test_transcript_accepts_a_as_list(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    transcript = tmp_path / "responses.json"
    responses = valid_responses()
    responses["a"] = [responses["a"]] * PREFLIGHT._CAPTURE.DEFAULT_VBUS_SAMPLES
    write_json(transcript, {"responses": responses})

    summary, _ = PREFLIGHT.run_preflight(parse_args(campaign, "--offline", "--uart-transcript", str(transcript)))

    assert summary["verdict"] == "FAIL"  # no physical identity, as designed
    assert "uart.physical_identity" in failed_ids(summary)
    assert all(check["id"] != "uart.adc" or check["result"] == "PASS" for check in summary["checks"])


@pytest.mark.parametrize(
    ("response", "expected_off"),
    [
        ("@PWM:MOE=0\r\n> ", True),
        ("@PWM:default_deny=1\r\n> ", True),
        # Реальные форматы диагностической прошивки (без маркеров):
        ("@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0\r\n> ", True),                # dec, MOE=0
        ("@PWM:FULL:SYS=170000000:T1:PSC=16:ARR=999:CCR=0,0,0:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0:T8:PSC=16:ARR=999:CCR=0,0,0:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0\r\n> ", True),
        ("@PWM:CR1=224:CCER=0:BDTR=0x00009CC0:CNT=0\r\n> ", False),         # hex, MOE=1
        ("@PWM:FULL:T1:BDTR=0x00001CC0:T8:BDTR=0x00009CC0\r\n> ", False),   # один таймер с MOE=1
        ("@PWM:CR1=224:CCER=0:CNT=0\r\n> ", False),                        # нет BDTR/маркеров -> не доказано
        ("@PWM:MOE=1\r\n> ", False),
        ("", False),
    ],
)
def test_pwm_off_evidence_formats(response: str, expected_off: bool) -> None:
    assert PREFLIGHT.pwm_off_evidence(response) is expected_off


def test_pwm_off_evidence_accepts_real_firmware_pdump() -> None:
    recorder = PREFLIGHT.Recorder()
    responses = valid_responses()
    responses["p?"] = "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0\r\n> "
    responses["pdump"] = ("@PWM:FULL:SYS=170000000:CFGR=0x0000000F:T1:PSC=16:ARR=999:CCR=0,0,0:"
                          "BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0:T8:PSC=16:ARR=999:"
                          "CCR=0,0,0:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0\r\n> ")

    PREFLIGHT.validate_uart_responses(responses, recorder, "TEST")

    failures = {check.check_id for check in recorder.checks if not check.passed}
    assert "uart.pwm_off.p?" not in failures
    assert "uart.pwm_off.pdump" not in failures


def test_extra_or_control_command_in_transcript_is_rejected() -> None:
    recorder = PREFLIGHT.Recorder()
    responses = valid_responses()
    responses["mcarm=1398361684"] = "@MC:ARM:cap=7:rc=0\n"

    PREFLIGHT.validate_uart_responses(responses, recorder, "TEST")

    failures = {check.check_id for check in recorder.checks if not check.passed}
    assert "uart.command_sequence" in failures
    assert "uart.forbidden.mcarm=1398361684" in failures


def test_cli_offline_returns_fail_closed_summary(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    completed = subprocess.run([
        sys.executable, str(SCRIPT), "--campaign", str(campaign), "--offline",
        "--confirm-dc-link-disconnected", "--confirm-pc4-zero",
        "--confirm-sd-high", "--confirm-sigrok-connected",
    ], text=True, capture_output=True, check=False)

    assert completed.returncode == 2
    assert "PREFLIGHT=FAIL" in completed.stdout
    assert (campaign / "preflight_summary.json").is_file()


def test_invalid_campaign_does_not_write_summary(tmp_path: Path) -> None:
    inside_git = REPO / "temporary_preflight_campaign"
    inside_git.mkdir(exist_ok=True)
    try:
        args = parse_args(inside_git, "--offline")
        summary, output = PREFLIGHT.run_preflight(args)
        assert summary["verdict"] == "FAIL"
        assert output is None
        assert not (inside_git / "preflight_summary.json").exists()
    finally:
        inside_git.rmdir()


def _set_scope(campaign: Path, key: str, value) -> None:
    path = campaign / "g0_approval.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scope"][key] = value
    write_json(path, payload)


def _set_define(campaign: Path, key: str, value: str) -> None:
    path = campaign / "diagnostic_build_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["defines"][key] = value
    write_json(path, payload)
