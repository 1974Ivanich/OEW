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


# ══ TZ_PHASE_REFERENCE_IMPLEMENTATION: слой привязки reference ═══════════════
# Строгий режим: привязка только по `sample_id == frame.sequence` + признак из
# карты (тождество). Каждый отказ обязан называть конкретный гейт (E4).

CALIB_JSON = {"vcc_mv": 5000,
              "sensors": {"U": {"v0_mv": 2500, "sens_mv_per_a": 100.0},
                          "V": {"v0_mv": 2500, "sens_mv_per_a": 100.0}}}


def _write_calib(tmp_path: Path) -> Path:
    p = tmp_path / "calib.json"
    p.write_text(json.dumps(CALIB_JSON), encoding="utf-8")
    return p


def _region_log_seqs(dir_: Path, r: int, seq0: int, n: int = 16) -> list:
    """region_<r>.log с ГЛОБАЛЬНЫМИ seq (seq = seq0..seq0+n-1), возвращает seq."""
    sector, window = msi.region_row(r)
    ccr = msi.expected_ccr(sector, window, 0)
    seqs = [seq0 + k for k in range(n)]
    lines = [make_rec_line(seq, 100 + r, ccr, 50 + 5 * k, 60 + 5 * k)
             for k, seq in enumerate(seqs)]
    lines.append(f"@MC:DRAIN:records={n}")
    (dir_ / f"region_{r}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return seqs


def _write_assoc_map(path: Path, entries: dict) -> None:
    """entries: seq -> (feature, kind)."""
    lines = ["seq,assoc_feature,assoc_feature_kind"]
    for seq in sorted(entries):
        feat, kind = entries[seq]
        lines.append(f"{seq},{feat},{kind}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_strict_csv(path: Path, rows: list) -> None:
    """rows: dict(sample_id, feature, mv_u, mv_v, dt_us[, kind])."""
    lines = ["pulse,sample_id,assoc_feature,assoc_feature_kind,ref_u_mv,ref_v_mv,"
             "dt_us,scope_qualified,margin_ticks,blanking_ticks"]
    for i, row in enumerate(rows, 1):
        lines.append(",".join(str(x) for x in (
            i, row["sample_id"], row["feature"], row.get("kind", "la_marker"),
            row["mv_u"], row["mv_v"], row.get("dt_us", 0.0), 1, 110, 15)))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _strict_fixture(tmp_path: Path, *, feature_of=None, dt_us=0.0,
                    mv_u=2500, mv_v=2600, regions: int = 12):
    """12 регионов x 16 записей с ГЛОБАЛЬНЫМИ seq; строгие CSV + одна общая карта."""
    logs, scope = tmp_path / "logs", tmp_path / "scope"
    logs.mkdir(exist_ok=True)
    scope.mkdir(exist_ok=True)
    feature_of = feature_of or (lambda seq: f"LA{seq:04d}")
    entries, seq = {}, 1
    for r in range(regions):
        seqs = _region_log_seqs(logs, r, seq)
        rows = []
        for s in seqs:
            feat = feature_of(s)
            entries[s] = (feat, "la_marker")
            rows.append({"sample_id": s, "feature": feat, "mv_u": mv_u,
                         "mv_v": mv_v, "dt_us": dt_us})
        _write_strict_csv(scope / f"scope_region_{r}.csv", rows)
        seq += 16
    assoc = tmp_path / "assoc_map.csv"
    _write_assoc_map(assoc, entries)
    return logs, scope, assoc


def test_strict_happy_path_is_stamped_reference_verified(tmp_path):
    logs, scope, assoc = _strict_fixture(tmp_path)
    out = tmp_path / "camp"
    manifest, samples = msi.build_campaign(
        logs, scope, out, calib_file=_write_calib(tmp_path), assoc_map_file=assoc,
        strict_association=True, dt_window_us=(-2.0, 2.0))
    assert len(samples) == 192
    assert manifest["association"]["mode"] == "reference_verified"
    assert manifest["association"]["independent_reference"] is True
    assert manifest["association"]["sample_id_field"] == "sample_id"
    assert manifest["association"]["dt_window_us"] == [-2.0, 2.0]
    assert all(s["sample_id"] == s["seq"] for s in samples)


def test_strict_requires_sample_id_column(tmp_path):
    logs, scope, assoc = _strict_fixture(tmp_path)
    csv = scope / "scope_region_0.csv"
    lines = csv.read_text(encoding="utf-8").splitlines()
    idx = lines[0].split(",").index("sample_id")
    lines = [",".join(c for i, c in enumerate(ln.split(",")) if i != idx)
             for ln in lines]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reference-режим требует колонки"):
        msi.build_campaign(logs, scope, tmp_path / "camp",
                           calib_file=_write_calib(tmp_path), assoc_map_file=assoc,
                           strict_association=True, dt_window_us=(-2.0, 2.0))


def test_strict_duplicate_sample_id_rejected(tmp_path):
    logs, scope, assoc = _strict_fixture(tmp_path)
    csv = scope / "scope_region_0.csv"
    lines = csv.read_text(encoding="utf-8").splitlines()
    cells = lines[2].split(",")
    cells[1] = lines[1].split(",")[1]          # sample_id первой строки продублирован
    lines[2] = ",".join(cells)
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="гейт sample_id_unique"):
        msi.build_campaign(logs, scope, tmp_path / "camp",
                           calib_file=_write_calib(tmp_path), assoc_map_file=assoc,
                           strict_association=True, dt_window_us=(-2.0, 2.0))


def test_strict_shifted_feature_rejected_by_identity_gate(tmp_path):
    """Рассинхрон привязки (CSV отнесён к соседнему кадру) обязан лечь на identity-гейт."""
    logs, scope, assoc = _strict_fixture(tmp_path)
    csv = scope / "scope_region_0.csv"
    lines = csv.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split(",")            # первая строка = sample_id 1
    cells[2] = "LA0002"                    # признак соседнего кадра (сдвиг ±1)
    lines[1] = ",".join(cells)
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="гейт assoc_feature_identity"):
        msi.build_campaign(logs, scope, tmp_path / "camp",
                           calib_file=_write_calib(tmp_path), assoc_map_file=assoc,
                           strict_association=True, dt_window_us=(-2.0, 2.0))


