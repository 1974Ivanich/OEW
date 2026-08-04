#include "adc.h"
#include "stm32g474xx.h"

/* Буфер последних измерений */
static volatile struct {
    uint16_t raw_i1;
    uint16_t raw_i2;
    uint16_t raw_ires;
    uint16_t raw_vbus;
    uint16_t offset_i1;    // нулевой код канала I1 (при 0 токе)
    uint16_t offset_i2;    // нулевой код канала I2
    uint16_t offset_ires;  // нулевой код канала Ires
} adc_data;

/* Счётчик overrun-событий ADC (потерянные измерения).
 * Инкрементируется в ISR при ADC_ISR_OVR. Доступен через ADC_GetOvrCount(). */
volatile uint32_t adc_ovr_count = 0;

/* ── Внутренние функции ──────────────────────────────────────────────── */

static void adc2_stop(void) {
    if(ADC2->CR & ADC_CR_ADSTART) {
        ADC2->CR |= ADC_CR_ADSTP;
        uint32_t t = 100000;
        while(ADC2->CR & ADC_CR_ADSTP) { if(--t == 0) break; }
    }
    ADC2->ISR = ADC_ISR_OVR;
}

static uint16_t adc2_read(uint32_t ch) {
    uint32_t t = 1000000;
    if(!(ADC2->CR & ADC_CR_ADEN)) return 0xFFFD;
    if(ADC2->CR & ADC_CR_ADSTART) {
        ADC2->CR |= ADC_CR_ADSTP; t = 100000;
        while(ADC2->CR & ADC_CR_ADSTP) { if(--t == 0) break; }
    }
    /* RM0440 гл.22, JQDIS=1: regular-старт при взведённом JADSTART не
     * определён — единственный injected-контекст конфликтует с ADSTART
     * (симптом: 0xC00 в DR на чётных вызовах при корректных JDR).
     * Снимаем ожидание TIM1_TRGO на время single-shot чтения. */
    uint32_t rearm = ADC2->CR & ADC_CR_JADSTART;
    if(rearm) {
        ADC2->CR |= ADC_CR_JADSTP;
        uint32_t tj = 100000;
        while(ADC2->CR & ADC_CR_JADSTP) { if(--tj == 0) break; }
    }
    ADC2->SQR1 = (ch << ADC_SQR1_SQ1_Pos);
    ADC2->ISR = (ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR);
    ADC2->CR |= ADC_CR_ADSTART;
    while(!(ADC2->ISR & ADC_ISR_EOC)) {
        if(--t == 0) {
            if(rearm) ADC2->CR |= ADC_CR_JADSTART;   /* не потерять реарм на таймауте */
            return 0xFFFF;
        }
    }
    uint16_t r = (uint16_t)(ADC2->DR);
    adc2_stop();
    if(rearm) ADC2->CR |= ADC_CR_JADSTART;  /* вернуть injected в ожидание TIM1_TRGO */
    return r;
}

/* ── Расчёт тока из шунтового датчика ──────────────────────────────
 * V_adc = I * Rshunt * Gain + V_offset
 * I(A) = (V_adc - V_offset) / (Rshunt * Gain)
 * I(мА) = (raw - offset) * VREF_mV * 1000 / (ADC_MAX_CODE * SHUNT_UV_PER_A)
 *
 * Коэффициент вычисляется из физических констант, не захардкожен.
 * При смене Rshunt/Gain/Vref достаточно изменить константы в adc.h. */
static int32_t calc_current_st(uint16_t raw, uint16_t offset) {
    int32_t diff = (int32_t)raw - (int32_t)offset;
    /* diff * VREF_mV * 1000 / (ADC_MAX_CODE * SHUNT_UV_PER_A) мА
     * = diff * 3300 * 1000 / (4095 * 63000)
     * = diff * 3300000 / 257985000 ≈ diff * 12.79 */
    return (int32_t)(((int64_t)diff * (int64_t)ADC_VREF_MV * 1000) /
                     ((int64_t)ADC_MAX_CODE * (int64_t)SHUNT_UV_PER_A));
}

/* ── Расчёт Vbus ────────────────────────────────────────────────────── */
static int32_t calc_vbus(uint16_t raw) {
    /* Vbus = raw * VREF_mV * делитель / ADC_MAX_CODE, мВ */
    return (int32_t)(((int64_t)raw * (int64_t)ADC_VREF_MV * (int64_t)VBUS_DIVIDER) /
                     (int64_t)ADC_MAX_CODE);
}

