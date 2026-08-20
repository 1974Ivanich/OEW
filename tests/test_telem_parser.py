from telem_parser import (
    VFLOG_FIELDS,
    parse_curve,
    parse_enc,
    parse_foc,
    parse_params,
    parse_vflog,
)


def test_vflog_all_fields_and_signed_values():
    line = (
        "@VFLOG:t=20:target=-100:meas=95:fe=10:fslip=-2:vmag=40:theta=123:"
        "du=10:dv=-20:dw=30:i1=100:i2=-200:ires=0:vbus=24000:"
        "eangle=33:espeed=-400:eerr=2:fault=1:drp=4"
    )
    value = parse_vflog(line)
    assert value is not None
    assert set(value) == set(VFLOG_FIELDS)
    assert value["target"] == -100
    assert value["drp"] == 4


def test_vflog_missing_fields_are_none():
    value = parse_vflog("@VFLOG:t=1:target=2:meas=3")
    assert value is not None
    assert value["t"] == 1
    assert value["target"] == 2
    assert value["drp"] is None


def test_vflog_rejects_wrong_or_empty_lines():
    assert parse_vflog("") is None
    assert parse_vflog("garbage") is None
    assert parse_vflog("@VFLOG:") is None


def test_foc_and_enc():
    foc = parse_foc("@FOC:I1=-10:I2=20:Ires=0:VBUS=24000:STATE=3:RUN=1")
    enc = parse_enc("@ENC:angle=8192:speed=-120:period_us=1087:pulse_us=543:err=0")
    assert foc == {"I1": -10, "I2": 20, "Ires": 0, "VBUS": 24000, "STATE": 3, "RUN": 1}
    assert enc["angle"] == 8192
    assert enc["speed"] == -120
    assert enc["err"] == 0


def test_params_mapping_and_prefixes():
    value = parse_params("@AT:PARAMS:Rs_mOhm=12:Ls_uH=450:Isat_mA=1000:p=6:J_x1e6=3")
    assert value == {"Rs": 12, "Ls": 450, "Isat": 1000, "p": 6, "J": 3}
    assert parse_params("@PARAMS:Rs=12") == {"Rs": 12}
    assert parse_params("@PARAMS:") is None


def test_curve_parser():
    value = parse_curve("@IDLE:CURVE:I=100,L=900:I=-200,L=850")
    assert value == {"points": [(100, 900), (-200, 850)]}
    assert parse_curve("@IDLE:CURVE:bad") is None
    assert parse_curve("@CURVE:I=1,L=2") is None
