"""Regression for tools/map_scope_ingest.py (scope layer of the BOAR campaign).

Собирает синтетическую кампанию (12 region-логов x 16 @MC:REC + 12 scope CSV
с независимым фазным референсом) и проверяет:

  * happy path: ingest -> кампания проходит официальный валидатор
    (map_bench_dataset.validate_campaign), 192 сэмпла, refs/provenance
    заполнены;
  * fail-closed: отсутствие scope CSV, scope_qualified=0, margin < 110,
    KCL-нарушение, неверный CCR (лог не того региона), отсутствующий лог,
    неполный лог (нет DRAIN / не 16 записей) — всё REJECT до записи вывода.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import map_scope_ingest as msi  # noqa: E402
import map_bench_dataset as mbd  # noqa: E402

TRIG = 0x4F455731


def make_rec_line(seq: int, cap: int, ccr: tuple, i1: int, i2: int) -> str:
    """Формат @MC:REC 1-в-1 с прошивкой (см. region_*.log 01.09.2026)."""
    ccr_s = ",".join(str(c) for c in ccr)
    raw1, raw2 = 2000 + i1, 2000 + i2
    return (f"@MC:REC:cap={cap}:seq={seq}:raw_i1={raw1}:raw_i2={raw2}"
            f":raw_ct=0:raw_vbus=600:i1={i1}:i2={i2}:vbus=60000"
            f":ccr1={ccr_s}:ccr8={ccr_s}:arr=999:trig={TRIG}:status=7:fault=0")


def make_region_log(dir_: Path, r: int, i1s: list, i2s: list,
                    point: int | None = None, records: int = 16) -> None:
    """point=None -> region_<r>.log (одиночная раскладка); иначе
    region_<r>_<p>.log (grid-раскладка). records = число REC на точку."""
    sector, window = msi.region_row(r)
    ccr = msi.expected_ccr(sector, window, point or 0)
    name = f"region_{r}.log" if point is None else f"region_{r}_{point}.log"
    lines = [make_rec_line(seq, 100 + r, ccr, i1, i2)
             for seq, (i1, i2) in enumerate(zip(i1s, i2s), 1)]
    lines.append(f"@MC:DRAIN:records={records}")
    (dir_ / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_scope_csv(dir_: Path, r: int, refs: list, qualified: int = 1,
                   margin: int = 110, blanking: int = 15,
                   point: int | None = None) -> None:
    sector, window = msi.region_row(r)
    name = f"scope_region_{r}.csv" if point is None \
        else f"scope_region_{r}_{point}.csv"
    lines = [f"# scope {name} sector={sector} window={window}"]
    lines.append("pulse,ref_u_ma,ref_v_ma,ref_w_ma,margin_ticks,blanking_ticks,"
                 "scope_qualified,note")
    for k, (u, v, w) in enumerate(refs, 1):
        lines.append(f"{k},{u},{v},{w},{margin},{blanking},{qualified},test")
    (dir_ / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_scope_mv_csv(dir_: Path, r: int, refs: list, point: int | None = None,
                      mixed: bool = False) -> None:
    name = f"scope_region_{r}.csv" if point is None else f"scope_region_{r}_{point}.csv"
    lines = ["# ACS712 raw oscilloscope millivolts; ref_w_mv blank means KCL"]
    header = ("pulse,ref_u_mv,ref_v_mv,ref_w_mv,margin_ticks,blanking_ticks,"
              "scope_qualified,note")
    if mixed:
        header = header.replace("ref_w_mv", "ref_w_ma")
    lines.append(header)
    # 2498/2504 mV are zero-current offsets for the fixture calibration.
    for k, (u, v, _w) in enumerate(refs, 1):
        lines.append(f"{k},{2498 + u // 10},{2504 + v // 10},,110,15,1,mv")
    (dir_ / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _lcg(seed: int, n: int, lo: int, hi: int) -> list:
    """Детерминированный псевдослучайный ряд в [lo, hi]."""
    out = []
    x = seed
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(lo + x % (hi - lo + 1))
    return out


def build_fixture(tmp_path: Path):
    """Полная синтетическая кампания: 12 логов + 12 scope CSV (identity-M)."""
    logs = tmp_path / "logs"
    scope = tmp_path / "scope"
    logs.mkdir()
    scope.mkdir()
    for r in range(12):
        # idc1/idc2 независимы по импульсам -> возбуждение не вырождено
        i1s = _lcg(1000 + r * 7, 16, 100, 900)
        i2s = _lcg(5000 + r * 11, 16, 120, 700)
        make_region_log(logs, r, i1s, i2s)
        refs = [(i1, i2, -(i1 + i2)) for i1, i2 in zip(i1s, i2s)]
        make_scope_csv(scope, r, refs)
    return logs, scope


def test_mv_calibration_and_kcl(tmp_path):
    logs, scope = build_fixture(tmp_path)
    calibration = tmp_path / "acs712_calibration.json"
    calibration.write_text(json.dumps({"vcc_mv": 5000, "sensors": {
        "U": {"v0_mv": 2498, "sens_mv_per_a": 100.0},
        "V": {"v0_mv": 2504, "sens_mv_per_a": 100.0}}}), encoding="utf-8")
    for r in range(12):
        i1s = _lcg(1000 + r * 7, 16, 100, 900)
        i2s = _lcg(5000 + r * 11, 16, 120, 700)
        make_scope_mv_csv(scope, r, [(i1, i2, 0) for i1, i2 in zip(i1s, i2s)])
    manifest, samples = msi.build_campaign(logs, scope, tmp_path / "out",
                                            calib_file=calibration)
    assert len(samples) == 192
    assert all(s["ref_w_ma"] == -(s["ref_u_ma"] + s["ref_v_ma"]) for s in samples)
    assert manifest["dataset_crc32"] != 0


def test_mv_explicit_sensitivity_and_ratiometric_default(tmp_path):
    csv_path = tmp_path / "scope.csv"
    csv_path.write_text("pulse,ref_u_mv,ref_v_mv,ref_w_mv,scope_qualified\n"
                        "1,2600,2600,,1\n", encoding="utf-8")
    calib_path = tmp_path / "calib.json"
    calib_path.write_text(json.dumps({"vcc_mv": 5100, "sensors": {
        "U": {"v0_mv": 2500, "sens_mv_per_a": 80.0},
        "V": {"v0_mv": 2500}}}), encoding="utf-8")
    rows = msi.parse_scope_csv(csv_path, 1, msi._load_calibration(calib_path))
    assert rows[0]["ref_u_ma"] == 1250
    assert rows[0]["ref_v_ma"] == 980  # 100 mV / 102 mV/A, rounded
    assert rows[0]["ref_w_ma"] == -2230


def test_mv_requires_calibration(tmp_path):
    logs, scope = build_fixture(tmp_path)
    for r in range(12):
        i1s = _lcg(1000 + r * 7, 16, 100, 900)
        i2s = _lcg(5000 + r * 11, 16, 120, 700)
        make_scope_mv_csv(scope, r, [(i1, i2, 0) for i1, i2 in zip(i1s, i2s)])
    with pytest.raises(ValueError, match="mV-режим требует"):
        msi.build_campaign(logs, scope, tmp_path / "out")


def test_mv_over_limit_and_mixed_headers_rejected(tmp_path):
    logs, scope = build_fixture(tmp_path)
    for r in range(12):
        i1s = [100] * 16; i2s = [100] * 16
        make_scope_mv_csv(scope, r, [(i1, i2, 0) for i1, i2 in zip(i1s, i2s)])
    calibration = tmp_path / "calib.json"
    calibration.write_text(json.dumps({"sensors": {"U": {"v0_mv": 0},
        "V": {"v0_mv": 2500}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="ref_u_ma"):
        msi.build_campaign(logs, scope, tmp_path / "out", calib_file=calibration)
    make_scope_mv_csv(scope, 0, [(100, 100, 0)] * 16, mixed=True)
    with pytest.raises(ValueError, match="смешение"):
        msi.build_campaign(logs, scope, tmp_path / "out2", calib_file=calibration)


def test_happy_path(tmp_path):
    logs, scope = build_fixture(tmp_path)
    out = tmp_path / "campaign"
    manifest, samples = msi.build_campaign(logs, scope, out)

    assert len(samples) == 192
    assert manifest["dataset_crc32"] != 0
    assert manifest["tool_build_id"] == msi.TOOL_BUILD_ID
    assert manifest["characterization_id"] == msi.BOAR_BASE_ID
    # официальный валидатор принимает собранную кампанию
    mbd.validate_campaign(out)
    # sector/window соответствуют региону, refs взяты из CSV
    for r in range(12):
        sector, window = msi.region_row(r)
        row = [s for s in samples if s["sector"] == sector
               and s["window"] == window]
        assert len(row) == 16
        for s in row:
            assert s["adc_settled"] == 1 and s["scope_qualified"] == 1
            assert s["margin_ticks"] == 110
            assert s["ref_u_ma"] + s["ref_v_ma"] + s["ref_w_ma"] == 0


def test_grid_layout(tmp_path):
    """Grid-раскладка (TZ_MAP_GRID_PROFILE): 4 точки на строку по 8 импульсов
    (профиль v2, pulse_count=8 -> 32 сэмпла на строку, лимит accumulator).
    Итог: 384 сэмпла, по строке 4 различных модуляционных точки."""
    logs = tmp_path / "logs"
    scope = tmp_path / "scope"
    logs.mkdir()
    scope.mkdir()
    for r in range(12):
        for p in range(4):
            i1s = _lcg(1000 + r * 7 + p * 101, 8, 100, 900)
            i2s = _lcg(5000 + r * 11 + p * 97, 8, 120, 700)
            make_region_log(logs, r, i1s, i2s, point=p, records=8)
            make_scope_csv(scope, r, [(i1, i2, -(i1 + i2))
                                      for i1, i2 in zip(i1s, i2s)], point=p)
    out = tmp_path / "campaign_grid"
    manifest, samples = msi.build_campaign(logs, scope, out)
    assert len(samples) == 12 * 4 * 8 == 384
    mbd.validate_campaign(out)
    for r in range(12):
        sector, window = msi.region_row(r)
        row = [s for s in samples if s["sector"] == sector
               and s["window"] == window]
        assert len(row) == 32
        points = {(s["ccr1"], s["ccr2"], s["ccr3"]) for s in row}
        assert len(points) == 4, points
        # каждая точка = ожидаемый grid-вектор (проверка exact-match)
        for p in range(4):
            assert msi.expected_ccr(sector, window, p) in points


def test_grid_missing_point_rejected(tmp_path):
    """grid-раскладка с пропущенной точкой -> REJECT (все 4 обязательны)."""
    logs = tmp_path / "logs"
    scope = tmp_path / "scope"
    logs.mkdir()
    scope.mkdir()
    for r in range(12):
        for p in range(4):
            i1s = _lcg(1 + r + p, 8, 100, 900)
            i2s = _lcg(100 + r + p, 8, 120, 700)
            make_region_log(logs, r, i1s, i2s, point=p, records=8)
            make_scope_csv(scope, r, [(i1, i2, -(i1 + i2))
                                      for i1, i2 in zip(i1s, i2s)], point=p)
    (logs / "region_3_2.log").unlink()
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "region_3_2.log" in str(exc.value)


def test_reject_missing_scope_csv(tmp_path):
    logs, scope = build_fixture(tmp_path)
    (scope / "scope_region_3.csv").unlink()
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "scope_region_3.csv" in str(exc.value)


def test_reject_scope_not_qualified(tmp_path):
    logs, scope = build_fixture(tmp_path)
    make_scope_csv(scope, 5,
                   [(100, 100, -200)] * 16, qualified=0)
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "scope_qualified=0" in str(exc.value)


def test_reject_margin_below_contract(tmp_path):
    logs, scope = build_fixture(tmp_path)
    make_scope_csv(scope, 7,
                   [(100, 100, -200)] * 16, margin=100)
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "margin 100 < 110" in str(exc.value)


def test_reject_kcl_violation(tmp_path):
    logs, scope = build_fixture(tmp_path)
    make_scope_csv(scope, 9, [(300, 200, 0)] * 16)  # KCL = 500 > 100
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "KCL" in str(exc.value)


def test_reject_wrong_ccr(tmp_path):
    """Лог региона 2 (sector 1) с вектором сектора 0 — лог не того региона."""
    logs, scope = build_fixture(tmp_path)
    i1s = _lcg(1, 16, 100, 900)
    i2s = _lcg(2, 16, 120, 700)
    lines = [make_rec_line(seq, 102, (625, 500, 375), i1, i2)
             for seq, (i1, i2) in enumerate(zip(i1s, i2s), 1)]
    lines.append("@MC:DRAIN:records=16")
    (logs / "region_2.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "ccr" in str(exc.value)


def test_reject_missing_region_log(tmp_path):
    logs, scope = build_fixture(tmp_path)
    (logs / "region_11.log").unlink()
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "region_11.log" in str(exc.value)


def test_reject_incomplete_log(tmp_path):
    logs, scope = build_fixture(tmp_path)
    # 15 записей вместо 16
    text = (logs / "region_0.log").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.startswith("@MC:REC:")]
    (logs / "region_0.log").write_text(
        "\n".join(lines[:15] + ["@MC:DRAIN:records=16"]) + "\n",
        encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "REC parsed" in str(exc.value)


def test_reject_no_drain_summary(tmp_path):
    logs, scope = build_fixture(tmp_path)
    text = (logs / "region_1.log").read_text(encoding="utf-8")
    (logs / "region_1.log").write_text(
        "\n".join(l for l in text.splitlines()
                  if not l.startswith("@MC:DRAIN:")) + "\n",
        encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "@MC:DRAIN" in str(exc.value)


def test_refs_above_shunt_limit(tmp_path):
    logs, scope = build_fixture(tmp_path)
    make_scope_csv(scope, 0, [(12000, 100, -12100)] * 16)  # > 10000 mA
    with pytest.raises(ValueError) as exc:
        msi.build_campaign(logs, scope, tmp_path / "out")
    assert "ref_u_ma" in str(exc.value)


def test_legacy_empty_ref_w_uses_kcl(tmp_path):
    logs, scope = build_fixture(tmp_path)
    text = (scope / "scope_region_0.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    lines[1] = lines[1]
    for i in range(2, len(lines)):
        fields = lines[i].split(",")
        fields[3] = ""
        lines[i] = ",".join(fields)
    (scope / "scope_region_0.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, samples = msi.build_campaign(logs, scope, tmp_path / "out")
    assert all(s["ref_w_ma"] == -(s["ref_u_ma"] + s["ref_v_ma"]) for s in samples[:16])


# ── Scope waiver mode (G0 v4) ─────────────────────────────────────────


def build_grid_fixture_logs_only(tmp_path: Path):
    """Grid-раскладка: 12 regions x 4 points x 8 records, only logs (no scope)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    for r in range(12):
        for p in range(4):
            i1s = _lcg(1000 + r * 7 + p * 101, 8, 100, 900)
            i2s = _lcg(5000 + r * 11 + p * 97, 8, 120, 700)
            make_region_log(logs, r, i1s, i2s, point=p, records=8)
    return logs


