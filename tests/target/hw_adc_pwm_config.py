"""HWT: интерфейс ADC ↔ PWM (G-IF.1–G-IF.6) — только чтение.

Сценарий читает регистры, которые к точке `UART_ReadLine` уже записаны
`ADC_Init()`/`ADC_InjectedInit()` (src/adc.c) и `PWM_Init()` (src/pwm.c),
и сверяет их с контрактом `pinout.md` §3 и «Примечания». Ничего не пишет,
PWM и ADC не запускает.

Статусы в отчёте:
* проверка (`t.check`) — фактическое значение регистра на цели (TARGET_READBACK);
* `DERIVED` — расчёт времени из прочитанных регистров; это не измерение;
* `NOT_EVALUATED` — то, что этим сценарием не наблюдаемо (латентность
  TRGO → выборка, положение окна относительно ШИМ, CNT в момент JEOS).

Поля регистров — по stm32g474xx.h (ST cmsis-device-g4). Длительность
выборки кода SMP = 111 — 640,5 такта (ST stm32g4xx_ll_adc.h,
LL_ADC_SAMPLINGTIME_640CYCLES_5); таблица RM0440 локально не прочитана.
"""

from __future__ import annotations

from hwt_stm32g4 import (ADC1_JSQR, ADC1_SMPR1, ADC1_SMPR2, ADC2_JSQR,
                         ADC2_SMPR1, ADC2_SMPR2, ADC12_CCR, RCC_CFGR,
                         RCC_PLLCFGR, TIM1_CR1, TIM1_CR2, TIM1_RCR, TIM8_SMCR)
from hwt_target import case

CHECKPOINT = "UART_ReadLine"

HSI16_HZ = 16_000_000
SMP_CYCLES = (2.5, 6.5, 12.5, 24.5, 47.5, 92.5, 247.5, 640.5)
CONV_12BIT_CYCLES = 12.5
HPRE_DIV = {0b1000: 2, 0b1001: 4, 0b1010: 8, 0b1011: 16,
            0b1100: 64, 0b1101: 128, 0b1110: 256, 0b1111: 512}

# Контракт прошивки (src/adc.c:10-13, 317-318; src/pwm.c:281-312; main.c:658-662).
CH_I1, CH_I2, CH_IRES, CH_VBUS = 1, 2, 3, 5
SMP_CONTRACT = 0b111


def _f(value: int, pos: int, width: int) -> int:
    return (value >> pos) & ((1 << width) - 1)


def _smp(smpr1: int, smpr2: int, ch: int) -> int:
    return _f(smpr1, ch * 3, 3) if ch <= 9 else _f(smpr2, (ch - 10) * 3, 3)


def _jsqr(jsqr: int) -> dict:
    jl = _f(jsqr, 0, 2)
    ranks = [_f(jsqr, pos, 5) for pos in (9, 15, 21, 27)][:jl + 1]
    return {"JL": jl, "JEXTSEL": _f(jsqr, 2, 5), "JEXTEN": _f(jsqr, 7, 2),
            "ranks": ranks}


@case("HW_ADC_PWM_CONFIG", timeout_s=20, labels=("config", "no-hv"),
      contracts=(CHECKPOINT,),
      description="G-IF: такт ADC, SMPR, injected-последовательности ADC1/ADC2, "
                  "TRGO TIM1 и Reset Mode TIM8 — чтение регистров, без запуска")
