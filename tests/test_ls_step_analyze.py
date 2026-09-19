import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ls_step_analyze as A


def synth(L=800.0, Rs=13000.0, levels=(5,10,15), dt_us=200.0, noise=0.0,
          pre=4, step=16, start_t=1_000_000, arr=999):
    rows = ["@AT:LS:START"]
    for d in levels:
        u = 3000.0 * d / 5.0
        iss = u / (Rs/1000.0)
        tau = L / (Rs/1000.0)
        rows.append(f"@AT:LS:LEVEL:d={d}:U_eff_mv={u:.0f}:ccr_hi=550:ccr_lo=450:arr={arr}")
        t = start_t
        for n in range(pre):
            rows.append(f"@AT:LS:FRAME:d={d}:n={n}:ph=0:t={int(t)}:I1=0:I2=0:Idiff=0:Vbus=30000:CCR1=500:CCR8=500")
            t += dt_us * 170.0
        for n in range(4, 4+step):
            x = (n-4)*dt_us
            i = iss*(1-math.exp(-x/tau)) + noise*((-1)**n)
            ii = int(round(i))
            rows.append(f"@AT:LS:FRAME:d={d}:n={n}:ph=1:t={int(t)}:I1={ii}:I2={-ii}:Idiff={ii}:Vbus=30000:CCR1=550:CCR8=450")
            t += dt_us * 170.0
        rows.append(f"@AT:LS:RESULT:d={d}:Lstep_uH={L:.3f}:n_valid=15:Rs_mOhm={int(Rs)}:SEMANTICS=Lstep_not_confirmed_Ls")
    rows.append("@AT:LS:DONE")
    return "\r\n".join(rows)+"\r\n"


def test_fit_800_three_levels():
    r=A.analyze(synth(800), "synthetic")
    assert r["verdict"] == "UNUSABLE"
    for lev in r["levels"]:
        assert abs(lev["fit"]["L_fit_uH"]-800)/800 < .05


def test_linear_is_biased_and_conditioned_800():
    r=A.analyze(synth(800), "synthetic")
    lev=r["levels"][0]
    assert lev["all_pairs"]["median_lin_uH"] > 800
    assert .2 < lev["cond"] < .5


def test_40mhy_estimators_agree():
    r=A.analyze(synth(40000), "synthetic")
    for lev in r["levels"]:
        assert abs(lev["fit"]["L_fit_uH"]-40000)/40000 < .05
        assert abs(lev["all_pairs"]["median_lin_uH"]-lev["fit"]["L_fit_uH"])/lev["fit"]["L_fit_uH"] < .10
    assert r["checks"]["CONDITIONING"] == "PASS"


def test_200uh_conditioning_fails():
    r=A.analyze(synth(200), "synthetic")
    assert r["checks"]["CONDITIONING"] == "FAIL:INSUFFICIENT_TRANSIENT"
    assert r["_exit"] == 1


def test_8000uh_conditioning_and_fit():
    r=A.analyze(synth(8000), "synthetic")
    for lev in r["levels"]:
        assert abs(lev["fit"]["L_fit_uH"]-8000)/8000 < .05
        assert 0.06 < lev["all_pairs"]["median_lin_uH"]/8000-1 < .18
    assert r["checks"]["CONDITIONING"] == "PASS"
    assert r["verdict"] == "USABLE"


def test_linear_formula_arithmetic():
    text=synth(40000, levels=(5,), dt_us=200)
    r=A.analyze(text, "synthetic")
    p=r["levels"][0]["pairs"][0]
    expected=(3000-(p["i_mid_ma"]*13))*200/p["dI_ma"]
    assert abs(p["L_uH"]-expected) < 1e-9


def test_pwm_period_pass():
    r=A.analyze(synth(40000), "synthetic")
    assert r["checks"]["PWM_PERIOD"] == "PASS"


def test_pwm_period_fail():
    r=A.analyze(synth(40000, dt_us=400), "synthetic")
    assert r["checks"]["PWM_PERIOD"] == "FAIL:PERIOD_MODEL_MISMATCH"


def test_tclk_changes_period_model():
    r=A.analyze(synth(40000, dt_us=100), "synthetic", tclk_mhz=20)
    assert r["checks"]["PWM_PERIOD"] == "PASS"


