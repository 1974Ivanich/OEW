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

/* Валидация правдоподобия Ls (мкГн). Диапазон для АД 0.1..1.5 кВт:
 * 0.5..500 мГн (500..500000 мкГн). Мусор из AT_MeasureLs_uH (почти
 * нулевые ΔI) даёт Ls в Гн — отсекаем. Возвращает значение при
 * прохождении, иначе 0 (REJECT — не перезаписывать сохранённое). */
static int32_t AT_SaneLs(int32_t l_uh) {
    if (l_uh < 500 || l_uh > 500000) return 0;
    return l_uh;
}

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

static void curve_sort_by_current(AtCurvePoint *curve, uint8_t n) {
    for (uint8_t i = 1; i < n; i++) {
        AtCurvePoint key = curve[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && curve[j].current_ma > key.current_ma) {
            curve[j + 1] = curve[j];
            j--;
        }
        curve[j + 1] = key;
    }
}

/* Ждём завершения заданного числа периодов TIM1 по флагу UIF.
 * Это гарантирует, что измерения происходят в одинаковой фазе ШИМ. */
static void pwm_wait_periods(uint8_t n) {
    for (uint8_t i = 0; i < n; i++) {
        TIM1->SR &= ~TIM_SR_UIF;
        while (!(TIM1->SR & TIM_SR_UIF)) { __NOP(); }
    }
}

static uint16_t AT_GetRawChannel(AtCurrentChannel ch) {
    switch (ch) {
        case AT_CH_I1: return ADC_GetRawI1();
        case AT_CH_I2: return ADC_GetRawI2();
        case AT_CH_IN: return ADC_GetRawIres();
        default:       return ADC_GetRawIres();
    }
}

