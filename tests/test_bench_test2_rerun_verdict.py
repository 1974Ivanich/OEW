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


RAW_SAMPLE = {"i1": 2048, "i2": 2048, "ires": 2048, "raw_vbus": 2}
RAW_SAMPLE_COUNT = 20


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


def adc_line(raw: dict[str, int] | None = None) -> str:
    value = raw or RAW_SAMPLE
    return f"@ADC:I1={value['i1']}:I2={value['i2']}:Ires={value['ires']}:VBUS={value['raw_vbus']}\r\n> "


def valid_summary(status: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "execution": {"mode": "PHYSICAL", "scenario": None},
        "verdict": {
            "automation": "PASS",
            "scope": "PENDING",
            "final": "PENDING",
            "checks": {name: True for name in RERUN.REQUIRED_EVALUATOR_CHECKS},
            "status": status or terminal_status(),
            "preflight_adc": [dict(RAW_SAMPLE) for _ in range(RAW_SAMPLE_COUNT)],
            "sigrok_capture_returncode_zero": True,
            "sigrok_csv_exists": True,
        },
    }


def valid_metadata() -> dict[str, Any]:
    return {
        "execution": {"mode": "PHYSICAL", "scenario": None},
        "profile_id": RERUN.APPROVED_PROFILE_ID,
        "arguments": {"vbus_samples": RAW_SAMPLE_COUNT},
    }