def test_strict_dt_outside_window_rejected(tmp_path):
    logs, scope, assoc = _strict_fixture(tmp_path, dt_us=25.0)
    with pytest.raises(ValueError, match="гейт dt_window"):
        msi.build_campaign(logs, scope, tmp_path / "camp",
                           calib_file=_write_calib(tmp_path), assoc_map_file=assoc,
                           strict_association=True, dt_window_us=(-2.0, 2.0))


def test_strict_reference_saturation_rejected():
    """E5: reference в упоре/отключён (вне [margin, vcc-margin]) — REJECT."""
    row = {"sample_id": 1, "assoc_feature": "LA0001",
           "assoc_feature_kind": "la_marker", "ref_u_mv": 1, "ref_v_mv": 2500}
    with pytest.raises(msi.AssocReject) as excinfo:
        msi.check_association(0, [{"seq": 1}], [row], None,
                              saturation_margin_mv=200.0, vcc_mv=5000.0)
    assert excinfo.value.gate == "reference_saturation"   # гейт проверяем структурно


def test_identity_gate_needs_per_frame_distinct_features():
    """Контроль ЧУВСТВИТЕЛЬНОСТИ: при одинаковых признаках сдвиг identity-гейт НЕ ловит.

    Это фиксирует требование к reference-тракту: признак обязан РАЗЛИЧАТЬ кадры,
    иначе E4 вырождается в тавтологию (сдвинул — пересчитал — PASS).
    """
    entries = {seq: ("LA-FLAT", "la_marker") for seq in range(1, 6)}
    rows = [{"sample_id": seq, "assoc_feature": "LA-FLAT",
             "assoc_feature_kind": "la_marker"} for seq in range(1, 6)]
    records = [{"seq": seq} for seq in range(1, 6)]
    msi.check_association(0, records, rows, entries, shift=1)   # неразличимо — не кидает
    rows[0]["assoc_feature"] = "ДРУГОЙ"
    with pytest.raises(msi.AssocReject) as excinfo:
        msi.check_association(0, records, rows, entries, shift=0)
    assert excinfo.value.gate == "assoc_feature_identity"  # гейт, а не «числа не сошлись»


