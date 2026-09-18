"""Тесты offline comparator'а вариантов карты (TZ-02, шаг b).

Проверяют зафиксированные правила: единственный baseline = M0-rebased (CRC-гейт),
отсутствие агрегированного рейтинга, idc только для repeatability, Ires как ZSV-канал,
N/A для метрик без независимого источника, BETTER/SAME/WORSE по каждой метрике.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from map_variant_compare import DEFAULT_TOL, build_report, compute_metrics, parse_log  # noqa: E402  # noqa: E501

TOOL = ROOT / "tools" / "map_variant_compare.py"
CRC_M0 = "0x95425CEB"


def make_log(tmp: Path, name: str, *, map_id: str = "M0-rebased", crc: str = CRC_M0,
             id_err: int = 10, iq_err: int = 5, ripple: int = 4, ires: int = 3,
             fault: int = 0, brk: int = 0, trunc: int = 0, drp: int = 0,
             adc_spread: int = 2, rows: int = 20) -> Path:
    lines = ["[2026-09-19T06:00:00+00:00] META", "mode=PHYSICAL", "[utc] BOOT"]
    for i in range(rows):
        t = 1000 + i * 100
        idv = 100 + (id_err if i % 2 == 0 else -id_err)
        iqv = (ripple if i % 2 == 0 else -ripple)
        ccr = 500 + (i % 3)
        lines.append(
            f"@FOC:t={t}:run_id={name}:map_id={map_id}:map_crc32={crc}:I1=0:I2=0:Ires={ires}:"
            f"Id={idv}:Iq={iqv}:Id_ref=100:Iq_ref=0:VBUS=201:STATE=0:SPD=0:TH=0:sector={i % 6}:"
            f"window={i % 2}:CCR1={ccr}:CCR2=500:CCR3=500:ADC_STATUS=0:FAULT={fault}:"
            f"FAULT_R={18 if fault else 0}:FAIL=0:RUN=0:em_stop1=1:em_stop2=1"
        )
    for i in range(20):
        lines.append(f"@ADC:I1={2039 + (i % max(1, adc_spread))}:I2=2068:Ires=2041:VBUS={2 + (i % 3)}")
    lines.append(f"@SYS:CLK=170000000:PSC=16:TCLK=10000000:uart_drp={drp}:uart_trunc={trunc}")
    if brk:
        lines.append("@BRK:valid=1:seq=1:src=TIM8:cyc=0:sr=1,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0:cap=0,0,0")
    p = tmp / f"{name}.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def metrics_of(tmp: Path, log: Path, name: str = "M0") -> dict:
    parsed = parse_log(log)
    parsed["metrics"] = compute_metrics(parsed, DEFAULT_TOL, None, None, 1.0)
    return parsed


def test_parses_foc_and_adc(tmp_path: Path) -> None:
    log = make_log(tmp_path, "M0")
    parsed = parse_log(log)
    assert parsed["row_count"] == 20
    assert len(parsed["adc"]) == 20
    assert parsed["t_monotonic"] is True
    assert parsed["sysinfo"] is not None


def test_baseline_crc_gate_rejects_old_artifact(tmp_path: Path) -> None:
    """Baseline обязан быть M0-rebased: старый CRC (0x0A8A8DAB) → REJECT rc=1."""
    old = make_log(tmp_path, "M0", map_id="M0", crc="0x0A8A8DAB")
    new = make_log(tmp_path, "M1")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--baseline", "M0", "--baseline-crc32", CRC_M0,
         "--run", f"M0={old}", "--run", f"M1={new}"],
        capture_output=True, text=True)
    assert proc.returncode == 1
    assert "не совпал с --baseline-crc32" in proc.stderr


def test_baseline_crc_gate_accepts_rebased(tmp_path: Path) -> None:
    base = make_log(tmp_path, "M0")
    variant = make_log(tmp_path, "M1")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--baseline", "M0", "--baseline-crc32", CRC_M0,
         "--run", f"M0={base}", "--run", f"M1={variant}"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "baseline: `M0`" in proc.stdout          # baseline указан явно
    assert "| id_error |" in proc.stdout            # матрица построена
    assert "BETTER" in proc.stdout or "SAME" in proc.stdout


def test_metric_direction_and_tolerance(tmp_path: Path) -> None:
    runs = {
        "M0": metrics_of(tmp_path, make_log(tmp_path, "M0", id_err=100, iq_err=100)),
        "M1": metrics_of(tmp_path, make_log(tmp_path, "M1", id_err=50, iq_err=100)),   # лучше/так же
        "M2": metrics_of(tmp_path, make_log(tmp_path, "M2", id_err=200, iq_err=100)),  # хуже
    }
    report = build_report(runs, "M0", DEFAULT_TOL, CRC_M0)
    assert report["matrix"]["id_error"]["M1"] == "BETTER"
    assert report["matrix"]["id_error"]["M2"] == "WORSE"
    assert report["matrix"]["iq_error"]["M1"] == "SAME"


def test_no_aggregate_rating(tmp_path: Path) -> None:
    runs = {
        "M0": metrics_of(tmp_path, make_log(tmp_path, "M0")),
        "M1": metrics_of(tmp_path, make_log(tmp_path, "M1", id_err=5)),
    }
    report = build_report(runs, "M0", DEFAULT_TOL, CRC_M0)
    flat = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in ('"overall"', '"aggregate"', '"score"', '"rating"', '"total_score"'):
        assert forbidden not in flat, f"найден агрегированный рейтинг: {forbidden}"
    assert report["verdict_absent_by_design"] is True
    assert "BASELINE" not in report["matrix"].get("id_error", {}).get("M1", "")


def test_idc_used_only_for_repeatability(tmp_path: Path) -> None:
    parsed = metrics_of(tmp_path, make_log(tmp_path, "M0", adc_spread=7))
    names = set(parsed["metrics"])
    assert "idc_repeatability" in names
    assert not [n for n in names if "true" in n or "physical" in n or "real" in n]
    assert parsed["metrics"]["idc_repeatability"]["value"] >= 0
    assert "repeatability" in parsed["metrics"]["idc_repeatability"]["note"]


def test_zsv_alert_when_dq_improves_but_zsv_grows(tmp_path: Path) -> None:
    runs = {
        "M0": metrics_of(tmp_path, make_log(tmp_path, "M0", id_err=100, ires=1)),
        "M1": metrics_of(tmp_path, make_log(tmp_path, "M1", id_err=50, ires=9)),
    }
    report = build_report(runs, "M0", DEFAULT_TOL, CRC_M0)
    assert report["matrix"]["zsv_ires"]["M1"] == "WORSE"
    assert report["matrix"]["id_error"]["M1"] == "BETTER"
    alert = report["zsv_alerts"]["M1"]
    assert alert["zsv_increased"] is True and alert["idq_improved"] is True
    assert "zero-sequence" in alert["note"]


def test_protection_and_integrity_rules(tmp_path: Path) -> None:
    runs = {
        "M0": metrics_of(tmp_path, make_log(tmp_path, "M0", fault=0, brk=0, trunc=0)),
        "M1": metrics_of(tmp_path, make_log(tmp_path, "M1", fault=0, brk=0, trunc=0)),
        "M2": metrics_of(tmp_path, make_log(tmp_path, "M2", fault=1, brk=1, trunc=3)),
    }
    report = build_report(runs, "M0", DEFAULT_TOL, CRC_M0)
    assert report["matrix"]["break_events"]["M1"] == "SAME"
    assert report["matrix"]["break_events"]["M2"] == "WORSE"
    assert report["matrix"]["uart_trunc"]["M2"] == "WORSE"
    assert report["matrix"]["protection_fault_rows"]["M2"] == "WORSE"


def test_metrics_without_independent_source_are_na(tmp_path: Path) -> None:
    runs = {
        "M0": metrics_of(tmp_path, make_log(tmp_path, "M0")),
        "M1": metrics_of(tmp_path, make_log(tmp_path, "M1")),
    }
    report = build_report(runs, "M0", DEFAULT_TOL, CRC_M0)
    assert report["matrix"]["ires_vs_iz"]["M1"] == "N/A"
    assert report["matrix"]["kcl_residual"]["M1"] == "N/A"
    assert "N/A" in report["metrics"]["M1"]["kcl_residual"]["note"]


def test_iz_and_ref_change_verdict(tmp_path: Path) -> None:
    base = make_log(tmp_path, "M0", ires=10)
    variant = make_log(tmp_path, "M1", ires=4)
    iz = tmp_path / "iz_M1.csv"
    iz.write_text("\n".join(f"{1000 + i * 100},4" for i in range(20)) + "\n", encoding="utf-8")
    ph = tmp_path / "phases_M1.csv"
    ph.write_text("\n".join(f"{i},1.0,2.0,-3.0" for i in range(20)) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--baseline", "M0", "--baseline-crc32", CRC_M0,
         "--run", f"M0={base}", "--run", f"M1={variant}", "--iz", f"M1={iz}", "--ref", f"M1={ph}"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "| ires_vs_iz |" in proc.stdout
    assert "kcl_residual" in proc.stdout


def test_markdown_has_no_total_row(tmp_path: Path) -> None:
    base = make_log(tmp_path, "M0")
    variant = make_log(tmp_path, "M1")
    md = tmp_path / "out.md"
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--baseline", "M0", "--baseline-crc32", CRC_M0,
         "--run", f"M0={base}", "--run", f"M1={variant}", "--markdown", str(md)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = md.read_text(encoding="utf-8").lower()
    assert "итог" not in text and "total" not in text and "score" not in text
    assert "агрегированного рейтинга нет" in text


def test_missing_baseline_run_is_rejected(tmp_path: Path) -> None:
    variant = make_log(tmp_path, "M1")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--baseline", "M0", "--run", f"M1={variant}"],
        capture_output=True, text=True)
    assert proc.returncode == 1
    assert "baseline" in proc.stderr