def valid_uart(status: dict[str, int] | None = None, raw_samples: list[dict[str, int]] | None = None) -> str:
    terminal = status or terminal_status()
    samples = raw_samples or [dict(RAW_SAMPLE) for _ in range(RAW_SAMPLE_COUNT)]
    return "\n".join((
        "[2026-08-26T08:00:00+00:00] META",
        "mode=PHYSICAL; port=COM15; baud=115200",
        "[2026-08-26T08:00:00+00:00] RX",
        *[adc_line(sample) for sample in samples],
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


def write_campaign(
    tmp_path: Path,
    summary: dict[str, Any] | str | None = None,
    uart: str | None = None,
    *,
    attestation: bool = True,
    metadata: dict[str, Any] | str | None = None,
) -> Path:
    campaign = tmp_path / "test3_nohv_20260826T080000Z"
    campaign.mkdir()
    csv = campaign / "sigrok_digital.csv"
    csv.write_text("time,D0,D1\n0.000000,0,0\n", encoding="utf-8")

    if isinstance(summary, str):
        (campaign / "summary.json").write_text(summary, encoding="utf-8")
    else:
        summary_value = json.loads(json.dumps(summary if summary is not None else valid_summary()))
        summary_value["sigrok"] = {
            "csv_path": str(csv),
            "csv_exists": True,
            "csv_size_bytes": csv.stat().st_size,
        }
        (campaign / "summary.json").write_text(json.dumps(summary_value), encoding="utf-8")
    (campaign / "uart.log").write_text(uart if uart is not None else valid_uart(), encoding="utf-8")

    if isinstance(metadata, str):
        (campaign / "metadata.json").write_text(metadata, encoding="utf-8")
    else:
        (campaign / "metadata.json").write_text(json.dumps(metadata if metadata is not None else valid_metadata()), encoding="utf-8")

    if attestation:
        attestation_value = {
            "schema": RERUN.ATTESTATION_SCHEMA,
            "role": "bench-operator",
            "operator": "Operator One",
            "observed_at": "2026-08-26T08:01:00Z",
            "statement": "I observed the physical no-HV run and preserved the listed original evidence files.",
            "evidence": {
                "summary_sha256": RERUN.sha256_file(campaign / "summary.json"),
                "uart_log_sha256": RERUN.sha256_file(campaign / "uart.log"),
                "metadata_sha256": RERUN.sha256_file(campaign / "metadata.json"),
                "sigrok_csv": {"path": str(csv), "sha256": RERUN.sha256_file(csv)},
            },
        }
        (campaign / RERUN.ATTESTATION_NAME).write_text(json.dumps(attestation_value), encoding="utf-8")
    return campaign


def result_for(report: dict[str, Any], identifier: str) -> bool:
    return next(item["result"] for item in report["checks"] if item["id"] == identifier)


def test_complete_attested_evidence_passes_and_cli_writes_report(tmp_path: Path) -> None:
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


def test_unattested_synthetic_pair_is_rejected_even_if_its_text_looks_physical(tmp_path: Path) -> None:
    """Handwritten matching files cannot claim physical provenance by omission."""
    campaign = write_campaign(tmp_path, attestation=False)
    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-00-physical-attestation")
    assert not result_for(report, "RV-06b-human-attestation")


def test_legacy_adc_saturated_terminal_is_rejected(tmp_path: Path) -> None:
    failed = terminal_status(term=-11, detail=2, raw_vbus=0, vbus_mv=0, adc_status=8)
    campaign = write_campaign(tmp_path, valid_summary(failed), valid_uart(failed))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-05-summary-terminal")
    assert not result_for(report, "RV-08-uart-terminal-contract")


def test_contradictory_earlier_terminal_status_is_rejected(tmp_path: Path) -> None:
    bad = terminal_status(term=-11, detail=2, raw_vbus=0, vbus_mv=0, adc_status=8)
    good = terminal_status()
    campaign = write_campaign(
        tmp_path,
        valid_summary(good),
        valid_uart(good).replace(status_line(good), status_line(bad) + "\n" + status_line(good)),
    )

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-08-single-terminal-status")


def test_full_status_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    summary = terminal_status()
    uart = terminal_status(cap=999, periods=123, sector=5, window=42)
    campaign = write_campaign(tmp_path, valid_summary(summary), valid_uart(uart))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-08-summary-uart-full-match")


def test_simulated_summary_and_uart_are_never_physical_pass(tmp_path: Path) -> None:
    summary = valid_summary()
    summary["execution"]["mode"] = "SIMULATED"
    metadata = valid_metadata()
    metadata["execution"]["mode"] = "SIMULATED"
    campaign = write_campaign(tmp_path, summary, valid_uart().replace("mode=PHYSICAL", "mode=SIMULATED").replace("@MC:", "@SIM:@MC:"), metadata=metadata)

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-01-physical-execution")
    assert not result_for(report, "RV-10-no-simulation-markers")


def test_missing_evaluator_check_is_fail_closed(tmp_path: Path) -> None:
    summary = valid_summary()
    del summary["verdict"]["checks"]["terminal_adc_window_invalid"]
    campaign = write_campaign(tmp_path, summary)

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-04-summary-evaluator-claims")


def test_uart_summary_terminal_mismatch_is_rejected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path, valid_summary(), valid_uart(terminal_status(raw_vbus=3)))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert result_for(report, "RV-08-uart-terminal-contract")
    assert not result_for(report, "RV-08-summary-uart-full-match")


def test_missing_accepted_run_evidence_is_rejected(tmp_path: Path) -> None:
    campaign = write_campaign(tmp_path, uart=valid_uart().replace("@MC:RUN:rc=0", "@MC:RUN:rc=-7"))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-07-uart-arm-before-run")


def test_preflight_adc_is_recomputed_from_uart_not_only_summary_claims(tmp_path: Path) -> None:
    summary = valid_summary()
    bad_samples = [dict(RAW_SAMPLE) for _ in range(RAW_SAMPLE_COUNT)]
    bad_samples[0]["raw_vbus"] = RERUN.MAX_RAW_VBUS_HARD + 1
    campaign = write_campaign(tmp_path, summary, valid_uart(raw_samples=bad_samples))

    report = RERUN.build_verdict(campaign)

    assert report["terminal_verdict"] == "FAIL"
    assert not result_for(report, "RV-04b-uart-preflight-recomputed")
    assert not result_for(report, "RV-04c-summary-uart-preflight-match")


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