def test_neg_shift_cli_rejects_and_reports_e4_pass(tmp_path, capsys):
    """E4 через CLI: сдвиг ±1 обязан быть отклонён, прогон сообщает гейт."""
    logs, scope, assoc = _strict_fixture(tmp_path)
    for shift in ("1", "-1"):
        rc = msi.main(["--logs", str(logs), "--scope", str(scope),
                       "--out", str(tmp_path / f"camp{shift}"),
                       "--calib", str(_write_calib(tmp_path)),
                       "--assoc-map", str(assoc),
                       "--dt-window-us", "-2", "2",
                       "--saturation-margin-mv", "200",
                       "--neg-shift", shift])
        err = capsys.readouterr()
        assert rc == 0, (shift, err.out, err.err)
        assert "E4 PASS" in err.out


def test_neg_shift_cli_gate_is_named(tmp_path, capsys):
    """E4 обязан назвать КОНКРЕТНЫЙ гейт, которым отклонён сдвиг (не «числа не сошлись»).

    При неразличимом признаке сдвиг ловится гейтом покрытия (reference региона не
    содержит соседних sample_id). Различимость признака как ТРЕБОВАНИЕ зафиксирована
    unit-тестом test_identity_gate_needs_per_frame_distinct_features.
    """
    logs, scope, assoc = _strict_fixture(tmp_path, feature_of=lambda seq: "LA-FLAT")
    rc = msi.main(["--logs", str(logs), "--scope", str(scope),
                   "--out", str(tmp_path / "camp"),
                   "--calib", str(_write_calib(tmp_path)),
                   "--assoc-map", str(assoc),
                   "--dt-window-us", "-2", "2",
                   "--neg-shift", "1"])
    out = capsys.readouterr()
    assert rc == 0, (out.out, out.err)
    assert "E4 PASS" in out.out and "sample_id_coverage" in out.out


def test_legacy_mode_is_stamped_positional_legacy(tmp_path):
    """Обратная совместимость: порядковая привязка работает, но штампуется честно."""
    logs, scope = tmp_path / "logs", tmp_path / "scope"
    logs.mkdir()
    scope.mkdir()
    for r in range(12):
        make_region_log(logs, r, [50 + 5 * k for k in range(16)],
                        [60 + 5 * k for k in range(16)])
        make_scope_csv(scope, r, [(50, 60, -110)] * 16)
    manifest, samples = msi.build_campaign(logs, scope, tmp_path / "camp")
    assert len(samples) == 192
    assert manifest["association"]["mode"] == "positional_legacy"
    assert manifest["association"]["independent_reference"] is False


def test_scope_waiver_is_stamped_not_independent(tmp_path):
    """G0 v4 waiver: независимого референса нет — штамп обязан это фиксировать."""
    logs = tmp_path / "logs"
    logs.mkdir()
    for r in range(12):
        make_region_log(logs, r, [50 + 5 * k for k in range(16)],
                        [60 + 5 * k for k in range(16)])
    manifest, _ = msi.build_campaign(logs, tmp_path / "none", tmp_path / "camp",
                                     scope_waiver=True)
    assert manifest["association"]["mode"] == "shunt_waiver"
    assert manifest["association"]["independent_reference"] is False


def test_saturation_gate_requires_mv_mode():
    """E5 не имеет права молча пропускаться: в legacy mA-режиме он невыполним ⇒ отказ."""
    row = {"sample_id": 1, "assoc_feature": "LA0001",
           "assoc_feature_kind": "la_marker", "ref_u_ma": 50, "ref_v_ma": 60}
    with pytest.raises(ValueError, match="E5 требует mV-режима"):
        msi.check_association(0, [{"seq": 1}], [row], None,
                              saturation_margin_mv=200.0, vcc_mv=5000.0)
