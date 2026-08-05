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
    GPIOB->BSRR = (1U<<4);  /* EN1 = HIGH */
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
    GPIOB->BSRR = (1U<<(16+4));  /* EN1 = LOW */
}

static void tim8_enable(void);
static void tim8_disable(void);
static void both_enable(void);
static void both_disable(void);

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
    /* Защитный сброс injected group: если предыдущий тест прервался по
     * таймауту в ADC_InjectedStop (JADSTP не снялся вовремя), JADSTART
     * может остаться взведённым и конфликтовать с regular-конверсией,
     * используемой ниже (ADC_StartConversion). Штатно foc_running=0
     * гарантирует, что injected не запущен, но это доп. страховка. */
    ADC_InjectedStop();
    __DSB();
    ADC_CalibrateOffsets();
    dwt_init();

    if (ADC_GetOffsetI1() < 1800 || ADC_GetOffsetI1() > 2300 ||
        ADC_GetOffsetI2() < 1800 || ADC_GetOffsetI2() > 2300) {
        UART_SendTelemetry("@DBG:CH:WARN:OFFSET_OUT_OF_RANGE:i1=%u:i2=%u:ires=%u\r\n",
            ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIres());
    }

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    UART_SendTelemetry("@DBG:CH:EN:GPIOB_ODR=0x%08lX:TIM1_CR1=0x%08lX:TIM1_BDTR=0x%08lX:TIM1_CCER=0x%08lX:TIM8_CR1=0x%08lX:TIM8_BDTR=0x%08lX:TIM8_CCER=0x%08lX\r\n",
        (unsigned long)GPIOB->ODR,
        (unsigned long)TIM1->CR1, (unsigned long)TIM1->BDTR, (unsigned long)TIM1->CCER,
        (unsigned long)TIM8->CR1, (unsigned long)TIM8->BDTR, (unsigned long)TIM8->CCER);
    UART_SendTelemetry("@DBG:CH:CCR:TIM1_CCR1=%lu:TIM1_ARR=%lu:TIM8_CCR1=%lu:TIM8_ARR=%lu\r\n",
        (unsigned long)TIM1->CCR1, (unsigned long)TIM1->ARR,
        (unsigned long)TIM8->CCR1, (unsigned long)TIM8->ARR);
    /* Fault проверяем сразу после включения ШИМ — если PROTECT сработал
     * между AT_SafetyCheck и этой точкой (например от шумового выброса),
     * PWM_Disable() внутри PROTECT_Check аппаратно снимет MOE ещё до
     * теста, и software должен это увидеть, а не считать NO_CURRENT
     * загадкой. PROTECT_Check() здесь не вызывается автоматически (ADC
     * IRQ отключён), поэтому проверяем сохранённый программный флаг. */
    UART_SendTelemetry("@DBG:CH:PRE_TEST:FAULT=%d\r\n", PROTECT_IsFault());

    ADC_StartConversion();
    int32_t i1_zero = ADC_GetI1_mA();
    int32_t i2_zero = ADC_GetI2_mA();
    int32_t in_zero = ADC_GetIres_mA();
    UART_SendTelemetry("@DBG:CH:ZERO:raw_i1=%u:raw_i2=%u:raw_ires=%u:raw_vbus=%u\r\n",
                       ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
    UART_SendTelemetry("@DBG:CH:ZERO:mA:i1=%ld:i2=%ld:ires=%ld:vbus=%ldmV\r\n",
                       (long)i1_zero, (long)i2_zero, (long)in_zero, (long)ADC_GetVbus_mV());

    PWM_SetDuty1(5, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    delay_us(300);

    UART_SendTelemetry("@DBG:CH:POST_DELAY:CCR1=%lu:CNT1=%lu:CR1=0x%08lX:BDTR=0x%08lX:FAULT=%d\r\n",
        (unsigned long)TIM1->CCR1, (unsigned long)TIM1->CNT,
        (unsigned long)TIM1->CR1, (unsigned long)TIM1->BDTR, PROTECT_IsFault());

    ADC_StartConversion();
    int32_t i1_test = ADC_GetI1_mA();
    int32_t i2_test = ADC_GetI2_mA();
    int32_t in_test = ADC_GetIres_mA();
    UART_SendTelemetry("@DBG:CH:TEST:raw_i1=%u:raw_i2=%u:raw_ires=%u:raw_vbus=%u\r\n",
                       ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
    UART_SendTelemetry("@DBG:CH:TEST:mA:i1=%ld:i2=%ld:ires=%ld:vbus=%ldmV\r\n",
                       (long)i1_test, (long)i2_test, (long)in_test, (long)ADC_GetVbus_mV());

    /* Снимок PWM/EN ДО отключения — иначе к моменту анализа NO_CURRENT
     * both_disable() уже сбросит MOE/CEN/EN и снапшот покажет неверную
     * (выключенную) картину состояния во время самого теста. */
    uint32_t snap_ccr1 = TIM1->CCR1, snap_arr1 = TIM1->ARR;
    uint32_t snap_moe  = TIM1->BDTR & TIM_BDTR_MOE;
    uint32_t snap_cen1 = TIM1->CR1  & TIM_CR1_CEN;
    uint32_t snap_cen8 = TIM8->CR1  & TIM_CR1_CEN;
    uint32_t snap_en1  = GPIOB->ODR & (1U<<4);
    uint32_t snap_en2  = GPIOB->ODR & (1U<<5);

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_disable();
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
        /* Детальный снапшот причины отказа — вместо одной строки FAULT
         * печатаем всё состояние тракта разом: ADC (raw+offset), PWM
         * (CCR/ARR/MOE/CEN), EN-пины, PROTECT. Разбито на короткие строки,
         * чтобы не упереться в лимит UART_SendTelemetry buf[256]. */
        UART_SendStr("@FAIL:NO_CURRENT\r\n");
        UART_SendTelemetry("@FAIL:ADC:OFFSET:i1=%u:i2=%u:ires=%u\r\n",
            ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIres());
        UART_SendTelemetry("@FAIL:ADC:RAW_TEST:i1=%u:i2=%u:ires=%u:vbus=%u\r\n",
            ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
        UART_SendTelemetry("@FAIL:PWM:TIM1:CCR1=%lu:ARR=%lu\r\n",
            (unsigned long)snap_ccr1, (unsigned long)snap_arr1);
        UART_SendTelemetry("@FAIL:PWM:MOE=%d:CEN1=%d:CEN8=%d\r\n",
            snap_moe ? 1 : 0, snap_cen1 ? 1 : 0, snap_cen8 ? 1 : 0);
        UART_SendTelemetry("@FAIL:EN:EN1=%d:EN2=%d\r\n",
            snap_en1 ? 1 : 0, snap_en2 ? 1 : 0);
        UART_SendTelemetry("@FAIL:PROTECT:fault=%d\r\n", PROTECT_IsFault());
        UART_SendStr("@AT:CH_DETECT:ERROR:NO_CURRENT\r\n");
        return -1;
    }

    g_motor_params.current_channel = ch;
    g_motor_params.current_sign    = (signed_current >= 0) ? 1 : -1;

    UART_SendTelemetry("@AT:CH_DETECT:OK:CH=%d:I=%ld:SIGN=%ld\r\n",
                       (int)ch, (long)best, (long)g_motor_params.current_sign);
    return 0;
}

static int32_t AT_ReadCurrentChannel_mA(AtCurrentChannel ch) {
    int32_t i = 0;
    switch (ch) {
        case AT_CH_I1: i = ADC_GetI1_mA(); break;
        case AT_CH_I2: i = ADC_GetI2_mA(); break;
        case AT_CH_IN: i = ADC_GetIres_mA(); break;
        default:       i = ADC_GetIres_mA(); break;
    }
    return i * g_motor_params.current_sign;
}

static int32_t AT_ReadCurrent_mA(void) {
    return AT_ReadCurrentChannel_mA(g_motor_params.current_channel);
}

static int32_t AT_ReadCurrentChannelMedian_mA(AtCurrentChannel ch) {
    int32_t s[5];
    for (uint8_t k = 0; k < 5; k++) {
        ADC_StartConversion();
        s[k] = AT_ReadCurrentChannel_mA(ch);
    }
    return median_small(s, 5);
}

static int32_t AT_ReadCurrentMedian_mA(void) {
    return AT_ReadCurrentChannelMedian_mA(g_motor_params.current_channel);
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

    /* Остаточный ток в обмотках/фильтрах после предыдущего теста (особенно
     * после `rr`, где ток доходил до единиц ампер) может ещё не спасть до
     * нуля и/или сместить offset АЦП. Раньше мы отказывали немедленно
     * (NONZERO_CURRENT), из-за чего `noload` систематически падал сразу
     * после `rr`. Теперь даём току спасть и повторно калибруем offset —
     * до 5 попыток по 100 мс (итого не более 500 мс, и только если ток
     * реально не нулевой; в штатном случае цикл завершается на первой
     * итерации без задержки). */
    int32_t i1 = 0, i2 = 0, in = 0;
    for (uint8_t retry = 0; retry < 5; retry++) {
        ADC_StartConversion();
        i1 = at_abs32(ADC_GetI1_mA());
        i2 = at_abs32(ADC_GetI2_mA());
        in = at_abs32(ADC_GetIres_mA());
        if (i1 <= 150 && i2 <= 150 && in <= 150) break;
        if (retry == 0) {
            UART_SendTelemetry("@AT:WARN:RESIDUAL_CURRENT:I1=%ld:I2=%ld:Ires=%ld:RETRYING\r\n",
                               (long)i1, (long)i2, (long)in);
        }
        delay_us(100000);
        ADC_CalibrateOffsets();
    }
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
static int32_t AT_MeasureLs_uH(uint8_t pair_idx, volatile uint32_t *ccr,
                               int32_t U_mV, uint16_t duty_pct,
                               int32_t rs_mOhm, AtCurrentChannel ch) {
    const uint32_t pwm_period_us   = 1000000U / 5000U;     /* 1 / 5 кГц = 200 мкс */
    const uint32_t dt_min_us       = pwm_period_us;
    const uint32_t dt_max_us       = 2000U;
    const int32_t  di_min_ma       = 30;
    const int32_t  di_target_min   = 80;
    const int32_t  di_target_max   = 300;
    const uint8_t  n_attempts      = 3;

    int32_t L_samples[5];
    uint8_t n_valid = 0;
    uint32_t dt_us = 1000U;

    for (uint8_t attempt = 0; attempt < n_attempts; attempt++) {
        if (g_autotune_abort) return 0;

        /* 1. Сброс тока в ноль: активная фаза замкнута на нижние ключи.
         * Ждём, пока |I| не упадёт ниже 10 мА (но не более ~30 мс). */
        *ccr = 0;
        TIM1->EGR |= TIM_EGR_UG;
        TIM1->EGR &= ~TIM_EGR_UG;
        delay_us(pwm_period_us);
        for (uint8_t w = 0; w < 60; w++) {
            ADC_StartConversion();
            if (at_abs32(AT_ReadCurrentChannel_mA(ch)) < 10) break;
            delay_us(500);
        }

        /* 2. Начальное измерение — после паузы ток должен быть близок к нулю. */
        ADC_StartConversion();
        int32_t i0 = AT_ReadCurrentChannel_mA(ch);

        /* 3. Подаём напряжение на выбранную фазу, синхронно с обновлением ШИМ. */
        uint32_t ccr_val = ((uint32_t)duty_pct * ((uint32_t)PWM_GetARR() + 1U)) / 100U;
        if (ccr_val == 0) ccr_val = 1;
        *ccr = (uint16_t)ccr_val;
        TIM1->EGR |= TIM_EGR_UG;
        TIM1->EGR &= ~TIM_EGR_UG;

        /* dt выбираем кратным периоду ШИМ, чтобы всегда измерять
         * в одинаковой фазе счётчика. */
        uint32_t dt_aligned = ((dt_us + pwm_period_us / 2) / pwm_period_us) * pwm_period_us;
        if (dt_aligned < dt_min_us) dt_aligned = dt_min_us;
        if (dt_aligned > dt_max_us) dt_aligned = dt_max_us;

        delay_us(dt_aligned);

        /* 4. Конечное измерение. */
        ADC_StartConversion();
        int32_t i1 = AT_ReadCurrentChannel_mA(ch);

        int32_t di = i1 - i0;
        int32_t di_abs = di < 0 ? -di : di;

        int32_t L_uH = 0;
        if (di_abs >= di_min_ma) {
            L_uH = (int32_t)(((int64_t)U_mV * dt_aligned) / di_abs);

            /* Первая поправка на активное сопротивление:
             * для RL-цепи L_true ≈ L_naive * (1 - di/(2*I_final)),
             * где I_final = U_mV / R  [A] = U_mV * 1000 / R_mOhm [мА]. */
            if (rs_mOhm > 0) {
                int32_t i_final = (int32_t)(((int64_t)U_mV * 1000LL) / rs_mOhm);
                if (i_final > 0 && di_abs < i_final) {
                    int32_t factor = 1000 - (di_abs * 1000LL) / (2 * i_final);
                    if (factor > 0 && factor < 1000) {
                        L_uH = (int32_t)(((int64_t)L_uH * factor) / 1000LL);
                    }
                }
            }
            L_samples[n_valid++] = L_uH;
        }

        UART_SendTelemetry("@AT:PAIR:%u:LS_STEP:D=%u:dt=%u:I0=%ld:I1=%ld:dI=%ld:L=%ld:ATT=%u\r\n",
                           (unsigned)pair_idx, (unsigned)duty_pct, (unsigned)dt_aligned,
                           (long)i0, (long)i1, (long)di, (long)L_uH,
                           (unsigned)(attempt + 1));

        /* Адаптация dt для следующей попытки. */
        if (di_abs < di_target_min && dt_aligned < dt_max_us) {
            dt_us = dt_aligned + pwm_period_us;
        } else if (di_abs > di_target_max && dt_aligned > dt_min_us) {
            dt_us = (dt_aligned > pwm_period_us) ? (dt_aligned - pwm_period_us) : dt_min_us;
        }
    }

    if (n_valid == 0) return 0;
    return median_small(L_samples, n_valid);
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
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    int32_t U[7], I[7];
    uint8_t valid_points = 0;
    int32_t vbus_initial = ADC_GetVbus_mV();

    for (uint8_t k = 0; k < n; k++) {
        if (g_autotune_abort) {
            both_disable();
            NVIC_EnableIRQ(ADC1_2_IRQn);
            UART_SendStr("@AT:RS_IV:ABORTED\r\n");
            return -5;
        }

        PWM_SetDuty1(duties[k], 0, 0);
        PWM_SetDuty2(100, 100, 100);
        delay_us(50000);

        int32_t i = AT_ReadCurrentMedian_mA();
        if (i < 0) i = -i;

        if (i > AUTOTUNE_MAX_CURRENT_MA) {
            both_disable();
            NVIC_EnableIRQ(ADC1_2_IRQn);
            UART_SendTelemetry("@AT:RS_IV:ERROR:OVERCURRENT I=%ld\r\n", (long)i);
            return -6;
        }

        ADC_StartConversion();
        int32_t vbus_now = ADC_GetVbus_mV();

        if (vbus_now < vbus_initial * 85 / 100) {
            UART_SendTelemetry("@AT:WARN:VBUS_SAG:%ld:%ld\r\n", (long)vbus_now, (long)vbus_initial);
        }

        int32_t U_applied = (int32_t)(((int64_t)vbus_now * duties[k]) / 100U);

        /* Точки с током < 100 мА сильно искажены падением на ключах/offset'ом —
         * не используем их для линейной регрессии Rs. */
        if (i < 100) {
            UART_SendTelemetry("@AT:RS_IV:POINT:D=%u:U=%ld:I=%ld:SKIP\r\n",
                               (unsigned)duties[k], (long)U_applied, (long)i);
            continue;
        }

        U[valid_points] = U_applied;
        I[valid_points] = i;

        UART_SendTelemetry("@AT:RS_IV:POINT:D=%u:U=%ld:I=%ld\r\n",
                           (unsigned)duties[k], (long)U[valid_points], (long)I[valid_points]);
        valid_points++;
    }

    both_disable();
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

    /* Используем канал, автоматически выбранный ch-детекцией —
     * он гарантированно видит ток активной пары. */
    AtCurrentChannel ch = g_motor_params.current_channel;

    uint16_t duty_ref = 10;
    PWM_SetDuty2(100, 100, 100);

    switch (pair_idx) {
        case 0: PWM_SetDuty1(duty_ref, 0, 0); break;
        case 1: PWM_SetDuty1(0, duty_ref, 0); break;
        case 2: PWM_SetDuty1(0, 0, duty_ref); break;
    }
    /* Для высокоиндуктивных обмоток ждём ~5 постоянных времени,
     * чтобы ток установился и Rs рассчитывалось по активному сопротивлению. */
    delay_us(50000);

    int32_t I_ss_ref = AT_ReadCurrentChannelMedian_mA(ch);
    if (I_ss_ref < 0) I_ss_ref = -I_ss_ref;

    if (I_ss_ref < 50) {
        UART_SendTelemetry("@AT:PAIR:%u:ERROR:OPEN_PHASE I=%ld\r\n", (unsigned)pair_idx, (long)I_ss_ref);
        PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100);
        return -2;
    }
    if (I_ss_ref > AUTOTUNE_MAX_CURRENT_MA) {
        UART_SendTelemetry("@AT:PAIR:%u:ERROR:SHORT I=%ld\r\n", (unsigned)pair_idx, (long)I_ss_ref);
        PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100);
        return -3;
    }

    /* Многоточечная V-I регрессия вместо одноточечного R=U/I (см. Autotune_MeasureRs_IV).
     * Одноточечный расчёт (старая версия) включал в U_ref номинальное duty*Vbus без
     * учёта падения на ключах/диодах и dead-time, которое даёт почти постоянную по duty
     * добавку к U — на одной точке это чистая ошибка смещения, завышающая R (см. TZ 3.1:
     * A=20.5, B=22.2, C=27.3 Ом при Rs(iv)≈13 Ом). Регрессия по нескольким точкам
     * (наклон U(I)) отбрасывает этот постоянный офсет так же, как это делает `iv`. */
    static const uint8_t rs_duties[] = { 2, 4, 6, 8, 10, 12, 15 };
    const uint8_t n_rs = sizeof(rs_duties) / sizeof(rs_duties[0]);
    int32_t Ur[7], Ir[7];
    uint8_t nreg = 0;

    for (uint8_t k = 0; k < n_rs; k++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -4; }
        switch (pair_idx) {
            case 0: PWM_SetDuty1(rs_duties[k], 0, 0); break;
            case 1: PWM_SetDuty1(0, rs_duties[k], 0); break;
            case 2: PWM_SetDuty1(0, 0, rs_duties[k]); break;
        }
        delay_us(50000);  /* то же время установления, что и в Rs_IV */

        int32_t I_k = AT_ReadCurrentChannelMedian_mA(ch);
        if (I_k < 0) I_k = -I_k;
        if (I_k > AUTOTUNE_MAX_CURRENT_MA) {
            PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100);
            UART_SendTelemetry("@AT:PAIR:%u:ERROR:OVERCURRENT I=%ld\r\n", (unsigned)pair_idx, (long)I_k);
            return -3;
        }

        ADC_StartConversion();
        int32_t vbus_now = ADC_GetVbus_mV();
        int32_t U_k = (int32_t)(((int64_t)vbus_now * rs_duties[k]) / 100U);

        if (I_k < 100) {
            UART_SendTelemetry("@AT:PAIR:%u:RS_POINT:D=%u:U=%ld:I=%ld:SKIP\r\n",
                               (unsigned)pair_idx, (unsigned)rs_duties[k], (long)U_k, (long)I_k);
            continue;
        }
        Ur[nreg] = U_k; Ir[nreg] = I_k; nreg++;
        UART_SendTelemetry("@AT:PAIR:%u:RS_POINT:D=%u:U=%ld:I=%ld\r\n",
                           (unsigned)pair_idx, (unsigned)rs_duties[k], (long)U_k, (long)I_k);
    }

    if (nreg >= 3) {
        int64_t sum_i = 0, sum_u = 0;
        for (uint8_t k = 0; k < nreg; k++) { sum_i += Ir[k]; sum_u += Ur[k]; }
        int64_t mean_i = sum_i / nreg, mean_u = sum_u / nreg;
        int64_t num = 0, den = 0;
        for (uint8_t k = 0; k < nreg; k++) {
            int64_t di = (int64_t)Ir[k] - mean_i;
            int64_t du = (int64_t)Ur[k] - mean_u;
            num += du * di; den += di * di;
        }
        if (den != 0) {
            out->Rs_mOhm = (int32_t)((num * 1000) / den);
        } else {
            /* Недостаточный разброс тока — деградируем до одноточечного расчёта. */
            out->Rs_mOhm = (int32_t)(((int64_t)Ur[0] * 1000) / Ir[0]);
            UART_SendTelemetry("@AT:PAIR:%u:WARN:RS_NO_SPREAD:FALLBACK_1PT\r\n", (unsigned)pair_idx);
        }
    } else {
        /* Меньше 3 валидных точек — используем опорную точку duty_ref как раньше,
         * но это признак проблемы (слишком малый ток на всех duty). */
        int32_t U_ref = (int32_t)(((int64_t)vbus * duty_ref) / 100U);
        out->Rs_mOhm = (int32_t)(((int64_t)U_ref * 1000) / I_ss_ref);
        UART_SendTelemetry("@AT:PAIR:%u:WARN:RS_TOO_FEW_POINTS:FALLBACK_1PT\r\n", (unsigned)pair_idx);
    }

    int32_t max_Ls = 0;
    volatile uint32_t *ccr = &TIM1->CCR1;
    switch (pair_idx) {
        case 0: ccr = &TIM1->CCR1; break;
        case 1: ccr = &TIM1->CCR2; break;
        case 2: ccr = &TIM1->CCR3; break;
    }

    for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -4; }

        switch (pair_idx) {
            case 0: PWM_SetDuty1(duty_pct, 0, 0); break;
            case 1: PWM_SetDuty1(0, duty_pct, 0); break;
            case 2: PWM_SetDuty1(0, 0, duty_pct); break;
        }
        delay_us(500);

        int32_t I_ss = AT_ReadCurrentChannelMedian_mA(ch);
        if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -5; }

        int32_t U_applied = (int32_t)(((int64_t)vbus * duty_pct) / 100U);
        int32_t Ls_uH = AT_MeasureLs_uH(pair_idx, ccr, U_applied, duty_pct,
                                         out->Rs_mOhm, ch);
        if (Ls_uH > max_Ls) max_Ls = Ls_uH;
    }

    out->Ls_uH = max_Ls;
    out->valid = 1;
    PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100);

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
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    int64_t sum_Rs = 0, sum_Ls = 0;
    uint8_t valid_count = 0;

    for (uint8_t p = 0; p < 3; p++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:PAIRS:ABORTED\r\n"); return -5; }
        rc = AT_MeasurePair(p, &g_motor_params.pairs[p]);
        if (rc == 0 && g_motor_params.pairs[p].valid) {
            sum_Rs += g_motor_params.pairs[p].Rs_mOhm;
            sum_Ls += g_motor_params.pairs[p].Ls_uH;
            valid_count++;
        }
    }

    both_disable();
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
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

        int32_t max_Ls = 0;
        int32_t Rs_this = 0;

        PWM_SetDuty1(0, 0, 0);
        PWM_SetDuty2(100, 100, 100);
        both_enable();

        for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
            if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

            PWM_SetDuty1(duty_pct, 0, 0);
            PWM_SetDuty2(100, 100, 100);
            delay_us(500);

            int32_t I_ss = AT_ReadCurrentMedian_mA();
            if (I_ss < 0) I_ss = -I_ss;

            if (I_ss > AUTOTUNE_MAX_CURRENT_MA) {
                both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendTelemetry("@IDLE:ERROR:OVERCURRENT I=%ld\r\n", (long)I_ss); return -6;
            }
            if (duty_pct >= 20 && I_ss < 30) {
                both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendStr("@IDLE:ERROR:OPEN_PHASE\r\n"); return -7;
            }
            if (duty_pct <= 2 && I_ss > 3000) {
                both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendStr("@IDLE:ERROR:SHORT_OR_LOW_RS\r\n"); return -8;
            }

            int32_t U_applied = (int32_t)(((int64_t)ADC_GetVbus_mV() * duty_pct) / 100U);
            int32_t Ls_uH = AT_MeasureLs_uH(0, &TIM1->CCR1, U_applied, duty_pct,
                                              Rs_this, AT_CH_I1);
            if (Ls_uH > max_Ls) max_Ls = Ls_uH;

            if (rep == 0 && g_motor_params.curve_count < 64 && I_ss > 100) {
                g_motor_params.curve[g_motor_params.curve_count].current_ma    = I_ss;
                g_motor_params.curve[g_motor_params.curve_count].inductance_uH = Ls_uH;
                g_motor_params.curve_count++;
            }

            if (duty_pct == 10 && I_ss > 50) {
                /* Для корректного Rs ток должен установиться (>5τ).
                 * Уже ждали 500 мкс в цикле, дожидаемся ещё ~50 мс. */
                delay_us(50000);
                I_ss = AT_ReadCurrentMedian_mA();
                if (I_ss < 0) I_ss = -I_ss;
                U_applied = (int32_t)(((int64_t)ADC_GetVbus_mV() * duty_pct) / 100U);
                Rs_this = (int32_t)(((int64_t)U_applied * 1000) / I_ss);
            }

            if ((duty_pct % 5) == 0) {
                UART_SendTelemetry("@IDLE:PROG=%u/50:D=%u:I=%ld:L=%ld:REP=%u/%u\r\n",
                                   (unsigned)duty_pct, (unsigned)duty_pct,
                                   (long)I_ss, (long)Ls_uH,
                                   (unsigned)(rep + 1), (unsigned)5);
            }
        }

        both_disable();

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
    GPIOB->BSRR = (1U<<5);  /* EN2 = HIGH */
    TIM8->CCER |= TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM8->CR1  |= TIM_CR1_CEN;
}

