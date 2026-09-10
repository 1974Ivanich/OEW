"""Regression for tools/geometry_partition_audit.py.

Фиксирует три числа, на которых стоит решение «вариант A или B» в
TZ_MAP_IDENTITY_QUALIFICATION_CONTRACT.md:

  * предикаты секторов покрывают 269.9° (74.97 %) окружности, dead zones 90.2°;
  * прямоугольник geometry-режима отвергает собственный центр вектора кампании
    (8192, 0, -8192), потому что требует среднюю фазу <= -1;
  * grid-точка профиля (+4/-4 CCR) внутрь прямоугольника попадает.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import geometry_partition_audit as gpa  # noqa: E402

ADJUSTED = (7500, 9000, 9000, 9600)
TZ = (6000, 10000, 10000, 14000)


def _covered_degrees() -> float:
    hits = 0
    for i in range(7200):
        th = i * 0.05 - 180.0
        u, v, w = gpa.mod_triple(th, 1.0)
        if any(f(u, v, w) for f in gpa.SECTOR_PREDICATE.values()):
            hits += 1
    return hits * 0.05


def test_predicate_coverage_is_about_75_percent():
    covered = _covered_degrees()
    assert abs(covered - 269.9) < 0.2, covered
    assert abs(100.0 * covered / 360.0 - 75.0) < 0.1


def test_dead_zones_are_30_to_90_and_180_to_210():
    dead = []
    for i in range(7200):
        th = i * 0.05 - 180.0
        u, v, w = gpa.mod_triple(th, 1.0)
        if not any(f(u, v, w) for f in gpa.SECTOR_PREDICATE.values()):
            dead.append(round(th, 2))
    inside_dead = [t for t in dead if 30.0 < t < 90.0]
    assert abs(len(inside_dead) * 0.05 - 59.9) < 0.2
    assert all(-180.0 <= t <= -150.0 for t in dead if t < -100.0)


def test_dead_zone_has_two_positive_phases():
    u, v, w = gpa.mod_triple(60.0, 1.0)
    assert u > 0 and v > 0 and w < 0  # ни один предикат не требует «двух плюсов»


def test_geometry_rectangle_rejects_campaign_center_vector():
    for zones in (ADJUSTED, TZ):
        b = gpa.sector_rect(0, 0, zones)
        assert not gpa.inside(b, (8192, 0, -8192))
        assert not gpa.inside(b, (8192, -8192, 0))


def test_geometry_rectangle_accepts_only_skewed_grid_point():
    b = gpa.sector_rect(0, 0, ADJUSTED)
    assert gpa.inside(b, (8454, -262, -8192))     # +4/-4 CCR, средняя фаза < 0
    assert not gpa.inside(b, (8192, 262, -8454))  # средняя фаза > 0
    assert not gpa.inside(b, (7930, 0, -8454))    # средняя фаза == 0


def test_all_twelve_rows_have_cells_outside_the_rectangle():
    dataset = _ROOT / "boar_geometry_dataset.txt"
    if not dataset.exists():
        import pytest
        pytest.skip("boar_geometry_dataset.txt отсутствует")
    rows = gpa.load_rows(dataset)
    assert len(rows) == 12
    for zones in (ADJUSTED, TZ):
        for key, cells in rows.items():
            b = gpa.sector_rect(key[0], key[1], zones)
            assert any(not gpa.inside(b, c) for c in cells), (zones, key)
