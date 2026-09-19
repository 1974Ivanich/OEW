"""Тесты сведения повторяемости M0 (R1…R5 → уровень baseline).

Проверяют: один прогон НЕ достигает baseline; пять одинаковых — достигают; каждое нарушение
инварианта (firmware/artifact/identity/envelope/чистота прогона/уникальность run_id) ловится
отдельным правилом; аномалия требует явного принятия оператором.
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
from m0_repeatability import (aggregate, apply_audits, parse_run, render,  # noqa: E402
                              spread)

TOOL = ROOT / "tools" / "m0_repeatability.py"
FW = "6d3ba90235e7b81681f88ea305957dd7ba5b2a69f810f2d73dfe088cfe3e0201"
CRC = "0x00E666F3"
ART = "c" * 64
IDENT = ("@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:"
         "adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0x13552B12")


def make_run(root: Path, run_id: str, *, records: int = 8, fw: str = FW, crc: str = CRC,
             art: str = ART, ccs: str = "0x13552B12", vbus_target: int = 30000,
             drain: int | None = None, fault: int = 0, brk: bool = False, trunc: int = 0,
             vbus_max: int = 305) -> Path:
    folder = root / run_id
    (folder / "logs").mkdir(parents=True, exist_ok=True)
    lines = ["[utc] META", "[utc] BOOT", ">>> p?", "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0",
             "@PWM:FULL:SYS=170000000:CFGR=0x0F:T1:PSC=16:ARR=999:CCR=0,0,0:BDTR=0x1CC0:"
             "CCER=0x0:CR1=0xE0:CNT=0",
             ">>> c", "@ADC:CAL:offset_i1=2038:offset_i2=2068:offset_ires=0",
             ">>> mapcap identity", IDENT.replace("0x13552B12", ccs),
             "@MAP:LOAD:OK:crc=" + crc + ":cid=0x424F4152", f"@RUN:ID={run_id}"]
    for i in range(records):
        lines.append(f"@MC:REC:cap=1:seq={i + 1}:raw_i1={2040 + i % 3}:raw_i2={2068 + i % 2}:"
                     f"raw_ct=0:raw_vbus={vbus_max - i}:i1=300:i2=200:vbus=3")
    lines.append(f"@MC:DRAIN:records={records if drain is None else drain}")
    lines.append(f"@MC:STATUS:state=3:term=0:frames={records}:dropped=0")
    lines.append(f"@SYS:CLK=170000000:PSC=16:TCLK=10000000:uart_drp=0:uart_trunc={trunc}")
    if brk:
        lines.append("@BRK:valid=1:seq=1:src=TIM8")
    else:
        lines.append("@BRK:valid=0:seq=1")
    for i in range(3):
        lines.append(f"@FOC:t={1000 + i * 100}:run_id={run_id}:map_id=M0-rebased:map_crc32={crc}:"
                     f"I1=0:I2=0:Ires=0:Id=0:Iq=0:Id_ref=0:Iq_ref=0:VBUS={vbus_target}:STATE=0:"
                     f"SPD=0:TH=0:sector=0:window=0:CCR1=0:CCR2=0:CCR3=0:FAULT={fault}:FAULT_R=0:"
                     f"RUN=0:em_stop1=1:em_stop2=1")
    (folder / "logs" / f"{run_id}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "tz2-session-manifest-1",
        "session": {"session_id": f"s-{run_id}", "date_utc": "2026-09-19T06:00:00Z",
                    "operator1": "A", "operator2": "B", "firmware_sha256": fw,
                    "envelope": {"vbus_min_mv": 24000, "vbus_max_mv": 36000, "current_limit_ma": 1000,
                                 "burst_max_ms": 1000, "pause_min_ms": 5000,
                                 "safety_owner_approval": "ID-111"},
                    "live_identity": {"source": "@MAP:IDENTITY", "captured_utc": "2026-09-19T06:00:00Z",
                                      "file": "identity.txt", "values": {"timer_arr": 999}}},
        "bursts": [{"run_id": run_id, "variant_id": "M0-rebased", "base_map_id": "M0-rebased",
                    "base_map_crc32": crc, "variant_map_id": "M0-rebased", "variant_map_crc32": crc,
                    "artifact_sha256": art, "firmware_sha256": fw,
                    "timestamp_start": "2026-09-19T06:01:00Z", "timestamp_end": "2026-09-19T06:01:01Z",
                    "vbus_target_mv": vbus_target, "current_limit_ma": 1000, "burst_duration_ms": 800,
                    "pause_before_ms": 6000, "operator1": "A", "operator2": "B",
                    "telemetry": {"raw_log": f"logs/{run_id}.log", "identity_file": "identity.txt",
                                  "artifact_file": "M0_rebased.bin", "preflight": [], "postflight": []},
                    "map_load_ok": True, "run_id_ack": True,
                    "stop_gate": {"triggered": False}, "break_snapshot_archived": None}],
    }
    (folder / "session_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                                  encoding="utf-8")
    return folder


def run_cli(runs: list[Path], *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(TOOL)]
    for p in runs:
        cmd += ["--run", f"{p.name}={p}"]
    return subprocess.run(cmd + list(extra), capture_output=True, text=True)


def parsed(runs: list[Path]) -> list[dict]:
    return [parse_run(p) for p in runs]


def test_single_run_does_not_reach_baseline(tmp_path: Path) -> None:
    folder = make_run(tmp_path, "M0-R1")
    proc = run_cli([folder], "--min-runs", "5")
    assert proc.returncode == 1
    assert "[R6]" in proc.stdout and "прогонов всего 1" in proc.stdout
    assert "не выпускается" in proc.stdout
    # без --allow-provisional запись не выпускается даже при min-runs=1
    proc1 = run_cli([folder], "--min-runs", "1")
    assert proc1.returncode == 1
    assert "[R6b]" in proc1.stdout and "полный baseline недостижим" in proc1.stdout
    # с --allow-provisional выпускается ПРЕДВАРИТЕЛЬНАЯ запись, а не полный baseline
    out = tmp_path / "prov"
    proc2 = run_cli([folder], "--min-runs", "1", "--allow-provisional", "--out-dir", str(out))
    assert proc2.returncode == 0, proc2.stdout
    assert "M0_BASELINE_PROVISIONAL" in proc2.stdout
    record = json.loads((out / "M0_BASELINE_PROVISIONAL.json").read_text(encoding="utf-8"))
    assert record["level"] == "M0_BASELINE_PROVISIONAL" and record["provisional"] is True
    assert record["runs_count"] == 1 and record["min_runs_required"] == 5
    assert not (out / "M0_BASELINE_FROZEN.json").exists()


def test_five_identical_runs_reach_baseline_and_emit_record(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    out = tmp_path / "out"
    proc = run_cli(runs, "--out-dir", str(out), "--json", str(tmp_path / "rep.json"))
    assert proc.returncode == 0, proc.stdout
    assert "уровень baseline достигнут" in proc.stdout
    record = json.loads((out / "M0_BASELINE_FROZEN.json").read_text(encoding="utf-8"))
    assert record["level"] == "M0_BASELINE_FROZEN"
    assert record["runs"] == ["M0-R1", "M0-R2", "M0-R3", "M0-R4", "M0-R5"]
    assert record["aggregate"]["verdict"] == "PASS"
    assert "НЕ физическая квалификация" in record["caveat"]


def test_firmware_mismatch_fails_r1(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", fw="f" * 64))
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert agg["verdict"] == "FAIL"
    assert next(c for c in agg["checks"] if c["id"] == "R1")["status"] == "FAIL"


def test_artifact_and_crc_mismatch_fail_r2(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", crc="0xDEADBEEF"))
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R2b")["status"] == "FAIL"

    runs = [make_run(tmp_path / "b", f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path / "b", "M0-R5", art="d" * 64))
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R2")["status"] == "FAIL"


def test_identity_mismatch_fails_r3(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", ccs="0xABE94C77"))   # состояние до калибровки
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R3")["status"] == "FAIL"


def test_envelope_mismatch_fails_r4(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", vbus_target=36000))
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R4")["status"] == "FAIL"


@pytest.mark.parametrize("kwargs,needle", [
    ({"fault": 1}, "FAULT!=0"),
    ({"brk": True}, "@BRK:valid=1"),
    ({"trunc": 4}, "потери UART"),
    ({"drain": 7}, "drain=7"),
])
def test_dirty_run_fails_r5(tmp_path: Path, kwargs, needle: str) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", **kwargs))
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    r5 = next(c for c in agg["checks"] if c["id"] == "R5")
    assert r5["status"] == "FAIL" and needle in r5["detail"]


def test_duplicate_run_id_fails_r6(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    dup = make_run(tmp_path / "dup", "M0-R1")
    agg = aggregate(parsed(runs + [dup]), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R6")["status"] == "FAIL"


def test_anomaly_requires_explicit_acknowledgement(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 5)]
    runs.append(make_run(tmp_path, "M0-R5", records=40))     # выброс по числу записей
    agg = aggregate(parsed(runs), FW, CRC, 5, 0.5, {})
    assert next(c for c in agg["checks"] if c["id"] == "R7")["status"] == "FAIL"
    assert any(a["run_id"] == "M0-R5" and a["metric"] == "records" for a in agg["anomalies"])

    proc = run_cli(runs, "--acknowledge-anomaly", "M0-R5=оператор подтвердил длительность")
    assert proc.returncode == 0, proc.stdout
    assert "все приняты приёмкой" in proc.stdout


def test_spread_and_parse_on_missing_files(tmp_path: Path) -> None:
    assert spread([]) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    parsed_run = parse_run(empty)
    assert parsed_run["problems"] == ["нет session_manifest.json"]
    assert aggregate([parsed_run], FW, CRC, 1, 0.5, {})["verdict"] == "FAIL"


def test_missing_sys_lines_is_a_defect(tmp_path: Path) -> None:
    """Отсутствие @SYS ≠ «потерь нет»: целостность UART не подтверждена."""
    folder = make_run(tmp_path, "M0-R1")
    log = folder / "logs" / "M0-R1.log"
    log.write_text("\n".join(ln for ln in log.read_text(encoding="utf-8").splitlines()
                             if not ln.startswith("@SYS:")) + "\n", encoding="utf-8")
    parsed = parse_run(folder)
    assert any("@SYS" in p for p in parsed["problems"]), parsed["problems"]


def test_log_is_taken_from_manifest_not_first_glob(tmp_path: Path) -> None:
    """Лог берётся строго из telemetry.raw_log; при двух семьях логов это принципиально."""
    folder = make_run(tmp_path, "M0-R2")
    logs = folder / "logs"
    # «грязная» семья с алфавитно первым именем — не должна использоваться
    (logs / "AAA.session.log").write_text("@BRK:valid=1\n@FOC:t=1:FAULT=1\n", encoding="utf-8")
    parsed = parse_run(folder)
    assert parsed["log_source"] == "manifest:telemetry.raw_log"
    assert parsed["log"] == "M0-R2.log"
    assert parsed["fault_rows"] == 0 and parsed["brk_valid1"] == 0
    assert not parsed["problems"], parsed["problems"]


def test_missing_manifest_log_is_a_problem(tmp_path: Path) -> None:
    folder = make_run(tmp_path, "M0-R2")
    (folder / "logs" / "M0-R2.log").unlink()
    parsed = parse_run(folder)
    assert any("telemetry.raw_log" in p for p in parsed["problems"]), parsed["problems"]


def test_break_archive_recorded_separately(tmp_path: Path) -> None:
    """BREAK-архив фиксируется отдельным каналом и не влияет на чистоту прогонов."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    archive = tmp_path / "brk"
    archive.mkdir()
    (archive / "M0-R2.log").write_text("@BRK:valid=1:seq=1:src=TIM8\n", encoding="utf-8")
    (archive / "OBSERVED.md").write_text("# OBSERVED\n", encoding="utf-8")
    out = tmp_path / "out"
    proc = run_cli(runs, "--out-dir", str(out), "--breakdiag-archive", str(archive))
    assert proc.returncode == 0, proc.stdout
    assert "отдельным каналом" in proc.stdout
    record = json.loads((out / "M0_BASELINE_FROZEN.json").read_text(encoding="utf-8"))
    assert record["break_diagnostic"]["affects_runs"] is False
    assert len(record["break_diagnostic"]["files"]) == 2
    assert record["aggregate"]["checks"] and all(
        c["status"] == "PASS" for c in record["aggregate"]["checks"])