static void tim8_disable(void) {
    TIM8->CR1  &= ~TIM_CR1_CEN;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM8->CCER &= ~(TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE);
    GPIOB->BSRR = (1U<<(16+5));  /* EN2 = LOW */
}

static void both_enable(void) {
    tim1_enable(); tim8_enable();
}

static void both_disable(void) {
    PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100);
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
    PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();
    int32_t max_Ls_oew = 0; uint8_t ci = 0;
    for (uint16_t d = 1; d <= 50; d++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:OEW:ABORTED\r\n"); return -5; }
        PWM_SetDuty1(d,0,0); PWM_SetDuty2(d,0,0);
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
    PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();

    /* Lock-in измерение Rr на одной амплитуде.
     * Измеренный ток коррелируем с опорным sin/cos угла theta.
     * Составляющая тока в фазе с напряжением (Id) несёт потери в R,
     * в квадратуре (Iq) — реактивная часть от Ls. Это устраняет шум
     * и не требует точного знания формы реального напряжения внутри
     * цикла усреднения — dead-time/диоды дают гармоники, почти не
     * коррелирующие с чистым sin/cos. */
    int32_t theta = 0;
    const int32_t rr_amp = 8;          /* % от полной шкалы ШИМ */
    const int32_t n_pts  = 3000;       /* 15 полных периодов при 5 Гц, Ts=1 мс */
    int32_t vbus = ADC_GetVbus_mV();
    int64_t sum_i_sin = 0, sum_i_cos = 0;
    int64_t i_sq_sum = 0;

    for (int32_t i = 0; i < n_pts; i++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:RR:ABORTED\r\n"); return -6; }
        theta += 31; if (theta >= 6283) theta -= 6283;
        int32_t sa = at_sin_q15(theta);
        int32_t sb = at_sin_q15(theta - 2094);
        int32_t sc = at_sin_q15(theta + 2094);
        int32_t ca = at_sin_q15(theta + 1571); /* cos через сдвиг на π/2 */

        int32_t da = 50 + (int32_t)(((int64_t)sa * rr_amp) / 32768);
        int32_t db = 50 + (int32_t)(((int64_t)sb * rr_amp) / 32768);
        int32_t dc = 50 + (int32_t)(((int64_t)sc * rr_amp) / 32768);
        if (da < 0) da = 0; if (da > 100) da = 100;
        if (db < 0) db = 0; if (db > 100) db = 100;
        if (dc < 0) dc = 0; if (dc > 100) dc = 100;

        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); PWM_SetDuty2(100,100,100); delay_us(1000);
        ADC_StartConversion(); int32_t i_ma = AT_ReadCurrent_mA();
        if (at_abs32(i_ma) > AUTOTUNE_MAX_CURRENT_MA) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:RR:ERROR:OVERCURRENT I=%ld\r\n",(long)i_ma); return -7; }

        sum_i_sin += (int64_t)i_ma * sa;
        sum_i_cos += (int64_t)i_ma * ca;
        i_sq_sum  += (int64_t)i_ma * i_ma;

        if ((i % 500) == 0) UART_SendTelemetry("@AT:RR:PROG=%ld/%ld:I=%ld\r\n",(long)i,(long)n_pts,(long)i_ma);
    }
    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);

    if (i_sq_sum == 0) { UART_SendStr("@AT:RR:ERROR:NO_CURRENT\r\n"); return -8; }

    /* Амплитуды тока в mA (Q15 sin амплитуда = 32768).
     * Корреляция: (2/N)*Σ i*sin = I_amp*cos(φ), (2/N)*Σ i*cos = I_amp*sin(φ).
     * Множитель 2 учитываем ниже через масштаб. */
    int64_t id_raw = (sum_i_sin * 2 + n_pts) / (2 * n_pts); /* I_d * 32768 */
    int64_t iq_raw = (sum_i_cos * 2 + n_pts) / (2 * n_pts); /* I_q * 32768 */

    /* Амплитуда напряжения в mV (sin, пик). */
    int64_t v_amp = ((int64_t)vbus * rr_amp) / 100LL;

    /* Полная проводимость: Y = I_amp / V_amp; активная часть Y*cos(φ) = Id/V_amp.
     * R_total = V_amp / |Id|.
     * Знак Id может быть инвертирован из-за polarity токового датчика — берём модуль. */
    if (id_raw == 0) { UART_SendStr("@AT:RR:ERROR:NO_RESISTIVE_CURRENT\r\n"); return -9; }
    int64_t id_abs = id_raw < 0 ? -id_raw : id_raw;
    int32_t r_total_pp = (int32_t)((v_amp * 1000LL) / (id_abs / 32768LL));

    /* Телеметрия: активная/реактивная составляющие тока и угол φ (°). */
    int32_t id_mA = (int32_t)(id_raw / 32768LL);
    int32_t iq_mA = (int32_t)(iq_raw / 32768LL);
    int32_t angle_q31 = CORDIC_Atan2(iq_raw, id_raw);          /* π = 0x7FFFFFFF */
    int32_t angle_deg = (int32_t)(((int64_t)angle_q31 * 180) / 2147483647LL);

    UART_SendTelemetry("@AT:RR:LOCKIN:Id=%ld:Iq=%ld:Vamp=%ld:PHI=%ld\r\n",
                       (long)id_mA, (long)iq_mA, (long)v_amp, (long)angle_deg);

    /* OEW: r_total_pp = Rs + Rr' (одна обмотка статора + приведённый ротор). */
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
    PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();
    int32_t vbus = ADC_GetVbus_mV(); int32_t theta = 0;

    UART_SendStr("@AT:NOLOAD:RAMP:START\r\n");
    for (int32_t f_mHz = 0; f_mHz <= 50000; f_mHz += 100) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:NOLOAD:ABORTED\r\n"); return -6; }
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
            PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); PWM_SetDuty2(100,100,100); delay_us(2000);
        if ((f_mHz % 5000) == 0) UART_SendTelemetry("@AT:NOLOAD:RAMP:F=%ld:V=%ld%%\r\n",(long)(f_mHz/1000),(long)v_mag);
    }
    UART_SendStr("@AT:NOLOAD:MEASURE:START\r\n");
    int64_t i_sum = 0; const int32_t n_meas = 500;
    for (int32_t i = 0; i < n_meas; i++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:NOLOAD:ABORTED\r\n"); return -6; }
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
            PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc); PWM_SetDuty2(100,100,100); delay_us(2000);
        ADC_StartConversion(); int32_t im = at_abs32(AT_ReadCurrent_mA());
        i_sum += im;
    }
    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
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
    PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();
    PWM_SetDuty1(20,0,0);
    for (int32_t i = 0; i < 100; i++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@SCOPE:ABORTED\r\n"); return -5; }
        delay_us(20); ADC_StartConversion(); int32_t i_ma = AT_ReadCurrent_mA();
        UART_SendTelemetry("@SCOPE:T=%ld:I=%ld\r\n",(long)(i*20),(long)i_ma);
    }
    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
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
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:LSPOS:ABORTED\r\n"); return -5; }
        UART_SendTelemetry("@AT:LSPOS:WAIT:POS=%u/5:TURN_ROTOR\r\n",(unsigned)(pos+1));
        for (uint32_t t = 0; t < 3000; t++) { if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:LSPOS:ABORTED\r\n"); return -5; } delay_us(1000); }
        PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();
        PWM_SetDuty1(10,0,0); delay_us(500);
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA || I_ss < 10) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:LSPOS:ERROR:BAD_CURRENT I=%ld\r\n",(long)I_ss); return -6; }
        uint32_t saved_ccmr1 = TIM1->CCMR1;
        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE;
        int32_t di_sum = 0; uint8_t good = 0;
        for (uint8_t k = 0; k < 4; k++) {
            TIM1->CCR1 = 0; delay_us(100); ADC_StartConversion(); int32_t i_lo = AT_ReadCurrent_mA();
            TIM1->CCR1 = (uint16_t)((10U * period) / 100U); delay_us(100); ADC_StartConversion(); int32_t i_hi = AT_ReadCurrent_mA();
            int32_t di = i_hi - i_lo; if (di > 0) { di_sum += di; good++; }
        }
        TIM1->CCMR1 = saved_ccmr1;
        PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_disable();
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
