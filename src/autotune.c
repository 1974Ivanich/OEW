#include "autotune.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "foc.h"
#include "protect.h"
#include "stm32g474xx.h"
#include <string.h>

MotorParams g_motor_params;
volatile uint8_t g_autotune_abort = 0;

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
    uint32_t ticks = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < ticks) {
        if (g_autotune_abort) return;
    }
}

static void tim1_enable(void) {
    TIM1->CCER = TIM_CCER_CC1E | TIM_CCER_CC1NE
               | TIM_CCER_CC2E | TIM_CCER_CC2NE
               | TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1  |= TIM_CR1_CEN;
}

static void tim1_disable(void) {
    TIM1->CR1  &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER  = 0;
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
    int32_t in_zero = ADC_GetIN_mA();

    PWM_SetDuty1(5, 0, 0);
    delay_us(300);

    ADC_StartConversion();
    int32_t i1_test = ADC_GetI1_mA();
    int32_t i2_test = ADC_GetI2_mA();
    int32_t in_test = ADC_GetIN_mA();

    PWM_SetDuty1(0, 0, 0);
    tim1_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);

    int32_t d_i1 = at_abs32(i1_test - i1_zero);
    int32_t d_i2 = at_abs32(i2_test - i2_zero);
    int32_t d_in = at_abs32(in_test - in_zero);

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
        case AT_CH_IN: i = ADC_GetIN_mA(); break;
        default:       i = ADC_GetIN_mA(); break;
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
    int32_t in = at_abs32(ADC_GetIN_mA());
    if (i1 > 150 || i2 > 150 || in > 150) {
        UART_SendTelemetry("@AT:ERROR:NONZERO_CURRENT:I1=%ld:I2=%ld:IN=%ld\r\n",
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
    return (int32_t)(((int64_t)U_mV * dt_us) / di_avg) / 2;
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
    g_motor_params.Rs_mOhm = (int32_t)(r_pp_mohm / 2);

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
    out->Rs_mOhm = (int32_t)(((int64_t)U_ref * 1000) / I_ss_ref) / 2;

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

    for (uint8_t rep = 0; rep < 5; rep++) {
        if (g_autotune_abort) { tim1_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

        g_motor_params.curve_count = 0;
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
                Rs_this = (int32_t)(((int64_t)U_applied * 1000) / I_ss) / 2;
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

        int32_t Isat_this = 0;
        if (max_Ls > 0 && rep == 0) {
            int32_t threshold = max_Ls * 70 / 100;
            for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
                if (g_motor_params.curve[i].inductance_uH <= threshold) {
                    Isat_this = g_motor_params.curve[i].current_ma;
                    break;
                }
            }
        }

        if (rep < 5) {
            g_motor_params.Rs_stat.values[rep]   = Rs_this;
            g_motor_params.Ls_stat.values[rep]   = max_Ls;
            g_motor_params.Isat_stat.values[rep] = Isat_this;
            g_motor_params.Rs_stat.count   = rep + 1;
            g_motor_params.Ls_stat.count   = rep + 1;
            g_motor_params.Isat_stat.count = rep + 1;
        }
    }

    NVIC_EnableIRQ(ADC1_2_IRQn);

    stat_compute(&g_motor_params.Rs_stat);
    stat_compute(&g_motor_params.Ls_stat);
    stat_compute(&g_motor_params.Isat_stat);

    g_motor_params.Rs_mOhm = g_motor_params.Rs_stat.median;
    g_motor_params.Ls_uH   = g_motor_params.Ls_stat.median;
    g_motor_params.Isat_ma = g_motor_params.Isat_stat.median;

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

void Autotune_Init(void) {
    memset(&g_motor_params, 0, sizeof(MotorParams));
}

void Autotune_PrintParams(void) {
    UART_SendTelemetry(
        "@PARAMS:Rs=%ld:Ls=%ld:Isat=%ld:Rr=%ld:Lm=%ld:Tr=%ld:Ke=%ld:p=%d:J=%ld:CH=%d\r\n",
        g_motor_params.Rs_mOhm, g_motor_params.Ls_uH, g_motor_params.Isat_ma,
        g_motor_params.Rr_mOhm, g_motor_params.Lm_uH, g_motor_params.Tr_us,
        g_motor_params.Ke_mV_rpm, g_motor_params.pole_pairs,
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
    const char *name[] = {"AB", "BC", "CA"};
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
        "@AT:STAT:Rs=%ld:%ld:%ld:%ld%%:Ls=%ld:%ld:%ld:%ld%%:Isat=%ld:%ld:%ld:%ld%%\r\n",
        g_motor_params.Rs_stat.median, g_motor_params.Rs_stat.min,
        g_motor_params.Rs_stat.max, (long)g_motor_params.Rs_stat.spread_pct,
        g_motor_params.Ls_stat.median, g_motor_params.Ls_stat.min,
        g_motor_params.Ls_stat.max, (long)g_motor_params.Ls_stat.spread_pct,
        g_motor_params.Isat_stat.median, g_motor_params.Isat_stat.min,
        g_motor_params.Isat_stat.max, (long)g_motor_params.Isat_stat.spread_pct);
}