/* ── Публичные функции ────────────────────────────────────────────────── */

void ADC_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN;
    volatile uint32_t d = 10000; while(d--);
    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST;
    RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST;
    d = 1000; while(d--);
    ADC12_COMMON->CCR = (2U << 16); /* CKMODE=10: HCLK/4 = 42.5 МГц (max 60) */
    ADC2->CR = 0;
    ADC2->CR &= ~ADC_CR_DEEPPWD;
    ADC2->CR |= ADC_CR_ADVREGEN;
    d = 100000; while(d--);
    ADC2->CR |= ADC_CR_ADCAL;
    uint32_t t = 1000000;
    while(ADC2->CR & ADC_CR_ADCAL) { if(--t == 0) return; }
    ADC2->CFGR = 0;
    /* SMP: IN1(PA0=I1), IN2(PA1=I2), IN3(PA6=Ires), IN5(PC4=Vbus).
     * Проверено по официальной таблице пинов STM32G474 (ST PeripheralPins.c):
     * ранее использовались IN7/IN15, которые на самом деле PC1 (ШИМ-выход
     * TIM1_CH2!) и PB15 (не настроен, плавающий) — не Ires/Vbus вообще. */
    ADC2->SMPR1 |= (7U<<ADC_SMPR1_SMP1_Pos)|(7U<<ADC_SMPR1_SMP2_Pos)
                 | (7U<<ADC_SMPR1_SMP3_Pos)|(7U<<ADC_SMPR1_SMP5_Pos);
    ADC2->ISR = ADC_ISR_ADRDY;
    ADC2->CR |= ADC_CR_ADEN;
    t = 1000000;
    while(!(ADC2->ISR & ADC_ISR_ADRDY)) { if(--t == 0) return; }

    /* Начальная калибровка offset — повторяется в FOC_Start перед каждым
     * запуском (ADC_CalibrateOffsets), когда инвертор гарантированно выключен. */
    ADC_CalibrateOffsets();
}

void ADC_StartConversion(void) {
    adc_data.raw_i1   = adc2_read(1);
    adc_data.raw_i2   = adc2_read(2);
    adc_data.raw_ires = adc2_read(3);   /* PA6 = ADC2_IN3 */
    adc_data.raw_vbus = adc2_read(5);   /* PC4 = ADC2_IN5 */
}

/* Калибровка нулей токовых каналов. Вызывать только при выключенном
 * инверторе (FOC_Start до PWM_Enable) — токи должны быть истинно нулевыми.
 * Усреднение по 8 выборкам — подавление шума. */
void ADC_CalibrateOffsets(void) {
    uint32_t s1 = 0, s2 = 0, sr = 0;
    for(int i = 0; i < ADC_OFFSET_SAMPLES; i++) {
        s1 += adc2_read(1);
        s2 += adc2_read(2);
        sr += adc2_read(3);   /* PA6 = ADC2_IN3 */
    }
    adc_data.offset_i1  = (uint16_t)(s1 / ADC_OFFSET_SAMPLES);
    adc_data.offset_i2  = (uint16_t)(s2 / ADC_OFFSET_SAMPLES);
    adc_data.offset_ires = (uint16_t)(sr / ADC_OFFSET_SAMPLES);
}

/* ── Debug tool: калибровка по 256 выборкам ──────────────────────── */
void ADC_CalibrateI1_256(void) {
    uint32_t s1 = 0, s2 = 0, sn = 0;
    uint32_t timeout;
    /* Save and disable HW trigger (JEXTEN), use software trigger instead */
    uint32_t saved_jsqr = ADC2->JSQR;
    ADC2->JSQR = saved_jsqr & ~(3U << ADC_JSQR_JEXTEN_Pos);

    ADC2->ISR = ADC_ISR_JEOS;

    for(int i = 0; i < 256; i++) {
        ADC2->CR |= ADC_CR_JADSTART;
        timeout = 100000;
        while(!(ADC2->ISR & ADC_ISR_JEOS)) {
            if(--timeout == 0) break;
        }
        ADC2->ISR = ADC_ISR_JEOS;
        s1 += (uint16_t)ADC2->JDR1;
        s2 += (uint16_t)ADC2->JDR2;
        sn += (uint16_t)ADC2->JDR3;
    }

    /* Restore HW trigger */
    ADC2->JSQR = saved_jsqr;
    adc_data.offset_i1  = (uint16_t)(s1 >> 8);
    adc_data.offset_i2  = (uint16_t)(s2 >> 8);
    adc_data.offset_ires = (uint16_t)(sn >> 8);
}

