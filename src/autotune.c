#include "autotune.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "foc.h"
#include "protect.h"
#include "stm32g474xx.h"
#include "cordic_math.h"
#include <string.h>

MotorParams g_motor_params;
volatile uint8_t g_autotune_abort = 0;

/* Последние расчётные Kp/Ki (для автоприменения через pi=N) */
static int32_t last_kp = 0;
static int32_t last_ki = 0;
static int     pi_calculated = 0;

/* ══════════════════════════════════════════════════════════════════════════
 *  Вспомогательные функции
 * ══════════════════════════════════════════════════════════════════════════ */

static void dwt_init(void) {
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;
}

static void delay_us(uint32_t us) {
    uint32_t start = DWT->CYCCNT;
    uint32_t ticks = (uint32_t)(((uint64_t)us * (SystemCoreClock / 1000000U)) & 0xFFFFFFFFU);
    uint32_t timeout = 100000000U;  /* guard against DWT stop */
    while ((DWT->CYCCNT - start) < ticks) {
        if (g_autotune_abort) return;
        if (--timeout == 0) return;
    }
}

static void tim1_enable(void) {
    TIM1->CCER |= TIM_CCER_CC1E | TIM_CCER_CC1NE
               |  TIM_CCER_CC2E | TIM_CCER_CC2NE
               |  TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1  |= TIM_CR1_CEN;
}

static void tim1_disable(void) {
    TIM1->CR1  &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER &= ~(TIM_CCER_CC1E | TIM_CCER_CC1NE
                  | TIM_CCER_CC2E | TIM_CCER_CC2NE
                  | TIM_CCER_CC3E | TIM_CCER_CC3NE);
}

static int32_t at_abs32(int32_t x) { return (x < 0) ? -x : x; }

static void sort_small(int32_t *a, uint8_t n) {
    for (uint8_t i = 1; i < n; i++) {
        int32_t x = a[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && a[j] > x) { a[j + 1] = a[j]; j--; }
        a[j + 1] = x;
    }
}

static int32_t median_small(int32_t *a, uint8_t n) {
    sort_small(a, n);
    return a[n / 2];
}