def test_invalid_run_excluded_from_statistics_not_zeroed(tmp_path: Path) -> None:
    """INVALID-прогон исключается из статистики целиком — не «чистится» и не превращается в ноль."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    log = runs[1] / "logs" / "M0-R2.log"       # портим R2: нет @SYS → целостность UART не доказана
    log.write_text("\n".join(ln for ln in log.read_text(encoding="utf-8").splitlines()
                             if not ln.startswith("@SYS:")) + "\n", encoding="utf-8")
    parsed = [parse_run(p) for p in runs]
    agg = aggregate(parsed, FW, CRC, 5, 0.5, {}, allow_provisional=False)

    assert agg["verdict"] == "FAIL" and agg["level"] == "NONE"
    assert agg["runs_count"] == 5 and agg["runs_valid_count"] == 4
    assert [i["run_id"] for i in agg["runs_invalid"]] == ["M0-R2"]
    assert "нет строк @SYS" in agg["runs_invalid"][0]["reasons"][0]
    assert "M0-R2" not in agg["runs_valid"]
    assert "INVALID исключены" in agg["statistics_basis"]

    # метрики построены на четырёх валидных прогонах; значения испорченного в выборку не попали
    for key, m in agg["metrics"].items():
        assert m["runs"] == agg["runs_valid"], key
        assert len(m["values"]) == 4, key
    assert not any(a["run_id"] == "M0-R2" for a in agg["anomalies"])
    r5 = next(c for c in agg["checks"] if c["id"] == "R5")
    assert r5["status"] == "FAIL" and "не ноль" in r5["detail"]


def test_invalid_run_does_not_satisfy_run_count(tmp_path: Path) -> None:
    """Уровень записи решает число ВАЛИДНЫХ прогонов, а не поданных."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    log = runs[1] / "logs" / "M0-R2.log"
    log.write_text("\n".join(ln for ln in log.read_text(encoding="utf-8").splitlines()
                             if not ln.startswith("@SYS:")) + "\n", encoding="utf-8")
    parsed = [parse_run(p) for p in runs]
    # даже при --min-runs 4 четыре валидных прогона не дают полного baseline
    agg = aggregate(parsed, FW, CRC, 4, 0.5, {}, allow_provisional=False)
    r6b = next(c for c in agg["checks"] if c["id"] == "R6b")
    assert r6b["status"] == "FAIL" and "валидных 4" in r6b["detail"]

    out = tmp_path / "out"
    proc = run_cli(runs, "--min-runs", "4", "--out-dir", str(out))
    assert proc.returncode == 1
    assert not (out / "M0_BASELINE_FROZEN.json").exists()
    assert not (out / "M0_BASELINE_PROVISIONAL.json").exists()


