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
