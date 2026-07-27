#include "adc.h"
#include "stm32g474xx.h"

/* Буфер последних измерений */
static volatile struct {
    uint16_t raw_i1;
    uint16_t raw_i2;
    uint16_t raw_in;
    uint16_t raw_vbus;
    uint16_t offset_i1;    // нулевой код канала I1 (при 0 токе)
    uint16_t offset_i2;    // нулевой код канала I2
    uint16_t offset_in;    // нулевой код канала IN
} adc_data;

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
    ADC2->SQR1 = (ch << ADC_SQR1_SQ1_Pos);
    ADC2->ISR = (ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR);
    ADC2->CR |= ADC_CR_ADSTART;
    while(!(ADC2->ISR & ADC_ISR_EOC)) { if(--t == 0) return 0xFFFF; }
    uint16_t r = (uint16_t)(ADC2->DR);
    adc2_stop();
    return r;
}

/* ── Расчёт тока с одношунтового датчика (STEVAL-IPM20B) ──────────────
 * V_adc = I * Rshunt * Gain + V_offset
 * I(A) = (V_adc - V_offset) / (Rshunt * Gain)
 * I(мА) = (raw * VREF / 4095 - V_offset_мВ) * 1000 / (Rshunt_Ом * Gain)
 *
 * Параметры тракта (из OEW_FOC_DOC.md / STEVAL_IPM20B_SETUP.md):
 *   Rshunt = 0.03 Ом, Gain = 2.1  →  Rm = 0.063 В/А
 *   Vref = 3.3 В, 12-бит АЦП     →  1 код = 0.806 мВ
 *
 * 1 код → 0.000806 В / 0.063 В/А = 0.01279 А = 12.79 мА
 * Округляем: (diff * 128) / 10 = diff * 12.8  (погрешность <0.1%).
 */
static int32_t calc_current_st(uint16_t raw, uint16_t offset) {
    int32_t diff = (int32_t)raw - (int32_t)offset;
    return (diff * 128) / 10;   // 1 код ≈ 12.8 мА
}

/* ── Расчёт нулевого тока (трансформатор, заглушка) ──────────────────── */
static int32_t calc_current_nct(uint16_t raw, uint16_t offset) {
    int32_t diff = (int32_t)raw - (int32_t)offset;
    /* Заглушка: 50 мВ/А, смещение 1.65В */
    /* diff * 3300 / 4095 / 0.05 * 1000 = diff * 3300 * 1000 / 4095 / 50 */
    return (int32_t)(((int64_t)diff * 3300 * 1000) / (4095 * 50));
}

/* ── Расчёт Vbus ────────────────────────────────────────────────────── */
static int32_t calc_vbus(uint16_t raw) {
    /* Напряжение на пине = raw * 3300 / 4095, мВ */
    /* Vbus = напряжение * делитель (125) */
    return ((int32_t)raw * 3300 * (int32_t)VBUS_DIVIDER) / 4095;
}

/* ── Публичные функции ────────────────────────────────────────────────── */

void ADC_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN;
    volatile uint32_t d = 10000; while(d--);
    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST;
    RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST;
    d = 1000; while(d--);
    ADC12_COMMON->CCR = (1U << 16);
    ADC2->CR = 0;
    ADC2->CR &= ~ADC_CR_DEEPPWD;
    ADC2->CR |= ADC_CR_ADVREGEN;
    d = 100000; while(d--);
    ADC2->CR |= ADC_CR_ADCAL;
    uint32_t t = 1000000;
    while(ADC2->CR & ADC_CR_ADCAL) { if(--t == 0) return; }
    ADC2->CFGR = 0;
    /* SMP: IN1, IN2, IN3, IN5 */
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
    adc_data.raw_in   = adc2_read(3);
    adc_data.raw_vbus = adc2_read(5);
}

/* Калибровка нулей токовых каналов. Вызывать только при выключенном
 * инверторе (FOC_Start до PWM_Enable) — токи должны быть истинно нулевыми.
 * Усреднение по 8 выборкам — подавление шума. */
void ADC_CalibrateOffsets(void) {
    uint32_t s1 = 0, s2 = 0, sn = 0;
    for(int i = 0; i < 8; i++) {
        s1 += adc2_read(1);
        s2 += adc2_read(2);
        sn += adc2_read(3);
    }
    adc_data.offset_i1 = (uint16_t)(s1 / 8);
    adc_data.offset_i2 = (uint16_t)(s2 / 8);
    adc_data.offset_in = (uint16_t)(sn / 8);
}

void ADC_WaitForEOC(void) { /* все синхронно */ }

/* ── Injected group: аппаратный запуск от TIM1_TRGO ────────────────────
 * RM0440 §22.4.13: ADC_JSQR настраивает injected-группу.
 * JEXTSEL=00000: TIM1_TRGO (update event от TIM1 в center-aligned mode).
 * JEXTEN=01: rising edge.
 * JL=11: 4 преобразования (rank 1..4).
 * Каналы: ch1=I1, ch2=I2, ch3=IN, ch5=Vbus.
 * После JADSTART ADC ждёт триггер от TIM1 — нулевой джиттер выборки. */
void ADC_InjectedInit(void) {
    ADC2->CFGR |= ADC_CFGR_JQDIS;   /* отключить queue — проще, детерминированно */
    ADC2->JSQR = (3U << ADC_JSQR_JL_Pos)            /* JL=3: 4 conversions */
               | (0U << ADC_JSQR_JEXTSEL_Pos)        /* JEXTSEL=0: TIM1_TRGO */
               | (1U << ADC_JSQR_JEXTEN_Pos)         /* JEXTEN=01: rising edge */
               | (1U << ADC_JSQR_JSQ1_Pos)           /* rank 1: ch1 = I1 */
               | (2U << ADC_JSQR_JSQ2_Pos)           /* rank 2: ch2 = I2 */
               | (3U << ADC_JSQR_JSQ3_Pos)           /* rank 3: ch3 = IN */
               | (5U << ADC_JSQR_JSQ4_Pos);          /* rank 4: ch5 = Vbus */
    ADC2->IER |= ADC_IER_JEOSIE;    /* прерывание по end-of-sequence */
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
    adc_data.raw_in   = (uint16_t)ADC2->JDR3;
    adc_data.raw_vbus = (uint16_t)ADC2->JDR4;
}

uint16_t ADC_GetRawI1(void)   { return adc_data.raw_i1; }
uint16_t ADC_GetRawI2(void)   { return adc_data.raw_i2; }
uint16_t ADC_GetRawIN(void)   { return adc_data.raw_in; }
uint16_t ADC_GetRawVbus(void) { return adc_data.raw_vbus; }
uint16_t ADC_GetOffset(void)  { return adc_data.offset_i1; }

int32_t ADC_GetI1_mA(void) {
    return calc_current_st(adc_data.raw_i1, adc_data.offset_i1);
}

int32_t ADC_GetI2_mA(void) {
    return calc_current_st(adc_data.raw_i2, adc_data.offset_i2);
}

int32_t ADC_GetIN_mA(void) {
    return calc_current_nct(adc_data.raw_in, adc_data.offset_in);
}

int32_t ADC_GetVbus_mV(void) {
    return calc_vbus(adc_data.raw_vbus);
}
