from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO / "tools" / "bench_test2_rerun_verdict.py"
SPEC = importlib.util.spec_from_file_location("bench_test2_rerun_verdict", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
RERUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RERUN
SPEC.loader.exec_module(RERUN)


def terminal_status(**changes: int) -> dict[str, int]:
    status = {
        "state": 5,
        "term": -12,
        "cap": 7,
        "frames": 0,
        "dropped": 0,
        "periods": 1,
        "avail": 0,
        "detail": 7,
        "raw_vbus": 2,
        "vbus_mv": 201,
        "i1_ma": 0,
        "i2_ma": 0,
        "adc_status": 7,
        "sector": 0,
        "window": 0,
    }
    status.update(changes)
    return status


def status_line(status: dict[str, int]) -> str:
    return (
        "@MC:STATUS:"
        f"state={status['state']}:term={status['term']}:cap={status['cap']}:"
        f"frames={status['frames']}:dropped={status['dropped']}:periods={status['periods']}:avail={status['avail']}:"
        f"detail={status['detail']}:raw_vbus={status['raw_vbus']}:vbus_mv={status['vbus_mv']}:"
        f"i1_ma={status['i1_ma']}:i2_ma={status['i2_ma']}:adc_status={status['adc_status']}:"
        f"sector={status['sector']}:window={status['window']}\r\n> "
    )


def valid_summary(status: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "execution": {"mode": "PHYSICAL", "scenario": None},
        "verdict": {
            "automation": "PASS",
            "scope": "PENDING",
            "final": "PENDING",
            "checks": {name: True for name in RERUN.REQUIRED_EVALUATOR_CHECKS},
            "status": status or terminal_status(),
            "sigrok_capture_returncode_zero": True,
            "sigrok_csv_exists": True,
        },
    }


def valid_uart(status: dict[str, int] | None = None) -> str:
    terminal = status or terminal_status()
    return "\n".join((
        "[2026-08-26T08:00:00+00:00] META",
        "mode=PHYSICAL; port=COM15; baud=115200",
        "[2026-08-26T08:00:01+00:00] TX",
        "mcarm=1398361684",
        "[2026-08-26T08:00:01+00:00] RX",
        "@MC:ARM:cap=7:rc=0:offsets_valid=1:inj_start_rc=0\r\n> ",
        "[2026-08-26T08:00:02+00:00] TX",
        "mapcap run",
        "[2026-08-26T08:00:02+00:00] RX",
        "@MC:RUN:rc=0\r\n> ",
        "[2026-08-26T08:00:03+00:00] RX",
        status_line(terminal),
        "[2026-08-26T08:00:04+00:00] RX",
        "@MC:DRAIN:records=0\r\n> ",
    ))


def write_campaign(tmp_path: Path, summary: dict[str, Any] | str | None = None, uart: str | None = None) -> Path:
    campaign = tmp_path / "test2_nohv_20260826T080000Z"
    campaign.mkdir()
    if isinstance(summary, str):
        (campaign / "summary.json").write_text(summary, encoding="utf-8")
    elif summary is not None:
        (campaign / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    else:
        (campaign / "summary.json").write_text(json.dumps(valid_summary()), encoding="utf-8")
    (campaign / "uart.log").write_text(uart if uart is not None else valid_uart(), encoding="utf-8")
    return campaign


def result_for(report: dict[str, Any], identifier: str) -> bool:
    return next(item["result"] for item in report["checks"] if item["id"] == identifier)


def test_complete_physical_evidence_passes_and_cli_writes_report(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path)
    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "PASS"
    assert report["stage_a_60v"] == "BLOCKED"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--campaign", str(campaign)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "TERMINAL_VERDICT=PASS stage_a_60v=BLOCKED" in completed.stdout
    written = json.loads((campaign / RERUN.OUTPUT_NAME).read_text(encoding="utf-8"))
    assert written["terminal_verdict"] == "PASS"


def test_legacy_adc_saturated_terminal_is_rejected(tmp_path: Path) -> None:
    failed = terminal_status(term=-11, detail=2, raw_vbus=0, vbus_mv=0, adc_status=8)
    campaign = write_campaign(tmp_path, valid_summary(failed), valid_uart(failed))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-05-summary-terminal")
    assert not result_for(report, "RV-08-uart-terminal-contract")


def test_simulated_summary_and_uart_are_never_physical_pass(tmp_path: Path) -> None:
    summary = valid_summary()
    summary["execution"]["mode"] = "SIMULATED"
    campaign = write_campaign(tmp_path, summary, valid_uart().replace("mode=PHYSICAL", "mode=SIMULATED").replace("@MC:", "@SIM:@MC:"))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-01-physical-execution")
    assert not result_for(report, "RV-10-physical-provenance")


def test_missing_evaluator_check_is_fail_closed(tmp_path: Path) -> None:
    summary = valid_summary()
    del summary["verdict"]["checks"]["terminal_adc_window_invalid"]
    campaign = write_campaign(tmp_path, summary)

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-04-evaluator-checks")


def test_uart_summary_terminal_mismatch_is_rejected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path, valid_summary(), valid_uart(terminal_status(raw_vbus=3)))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert result_for(report, "RV-08-uart-terminal-contract")
    assert not result_for(report, "RV-08-summary-uart-match")


def test_missing_accepted_run_evidence_is_rejected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path, uart=valid_uart().replace("@MC:RUN:rc=0", "@MC:RUN:rc=-7"))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-07-uart-arm-before-run")


def test_malformed_summary_produces_fail_report_and_exit_two(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path, summary="{not-json")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--campaign", str(campaign)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    written = json.loads((campaign / RERUN.OUTPUT_NAME).read_text(encoding="utf-8"))
    assert written["terminal_verdict"] == "FAIL"
    assert not result_for(written, "RV-00-summary-json")
