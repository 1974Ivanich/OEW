"""Тесты аудита лога прогона M0: два режима (session | repeat).

Фикстуры повторяют фактические формы логов: сессионный (как возврат R1, с preflight-командами
и mapload) и пер-прогонный (как R2…R5, без preflight/mapload). Проверяется, что контракты
разделены: пер-прогонный лог не обязан проходить session-аудит и наоборот.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from audit_m0_run import audit_repeat, audit_session, Log  # noqa: E402

TOOL = ROOT / "tools" / "audit_m0_run.py"
IDENT = ("@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:"
         "sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0x13552B12")

SESSION_LOG = "\n".join([
    "[utc] META", "[utc] BOOT", ">>> sysinfo",
    "@SYS:CLK=170000000:PSC=16:TCLK=10000000:uart_drp=0:uart_trunc=0", ">>> p?",
    "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0", ">>> pdump",
    "@PWM:FULL:SYS=170000000:T1:PSC=16:ARR=999:CCR=0,0,0", ">>> enc",
    "@ENC:angle=0:speed=0:err=0", ">>> c", "@ADC:CAL:offset_i1=2038", ">>> mapcap identity",
    IDENT, "@MAP:LOAD:OK:crc=0x00E666F3:cid=0x424F4152", "@RUN:ID=M0-R1",
    "@MC:ARM:cap=1:rc=0:offsets_valid=1:inj_start_rc=0", "@MC:RUN:rc=0",
    *[f"@MC:REC:cap=1:seq={i}:raw_i1={2040 + i}:raw_i2={2068 + i}:raw_ct=0:raw_vbus=305:i1=1:i2=2:vbus=3"
      for i in range(1, 9)],
    "@MC:DRAIN:records=8", "@MC:STATUS:state=3:term=0:cap=1:frames=8:dropped=0",
    "@BRK:valid=0", "@SYS:CLK=170000000:uart_drp=0:uart_trunc=0",
    *[f"@FOC:t={1000 + i * 100}:run_id=M0-R1:map_crc32=0x00E666F3:VBUS=30521:FAULT=0:RUN=0"
      for i in range(5)],
])

RUN_LOG = "\n".join([
    *[f"@FOC:t={900 + i}:run_id=UNSET:VBUS=0" for i in range(3)],   # старт: run_id ещё не выставлен
    ">>> mapcap identity", IDENT, ">>> run=M0-R2", "@RUN:ID=M0-R2",
    *[f"@FOC:t={1000 + i * 100}:run_id=M0-R2:VBUS=30521:FAULT=0" for i in range(5)],
    "@MC:ARM:cap=8:rc=0:offsets_valid=1:inj_start_rc=0", ">>> mapcap run", "@MC:RUN:rc=0",
    *[f"@MC:REC:cap=8:seq={i}:raw_i1={2040 + i % 3}:raw_i2={2068 + i % 2}:raw_ct=0:raw_vbus={300 + i}:i1=1:i2=2:vbus=3"
      for i in range(1, 9)],
    ">>> mapcap drain", "@MC:DRAIN:records=8", ">>> mapcap status",
    "@MC:STATUS:state=3:term=0:cap=8:frames=8:dropped=0", ">>> breakdiag", "@BRK:valid=0",
    ">>> sysinfo", "@SYS:CLK=170000000:uart_drp=0:uart_trunc=0", ">>> p?",
    "@PWM:CR1=224:CCER=0:BDTR=7360:CNT=635",
])


def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text + "\n", encoding="utf-8")
    return p


def run_cli(log: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), str(log), *extra],
                          capture_output=True, text=True)


def session_checks(log: Path):
    return audit_session(Log(log), "0x00E666F3", "0x424F4152", 0x13552B12, 24000, 36000)[0]


def repeat_checks(log: Path, run_id: str | None = "M0-R2"):
    return audit_repeat(Log(log), run_id, 0x13552B12, 8, 24000, 36000, 100)[0]


def test_session_mode_passes_on_session_log(tmp_path: Path) -> None:
    log = write(tmp_path, "M0-R1.log", SESSION_LOG)
    checks = session_checks(log)
    assert not checks.failed, checks.render()
    proc = run_cli(log, "--mode", "session")
    assert proc.returncode == 0 and "АУДИТ: PASS" in proc.stdout


def test_session_mode_negative_cases(tmp_path: Path) -> None:
    no_c = write(tmp_path, "a.log", SESSION_LOG.replace(">>> c\n@ADC:CAL:offset_i1=2038\n", ""))
    assert "A1" in {r[0] for r in session_checks(no_c).failed}

    no_load = write(tmp_path, "b.log", SESSION_LOG.replace("@MAP:LOAD:OK:crc=0x00E666F3:cid=0x424F4152", ""))
    assert "A3" in {r[0] for r in session_checks(no_load).failed}

    bad_vbus = write(tmp_path, "c.log", SESSION_LOG.replace("VBUS=30521", "VBUS=41000"))
    assert "A8" in {r[0] for r in session_checks(bad_vbus).failed}

    brk = write(tmp_path, "d.log", SESSION_LOG + "\n@BRK:valid=1:seq=1:src=TIM8")
    assert "A6" in {r[0] for r in session_checks(brk).failed}


def test_repeat_mode_passes_on_run_log(tmp_path: Path) -> None:
    log = write(tmp_path, "M0-R2.log", RUN_LOG)
    checks = repeat_checks(log)
    assert not checks.failed, checks.render()
    proc = run_cli(log, "--mode", "repeat", "--run-id", "M0-R2", "--json", str(tmp_path / "r.json"))
    assert proc.returncode == 0, proc.stdout
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS" and data["cap"] == 8 and data["identity"]["timer_arr"] == 999


@pytest.mark.parametrize("mutation,rule", [
    (lambda t: t.replace(">>> sysinfo\n@SYS:CLK=170000000:uart_drp=0:uart_trunc=0\n", ""), "B7"),
    (lambda t: t.replace("@MC:ARM:cap=8:rc=0", "@MC:ARM:cap=8:rc=-3"), "B3"),
    (lambda t: t.replace("@MC:RUN:rc=0", "@MC:RUN:rc=-14"), "B4"),
    (lambda t: t.replace("dropped=0", "dropped=2"), "B5"),
    (lambda t: t.replace("@BRK:valid=0", "@BRK:valid=1:seq=1:src=TIM8"), "B6"),
    (lambda t: t.replace("raw_vbus=308", "raw_vbus=900"), "B9"),
    (lambda t: t.replace("@RUN:ID=M0-R2", "@RUN:ID=M0-R7"), "B2"),
    (lambda t: t.replace("ccs=0x13552B12", "ccs=0xABE94C77"), "B1"),
])
def test_repeat_mode_negative_cases(tmp_path: Path, mutation, rule: str) -> None:
    log = write(tmp_path, "M0-R2.log", mutation(RUN_LOG))
    failed = {r[0] for r in repeat_checks(log).failed}
    assert rule in failed, (rule, failed)


def test_modes_are_separated(tmp_path: Path) -> None:
    """Пер-прогонный лог не проходит session-контракт (нет preflight/mapload) — и это правильно."""
    run_log = write(tmp_path, "M0-R2.log", RUN_LOG)
    assert {"A1", "A3"} <= {r[0] for r in session_checks(run_log).failed}

    session_log = write(tmp_path, "M0-R1.log", SESSION_LOG)
    # а сессионный лог проходит repeat-контракт, если в нём есть признаки прогона
    repeat_ok = {r[0] for r in repeat_checks(session_log).failed}
    assert "B5" not in repeat_ok and "B3" not in repeat_ok


def test_missing_file_is_blocked(tmp_path: Path) -> None:
    proc = run_cli(tmp_path / "нет.log", "--mode", "repeat")
    assert proc.returncode == 1 and "BLOCKED" in proc.stdout
def test_b10_flags_foreign_run_id_as_not_independent(tmp_path: Path) -> None:
    """Телеметрия чужого прогона до команд = arm'ы в одной эпохе, а не независимый старт."""
    log = write(tmp_path, "M0-R2.log", RUN_LOG)
    assert "B10" not in {r[0] for r in repeat_checks(log).failed}

    dirty = RUN_LOG.replace("@FOC:t=900:run_id=UNSET", "@FOC:t=900:run_id=M0-R5")
    log2 = write(tmp_path, "M0-R2b.log", dirty)
    failed = {r[0] for r in repeat_checks(log2).failed}
    assert "B10" in failed
    detail = dict((r[0], r[2]) for r in repeat_checks(log2).failed)["B10"]
    assert "M0-R5" in detail and "одной эпохе" in detail
