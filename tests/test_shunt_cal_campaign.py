"""Host tests for tools/shunt_cal_campaign.py (no hardware needed).

Pins down:
  * capability/readback gate: any refusal -> verdict SCOPE_CONTROL_BLOCKED,
    no records created (SET-команды V3.0.0 игнорируются, поэтому гейт
    верифицирует ручную настройку readback'ом);
  * volts model == live calibration point (code 222 @ 500mV/div, OFFSET=-100,
    1X -> +0.3300 V, tools/OWON_TAO3104A_PROTOCOL.md 2026-09-27);
  * SI parser incl. the 1MSa/s mega trap;
  * record building: I_ref = mean_mv / R_shunt (мА), advisory gain vs zero;
  * full run_campaign orchestration on fakes (RECORDED / BLOCKED / skip-all),
    crash-resilient report writing, dataset_crc32 over immutable artifacts.
"""
from __future__ import annotations

import builtins
import io
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import shunt_cal_campaign as scc  # noqa: E402

IDN = "OWON,TAO3104A,2306027,V3.0.0"


def make_head(datalen=8, ch1=None, ch2=None):
    def chan(name, over):
        c = {"NAME": name, "DISPLAY": "ON", "SCALE": "100mV", "PROBE": "1X",
             "COUPLING": "DC", "OFFSET": -100, "INVERSE": "OFF"}
        c.update(over or {})
        return c
    return {"TIMEBASE": {"SCALE": "500us"},
            "SAMPLE": {"DATALEN": datalen, "FULLSCREEN": datalen},
            "CHANNEL": [chan("CH1", ch1), chan("CH2", ch2), chan("CH3", {}), chan("CH4", {})],
            "IDN": IDN, "MODEL": "510401102"}


class FakeScope:
    def __init__(self, head=None, waves=None, idn=IDN, fail=None):
        self.head_data = head or make_head()
        self.waves = waves or {"CH1": [100, 101, 102, 103, 104, 105, 106, 107],
                               "CH2": [200, 201, 202, 203, 204, 205, 206, 207]}
        self.idn = idn
        self.fail = fail or {}

    def identify(self):
        if self.fail.get("idn"):
            raise RuntimeError("no reply")
        return self.idn

    def head(self):
        if self.fail.get("head"):
            raise RuntimeError("bulk failed")
        return self.head_data

    def waveform_codes(self, ch):
        if self.fail.get("wave_" + ch):
            raise RuntimeError("wave failed")
        return list(self.waves[ch])

    def close(self):
        pass


class FakeStm:
    """ci 0/1/2 и телеметрия по сценарию."""

    def __init__(self, ci=None, adc=None):
        self.ci_script = ci or {}
        self.adc = adc or {"i1_ma": 1000, "i2_ma": -100, "ires_ma": 0, "vbus_mv": 40012}
        self.calls = []

    def ci_window(self, ch, wait_s=3.0):
        self.calls.append(("ci", ch))
        return self.ci_script.get(ch)

    def read_adc(self, wait_s=1.5):
        self.calls.append(("adc",))
        return dict(self.adc)

    def close(self):
        pass


