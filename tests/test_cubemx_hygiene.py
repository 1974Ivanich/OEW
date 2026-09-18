"""Hygiene-тесты механизма cubemx_check.py: проверка не должна менять tracked-файлы.

Контекст: `scripts/cubemx_check.py` писал генерируемый скрипт headless-прогона в
`scripts/cubemx_check_script.txt` — файл ПОД git. После каждой проверки (в т.ч. из pre-push hook)
рабочее дерево становилось грязным, причём с абсолютными путями того ПК, где шла проверка; при
`git add -A` локальные пути уехали бы в репозиторий.

Правило пакета: любой generated-артефакт проверки пишется только в untracked/ignored путь
(`logs/`). Тесты проверяют сам механизм, а не только текущее имя файла: если завтра артефакт
переименуют или добавят второй, инвариант «ничего tracked не изменяется» обязан сохраниться.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "cubemx_check.py"
HOOK = ROOT / "scripts" / "hooks" / "pre-push"
OLD_TRACKED = ROOT / "scripts" / "cubemx_check_script.txt"
CUBEMX_EXE = Path(r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\STM32CubeMX.exe")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def load_module():
    spec = importlib.util.spec_from_file_location("cubemx_check_under_test", CHECK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def is_ignored_or_untracked(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    tracked = git("ls-files", "--error-unmatch", rel)
    if tracked.returncode == 0:
        return False                      # файл под git — нарушение
    return git("check-ignore", "-q", rel).returncode == 0


def write_targets(tree: ast.AST) -> list[str]:
    """Строковые литералы-пути, которые модуль пытается писать."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(ch in mode for ch in "wax"):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                            and ("/" in sub.value or "\\" in sub.value) and len(sub.value) > 3:
                        found.append(sub.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("makedirs", "mkdir"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and ("/" in sub.value or "\\" in sub.value):
                    found.append(sub.value)
    return found


def test_generated_script_path_is_untracked_and_ignored() -> None:
    module = load_module()
    path = Path(module.script_path())
    assert path.parent.name == "logs", f"generated-скрипт должен лежать в logs/, а не {path}"
    assert is_ignored_or_untracked(path), f"{path} обязан быть untracked и в .gitignore"


def test_writing_generated_script_keeps_tree_clean() -> None:
    """Запись артефакта проверки не должна менять состояние дерева (before == after)."""
    module = load_module()
    path = Path(module.script_path())
    existed = path.exists()
    backup = path.read_bytes() if existed else None
    before = git("status", "--porcelain").stdout
    try:
        path.write_text("config load X\r\ncsv pinout Y\r\nexit\r\n", encoding="utf-8", newline="")
        after = git("status", "--porcelain").stdout
        assert after == before, f"после записи артефакта состояние дерева изменилось:\n{after}"
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)


def test_no_generated_artifact_in_tracked_paths() -> None:
    source = CHECK.read_text(encoding="utf-8")
    assert "scripts\", \"cubemx_check_script.txt" not in source, \
        "старый tracked путь не должен вернуться в код"
    tree = ast.parse(source)
    for literal in write_targets(tree):
        candidate = Path(literal)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.suffix == "":
            continue
        assert is_ignored_or_untracked(candidate), \
            f"модуль пишет в tracked-путь {literal} — проверка не должна менять репозиторий"
    assert "script_path()" in source, "модуль обязан получать путь генерации через script_path()"


def test_old_tracked_file_is_removed_from_index() -> None:
    assert git("ls-files", "--error-unmatch", "scripts/cubemx_check_script.txt").returncode != 0, \
        "generated-скрипт обязан быть убран из индекса"


def test_hook_contract_intact() -> None:
    hook = HOOK.read_text(encoding="utf-8", errors="replace")
    assert "cubemx_check.py" in hook
    for rc in ("-eq 1", "-eq 2"):
        assert rc in hook, "контракт кодов возврата hook (0/1/2) обязан сохраниться"


@pytest.mark.skipif(not CUBEMX_EXE.exists(), reason="CubeMX не установлен на этом ПК")
def test_real_check_keeps_tree_clean() -> None:
    """Полный прогон проверки (CubeMX headless) не должен менять tracked-файлы."""
    before = git("status", "--porcelain").stdout
    proc = subprocess.run([sys.executable, str(CHECK)], cwd=ROOT, capture_output=True, text=True,
                          timeout=600)
    assert proc.returncode in (0, 1, 2), proc.stdout[-2000:] + proc.stderr[-2000:]
    after = git("status", "--porcelain").stdout
    assert after == before, f"прогон cubemx_check.py изменил дерево:\n{after}"