def test_all_valid_runs_basis_is_all_runs(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    agg = aggregate([parse_run(p) for p in runs], FW, CRC, 5, 0.5, {}, allow_provisional=False)
    assert agg["runs_valid_count"] == 5 and agg["runs_invalid"] == []
    assert agg["statistics_basis"] == "5 процедурно валидных прогонов"
    assert agg["verdict"] == "PASS" and agg["level"] == "M0_BASELINE_FROZEN"


def test_invalid_run_reported_in_record_and_cli(tmp_path: Path) -> None:
    """INVALID виден и в stdout, и в записи (числом и по именам, с причинами)."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    bad = runs[0] / "logs" / "M0-R1.log"
    bad.write_text(bad.read_text(encoding="utf-8").replace("dropped=0", "dropped=2"),
                   encoding="utf-8")
    for i in range(1, 6):
        pass
    parsed = [parse_run(p) for p in runs]
    agg = aggregate(parsed, FW, CRC, 5, 0.5, {}, allow_provisional=True)
    assert agg["runs_valid_count"] == 4
    assert agg["runs_invalid"][0]["run_id"] == "M0-R1"
    assert "dropped=2" in agg["runs_invalid"][0]["reasons"][0]
    assert agg["provisional"] is False and agg["verdict"] == "FAIL"
    assert "INVALID" in render(agg)
def audit_json(path: Path, run_id: str, verdict: str, failed: list[str]) -> Path:
    path.write_text(json.dumps({
        "mode": "repeat", "log": f"logs/{run_id}.log", "verdict": verdict,
        "checks": [{"id": cid, "status": "FAIL" if cid in failed else "PASS", "detail": ""}
                   for cid in ("B1", "B2", "B5", "B9")]}, ensure_ascii=False),
        encoding="utf-8")
    return path


def test_audit_fail_makes_run_invalid(tmp_path: Path) -> None:
    """Порядок приёмки A…F: прогон, не прошедший процедурный аудит, в статистику не входит."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    a = audit_json(tmp_path / "audit_M0-R2.json", "M0-R2", "FAIL", ["B9"])
    parsed = apply_audits([parse_run(p) for p in runs], {"M0-R2": a})
    agg = aggregate(parsed, FW, CRC, 5, 0.5, {})

    assert agg["runs_valid_count"] == 4 and agg["runs_count"] == 5
    assert [i["run_id"] for i in agg["runs_invalid"]] == ["M0-R2"]
    assert "процедурный аудит: FAIL (B9)" in agg["runs_invalid"][0]["reasons"][0]
    assert parsed[1]["audit"]["verdict"] == "FAIL" and parsed[1]["audit"]["failed"] == ["B9"]
    assert parsed[1]["audit"]["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert len(agg["metrics"]["raw_i1_spread"]["values"]) == 4
    assert agg["verdict"] == "FAIL" and agg["level"] == "NONE"


def test_audit_pass_keeps_run_valid(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    a = audit_json(tmp_path / "audit_M0-R1.json", "M0-R1", "PASS", [])
    parsed = apply_audits([parse_run(p) for p in runs], {"M0-R1": a})
    assert parsed[0]["validity"] == "valid" and parsed[0]["audit"]["failed"] == []
    agg = aggregate(parsed, FW, CRC, 5, 0.5, {})
    assert agg["verdict"] == "PASS" and agg["level"] == "M0_BASELINE_FROZEN"


def test_audit_missing_run_is_not_silently_audited(tmp_path: Path) -> None:
    """Прогон без поданного аудита получает пометку отсутствия вердикта, а не «PASS»."""
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    a = audit_json(tmp_path / "audit_M0-R3.json", "M0-R3", "PASS", [])
    parsed = apply_audits([parse_run(p) for p in runs], {"M0-R3": a})
    assert "audit" in parsed[2] and "audit" not in parsed[0]


def test_cli_audit_blocks_baseline(tmp_path: Path) -> None:
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    a = audit_json(tmp_path / "a.json", "M0-R3", "FAIL", ["B9"])
    out = tmp_path / "out"
    proc = run_cli(runs, "--out-dir", str(out), "--audit", f"M0-R3={a}")
    assert proc.returncode == 1
    assert "процедурный аудит: FAIL (B9)" in proc.stdout
    assert not (out / "M0_BASELINE_FROZEN.json").exists()
    assert not (out / "M0_BASELINE_PROVISIONAL.json").exists()

    # отсутствующий файл аудита — блокировка запуска, а не «прогон без аудита»
    proc2 = run_cli(runs, "--out-dir", str(out), "--audit", f"M0-R3={tmp_path / 'нет.json'}")
    assert proc2.returncode == 1 and "BLOCKED" in proc2.stdout


def test_artifacts_written_when_console_is_cp1251(tmp_path: Path) -> None:
    """Печать в cp1251-консоль не должна лишать приёмку артефактов (порядок: файлы, затем вывод)."""
    import os
    runs = [make_run(tmp_path, f"M0-R{i}") for i in range(1, 6)]
    js = tmp_path / "out.json"
    env = dict(os.environ, PYTHONIOENCODING="cp1251")
    cmd = [sys.executable, str(TOOL)]
    for p in runs:
        cmd += ["--run", f"{p.name}={p}"]
    proc = subprocess.run(cmd + ["--json", str(js)], capture_output=True, env=env, text=False)
    assert proc.returncode in (0, 1), proc.stderr.decode("utf-8", "replace")[-400:]
    assert js.is_file(), proc.stderr.decode("utf-8", "replace")[-400:]
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS" and data["runs_valid_count"] == 5
    assert dict(agg_block := data) is not None and len(agg_block["checks"]) >= 8