def test_missing_level_incomplete():
    r=A.analyze(synth(40000, levels=(5,10)), "synthetic")
    assert r["verdict"] == "INCOMPLETE"
    assert r["_exit"] == 1


def test_mixed_sign_fails():
    text=synth(40000)
    text=text.replace(":n=5:ph=1:", ":n=5:ph=1:")  # keep grammar stable
    lines=text.splitlines()
    for i,line in enumerate(lines):
        if ":d=5:n=5:ph=1:" in line:
            parts=line.split(":Idiff=")
            val=int(parts[1].split(":")[0])
            lines[i]=line.replace(f":Idiff={val}:", f":Idiff={-val}:").replace(f":I1={val}:", f":I1={-val}:").replace(f":I2={-val}:", f":I2={val}:")
            break
    r=A.analyze("\n".join(lines)+"\n","synthetic")
    assert r["checks"]["SIGN"] == "FAIL"


def test_pre_plateau_fails():
    text=synth(40000).replace(":n=2:ph=0:t=", ":n=2:ph=0:t=")
    lines=text.splitlines()
    for i,line in enumerate(lines):
        if ":d=5:n=2:ph=0:" in line:
            lines[i]=line.replace("I1=0:I2=0:Idiff=0", "I1=400:I2=-400:Idiff=400")
            break
    r=A.analyze("\n".join(lines)+"\n","synthetic")
    assert r["checks"]["SHAPE"] == "FAIL"


def test_dt_outlier_rejected():
    lines=synth(40000).splitlines()
    target=None
    for i,line in enumerate(lines):
        if ":d=5:n=5:ph=1:" in line:
            target=i
            break
    # Stretch only the timestamp of frame n=5; its predecessor pair is rejected.
    old=int(lines[target].split(":t=")[1].split(":")[0])
    lines[target]=lines[target].replace(f":t={old}:", f":t={old+500000}:")
    r=A.analyze("\n".join(lines)+"\n","synthetic")
    reasons=[p["reason"] for p in r["levels"][0]["pairs"]]
    assert "REJECT:DT_OUT_OF_ENVELOPE" in reasons


def test_small_di_level_is_incomplete():
    lines=synth(40000).splitlines()
    for i,line in enumerate(lines):
        if ":d=5:" in line and ":ph=1:" in line:
            lines[i]=re.sub(r":I1=-?\\d+:I2=-?\\d+:Idiff=-?\\d+", ":I1=100:I2=-100:Idiff=100", line)
    r=A.analyze("\n".join(lines)+"\\n","synthetic")
    assert r["levels"][0]["all_pairs"]["n_accepted"] == 0

def test_all_nonpositive_di_sign_fails():
    lines=synth(40000).splitlines()
    for i,line in enumerate(lines):
        if ":d=5:" in line and ":ph=1:" in line:
            parts=line.split(":Idiff=")
            val=int(parts[1].split(":")[0])
            if val > 0:
                lines[i]=line.replace(f":Idiff={val}:", f":Idiff={-val}:")
    r=A.analyze("\n".join(lines)+"\n","synthetic")
    assert r["checks"]["SIGN"] == "FAIL"


def test_convergence_fails():
    texts=[]
    for L in (800,1600,800):
        texts.append(synth(L))
    # Build one log with one START/DONE and distinct level records.
    base=synth(800).splitlines()
    extra=synth(1600).splitlines()
    for d in (5,10,15):
        pass
    # Change only d=10 RESULT and data by replacing its numeric model in a valid log.
    lines=synth(800).splitlines()
    start=next(i for i,x in enumerate(lines) if ":d=10:" in x and "LEVEL" in x)
    end=next(i for i in range(start+1,len(lines)) if ":d=15:" in lines[i] and "LEVEL" in lines[i])
    block=synth(1600, levels=(10,)).splitlines()
    block=block[1:-1]
    lines=lines[:start]+block+lines[end:]
    r=A.analyze("\n".join(lines)+"\n","synthetic")
    assert r["checks"]["CONVERGENCE"] == "FAIL"


@pytest.mark.parametrize("L", [200, 900000])
def test_plausibility_out_of_range(L):
    r=A.analyze(synth(L), "synthetic")
    assert r["checks"]["PLAUSIBILITY"] == "FAIL:OUT_OF_RANGE"


def test_incomplete_without_done():
    r=A.analyze(synth(40000).replace("@AT:LS:DONE",""), "synthetic")
    assert r["verdict"] == "INCOMPLETE"
    assert r["_exit"] == 1


