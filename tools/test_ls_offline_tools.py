from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET = ROOT / "tools" / "ls_observability_budget.py"
ANALYZER = ROOT / "tools" / "autotune_idle_analyzer.py"


def run_tool(tool: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(tool), *args], cwd=ROOT,
                          text=True, capture_output=True, check=False)


def idle_log(tmp_path: Path, values: dict[int, list[int]], pairs: list[tuple[str, int]] | None = None) -> Path:
    path = tmp_path / "idle.log"
    lines = []
    for duty, ls_values in values.items():
        for rep, ls in enumerate(ls_values, 1):
            lines.append(f"@IDLE:PROG={duty}/50:D={duty}:I={duty * 100}:L={ls}:REP={rep}/{len(ls_values)}")
    if pairs:
        for name, ls in pairs:
            lines.append(f"@AT:PAIR:{name}:Rs=13000:Ls={ls}:Isat=0:V=1")
        lines.append(f"@AT:PAIRS:OK:Rs=13000:Ls={sum(x[1] for x in pairs)//len(pairs)}:ASYM=1%:VALID=3")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def test_budget_is_deterministic_and_reports_non_discriminable(tmp_path: Path):
    log = tmp_path / "ls.log"
    log.write_text("\n".join(f"@AT:LS:FRAME:i1={100 + i}:i2={200 - i}:vstep_mv=100:tstep_us=10" for i in range(8)) + "\n", encoding="utf-8")
    out1, out2 = tmp_path / "a.json", tmp_path / "b.json"
    args = ["--log", str(log), "--json", str(out1), "--mc-trials", "200", "--seed", "7"]
    first = run_tool(BUDGET, args)
    assert first.returncode == 0, first.stderr
    args[3] = str(out2)
    second = run_tool(BUDGET, args)
    assert second.returncode == 0, second.stderr
    assert out1.read_bytes() == out2.read_bytes()
    report = json.loads(out1.read_text(encoding="utf-8"))
    assert report["verdict"] == "NOT_DISCRIMINABLE_WITH_THIS_NOISE"
    assert report["noise"]["one_lsb_ma"] > 12.0
    assert "required_u_times_t_mv_us" in report["budget"]
    assert report["budget"]["monte_carlo"]["valid_estimates"] > 0


def test_budget_incomplete_input_is_machine_readable(tmp_path: Path):
    log = tmp_path / "empty.log"
    log.write_text("not an LS frame\n", encoding="utf-8")
    out = tmp_path / "failure.json"
    result = run_tool(BUDGET, ["--log", str(log), "--json", str(out)])
    assert result.returncode == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "INSUFFICIENT_INPUT"


def test_idle_and_pairs_pass_and_preserve_curve(tmp_path: Path):
    log = idle_log(tmp_path, {5: [1000] * 5, 10: [900] * 5, 15: [800] * 5},
                   [("AB", 1000), ("BC", 990), ("CA", 1010)])
    out = tmp_path / "report.json"
    result = run_tool(ANALYZER, ["--log", str(log), "--json", str(out)])
    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert len(report["curve_ls_i"]) == 3
    assert report["pairs"]["valid"] == 3
    assert any(f["code"] == "ISAT_UNREACHED" for f in report["flags"])


def test_negative_controls_all_fire(tmp_path: Path):
    rising = idle_log(tmp_path, {5: [100] * 5, 10: [200] * 5})
    out = tmp_path / "rising.json"
    result = run_tool(ANALYZER, ["--log", str(rising), "--json", str(out)])
    assert result.returncode == 1
    flags = {f["code"] for f in json.loads(out.read_text())["flags"]}
    assert "LS_RISES_WITH_I" in flags

    spread = idle_log(tmp_path, {5: [100, 100, 100, 100, 120], 10: [80] * 5})
    out = tmp_path / "spread.json"
    result = run_tool(ANALYZER, ["--log", str(spread), "--json", str(out)])
    assert result.returncode == 1
    assert "SPREAD_HIGH" in {f["code"] for f in json.loads(out.read_text())["flags"]}

    asym = idle_log(tmp_path, {5: [1000] * 5, 10: [900] * 5}, [("AB", 1000), ("BC", 1000), ("CA", 1200)])
    out = tmp_path / "asym.json"
    result = run_tool(ANALYZER, ["--log", str(asym), "--json", str(out)])
    assert result.returncode == 1
    assert "ASYM_HIGH" in {f["code"] for f in json.loads(out.read_text())["flags"]}

    zero = idle_log(tmp_path, {5: [0] * 5, 10: [0] * 5})
    out = tmp_path / "zero.json"
    result = run_tool(ANALYZER, ["--log", str(zero), "--json", str(out)])
    assert result.returncode == 0
    report = json.loads(out.read_text())
    zero_flag = next(f for f in report["flags"] if f["code"] == "LS_ZERO")
    assert len(zero_flag["possible_causes"]) == 3
    assert any(f["code"] == "ISAT_UNREACHED" for f in report["flags"])


def test_truncated_log_is_rc2_without_verdict(tmp_path: Path):
    log = tmp_path / "truncated.log"
    log.write_text("@IDLE:PROG=5/50:D=5:I=500:L=1000:REP=1/5\n", encoding="utf-8")
    out = tmp_path / "report.json"
    result = run_tool(ANALYZER, ["--log", str(log), "--json", str(out)])
    assert result.returncode == 2
    report = json.loads(out.read_text())
    assert report["verdict"] is None
    assert any(x["code"] == "INCOMPLETE_REPETITIONS" for x in report["rejected"])


def test_sensitivity_mutation_cannot_make_spread_pass(tmp_path: Path):
    log = idle_log(tmp_path, {5: [100, 100, 100, 100, 120], 10: [80] * 5})
    mutated = tmp_path / "mutated.py"
    source = ANALYZER.read_text(encoding="utf-8")
    mutated.write_text(source.replace("p[\"spread_pct\"] >= 15.0", "p[\"spread_pct\"] >= 1000.0"), encoding="utf-8")
    out = tmp_path / "mutated.json"
    result = run_tool(mutated, ["--log", str(log), "--json", str(out)])
    assert result.returncode == 0
    assert "SPREAD_HIGH" not in {f["code"] for f in json.loads(out.read_text())["flags"]}
    # The regression is sensitive: with the production threshold the same fixture rejects.
    production = run_tool(ANALYZER, ["--log", str(log), "--json", str(tmp_path / "production.json")])
    assert production.returncode == 1
