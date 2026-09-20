from __future__ import annotations

import json
import statistics
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


def budget_log(tmp_path: Path, blocks: list) -> Path:
    """blocks: (d, ph, [i1...], [i2...]) — кадры в формате прошивки (@AT:LS:FRAME)."""
    path = tmp_path / "ls.log"
    lines = []
    for d, ph, i1s, i2s in blocks:
        for n, (a, b) in enumerate(zip(i1s, i2s), 1):
            lines.append(f"@AT:LS:FRAME:d={d}:n={n}:ph={ph}:t={n * 1000}:I1={a}:I2={b}:"
                         f"Idiff={a - b}:Vbus=30000:CCR1=500:CCR8=500")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def test_budget_is_deterministic_and_reports_non_discriminable(tmp_path: Path):
    log = budget_log(tmp_path, [(5, 0, [100, 140, 60, 100], [100, 60, 140, 100])])
    out1, out2 = tmp_path / "a.json", tmp_path / "b.json"
    args = ["--log", str(log), "--json", str(out1), "--mc-trials", "200", "--seed", "7",
            "--vstep-mv", "100", "--tstep-us", "10"]
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
    # Нулевой Ls — это ОТСУТСТВИЕ измерения: PASS был бы приёмкой ничего.
    assert result.returncode == 1
    report = json.loads(out.read_text())
    assert report["verdict"] == "REJECT"
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


def test_noise_is_within_block_not_pooled_over_levels(tmp_path: Path):
    """Дефект интеграции: шум мерился по всему логу, хотя уровни d дают СИГНАЛЬНЫЙ разброс.

    Два уровня с одинаковым малым внутриблочным шумом и разницей средних 800 мА:
    правильный ответ — шум ~3 мА (порог не раздут), pooled-оценка дала бы >300 мА.
    """
    log = budget_log(tmp_path, [(5, 0, [100, 101, 99, 100], [100, 99, 101, 100]),
                                (10, 0, [900, 901, 899, 900], [900, 899, 901, 900])])
    out = tmp_path / "blocked.json"
    result = run_tool(BUDGET, ["--log", str(log), "--json", str(out), "--vstep-mv", "3036",
                               "--tstep-us", "200", "--mc-trials", "200"])
    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["noise"]["source_kind"] == "pooled_within_block_ph0"
    assert report["noise"]["reliable"] is True
    assert report["noise"]["effective_sd_di_ma"] < 5.0
    pooled = statistics.stdev([100, 101, 99, 100, 900, 901, 899, 900])
    assert pooled > 300.0                      # ровно то, что давал прежний (неверный) способ


def test_whole_log_noise_refused_without_explicit_sd(tmp_path: Path):
    """Шум без блоков ph=0 запрещён; с явным --sd-ma прогон разрешён и это видно в отчёте."""
    log = tmp_path / "nogroups.log"
    log.write_text("\n".join(f"@AT:LS:FRAME:i1={100 + i}:i2={200 - i}" for i in range(8)) + "\n",
                   encoding="utf-8", newline="\n")
    out = tmp_path / "refused.json"
    result = run_tool(BUDGET, ["--log", str(log), "--json", str(out),
                               "--vstep-mv", "100", "--tstep-us", "10"])
    assert result.returncode == 2
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "INSUFFICIENT_INPUT"

    out2 = tmp_path / "explicit.json"
    result2 = run_tool(BUDGET, ["--log", str(log), "--json", str(out2), "--vstep-mv", "100",
                                "--tstep-us", "10", "--sd-ma", "36.0", "--mc-trials", "200"])
    assert result2.returncode == 0, result2.stderr
    report = json.loads(out2.read_text(encoding="utf-8"))
    assert report["noise"]["source_kind"] == "explicit_sd_override"
    assert report["noise"]["effective_sd_di_ma"] == 36.0


def test_mc_reports_nonpositive_fraction_and_reliability(tmp_path: Path):
    """Monte-Carlo не имеет права молча отбрасывать неположительные оценки."""
    log = budget_log(tmp_path, [(5, 0, [100, 140, 60, 100], [100, 60, 140, 100])])
    out = tmp_path / "mc.json"
    result = run_tool(BUDGET, ["--log", str(log), "--json", str(out), "--vstep-mv", "1",
                               "--tstep-us", "1", "--mc-trials", "400"])
    assert result.returncode == 0, result.stderr
    mc = json.loads(out.read_text(encoding="utf-8"))["budget"]["monte_carlo"]
    assert mc["non_positive_fraction"] > 0.05
    assert mc["reliable"] is False


def test_quantization_floor_is_named_not_hidden(tmp_path: Path):
    """Если цель недостижима из-за пола квантования, required_sd обязан быть null с пометкой."""
    log = budget_log(tmp_path, [(5, 0, [100, 101, 99, 100], [100, 99, 101, 100])])
    out = tmp_path / "quant.json"
    result = run_tool(BUDGET, ["--log", str(log), "--json", str(out), "--vstep-mv", "1",
                               "--tstep-us", "1", "--ls-max-uh", "500000", "--mc-trials", "100"])
    assert result.returncode == 0, result.stderr
    budget = json.loads(out.read_text(encoding="utf-8"))["budget"]
    assert budget["quantization_limited"] is True
    assert budget["required_sd_ma_at_given_u_times_t"] is None
