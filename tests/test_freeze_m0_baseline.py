"""Тесты заморозки M0 baseline (TZ-02, шаг c): M1 не проектируется до freeze.

Покрывают: полную цепочку (raw → manifest → gate → comparator → audit → freeze),
негативы по каждому пункту F1..F8, проставление baseline_frozen в манифест,
и гейт G2b в preflight для не-baseline варианта.
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
sys.path.insert(0, str(ROOT / "tests"))
from freeze_m0_baseline import SCHEMA_VERSION  # noqa: E402
from preflight_m0_burst import run_gates  # noqa: E402
from test_preflight_m0_burst import LIVE, PREFLIGHT_LOG, build_bundle, status_of  # noqa: E402

TOOL = ROOT / "tools" / "freeze_m0_baseline.py"
CRC_M0 = "0x95425CEB"


def burst_rows(run_id: str, *, fault: int = 0, trunc: int = 0, n: int = 10,
               broken_t: bool = False) -> str:
    lines = []
    for i in range(n):
        t = 1000 + i * 100
        if broken_t and i == n - 1:
            t = 500
        lines.append(f"@FOC:t={t}:run_id={run_id}:map_id=M0-rebased:map_crc32={CRC_M0}:"
                     f"I1=2039:I2=2068:Ires=2041:Id=10:Iq=2:Id_ref=10:Iq_ref=2:VBUS=201:STATE=0:"
                     f"SPD=0:TH=0:sector={i % 6}:window=0:CCR1=500:CCR2=500:CCR3=500:"
                     f"ADC_STATUS=0:FAULT={fault}:FAULT_R={18 if fault else 0}:FAIL=0:RUN=0:"
                     f"em_stop1=1:em_stop2=1")
    lines.append(f"@SYS:CLK=170000000:PSC=16:TCLK=10000000:uart_drp=0:uart_trunc={trunc}")
    return "\n".join(lines)


def prepare(tmp_path: Path, *, log_extra: str | None = None, burst_text: str | None = None,
            gate_verdict: str | None = "PASS", mutation=None, with_comparator: bool = True):
    bundle, manifest = build_bundle(tmp_path, manifest_mut=mutation)
    log = bundle / "logs" / "M0-R1.log"
    text = log.read_text(encoding="utf-8") + "\n@RUN:ID=M0-R1\n" + (burst_text or burst_rows("M0-R1"))
    if log_extra:
        text += "\n" + log_extra
    log.write_text(text + "\n", encoding="utf-8")

    gate = bundle / "gate.json"
    if gate_verdict is not None:
        gate.write_text(json.dumps({"verdict": gate_verdict, "gates": []}, ensure_ascii=False),
                        encoding="utf-8")
    comparator = None
    if with_comparator:
        comparator = bundle / "compare.json"
        comparator.write_text(json.dumps({
            "baseline": "M0",
            "metrics": {"M0": {"map_identity": {"value": {"map_id": ["M0-rebased"],
                                                          "map_crc32": [CRC_M0]}}}},
        }, ensure_ascii=False), encoding="utf-8")

    mp = bundle / "session_manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle, manifest, mp, gate, comparator


def run_freeze(bundle: Path, mp: Path, **extra) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(TOOL), "--manifest", str(mp), "--bundle", str(bundle)]
    for key, val in extra.items():
        if val is None:
            continue
        flag = "--" + key.replace("_", "-")
        cmd += [flag, str(val)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_full_chain_freezes_baseline(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path)
    proc = run_freeze(bundle, mp, gate=gate, comparator=comparator, audit_by="reviewer-2",
                      return_manifest=bundle / "RETURN_SHA256.txt")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FREEZE OK" in proc.stdout
    assert "ОГРАНИЧЕНИЕ" in proc.stdout
    record = json.loads((bundle / "M0_BASELINE_FROZEN.json").read_text(encoding="utf-8"))
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["caveat"]
    assert record["artifact"]["map_crc32"] == CRC_M0
    assert record["checks"]["F3"] == "PASS" and record["checks"]["F8"] == "PASS"
    assert record["audit"]["by"] == "reviewer-2"


def test_truncated_foc_rows_do_not_break_freeze(tmp_path: Path) -> None:
    """Усечённые по RX-окну строки @FOC (без поля FAULT) не должны ронять заморозку."""
    text = (burst_rows("M0-R1") + "\n"
            "@FOC:t=5000:run_id=M0-R1:map_id=M0-rebased:map_crc32=0x95425CEB:I1=2039:I2=2068:"
            "Ires=2041:Id=10")   # строка обрезана логгером: поля FAULT нет
    bundle, _, mp, gate, comparator = prepare(tmp_path, burst_text=text)
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 0, proc.stdout
    record = json.loads((bundle / "M0_BASELINE_FROZEN.json").read_text(encoding="utf-8"))
    assert record["raw_log"]["safety_coverage"] < 1.0     # покрытие посчитано честно
    assert record["checks"]["F3"] == "PASS"


def test_return_manifest_is_written_last_and_verifies(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path)
    rm = bundle / "RETURN_SHA256.txt"
    proc = run_freeze(bundle, mp, gate=gate, comparator=comparator, audit_by="r2",
                      return_manifest=rm)
    assert proc.returncode == 0, proc.stdout
    lines = [ln for ln in rm.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines[-1].endswith("M0_BASELINE_FROZEN.json"), "запись о заморозке обязана быть последней"
    for line in lines:
        digest, _, rel = line.partition("  ")
        actual = hashlib.sha256((bundle / rel).read_bytes()).hexdigest()
        assert actual == digest, rel


def test_missing_gate_blocks_freeze(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path, gate_verdict=None)
    proc = run_freeze(bundle, mp, audit_by="r2")
    assert proc.returncode == 1
    assert "F2" in proc.stdout and "BLOCKED" in proc.stdout


def test_gate_not_pass_blocks_freeze(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path, gate_verdict="BLOCKED")
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 1
    assert "F2" in proc.stdout


@pytest.mark.parametrize("kwargs,item", [
    ({"burst_text": burst_rows("M0-R1", fault=1)}, "FAULT"),
    ({"log_extra": "@BRK:valid=1:seq=1:src=TIM8"}, "@BRK"),
    ({"burst_text": burst_rows("M0-R1", trunc=3)}, "uart_drp"),
    ({"burst_text": burst_rows("M0-R1", broken_t=True)}, "монотонен"),
])
def test_integrity_defects_block_freeze(tmp_path: Path, kwargs, item: str) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path, **kwargs)
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 1, item
    assert "F3" in proc.stdout
    assert item in proc.stdout


def test_artifact_sha_mismatch_blocks(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(
        tmp_path, mutation=lambda m: m["bursts"][0].update({"artifact_sha256": "b" * 64}))
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 1
    assert "F4" in proc.stdout


def test_identity_mismatch_blocks(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(
        tmp_path, mutation=lambda m: m["session"]["live_identity"]["values"].update(
            {"pwm_frequency_hz": 294}))
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 1
    assert "F5" in proc.stdout


def test_audit_required(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(tmp_path)
    proc = run_freeze(bundle, mp, gate=gate)
    assert proc.returncode == 1
    assert "F7" in proc.stdout


def test_stop_gate_blocks(tmp_path: Path) -> None:
    bundle, _, mp, gate, comparator = prepare(
        tmp_path, mutation=lambda m: m["bursts"][0].update({"stop_gate": {"triggered": True}}))
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 1
    assert "F1" in proc.stdout or "F6" in proc.stdout


def test_patch_manifest_enables_m1_and_gate_g2b(tmp_path: Path) -> None:
    """Freeze + --patch-manifest → M1 burst получает baseline_frozen, гейт G2b проходит."""
    bundle, manifest, mp, gate, comparator = prepare(tmp_path)
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 0, proc.stdout

    # M1 burst, добавленный сессией (пока без baseline_frozen)
    m1 = json.loads(json.dumps(manifest["bursts"][0]))
    m1.update({"run_id": "M1-R1", "variant_id": "M1", "variant_map_id": "M1",
               "variant_map_crc32": "0x11223344"})
    m1["previous_variant_comparison"] = {"variant_id": "M0-rebased", "analyzed": True}
    m1.pop("baseline_frozen", None)
    manifest["bursts"].append(m1)
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # без freeze-записи гейт G2b падает
    data = json.loads(mp.read_text(encoding="utf-8"))
    g = run_gates(data, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None,
                  variant_id="M1")
    assert status_of(g, "G2b") == "FAIL"

    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2", patch_manifest=mp)
    assert proc.returncode == 0, proc.stdout
    assert "baseline_frozen проставлен" in proc.stdout
    data = json.loads(mp.read_text(encoding="utf-8"))
    m1 = next(b for b in data["bursts"] if b["variant_id"] == "M1")
    freeze_sha = hashlib.sha256((bundle / "M0_BASELINE_FROZEN.json").read_bytes()).hexdigest()
    assert m1["baseline_frozen"]["sha256"] == freeze_sha
    assert m1["baseline_frozen"]["baseline_crc32"] == CRC_M0

    g = run_gates(data, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None,
                  variant_id="M1")
    assert status_of(g, "G2b") == "PASS", g.render()


def test_freeze_record_crc_mismatch_fails_g2b(tmp_path: Path) -> None:
    bundle, manifest, mp, gate, comparator = prepare(tmp_path)
    proc = run_freeze(bundle, mp, gate=gate, audit_by="r2")
    assert proc.returncode == 0, proc.stdout
    frozen = bundle / "M0_BASELINE_FROZEN.json"
    rec = json.loads(frozen.read_text(encoding="utf-8"))
    rec["artifact"]["map_crc32"] = "0xDEADBEEF"
    frozen.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    freeze_sha = hashlib.sha256(frozen.read_bytes()).hexdigest()

    m1 = json.loads(json.dumps(manifest["bursts"][0]))
    m1.update({"run_id": "M1-R1", "variant_id": "M1", "variant_map_crc32": "0x11223344",
               "baseline_frozen": {"file": "M0_BASELINE_FROZEN.json", "sha256": freeze_sha,
                                   "baseline_crc32": CRC_M0},
               "previous_variant_comparison": {"variant_id": "M0-rebased", "analyzed": True}})
    manifest["bursts"].append(m1)
    g = run_gates(manifest, bundle, firmware=None, rebase_manifest=None, dump_cmd=None, run_id=None,
                  variant_id="M1")
    assert status_of(g, "G2b") == "FAIL"
