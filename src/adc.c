#include "adc.h"
#include "stm32g474xx.h"

/* Буфер последних измерений */
static volatile struct {
    uint16_t raw_i1;
    uint16_t raw_i2;
    uint16_t raw_in;
    uint16_t raw_vbus;
    uint16_t offset;    // нулевой код (при 0 токе)
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
    (void)offset;
    int32_t diff = (int32_t)raw - (int32_t)offset;
    /* Заглушка: 50 мВ/А, смещение 1.65В */
    /* diff * 3300 / 4095 / 0.05 * 1000 = diff * 3300 * 1000 / 4095 / 50 */
    return (diff * 3300 * 1000) / (4095 * 50);
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

    /* Начальная калибровка offset (читаем при нулевом токе) */
    adc_data.offset = adc2_read(1);
}

void ADC_StartConversion(void) {
    adc_data.raw_i1   = adc2_read(1);
    adc_data.raw_i2   = adc2_read(2);
    adc_data.raw_in   = adc2_read(3);
    adc_data.raw_vbus = adc2_read(5);
}

void ADC_WaitForEOC(void) { /* все синхронно */ }

uint16_t ADC_GetRawI1(void)   { return adc_data.raw_i1; }
uint16_t ADC_GetRawI2(void)   { return adc_data.raw_i2; }
uint16_t ADC_GetRawIN(void)   { return adc_data.raw_in; }
uint16_t ADC_GetRawVbus(void) { return adc_data.raw_vbus; }
uint16_t ADC_GetOffset(void)  { return adc_data.offset; }

int32_t ADC_GetI1_mA(void) {
    return calc_current_st(adc_data.raw_i1, adc_data.offset);
}

int32_t ADC_GetI2_mA(void) {
    return calc_current_st(adc_data.raw_i2, adc_data.offset);
}

int32_t ADC_GetIN_mA(void) {
    return calc_current_nct(adc_data.raw_in, adc_data.offset);
}

int32_t ADC_GetVbus_mV(void) {
    return calc_vbus(adc_data.raw_vbus);
}