def test_scope_waiver_grid_happy_path(tmp_path):
    """scope_waiver=True: 384 samples from shunt ADC, validator passes."""
    logs = build_grid_fixture_logs_only(tmp_path)
    out = tmp_path / "campaign"
    manifest, samples = msi.build_campaign(
        logs, ".", out, scope_waiver=True)
    assert len(samples) == 384
    assert manifest["dataset_crc32"] != 0
    mbd.validate_campaign(out)
    for s in samples:
        assert s["adc_settled"] == 1
        assert s["scope_qualified"] == 1
        assert s["margin_ticks"] == msi.BOAR_MARGIN
        assert s["blanking_ticks"] == msi.BOAR_BLANKING
        # ref comes from shunt ADC: ref_u=idc1, ref_v=idc2, ref_w=-(i1+i2)
        assert s["ref_u_ma"] == s["idc1_ma"]
        assert s["ref_v_ma"] == s["idc2_ma"]
        assert s["ref_w_ma"] == -(s["ref_u_ma"] + s["ref_v_ma"])


def test_scope_waiver_no_scope_dir_needed(tmp_path):
    """scope_waiver=True: no scope/ directory required at all."""
    logs = build_grid_fixture_logs_only(tmp_path)
    out = tmp_path / "campaign"
    # scope_dir points to nonexistent path — must not raise
    manifest, samples = msi.build_campaign(
        logs, tmp_path / "nonexistent_scope", out, scope_waiver=True)
    assert len(samples) == 384
    mbd.validate_campaign(out)


