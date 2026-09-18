"""Тесты intake-гейта входов со стенда (identity + raw uart.log).

Каждое правило I1..I11 имеет негативный тест; happy-path проверяет, что собранный
live.txt пригоден для M0-rebased.
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
from identity_intake_check import IDENT_KEYS, parse_identity_line, parse_live_txt  # noqa: E402

TOOL = ROOT / "tools" / "identity_intake_check.py"
FW = "6d3ba90235e7b81681f88ea305957dd7ba5b2a69f810f2d73dfe088cfe3e0201"
COMMIT = "38b66a5a1751354a52b2d1c70eb673b5ed49e76f"
IDENTITY_LINE = ("@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:"
                 "adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0xE98FCB2C")

SESSION_LOG = "\n".join([
    "[2026-09-19T06:00:00Z] META|mode=IDENTITY_NO_HV",
    "[2026-09-19T06:00:01Z] BOOT",
    ">>> sysinfo",
    "@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x4150:OVR=0:JEOS=0:TO=0:JQOVF=0:"
    "uart_drp=0:uart_trunc=0",
    ">>> p?",
    "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0",
    ">>> pdump",
    "@PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:CR2=0x00000000:CCER=0x00000000",
    ">>> enc",
    "@ENC:angle=0:speed=0:period_us=0:pulse_us=0:err=0",
    ">>> mapcap identity",
    IDENTITY_LINE,
])

IDENTITY_VALUES = {"board_revision": 7, "pwm_frequency_hz": 5000, "timer_arr": 999,
                   "adc_trigger_id": 1329944369, "trigger_offset_ticks": 0, "deadtime_ticks": 192,
                   "adc_clock_hz": 42500000, "adc_sample_cycles_x2": 1281, "adc_resolution": 0,
                   "adc_config_signature": 649705851, "current_calibration_signature": 3918514988}


def write_manifest(folder: Path) -> None:
    entries = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS.txt", "RETURN_SHA256.txt"):
            entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
                           f"{path.relative_to(folder).as_posix()}")
    (folder / "SHA256SUMS.txt").write_text("\n".join(entries) + "\n", encoding="utf-8",
                                           newline="\n")


def build(tmp: Path, *, log_text: str | None = None, meta_mut=None, with_identity_txt: bool = False,
          identity_txt_text: str | None = None, skip_meta: bool = False,
          extra_file: bool = False, refresh_manifest: bool = True) -> Path:
    folder = tmp / "pc3_return"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "uart_identity.log").write_text((log_text or SESSION_LOG) + "\n", encoding="utf-8")
    if not skip_meta:
        meta = {"firmware_sha256": FW, "source_commit": COMMIT, "board_revision": 7,
                "operator": "ПК-3 оператор", "date_utc": "2026-09-19T06:00:00Z",
                "firmware_image": "firmware_tz2_map_capable_6d3ba902.bin",
                "tools": {"identity_intake_check": "1.0"}}
        if meta_mut:
            meta_mut(meta)
        (folder / "SESSION_META.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    if with_identity_txt:
        text = identity_txt_text or "\n".join(f"{k}={v}" for k, v in IDENTITY_VALUES.items()) + "\n"
        (folder / "identity.txt").write_text(text, encoding="utf-8")
    if extra_file:
        (folder / "notes.txt").write_text("не внесено в манифест\n", encoding="utf-8")
    if refresh_manifest:
        write_manifest(folder)
    return folder


def run(folder: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), str(folder), *extra],
                          capture_output=True, text=True)


def test_happy_path_intake_pass_and_live_txt(tmp_path: Path) -> None:
    folder = build(tmp_path, with_identity_txt=True)
    live = tmp_path / "live.txt"
    proc = run(folder, "--emit-live-txt", str(live), "--json", str(tmp_path / "intake.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "INTAKE PASS" in proc.stdout
    lines = [ln for ln in live.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 11
    parsed = parse_live_txt(live.read_text(encoding="utf-8"))
    assert parsed == IDENTITY_VALUES
    report = json.loads((tmp_path / "intake.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["identity"]["pwm_frequency_hz"] == 5000


def test_missing_manifest_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path)
    (folder / "SHA256SUMS.txt").unlink()
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I1]" in proc.stdout


def test_manifest_sha_mismatch_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path)
    (folder / "uart_identity.log").write_text("подменено\n", encoding="utf-8")
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I1]" in proc.stdout


def test_file_not_covered_by_manifest_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path)
    (folder / "notes_uncovered.txt").write_text("добавлено после манифеста\n", encoding="utf-8")
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I1]" in proc.stdout and "не покрыто" in proc.stdout


def test_missing_log_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path, refresh_manifest=False)
    (folder / "uart_identity.log").unlink()
    write_manifest(folder)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I2]" in proc.stdout


def test_command_echo_required(tmp_path: Path) -> None:
    folder = build(tmp_path, log_text=SESSION_LOG.replace(">>> mapcap identity\n", ""))
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I3]" in proc.stdout


def test_identity_fail_reply_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path, log_text=SESSION_LOG.replace(IDENTITY_LINE, "@MAP:IDENTITY:FAIL:REJECTED"))
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I4]" in proc.stdout and "прошивка ответила" in proc.stdout


def test_zero_critical_field_blocks(tmp_path: Path) -> None:
    folder = build(tmp_path, log_text=SESSION_LOG.replace("board=7:pwm=5000", "board=7:pwm=0"))
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I5]" in proc.stdout and "pwm_frequency_hz" in proc.stdout


def test_zero_legit_fields_pass(tmp_path: Path) -> None:
    """offset/res = 0 — норма (исключены из I5)."""
    parsed = parse_identity_line(IDENTITY_LINE)
    assert parsed == IDENTITY_VALUES
    folder = build(tmp_path)
    assert run(folder).returncode == 0


def test_identity_txt_mismatch_blocks(tmp_path: Path) -> None:
    bad = "\n".join(f"{k}={v}" for k, v in {**IDENTITY_VALUES, "pwm_frequency_hz": 294}.items()) + "\n"
    folder = build(tmp_path, with_identity_txt=True, identity_txt_text=bad)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I6]" in proc.stdout and "pwm_frequency_hz" in proc.stdout


def test_identity_txt_absent_is_info(tmp_path: Path) -> None:
    folder = build(tmp_path)
    proc = run(folder)
    assert proc.returncode == 0, proc.stdout
    assert "[I6]" in proc.stdout and "INFO" in proc.stdout


def test_unknown_reply_blocks_stale_firmware(tmp_path: Path) -> None:
    log = SESSION_LOG.replace(IDENTITY_LINE, "unknown")
    folder = build(tmp_path, log_text=log)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I7]" in proc.stdout


def test_uart_truncation_blocks(tmp_path: Path) -> None:
    log = SESSION_LOG.replace("uart_drp=0:uart_trunc=0", "uart_drp=0:uart_trunc=3")
    folder = build(tmp_path, log_text=log)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I8]" in proc.stdout


@pytest.mark.parametrize("mutation,rid", [
    (lambda t: t + "\n@BRK:valid=1:seq=1:src=TIM8", "I9"),
    (lambda t: t + "\n@FOC:t=1000:run_id=M0-R1:FAULT=1:FAULT_R=18", "I9"),
    (lambda t: t + "\n@FOC:t=2000:FAULT=0\n@FOC:t=500:FAULT=0", "I9"),
])
def test_protection_and_integrity_blocks(tmp_path: Path, mutation, rid: str) -> None:
    folder = build(tmp_path, log_text=mutation(SESSION_LOG))
    proc = run(folder)
    assert proc.returncode == 1
    assert f"[{rid}]" in proc.stdout


def test_meta_required_and_cross_checked(tmp_path: Path) -> None:
    folder = build(tmp_path, skip_meta=True)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I10]" in proc.stdout

    folder = build(tmp_path / "b", meta_mut=lambda m: m.update({"board_revision": 8}))
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I10]" in proc.stdout and "board_revision" in proc.stdout

    folder = build(tmp_path / "c", meta_mut=lambda m: m.update({"firmware_sha256": "нет"}))
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I10]" in proc.stdout


def test_pwm_must_be_off(tmp_path: Path) -> None:
    log = SESSION_LOG.replace("@PWM:CR1=224:CCER=0", "@PWM:CR1=224:CCER=3")
    folder = build(tmp_path, log_text=log)
    proc = run(folder)
    assert proc.returncode == 1
    assert "[I11]" in proc.stdout

    log_moe = SESSION_LOG.replace("@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0", "") + "\n@FOC:t=1:MOE=0"
    folder = build(tmp_path / "moe", log_text=log_moe)
    assert run(folder).returncode == 0, "MOE=0 — достаточное подтверждение PWM OFF"
