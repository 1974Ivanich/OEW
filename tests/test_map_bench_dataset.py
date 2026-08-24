"""End-to-end regression for the bench campaign dataset (PR-2 layer).

Гоняет канонический формат campaign/manifest.json + samples.jsonl через
validator (tools/map_bench_dataset.py) и host-конвейер (CLI) и проверяет
всю REJECT-матрицу из tools/map_bench_dataset.md:

  * evidence-гейты (validator): adc_settled, scope_qualified, margin<=0;
  * pipeline: duplicate seq, KCL, сингулярная строка, вырожденный регион,
    перекрытие регионов (через CurrentMap_LoadMeasured в CLI).

CLI собирается один раз на сессию через standalone .mk.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import map_bench_dataset as mbd  # noqa: E402

DEMO = _TOOLS / "campaign_demo"
ARR = 5000
MID = (ARR + 1) // 2


def ccr_from_q15(q: int) -> int:
    return MID + round(q * MID / 32768)


def row_cmu(sector: int, window: int) -> int:
    return -3000 + sector * 1000 + window * 500


@pytest.fixture(scope="module")
def cli_exe(tmp_path_factory) -> Path:
    """Собирает CLI один раз; возвращает путь к бинарю."""
    exe = _TOOLS / ("map_artifact_pipeline_cli.exe")
    res = subprocess.run(
        ["make", "-f", "tools/map_artifact_writer_test.mk", "map-artifact-cli"],
        cwd=_ROOT, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr
    assert exe.is_file(), exe
    return exe


@pytest.fixture()
def workdir(tmp_path) -> Path:
    return tmp_path


def read_samples(campaign: Path) -> list[dict]:
    return [json.loads(line) for line in
            (campaign / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_campaign(dst: Path, manifest: dict, samples: list[dict]) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (dst / "samples.jsonl").write_text(
        "\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")
    return dst


def mutate_samples(mutator) -> list[dict]:
    """Копирует демо-сэмплы и применяет mutator(sample) к каждой записи."""
    out = []
    for s in read_samples(DEMO):
        s = dict(s)
        mutator(s)
        out.append(s)
    return out


def run_cli(cli: Path, dataset: Path, out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(cli), str(dataset), str(out_dir)],
        cwd=_ROOT, capture_output=True, text=True, timeout=120)


def convert_and_run(cli: Path, campaign: Path, out_dir: Path):
    dataset = out_dir / "dataset.txt"
    mbd.convert_campaign(campaign, dataset)
    return run_cli(cli, dataset, out_dir), dataset


def test_validate_demo_ok():
    manifest, samples = mbd.validate_campaign(DEMO)
    assert manifest["campaign_id"] == "oew-demo-campaign-001"
    assert len(samples) == 96


def test_happy_path_e2e(cli_exe, workdir):
    """Валидная кампания: конвертация -> CLI -> 497-байтный артефакт."""
    res, dataset = convert_and_run(cli_exe, DEMO, workdir)
    assert res.returncode == 0, res.stderr
    assert dataset.is_file()
    bin_path = workdir / "oew_map_v2.bin"
    assert bin_path.is_file()
    data = bin_path.read_bytes()
    assert len(data) == 497
    assert int.from_bytes(data[:4], "little") == 0x4F45574D  # "OEWM" LE
    assert (workdir / "oew_map_v2.json").is_file()


def test_convert_matches_cli_format(cli_exe, workdir):
    """Сконвертированный dataset.txt прогоняется CLI без правок (см. e2e)."""
    dataset = workdir / "dataset.txt"
    mbd.convert_campaign(DEMO, dataset)
    text = dataset.read_text(encoding="utf-8")
    assert "identity board=7" in text
    assert "trigger=0x4F455731" in text
    assert "startup sector=2 window=1" in text
    assert text.count("sample seq=") == 96
    assert text.count("cell mu=") == 96


@pytest.mark.parametrize("key,expected_msg", [
    ("scope_qualified", "scope_qualified"),
    ("adc_settled", "adc_settled"),
    ("margin_ticks", "margin_ticks"),
])
def test_reject_evidence_gates(key, expected_msg, workdir):
    samples = mutate_samples(lambda s: s.__setitem__(key, 0))
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    with pytest.raises(ValueError) as exc:
        mbd.validate_campaign(campaign)
    assert expected_msg in str(exc.value)


def test_reject_margin_below_region_min(cli_exe, workdir):
    """margin=1 (>0, но < region.min_margin=3) — pipeline: MAP_CERT_MARGIN_BAD."""
    samples = mutate_samples(lambda s: s.__setitem__("margin_ticks", 1))
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)  # evidence-гейты проходят
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "status 4" in res.stderr  # MAP_PIPELINE_CERT_FAILED
    assert "row 0/0" in res.stderr


def test_reject_duplicate_sequence(cli_exe, workdir):
    """Дубликат seq в строке — pipeline: MAP_ACCUM_SAMPLE_DUPLICATE."""
    samples = mutate_samples(lambda s: None)
    samples[1]["seq"] = samples[0]["seq"]  # row 0/0: seq 1 повторён
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "status 2" in res.stderr  # MAP_PIPELINE_ACCUM_FAILED
    assert "row 0/0" in res.stderr


def test_reject_kcl_violation(cli_exe, workdir):
    """ref_u+ref_v+ref_w != 0 — pipeline: MAP_ACCUM_KCL_ERROR / NOISE_TOO_HIGH."""
    samples = mutate_samples(lambda s: s.__setitem__("ref_w_ma", 0))
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "status 2" in res.stderr  # MAP_PIPELINE_ACCUM_FAILED


def test_reject_singular_row(cli_exe, workdir):
    """idc2=idc1 во всех сэмплах строки 2/0 — pipeline: MAP_SOLVER_FAILED."""
    samples = mutate_samples(lambda s: None)
    for s in samples:
        if (s["sector"], s["window"]) == (2, 0):
            s["idc2_ma"] = s["idc1_ma"]
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "status 3" in res.stderr  # MAP_PIPELINE_SOLVER_FAILED
    assert "row 2/0" in res.stderr


def test_reject_degenerate_region(cli_exe, workdir):
    """Все сэмплы строки 4/1 в одной modulation-точке — MAP_CERT_DEGENERATE."""
    samples = mutate_samples(lambda s: None)
    cmu = row_cmu(4, 1)
    for s in samples:
        if (s["sector"], s["window"]) == (4, 1):
            s["ccr1"] = ccr_from_q15(cmu)
            s["ccr2"] = ccr_from_q15(0)
            s["ccr3"] = ccr_from_q15(-cmu)
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "status 4" in res.stderr  # MAP_PIPELINE_CERT_FAILED
    assert "row 4/1" in res.stderr


def test_reject_overlapping_regions(cli_exe, workdir):
    """Строка 1/0 повторяет modulation-точки строки 0/0 — загрузчик
    отвергает перекрытие: CLI отклоняет артефакт (CurrentMap_LoadMeasured)."""
    samples = mutate_samples(lambda s: None)
    row00 = [s for s in samples if (s["sector"], s["window"]) == (0, 0)]
    for s in samples:
        if (s["sector"], s["window"]) == (1, 0):
            tpl = row00[s["seq"] - 1]
            s["ccr1"], s["ccr2"], s["ccr3"] = tpl["ccr1"], tpl["ccr2"], tpl["ccr3"]
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    mbd.validate_campaign(campaign)
    res, _ = convert_and_run(cli_exe, campaign, workdir)
    assert res.returncode == 1
    assert "CurrentMap_LoadMeasured" in res.stderr


def test_missing_row_rejected(workdir):
    samples = [s for s in read_samples(DEMO)
               if (s["sector"], s["window"]) != (5, 1)]
    campaign = write_campaign(workdir, json.loads(
        (DEMO / "manifest.json").read_text(encoding="utf-8")), samples)
    with pytest.raises(ValueError) as exc:
        mbd.validate_campaign(campaign)
    assert "строка 5/1" in str(exc.value)