static uint8_t curve_filter_outliers(AtCurvePoint *curve, uint8_t n) {
    /* 1. Убираем неположительные точки. */
    uint8_t valid = 0;
    for (uint8_t i = 0; i < n; i++) {
        if (curve[i].inductance_uH > 0) {
            curve[valid++] = curve[i];
        }
    }
    if (valid < 5) return valid;

    /* 2. Локальная медианная фильтрация: для каждой точки смотрим
     * соседей ±2 и отбрасываем точку, если она отличается более чем
     * в 2 раза от локальной медианы. Это сохраняет плавный наклон
     * насыщения, но удаляет одиночные шумовые выбросы. */
    AtCurvePoint tmp[64];
    uint8_t kept = 0;
    for (uint8_t i = 0; i < valid; i++) {
        int32_t win[5];
        uint8_t nw = 0;
        for (int8_t j = -2; j <= 2; j++) {
            int16_t idx = (int16_t)i + j;
            if (idx >= 0 && idx < valid) {
                win[nw++] = curve[idx].inductance_uH;
            }
        }
        int32_t med = median_small(win, nw);
        int32_t L = curve[i].inductance_uH;
        if (med > 0 && L >= med / 2 && L <= med * 2) {
            tmp[kept++] = curve[i];
        }
    }
    memcpy(curve, tmp, kept * sizeof(AtCurvePoint));
    return kept;
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
        if (i1 <= 20 && i2 <= 20 && in <= 20) {
            ADC_CalibrateOffsets();
            break;
        }
        if (retry == 0) {
            UART_SendTelemetry("@AT:WARN:RESIDUAL_CURRENT:I1=%ld:I2=%ld:Ires=%ld:RETRYING\r\n",
                               (long)i1, (long)i2, (long)in);
        }
        delay_us(100000);
    }
    if (i1 > 20 || i2 > 20 || in > 20) {
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
                               int32_t vbus_mV, uint16_t duty_pct,
                               int32_t rs_mOhm, AtCurrentChannel ch) {
    const uint32_t pwm_period_us   = 1000000U / 5000U;     /* 1 / 5 кГц = 200 мкс */
    const uint8_t  n_periods     = 3;                      /* усреднение по 3 периодам */
    const uint32_t dt_total_us     = n_periods * pwm_period_us;
    const int32_t  di_min_ma       = 30;
    const int32_t  di_max_ma       = 1000;
    const int32_t  reset_i_th      = 20;
    const uint8_t  n_attempts      = 3;
    const uint16_t raw_min         = 50;   /* отсечка около 0 АЦП */
    const uint16_t raw_max         = 4045; /* отсечка около насыщения АЦП */

    int32_t L_samples[5];
    uint8_t n_valid = 0;

    if (duty_pct == 0 || duty_pct > 100) return 0;

    /* Сброс тока в ноль: freewheel через нижние ключи (U≈0, спад только
     * через Rs — может быть медленным). Ждём до ~100 мс с диагностикой. */
    *ccr = 0;
    TIM1->EGR |= TIM_EGR_UG;
    pwm_wait_periods(1);
    uint8_t decayed = 0;
    for (uint8_t w = 0; w < 200; w++) {
        ADC_StartConversion();
        if (at_abs32(AT_ReadCurrentChannel_mA(ch)) < reset_i_th) { decayed = 1; break; }
        delay_us(500);
    }
    if (!decayed) {
        ADC_StartConversion();
        UART_SendTelemetry("@AT:PAIR:%u:LS_WARN:DECAY_TIMEOUT:I=%ld\r\n",
                           (unsigned)pair_idx,
                           (long)AT_ReadCurrentChannel_mA(ch));
    }

    for (uint8_t attempt = 0; attempt < n_attempts; attempt++) {
        if (g_autotune_abort) return 0;

        /* Начальная выборка — после UEV и ровно n_periods периодов
         * с CCR=0 (фаза закорочена). */
        *ccr = 0;
        TIM1->EGR |= TIM_EGR_UG;
        pwm_wait_periods(n_periods);
        ADC_StartConversion();
        int32_t i0 = AT_ReadCurrentChannel_mA(ch);
        uint16_t raw0 = AT_GetRawChannel(ch);

        /* Подаём напряжение на выбранную фазу на n_periods периодов. */
        uint32_t ccr_val = ((uint32_t)duty_pct * ((uint32_t)PWM_GetARR() + 1U)) / 100U;
        if (ccr_val == 0) ccr_val = 1;
        *ccr = (uint16_t)ccr_val;
        TIM1->EGR |= TIM_EGR_UG;
        pwm_wait_periods(n_periods);
        ADC_StartConversion();
        int32_t i1 = AT_ReadCurrentChannel_mA(ch);
        uint16_t raw1 = AT_GetRawChannel(ch);

        int32_t di = i1 - i0;
        int32_t di_abs = di < 0 ? -di : di;

        int32_t L_uH = 0;
        int32_t u_R = 0;
        int32_t u_L = 0;
        uint8_t skip = 0;
        if (raw0 < raw_min || raw0 > raw_max || raw1 < raw_min || raw1 > raw_max) {
            skip = 1;
        } else if (di_abs < di_min_ma || di_abs > di_max_ma) {
            skip = 1;
        } else {
            /* Вольт-секундный баланс за dt_total (проверено по pwm.c):
             * Ton: обмотка видит +Vbus (HIN_U1 + LIN_U2 открыты).
             * Toff: freewheel через нижние ключи, U≈0, спад только через Rs.
             * ∫U_L dt = Vbus·Ton − Rs·∫I dt ≈ (Vbus·duty − Rs·Iavg)·dt_total.
             * → L = (Vbus·duty/100 − Iavg·Rs) · dt_total / dI. */
            int32_t i_avg = (i0 + i1) / 2;
            int32_t u_avg = (int32_t)(((int64_t)vbus_mV * duty_pct) / 100U);
            u_L = u_avg;
            if (rs_mOhm > 0) {
                u_R = (int32_t)(((int64_t)i_avg * rs_mOhm) / 1000LL);
                u_L = u_avg - u_R;
            }
            if (u_L > 0) {
                L_uH = (int32_t)(((int64_t)u_L * dt_total_us) / di_abs);
                L_samples[n_valid++] = L_uH;
            }
        }

        UART_SendTelemetry("@AT:PAIR:%u:LS_STEP:D=%u:dt=%u:UL=%ld:UR=%ld:RAW0=%u:RAW1=%u:I0=%ld:I1=%ld:dI=%ld:L=%ld:SKIP=%u:ATT=%u\r\n",
                           (unsigned)pair_idx, (unsigned)duty_pct, (unsigned)dt_total_us,
                           (long)u_L, (long)u_R,
                           (unsigned)raw0, (unsigned)raw1,
                           (long)i0, (long)i1, (long)di, (long)L_uH,
                           (unsigned)skip, (unsigned)(attempt + 1));
    }

    if (n_valid == 0) return 0;

    /* Контроль качества: разброс между попытками > 30% — измерение
     * сомнительно, помечаем в логе (результат всё равно возвращаем). */
    int32_t L_med = median_small(L_samples, n_valid);
    if (n_valid >= 2 && L_med > 0) {
        int32_t L_min = L_samples[0], L_max = L_samples[0];
        for (uint8_t i = 1; i < n_valid; i++) {
            if (L_samples[i] < L_min) L_min = L_samples[i];
            if (L_samples[i] > L_max) L_max = L_samples[i];
        }
        int32_t spread_pct = (int32_t)(((int64_t)(L_max - L_min) * 100) / L_med);
        if (spread_pct > 30) {
            UART_SendTelemetry("@AT:PAIR:%u:LS_WARN:HIGH_SPREAD:D=%u:MIN=%ld:MAX=%ld:SPREAD=%ld%%\r\n",
                               (unsigned)pair_idx, (unsigned)duty_pct,
                               (long)L_min, (long)L_max, (long)spread_pct);
        }
    }
    return L_med;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  Multi-point Rs
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureRs_IV(void) {
    static const uint8_t duties[] = { 5, 7, 9, 11, 13, 15 };
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
static void AT_SetPairDuty(uint8_t pair_idx, uint16_t duty) {
    switch (pair_idx) {
        case 0: PWM_SetDuty1(duty, 0, 0); break;
        case 1: PWM_SetDuty1(0, duty, 0); break;
        case 2: PWM_SetDuty1(0, 0, duty); break;
    }
}

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
    AT_SetPairDuty(pair_idx, duty_ref);
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
    static const uint8_t rs_duties[] = { 5, 7, 9, 11, 13, 15 };
    const uint8_t n_rs = sizeof(rs_duties) / sizeof(rs_duties[0]);
    int32_t Ur[7], Ir[7];
    uint8_t nreg = 0;

    for (uint8_t k = 0; k < n_rs; k++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -4; }
        AT_SetPairDuty(pair_idx, rs_duties[k]);
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

    int32_t L_buf[10];
    uint8_t  L_cnt = 0;
    volatile uint32_t *ccr = &TIM1->CCR1;
    switch (pair_idx) {
        case 0: ccr = &TIM1->CCR1; break;
        case 1: ccr = &TIM1->CCR2; break;
        case 2: ccr = &TIM1->CCR3; break;
    }

    /* duty < 5% не измеряем: dead-time (1.5 мкс при Ton=2 мкс на 1%)
     * и Vce дают ошибку в десятки процентов. */
    for (uint16_t duty_pct = 5; duty_pct <= 50; duty_pct++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -4; }

        AT_SetPairDuty(pair_idx, duty_pct);
        delay_us(500);

        int32_t I_ss = AT_ReadCurrentChannelMedian_mA(ch);
        if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -5; }

        int32_t Ls_uH = AT_MeasureLs_uH(pair_idx, ccr, vbus, duty_pct,
                                         out->Rs_mOhm, ch);
        if (Ls_uH > 0 && L_cnt < 10) {
            L_buf[L_cnt++] = Ls_uH;
        }
    }

    if (L_cnt > 0) {
        out->Ls_uH = median_small(L_buf, L_cnt);
    } else {
        out->Ls_uH = 0;
    }
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
    /* Валидация перед записью Ls — отсекаем мусор (почти-нулевые ΔI). */
    {
        int32_t ls_avg = (int32_t)(sum_Ls / valid_count);
        if (AT_SaneLs(ls_avg) > 0) {
            g_motor_params.Ls_uH = ls_avg;
        } else {
            UART_SendTelemetry("@AT:PAIRS:WARN:LS_REJECT:%ld\r\n", (long)ls_avg);
        }
    }

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

        int32_t L_buf[10];
        uint8_t  L_cnt = 0;

        PWM_SetDuty1(0, 0, 0);
        PWM_SetDuty2(100, 100, 100);
        both_enable();

        /* Используем Rs, измеренную ранее (iv/pairs). Она получена
         * многоточечной регрессией и точнее, чем U/I на одном duty.
         * Если по какой-то причине Rs нет — быстро измеряем на 10%. */
        int32_t Rs_this = g_motor_params.Rs_mOhm;
        if (Rs_this <= 0) {
            PWM_SetDuty1(10, 0, 0);
            PWM_SetDuty2(100, 100, 100);
            delay_us(100000);
            int32_t I_rs = AT_ReadCurrentMedian_mA();
            if (I_rs < 0) I_rs = -I_rs;
            int32_t U_rs = (int32_t)(((int64_t)ADC_GetVbus_mV() * 10) / 100U);
            if (I_rs > 10) {
                Rs_this = (int32_t)(((int64_t)U_rs * 1000) / I_rs);
            }
        }

        /* duty < 5% не измеряем — dead-time/Vce доминируют. */
        for (uint16_t duty_pct = 5; duty_pct <= 50; duty_pct++) {
            if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@IDLE:ABORTED\r\n"); return -5; }

            PWM_SetDuty1(duty_pct, 0, 0);
            PWM_SetDuty2(100, 100, 100);
            delay_us(500);

            int32_t I_ss = AT_ReadCurrentMedian_mA();
            if (I_ss < 0) I_ss = -I_ss;
            int32_t U_applied = (int32_t)(((int64_t)ADC_GetVbus_mV() * duty_pct) / 100U);
            int32_t vbus_idle   = ADC_GetVbus_mV();

            if (I_ss > AUTOTUNE_MAX_CURRENT_MA) {
                both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendTelemetry("@IDLE:ERROR:OVERCURRENT I=%ld\r\n", (long)I_ss); return -6;
            }
            if (duty_pct >= 20 && Rs_this > 0) {
                int32_t i_expected = (int32_t)(((int64_t)U_applied * 1000LL) / Rs_this);
                if (I_ss < i_expected / 10) {
                    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                    UART_SendTelemetry("@IDLE:ERROR:OPEN_PHASE I=%ld:EXP=%ld\r\n",
                                       (long)I_ss, (long)i_expected); return -7;
                }
            }
            if (duty_pct == 5 && I_ss > 6000) {
                both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);
                UART_SendStr("@IDLE:ERROR:SHORT_OR_LOW_RS\r\n"); return -8;
            }
            int32_t Ls_uH = AT_MeasureLs_uH(0, &TIM1->CCR1, vbus_idle, duty_pct,
                                              Rs_this,
                                              g_motor_params.current_channel);
            if (Ls_uH > 0 && L_cnt < 10) {
                L_buf[L_cnt++] = Ls_uH;
            }

            if (rep == 0 && g_motor_params.curve_count < 64 && I_ss > 100 && Ls_uH > 0) {
                g_motor_params.curve[g_motor_params.curve_count].current_ma    = I_ss;
                g_motor_params.curve[g_motor_params.curve_count].inductance_uH = Ls_uH;
                g_motor_params.curve_count++;
            }

            if ((duty_pct % 5) == 0) {
                UART_SendTelemetry("@IDLE:PROG=%u/50:D=%u:I=%ld:L=%ld:REP=%u/%u\r\n",
                                   (unsigned)duty_pct, (unsigned)duty_pct,
                                   (long)I_ss, (long)Ls_uH,
                                   (unsigned)(rep + 1), (unsigned)5);
            }
        }

        both_disable();

        int32_t L_rep = (L_cnt > 0) ? median_small(L_buf, L_cnt) : 0;
        if (rep < 5) {
            g_motor_params.Rs_stat.values[rep]   = Rs_this;
            g_motor_params.Ls_stat.values[rep]   = L_rep;
            g_motor_params.Rs_stat.count   = rep + 1;
            g_motor_params.Ls_stat.count   = rep + 1;
        }
    }

    NVIC_EnableIRQ(ADC1_2_IRQn);

    stat_compute(&g_motor_params.Rs_stat);
    stat_compute(&g_motor_params.Ls_stat);

    g_motor_params.Rs_mOhm = g_motor_params.Rs_stat.median;
    /* Валидация: мусорная медиана Ls (из почти-нулевых ΔI) не должна
     * перезаписывать сохранённое значение. */
    if (AT_SaneLs(g_motor_params.Ls_stat.median) > 0) {
        g_motor_params.Ls_uH = g_motor_params.Ls_stat.median;
    } else {
        UART_SendTelemetry("@AT:IDLE:WARN:LS_REJECT:%ld\r\n",
                           (long)g_motor_params.Ls_stat.median);
    }

    /* Подготовка кривой насыщения: сортировка по току и удаление
     * выбросов. Без этого кривая была неотсортированной и содержала
     * артефакты от почти-нулевых ΔI. */
    curve_sort_by_current(g_motor_params.curve, g_motor_params.curve_count);
    g_motor_params.curve_count = curve_filter_outliers(g_motor_params.curve, g_motor_params.curve_count);

    /* Isat: вычисляется один раз после всех повторов.
     * L0 — медиана первых 5–10 точек с наименьшим током (ненасыщенная L).
     * Ищем первую точку, где L падает ниже 0.7*L0. */
    g_motor_params.Isat_ma = 0;
    if (g_motor_params.curve_count > 0) {
        uint8_t n_L0 = (g_motor_params.curve_count < 10) ? g_motor_params.curve_count : 10;
        int32_t L0_buf[10];
        for (uint8_t i = 0; i < n_L0; i++) {
            L0_buf[i] = g_motor_params.curve[i].inductance_uH;
        }
        int32_t L0 = median_small(L0_buf, n_L0);
        if (L0 > 0) {
            int32_t threshold = L0 * 70 / 100;
            for (uint8_t i = 0; i < g_motor_params.curve_count; i++) {
                if (g_motor_params.curve[i].inductance_uH <= threshold) {
                    g_motor_params.Isat_ma = g_motor_params.curve[i].current_ma;
                    break;
                }
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

    /* Tr = Lr / Rr — роторную постоянную времени вычисляем из уже измеренных
     * параметров схемы замещения. Lr = Lm + Ls/2 (Ls — статорная индуктивность
     * из pairs/oew, Lm — магнитизирующая из noload). */
    int32_t Lr_uH = g_motor_params.Lm_uH + g_motor_params.Ls_uH / 2;
    if (g_motor_params.Rr_mOhm <= 0 || Lr_uH <= 0) {
        UART_SendStr("@IROT:ERROR:RR_OR_LR_NOT_MEASURED\r\n");
        return -2;
    }
    g_motor_params.Tr_rotor_us = (int32_t)(((int64_t)Lr_uH * 1000LL) /
                                          (int64_t)g_motor_params.Rr_mOhm);
    UART_SendTelemetry("@IROT:OK:Lr=%ld:Rr=%ld:Tr=%ld\r\n",
                       (long)Lr_uH, (long)g_motor_params.Rr_mOhm,
                       (long)g_motor_params.Tr_rotor_us);
    Autotune_PrintParams();
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
    for (uint16_t d = 5; d <= 50; d++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:OEW:ABORTED\r\n"); return -5; }
        PWM_SetDuty1(d,0,0); PWM_SetDuty2(d,0,0);
        delay_us(500);
        /* При d<50 установившийся ток отрицателен: V_U=(2d/100−1)·Vbus<0.
         * Это штатно (метка кривой L(I) от обратного тока); на сохраняемый
         * Ls не влияет — импульс L меряется от сброшенного ~0 тока. */
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendTelemetry("@AT:OEW:ERROR:OVERCURRENT I=%ld\r\n",(long)I_ss); return -6; }
        uint32_t saved_ccmr1_1 = TIM1->CCMR1; uint32_t saved_ccmr1_8 = TIM8->CCMR1;
        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE; TIM8->CCMR1 &= ~TIM_CCMR1_OC1PE;
        uint16_t half = (uint16_t)(period / 2U);
        uint16_t ccr  = (uint16_t)(((uint32_t)d * period) / 100U);

        const uint32_t pwm_period_us = 1000000U / 5000U;
        const uint8_t  n_periods     = 3;
        const uint32_t dt_total_us   = n_periods * pwm_period_us;
        const int32_t  di_min_ma     = 30;
        const int32_t  di_max_ma     = 1000;
        const int32_t  reset_i_th    = 20;
        const uint16_t raw_min       = 50;
        const uint16_t raw_max       = 4045;

        int32_t L_samples[5];
        uint8_t n_valid = 0;

        /* Сброс дифференциального тока: оба инвертора в нейтраль (half). */
        TIM1->CCR1 = half; TIM8->CCR1 = half;
        TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
        pwm_wait_periods(1);
        for (uint8_t w = 0; w < 200; w++) {
            ADC_StartConversion();
            if (at_abs32(AT_ReadCurrent_mA()) < reset_i_th) break;
            delay_us(500);
        }

        for (uint8_t attempt = 0; attempt < 3; attempt++) {
            if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:OEW:ABORTED\r\n"); return -5; }

            /* Измерение в одинаковой фазе счётчика. */
            TIM1->CCR1 = half; TIM8->CCR1 = half;
            TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
            pwm_wait_periods(n_periods);
            ADC_StartConversion();
            int32_t i0 = AT_ReadCurrent_mA();
            uint16_t raw0 = AT_GetRawChannel(g_motor_params.current_channel);

            /* Дифференциальный импульс напряжения длительностью n_periods.
             * mode 2 (TIM8 активен при CNT>CCR): ОДИНАКОВЫЙ CCR на обоих —
             * тогда V_U = (50+ccr)%·Vbus − (50−ccr)%·Vbus = 2·ccr%·Vbus.
             * (half−ccr на TIM8 дал бы V_U=0 — баг из эпохи mode 1.) */
            TIM1->CCR1 = (uint16_t)(half + ccr); TIM8->CCR1 = (uint16_t)(half + ccr);
            TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
            pwm_wait_periods(n_periods);
            ADC_StartConversion();
            int32_t i1 = AT_ReadCurrent_mA();
            uint16_t raw1 = AT_GetRawChannel(g_motor_params.current_channel);

            int32_t di = i1 - i0;
            int32_t di_abs = di < 0 ? -di : di;

            int32_t Ls_oew = 0;
            int32_t u_L = 0;
            int32_t u_R = 0;
            uint8_t skip = 0;
            if (raw0 < raw_min || raw0 > raw_max || raw1 < raw_min || raw1 > raw_max) {
                skip = 1;
            } else if (di_abs < di_min_ma || di_abs > di_max_ma) {
                skip = 1;
            } else {
                int32_t U_eff = (int32_t)(((int64_t)vbus * d * 2) / 100U);
                int32_t i_avg = (i0 + i1) / 2;
                u_L = U_eff;
                if (g_motor_params.Rs_mOhm > 0) {
                    u_R = (int32_t)(((int64_t)i_avg * g_motor_params.Rs_mOhm) / 1000LL);
                    u_L = U_eff - u_R;
                }
                if (u_L > 0) {
                    Ls_oew = (int32_t)(((int64_t)u_L * dt_total_us) / di_abs);
                    L_samples[n_valid++] = Ls_oew;
                }
            }

            UART_SendTelemetry("@AT:OEW:LS_STEP:D=%u:dt=%u:UL=%ld:UR=%ld:RAW0=%u:RAW1=%u:I0=%ld:I1=%ld:dI=%ld:L=%ld:SKIP=%u:ATT=%u\r\n",
                               (unsigned)d, (unsigned)dt_total_us,
                               (long)u_L, (long)u_R,
                               (unsigned)raw0, (unsigned)raw1,
                               (long)i0, (long)i1, (long)di, (long)Ls_oew,
                               (unsigned)skip, (unsigned)(attempt + 1));
        }
        TIM1->CCMR1 = saved_ccmr1_1; TIM8->CCMR1 = saved_ccmr1_8;

        int32_t Ls_oew = 0;
        if (n_valid > 0) {
            Ls_oew = median_small(L_samples, n_valid);
        }
        if (ci < 64 && I_ss > 100 && Ls_oew > 0) {
            g_motor_params.curve[ci].current_ma = I_ss;
            g_motor_params.curve[ci].inductance_uH = Ls_oew;
            ci++;
        }
        if ((d % 10) == 0) UART_SendTelemetry("@AT:OEW:PROG=%u/50:D=%u:I=%ld:L=%ld\r\n",(unsigned)d,(unsigned)d,(long)I_ss,(long)Ls_oew);
    }
    both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn);

    curve_sort_by_current(g_motor_params.curve, ci);
    ci = curve_filter_outliers(g_motor_params.curve, ci);
    g_motor_params.curve_count = ci;

    /* Берём медиану первых 5–10 точек с наименьшим током — это наиболее
     * близко к ненасыщенной индуктивности. */
    uint8_t n_L0 = (ci < 10) ? ci : 10;
    int32_t L0_buf[10];
    for (uint8_t i = 0; i < n_L0; i++) L0_buf[i] = g_motor_params.curve[i].inductance_uH;
    max_Ls_oew = (n_L0 > 0) ? median_small(L0_buf, n_L0) : 0;
    /* Валидация: max_Ls_oew из кривой L(I) — если мусор, не перезаписывать. */
    if (AT_SaneLs(max_Ls_oew) > 0) {
        g_motor_params.Ls_uH = max_Ls_oew;
    } else {
        UART_SendTelemetry("@AT:OEW:WARN:LS_REJECT:%ld\r\n", (long)max_Ls_oew);
    }

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
    const int32_t n_pts  = 3040;       /* 3040·31 мрад = 94240 ≈ 15·6283 = 15 полных
                                          периодов при 5 Гц (было 3000 = 14.8 — утечка) */
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

        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        /* OEW mode 2 (TIM8 активен при CNT>CCR): ТЕ ЖЕ duty на оба инвертора —
         * тогда V_U = V_U1 − V_U2 = (2d/100−1)·Vbus = 2·(sa·rr_amp/32768)%·Vbus —
         * ЧИСТЫЙ синус БЕЗ DC-смещения. Раньше (d2=100) на обмотке было
         * 50%·Vbus = 30 В постоянки при 60 В → 2.3 А DC → глубокое насыщение
         * → Rtotal < Rs (Id в 3.6× больше реального). */
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(1000);
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
     * Корреляция: (2/N)*Σ i*sin = I_amp*cos(φ), (2/N)*Σ i*cos = I_amp*sin(φ). */
    int64_t id_raw = (sum_i_sin * 2LL) / n_pts; /* I_d * 32768 */
    int64_t iq_raw = (sum_i_cos * 2LL) / n_pts; /* I_q * 32768 */

    /* Амплитуда напряжения в mV (sin, пик).
     * OEW mode 2 с d1=d2: V_U = (2d/100−1)·Vbus = 2·(sa·rr_amp/32768)%·Vbus
     * → пик = 2·rr_amp%·Vbus (×2 от одиночного инвертора!). */
    int64_t v_amp = (((int64_t)vbus * rr_amp) * 2LL) / 100LL;

    /* Активная составляющая импеданса: R = V·Id/(Id²+Iq²) = Re(Z).
     * V/Id завышало бы R на (1+(Iq/Id)²) — на 5 Гц мало, но строгость
     * даром. Знак Id зависит от polarity датчика — берём модуль.
     * В mA: V·1000·id_mA / (id_mA² + iq_mA²), int64 безопасно. */
    if (id_raw == 0) { UART_SendStr("@AT:RR:ERROR:NO_RESISTIVE_CURRENT\r\n"); return -9; }
    int64_t id_abs = id_raw < 0 ? -id_raw : id_raw;
    int32_t id_mA = (int32_t)(id_abs / 32768LL);
    int32_t iq_mA = (int32_t)(iq_raw / 32768LL);
    if (id_mA == 0) { UART_SendStr("@AT:RR:ERROR:NO_RESISTIVE_CURRENT\r\n"); return -9; }
    int64_t i_sq_sum_pp = (int64_t)id_mA * id_mA + (int64_t)iq_mA * iq_mA;
    int32_t r_total_pp = (int32_t)((v_amp * 1000LL * id_mA) / i_sq_sum_pp);

    /* Телеметрия: активная/реактивная составляющие тока и угол φ (°). */
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
        /* OEW диф-драйв (mode 2, d1=d2): V_U = 2·(sa·v_mag/32768)%·Vbus —
         * ЧИСТЫЙ AC без DC. v_mag ≤ 40% чтобы da=50±v_mag не клипповал
         * (50+40=90 < 100). Раньше v_mag=80 с d2=100 давал 30 В DC на обмотке
         * + клиппинг da=130 → v_rms завышал фундаментал. */
        int32_t v_mag = (int32_t)(((int64_t)f_mHz * 40) / 50000);
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
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(2000);
        if ((f_mHz % 5000) == 0) UART_SendTelemetry("@AT:NOLOAD:RAMP:F=%ld:V=%ld%%\r\n",(long)(f_mHz/1000),(long)v_mag);
    }
    UART_SendStr("@AT:NOLOAD:MEASURE:START\r\n");
    int64_t i_sum = 0; const int32_t n_meas = 500;
    for (int32_t i = 0; i < n_meas; i++) {
        if (g_autotune_abort) { both_disable(); NVIC_EnableIRQ(ADC1_2_IRQn); UART_SendStr("@AT:NOLOAD:ABORTED\r\n"); return -6; }
        theta += 628; if (theta >= 6283) theta -= 6283;
        int32_t sa = at_sin_q15(theta);
        int32_t da = 50 + (int32_t)(((int64_t)sa * 40) / 32768);
        if (da < 0) da = 0; if (da > 100) da = 100;
        int32_t sb = at_sin_q15(theta - 2094);
        int32_t sc = at_sin_q15(theta + 2094);
        int32_t db = 50 + (int32_t)(((int64_t)sb * 40) / 32768);
        int32_t dc = 50 + (int32_t)(((int64_t)sc * 40) / 32768);
        if (db < 0) db = 0; if (db > 100) db = 100;
        if (dc < 0) dc = 0; if (dc > 100) dc = 100;
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc); delay_us(2000);
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
    /* v_rms: 40 — модуляция в цикле измерения (v_mag на 50 Гц),
     * ×2 — дифференциальный OEW-драйв (V_U = 2·v_mag%·Vbus),
     * 707/1000 — sin→RMS (1/√2). Пик = 80%·Vbus. */
    int32_t v_rms = (int32_t)(((int64_t)vbus * 2 * 40 * 707) / (100 * 1000));
    int32_t z_total = (int32_t)(((int64_t)v_rms * 1000) / i_rms);
    int32_t l_total = (int32_t)(((int64_t)z_total * 1000) / 314);
    /* Lm < 0 — это признак мусорной Ls (l_total < Ls физически невозможен
     * для АД: Ls = Lσs + Lm·(...) < Lm + Lσ). Раньше молчаливый clamp в 0
     * скрывал каскад Ls→Lm→Lr→Tr. Теперь — явная ошибка, ничего не пишем. */
    if (l_total <= g_motor_params.Ls_uH) {
        UART_SendTelemetry("@AT:NOLOAD:ERROR:LTOTAL_LTE_LS:Ltotal=%ld:Ls=%ld\r\n",
                           (long)l_total, (long)g_motor_params.Ls_uH);
        return -8;
    }
    /* Здесь l_total > Ls гарантирован (проверено выше) → Lm > 0 всегда. */
    g_motor_params.Lm_uH = l_total - g_motor_params.Ls_uH;
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
        int32_t vbus_idle = ADC_GetVbus_mV();
        int32_t Ls_uH = AT_MeasureLs_uH(0, &TIM1->CCR1, vbus_idle, 10,
                                         g_motor_params.Rs_mOhm,
                                         g_motor_params.current_channel);
        PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_disable();
        if (Ls_uH <= 0) Ls_uH = 1;
        ls_vals[cnt++] = Ls_uH;
        UART_SendTelemetry("@AT:LSPOS:MEAS:POS=%u/5:Ls=%ld:I=%ld\r\n",(unsigned)(pos+1),(long)Ls_uH,(long)I_ss);
    }
    NVIC_EnableIRQ(ADC1_2_IRQn);
    AtStat32 stat; stat.count = cnt;
    for (uint8_t i = 0; i < cnt; i++) stat.values[i] = ls_vals[i];
    stat_compute(&stat);
    UART_SendTelemetry("@AT:LSPOS:OK:MEDIAN=%ld:MIN=%ld:MAX=%ld:SPREAD=%ld%%\r\n",(long)stat.median,(long)stat.min,(long)stat.max,(long)stat.spread_pct);
    if (stat.spread_pct > 20) UART_SendStr("@AT:LSPOS:WARN:HIGH_SPREAD:SALIENCY_OR_NOISE\r\n");
    /* LSPOS раньше НЕ сохранял результат — Ls оставался от предыдущего
     * теста (idle/pairs), часто мусорный. Сохраняем медиану с валидацией. */
    if (AT_SaneLs(stat.median) > 0) {
        g_motor_params.Ls_uH = stat.median;
        UART_SendTelemetry("@AT:LSPOS:SAVE:Ls=%ld\r\n", (long)g_motor_params.Ls_uH);
    } else {
        UART_SendTelemetry("@AT:LSPOS:WARN:LS_REJECT:%ld\r\n", (long)stat.median);
    }
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
