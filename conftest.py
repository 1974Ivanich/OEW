"""Pytest bootstrap: make the repository root importable.

Tests import package modules as ``tools.*`` (e.g. ``tools.test3_g0_gate``).
When pytest is invoked as a plain ``pytest`` command (as in CI), the repo
root is not on ``sys.path``, so those imports fail.  Inserting the root here
keeps the import contract identical between local runs and CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_CLASSES = {
    "physics": "A — физическое тождество (гейт физики модели)",
    "numerics": "B — проверка численного метода",
    "implementation": "C — согласованность реализации",
    "integration": "D — интеграционная/траекторная проверка",
    "diagnostic": "E — только диагностика, не гейт",
}


def pytest_configure(config):
    for name, description in TEST_CLASSES.items():
        config.addinivalue_line("markers", f"{name}: {description}")
