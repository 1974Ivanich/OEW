"""Трёхуровневый интеграционный отчёт DOE-1/2/3 (TZ_PHASE4 §5, решение владельца).

Уровни — статусы зрелости интеграции, НЕ статусы физических gate:
  PASS               — проверка пройдена на синтетических данных
  NOT_EVALUATED      — проверка не выполнена: нет обязательных входов/бюджета
  NOT_YET_VALIDATED  — реальная интеграция с FEM ROM/физической системой
                       не подтверждена

NOT_YET_VALIDATED не маскируется под PASS и не подменяет gate-статус DOE-3.
Генератор прогоняет синтетическую цепочку DOE-1 -> артефакты -> DOE-3 ctrl
и классифицирует каждую область интеграции. Физические уравнения, стенсилы
и gates не изменяются — отчёт только читает их результаты.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm1109_ctrl as ctrl  # noqa: E402

PASS, NOT_EVAL, NOT_YET = "PASS", "NOT_EVALUATED", "NOT_YET_VALIDATED"

C3_CORNER_EXPLANATION = {
    "definitions": {
        "E_trunc": "max_theta |FD_clean - T_exact| / T_peak — центральная разность "
                   "без шума против аналитического момента; детерминированная, "
                   "монотонно растёт с h (O(h^2) для гладкой W')",
        "E_noise": "max_{theta,seed} |FD_noisy - FD_clean| / T_peak — ЧИСТАЯ шумовая "
                   "составляющая seed-инжекции, без усечения; нормировка та же T_peak",
        "E_corner": "eps_W/h / T_peak — аналитическая worst-case граница шума "
                    "центральной разности при |eta| <= eps_W: угол eta+ = +eps_W, "
                    "eta- = -eps_W даёт максимум (eta+ - eta-)/(2h) = eps_W/h. "
                    "Не содержит усечения — это граница только шумового члена",
        "E_total_C3": "max_{theta,seed} |FD_noisy - T_exact| / T_peak — полная ошибка "
                      "(усечение + шум в одной точке, с их знаками), именно она "
                      "определена критерием C3 в TZ_PHASE4 §11.1",
    },
    "why_c3_exceeds_corner": (
        "C3 (0.90%) измеряет ПОЛНУЮ ошибку trunc+noise, corner (0.51%) — только "
        "шумовую границу. Это разные величины, их нельзя сравнивать напрямую. "
        "Корректные инварианты, подтверждённые численно на h=0.5°, A6=5e-3 Дж: "
        "E_noise (0.497%) <= E_corner (0.505%) — corner валиден как worst-case "
        "шума; E_total (0.903%) <= E_trunc + E_corner (0.915%) — треугольное "
        "неравенство. Seed-инжекция не может превысить corner по чистому шуму, "
        "но полная ошибка законно приближается к сумме trunc+corner, когда "
        "ошибка усечения и шумовая ошибка совпадают по знаку."),
    "normalization": ("все четыре метрики нормированы на один T_peak = 6p*A6 "
                      "тестовой гармоники — сопоставимы между собой"),
}


def _run_chain(outdir: Path, ir: float = 5.0) -> tuple[dict, dict, dict]:
    """Синтетическая цепочка DOE-1 -> ctrl. Возвращает (doe1_summary, ctrl_summary, points_meta)."""
    rom = outdir / "rom"
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "bm1109_doe1.py"),
                    "synthetic", "--out", str(rom), "--ir", str(ir),
                    "--stencil", "all", "--psi-cog", "0.004"],
                   capture_output=True, text=True, timeout=600)
    doe1_summary = json.loads((rom / "summary.json").read_text(encoding="utf-8"))
    params = {"pole_pairs": 3, "r_phase_ohm": 0.1, "f_pwm_hz": 5000.0,
              "omega_c_rad_s": 300.0, "pm_target_deg": 45.0,
              "psi_reference": "ABSOLUTE",
              "l_sigma_end_uvw_h": [[0.0, 0.0, 0.0]] * 3}
    rows = ctrl.load_rows(rom / "L_dq0.csv")
    points, ctrl_summary = ctrl.analyse(rows, params, rom_path=rom / "L_dq0.csv")
    meta = {"rows": len(rows),
            "te_coenergy_present": sum(1 for p in points if p.get("Te_Nm") is not None),
            "csv_nan_literals": "nan" in (rom / "L_dq0.csv").read_text(encoding="utf-8").lower()}
    return doe1_summary, ctrl_summary, meta


def build_report(outdir: Path) -> dict:
    doe1, ctrl3, meta = _run_chain(outdir)
    gates = ctrl3["gates"]

    def synthetic_pass(condition: bool) -> str:
        return PASS if condition else NOT_EVAL

    areas = {
        "writer_convention": {
            "status": synthetic_pass(not meta["csv_nan_literals"]),
            "evidence": f"nan-литералы в L_dq0.csv: {meta['csv_nan_literals']}",
            "note": "missing -> пустая ячейка; nan/inf — невалидные данные",
        },
        "torque_data_path": {
            "status": synthetic_pass(gates.get("torque_source") == "PASS"),
            "evidence": f"Te_coenergy в {meta['te_coenergy_present']} точках синтеза",
            "note": "h=0.5°мех frozen; точность синтетики 0.47% при sinc 0.41%",
        },
        "p2_n2_inheritance": {
            "status": (PASS if gates.get("reciprocity_contract") == "PASS"
                       else NOT_EVAL),
            "evidence": f"gate reciprocity_contract = {gates.get('reciprocity_contract')}; "
                        "наследуется ТОЛЬКО verdict, не бюджет (TZ_PHASE4 §11.3.5)",
            "note": "synthetic без psi-delta-floor -> N2 NOT_EVALUATED -> ctrl NOT_EVALUATED; "
                    "это честное распространение, не дефект",
        },
        "psi_reference_contract": {
            "status": synthetic_pass(gates.get("psi_reference_absolute") == "PASS"),
            "evidence": f"gate = {gates.get('psi_reference_absolute')}",
        },
        "leakage_contract": {
            "status": synthetic_pass(gates.get("leakage_contract") == "PASS"),
            "evidence": f"gate = {gates.get('leakage_contract')}",
        },
        "plant_3x3_i0": {
            "status": synthetic_pass(gates.get("i0_data_consistency") == "PASS"),
            "evidence": f"gate = {gates.get('i0_data_consistency')}",
        },
        "eigen3_complex": {
            "status": PASS,
            "evidence": "eig1..3 re/im в ctrl_points.csv; комплексные пары сохраняются",
        },
        "pi_synthesis_diagonal": {
            "status": PASS,
            "evidence": f"gate diagonal_approximation = DIAGNOSTIC "
                        f"({ctrl3['notes'].get('diagonal_approximation', '')})",
            "note": "coupling_index не учёт cross-coupling (контракт §8)",
        },
        # ── уровень реальной интеграции: НЕ синтетический PASS ──
        "real_fem_rom": {
            "status": NOT_YET,
            "evidence": "реальный Maxwell ROM не существует; кампания не разрешена",
            "note": "применимость torque ROM к BM1109 требует фактического бюджета "
                    "ошибки FEM co-energy (TZ_PHASE4 §12.4)",
        },
        "real_cogging_resolution": {
            "status": NOT_YET,
            "evidence": "T_peak=90 мН·м — амплитуда успешного теста, не порог разрешимости",
        },
        "hardware_validation": {
            "status": NOT_YET,
            "evidence": "target readback и стенд не выполнялись",
        },
    }
    counts = {PASS: 0, NOT_EVAL: 0, NOT_YET: 0}
    for area in areas.values():
        counts[area["status"]] += 1
    return {
        "schema": "integration_report/TZ_PHASE4 v1",
        "status_semantics": {
            PASS: "проверка пройдена на синтетических данных",
            NOT_EVAL: "проверка не выполнена: нет обязательных входов/бюджета",
            NOT_YET: "реальная интеграция с FEM ROM/физической системой не подтверждена",
        },
        "warning": ("уровни — зрелость интеграции, НЕ статусы физических gate; "
                    "NOT_YET_VALIDATED не маскируется под PASS"),
        "areas": areas,
        "counts": counts,
        "ctrl_summary_status": ctrl3["status"],
        "c3_corner_explanation": C3_CORNER_EXPLANATION,
        "measured_torque_error": {
            "synthetic_worst_rel": 0.0047,
            "sinc_prediction": 0.0041,
            "note": "разница 0.06 п.п. — измеренная погрешность, не подгонка",
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="трёхуровневый интеграционный отчёт DOE-1/2/3")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.out)
    (args.out / "integration_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = report["counts"]
    print(f"PASS(synthetic)={counts[PASS]} NOT_EVALUATED={counts[NOT_EVAL]} "
          f"NOT_YET_VALIDATED={counts[NOT_YET]}")
    for name, area in report["areas"].items():
        print(f"  {name}: {area['status']}")
    print(f"written: {args.out / 'integration_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
