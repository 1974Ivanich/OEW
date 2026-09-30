"""MAP_BASIS_FIX Phase 1 gate: физический базис карты (inv1 − inv8).

ТЗ: ``MAP_BASIS_FIX_SCOPE_rev2.md`` §2 (ревизия 2). Дефект: ingest описывал
сектор ОДНИМ инвертором (``inv1``), поэтому построитель ставил регионы карты
на 30/90/150/…, тогда как физический вектор привода с ОТКРЫТЫМИ ОБМОТКАМИ
равен разности инверторов и лежит на 60/0/120/180/−60/−120°. Это
систематический поворот карты на +30° (плюс ошибка амплитуды ×√3 в плоскости
Кларка) — селектор FOC не попадал в боксы, handoff не достигался.

Гейт обязан быть ЗЕЛЁНЫМ на физическом базисе и КРАСНЫМ на старой
одноинверторной таблице — иначе это декорация, а не гейт (§2.4).

Данные: 48 пар ``ccr1``/``ccr8`` кампании BOAR 01.09.2026 — raw dataset
хендовера ``map_basis_fix_handover_20260929/RAW/logs`` (48 файлов × 8 записей
= 384 ``@MC:REC``; файл заморожен и не перезаписывается). Внутри файла пара
постоянна; регион r = sector*2 + window, файл ``region_<r>_<point>.log``.
Значения сняты дампером пар из самих логов (не вводились руками).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import map_scope_ingest as msi  # noqa: E402

TRIG = 0x4F455731

# region -> point -> (ccr1, ccr8)  [raw campaign 01.09.2026, read-only]
CAMPAIGN_CCR_PAIRS = {
    0: {
        0: ((625, 500, 375), (500, 375, 625)),
        1: ((629, 496, 375), (496, 375, 629)),
        2: ((625, 504, 371), (504, 371, 625)),
        3: ((621, 500, 379), (500, 379, 621)),
    },
    1: {
        0: ((641, 500, 359), (500, 359, 641)),
        1: ((645, 496, 359), (496, 359, 645)),
        2: ((641, 504, 355), (504, 355, 641)),
        3: ((637, 500, 363), (500, 363, 637)),
    },
    2: {
        0: ((625, 375, 500), (375, 500, 625)),
        1: ((629, 371, 500), (371, 500, 629)),
        2: ((625, 379, 496), (379, 496, 625)),
        3: ((621, 375, 504), (375, 504, 621)),
    },
    3: {
        0: ((641, 359, 500), (359, 500, 641)),
        1: ((645, 355, 500), (355, 500, 645)),
        2: ((641, 363, 496), (363, 496, 641)),
        3: ((637, 359, 504), (359, 504, 637)),
    },
    4: {
        0: ((500, 625, 375), (625, 375, 500)),
        1: ((504, 621, 375), (621, 375, 504)),
        2: ((500, 629, 371), (629, 371, 500)),
        3: ((496, 625, 379), (625, 379, 496)),
    },
    5: {
        0: ((500, 641, 359), (641, 359, 500)),
        1: ((504, 637, 359), (637, 359, 504)),
        2: ((500, 645, 355), (645, 355, 500)),
        3: ((496, 641, 363), (641, 363, 496)),
    },
    6: {
        0: ((375, 625, 500), (625, 500, 375)),
        1: ((379, 621, 500), (621, 500, 379)),
        2: ((375, 629, 496), (629, 496, 375)),
        3: ((371, 625, 504), (625, 504, 371)),
    },
    7: {
        0: ((359, 641, 500), (641, 500, 359)),
        1: ((363, 637, 500), (637, 500, 363)),
        2: ((359, 645, 496), (645, 496, 359)),
        3: ((355, 641, 504), (641, 504, 355)),
    },
    8: {
        0: ((500, 375, 625), (375, 625, 500)),
        1: ((504, 371, 625), (371, 625, 504)),
        2: ((500, 379, 621), (379, 621, 500)),
        3: ((496, 375, 629), (375, 629, 496)),
    },
    9: {
        0: ((500, 359, 641), (359, 641, 500)),
        1: ((504, 355, 641), (355, 641, 504)),
        2: ((500, 363, 637), (363, 637, 500)),
        3: ((496, 359, 645), (359, 645, 496)),
    },
    10: {
        0: ((375, 500, 625), (500, 625, 375)),
        1: ((379, 496, 625), (496, 625, 379)),
        2: ((375, 504, 621), (504, 621, 375)),
        3: ((371, 500, 629), (500, 629, 371)),
    },
    11: {
        0: ((359, 500, 641), (500, 641, 359)),
        1: ((363, 496, 641), (496, 641, 363)),
        2: ((359, 504, 637), (504, 637, 359)),
        3: ((355, 500, 645), (500, 645, 355)),
    },
}

# ── Замороженные таблицы базиса ─────────────────────────────────────────
# Физический базис секторов (inv1 − inv8), ТЗ §2; углы сверены с измеренными.
PHYSICAL_Q15 = {
    0: (8192, 8192, -16384),    # +60°
    1: (16384, -8192, -8192),   # 0°
    2: (-8192, 16384, -8192),   # +120°
    3: (-16384, 8192, 8192),    # 180°
    4: (8192, -16384, 8192),    # −60°
    5: (-8192, -8192, 16384),   # −120°
}

# Таблица ДО фикса (она же измеренный абсолютный паттерн inv1): один
# инвертор, углы 30/−30/90/150/−90/−150 — отстают на 30° от физических.
LEGACY_INV1_Q15 = {
    0: (8192, 0, -8192),
    1: (8192, -8192, 0),
    2: (0, 8192, -8192),
    3: (-8192, 8192, 0),
    4: (0, -8192, 8192),
    5: (-8192, 0, 8192),
}

SECTOR_ANGLE_DEG = {0: 60.0, 1: 0.0, 2: 120.0, 3: 180.0, 4: -60.0, 5: -120.0}

_GRID_OFFSETS = ((0, 0, 0), (4, -4, 0), (0, 4, -4), (-4, 0, 4))
_WINDOW1_SHIFT = 16


def _angle_deg(vec: tuple[int, int, int]) -> float:
    """Угол трёхфазного вектора в плоскости Кларка, град."""
    a, b, _c = vec
    beta = (a + 2.0 * b) / math.sqrt(3.0)
    return math.degrees(math.atan2(beta, float(a)))


def _clarke_mag(vec: tuple[int, int, int]) -> float:
    """Модуль вектора в плоскости Кларка."""
    a, b, _c = vec
    beta = (a + 2.0 * b) / math.sqrt(3.0)
    return math.hypot(float(a), beta)


def _q15_from_diff_ccr(diff: tuple[int, int, int]) -> tuple[int, int, int]:
    """Дифференциальный CCR -> Q15: центр 500 сокращается, поэтому
    mod = diff*32768/500 (для разности паттернов это тождественно
    (ccr1 − 500) − (ccr8 − 500) в Q15)."""
    return tuple(round(c * 32768 / 500) for c in diff)


def _inv_ccr(table: dict, sector: int, window: int, point: int,
             rotate_offsets: bool) -> tuple[int, int, int]:
    """CCR одного инвертора: его вектор + сдвиг окна + grid-смещение точки.

    Окно 1 сдвигает собственный max/min этого паттерна на ±16 CCR; для
    второго инвертора grid-смещение повёрнуто на одну фазу (та же раскладка
    в его собственной нумерации — сверено со всеми 48 логами кампании).
    """
    base = [500 + m * 500 // 32768 for m in table[sector]]
    if window == 1:
        mx = max(range(3), key=lambda i: table[sector][i])
        mn = min(range(3), key=lambda i: table[sector][i])
        base[mx] += _WINDOW1_SHIFT
        base[mn] -= _WINDOW1_SHIFT
    off = _GRID_OFFSETS[point]
    if rotate_offsets:
        off = (off[1], off[2], off[0])
    return tuple(base[i] + off[i] for i in range(3))


def _pair_for(physical_table: dict, sector: int, window: int,
              point: int) -> tuple[tuple, tuple]:
    """(ccr1, ccr8) для гипотезы «физический вектор сектора = table[sector]»."""
    inv8 = {s: tuple(LEGACY_INV1_Q15[s][i] - physical_table[s][i]
                     for i in range(3))
            for s in physical_table}
    return (_inv_ccr(LEGACY_INV1_Q15, sector, window, point, False),
            _inv_ccr(inv8, sector, window, point, True))


def physical_table_matches_campaign(table: dict) -> bool:
    """Воспроизводит ли модель ingest с физической таблицей `table` все 48 пар?

    Именно это делает тест гейтом: True только для физического базиса
    (inv1 − inv8). Для старой одноинверторной таблицы модель не сходится с
    записями (inv8 вырождается в нули, окно/точки не совпадают) -> False.
    """
    for region, points in CAMPAIGN_CCR_PAIRS.items():
        sector, window = region // 2, region % 2
        for point, pair in points.items():
            if _pair_for(table, sector, window, point) != pair:
                return False
    return True


def legacy_expected_ccr(sector: int, window: int = 0,
                        point: int = 0) -> tuple[int, int, int]:
    """Модель ДО фикса (литерально старая): сектор = один инвертор, ccr1==ccr8."""
    return _inv_ccr(LEGACY_INV1_Q15, sector, window, point, False)


def _write_legacy_logs(root: Path) -> None:
    """Grid-логи в СТАРОМ одноинверторном базисе (ccr1 == ccr8)."""
    for region in range(12):
        sector, window = region // 2, region % 2
        for point in range(4):
            ccr = legacy_expected_ccr(sector, window, point)
            ccr_s = ",".join(str(c) for c in ccr)
            lines = [
                f"@MC:REC:cap={100 + region}:seq={seq}:raw_i1={2000 + i1}"
                f":raw_i2={2000 + i2}:raw_ct=0:raw_vbus=600:i1={i1}:i2={i2}"
                f":vbus=60000:ccr1={ccr_s}:ccr8={ccr_s}:arr=999:trig={TRIG:#x}"
                f":status=7:fault=0"
                for seq, i1, i2 in ((k, 300 + k, 400 + k) for k in range(1, 9))
            ]
            lines.append("@MC:DRAIN:records=8")
            (root / f"region_{region}_{point}.log").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")


# ── Гейт: зелёный на физическом базисе, красный на старом ───────────────


def test_gate_green_on_physical_basis():
    """§2.1: модель с физической таблицей воспроизводит ВСЕ 48 пар кампании."""
    assert physical_table_matches_campaign(msi.BOAR_MOD_Q15)
    assert physical_table_matches_campaign(PHYSICAL_Q15)
    assert msi.BOAR_MOD_Q15 == PHYSICAL_Q15


def test_gate_red_on_legacy_single_inverter_basis():
    """§2.4: старая одноинверторная таблица обязана тест ПРОВАЛИТЬ."""
    assert not physical_table_matches_campaign(LEGACY_INV1_Q15)
    # предметная причина: запись несёт ДВА паттерна (ccr1 != ccr8), а старая
    # модель знает только inv1 и пишет один паттерн в оба поля.
    ccr1, ccr8 = CAMPAIGN_CCR_PAIRS[0][0]
    assert ccr1 != ccr8
    assert legacy_expected_ccr(0, 0, 0) == ccr1     # inv1 старая модель знает
    assert legacy_expected_ccr(0, 0, 0) != ccr8     # inv8 — нет
    assert legacy_expected_ccr(0, 0, 0) == (625, 500, 375)


def test_module_model_reproduces_all_48_campaign_records():
    """§2.1: для каждого сектора/окна/точки модельный вектор ingest равен
    (inv1 − inv8), посчитанному ИЗ ЗАПИСЕЙ кампании."""
    seen = 0
    for region, points in CAMPAIGN_CCR_PAIRS.items():
        sector, window = region // 2, region % 2
        for point, (ccr1, ccr8) in points.items():
            diff = tuple(ccr1[i] - ccr8[i] for i in range(3))
            assert msi.expected_inv1_ccr(sector, window, point) == ccr1
            assert msi.expected_inv8_ccr(sector, window, point) == ccr8
            assert msi.expected_ccr(sector, window, point) == diff
            # тестовая модель == модульная (гейт проверяет тот же алгоритм)
            assert _pair_for(PHYSICAL_Q15, sector, window, point) == (ccr1, ccr8)
            seen += 1
    assert seen == 48


def test_sector0_physical_direction_is_60_not_30():
    """§2.2: направление физического вектора сектора 0 — 60°, не 30°."""
    assert _angle_deg(msi.BOAR_MOD_Q15[0]) == pytest.approx(60.0, abs=1e-9)
    assert _angle_deg(LEGACY_INV1_Q15[0]) == pytest.approx(30.0, abs=1e-9)
    # поворот систематический: ровно +30° во всех шести секторах
    for sector in range(6):
        delta = (_angle_deg(msi.BOAR_MOD_Q15[sector])
                 - _angle_deg(LEGACY_INV1_Q15[sector]))
        assert delta == pytest.approx(30.0, abs=1e-9)


def test_scale_relation_is_sqrt3():
    """Физический вектор = inv1 − inv8 = √3 × |inv1| (inv8 = inv1−120°)."""
    for sector in range(6):
        ratio = (_clarke_mag(msi.BOAR_MOD_Q15[sector])
                 / _clarke_mag(LEGACY_INV1_Q15[sector]))
        assert ratio == pytest.approx(math.sqrt(3.0), rel=1e-12)


def test_sector_angle_binding_matches_measured_table():
    """§2.3: привязка sector ID -> угол совпадает с измеренной таблицей
    (немонотонная — обязана сохраниться, иначе починим одни вектора и
    сломаем другие)."""
    assert [SECTOR_ANGLE_DEG[s] for s in range(6)] == [60.0, 0.0, 120.0,
                                                      180.0, -60.0, -120.0]
    assert msi.BOAR_SECTOR_ANGLE_DEG == SECTOR_ANGLE_DEG
    for region in (0, 2, 4, 6, 8, 10):       # окно 0, точка 0 — центры
        sector = region // 2
        ccr1, ccr8 = CAMPAIGN_CCR_PAIRS[region][0]
        phys = _q15_from_diff_ccr(tuple(ccr1[i] - ccr8[i] for i in range(3)))
        assert msi.BOAR_MOD_Q15[sector] == phys
        assert _angle_deg(phys) == pytest.approx(SECTOR_ANGLE_DEG[sector],
                                                 abs=1e-9)


def test_inv8_table_derived_and_zero_sum():
    """inv8 не отдельная константа: выведен как inv1 − физический; все
    вектора — трёхфазные наборы с нулевой суммой."""
    for sector in range(6):
        expected = tuple(LEGACY_INV1_Q15[sector][i] - PHYSICAL_Q15[sector][i]
                         for i in range(3))
        assert msi.BOAR_MOD_INV8_Q15[sector] == expected
        assert msi.BOAR_MOD_INV8_Q15[sector] != (0, 0, 0)
        assert sum(msi.BOAR_MOD_INV8_Q15[sector]) == 0
        assert sum(msi.BOAR_MOD_Q15[sector]) == 0
        assert sum(msi.BOAR_MOD_INV1_Q15[sector]) == 0


def test_reingest_of_legacy_basis_logs_is_rejected(tmp_path):
    """Старый базис ingest не проходит: лог, где ccr1 == ccr8 (так выглядела
    бы запись без второго инвертора), отклоняется fail-closed."""
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_legacy_logs(logs)
    with pytest.raises(ValueError, match="ccr"):
        msi.build_campaign(logs, ".", tmp_path / "out", scope_waiver=True)