def test_scope_waiver_single_point_layout(tmp_path):
    """scope_waiver=True with single-point (16 records) layout."""
    logs = tmp_path / "logs"
    logs.mkdir()
    for r in range(12):
        i1s = _lcg(1000 + r * 7, 16, 100, 900)
        i2s = _lcg(5000 + r * 11, 16, 120, 700)
        make_region_log(logs, r, i1s, i2s)
    out = tmp_path / "campaign"
    manifest, samples = msi.build_campaign(
        logs, ".", out, scope_waiver=True)
    assert len(samples) == 192
    mbd.validate_campaign(out)
    for s in samples:
        assert s["ref_u_ma"] == s["idc1_ma"]
        assert s["ref_v_ma"] == s["idc2_ma"]


def test_scope_waiver_preserves_ccr_validation(tmp_path):
    """scope_waiver=True still validates CCR vectors (wrong region -> REJECT)."""
    logs = build_grid_fixture_logs_only(tmp_path)
    # Overwrite region_2_0.log with wrong CCR (sector 0 vector instead of sector 1)
    i1s = _lcg(1, 8, 100, 900)
    i2s = _lcg(2, 8, 120, 700)
    wrong_ccr = msi.expected_ccr(0, 0, 0)  # sector 0, not sector 1
    lines = [make_rec_line(seq, 102, wrong_ccr, i1, i2)
             for seq, (i1, i2) in enumerate(zip(i1s, i2s), 1)]
    lines.append("@MC:DRAIN:records=8")
    (logs / "region_2_0.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ccr"):
        msi.build_campaign(logs, ".", tmp_path / "out", scope_waiver=True)