static void stat_compute(AtStat32 *s) {
    if (s->count == 0) { s->median = s->min = s->max = 0; s->spread_pct = 0; return; }
    int32_t tmp[5];
    memcpy(tmp, s->values, sizeof(int32_t) * s->count);
    sort_small(tmp, s->count);
    s->median = tmp[s->count / 2];
    s->min    = tmp[0];
    s->max    = tmp[s->count - 1];
    s->spread_pct = (s->median > 0)
        ? (int32_t)(((int64_t)(s->max - s->min) * 100) / s->median)
        : 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Автоопределение канала тока
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_DetectChannel(void) {
    UART_SendStr("@AT:CH_DETECT:START\r\n");
    if (FOC_IsRunning()) FOC_Stop();

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();

    PWM_SetDuty1(0, 0, 0);
    tim1_enable();

    ADC_StartConversion();
    int32_t i1_zero = ADC_GetI1_mA();
    int32_t i2_zero = ADC_GetI2_mA();
    int32_t in_zero = ADC_GetIres_mA();
    UART_SendTelemetry("@DBG:CH:ZERO:raw_i1=%u:raw_i2=%u:raw_ires=%u:raw_vbus=%u\r\n",
                       ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
    UART_SendTelemetry("@DBG:CH:ZERO:mA:i1=%ld:i2=%ld:ires=%ld:vbus=%ldmV\r\n",
                       (long)i1_zero, (long)i2_zero, (long)in_zero, (long)ADC_GetVbus_mV());

    PWM_SetDuty1(5, 0, 0);
    delay_us(300);

    ADC_StartConversion();
    int32_t i1_test = ADC_GetI1_mA();
    int32_t i2_test = ADC_GetI2_mA();
    int32_t in_test = ADC_GetIres_mA();
    UART_SendTelemetry("@DBG:CH:TEST:raw_i1=%u:raw_i2=%u:raw_ires=%u:raw_vbus=%u\r\n",
                       ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
    UART_SendTelemetry("@DBG:CH:TEST:mA:i1=%ld:i2=%ld:ires=%ld:vbus=%ldmV\r\n",
                       (long)i1_test, (long)i2_test, (long)in_test, (long)ADC_GetVbus_mV());

    PWM_SetDuty1(0, 0, 0);
    tim1_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);

    int32_t d_i1 = at_abs32(i1_test - i1_zero);
    int32_t d_i2 = at_abs32(i2_test - i2_zero);
    int32_t d_in = at_abs32(in_test - in_zero);
    UART_SendTelemetry("@DBG:CH:DELTA:d_i1=%ld:d_i2=%ld:d_ires=%ld\r\n",
                       (long)d_i1, (long)d_i2, (long)d_in);

    int32_t best = d_i1;
    AtCurrentChannel ch = AT_CH_I1;
    int32_t signed_current = i1_test - i1_zero;
    if (d_i2 > best) { best = d_i2; ch = AT_CH_I2; signed_current = i2_test - i2_zero; }
    if (d_in > best) { best = d_in; ch = AT_CH_IN; signed_current = in_test - in_zero; }

    if (best < 30) {
        UART_SendStr("@AT:CH_DETECT:ERROR:NO_CURRENT\r\n");
        return -1;
    }

    g_motor_params.current_channel = ch;
    g_motor_params.current_sign    = (signed_current >= 0) ? 1 : -1;

    UART_SendTelemetry("@AT:CH_DETECT:OK:CH=%d:I=%ld:SIGN=%ld\r\n",
                       (int)ch, (long)best, (long)g_motor_params.current_sign);
    return 0;
}

static int32_t AT_ReadCurrent_mA(void) {
    int32_t i = 0;
    switch (g_motor_params.current_channel) {
        case AT_CH_I1: i = ADC_GetI1_mA(); break;
        case AT_CH_I2: i = ADC_GetI2_mA(); break;
        case AT_CH_IN: i = ADC_GetIres_mA(); break;
        default:       i = ADC_GetIres_mA(); break;
    }
    return i * g_motor_params.current_sign;
}

static int32_t AT_ReadCurrentMedian_mA(void) {
    int32_t s[5];
    for (uint8_t k = 0; k < 5; k++) {
        ADC_StartConversion();
        s[k] = AT_ReadCurrent_mA();
    }
    return median_small(s, 5);
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Проверка безопасности
 * ══════════════════════════════════════════════════════════════════════════ */
static int8_t AT_SafetyCheck(void) {
    /* ВНИМАНИЕ: если Inv1 и Inv2 запитаны от РАЗДЕЛЬНЫХ изолированных
     * источников, ток freewheeling через диоды пассивного Inv2 будет
     * заряжать конденсатор шины Inv2 без пути разряда. За 250 циклов
     * теста (50 duty * 5 повторов) есть риск перенапряжения на шине Inv2.
     * Убедитесь, что оба инвертора сидят на одной шине DC, или
     * ограничьте число циклов / добавьте bleed-резистор на шину Inv2. */
    if (PROTECT_IsFault()) {
        UART_SendStr("@AT:ERROR:FAULT_CLEAR_FIRST\r\n");
        return -1;
    }

    ADC_StartConversion();
    int32_t vbus = ADC_GetVbus_mV();
    if (vbus < 12000) {
        UART_SendTelemetry("@AT:ERROR:VBUS_LOW:%ld\r\n", (long)vbus);
        return -2;
    }

    int32_t i1 = at_abs32(ADC_GetI1_mA());
    int32_t i2 = at_abs32(ADC_GetI2_mA());
    int32_t in = at_abs32(ADC_GetIres_mA());
    if (i1 > 150 || i2 > 150 || in > 150) {
        UART_SendTelemetry("@AT:ERROR:NONZERO_CURRENT:I1=%ld:I2=%ld:Ires=%ld\r\n",
                           (long)i1, (long)i2, (long)in);
        return -3;
    }
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Адаптивное измерение Ls
 * ══════════════════════════════════════════════════════════════════════════ */
static int32_t AT_MeasureLs_uH(int32_t U_mV, uint16_t duty_pct, uint32_t period) {
    (void)period;
    uint32_t dt_us = 100;
    int32_t di_avg = 0;

    for (uint8_t attempt = 0; attempt < 5; attempt++) {
        int32_t di_samples[4];
        uint8_t good = 0;

        for (uint8_t k = 0; k < 4; k++) {
            if (g_autotune_abort) return 0;
            TIM1->CCR1 = 0;
            delay_us(dt_us);
            ADC_StartConversion();
            int32_t i_lo = AT_ReadCurrent_mA();

            TIM1->CCR1 = (uint16_t)(((uint32_t)duty_pct * ((uint32_t)PWM_GetARR() + 1U)) / 100U);
            delay_us(dt_us);
            ADC_StartConversion();
            int32_t i_hi = AT_ReadCurrent_mA();

            int32_t di = i_hi - i_lo;
            if (di > 0) di_samples[good++] = di;
        }

        if (good == 0) { di_avg = 0; }
        else           { di_avg = median_small(di_samples, good); }

        if (di_avg < AT_DI_TARGET_MIN_MA && dt_us < 500) { dt_us *= 2; continue; }
        if (di_avg > AT_DI_TARGET_MAX_MA && dt_us > 20)  { dt_us /= 2; continue; }
        break;
    }

    if (di_avg <= 10) return 0;
    /* OEW: ток через одну обмотку (Inv1→обмотка→Inv2/диод→DC-).
     * Для звезды раскомментируйте /2 (две обмотки последовательно). */
    return (int32_t)(((int64_t)U_mV * dt_us) / di_avg);
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Multi-point Rs
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureRs_IV(void) {
    static const uint8_t duties[] = { 2, 4, 6, 8, 10, 12, 15 };
    const uint8_t n = sizeof(duties) / sizeof(duties[0]);

    UART_SendStr("@AT:RS_IV:START\r\n");
    g_autotune_abort = 0;

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();

    PWM_SetDuty1(0, 0, 0);
    tim1_enable();

    int32_t U[7], I[7];
    uint8_t valid_points = 0;
    int32_t vbus_initial = ADC_GetVbus_mV();

    for (uint8_t k = 0; k < n; k++) {
        if (g_autotune_abort) {
            PWM_SetDuty1(0, 0, 0); tim1_disable();
            NVIC_EnableIRQ(ADC1_2_IRQn);
            UART_SendStr("@AT:RS_IV:ABORTED\r\n");
            return -5;
        }

        PWM_SetDuty1(duties[k], 0, 0);
        delay_us(1000);

        int32_t i = AT_ReadCurrentMedian_mA();
        if (i < 0) i = -i;

        if (i > AUTOTUNE_MAX_CURRENT_MA) {
            PWM_SetDuty1(0, 0, 0); tim1_disable();
            NVIC_EnableIRQ(ADC1_2_IRQn);
            UART_SendTelemetry("@AT:RS_IV:ERROR:OVERCURRENT I=%ld\r\n", (long)i);
            return -6;
        }

        ADC_StartConversion();
        int32_t vbus_now = ADC_GetVbus_mV();

        if (vbus_now < vbus_initial * 85 / 100) {
            UART_SendTelemetry("@AT:WARN:VBUS_SAG:%ld:%ld\r\n", (long)vbus_now, (long)vbus_initial);
        }

        U[valid_points] = (int32_t)(((int64_t)vbus_now * duties[k]) / 100U);
        I[valid_points] = i;
        valid_points++;

        UART_SendTelemetry("@AT:RS_IV:POINT:D=%u:U=%ld:I=%ld\r\n",
                           (unsigned)duties[k], (long)U[k], (long)I[k]);
    }

    PWM_SetDuty1(0, 0, 0);
    tim1_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);

    if (valid_points < 3) {
        UART_SendStr("@AT:RS_IV:ERROR:TOO_FEW_POINTS\r\n");
        return -7;
    }

    int64_t sum_i = 0, sum_u = 0;
    for (uint8_t k = 0; k < valid_points; k++) { sum_i += I[k]; sum_u += U[k]; }
    int64_t mean_i = sum_i / valid_points;
    int64_t mean_u = sum_u / valid_points;

    int64_t num = 0, den = 0;
    for (uint8_t k = 0; k < valid_points; k++) {
        int64_t di = (int64_t)I[k] - mean_i;
        int64_t du = (int64_t)U[k] - mean_u;
        num += du * di;
        den += di * di;
    }

    if (den == 0) {
        UART_SendStr("@AT:RS_IV:ERROR:NO_CURRENT_SPREAD\r\n");
        return -8;
    }

    int64_t r_pp_mohm = (num * 1000) / den;
    /* OEW: r_pp_mohm — сопротивление одной обмотки (Inv1→обмотка→Inv2/диод).
     * Для звезды здесь было бы /2 (две обмотки последовательно). */
    g_motor_params.Rs_mOhm = (int32_t)r_pp_mohm;

    UART_SendTelemetry("@AT:RS_IV:OK:Rs=%ld\r\n", (long)g_motor_params.Rs_mOhm);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Измерение одной пары фаз
 * ══════════════════════════════════════════════════════════════════════════ */
static int8_t AT_MeasurePair(uint8_t pair_idx, AtPairResult *out) {
    uint16_t arr    = PWM_GetARR();
    uint32_t period = (uint32_t)arr + 1U;
    int32_t  vbus   = ADC_GetVbus_mV();

    out->valid  = 0;
    out->Rs_mOhm = 0;
    out->Ls_uH   = 0;
    out->Isat_ma = 0;

    uint16_t duty_ref = 10;

    switch (pair_idx) {
        case 0: PWM_SetDuty1(duty_ref, 0, 0); break;
        case 1: PWM_SetDuty1(0, duty_ref, 0); break;
        case 2: PWM_SetDuty1(0, 0, duty_ref); break;
    }
    delay_us(1000);

    int32_t I_ss_ref = AT_ReadCurrentMedian_mA();
    if (I_ss_ref < 0) I_ss_ref = -I_ss_ref;

    if (I_ss_ref < 50) {
        UART_SendTelemetry("@AT:PAIR:%u:ERROR:OPEN_PHASE I=%ld\r\n", (unsigned)pair_idx, (long)I_ss_ref);
        PWM_SetDuty1(0, 0, 0);
        return -2;
    }
    if (I_ss_ref > AUTOTUNE_MAX_CURRENT_MA) {
        UART_SendTelemetry("@AT:PAIR:%u:ERROR:SHORT I=%ld\r\n", (unsigned)pair_idx, (long)I_ss_ref);
        PWM_SetDuty1(0, 0, 0);
        return -3;
    }

    int32_t U_ref = (int32_t)(((int64_t)vbus * duty_ref) / 100U);
    out->Rs_mOhm = (int32_t)(((int64_t)U_ref * 1000) / I_ss_ref);

    int32_t max_Ls = 0;
    for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); return -4; }

        switch (pair_idx) {
            case 0: PWM_SetDuty1(duty_pct, 0, 0); break;
            case 1: PWM_SetDuty1(0, duty_pct, 0); break;
            case 2: PWM_SetDuty1(0, 0, duty_pct); break;
        }
        delay_us(500);

        int32_t I_ss = AT_ReadCurrentMedian_mA();
        if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { PWM_SetDuty1(0, 0, 0); return -5; }

        int32_t U_applied = (int32_t)(((int64_t)vbus * duty_pct) / 100U);
        int32_t Ls_uH = AT_MeasureLs_uH(U_applied, duty_pct, period);
        if (Ls_uH > max_Ls) max_Ls = Ls_uH;
    }

    out->Ls_uH = max_Ls;
    out->valid = 1;
    PWM_SetDuty1(0, 0, 0);

    UART_SendTelemetry("@AT:PAIR:%u:Rs=%ld:Ls=%ld\r\n", (unsigned)pair_idx, (long)out->Rs_mOhm, (long)out->Ls_uH);
    return 0;
}

int8_t Autotune_MeasureAllPairs(void) {
    UART_SendStr("@AT:PAIRS:START\r\n");
    g_autotune_abort = 0;

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();

    PWM_SetDuty1(0, 0, 0);
    tim1_enable();

    int64_t sum_Rs = 0, sum_Ls = 0;
    uint8_t valid_count = 0;

    for (uint8_t p = 0; p < 3; p++) {
        if (g_autotune_abort) { tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:PAIRS:ABORTED\r\n"); return -5; }
        rc = AT_MeasurePair(p, &g_motor_params.pairs[p]);
        if (rc == 0 && g_motor_params.pairs[p].valid) {
            sum_Rs += g_motor_params.pairs[p].Rs_mOhm;
            sum_Ls += g_motor_params.pairs[p].Ls_uH;
            valid_count++;
        }
    }

    tim1_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);

    if (valid_count == 0) { UART_SendStr("@AT:PAIRS:ERROR:ALL_FAILED\r\n"); return -6; }

    g_motor_params.Rs_mOhm = (int32_t)(sum_Rs / valid_count);
    g_motor_params.Ls_uH   = (int32_t)(sum_Ls / valid_count);

    int32_t Rs_min = g_motor_params.pairs[0].Rs_mOhm;
    int32_t Rs_max = Rs_min;
    for (uint8_t p = 1; p < 3; p++) {
        if (g_motor_params.pairs[p].Rs_mOhm < Rs_min) Rs_min = g_motor_params.pairs[p].Rs_mOhm;
        if (g_motor_params.pairs[p].Rs_mOhm > Rs_max) Rs_max = g_motor_params.pairs[p].Rs_mOhm;
    }
    int32_t asym_pct = (g_motor_params.Rs_mOhm > 0)
        ? (int32_t)(((int64_t)(Rs_max - Rs_min) * 100) / g_motor_params.Rs_mOhm) : 0;

    UART_SendTelemetry("@AT:PAIRS:OK:Rs=%ld:Ls=%ld:ASYM=%ld%%\r\n",
                       (long)g_motor_params.Rs_mOhm, (long)g_motor_params.Ls_uH, (long)asym_pct);
    if (asym_pct > 10) UART_SendTelemetry("@AT:WARN:ASYMMETRY_HIGH:%ld%%\r\n", (long)asym_pct);
    Autotune_PrintPairs();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  STATIC IDLE TEST — полная версия
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_Idle(void) {
    UART_SendStr("@IDLE:START\r\n");
    g_autotune_abort = 0;

    if (FOC_IsRunning()) FOC_Stop();

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();

    uint16_t arr    = PWM_GetARR();
    uint32_t period = (uint32_t)arr + 1U;

    g_motor_params.curve_count = 0;  /* Сброс кривой один раз перед всеми повторами */

    for (uint8_t rep = 0; rep < 5; rep++) {
        if (g_autotune_abort) { tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

        int32_t max_Ls = 0;
        int32_t Rs_this = 0;

        PWM_SetDuty1(0, 0, 0);
        tim1_enable();

        for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
            if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

            PWM_SetDuty1(duty_pct, 0, 0);
            delay_us(500);

            int32_t I_ss = AT_ReadCurrentMedian_mA();
            if (I_ss < 0) I_ss = -I_ss;

            if (I_ss > AUTOTUNE_MAX_CURRENT_MA) {
                PWM_SetDuty1(0, 0, 0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendTelemetry("@IDLE:ERROR:OVERCURRENT I=%ld\r\n", (long)I_ss); return -6;
            }
            if (duty_pct >= 20 && I_ss < 30) {
                PWM_SetDuty1(0, 0, 0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendStr("@IDLE:ERROR:OPEN_PHASE\r\n"); return -7;
            }
            if (duty_pct <= 2 && I_ss > 3000) {
                PWM_SetDuty1(0, 0, 0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendStr("@IDLE:ERROR:SHORT_OR_LOW_RS\r\n"); return -8;
            }

            int32_t U_applied = (int32_t)(((int64_t)ADC_GetVbus_mV() * duty_pct) / 100U);
            int32_t Ls_uH = AT_MeasureLs_uH(U_applied, duty_pct, period);
            if (Ls_uH > max_Ls) max_Ls = Ls_uH;

            if (rep == 0 && g_motor_params.curve_count < 64 && I_ss > 100) {
                g_motor_params.curve[g_motor_params.curve_count].current_ma    = I_ss;
                g_motor_params.curve[g_motor_params.curve_count].inductance_uH = Ls_uH;
                g_motor_params.curve_count++;
            }

            if (duty_pct == 10 && I_ss > 50) {
                Rs_this = (int32_t)(((int64_t)U_applied * 1000) / I_ss);
            }

            if ((duty_pct % 5) == 0) {
                UART_SendTelemetry("@IDLE:PROG=%u/50:D=%u:I=%ld:L=%ld:REP=%u/%u\r\n",
                                   (unsigned)duty_pct, (unsigned)duty_pct,
                                   (long)I_ss, (long)Ls_uH,
                                   (unsigned)(rep + 1), (unsigned)5);
            }
        }

        PWM_SetDuty1(0, 0, 0);
        tim1_disable();

        if (rep < 5) {
            g_motor_params.Rs_stat.values[rep]   = Rs_this;
            g_motor_params.Ls_stat.values[rep]   = max_Ls;
            g_motor_params.Rs_stat.count   = rep + 1;
            g_motor_params.Ls_stat.count   = rep + 1;
        }
    }

    NVIC_EnableIRQ(ADC1_2_IRQn);

    stat_compute(&g_motor_params.Rs_stat);
    stat_compute(&g_motor_params.Ls_stat);

    g_motor_params.Rs_mOhm = g_motor_params.Rs_stat.median;
    g_motor_params.Ls_uH   = g_motor_params.Ls_stat.median;

    /* Isat: вычисляется один раз после всех повторов,
     * по медиане Ls и кривой насыщения (собранной на rep==0).
     * Раньше вычислялось только на rep==0, а для reps 1-4 было 0,
     * медиана [X,0,0,0,0] = 0 — измерение насыщения не работало. */
    g_motor_params.Isat_ma = 0;
    if (g_motor_params.Ls_uH > 0 && g_motor_params.curve_count > 0) {
        int32_t threshold = g_motor_params.Ls_uH * 70 / 100;
        for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
            if (g_motor_params.curve[i].inductance_uH <= threshold) {
                g_motor_params.Isat_ma = g_motor_params.curve[i].current_ma;
                break;
            }
        }
    }

    UART_SendStr("@IDLE:DONE\r\n");
    Autotune_PrintStats();
    Autotune_PrintParams();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Заглушки
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_Irot(void) {
    UART_SendStr("@IROT:START\r\n");
    if (FOC_IsRunning()) FOC_Stop();
    if (PROTECT_IsFault()) { UART_SendStr("@IROT:ERROR:FAULT\r\n"); return -1; }
    UART_SendStr("@IROT:DONE (stub)\r\n");
    return 0;
}

int8_t Autotune_Inertia(void) {
    UART_SendStr("@INERTIA:START\r\n");
    if (!FOC_IsRunning()) { UART_SendStr("@INERTIA:ERROR:FOC_NOT_RUNNING\r\n"); return -1; }
    UART_SendStr("@INERTIA:DONE (stub)\r\n");
    return 0;
}


/* ══════════════════════════════════════════════════════════════════════════
 *  Вспомогательные: управление обоими инверторами + синус
 * ══════════════════════════════════════════════════════════════════════════ */

static void tim8_enable(void) {
    TIM8->CCER |= TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM8->CR1  |= TIM_CR1_CEN;
}

static void tim8_disable(void) {
    TIM8->CR1  &= ~TIM_CR1_CEN;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM8->CCER &= ~(TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE);
}

static void both_enable(void) {
    tim1_enable(); tim8_enable();
}

static void both_disable(void) {
    PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(0, 0, 0);
    tim1_disable(); tim8_disable();
}

static int32_t at_sin_q15(int32_t angle_x1000) {
    /* millirad (0..6283 = 0..2π) → q31 (0x7FFFFFFF = π).
     * Используем аппаратный CORDIC — точность 2^-18 ≈ 0.0004°
     * вместо таблицы 64 точки (0.1°). */
    angle_x1000 %= 6283;
    if (angle_x1000 < 0) angle_x1000 += 6283;
    int32_t q31 = (int32_t)(((int64_t)angle_x1000 * 2147483647LL) / 3142);
    int32_t s, c;
    CORDIC_SinCos(q31, &s, &c);
    return s;
}


/* ══════════════════════════════════════════════════════════════════════════
 *  1. OEW: измерение Ls через оба инвертора в противофазе
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureLs_OEW(void) {
    UART_SendStr("@AT:OEW:START\r\n"); g_autotune_abort = 0;
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    NVIC_DisableIRQ(ADC1_2_IRQn); PWM_Disable(); ADC_CalibrateOffsets(); dwt_init();
    uint16_t arr = PWM_GetARR(); uint32_t period = (uint32_t)arr + 1U; int32_t vbus = ADC_GetVbus_mV();
    PWM_SetDuty1(0,0,0); PWM_SetDuty2(0,0,0); both_enable();
    int32_t max_Ls_oew = 0; uint8_t ci = 0;
    for (uint16_t d = 1; d <= 50; d++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:OEW:ABORTED\r\n"); return -5; }
        PWM_SetDuty1(d,0,0); PWM_SetDuty2((uint16_t)(100U-d),0,0);
        delay_us(500);
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:OEW:ERROR:OVERCURRENT I=%ld\r\n",(long)I_ss); return -6; }
        uint32_t saved_ccmr1_1 = TIM1->CCMR1; uint32_t saved_ccmr1_8 = TIM8->CCMR1;
        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE; TIM8->CCMR1 &= ~TIM_CCMR1_OC1PE;
        uint16_t half = (uint16_t)(period / 2U);
        uint16_t ccr  = (uint16_t)(((uint32_t)d * period) / 100U);
        uint32_t dt_us = 100;
        int32_t di_avg = 0;
        for (uint8_t attempt = 0; attempt < 5; attempt++) {
            int32_t di_sum = 0; uint8_t good = 0;
            for (uint8_t k = 0; k < 4; k++) {
                TIM1->CCR1 = half; TIM8->CCR1 = half; delay_us(dt_us);
                ADC_StartConversion(); int32_t i_lo = AT_ReadCurrent_mA();
                TIM1->CCR1 = (uint16_t)(half + ccr); TIM8->CCR1 = (uint16_t)(half - ccr); delay_us(dt_us);
                ADC_StartConversion(); int32_t i_hi = AT_ReadCurrent_mA();
                int32_t di = i_hi - i_lo; if (di > 0) { di_sum += di; good++; }
            }
            di_avg = (good > 0) ? (di_sum / good) : 0;
            if (di_avg < AT_DI_TARGET_MIN_MA && dt_us < 500) { dt_us *= 2; continue; }
            if (di_avg > AT_DI_TARGET_MAX_MA && dt_us > 20)  { dt_us /= 2; continue; }
            break;
        }
        TIM1->CCMR1 = saved_ccmr1_1; TIM8->CCMR1 = saved_ccmr1_8;
        if (di_avg <= 10) di_avg = 1;
        int32_t U_eff = (int32_t)(((int64_t)vbus * d * 2) / 100U);
        int32_t Ls_oew = (int32_t)(((int64_t)U_eff * dt_us) / di_avg);
        if (Ls_oew > max_Ls_oew) max_Ls_oew = Ls_oew;
        if (ci < 64 && I_ss > 100) { g_motor_params.curve[ci].current_ma = I_ss; g_motor_params.curve[ci].inductance_uH = Ls_oew; ci++; }
        if ((d % 10) == 0) UART_SendTelemetry("@AT:OEW:PROG=%u/50:D=%u:I=%ld:L=%ld\r\n",(unsigned)d,(unsigned)d,(long)I_ss,(long)Ls_oew);
    }
    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
    g_motor_params.curve_count = ci; g_motor_params.Ls_uH = max_Ls_oew;
    UART_SendTelemetry("@AT:OEW:OK:Ls=%ld\r\n",(long)max_Ls_oew); Autotune_PrintCurve();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  2. Rr: синусоида 5 Гц на заблокированном валу
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureRr(void) {
    UART_SendStr("@AT:RR:START:LOCK_ROTOR\r\n"); g_autotune_abort = 0;
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    if (g_motor_params.Rs_mOhm <= 0) { UART_SendStr("@AT:RR:ERROR:RS_NOT_MEASURED\r\n"); return -5; }
    NVIC_DisableIRQ(ADC1_2_IRQn); PWM_Disable(); ADC_CalibrateOffsets(); dwt_init();
    PWM_SetDuty1(0,0,0); tim1_enable();
    int64_t p_sum = 0, i_sq_sum = 0;
    int32_t theta = 0; const int32_t n_pts = 2000; int32_t vbus = ADC_GetVbus_mV();
    for (int32_t i = 0; i < n_pts; i++) {
        if (g_autotune_abort) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:RR:ABORTED\r\n"); return -6; }
        theta += 31; if (theta >= 6283) theta -= 6283;
        int32_t sa = at_sin_q15(theta);
        int32_t sb = at_sin_q15(theta - 2094);
        int32_t sc = at_sin_q15(theta + 2094);
        int32_t da = 50 + (int32_t)(((int64_t)sa * 20) / 32768);
        int32_t db = 50 + (int32_t)(((int64_t)sb * 20) / 32768);
        int32_t dc = 50 + (int32_t)(((int64_t)sc * 20) / 32768);
        if (da < 0) da = 0; if (da > 100) da = 100;
        if (db < 0) db = 0; if (db > 100) db = 100;
        if (dc < 0) dc = 0; if (dc > 100) dc = 100;
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(1000);
        ADC_StartConversion(); int32_t i_ma = AT_ReadCurrent_mA();
        if (at_abs32(i_ma) > AUTOTUNE_MAX_CURRENT_MA) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:RR:ERROR:OVERCURRENT I=%ld\r\n",(long)i_ma); return -7; }
        int32_t u_inst = (int32_t)(((int64_t)vbus * da) / 100U);
        p_sum += (int64_t)u_inst * i_ma; i_sq_sum += (int64_t)i_ma * i_ma;
        if ((i % 500) == 0) UART_SendTelemetry("@AT:RR:PROG=%ld/%ld:I=%ld\r\n",(long)i,(long)n_pts,(long)i_ma);
    }
    PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
    if (i_sq_sum == 0) { UART_SendStr("@AT:RR:ERROR:NO_CURRENT\r\n"); return -8; }
    int32_t r_total_pp = (int32_t)((p_sum * 1000) / i_sq_sum);
    /* OEW: r_total_pp = Rs + Rr' (одна обмотка статора + приведённый ротор).
     * Для звезды было бы /2 (две обмотки статора в петле). */
    int32_t rs_pp = g_motor_params.Rs_mOhm;
    if (r_total_pp > rs_pp) g_motor_params.Rr_mOhm = (r_total_pp - rs_pp);
    else { g_motor_params.Rr_mOhm = 0; UART_SendStr("@AT:RR:WARN:RR_LESS_THAN_RS\r\n"); }
    UART_SendTelemetry("@AT:RR:OK:Rr=%ld:Rtotal=%ld\r\n",(long)g_motor_params.Rr_mOhm,(long)r_total_pp);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  3. Lm/Lr: холостой ход с V/f разгоном до 50 Гц
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureNoLoad(void) {
    UART_SendStr("@AT:NOLOAD:START:FREE_ROTOR\r\n"); g_autotune_abort = 0;
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    if (g_motor_params.Ls_uH <= 0) { UART_SendStr("@AT:NOLOAD:ERROR:LS_NOT_MEASURED\r\n"); return -5; }
    NVIC_DisableIRQ(ADC1_2_IRQn); PWM_Disable(); ADC_CalibrateOffsets(); dwt_init();
    PWM_SetDuty1(0,0,0); tim1_enable();
    int32_t vbus = ADC_GetVbus_mV(); int32_t theta = 0;

    UART_SendStr("@AT:NOLOAD:RAMP:START\r\n");
    for (int32_t f_mHz = 0; f_mHz <= 50000; f_mHz += 100) {
        if (g_autotune_abort) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:NOLOAD:ABORTED\r\n"); return -6; }
        int32_t v_mag = (int32_t)(((int64_t)f_mHz * 80) / 50000);
        theta += (int32_t)(((int64_t)6283 * f_mHz) / 500000);
        if (theta >= 6283) theta -= 6283;
        int32_t sa = at_sin_q15(theta);
        int32_t da = 50 + (int32_t)(((int64_t)sa * v_mag) / 32768);
        if (da < 0) da = 0; if (da > 100) da = 100;
                    int32_t sb = at_sin_q15(theta - 2094);
            int32_t sc = at_sin_q15(theta + 2094);
            int32_t db = 50 + (int32_t)(((int64_t)sb * v_mag) / 32768);
            int32_t dc = 50 + (int32_t)(((int64_t)sc * v_mag) / 32768);
            if (db < 0) db = 0; if (db > 100) db = 100;
            if (dc < 0) dc = 0; if (dc > 100) dc = 100;
            PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(2000);
        if ((f_mHz % 5000) == 0) UART_SendTelemetry("@AT:NOLOAD:RAMP:F=%ld:V=%ld%%\r\n",(long)(f_mHz/1000),(long)v_mag);
    }
    UART_SendStr("@AT:NOLOAD:MEASURE:START\r\n");
    int64_t i_sum = 0; const int32_t n_meas = 500;
    for (int32_t i = 0; i < n_meas; i++) {
        if (g_autotune_abort) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:NOLOAD:ABORTED\r\n"); return -6; }
        theta += 628; if (theta >= 6283) theta -= 6283;
        int32_t sa = at_sin_q15(theta);
        int32_t da = 50 + (int32_t)(((int64_t)sa * 80) / 32768);
        if (da < 0) da = 0; if (da > 100) da = 100;
                    int32_t sb = at_sin_q15(theta - 2094);
            int32_t sc = at_sin_q15(theta + 2094);
            int32_t db = 50 + (int32_t)(((int64_t)sb * 80) / 32768);
            int32_t dc = 50 + (int32_t)(((int64_t)sc * 80) / 32768);
            if (db < 0) db = 0; if (db > 100) db = 100;
            if (dc < 0) dc = 0; if (dc > 100) dc = 100;
            PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(2000);
        ADC_StartConversion(); int32_t im = at_abs32(AT_ReadCurrent_mA());
        i_sum += im;
    }
    PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
    int32_t i_avg = (int32_t)(i_sum / n_meas);
    /* mean(|I|) → Irms: для синуса mean(|I|)=2*Ip/π, Irms=Ip/√2.
     * Irms = mean(|I|) * π/(2√2) ≈ mean(|I|) * 1.1107.
     * Раньше было *0.707 (1/√2) — неправильно, давало Irms на 36% меньше. */
    int32_t i_rms = (int32_t)(((int64_t)i_avg * 1107) / 1000);
    if (i_rms < 10) { UART_SendStr("@AT:NOLOAD:ERROR:NO_CURRENT\r\n"); return -7; }
    int32_t v_rms = (int32_t)(((int64_t)vbus * 80 * 707) / (100 * 1000));
    int32_t z_total = (int32_t)(((int64_t)v_rms * 1000) / i_rms);
    int32_t l_total = (int32_t)(((int64_t)z_total * 1000) / 314);
    g_motor_params.Lm_uH = l_total - g_motor_params.Ls_uH;
    if (g_motor_params.Lm_uH < 0) g_motor_params.Lm_uH = 0;
    int32_t Lr_uH = g_motor_params.Lm_uH + g_motor_params.Ls_uH / 2;
    if (g_motor_params.Rr_mOhm > 0) g_motor_params.Tr_rotor_us = (int32_t)(((int64_t)Lr_uH * 1000) / g_motor_params.Rr_mOhm);
    UART_SendTelemetry("@AT:NOLOAD:OK:Irms=%ld:Z=%ld:Ltotal=%ld:Lm=%ld:Lr=%ld:Tr=%ld\r\n",(long)i_rms,(long)z_total,(long)l_total,(long)g_motor_params.Lm_uH,(long)Lr_uH,(long)g_motor_params.Tr_rotor_us);
    Autotune_PrintParams(); return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  4. Осциллограмма: 100 точек тока при duty=20%
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_Scope(void) {
    UART_SendStr("@SCOPE:START\r\n"); g_autotune_abort = 0;
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    NVIC_DisableIRQ(ADC1_2_IRQn); PWM_Disable(); ADC_CalibrateOffsets(); dwt_init();
    PWM_SetDuty1(0,0,0); tim1_enable();
    PWM_SetDuty1(20,0,0);
    for (int32_t i = 0; i < 100; i++) {
        if (g_autotune_abort) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@SCOPE:ABORTED\r\n"); return -5; }
        delay_us(20); ADC_StartConversion(); int32_t i_ma = AT_ReadCurrent_mA();
        UART_SendTelemetry("@SCOPE:T=%ld:I=%ld\r\n",(long)(i*20),(long)i_ma);
    }
    PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
    UART_SendStr("@SCOPE:DONE\r\n"); return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  5. Расчёт ПИ-регулятора из Ls и Rs
 * ══════════════════════════════════════════════════════════════════════════ */
void Autotune_CalcPI(int32_t bw_hz) {
    if (g_motor_params.Ls_uH <= 0 || g_motor_params.Rs_mOhm <= 0) { UART_SendStr("@AT:PI:ERROR:PARAMS_NOT_MEASURED\r\n"); return; }
    if (bw_hz < 100) bw_hz = 100; if (bw_hz > 5000) bw_hz = 5000;
    int64_t kp = ((int64_t)6283 * bw_hz * g_motor_params.Ls_uH) / (1732LL * 1000000LL);
    int64_t ki = ((int64_t)6283 * bw_hz * g_motor_params.Rs_mOhm) / (1732LL * 1000LL);
    last_kp = (int32_t)kp;
    last_ki = (int32_t)ki;
    pi_calculated = 1;
    UART_SendTelemetry("@AT:PI:BW=%ld:Kp=%ld:Ki=%ld:Ls=%ld:Rs=%ld\r\n",(long)bw_hz,(long)kp,(long)ki,(long)g_motor_params.Ls_uH,(long)g_motor_params.Rs_mOhm);
}

int Autotune_GetLastPI(int32_t *kp, int32_t *ki) {
    if(!pi_calculated) return -1;
    if(kp) *kp = last_kp;
    if(ki) *ki = last_ki;
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  6. Lσ от положения ротора: 6 замеров с поворотом вала
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureLs_Position(void) {
    UART_SendStr("@AT:LSPOS:START:TURN_ROTOR\r\n"); g_autotune_abort = 0;
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    NVIC_DisableIRQ(ADC1_2_IRQn); PWM_Disable(); ADC_CalibrateOffsets(); dwt_init();
    uint16_t arr = PWM_GetARR(); uint32_t period = (uint32_t)arr + 1U; int32_t vbus = ADC_GetVbus_mV();
    int32_t ls_vals[5]; uint8_t cnt = 0;
    for (uint8_t pos = 0; pos < 5; pos++) {
        if (g_autotune_abort) { tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:LSPOS:ABORTED\r\n"); return -5; }
        UART_SendTelemetry("@AT:LSPOS:WAIT:POS=%u/5:TURN_ROTOR\r\n",(unsigned)(pos+1));
        for (uint32_t t = 0; t < 3000; t++) { if (g_autotune_abort) { tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:LSPOS:ABORTED\r\n"); return -5; } delay_us(1000); }
        PWM_SetDuty1(0,0,0); tim1_enable();
        PWM_SetDuty1(10,0,0); delay_us(500);
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA || I_ss < 30) { PWM_SetDuty1(0,0,0); tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:LSPOS:ERROR:BAD_CURRENT I=%ld\r\n",(long)I_ss); return -6; }
        uint32_t saved_ccmr1 = TIM1->CCMR1;
        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE;
        int32_t di_sum = 0; uint8_t good = 0;
        for (uint8_t k = 0; k < 4; k++) {
            TIM1->CCR1 = 0; delay_us(100); ADC_StartConversion(); int32_t i_lo = AT_ReadCurrent_mA();
            TIM1->CCR1 = (uint16_t)((10U * period) / 100U); delay_us(100); ADC_StartConversion(); int32_t i_hi = AT_ReadCurrent_mA();
            int32_t di = i_hi - i_lo; if (di > 0) { di_sum += di; good++; }
        }
        TIM1->CCMR1 = saved_ccmr1;
        PWM_SetDuty1(0,0,0); tim1_disable();
        int32_t di_avg = (good > 0) ? (di_sum / good) : 1; if (di_avg <= 10) di_avg = 1;
        int32_t U_app = (int32_t)(((int64_t)vbus * 10) / 100U);
        /* OEW: одна обмотка в петле, деление на 2 (конвенция для звезды) не нужно. */
        int32_t Ls_uH = (int32_t)(((int64_t)U_app * 100) / di_avg);
        ls_vals[cnt++] = Ls_uH;
        UART_SendTelemetry("@AT:LSPOS:MEAS:POS=%u/6:Ls=%ld:I=%ld\r\n",(unsigned)(pos+1),(long)Ls_uH,(long)I_ss);
    }
    NVIC_EnableIRQ(ADC1_2_IRQn);
    AtStat32 stat; stat.count = cnt;
    for (uint8_t i = 0; i < cnt; i++) stat.values[i] = ls_vals[i];
    stat_compute(&stat);
    UART_SendTelemetry("@AT:LSPOS:OK:MEDIAN=%ld:MIN=%ld:MAX=%ld:SPREAD=%ld%%\r\n",(long)stat.median,(long)stat.min,(long)stat.max,(long)stat.spread_pct);
    if (stat.spread_pct > 20) UART_SendStr("@AT:LSPOS:WARN:HIGH_SPREAD:SALIENCY_OR_NOISE\r\n");
    return 0;
}


void Autotune_Init(void) {
    memset(&g_motor_params, 0, sizeof(MotorParams));
}

void Autotune_PrintParams(void) {
    UART_SendTelemetry(
        "@PARAMS:Rs=%ld:Ls=%ld:Isat=%ld:Rr=%ld:Lm=%ld:Tr=%ld:Ke=%ld:p=%d:J=%ld:CH=%d\r\n",
        g_motor_params.Rs_mOhm, g_motor_params.Ls_uH, g_motor_params.Isat_ma,
        g_motor_params.Rr_mOhm, g_motor_params.Lm_uH, g_motor_params.Tr_rotor_us,
        g_motor_params.Ke_mV_per_rpm, g_motor_params.pole_pairs,
        g_motor_params.J_kg_m2_x1e6, (int)g_motor_params.current_channel);
}

void Autotune_PrintCurve(void) {
    UART_SendStr("@IDLE:CURVE:");
    for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
        UART_SendTelemetry("I=%ld,L=%ld",
                           g_motor_params.curve[i].current_ma,
                           g_motor_params.curve[i].inductance_uH);
        if (i < g_motor_params.curve_count - 1) UART_SendStr(":");
    }
    UART_SendStr("\r\n");
}

void Autotune_PrintPairs(void) {
    const char *name[] = {"A", "B", "C"};
    for (uint8_t p = 0; p < 3; p++) {
        UART_SendTelemetry("@AT:PAIR:%s:Rs=%ld:Ls=%ld:Isat=%ld:V=%u\r\n",
                           name[p],
                           (long)g_motor_params.pairs[p].Rs_mOhm,
                           (long)g_motor_params.pairs[p].Ls_uH,
                           (long)g_motor_params.pairs[p].Isat_ma,
                           (unsigned)g_motor_params.pairs[p].valid);
    }
}

void Autotune_PrintStats(void) {
    UART_SendTelemetry(
        "@AT:STAT:Rs=%ld:%ld:%ld:%ld%%:Ls=%ld:%ld:%ld:%ld%%:Isat=%ld\r\n",
        g_motor_params.Rs_stat.median, g_motor_params.Rs_stat.min,
        g_motor_params.Rs_stat.max, (long)g_motor_params.Rs_stat.spread_pct,
        g_motor_params.Ls_stat.median, g_motor_params.Ls_stat.min,
        g_motor_params.Ls_stat.max, (long)g_motor_params.Ls_stat.spread_pct,
        (long)g_motor_params.Isat_ma);
}