def hw_adc_pwm_config(t):
    t.reach(CHECKPOINT)

    # ── G-IF.1: такт ADC ──────────────────────────────────────────────────
    cfgr = t.read_u32(RCC_CFGR)
    pll = t.read_u32(RCC_PLLCFGR)
    ccr = t.read_u32(ADC12_CCR)
    t.note(f"raw: RCC_CFGR=0x{cfgr:08X} RCC_PLLCFGR=0x{pll:08X} "
           f"ADC12_CCR=0x{ccr:08X}")
    t.check("G-IF.1 RCC_CFGR.SWS = PLL", _f(cfgr, 2, 2), 0b11,
            detail="SWS[3:2]=11 — SYSCLK от PLL (main.c:670)")
    t.check("G-IF.1 RCC_CFGR.HPRE = /1", _f(cfgr, 4, 4) & 0b1000, 0,
            detail="HPRE[7:4]=0xxx — HCLK = SYSCLK")
    t.check("G-IF.1 PLLCFGR.PLLSRC = HSI16", _f(pll, 0, 2), 0b10,
            detail="main.c:662")
    t.check("G-IF.1 PLLCFGR.PLLM = /4", _f(pll, 4, 4), 3, detail="поле M−1")
    t.check("G-IF.1 PLLCFGR.PLLN = 85", _f(pll, 8, 7), 85)
    t.check("G-IF.1 PLLCFGR.PLLR = /2", _f(pll, 25, 2), 0, detail="00 = /2")
    t.check("G-IF.1 PLLCFGR.PLLREN", _f(pll, 24, 1), 1)
    t.check("G-IF.1 ADC12_CCR.CKMODE = HCLK/4", _f(ccr, 16, 2), 0b11,
            detail="src/adc.c:323")

    f_adc = None
    if _f(cfgr, 2, 2) == 0b11 and _f(pll, 0, 2) == 0b10 and _f(ccr, 16, 2) == 0b11:
        sysclk = HSI16_HZ / (_f(pll, 4, 4) + 1) * _f(pll, 8, 7) / \
            (2 * (_f(pll, 25, 2) + 1))
        hclk = sysclk / HPRE_DIV.get(_f(cfgr, 4, 4), 1)
        f_adc = hclk / 4
        t.note(f"G-IF.1 DERIVED: SYSCLK={sysclk / 1e6:.2f} МГц, HCLK="
               f"{hclk / 1e6:.2f} МГц, f_ADC={f_adc / 1e6:.3f} МГц "
               "(HSI16 номинал, без поправки на точность HSI)")
    else:
        t.note("G-IF.1 NOT_EVALUATED: f_ADC не выводится однозначно из регистров")

    # ── G-IF.2: время выборки ─────────────────────────────────────────────
    a1s1, a1s2 = t.read_u32(ADC1_SMPR1), t.read_u32(ADC1_SMPR2)
    a2s1, a2s2 = t.read_u32(ADC2_SMPR1), t.read_u32(ADC2_SMPR2)
    t.note(f"raw: ADC1_SMPR1=0x{a1s1:08X} ADC1_SMPR2=0x{a1s2:08X} "
           f"ADC2_SMPR1=0x{a2s1:08X} ADC2_SMPR2=0x{a2s2:08X}")
    used = (("ADC1", "I1", CH_I1, a1s1, a1s2), ("ADC2", "I2", CH_I2, a2s1, a2s2),
            ("ADC2", "Ires", CH_IRES, a2s1, a2s2),
            ("ADC2", "VBUS", CH_VBUS, a2s1, a2s2))
    smp_by_ch = {}
    for adc, sig, ch, s1, s2 in used:
        smp = _smp(s1, s2, ch)
        smp_by_ch[(adc, ch)] = smp
        t.check(f"G-IF.2 {adc} IN{ch} ({sig}) SMP = 111", smp, SMP_CONTRACT,
                detail="640,5 такта ADC (src/adc.c:195-206)")
    for adc, s1 in (("ADC1", a1s1), ("ADC2", a2s1)):
        t.check(f"G-IF.2 {adc} SMPR1.SMPPLUS = 0", _f(s1, 31, 1), 0)

    # ── G-IF.3: injected-последовательности и триггер ─────────────────────
    j1, j2 = t.read_u32(ADC1_JSQR), t.read_u32(ADC2_JSQR)
    d1, d2 = _jsqr(j1), _jsqr(j2)
    t.note(f"raw: ADC1_JSQR=0x{j1:08X} {d1}; ADC2_JSQR=0x{j2:08X} {d2}")
    t.check("G-IF.3 ADC12_CCR.DUAL = injected simultaneous", _f(ccr, 0, 5),
            0b00101, detail="src/adc.c:8, 323")
    for name, d, ranks in (("ADC1", d1, [CH_I1]),
                           ("ADC2", d2, [CH_I2, CH_IRES, CH_VBUS])):
        t.check(f"G-IF.3 {name} JL", d["JL"], len(ranks) - 1)
        t.check(f"G-IF.3 {name} ранги 1..N", d["ranks"], ranks,
                detail="src/adc.c:317-318")
        t.check(f"G-IF.3 {name} JEXTSEL = TIM1_TRGO", d["JEXTSEL"], 0,
                detail="00000 = TIM1_TRGO (src/adc.c:5; ST LL "
                       "LL_ADC_INJ_TRIG_EXT_TIM1_TRGO)")
        t.check(f"G-IF.3 {name} JEXTEN = передний фронт", d["JEXTEN"], 0b01)

    # ── G-IF.4: TIM1 TRGO ─────────────────────────────────────────────────
    cr1, cr2, rcr = t.read_u32(TIM1_CR1), t.read_u32(TIM1_CR2), t.read_u32(TIM1_RCR)
    t.note(f"raw: TIM1_CR1=0x{cr1:08X} TIM1_CR2=0x{cr2:08X} TIM1_RCR=0x{rcr:08X}")
    t.check("G-IF.4 TIM1 CR2.MMS = update", _f(cr2, 4, 3) | (_f(cr2, 25, 1) << 3),
            0b0010, detail="src/pwm.c:294")
    t.check("G-IF.4 TIM1 CR2.MMS2 = 0 (TRGO2 не используется)", _f(cr2, 20, 4), 0)
    t.check("G-IF.4 TIM1 RCR = 1", rcr & 0xFFFF, 1, detail="src/pwm.c:282")
    t.check("G-IF.4 TIM1 CR1.CMS = center-aligned 3", _f(cr1, 5, 2), 0b11)
    t.check("G-IF.4 TIM1 CR1.ARPE", _f(cr1, 7, 1), 1)
    t.check("G-IF.4 TIM1 CR1.CKD = DIV1", _f(cr1, 8, 2), 0)
    t.note("G-IF.4 DERIVED: при CMS=11 и RCR=1 событие update (TRGO) — раз за "
           "период ШИМ; на каком экстремуме счёта — NOT_EVALUATED")

    # ── G-IF.5: TIM8 Reset Mode от TIM1_TRGO ──────────────────────────────
    smcr = t.read_u32(TIM8_SMCR)
    t.note(f"raw: TIM8_SMCR=0x{smcr:08X}")
    t.check("G-IF.5 TIM8 SMCR.SMS = Reset Mode",
            _f(smcr, 0, 3) | (_f(smcr, 16, 1) << 3), 0b0100, detail="src/pwm.c:312")
    t.check("G-IF.5 TIM8 SMCR.TS = ITR0",
            _f(smcr, 4, 3) | (_f(smcr, 20, 2) << 3), 0,
            detail="ITR0 = TIM1_TRGO для TIM8 — по src/pwm.c:312, RM0440 не прочитан")

    # ── расчёт времени (DERIVED) и ненаблюдаемое (NOT_EVALUATED) ──────────
    if f_adc and all(v == SMP_CONTRACT for v in smp_by_ch.values()):
        n = SMP_CYCLES[SMP_CONTRACT]
        ts, tc = n / f_adc * 1e6, (n + CONV_12BIT_CYCLES) / f_adc * 1e6
        seq = len(d2["ranks"]) * tc
        t.note(f"G-IF.2 DERIVED: t_sample={ts:.2f} мкс, t_conv={tc:.2f} мкс; "
               f"последовательность ADC2 ({len(d2['ranks'])} ранга) ≈ {seq:.1f} мкс; "
               f"начало выборки Ires/VBUS после I2 ≈ +{tc:.2f}/+{2 * tc:.2f} мкс "
               "(без латентности триггера и межранговых задержек)")
    else:
        t.note("G-IF.2 NOT_EVALUATED: время не рассчитывается — f_ADC или SMP "
               "не соответствуют контракту")
    t.note("G-IF.3 NOT_EVALUATED: латентность TIM1_TRGO → начало выборки — "
           "только осциллографом")
    t.note("G-IF.4 NOT_EVALUATED: положение окна выборки относительно ШИМ")
    t.note("G-IF.6 NOT_EVALUATED: TIM1->CNT в момент JEOS — нужен работающий "
           "ШИМ/ADC и запись CNT в обработчике JEOS (src/adc.c — safety, "
           "отдельное ТЗ); кадр сейчас несёт только DWT->CYCCNT")