# ---------------------------------------------------------------- gate
def test_gate_blocks_on_idn_failure():
    g = scc.run_capability_gate(FakeScope(fail={"idn": True}), {})
    assert g["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert g["blocked_reason"] == "idn_no_reply"


def test_gate_blocks_on_wrong_instrument():
    g = scc.run_capability_gate(FakeScope(idn="RIGOL,DS1054Z,1,V1"), {})
    assert g["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert g["blocked_reason"] == "idn_unexpected_instrument"


def test_gate_blocks_on_head_failure_and_datalen():
    g = scc.run_capability_gate(FakeScope(fail={"head": True}), {})
    assert g["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert g["blocked_reason"] == "head_read_failed"
    head = make_head()
    head["SAMPLE"]["DATALEN"] = 0
    g = scc.run_capability_gate(FakeScope(head=head), {})
    assert g["blocked_reason"] == "head_datalen_zero"


def test_gate_blocks_on_head_idn_mismatch():
    head = make_head()
    head["IDN"] = "OWON,TAO3104A,0000000,V3.0.0"
    g = scc.run_capability_gate(FakeScope(head=head), {})
    assert g["blocked_reason"] == "head_idn_mismatch"


def test_gate_blocks_on_channel_absent_or_display_off():
    g = scc.run_capability_gate(FakeScope(head=make_head(ch2={"DISPLAY": "OFF"})), {})
    assert g["blocked_reason"] == "CH2_display_off"
    head = make_head()
    head["CHANNEL"] = head["CHANNEL"][:1]
    g = scc.run_capability_gate(FakeScope(head=head), {})
    assert g["blocked_reason"] == "CH2_absent_in_head"
    # allow_display_off снимает блок
    g = scc.run_capability_gate(FakeScope(head=make_head(ch2={"DISPLAY": "OFF"})),
                                {"allow_display_off": True})
    assert g["verdict"] == "PASS"


def test_gate_blocks_on_scale_probe_coupling_mismatch():
    g = scc.run_capability_gate(FakeScope(head=make_head(ch1={"SCALE": "200mV"})),
                                {"scale_ch1": "100mV", "scale_tolerance_pct": 15.0})
    assert g["blocked_reason"] == "CH1_scale_mismatch"
    # в допуске 15%: 110mV против 100mV (10%) проходит; 120mV (20%) — нет
    g = scc.run_capability_gate(FakeScope(head=make_head(ch1={"SCALE": "110mV"})),
                                {"scale_ch1": "100mV"})
    assert g["verdict"] == "PASS"
    g = scc.run_capability_gate(FakeScope(head=make_head(ch1={"PROBE": "10X"})),
                                {"probe_ch1": "1X"})
    assert g["blocked_reason"] == "CH1_probe_mismatch"
    g = scc.run_capability_gate(FakeScope(head=make_head(ch2={"COUPLING": "AC"})),
                                {"coupling_ch2": "DC"})
    assert g["blocked_reason"] == "CH2_coupling_mismatch"


def test_gate_blocks_on_waveform_problems():
    g = scc.run_capability_gate(FakeScope(waves={"CH1": [0] * 8,
                                                 "CH2": [1, 2, 3, 4, 5, 6, 7, 8]}), {})
    assert g["blocked_reason"] == "CH1_waveform_all_zero"
    g = scc.run_capability_gate(FakeScope(waves={"CH1": [1, 2, 3],
                                                 "CH2": [1, 2, 3, 4, 5, 6, 7, 8]}), {})
    assert g["blocked_reason"] == "CH1_waveform_len_mismatch"
    g = scc.run_capability_gate(FakeScope(fail={"wave_CH2": True}), {})
    assert g["blocked_reason"] == "CH2_waveform_read_failed"


def test_gate_pass_steps():
    g = scc.run_capability_gate(FakeScope(), {"scale_ch1": "100mV",
                                              "probe_ch1": "1X",
                                              "coupling_ch1": "DC",
                                              "scale_ch2": "100mV",
                                              "probe_ch2": "1X",
                                              "coupling_ch2": "DC"})
    assert g["verdict"] == "PASS"
    names = [s["step"] for s in g["steps"]]
    assert names[0] == "idn" and "head_datalen" in names
    assert names.count("CH1_waveform_probe") == 1
    assert g["steps"][-1]["step"] == "CH2_waveform_probe"


# ---------------------------------------------------------------- math/model
def test_num_si_and_mega_trap():
    assert scc.num("100mV") == pytest.approx(0.1)
    assert scc.num("500us") == pytest.approx(5e-4)
    assert scc.num("(1MSa/s)") == pytest.approx(1e6)   # M = мега, не милли
    assert scc.num("50.29267") == pytest.approx(50.29267)
    assert scc.num("") == 1.0 and scc.num(None) == 1.0


def test_codes_to_volts_live_calibration_point():
    # Живая точка прибора: code 222, OFFSET=-100, 500mV/div, 1X -> +0.3300 V
    meta = {"SCALE": "500mV", "OFFSET": -100, "PROBE": "1X"}
    assert scc.codes_to_volts([222], meta)[0] == pytest.approx(0.3300, abs=1e-9)


def test_window_stats():
    st = scc.window_stats_volts([0.0, 0.1, 0.2])
    assert st["n"] == 3
    assert st["mean_mv"] == pytest.approx(100.0)
    assert st["median_mv"] == pytest.approx(100.0)
    assert st["pp_mv"] == pytest.approx(200.0)
    assert st["rms_mv"] == pytest.approx((50000 / 3) ** 0.5)  # (0^2+100^2+200^2)/3


def test_build_record_i_ref_and_advisory_gain():
    head = make_head()  # SCALE 100mV/div: code c -> (c+100-305.5)*0.004 V
    codes1 = [222] * 8   # 0.066 V = 66 мВ
    codes2 = [223] * 8   # 0.070 V = 70 мВ
    zero = {"raw_i1_avg": 2039, "raw_i2_avg": 2068, "samples": 256}
    ci1 = {"raw_i1_avg": 2092, "samples": 256}
    ci2 = {"raw_i2_avg": 2135, "samples": 256}
    tel = {"i1_ma": 1000, "i2_ma": 1050, "ires_ma": 0, "vbus_mv": 40012}
    rec = scc.build_record("1000", 0.03, head, codes1, codes2, zero, ci1, ci2, tel)
    assert rec["scope"]["ch1"]["mean_mv"] == pytest.approx(66.0)
    assert rec["scope"]["ch1"]["i_ref_ma"] == pytest.approx(66.0 / 0.03)      # 2200 мА
    assert rec["scope"]["ch2"]["i_ref_ma"] == pytest.approx(70.0 / 0.03)      # 2333.3 мА
    assert rec["stm32"]["zero_window"] == zero
    assert rec["stm32"]["raw_i1_window"]["raw_i1_avg"] == 2092
    assert rec["stm32"]["telemetry"]["vbus_mv"] == 40012
    adv = rec["model_check"]["advisory"]
    # (raw - zero) / I_ref[A] = (2092-2039)/2.2 = 24.09 raw/A (advisory)
    assert adv["ch1"]["gain_raw_per_a_advisory"] == pytest.approx((2092 - 2039) / 2.2, abs=0.02)
    assert "pre-fit only" in rec["model_check"]["note"]


# ---------------------------------------------------------------- orchestration
def test_run_campaign_records_full_flow(tmp_path, monkeypatch):
    stm = FakeStm(
        ci={0: {"samples": 256, "raw_i1_avg": 2039, "raw_i2_avg": 2068},
            1: {"samples": 256, "raw_i1_avg": 2092},
            2: {"samples": 256, "raw_i2_avg": 2135}})
    monkeypatch.setattr(builtins, "input", lambda *a: "")   # Enter на каждом шаге
    report = scc.run_campaign(FakeScope(), stm, tmp_path,
                              {"expect": {}, "rshunt": 0.03,
                               "points": ["500", "1000"],
                               "firmware_sha256": "abc", "map_crc32": "def"})
    assert report["verdict"] == "RECORDED" and report["phase"] == "DONE"
    assert [r["point"] for r in report["records"]] == ["0A", "500", "1000"]
    assert report["records"][0]["stm32"]["zero_window"]["raw_i1_avg"] == 2039
    assert report["records"][0]["stm32"]["raw_i1_window"] is None
    assert report["records"][1]["stm32"]["raw_i1_window"]["raw_i1_avg"] == 2092
    assert report["records"][1]["stm32"]["raw_i2_window"]["raw_i2_avg"] == 2135
    assert report["records"][1]["stm32"]["telemetry"]["vbus_mv"] == 40012
    assert len(report["waveform_files"]) == 6
    for name in report["waveform_files"]:
        assert (tmp_path / name).exists()
    assert report["scope_identity"]["model"] == "510401102"
    assert report["dataset_crc32"]
    # crash-resilient: отчёт на диске уже после последней записи
    on_disk = json.loads((tmp_path / "campaign_report.json").read_text("utf-8"))
    assert on_disk["verdict"] == "RECORDED"
    assert len(on_disk["records"]) == 3


def test_run_campaign_gate_blocked_writes_report_no_records(tmp_path):
    report = scc.run_campaign(FakeScope(fail={"idn": True}), None, tmp_path,
                              {"expect": {}, "rshunt": 0.03, "points": ["500"]})
    assert report["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert report["records"] == []
    on_disk = json.loads((tmp_path / "campaign_report.json").read_text("utf-8"))
    assert on_disk["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert not list(tmp_path.glob("scope_*.csv"))


def test_run_campaign_skip_all_is_no_records(tmp_path, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "s")
    report = scc.run_campaign(FakeScope(), None, tmp_path,
                              {"expect": {}, "rshunt": 0.03, "points": ["500"]})
    assert report["verdict"] == "NO_RECORDS"


def test_main_scope_open_failed(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("no usb")
    monkeypatch.setattr(scc, "ScopeAdapter", boom)
    rc = scc.main(["campaign", "--out", str(tmp_path / "out"),
                   "--points", "500"])
    assert rc == 3
    rep = json.loads((tmp_path / "out" / "campaign_report.json").read_text("utf-8"))
    assert rep["verdict"] == scc.SCOPE_CONTROL_BLOCKED
    assert rep["verdict_reason"] == "scope_open_failed"


# ---------------------------------------------------------------- stm32 parse
def make_stm_without_port():
    st = object.__new__(scc.Stm32Adapter)
    return st


def test_stm_ci_window_parsing(monkeypatch):
    st = make_stm_without_port()
    monkeypatch.setattr(st, "_transact", lambda cmd, wait_s: (
        "@CI:ZERO:samples=256:raw_i1_avg=2039:raw_i2_avg=2068:raw_i1_min=2035:"
        "raw_i1_max=2043:raw_i2_min=2064:raw_i2_max=2072:dt_ms=12:vbus_mv=0\r\n> " if cmd == "ci 0"
        else "@CI:PT1:samples=256:raw_i1_avg=2092:raw_i1_min=2085:raw_i1_max=2101:"
             "dt_ms=11:vbus_mv=40012\r\n> "))
    z = st.ci_window(0)
    assert z["raw_i1_avg"] == 2039 and z["samples"] == 256 and z["vbus_mv"] == 0
    p1 = st.ci_window(1)
    assert p1["raw_i1_max"] == 2101
    monkeypatch.setattr(st, "_transact", lambda cmd, wait_s: "@CI:FAIL:ch=1:rc=-1\r\n> ")
    assert st.ci_window(1) is None
    monkeypatch.setattr(st, "_transact", lambda cmd, wait_s: "garbage\r\n> ")
    assert st.ci_window(2) is None


def test_stm_read_adc_parsing(monkeypatch):
    st = make_stm_without_port()
    monkeypatch.setattr(st, "_transact", lambda cmd, wait_s:
                        "@ADC:I1=1000:I2=-100:Ires=0:VBUS=40012\r\n> ")
    t = st.read_adc()
    assert t == {"i1_ma": 1000, "i2_ma": -100, "ires_ma": 0, "vbus_mv": 40012}