def test_unreadable_without_start():
    r=A.analyze("@SYS:hello\n", "synthetic")
    assert r["verdict"] == "UNREADABLE"
    assert r["_exit"] == 2


def test_pwm_arm_fault_preserved():
    text=synth(40000).replace("@AT:LS:DONE","@AT:LS:ERROR:PWM_ARM_FAIL:-5\n")
    r=A.analyze(text, "synthetic")
    assert r["verdict"] == "INCOMPLETE"
    assert r["data_integrity"]["fault_lines"] == ["@AT:LS:ERROR:PWM_ARM_FAIL:-5"]


def test_overcurrent_fault_preserved():
    text=synth(40000).replace("@AT:LS:DONE","@AT:LS:ERROR:OVERCURRENT I1=7000 I2=-200\n")
    r=A.analyze(text, "synthetic")
    assert r["verdict"] == "INCOMPLETE"
    assert r["data_integrity"]["fault_lines"][0].endswith("I1=7000 I2=-200")


def test_firmware_mismatch_is_warning_only():
    text=synth(40000).replace("Lstep_uH=40000.000","Lstep_uH=1000.000")
    r=A.analyze(text, "synthetic")
    assert "WARN:FIRMWARE_MISMATCH" in r["reasons"]
    assert r["verdict"] == "USABLE"


def test_noise_increases_fit_residual():
    clean=A.analyze(synth(8000), "synthetic")
    noisy=A.analyze(synth(8000, noise=20), "synthetic")
    assert noisy["levels"][0]["fit"]["residual_rms_ma"] > clean["levels"][0]["fit"]["residual_rms_ma"]


def test_crlf_and_noise_lines_are_accepted():
    text="junk\r\n@SYS:x\r\n"+synth(40000)+"@MC:REC\r\n"
    r=A.analyze(text, "synthetic")
    assert r["data_integrity"]["start"] is True
    assert r["verdict"] == "USABLE"


def test_rs_zero_is_direct_u_dt_di():
    rows = ["@AT:LS:START", "@AT:LS:LEVEL:d=5:U_eff_mv=3000:ccr_hi=550:ccr_lo=450:arr=999"]
    for n,i in enumerate((0,100,200,300)):
        rows.append(f"@AT:LS:FRAME:d=5:n={n}:ph=1:t={100000+n*34000}:I1={i}:I2=0:Idiff={i}:Vbus=30000:CCR1=550:CCR8=450")
    rows.append("@AT:LS:RESULT:d=5:Lstep_uH=1000:n_valid=3:Rs_mOhm=0:SEMANTICS=Lstep_not_confirmed_Ls")
    rows.append("@AT:LS:DONE")
    r=A.analyze("\n".join(rows)+"\\n","synthetic",rs_mohm=0)
    assert r["levels"][0]["pairs"][0]["L_uH"] > 0

def test_json_shape_and_cli(tmp_path):
    log=tmp_path/"log.txt"
    out=tmp_path/"out.json"
    log.write_text(synth(40000),encoding="utf-8",newline="\n")
    p=subprocess.run([sys.executable,str(ROOT/"tools/ls_step_analyze.py"),str(log),"--json",str(out)],
                     text=True,capture_output=True)
    assert p.returncode == 0
    data=json.loads(out.read_text(encoding="utf-8"))
    assert data["tool"]=="ls_step_analyze"
    assert data["schema"]=="tz-ls-step-analyze-1"
    assert "levels" in data


def test_json_unwritable_returns_one(tmp_path):
    log=tmp_path/"log.txt"
    log.write_text(synth(40000),encoding="utf-8")
    bad=tmp_path/"missing"/"out.json"
    p=subprocess.run([sys.executable,str(ROOT/"tools/ls_step_analyze.py"),str(log),"--json",str(bad)],
                     text=True,capture_output=True)
    assert p.returncode == 1
    assert "ERROR:" in p.stderr


def test_posthoc_firmware_mismatch_does_not_recommend_mp():
    r=A.analyze(synth(40000).replace("Lstep_uH=40000.000","Lstep_uH=1000.000"),"synthetic")
    rendered=A.render(r,"synthetic")
    assert "mp=" not in rendered.lower()
    assert "recommend" not in rendered.lower()
