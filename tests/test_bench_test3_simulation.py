"""End-to-end software-HIL tests for the production test №2 execution pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import bench_test3_capture as capture


EXPECTED_GOOD_SEQUENCE = [
    "sysinfo",
    "p?",
    "pdump",
    *(["a"] * capture.DEFAULT_VBUS_SAMPLES),
    "c",
    "enc",
    "mapcap status",
    f"mcarm={capture.APPROVED_TEST3_PROFILE_ID}",
    "mapcap status",
    "mapcap run",
    "mapcap status",
    "mapcap drain",
    "p?",
    "pdump",
]


def run_scenario(tmp_path: Path, scenario: str, timeout: str = "0.02") -> tuple[int, dict, Path]:
    output_dir = tmp_path / scenario
    rc = capture.main([
        "--simulate-uart", scenario,
        "--simulate-sigrok",
        "--terminal-timeout-seconds", timeout,
        "--terminal-poll-seconds", "0.001",
        "--output-dir", str(output_dir),
    ])
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    return rc, summary, output_dir


def assert_common_simulation_evidence(summary: dict, output_dir: Path) -> None:
    assert summary["execution"] == {"mode": "SIMULATED", "scenario": summary["execution"]["scenario"]}
    assert summary["verdict"]["scope"] == "NOT_APPLICABLE"
    assert summary["verdict"]["final"] == "SIMULATED"
    assert (output_dir / "uart.log").is_file()
    assert (output_dir / "metadata.json").is_file()
    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "sigrok.stdout").is_file()
    assert (output_dir / "sigrok.stderr").is_file()
    assert "f" not in summary["commands"]["sequence"]


def test_vbus_low_valid_runs_complete_production_pipeline(tmp_path: Path) -> None:
    rc, summary, output_dir = run_scenario(tmp_path, "vbus-low-valid")
    assert rc == 0
    assert_common_simulation_evidence(summary, output_dir)
    assert summary["verdict"]["automation"] == "PASS"
    assert summary["commands"]["sequence"] == EXPECTED_GOOD_SEQUENCE
    assert summary["capture_events"] == ["sigrok:start", "sigrok:collect"]
    timeline = summary["orchestration_events"]
    assert timeline.index("capture:start") < timeline.index("uart:mapcap run") < timeline.index("capture:collect")
    assert (output_dir / "sigrok.csv").is_file()
    uart_log = (output_dir / "uart.log").read_text(encoding="utf-8")
    assert "mapcap run" in uart_log
    assert "detail=7" in uart_log


@pytest.mark.parametrize("scenario", [
    "i1-limit", "i2-limit", "vbus-high", "adc-invalid", "records",
    "sigrok-fail", "missing-csv",
])
def test_terminal_and_capture_negative_scenarios_fail_without_fault_clear(tmp_path: Path, scenario: str) -> None:
    rc, summary, output_dir = run_scenario(tmp_path, scenario)
    assert rc == 1
    assert_common_simulation_evidence(summary, output_dir)
    assert summary["verdict"]["automation"] == "FAIL"
    assert "mapcap run" in summary["commands"]["sequence"]
    assert (output_dir / "sigrok.csv").exists() is (scenario != "missing-csv")


def test_arm_failure_never_sends_run(tmp_path: Path) -> None:
    rc, summary, output_dir = run_scenario(tmp_path, "arm-fail")
    assert rc == 1
    assert_common_simulation_evidence(summary, output_dir)
    assert summary["verdict"]["failure_stage"] == "ARM"
    assert "mapcap run" not in summary["commands"]["sequence"]


def test_run_failure_never_retries_run_or_polls_terminal(tmp_path: Path) -> None:
    rc, summary, output_dir = run_scenario(tmp_path, "run-fail")
    assert rc == 1
    assert_common_simulation_evidence(summary, output_dir)
    assert summary["verdict"]["failure_stage"] == "RUN"
    assert summary["commands"]["sequence"].count("mapcap run") == 1
    assert summary["commands"]["sequence"].count("mapcap status") == 2


def test_timeout_fails_with_absolute_terminal_poll_stage(tmp_path: Path) -> None:
    rc, summary, output_dir = run_scenario(tmp_path, "timeout", timeout="0.01")
    assert rc == 1
    assert_common_simulation_evidence(summary, output_dir)
    assert summary["verdict"]["failure_stage"] == "TERMINAL_POLL"
    assert "mapcap drain" not in summary["commands"]["sequence"]


def test_simulation_requires_both_backends(tmp_path: Path) -> None:
    rc = capture.main([
        "--simulate-uart", "vbus-low-valid",
        "--output-dir", str(tmp_path / "must_fail"),
    ])
    assert rc == 2
    assert not (tmp_path / "must_fail").exists()