uint16_t ADC_GetOffsetI1(void) { return adc_data.offset_i1; }
uint16_t ADC_GetOffsetI2(void) { return adc_data.offset_i2; }
uint16_t ADC_GetOffsetIres(void) { return adc_data.offset_ires; }

void ADC_WaitForEOC(void) { /* все синхронно */ }

/* ── Injected group: аппаратный запуск от TIM1_TRGO ────────────────────
 * RM0440 §22.4.13: ADC_JSQR настраивает injected-группу.
 * JEXTSEL=00000: TIM1_TRGO (update event от TIM1 в center-aligned mode).
 * JEXTEN=01: rising edge.
 * JL=11: 4 преобразования (rank 1..4).
 * Каналы: ch1=I1(PA0), ch2=I2(PA1), ch3=Ires(PA6), ch5=Vbus(PC4).
 * После JADSTART ADC ждёт триггер от TIM1 — нулевой джиттер выборки. */
void ADC_InjectedInit(void) {
    ADC2->CFGR |= ADC_CFGR_JQDIS;   /* отключить queue — проще, детерминированно */
    ADC2->JSQR = (3U << ADC_JSQR_JL_Pos)            /* JL=3: 4 conversions */
               | (0U << ADC_JSQR_JEXTSEL_Pos)        /* JEXTSEL=0: TIM1_TRGO */
               | (1U << ADC_JSQR_JEXTEN_Pos)         /* JEXTEN=01: rising edge */
               | (1U << ADC_JSQR_JSQ1_Pos)           /* rank 1: ch1 = I1 (PA0) */
               | (2U << ADC_JSQR_JSQ2_Pos)           /* rank 2: ch2 = I2 (PA1) */
               | (3U << ADC_JSQR_JSQ3_Pos)           /* rank 3: ch3 = IN (PA6) */
               | (5U << ADC_JSQR_JSQ4_Pos);          /* rank 4: ch5 = Vbus (PC4) */
    ADC2->IER |= ADC_IER_JEOSIE | ADC_IER_OVRIE;  /* JEOS + overrun interrupt */
}

void ADC_InjectedStart(void) {
    ADC2->ISR = ADC_ISR_JEOS;       /* сброс флага перед стартом */
    ADC2->CR |= ADC_CR_JADSTART;    /* запуск injected — ждёт TIM1_TRGO */
}

void ADC_InjectedStop(void) {
    if(ADC2->CR & ADC_CR_JADSTART) {
        ADC2->CR |= ADC_CR_JADSTP;
        uint32_t t = 100000;
        while(ADC2->CR & ADC_CR_JADSTP) { if(--t == 0) break; }
    }
}

void ADC_ReadInjected(void) {
    adc_data.raw_i1   = (uint16_t)ADC2->JDR1;
    adc_data.raw_i2   = (uint16_t)ADC2->JDR2;
    adc_data.raw_ires = (uint16_t)ADC2->JDR3;
    adc_data.raw_vbus = (uint16_t)ADC2->JDR4;
}

uint16_t ADC_GetRawI1(void)   { return adc_data.raw_i1; }
uint16_t ADC_GetRawI2(void)   { return adc_data.raw_i2; }
uint16_t ADC_GetRawIres(void) { return adc_data.raw_ires; }
uint16_t ADC_GetRawVbus(void) { return adc_data.raw_vbus; }

int32_t ADC_GetI1_mA(void) {
    return calc_current_st(adc_data.raw_i1, adc_data.offset_i1);
}

int32_t ADC_GetI2_mA(void) {
    return calc_current_st(adc_data.raw_i2, adc_data.offset_i2);
}

int32_t ADC_GetIres_mA(void) {
    return calc_current_st(adc_data.raw_ires, adc_data.offset_ires);
}

int32_t ADC_GetVbus_mV(void) {
    return calc_vbus(adc_data.raw_vbus);
}

uint32_t ADC_GetOvrCount(void) {
    return adc_ovr_count;
}
