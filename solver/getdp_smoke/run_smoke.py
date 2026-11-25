"""Smoke-тест инфраструктуры Gmsh + GetDP.

Проверяет ТОЛЬКО цепочку: .geo -> .msh -> GetDP -> экспорт величин -> парсер.
Никаких размеров BM1109, никакой топологии OEW-6T, никаких данных для ROM:
результаты идут в build/tools/smoke/ и не попадают в build/doe1/.

Гейты:
  S0  Gmsh строит сетку (файл .msh с ненулевым числом узлов)
  S1  GetDP принимает сетку и решает задачу (rc=0, в логе нет Error, есть .res)
  S2  решение конечно (энергия конечна и > 0); residual для прямого линейного
      решателя не определён — статус фиксируется как NOT_APPLICABLE_DIRECT_SOLVE
  S3  поле B конечно: нет NaN/Inf в b.pos
  S4  повторный прогон даёт те же интегральные величины (энергия и поток)
  S5  результаты выгружены в отдельный smoke-dataset (не в build/doe1)

Код возврата: 0 — все гейты PASS, 1 — иначе, 2 — backend не найден (BLOCKED).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent
ROOT = MODEL_DIR.parents[1]
DEFAULT_GMSH = ROOT / "build/tools/gmsh/gmsh-4.15.2-Windows64/gmsh.exe"
DEFAULT_GETDP = ROOT / "build/tools/getdp/getdp-3.5.0-Windows64/getdp.exe"
DEFAULT_OUT = ROOT / "build/tools/smoke"

MODEL_FILES = ("inductor.geo", "inductor.pro")
SOLVE_NAME = "MagSta_a"
POST_NAME = "PP"
RTOL = 1e-9


def _run(cmd: list[str], cwd: Path, log: Path) -> int:
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        handle.write("$ " + " ".join(str(item) for item in cmd) + "\n")
        handle.flush()
        result = subprocess.run([str(item) for item in cmd], cwd=str(cwd),
                                stdout=handle, stderr=subprocess.STDOUT)
    return result.returncode


def _version(exe: Path) -> str | None:
    try:
        result = subprocess.run([str(exe), "-version"], capture_output=True,
                                text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0].strip() if text else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mesh_stats(path: Path) -> dict:
    """Число узлов и элементов из заголовков .msh (формат 2.2 или 4.1)."""
    stats = {"nodes": 0, "elements": 0, "version": None}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "$MeshFormat" and index + 1 < len(lines):
            stats["version"] = lines[index + 1].split()[0]
        if line.strip() == "$Nodes" and index + 1 < len(lines):
            parts = lines[index + 1].split()
            stats["nodes"] = int(parts[0]) if len(parts) == 1 else int(parts[1])
        if line.strip() == "$Elements" and index + 1 < len(lines):
            parts = lines[index + 1].split()
            stats["elements"] = int(parts[0]) if len(parts) == 1 else int(parts[1])
    return stats


def _table_value(path: Path) -> float | None:
    """Последнее число последней строки таблицы (формат GetDP 'step value')."""
    if not path.is_file():
        return None
    value = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        tokens = [token for token in re.split(r"[\s,]+", line.strip()) if token]
        numbers = []
        for token in tokens:
            try:
                numbers.append(float(token))
            except ValueError:
                continue
        if numbers:
            value = numbers[-1]
    return value


def _field_max(path: Path) -> tuple[float | None, int]:
    """max |B| и число нефинитных значений из .pos (View 'b')."""
    if not path.is_file():
        return None, 0
    text = path.read_text(encoding="utf-8", errors="replace")
    values: list[float] = []
    for block in re.findall(r"\{([^{}]*)\}", text):
        for token in block.split(","):
            try:
                values.append(float(token))
            except ValueError:
                continue
    bad = sum(1 for value in values if not math.isfinite(value))
    magnitudes = []
    for index in range(0, len(values) - 2, 3):
        triple = values[index:index + 3]
        if len(triple) == 3:
            magnitudes.append(math.sqrt(sum(value * value for value in triple)))
    return (max(magnitudes) if magnitudes else None), bad


def _relative(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1e-30)
    return abs(a - b) / scale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--runs", type=int, default=2,
                        help="число повторных прогонов для S4 (>=2)")
    parser.add_argument("--gmsh", type=Path, default=DEFAULT_GMSH)
    parser.add_argument("--getdp", type=Path, default=DEFAULT_GETDP)
    args = parser.parse_args(argv)

    out_dir = args.out.resolve()
    # S5: smoke-результаты не должны попадать в FEM dataset BM1109.
    forbidden = (ROOT / "build/doe1").resolve()
    if out_dir == forbidden or forbidden in out_dir.parents:
        print(f"REFUSED: {out_dir} внутри build/doe1 — smoke-данные туда не пишутся")
        return 1

    gmsh_version = _version(args.gmsh)
    getdp_version = _version(args.getdp)
    if not gmsh_version or not getdp_version:
        print(f"BLOCKED: backend не найден (gmsh={args.gmsh}, getdp={args.getdp})")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    for number in range(1, max(args.runs, 1) + 1):
        run_dir = out_dir / f"run{number}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        for name in MODEL_FILES:
            shutil.copy2(MODEL_DIR / name, run_dir / name)

        mesh = run_dir / "inductor.msh"
        gmsh_rc = _run([args.gmsh, "inductor.geo", "-2", "-o", mesh.name],
                       run_dir, out_dir / f"gmsh_run{number}.log")
        stats = _mesh_stats(mesh) if mesh.is_file() else {"nodes": 0,
                                                          "elements": 0,
                                                          "version": None}
        getdp_rc = 1
        log_text = ""
        if mesh.is_file():
            getdp_rc = _run([args.getdp, "inductor.pro", "-msh", mesh.name,
                             "-solve", SOLVE_NAME, "-pos", POST_NAME],
                            run_dir, out_dir / f"getdp_run{number}.log")
            log_path = out_dir / f"getdp_run{number}.log"
            log_text = log_path.read_text(encoding="utf-8", errors="replace")

        energy = _table_value(run_dir / "energy.txt")
        az_bottom = _table_value(run_dir / "az_p1.txt")
        az_top = _table_value(run_dir / "az_p2.txt")
        flux = (az_top - az_bottom
                if az_bottom is not None and az_top is not None else None)
        b_max, b_bad = _field_max(run_dir / "b.pos")

        runs.append({
            "run": number,
            "gmsh_rc": gmsh_rc,
            "getdp_rc": getdp_rc,
            "mesh": stats,
            "res_file": (run_dir / "inductor.res").is_file(),
            "log_errors": log_text.count("Error"),
            "energy_J_per_m": energy,
            "az_bottom_Wb_per_m": az_bottom,
            "az_top_Wb_per_m": az_top,
            "flux_Wb_per_m": flux,
            "b_max_T": b_max,
            "b_nonfinite": b_bad,
        })

    first = runs[0]
    last = runs[-1]
    d_energy = (_relative(first["energy_J_per_m"], last["energy_J_per_m"])
                if first["energy_J_per_m"] is not None
                and last["energy_J_per_m"] is not None else None)
    d_flux = (math.inf if None in (first["flux_Wb_per_m"], last["flux_Wb_per_m"])
              else _relative(first["flux_Wb_per_m"], last["flux_Wb_per_m"]))

    gates = {
        "S0_mesh_generated": {
            "status": "PASS" if first["gmsh_rc"] == 0 and first["mesh"]["nodes"] > 0
                      and first["mesh"]["elements"] > 0 else "FAIL",
            "nodes": first["mesh"]["nodes"], "elements": first["mesh"]["elements"],
            "msh_version": first["mesh"]["version"], "gmsh_rc": first["gmsh_rc"]},
        "S1_getdp_solved": {
            "status": "PASS" if first["getdp_rc"] == 0 and first["log_errors"] == 0
                      and first["res_file"] and first["energy_J_per_m"] is not None
                      else "FAIL",
            "getdp_rc": first["getdp_rc"], "log_errors": first["log_errors"],
            "res_file": first["res_file"]},
        "S2_solution_finite": {
            "status": "PASS" if first["energy_J_per_m"] is not None
                      and math.isfinite(first["energy_J_per_m"])
                      and first["energy_J_per_m"] > 0.0 else "FAIL",
            "energy_J_per_m": first["energy_J_per_m"],
            "residual": "NOT_APPLICABLE_DIRECT_SOLVE",
            "note": "прямой разреженный решатель: итерационного residual нет; "
                    "конечность и знак энергии проверены явно"},
        "S3_field_finite": {
            "status": "PASS" if first["b_max_T"] is not None
                      and first["b_nonfinite"] == 0
                      and math.isfinite(first["b_max_T"]) else "FAIL",
            "b_max_T": first["b_max_T"], "b_nonfinite": first["b_nonfinite"]},
        "S4_repeat_consistent": {
            "status": "PASS" if d_energy is not None and d_flux is not None
                      and d_energy <= RTOL and d_flux <= RTOL else "FAIL",
            "energy_rel_diff": d_energy, "flux_rel_diff": d_flux,
            "rtol": RTOL, "runs": len(runs)},
        "S5_separate_dataset": {
            "status": "PASS", "dataset": str(out_dir.relative_to(ROOT)),
            "forbidden": "build/doe1"},
    }

    statuses = {name: gate["status"] for name, gate in gates.items()}
    ok = all(status == "PASS" for status in statuses.values())
    manifest = {
        "schema": "oew6t-getdp-smoke/0.1",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "INFRASTRUCTURE ONLY: neutral geometry, not BM1109, not OEW-6T",
        "backend": {
            "gmsh": {"path": str(args.gmsh.relative_to(ROOT))
                     if args.gmsh.is_relative_to(ROOT) else str(args.gmsh),
                     "version": gmsh_version},
            "getdp": {"path": str(args.getdp.relative_to(ROOT))
                      if args.getdp.is_relative_to(ROOT) else str(args.getdp),
                      "version": getdp_version},
        },
        "model": {name: _sha256(MODEL_DIR / name) for name in MODEL_FILES},
        "physics": {
            "formulation": "2D magnetostatics, a-formulation (A_z), linear materials",
            "core_mu_r": 1000.0,
            "coil_current_density_A_per_m2": 1.0e6,
            "current_sign": "top +J, bottom -J",
            "geometry": "0.20 x 0.14 m air box, 0.08 x 0.01 m core, two 0.06 x 0.01 m coil blocks",
            "units": "2D per 1 m depth: energy J/m, flux Wb/m",
        },
        "runs": runs,
        "gates": gates,
        "status": {
            "GETDP_INFRASTRUCTURE_SMOKE": "PASS" if ok else "FAIL",
            "BM1109_FEM_POC": "BLOCKED — geometry source (G0) not satisfied",
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out_dir / "diagnostics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run", "quantity", "value", "unit"])
        for run in runs:
            writer.writerow([run["run"], "energy", run["energy_J_per_m"], "J/m"])
            writer.writerow([run["run"], "az_bottom", run["az_bottom_Wb_per_m"], "Wb/m"])
            writer.writerow([run["run"], "az_top", run["az_top_Wb_per_m"], "Wb/m"])
            writer.writerow([run["run"], "flux", run["flux_Wb_per_m"], "Wb/m"])
            writer.writerow([run["run"], "b_max", run["b_max_T"], "T"])

    for name, gate in gates.items():
        print(f"{name}: {gate['status']}")
    print(f"energy={first['energy_J_per_m']} J/m  flux={first['flux_Wb_per_m']} Wb/m  "
          f"B_max={first['b_max_T']} T")
    print(f"GETDP_INFRASTRUCTURE_SMOKE={manifest['status']['GETDP_INFRASTRUCTURE_SMOKE']}  "
          f"BM1109_FEM_POC=BLOCKED  out={out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
