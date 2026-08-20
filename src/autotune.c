#include "autotune.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "foc.h"
#include "protect.h"
#include "vf_control.h"   /* VFC_IsRunning — CORDIC-гвард в AT_SafetyCheck */
#include "stm32g474xx.h"
#include "cordic_math.h"
#include <string.h>
#include <limits.h>

MotorParams g_motor_params;
volatile uint8_t g_autotune_abort = 0;
volatile uint8_t g_autotune_busy = 0;   /* тест выполняется (main loop занят) */

int Autotune_IsActive(void)
{
    return g_autotune_busy != 0;
}

/* Последние расчётные Kp/Ki (для автоприменения через pi=N) */
static int32_t last_kp = 0;
static int32_t last_ki = 0;
static int32_t last_bw_hz = 0;
static int32_t last_ls_uH = 0;
static int32_t last_rs_mOhm = 0;
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

static void both_enable(void);
static void both_disable(void);
static int8_t AT_SafetyCheck(void);   /* определена ниже — для ch/chu/chv/chw (AT-2S-01) */

/* ── Lifecycle: централизованный вход/выход для autotune-тестов ──
 * AT_TestBegin: disable ADC IRQ, PWM off, calibrate offsets, init DWT.
 * AT_TestEnd:   safe PWM shutdown, inverters off, re-enable ADC IRQ.
 * Гарантирует восстановление состояния при любом выходе (goto cleanup). */
typedef struct {
    uint8_t irq_was_enabled;   /* прежнее состояние ADC1_2_IRQn ДО теста */
} AT_TestSession;

static void AT_TestBegin(AT_TestSession *s) {
    g_autotune_busy = 1;
    /* Сохраняем прежнее состояние IRQ (ревью Gemini п.6): если оно было
     * выключено ДО теста внешним кодом — AT_TestEnd не должен включать. */
    s->irq_was_enabled = NVIC_GetEnableIRQ(ADC1_2_IRQn) ? 1U : 0U;
    if (s->irq_was_enabled) NVIC_DisableIRQ(ADC1_2_IRQn);
    /* Ревью (совместимость adc↔autotune): явный сброс injected-группы
     * (JADSTART=0) ДО калибровки — иначе adc2_read() в ADC_CalibrateOffsets
     * вернул бы sentinel (adc.c: при JADSTART regular запрещён), и offset
     * тихо остались бы старыми. Идемпотентно: при JADSTART=0 — только
     * очистка флагов. */
    ADC_InjectedStop();
    PWM_Disable();
    ADC_CalibrateOffsets();
    dwt_init();
}

static void AT_TestEnd(AT_TestSession *s) {
    g_autotune_busy = 0;
    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_disable();
    /* Восстановить ТОЧНО прежнее состояние, а не безусловно включить. */
    if (s->irq_was_enabled) NVIC_EnableIRQ(ADC1_2_IRQn);
}

static int32_t at_abs32(int32_t x) { return (x < 0) ? -x : x; }

/* Валидация правдоподобия Ls (мкГн). Диапазон для АД 0.1..1.5 кВт:
 * 0.5..500 мГн (500..500000 мкГн). Мусор из AT_MeasureLs_uH (почти
 * нулевые ΔI) даёт Ls в Гн — отсекаем. Возвращает значение при
 * прохождении, иначе 0 (REJECT — не перезаписывать сохранённое). */
static int32_t AT_SaneLs(int32_t l_uh) {
    if (l_uh < 500 || l_uh > 500000) return 0;
    return l_uh;
}

/* Валидация правдоподобия Rs (мОм). Для АД 0.1..1.5 кВт типично
 * от десятков мОм до десятков Ом. Защита от обрыва/коротких контактов. */
#define AT_SANE_RS_MIN_MOHM  10
#define AT_SANE_RS_MAX_MOHM  100000
static int32_t AT_SaneRs(int32_t r_mohm) {
    if (r_mohm < AT_SANE_RS_MIN_MOHM || r_mohm > AT_SANE_RS_MAX_MOHM) return 0;
    return r_mohm;
}

/* Параметры Autotune_Idle */
#define AT_IDLE_REPEATS          AUTOTUNE_MAX_REPEATS  /* из autotune.h — размер values[] в AtStat32 (не дублировать!) */
#define AT_IDLE_DUTY_MAX         50
#define AT_IDLE_RS_DUTY          10
#define AT_IDLE_RS_SETTLE_US     100000
#define AT_IDLE_OPEN_PHASE_PCT   10      /* I < I_expected / 10 → обрыв фазы */
#define AT_IDLE_SHORT_I_MA       6000    /* I при duty=1 выше → КЗ или низкое Rs */
#define AT_IDLE_CURVE_I_MIN_MA   100     /* мин. I для точки кривой насыщения */
#define AT_IDLE_L0_WINDOW        10      /* сколько первых точек для L0 */
#define AT_IDLE_ISAT_THR_PCT     70      /* L падает до 70% от L0 → Isat */
#define AT_IDLE_MIN_CURVE_POINTS 8       /* мин. точек кривой для расчёта Isat */
#define AT_IDLE_SETTLE_US        5000    /* 25 PWM периодов — мин. время установления тока */

/* Параметры Autotune_MeasureRr */
#define AT_RR_FREQ_HZ            5
#define AT_RR_NPTS               3040    /* 15 полных периодов, 1 мс/точка */
#define AT_RR_THETA_STEP         31      /* 2π/200 ≈ 0.0314 рад = 31 мрад */
#define AT_RR_PWM_PERIODS        5       /* 5·200 us = 1000 us — интервал выборки */
#define AT_RR_SAMP_PER_PERIOD    200     /* 5·200 us · 200 = 200 ms = 1/5 Гц */
#define AT_RR_TAU_MARGIN         5       /* ждём 5·L/R перед измерением */
#define AT_RR_SETTLE_MAX_US      200000  /* 200 мс — потолок */
#define AT_RR_AMP_CAL_PCT        2       /* амплитуда для калибровочного периода */
#define AT_RR_AMP_MIN_PCT        1
#define AT_RR_AMP_MAX_PCT        30
#define AT_RR_TARGET_I_MA        2000    /* 2 А пик, если Isat неизвестен */
#define AT_RR_OFFSET_SAMPLES     8       /* усреднение DC-offset */
#define AT_RR_MIN_SNR_PCT        90      /* ≥ 90% энергии в фундаментале ≈ 10 дБ */
#define AT_RR_RR_MIN_MUL_NUM     1
#define AT_RR_RR_MIN_MUL_DEN     5       /* Rr > 0.2·Rs */
#define AT_RR_RR_MAX_MUL         5       /* Rr < 5·Rs */
#define AT_RR_DUTY_BASE          50
#define AT_RR_DUTY_MAX           100

/* Параметры Autotune_MeasureNoLoad */
#define AT_NOLOAD_RAMP_FMAX_MHZ  50000
#define AT_NOLOAD_RAMP_STEP_MHZ  100
#define AT_NOLOAD_RAMP_VMAG_MAX  40      /* % модуляции на 50 Гц */
#define AT_NOLOAD_PWM_PERIODS    10     /* 10·200 us = 2 ms — период захвата */
#define AT_NOLOAD_FLUX_SETTLE_US 300000  /* 300 мс на стабилизацию потока */
#define AT_NOLOAD_MEAS_N         500
#define AT_NOLOAD_MEAS_VMAG      40
#define AT_NOLOAD_RAMP_SAMPLES_PER_PERIOD 10
#define AT_NOLOAD_MEAS_THETA_STEP ((int32_t)TWO_PI_X1000 / AT_NOLOAD_RAMP_SAMPLES_PER_PERIOD)
#define AT_NOLOAD_RAMP_THETA_DEN (AT_NOLOAD_RAMP_FMAX_MHZ * AT_NOLOAD_RAMP_SAMPLES_PER_PERIOD)
#define AT_NOLOAD_OMEGA_50HZ     314     /* 2π·50, мрад/рад — для L=X/ω */

/* Параметры Autotune_Scope */
#define AT_SCOPE_N               128     /* количество точек осциллограммы */
#define AT_SCOPE_DUTY            20      /* duty для фазы A, % */
#define AT_SCOPE_MAX_CURRENT_MA  8000    /* предел тока для Scope */

/* Параметры Autotune_CalcPI.
 * Должны соответствовать foc.c/pwm.c (5 кГц, 200 мкс). */
#define AT_PI_FOC_FS_HZ          5000
#define AT_PI_FOC_TS_US          200
#define AT_PI_BW_MIN_HZ          100
#define AT_PI_BW_MAX_HZ          (AT_PI_FOC_FS_HZ / 10)
#define TWO_PI_X1000             6283LL
#define SQRT3_X1000              1732LL
#define AT_PI_MRAD               3142
#define AT_PI_2_MRAD             1571
#define AT_TWO_PI_3_MRAD         2094

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

/* Целочисленный isqrt для uint64_t (Ньютон/бисекция). */
static uint64_t isqrt_u64(uint64_t x) {
    if (x == 0) return 0;
    uint64_t r = x;
    uint64_t t;
    do {
        t = (r + x / r) / 2;
        if (t >= r) break;
        r = t;
    } while (1);
    return r;
}

static void stat_compute(AtStat32 *s) {
    if (s->count == 0) { s->median = s->min = s->max = 0; s->spread_pct = 0; return; }
    /* Defensive clamp (ревью Gemini п.5): count больше размера values[]
     * переполнил бы стековый tmp через memcpy. В текущем коде count
     * ограничен циклами ≤ AUTOTUNE_MAX_REPEATS, но защита дешёвая. */
    uint8_t count = (s->count > AUTOTUNE_MAX_REPEATS) ? AUTOTUNE_MAX_REPEATS : s->count;
    int32_t tmp[AUTOTUNE_MAX_REPEATS];  /* буфер сортировки — размер из autotune.h */
    memcpy(tmp, s->values, sizeof(int32_t) * count);
    sort_small(tmp, count);
    s->median = tmp[count / 2];
    s->min    = tmp[0];
    s->max    = tmp[count - 1];
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
 * Это гарантирует, что измерения происходят в одинаковой фазе ШИМ.
 * Ревью AT-02: раньше — бесконечный busy-wait; после аппаратного fault
 * (MOE снят, TIM1 остановлен) UIF не появится никогда. Теперь ожидание
 * отменяемое: abort/fault/остановка таймера/таймаут возвращают <0.
 * Коды: -1 abort, -2 fault, -3 таймер остановлен, -4 таймаут (100 мс). */
static int pwm_wait_periods(uint8_t n) {
    for (uint8_t i = 0; i < n; i++) {
        TIM1->SR &= ~TIM_SR_UIF;
        uint32_t start = DWT->CYCCNT;
        const uint32_t limit = SystemCoreClock / 10U;   /* 100 мс потолок */
        while (!(TIM1->SR & TIM_SR_UIF)) {
            if (g_autotune_abort) return -1;
            if (PROTECT_IsFault()) return -2;
            if (!(TIM1->CR1 & TIM_CR1_CEN)) return -3;
            if ((uint32_t)(DWT->CYCCNT - start) > limit) return -4;
        }
    }
    return 0;
}

/* Ждём одну выборку injected-группы, синхронизированную с TIM1_TRGO.
 * n_periods — сколько периодов ШИМ ждать перед чтением.
 * После чтения adc_data содержит i1/i2/ires/vbus.
 * Возвращает 0 при успехе; -1 если JEOS не пришёл (сбой триггерной
 * цепочки TIM1_TRGO→ADC) — данные НЕ читаются (были бы stale, ревью
 * Gemini п.4), вызывающий код прерывает тест. */
static int at_injected_sync(uint8_t n_periods) {
    /* ADC1 — injected master, ADC2 — slave; arm только через ADC_InjectedStart().
     * JEOS ISR публикует новый AdcFrame; этот legacy-диагностический путь ждёт
     * роста sequence и принимает raw-фреймы со статусом WINDOW_INVALID — он не
     * владеет измеренной control-апертурой. Публиковать синтетическое валидное
     * окно/admission здесь ЗАПРЕЩЕНО (P0-C, аудит OEW-HS-1): валидный
     * сектор/окно — исключительно prerogativa map-verified FOC. */

    AdcFrame fr;
    if (!ADC_GetLatestFrame(&fr)) return -1;
    uint32_t seq0 = fr.sequence;
    if (ADC_InjectedStart() != 0) return -1;

    for (uint8_t p = 0; p < n_periods; p++) {
        if (pwm_wait_periods(1) != 0) return -1;
        uint32_t t = 10000;
        do {
            if (!ADC_GetLatestFrame(&fr)) { t = 0; break; }
            if (--t == 0) break;
        } while (fr.sequence == seq0);
        if (t == 0) return -1;   /* новый фрейм не пришёл (таймаут) */
        if (fr.status == ADC_FRAME_OVERRUN || fr.status == ADC_FRAME_QUEUE_OVERRUN ||
            fr.status == ADC_FRAME_DESYNCHRONIZED || fr.status == ADC_FRAME_NOT_ARMED ||
            fr.status == ADC_FRAME_JEOS_TIMEOUT) return -1;
        seq0 = fr.sequence;
    }
    return 0;
}

static uint16_t AT_GetRawChannel(AtCurrentChannel ch) {
    switch (ch) {
        case AT_CH_I1: return ADC_GetRawI1();
        case AT_CH_I2: return ADC_GetRawI2();
        case AT_CH_IN: return ADC_GetRawIres();
        default:       return ADC_GetRawIres();
    }
}

/* Расчёт Isat из кривой L(I) с линейной интерполяцией.
 * Ищем 2 consecutive points где L ≤ threshold = L0 * pct / 100.
 * Isat интерполируется между точкой выше и ниже threshold. */
static int32_t AT_CalcIsat(const AtCurvePoint *curve, uint8_t n,
                            int32_t L0, int32_t pct, const char *tag)
{
    if (n < AT_IDLE_MIN_CURVE_POINTS || L0 <= 0) {
        UART_SendTelemetry("@AT:ISAT:%s:SKIP:N=%u:L0=%ld\r\n",
                           tag, (unsigned)n, (long)L0);
        return 0;
    }
    int32_t threshold = L0 * pct / 100;
    /* Ревью Claude (баг 1): интерполировать между ПОСЛЕДНЕЙ точкой ВЫШЕ
     * порога и ПЕРВОЙ точкой НИЖЕ. Раньше `consec>=2` интерполировал между
     * двумя точками ниже порога — систематическое смещение Isat (пример:
     * (300,750),(400,650),(500,600) при thr=700 давал 300 вместо 350).
     * «Два подряд ниже» — только подтверждение, не пара для интерполяции. */
    uint8_t consec = 0;
    uint8_t first_below = 0xFF;
    for (uint8_t i = 0; i < n; i++) {
        if (curve[i].inductance_uH <= threshold) {
            if (first_below == 0xFF) first_below = i;
            consec++;
            if (consec >= 2) {
                /* Ревью Gemini (п.1): если уже ПЕРВАЯ точка кривой ниже
                 * порога, first_below=0 и lo = 0-1 = 255 (uint8_t wrap) —
                 * чтение curve[255] за границей массива. Точки ВЫШЕ порога
                 * нет — интерполяция невозможна, консервативно возвращаем
                 * ток первой точки (насыщение наступило до минимального
                 * измеренного тока). */
                if (first_below == 0) {
                    UART_SendTelemetry(
                        "@AT:ISAT:%s:FIRST_POINT_BELOW:Isat_mA=%ld:L0=%ld:THR=%ld\r\n",
                        tag, (long)curve[0].current_ma, (long)L0, (long)threshold);
                    return curve[0].current_ma;
                }
                /* Найдено подтверждение: first_below-1 (ещё выше порога)
                 * и first_below (первая ниже) — правильная пара. */
                uint8_t lo = first_below - 1;
                uint8_t hi = first_below;
                int32_t I_lo = curve[lo].current_ma;
                int32_t I_hi = curve[hi].current_ma;
                int32_t L_lo = curve[lo].inductance_uH;
                int32_t L_hi = curve[hi].inductance_uH;
                int32_t Isat;
                if (L_lo == L_hi) {
                    Isat = I_lo;
                } else {
                    Isat = I_lo + (int32_t)(((int64_t)(threshold - L_lo) *
                              (I_hi - I_lo)) / (L_hi - L_lo));
                }
                UART_SendTelemetry(
                    "@AT:ISAT:%s:Isat_mA=%ld:L0_uH=%ld:THR_uH=%ld:THR_PCT=%ld:"
                    "I_LO=%ld:L_LO=%ld:I_HI=%ld:L_HI=%ld\r\n",
                    tag, (long)Isat, (long)L0, (long)threshold, (long)pct,
                    (long)I_lo, (long)L_lo, (long)I_hi, (long)L_hi);
                return Isat;
            }
        } else {
            consec = 0;
            first_below = 0xFF;  /* сброс: точка вернулась выше порога (шум) —
                                  * подтверждение «2 подряд» должно начинаться
                                  * заново, иначе интерполяция по устаревшей паре */
        }
    }
    UART_SendTelemetry("@AT:ISAT:%s:NOT_FOUND:L0=%ld:THR=%ld\r\n",
                       tag, (long)L0, (long)threshold);
    return 0;
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
    AtCurvePoint tmp[AUTOTUNE_MAX_CURVE_POINTS];  /* размер из autotune.h */
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
    int8_t retcode = -1;
    g_autotune_busy = 1;
    UART_SendStr("@AT:CH_DETECT:START\r\n");
    /* Ревью AT-2S-01 (P0): safety-gate ДО возбуждения мостов — fault-latch
     * + Vbus-окно + VFC-исключение (AT_SafetyCheck). Без него команда ch
     * могла включить инверторы при недопустимой шине или активном fault. */
    if (AT_SafetyCheck() != 0) {
        UART_SendStr("@AT:CH_DETECT:ERROR:SAFETY\r\n");
        goto cleanup;
    }
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

    /* Адаптивное ожидание нарастания тока: при OEW-подключении полная
     * индуктивность контура велика (десятки мГн), за фиксированные 300 мкс
     * ток может не успеть подняться выше порога 30 мА (ΔI = Vbus·Ton/L,
     * при 5% duty Ton за 300 мкс = 15 мкс → 60В·15мкс/50мГн = 18 мА).
     * Ждём до 20 мс, выходим раньше при |ΔI| ≥ 50 мА или перегрузке. */
    int32_t i1_test = i1_zero, i2_test = i2_zero, in_test = in_zero;
    for (uint8_t w = 0; w < 40; w++) {
        delay_us(500);
        ADC_StartConversion();
        i1_test = ADC_GetI1_mA();
        i2_test = ADC_GetI2_mA();
        in_test = ADC_GetIres_mA();
        int32_t m = at_abs32(i1_test - i1_zero);
        if (at_abs32(i2_test - i2_zero) > m) m = at_abs32(i2_test - i2_zero);
        if (at_abs32(in_test - in_zero) > m) m = at_abs32(in_test - in_zero);
        if (m >= 50) break;
        if (at_abs32(i1_test) > 2000 || at_abs32(i2_test) > 2000 ||
            at_abs32(in_test) > 2000) break;
    }

    UART_SendTelemetry("@DBG:CH:POST_DELAY:CCR1=%lu:CNT1=%lu:CR1=0x%08lX:BDTR=0x%08lX:FAULT=%d\r\n",
        (unsigned long)TIM1->CCR1, (unsigned long)TIM1->CNT,
        (unsigned long)TIM1->CR1, (unsigned long)TIM1->BDTR, PROTECT_IsFault());

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
        goto cleanup;
    }

    g_motor_params.current_channel = ch;
    g_motor_params.current_sign    = (signed_current >= 0) ? 1 : -1;
    g_motor_params.measured_mask  |= AT_VALID_CH;

    UART_SendTelemetry("@AT:CH_DETECT:OK:CH=%d:I=%ld:SIGN=%ld\r\n",
                       (int)ch, (long)best, (long)g_motor_params.current_sign);
    retcode = 0;

cleanup:
    g_autotune_busy = 0;
    return retcode;
}

/* ── Debug: диагностика отклика каналов на фазу (ревью AT-03/06) ──────────
 * Autotune_ProbePhase(phase): возбуждает фазу U(0)/V(1)/W(2) и печатает
 * отклик ВСЕХ трёх каналов со ЗНАКАМИ (d_i1/d_i2/d_ires — signed Δ от нуля).
 * НЕ меняет current_channel — чистая диагностика для подтверждения модели
 * «шунт противоположного инвертора»: при Inv1-модуляции (Inv2 на GND) ток
 * любой фазы всегда идёт через шунт I2 (+), I1 видит только в нулевом
 * векторе со знаком минус, Ires (CT) — только переменную составляющую.
 * Команды UART: chu / chv / chw. */
int8_t Autotune_ProbePhase(uint8_t phase) {
    const char *names[3] = { "U", "V", "W" };
    int8_t retcode = -1;
    if (phase > 2) return -9;
    g_autotune_busy = 1;
    UART_SendTelemetry("@DBG:CH%c:START\r\n", names[phase][0]);
    /* Ревью AT-2S-01 (P0): safety-gate ДО возбуждения мостов. */
    if (AT_SafetyCheck() != 0) {
        UART_SendTelemetry("@DBG:CH%c:ERROR:SAFETY\r\n", names[phase][0]);
        goto cleanup;
    }
    g_autotune_abort = 0;
    if (FOC_IsRunning()) FOC_Stop();

    NVIC_DisableIRQ(ADC1_2_IRQn);
    PWM_Disable();
    ADC_InjectedStop();
    __DSB();
    ADC_CalibrateOffsets();

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    ADC_StartConversion();
    int32_t i1_zero = ADC_GetI1_mA();
    int32_t i2_zero = ADC_GetI2_mA();
    int32_t in_zero = ADC_GetIres_mA();
    UART_SendTelemetry("@DBG:CH%c:ZERO:i1=%ld:i2=%ld:ires=%ld:vbus=%ld\r\n",
                       names[phase][0], (long)i1_zero, (long)i2_zero,
                       (long)in_zero, (long)ADC_GetVbus_mV());

    switch (phase) {
        case 0: PWM_SetDuty1(5, 0, 0); break;
        case 1: PWM_SetDuty1(0, 5, 0); break;
        case 2: PWM_SetDuty1(0, 0, 5); break;
    }

    /* Адаптивное ожидание нарастания тока (как в DetectChannel): до 20 мс,
     * выход при |ΔI| >= 50 мА на любом канале или перегрузке > 2 А. */
    int32_t i1_t = i1_zero, i2_t = i2_zero, in_t = in_zero;
    for (uint8_t w = 0; w < 40; w++) {
        delay_us(500);
        ADC_StartConversion();
        i1_t = ADC_GetI1_mA();
        i2_t = ADC_GetI2_mA();
        in_t = ADC_GetIres_mA();
        int32_t m = at_abs32(i1_t - i1_zero);
        if (at_abs32(i2_t - i2_zero) > m) m = at_abs32(i2_t - i2_zero);
        if (at_abs32(in_t - in_zero) > m) m = at_abs32(in_t - in_zero);
        if (m >= 50) break;
        if (at_abs32(i1_t) > 2000 || at_abs32(i2_t) > 2000 ||
            at_abs32(in_t) > 2000) break;
    }

    UART_SendTelemetry("@DBG:CH%c:TEST:i1=%ld:i2=%ld:ires=%ld:vbus=%ld\r\n",
                       names[phase][0], (long)i1_t, (long)i2_t,
                       (long)in_t, (long)ADC_GetVbus_mV());
    /* Сигнатуры: d_* — signed Δ, знак важен (I1 в нулевом векторе — минус). */
    UART_SendTelemetry("@DBG:CH%c:DELTA:d_i1=%ld:d_i2=%ld:d_ires=%ld\r\n",
                       names[phase][0],
                       (long)(i1_t - i1_zero), (long)(i2_t - i2_zero),
                       (long)(in_t - in_zero));

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_disable();
    NVIC_EnableIRQ(ADC1_2_IRQn);
    UART_SendTelemetry("@DBG:CH%c:OK\r\n", names[phase][0]);
    retcode = 0;

cleanup:
    g_autotune_busy = 0;
    return retcode;
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

    /* CORDIC-гвард: аппаратный CORDIC один, без арбитража. Autotune
     * (at_sin_q15/CORDIC_Atan2 из main) при работающем V/f был бы прерван
     * TIM6 ISR (VFC_Update → CORDIC_SinCos) посреди транзакции CSR/WDATA/
     * RDATA — оба получили бы мусор (NVIC_DisableIRQ в main.c маскирует
     * только ADC1_2, не TIM6). Плюс autotune сам управляет инвертором —
     * одновременный V/f физически недопустим. */
    if (VFC_IsRunning()) {
        UART_SendStr("@AT:ERROR:VFC_RUNNING_STOP_FIRST\r\n");
        return -1;
    }

    ADC_StartConversion();
    int32_t vbus = ADC_GetVbus_mV();
    if (vbus < 12000) {
        UART_SendTelemetry("@AT:ERROR:VBUS_LOW:%ld\r\n", (long)vbus);
        return -2;
    }
    /* Ревью AT-05: верхний предел Vbus — защита от запуска автотюна на
     * высоком напряжении (стенд 8-80 В, плата до 400 В, тесты рассчитаны
     * на низковольтный режим; фиксированные duty на 350+ В опасны). */
    if (vbus > 350000) {
        UART_SendTelemetry("@AT:ERROR:VBUS_HIGH:%ld\r\n", (long)vbus);
        return -4;
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
        if (i1 <= 50 && i2 <= 50 && in <= 50) {
            ADC_CalibrateOffsets();
            break;
        }
        if (retry == 0) {
            UART_SendTelemetry("@AT:WARN:RESIDUAL_CURRENT:I1=%ld:I2=%ld:Ires=%ld:RETRYING\r\n",
                               (long)i1, (long)i2, (long)in);
        }
        delay_us(100000);
    }
    if (i1 > 50 || i2 > 50 || in > 50) {
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
    /* Целевое окно ΔI — значения, подобранные на стенде 06.08 (autotune.h). */
    const int32_t  di_target_min   = AT_DI_TARGET_MIN_MA;
    const int32_t  di_target_max   = AT_DI_TARGET_MAX_MA;
    const uint8_t  n_attempts      = 3;

    int32_t L_samples[AUTOTUNE_MAX_REPEATS];
    uint8_t n_valid = 0;
    uint32_t dt_us = 1000U;

    for (uint8_t attempt = 0; attempt < n_attempts; attempt++) {
        if (g_autotune_abort) return 0;

        /* Сброс тока в ноль: активная фаза замкнута на нижние ключи.
         * Ждем, пока |I| не упадет ниже 10 мА (но не более ~30 мс). */
        *ccr = 0;
        TIM1->EGR |= TIM_EGR_UG;
        TIM1->EGR &= ~TIM_EGR_UG;
        delay_us(pwm_period_us);
        for (uint8_t w = 0; w < 60; w++) {
            ADC_StartConversion();
            if (at_abs32(AT_ReadCurrentChannel_mA(ch)) < 10) break;
            delay_us(500);
        }

        /* Начальное измерение — после паузы ток должен быть близок к нулю. */
        ADC_StartConversion();
        int32_t i0 = AT_ReadCurrentChannel_mA(ch);

        /* Подаем напряжение на выбранную фазу, синхронно с обновлением ШИМ. */
        uint32_t ccr_val = ((uint32_t)duty_pct * ((uint32_t)PWM_GetARR() + 1U)) / 100U;
        if (ccr_val == 0) ccr_val = 1;
        *ccr = (uint16_t)ccr_val;
        TIM1->EGR |= TIM_EGR_UG;
        TIM1->EGR &= ~TIM_EGR_UG;

        /* dt выбираем кратным периоду ШИМ, чтобы всегда измерять
         * в одинаковой фазе счетчика. */
        uint32_t dt_aligned = ((dt_us + pwm_period_us / 2) / pwm_period_us) * pwm_period_us;
        if (dt_aligned < dt_min_us) dt_aligned = dt_min_us;
        if (dt_aligned > dt_max_us) dt_aligned = dt_max_us;

        delay_us(dt_aligned);

        /* Конечное измерение. */
        ADC_StartConversion();
        int32_t i1 = AT_ReadCurrentChannel_mA(ch);

        int32_t di = i1 - i0;
        int32_t di_abs = di < 0 ? -di : di;

        int32_t L_uH = 0;
        if (di_abs >= di_min_ma) {
            L_uH = (int32_t)(((int64_t)U_mV * dt_aligned) / di_abs);

            /* Поправка на активное сопротивление:
             * для RL-цепи L_true ≈ L_naive * (1 - di/(2*I_final)),
             * где I_final = U_mV / R [A] = U_mV * 1000 / R_mOhm [мА]. */
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

    /* Ревью AT-01: остановка FOC ДО safety-проверки — иначе при работающем
     * FOC (injected вооружена) ADC_StartConversion() читает устаревший кеш. */
    if (FOC_IsRunning()) FOC_Stop();

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    AT_TestSession session;
    AT_TestBegin(&session);

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    int32_t U[7], I[7];
    uint8_t valid_points = 0;
    int32_t vbus_initial = ADC_GetVbus_mV();
    int8_t retcode = 0;

    for (uint8_t k = 0; k < n; k++) {
        if (g_autotune_abort) {
            UART_SendStr("@AT:RS_IV:ABORTED\r\n");
            retcode = -5; goto rsiv_cleanup;
        }

        PWM_SetDuty1(duties[k], 0, 0);
        PWM_SetDuty2(100, 100, 100);
        delay_us(50000);

        int32_t i = AT_ReadCurrentMedian_mA();
        if (i < 0) i = -i;

        if (i > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:RS_IV:ERROR:OVERCURRENT I=%ld\r\n", (long)i);
            retcode = -6; goto rsiv_cleanup;
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

rsiv_cleanup:
    AT_TestEnd(&session);
    if (retcode < 0) return retcode;

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
     * Для звезды здесь было бы /2 (две обмотки последовательно).
     * Ревью AT-08: AT_SaneRs ДО коммита — отрицательный/мусорный наклон
     * регрессии раньше получал AT_VALID_RS без проверки диапазона. */
    int32_t rs_pp = AT_SaneRs((int32_t)r_pp_mohm);
    if (rs_pp == 0) {
        UART_SendTelemetry("@AT:RS_IV:ERROR:RS_OUT_OF_RANGE:%ld\r\n", (long)r_pp_mohm);
        return -9;
    }
    g_motor_params.Rs_mOhm = rs_pp;
    g_motor_params.measured_mask |= AT_VALID_RS;

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
    (void)arr;
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
    static const uint8_t rs_duties[] = { 2, 4, 6, 8, 10, 12, 15 };
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

    for (uint16_t duty_pct = 1; duty_pct <= 50; duty_pct++) {
        if (g_autotune_abort) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -4; }

        AT_SetPairDuty(pair_idx, duty_pct);
        delay_us(AT_IDLE_SETTLE_US);

        int32_t I_ss = AT_ReadCurrentChannelMedian_mA(ch);
        if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) { PWM_SetDuty1(0, 0, 0); PWM_SetDuty2(100, 100, 100); return -5; }

        /* AT_MeasureLs_uH ожидает реальное напряжение на выбранной паре.
         * Duty2=100% → Inv2 на GND, U_обмотки = Vbus * duty / 100. */
        int32_t U_applied = (int32_t)(((int64_t)vbus * duty_pct) / 100U);
        int32_t Ls_uH = AT_MeasureLs_uH(pair_idx, ccr, U_applied, duty_pct,
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

    /* Ревью AT-01: остановка FOC до safety-проверки (см. RS_IV). */
    if (FOC_IsRunning()) FOC_Stop();

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    AT_TestSession session;
    AT_TestBegin(&session);

    /* Сброс старых результатов по парам — иначе при частичном измерении
     * valid-флаги от предыдущего запуска могут попасть в статистику. */
    memset(g_motor_params.pairs, 0, sizeof(g_motor_params.pairs));

    PWM_SetDuty1(0, 0, 0);
    PWM_SetDuty2(100, 100, 100);
    both_enable();

    int32_t rs_values[3];
    int32_t ls_values[3];
    uint8_t valid_count = 0;
    int8_t retcode = 0;

    for (uint8_t p = 0; p < AUTOTUNE_NUM_PAIRS; p++) {
        if (g_autotune_abort) {
            UART_SendStr("@AT:PAIRS:ABORTED\r\n");
            retcode = -5;
            goto cleanup;
        }

        rc = AT_MeasurePair(p, &g_motor_params.pairs[p]);
        if (rc < 0) {
            g_motor_params.pairs[p].valid = 0;
            UART_SendTelemetry("@AT:PAIR:%u:REJECT:MEASURE_ERROR:%d\r\n", (unsigned)p, (int)rc);
            continue;
        }

        /* Попарная валидация значений, а не только факта успеха измерения. */
        if (AT_SaneRs(g_motor_params.pairs[p].Rs_mOhm) == 0) {
            g_motor_params.pairs[p].valid = 0;
            UART_SendTelemetry("@AT:PAIR:%u:REJECT:RS_OUT_OF_RANGE:%ld\r\n",
                               (unsigned)p, (long)g_motor_params.pairs[p].Rs_mOhm);
            continue;
        }
        if (AT_SaneLs(g_motor_params.pairs[p].Ls_uH) == 0) {
            g_motor_params.pairs[p].valid = 0;
            UART_SendTelemetry("@AT:PAIR:%u:REJECT:LS_OUT_OF_RANGE:%ld\r\n",
                               (unsigned)p, (long)g_motor_params.pairs[p].Ls_uH);
            continue;
        }

        g_motor_params.pairs[p].valid = 1;
        rs_values[valid_count] = g_motor_params.pairs[p].Rs_mOhm;
        ls_values[valid_count] = g_motor_params.pairs[p].Ls_uH;
        valid_count++;
    }

    if (valid_count == 0) {
        UART_SendStr("@AT:PAIRS:ERROR:ALL_FAILED\r\n");
        retcode = -6;
        goto cleanup;
    }
    if (valid_count < 2) {
        UART_SendTelemetry("@AT:PAIRS:ERROR:INSUFFICIENT_VALID:%u/3\r\n",
                           (unsigned)valid_count);
        retcode = -7;
        goto cleanup;
    }

    /* Медиана для 3 валидных пар — робастнее к одному выбросу.
     * Для 1-2 пар усредняем оставшиеся. */
    if (valid_count == 3) {
        g_motor_params.Rs_mOhm = median_small(rs_values, 3);
        g_motor_params.Ls_uH   = median_small(ls_values, 3);
    } else {
        int64_t sum_Rs = 0, sum_Ls = 0;
        for (uint8_t i = 0; i < valid_count; i++) {
            sum_Rs += rs_values[i];
            sum_Ls += ls_values[i];
        }
        g_motor_params.Rs_mOhm = (int32_t)(sum_Rs / valid_count);
        g_motor_params.Ls_uH   = (int32_t)(sum_Ls / valid_count);
    }
    g_motor_params.measured_mask |= AT_VALID_RS | AT_VALID_LS | AT_VALID_PAIRS;

    /* Асимметрия только по валидным парам. */
    int32_t Rs_min = 0, Rs_max = 0;
    uint8_t first = 1;
    for (uint8_t p = 0; p < AUTOTUNE_NUM_PAIRS; p++) {
        if (!g_motor_params.pairs[p].valid) continue;
        if (first) {
            Rs_min = Rs_max = g_motor_params.pairs[p].Rs_mOhm;
            first = 0;
        } else {
            if (g_motor_params.pairs[p].Rs_mOhm < Rs_min) Rs_min = g_motor_params.pairs[p].Rs_mOhm;
            if (g_motor_params.pairs[p].Rs_mOhm > Rs_max) Rs_max = g_motor_params.pairs[p].Rs_mOhm;
        }
    }
    int32_t asym_pct = (g_motor_params.Rs_mOhm > 0)
        ? (int32_t)(((int64_t)(Rs_max - Rs_min) * 100) / g_motor_params.Rs_mOhm) : 0;

    UART_SendTelemetry("@AT:PAIRS:OK:Rs=%ld:Ls=%ld:ASYM=%ld%%:VALID=%u\r\n",
                       (long)g_motor_params.Rs_mOhm, (long)g_motor_params.Ls_uH,
                       (long)asym_pct, (unsigned)valid_count);
    if (asym_pct > AT_ASYMMETRY_WARN_PCT) UART_SendTelemetry("@AT:WARN:ASYMMETRY_HIGH:%ld%%\r\n", (long)asym_pct);
    Autotune_PrintPairs();

cleanup:
    AT_TestEnd(&session);
    return retcode;
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

    AT_TestSession session;
    AT_TestBegin(&session);

    /* retcode — единая точка выхода через cleanup; valid_reps —
     * сколько повторов дали валидные Rs и Ls. */
    int8_t  retcode   = 0;
    uint8_t valid_reps = 0;

    g_motor_params.curve_count = 0;  /* сброс кривой один раз перед всеми повторами */
    g_motor_params.Rs_stat.count = 0;
    g_motor_params.Ls_stat.count = 0;

    for (uint8_t rep = 0; rep < AT_IDLE_REPEATS; rep++) {
        if (g_autotune_abort) {
            UART_SendStr("@IDLE:ABORTED\r\n");
            retcode = -5;
            goto cleanup;
        }

        int32_t L_buf[10];
        uint8_t  L_cnt = 0;

        PWM_SetDuty1(0, 0, 0);
        PWM_SetDuty2(100, 100, 100);
        both_enable();

        /* Rs берётся из pairs/предыдущего теста, не измеряется заново.
         * Rs_stat — это статистика повторов с одним и тем же Rs,
         * используемая только для валидации Ls (Rs compensation). */
        int32_t Rs_this = g_motor_params.Rs_mOhm;
        if (Rs_this <= 0) {
            PWM_SetDuty1(AT_IDLE_RS_DUTY, 0, 0);
            PWM_SetDuty2(100, 100, 100);
            delay_us(AT_IDLE_RS_SETTLE_US);
            int32_t I_rs = AT_ReadCurrentMedian_mA();
            if (I_rs < 0) I_rs = -I_rs;
            int32_t U_rs = (int32_t)(((int64_t)ADC_GetVbus_mV() * AT_IDLE_RS_DUTY) / 100U);
            if (I_rs > 10) {
                Rs_this = (int32_t)(((int64_t)U_rs * 1000) / I_rs);
                if (AT_SaneRs(Rs_this) == 0) {
                    UART_SendTelemetry("@IDLE:WARN:RS_QUICK_REJECT:%ld\r\n", (long)Rs_this);
                    Rs_this = 0;
                }
            }
        }

        for (uint16_t duty_pct = 1; duty_pct <= AT_IDLE_DUTY_MAX; duty_pct++) {
            if (g_autotune_abort) {
                UART_SendStr("@IDLE:ABORTED\r\n");
                retcode = -5;
                goto cleanup;
            }

            PWM_SetDuty1(duty_pct, 0, 0);
            PWM_SetDuty2(100, 100, 100);
            delay_us(AT_IDLE_SETTLE_US);

            int32_t I_ss = AT_ReadCurrentMedian_mA();
            if (I_ss < 0) I_ss = -I_ss;
            int32_t vbus_now  = ADC_GetVbus_mV();
            int32_t U_applied = (int32_t)(((int64_t)vbus_now * duty_pct) / 100U);

            if (I_ss > AUTOTUNE_MAX_CURRENT_MA) {
                UART_SendTelemetry("@IDLE:ERROR:OVERCURRENT I=%ld\r\n", (long)I_ss);
                retcode = -6;
                goto cleanup;
            }
            if (duty_pct >= 20 && Rs_this > 0) {
                int32_t i_expected = (int32_t)(((int64_t)U_applied * 1000LL) / Rs_this);
                if (I_ss < i_expected / AT_IDLE_OPEN_PHASE_PCT) {
                    UART_SendTelemetry("@IDLE:ERROR:OPEN_PHASE I=%ld:EXP=%ld\r\n",
                                       (long)I_ss, (long)i_expected);
                    retcode = -7;
                    goto cleanup;
                }
            }
            if (duty_pct == 1 && I_ss > AT_IDLE_SHORT_I_MA) {
                UART_SendStr("@IDLE:ERROR:SHORT_OR_LOW_RS\r\n");
                retcode = -8;
                goto cleanup;
            }

            /* AT_MeasureLs_uH ожидает РЕАЛЬНОЕ напряжение на обмотке.
             * В конфигурации Idle Duty2=100% (Inv2 на GND) это Vbus*duty/100.
             * Передача vbus_idle (полное Vbus) завышала Ls в 1/duty раз. */
            int32_t Ls_uH = AT_MeasureLs_uH(0, &TIM1->CCR1, U_applied, duty_pct,
                                              Rs_this,
                                              g_motor_params.current_channel);

            /* Валидация Ls до накопления и кривой. */
            if (AT_SaneLs(Ls_uH) > 0 && L_cnt < 10) {
                L_buf[L_cnt++] = Ls_uH;
            }

            if (rep == 0 && g_motor_params.curve_count < AUTOTUNE_MAX_CURVE_POINTS &&
                I_ss > AT_IDLE_CURVE_I_MIN_MA && AT_SaneLs(Ls_uH) > 0) {
                g_motor_params.curve[g_motor_params.curve_count].current_ma    = I_ss;
                g_motor_params.curve[g_motor_params.curve_count].inductance_uH = Ls_uH;
                g_motor_params.curve_count++;
            }

            if ((duty_pct % 5) == 0) {
                UART_SendTelemetry("@IDLE:PROG=%u/%u:D=%u:I=%ld:L=%ld:REP=%u/%u\r\n",
                                   (unsigned)duty_pct, (unsigned)AT_IDLE_DUTY_MAX,
                                   (unsigned)duty_pct,
                                   (long)I_ss, (long)Ls_uH,
                                   (unsigned)(rep + 1), (unsigned)AT_IDLE_REPEATS);
            }
        }

        both_disable();

        int32_t L_rep = (L_cnt > 0) ? median_small(L_buf, L_cnt) : 0;
        if (L_cnt > 0 && AT_SaneLs(L_rep) > 0 && AT_SaneRs(Rs_this) > 0) {
            g_motor_params.Rs_stat.values[valid_reps] = Rs_this;
            g_motor_params.Ls_stat.values[valid_reps] = L_rep;
            valid_reps++;
        } else {
            UART_SendTelemetry("@IDLE:WARN:REP_REJECT:%u:RS=%ld:LS=%ld\r\n",
                               (unsigned)rep, (long)Rs_this, (long)L_rep);
        }
    }

    g_motor_params.Rs_stat.count = valid_reps;
    g_motor_params.Ls_stat.count = valid_reps;

    stat_compute(&g_motor_params.Rs_stat);

    if (g_motor_params.Rs_stat.count > 0) {
        if (AT_SaneRs(g_motor_params.Rs_stat.median) > 0) {
            g_motor_params.Rs_mOhm = g_motor_params.Rs_stat.median;
            g_motor_params.measured_mask |= AT_VALID_RS;
            if (g_motor_params.Rs_stat.spread_pct > AT_SPREAD_WARN_PCT) {
                UART_SendTelemetry("@IDLE:WARN:RS_HIGH_SPREAD:%ld%%\r\n",
                                   (long)g_motor_params.Rs_stat.spread_pct);
            }
        } else {
            UART_SendTelemetry("@IDLE:WARN:RS_REJECT:%ld\r\n",
                               (long)g_motor_params.Rs_stat.median);
        }
    } else {
        UART_SendStr("@IDLE:ERROR:RS_NO_VALID_REPS\r\n");
        retcode = -9;
        goto cleanup;
    }

    /* Валидация: мусорная медиана Ls (из почти-нулевых ΔI) не должна
     * перезаписывать сохранённое значение. */
    if (AT_SaneLs(g_motor_params.Ls_stat.median) > 0) {
        if (g_motor_params.Ls_stat.spread_pct > AT_SPREAD_WARN_PCT) {
            UART_SendTelemetry("@IDLE:WARN:LS_HIGH_SPREAD:%ld%%\r\n",
                               (long)g_motor_params.Ls_stat.spread_pct);
        }
        g_motor_params.Ls_uH = g_motor_params.Ls_stat.median;
        g_motor_params.measured_mask |= AT_VALID_LS;
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
     * Isat интерполируется между точками выше и ниже threshold = 0.7*L0. */
    g_motor_params.Isat_ma = 0;
    if (g_motor_params.curve_count >= AT_IDLE_MIN_CURVE_POINTS) {
        uint8_t n_L0 = (g_motor_params.curve_count < AT_IDLE_L0_WINDOW)
                       ? g_motor_params.curve_count : AT_IDLE_L0_WINDOW;
        int32_t L0_buf[10];
        for (uint8_t i = 0; i < n_L0; i++)
            L0_buf[i] = g_motor_params.curve[i].inductance_uH;
        int32_t L0 = median_small(L0_buf, n_L0);
        g_motor_params.Isat_ma = AT_CalcIsat(g_motor_params.curve,
                                             g_motor_params.curve_count,
                                             L0, AT_IDLE_ISAT_THR_PCT, "IDLE");
        if (g_motor_params.Isat_ma > 0)
            g_motor_params.measured_mask |= AT_VALID_ISAT;
    } else {
        UART_SendTelemetry("@IDLE:WARN:TOO_FEW_CURVE_POINTS:%u\r\n",
                           (unsigned)g_motor_params.curve_count);
    }

    UART_SendStr("@IDLE:DONE\r\n");
    Autotune_PrintStats();
    Autotune_PrintParams();

cleanup:
    AT_TestEnd(&session);
    return retcode;
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
    if (g_motor_params.Tr_rotor_us < 0) {
        UART_SendStr("@IROT:ERROR:TR_OVERFLOW\r\n");
        return -3;
    }
    g_motor_params.measured_mask |= AT_VALID_TR;
    UART_SendTelemetry("@IROT:OK:Lr=%ld:Rr=%ld:Tr=%ld\r\n",
                       (long)Lr_uH, (long)g_motor_params.Rr_mOhm,
                       (long)g_motor_params.Tr_rotor_us);
    Autotune_PrintParams();
    return 0;
}

int8_t Autotune_Inertia(void) {
    UART_SendStr("@INERTIA:START\r\n");
    if (!FOC_IsRunning()) { UART_SendStr("@INERTIA:ERROR:FOC_NOT_RUNNING\r\n"); return -1; }
    UART_SendStr("@INERTIA:ERROR:NOT_IMPLEMENTED\r\n");
    return -2;
}


/* ══════════════════════════════════════════════════════════════════════════
 *  Вспомогательные: управление обоими инверторами + синус
 * ══════════════════════════════════════════════════════════════════════════ */

static void both_enable(void) {
    /* Интеграция sample-context: прямого включения мостов больше НЕТ.
     * Energised autotune-тесты заблокированы, пока измеренная карта
     * OEW sector/window не существует (CurrentMap_Select* = fail-closed).
     * Обход PWM_Enable() (CCER/BDTR/CEN/EN напрямую) удалён — это был
     * второй, расходящийся порядок gate-открытия. */
    PWM_InvalidateSampleContext();
    g_autotune_abort = 1;
    UART_SendStr("@AT:ERROR:POWER_BLOCKED:no measured OEW sector/window map\r\n");
}

static void both_disable(void) {
    /* Единый production-путь стопа: EN LOW первым, затем CEN/MOE/CCER,
     * затем снятие injected (ADC_InjectedStop внутри PWM_Disable). */
    PWM_Disable();
}

static int32_t at_sin_q15(int32_t angle_x1000) {
    /* millirad (0..2π) → CORDIC q31 (0x7FFFFFFF = π).
     * CORDIC принимает угол в диапазоне [-π, +π] → [-0x80000000, 0x7FFFFFFF].
     * Нормализация к [-π, +π] предотвращает переполнение signed int32.
     * Возвращает Q15 [-32768, 32767] — CORDIC_SinCos делает q31→q15. */
    angle_x1000 %= (int32_t)TWO_PI_X1000;
    if (angle_x1000 < 0) angle_x1000 += (int32_t)TWO_PI_X1000;
    if (angle_x1000 > AT_PI_MRAD) angle_x1000 -= (int32_t)TWO_PI_X1000;
    int32_t q31 = (int32_t)(((int64_t)angle_x1000 * 2147483647LL) / AT_PI_MRAD);
    int32_t s, c;
    CORDIC_SinCos(q31, &s, &c);
    return s;
}


/* ══════════════════════════════════════════════════════════════════════════
 *  1. OEW: измерение Ls через оба инвертора в противофазе
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureLs_OEW(void) {
    UART_SendStr("@AT:OEW:START\r\n"); g_autotune_abort = 0;
    /* Ревью AT-01: остановка FOC до safety-проверки (см. RS_IV). */
    if (FOC_IsRunning()) FOC_Stop();
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    AT_TestSession session;
    AT_TestBegin(&session);

    int8_t retcode = 0;
    uint16_t arr = PWM_GetARR();
    uint32_t period = (uint32_t)arr + 1U;
    uint16_t half = (uint16_t)(period / 2U);

    /* CCMR1 OC1PE сохраняем до цикла — восстановление в oew_cleanup
     * гарантирует возврат preload при любом выходе (abort/error). */
    uint32_t saved_ccmr1_1 = TIM1->CCMR1;
    uint32_t saved_ccmr1_8 = TIM8->CCMR1;

    int32_t vbus_init = ADC_GetVbus_mV();
    if (vbus_init < 12000) {
        UART_SendTelemetry("@AT:OEW:ERROR:VBUS_LOW:%ld\r\n", (long)vbus_init);
        retcode = -7; goto oew_cleanup;
    }

    PWM_SetDuty1(0,0,0); PWM_SetDuty2(100,100,100); both_enable();

    /* OEW-кривая дописывается в конец общей curve[], не затирая Idle. */
    int32_t L0_oew_uH = 0;

    for (uint16_t d = 5; d <= 50; d++) {
        if (g_autotune_abort) { UART_SendStr("@AT:OEW:ABORTED\r\n"); retcode = -5; goto oew_cleanup; }
        PWM_SetDuty1(d,0,0); PWM_SetDuty2(d,0,0);
        delay_us(AT_IDLE_SETTLE_US);
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:OEW:ERROR:OVERCURRENT I=%ld\r\n",(long)I_ss);
            retcode = -6; goto oew_cleanup;
        }

        TIM1->CCMR1 &= ~TIM_CCMR1_OC1PE; TIM8->CCMR1 &= ~TIM_CCMR1_OC1PE;

        /* CCR clamp: half + ccr_duty не должно превышать ARR.
         * При d=50: ccr=period/2, half=period/2 → sum=period=ARR+1 — overflow.
         * Используем (period-1) как максимум. */
        uint16_t ccr  = (uint16_t)(((uint32_t)d * (period - 1U)) / 100U);
        uint16_t ccr_hi = (uint16_t)(half + ccr);
        if (ccr_hi > arr) ccr_hi = arr;

        const uint32_t pwm_period_us = 1000000U / 5000U;
        const uint8_t  n_periods     = 3;
        const uint32_t dt_total_us   = n_periods * pwm_period_us;
        const int32_t  di_min_ma     = 30;
        const int32_t  di_max_ma     = 1000;
        const int32_t  reset_i_th    = 20;
        const uint16_t raw_min       = 50;
        const uint16_t raw_max       = 4045;
        const int32_t  u_L_min_mv    = 200;  /* мин. U_L для надёжного Ls */

        int32_t L_samples[AUTOTUNE_MAX_REPEATS];
        uint8_t n_valid = 0;
        int32_t i0 = 0, i1 = 0;  /* для I_mid после attempt loop */

        /* Сброс дифференциального тока: оба инвертора в нейтраль (half). */
        TIM1->CCR1 = half; TIM8->CCR1 = half;
        TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
        if (pwm_wait_periods(1) != 0) { retcode = -8; goto oew_cleanup; }
        uint8_t reset_ok = 0;
        for (uint8_t w = 0; w < 200; w++) {
            ADC_StartConversion();
            if (at_abs32(AT_ReadCurrent_mA()) < reset_i_th) { reset_ok = 1; break; }
            delay_us(500);
        }
        if (!reset_ok) {
            UART_SendTelemetry("@AT:OEW:WARN:RESET_FAIL:D=%u\r\n", (unsigned)d);
            TIM1->CCMR1 = saved_ccmr1_1; TIM8->CCMR1 = saved_ccmr1_8;
            continue;
        }

        for (uint8_t attempt = 0; attempt < 3; attempt++) {
            if (g_autotune_abort) { UART_SendStr("@AT:OEW:ABORTED\r\n"); retcode = -5; goto oew_cleanup; }

            /* Измерение в одинаковой фазе счётчика. */
            TIM1->CCR1 = half; TIM8->CCR1 = half;
            TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
            if (pwm_wait_periods(n_periods) != 0) { retcode = -8; goto oew_cleanup; }
            ADC_StartConversion();
            i0 = AT_ReadCurrent_mA();
            uint16_t raw0 = AT_GetRawChannel(g_motor_params.current_channel);

            /* Дифференциальный импульс напряжения длительностью n_periods.
             * mode 2 (TIM8 активен при CNT>CCR): ОДИНАКОВЫЙ CCR на обоих —
             * тогда V_U = (50+ccr)%·Vbus − (50−ccr)%·Vbus = 2·ccr%·Vbus. */
            TIM1->CCR1 = ccr_hi; TIM8->CCR1 = ccr_hi;
            TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
            if (pwm_wait_periods(n_periods) != 0) { retcode = -8; goto oew_cleanup; }
            ADC_StartConversion();
            i1 = AT_ReadCurrent_mA();
            uint16_t raw1 = AT_GetRawChannel(g_motor_params.current_channel);

            int32_t di = i1 - i0;
            int32_t di_abs = di < 0 ? -di : di;

            int32_t Ls_oew = 0;
            int32_t u_L = 0;
            int32_t u_R = 0;
            uint8_t skip = 0;
            const char *skip_reason = "";

            if (raw0 < raw_min || raw0 > raw_max || raw1 < raw_min || raw1 > raw_max) {
                skip = 1; skip_reason = "RAW_RANGE";
            } else if (di_abs < di_min_ma) {
                skip = 1; skip_reason = "DI_LOW";
            } else if (di_abs > di_max_ma) {
                skip = 1; skip_reason = "DI_HIGH";
            } else {
                /* Vbus измеряем на каждом шаге — просадка питания
                 * при больших токах не искажает Ls. */
                ADC_StartConversion();
                int32_t vbus_step = ADC_GetVbus_mV();
                int32_t U_eff = (int32_t)(((int64_t)vbus_step * d * 2) / 100U);
                int32_t i_avg = (i0 + i1) / 2;
                u_L = U_eff;
                if (AT_SaneRs(g_motor_params.Rs_mOhm) > 0) {
                    u_R = (int32_t)(((int64_t)i_avg * g_motor_params.Rs_mOhm) / 1000LL);
                    u_L = U_eff - u_R;
                }
                if (u_L <= 0) {
                    skip = 1; skip_reason = "RESISTIVE_DROP";
                } else if (u_L < u_L_min_mv) {
                    skip = 1; skip_reason = "UL_TOO_SMALL";
                } else {
                    Ls_oew = (int32_t)(((int64_t)u_L * dt_total_us) / di_abs);
                    if (AT_SaneLs(Ls_oew) == 0) {
                        skip = 1; skip_reason = "LS_OUT_OF_RANGE";
                        Ls_oew = 0;
                    } else if (n_valid < AUTOTUNE_MAX_REPEATS) {
                        L_samples[n_valid++] = Ls_oew;
                    }
                }
            }

            UART_SendTelemetry("@AT:OEW:LS_STEP:D=%u:dt=%u:UL=%ld:UR=%ld:RAW0=%u:RAW1=%u:I0=%ld:I1=%ld:dI=%ld:L=%ld:SKIP=%u:RSN=%s:ATT=%u\r\n",
                               (unsigned)d, (unsigned)dt_total_us,
                               (long)u_L, (long)u_R,
                               (unsigned)raw0, (unsigned)raw1,
                               (long)i0, (long)i1, (long)di, (long)Ls_oew,
                               (unsigned)skip, skip_reason, (unsigned)(attempt + 1));
        }

        int32_t Ls_oew = 0;
        if (n_valid > 0) {
            Ls_oew = median_small(L_samples, n_valid);
        }
        if (g_motor_params.curve_count < AUTOTUNE_MAX_CURVE_POINTS &&
            I_ss > AT_IDLE_CURVE_I_MIN_MA && Ls_oew > 0) {
            int32_t I_mid = (i0 + i1) / 2;
            if (I_mid < 0) I_mid = -I_mid;
            g_motor_params.curve[g_motor_params.curve_count].current_ma = I_mid;
            g_motor_params.curve[g_motor_params.curve_count].inductance_uH = Ls_oew;
            g_motor_params.curve_count++;
        }
        if ((d % 10) == 0) UART_SendTelemetry("@AT:OEW:PROG=%u/50:D=%u:I=%ld:L=%ld\r\n",(unsigned)d,(unsigned)d,(long)I_ss,(long)Ls_oew);

        TIM1->CCMR1 = saved_ccmr1_1; TIM8->CCMR1 = saved_ccmr1_8;
    }

oew_cleanup:
    TIM1->CCMR1 = saved_ccmr1_1; TIM8->CCMR1 = saved_ccmr1_8;
    AT_TestEnd(&session);

    /* Ревью AT-04: публикация ТОЛЬКО при полном успехе (retcode==0).
     * При abort/fault/timeout кривая, Ls, Isat и mask остаются от
     * предыдущего успешного теста — OEW-тест теперь транзакция. */
    if (retcode != 0) {
        UART_SendTelemetry("@AT:OEW:ERROR:RC=%d\r\n", (int)retcode);
        return retcode;
    }

    curve_sort_by_current(g_motor_params.curve, g_motor_params.curve_count);
    g_motor_params.curve_count = curve_filter_outliers(g_motor_params.curve, g_motor_params.curve_count);

    /* Берём медиану первых 5–10 точек с наименьшим током — это наиболее
     * близко к ненасыщенной индуктивности (L0). */
    uint8_t n_L0 = (g_motor_params.curve_count < 10) ? g_motor_params.curve_count : 10;
    int32_t L0_buf[10];
    for (uint8_t i = 0; i < n_L0; i++) L0_buf[i] = g_motor_params.curve[i].inductance_uH;
    L0_oew_uH = (n_L0 > 0) ? median_small(L0_buf, n_L0) : 0;

    if (AT_SaneLs(L0_oew_uH) > 0) {
        g_motor_params.Ls_uH = L0_oew_uH;
        g_motor_params.measured_mask |= AT_VALID_LS;
    } else {
        UART_SendTelemetry("@AT:OEW:WARN:LS_REJECT:%ld\r\n", (long)L0_oew_uH);
    }

    /* Пересчёт Isat из новой кривой L(I) с интерполяцией. */
    g_motor_params.Isat_ma = 0;
    if (g_motor_params.curve_count >= AT_IDLE_MIN_CURVE_POINTS) {
        uint8_t n_L0b = (g_motor_params.curve_count < AT_IDLE_L0_WINDOW)
                       ? g_motor_params.curve_count : AT_IDLE_L0_WINDOW;
        int32_t L0b_buf[10];
        for (uint8_t i = 0; i < n_L0b; i++) L0b_buf[i] = g_motor_params.curve[i].inductance_uH;
        int32_t L0 = median_small(L0b_buf, n_L0b);
        g_motor_params.Isat_ma = AT_CalcIsat(g_motor_params.curve,
                                             g_motor_params.curve_count,
                                             L0, AT_IDLE_ISAT_THR_PCT, "OEW");
        if (g_motor_params.Isat_ma > 0)
            g_motor_params.measured_mask |= AT_VALID_ISAT;
    }

    UART_SendTelemetry("@AT:OEW:OK:Ls=%ld:Isat=%ld\r\n",(long)L0_oew_uH,(long)g_motor_params.Isat_ma);
    Autotune_PrintCurve();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  2. Rr: синусоида 5 Гц на заблокированном валу
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureRr(void) {
    UART_SendStr("@AT:RR:START:LOCK_ROTOR\r\n");
    g_autotune_abort = 0;

    if (FOC_IsRunning()) FOC_Stop();

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;

    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }
    if (g_motor_params.Rs_mOhm <= 0) {
        UART_SendStr("@AT:RR:ERROR:RS_NOT_MEASURED\r\n");
        return -5;
    }
    if (g_motor_params.Ls_uH <= 0) {
        UART_SendStr("@AT:RR:ERROR:LS_NOT_MEASURED\r\n");
        return -5;
    }

    AT_TestSession session;
    AT_TestBegin(&session);

    int8_t retcode = 0;

    /* Начальный режим 50/50 — нулевое напряжение на обмотках.
     * Важно: both_enable() ДО at_injected_sync(), т.к. sync ждёт UIF,
     * а UIF генерируется только при CEN=1. */
    PWM_SetDuty1(AT_RR_DUTY_BASE, AT_RR_DUTY_BASE, AT_RR_DUTY_BASE);
    PWM_SetDuty2(AT_RR_DUTY_BASE, AT_RR_DUTY_BASE, AT_RR_DUTY_BASE);
    both_enable();

    if (at_injected_sync(1) != 0) { retcode = -6; goto rr_done; }
    int32_t vbus = ADC_GetVbus_mV();

    /* Постоянная времени τ = L/R (мкс). Ls_uH·1000 / Rs_mOhm = us. */
    int32_t tau_us = (g_motor_params.Ls_uH * 1000) / g_motor_params.Rs_mOhm;
    if (tau_us < 1000) tau_us = 1000;
    int32_t settle_us = tau_us * AT_RR_TAU_MARGIN;
    if (settle_us > AT_RR_SETTLE_MAX_US) settle_us = AT_RR_SETTLE_MAX_US;

    delay_us(settle_us);

    /* Измерение DC-offset датчика при нулевом напряжении. */
    int64_t offset_sum = 0;
    for (uint8_t k = 0; k < AT_RR_OFFSET_SAMPLES; k++) {
        if (g_autotune_abort) { retcode = -6; goto rr_done; }
        if (at_injected_sync(1) != 0) { retcode = -6; goto rr_done; }
        offset_sum += AT_ReadCurrent_mA();
    }
    int32_t i_offset = (int32_t)(offset_sum / AT_RR_OFFSET_SAMPLES);

    /* Калибровочный период на малой амплитуде: оцениваем пиковый ток
     * и подбираем рабочую амплитуду под целевой ток. */
    int32_t rr_amp = AT_RR_AMP_CAL_PCT;
    int32_t i_min = INT32_MAX;
    int32_t i_max = INT32_MIN;
    int32_t theta = 0;
    for (int32_t i = 0; i < AT_RR_SAMP_PER_PERIOD; i++) {
        if (g_autotune_abort) { retcode = -6; goto rr_done; }
        theta += AT_RR_THETA_STEP;
        if (theta >= (int32_t)TWO_PI_X1000) theta -= (int32_t)TWO_PI_X1000;

        int32_t sa = at_sin_q15(theta);
        int32_t sb = at_sin_q15(theta - AT_TWO_PI_3_MRAD);
        int32_t sc = at_sin_q15(theta + AT_TWO_PI_3_MRAD);

        int32_t da = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sa * rr_amp) / 32768);
        int32_t db = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sb * rr_amp) / 32768);
        int32_t dc = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sc * rr_amp) / 32768);
        if (da < 0) { da = 0; }
        if (da > AT_RR_DUTY_MAX) { da = AT_RR_DUTY_MAX; }
        if (db < 0) { db = 0; }
        if (db > AT_RR_DUTY_MAX) { db = AT_RR_DUTY_MAX; }
        if (dc < 0) { dc = 0; }
        if (dc > AT_RR_DUTY_MAX) { dc = AT_RR_DUTY_MAX; }

        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        if (at_injected_sync(AT_RR_PWM_PERIODS) != 0) { retcode = -6; goto rr_done; }
        int32_t i_raw = AT_ReadCurrent_mA();
        if (at_abs32(i_raw) > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:RR:ERROR:OVERCURRENT I=%ld\r\n", (long)i_raw);
            retcode = -7;
            goto rr_done;
        }
        int32_t i_cal = i_raw - i_offset;
        if (i_cal < i_min) i_min = i_cal;
        if (i_cal > i_max) i_max = i_cal;
    }

    int32_t i_peak_est = (i_max - i_min) / 2;
    if (i_peak_est < 10) i_peak_est = 10;

    /* Целевой пиковый ток: 0.2·Isat, но не выше AT_RR_TARGET_I_MA. */
    int32_t i_target = AT_RR_TARGET_I_MA;
    if (g_motor_params.Isat_ma > 0) {
        int32_t i_from_isat = g_motor_params.Isat_ma / 5;
        if (i_from_isat < i_target) i_target = i_from_isat;
    }
    if (i_target < 100) i_target = 100;

    rr_amp = (int32_t)(((int64_t)i_target * AT_RR_AMP_CAL_PCT) / i_peak_est);
    if (rr_amp < AT_RR_AMP_MIN_PCT) rr_amp = AT_RR_AMP_MIN_PCT;
    if (rr_amp > AT_RR_AMP_MAX_PCT) rr_amp = AT_RR_AMP_MAX_PCT;

    UART_SendTelemetry("@AT:RR:AUTOAMP:IPEAK=%ld:ITARGET=%ld:AMP=%ld\r\n",
                       (long)i_peak_est, (long)i_target, (long)rr_amp);

    /* Установление при выбранной амплитуде. */
    delay_us(settle_us);

    /* Основной lock-in на 15 периодов. */
    int64_t sum_i_sin = 0, sum_i_cos = 0, i_sq_sum = 0;
    int64_t vbus_sum = 0;   /* AT-09: накопление Vbus для среднего */
    uint32_t vbus_n = 0;
    theta = 0;
    for (int32_t i = 0; i < AT_RR_NPTS; i++) {
        if (g_autotune_abort) { retcode = -6; goto rr_done; }
        theta += AT_RR_THETA_STEP;
        if (theta >= (int32_t)TWO_PI_X1000) theta -= (int32_t)TWO_PI_X1000;

        int32_t sa = at_sin_q15(theta);
        int32_t sb = at_sin_q15(theta - AT_TWO_PI_3_MRAD);
        int32_t sc = at_sin_q15(theta + AT_TWO_PI_3_MRAD);
        int32_t ca = at_sin_q15(theta + AT_PI_2_MRAD); /* cos */

        int32_t da = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sa * rr_amp) / 32768);
        int32_t db = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sb * rr_amp) / 32768);
        int32_t dc = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sc * rr_amp) / 32768);
        if (da < 0) { da = 0; }
        if (da > AT_RR_DUTY_MAX) { da = AT_RR_DUTY_MAX; }
        if (db < 0) { db = 0; }
        if (db > AT_RR_DUTY_MAX) { db = AT_RR_DUTY_MAX; }
        if (dc < 0) { dc = 0; }
        if (dc > AT_RR_DUTY_MAX) { dc = AT_RR_DUTY_MAX; }

        /* OEW mode 2 (d1=d2): V_обмотки = (2d/100−1)·Vbus — чистый AC без DC. */
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        if (at_injected_sync(AT_RR_PWM_PERIODS) != 0) { retcode = -6; goto rr_done; }
        int32_t i_raw = AT_ReadCurrent_mA();
        if (at_abs32(i_raw) > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:RR:ERROR:OVERCURRENT I=%ld\r\n", (long)i_raw);
            retcode = -7;
            goto rr_done;
        }
        int32_t i_ma = i_raw - i_offset;

        sum_i_sin += (int64_t)i_ma * sa;
        sum_i_cos += (int64_t)i_ma * ca;
        i_sq_sum  += (int64_t)i_ma * i_ma;

        /* Ревью AT-09: Vbus из injected-снимка (JDR4, обновлён в
         * at_injected_sync) усредняем по ходу lock-in — просадка DC-link
         * за 3-секундное измерение иначе попадает в v_amp и завышает Rr. */
        if ((i % 100) == 0) {
            vbus_sum += ADC_GetVbus_mV();
            vbus_n++;
        }

        if ((i % 500) == 0) {
            UART_SendTelemetry("@AT:RR:PROG=%ld/%d:I=%ld:AMP=%ld\r\n",
                               (long)i, (int)AT_RR_NPTS, (long)i_ma, (long)rr_amp);
        }
    }
    goto rr_done;

rr_done:
    AT_TestEnd(&session);
    if (retcode < 0) return retcode;

    if (i_sq_sum == 0) {
        UART_SendStr("@AT:RR:ERROR:NO_CURRENT\r\n");
        return -8;
    }

    /* Амплитуды тока в mA (Q15 sin = 32768).
     * (2/N)*Σ i*sin = I_amp*cos(φ), (2/N)*Σ i*cos = I_amp*sin(φ). */
    int64_t id_raw = (sum_i_sin * 2LL) / AT_RR_NPTS; /* I_d * 32768 */
    int64_t iq_raw = (sum_i_cos * 2LL) / AT_RR_NPTS; /* I_q * 32768 */

    /* Амплитуда напряжения (пик). d = 50 ± rr_amp%:
     * V = (2d/100 − 1)·Vbus = 2·(sa·rr_amp/32768)%·Vbus.
     * Ревью AT-09: vbus_avg — среднее за время lock-in (раньше — начальный
     * Vbus один раз; просадка DC-link систематически завышала Rr). */
    int32_t vbus_avg = (vbus_n > 0) ? (int32_t)(vbus_sum / vbus_n) : vbus;
    if (vbus_avg < (int32_t)((int64_t)vbus * 85 / 100)) {
        UART_SendTelemetry("@AT:RR:WARN:VBUS_SAG:INIT=%ld:AVG=%ld\r\n",
                           (long)vbus, (long)vbus_avg);
    }
    int64_t v_amp = (((int64_t)vbus_avg * rr_amp) * 2LL) / 100LL;

    if (id_raw == 0) {
        UART_SendStr("@AT:RR:ERROR:NO_RESISTIVE_CURRENT\r\n");
        return -9;
    }
    int64_t id_abs = id_raw < 0 ? -id_raw : id_raw;
    int32_t id_mA = (int32_t)(id_abs / 32768LL);
    int32_t iq_mA = (int32_t)(iq_raw / 32768LL);
    if (id_mA == 0) {
        UART_SendStr("@AT:RR:ERROR:NO_RESISTIVE_CURRENT\r\n");
        return -9;
    }
    int64_t i_sq_sum_pp = (int64_t)id_mA * id_mA + (int64_t)iq_mA * iq_mA;
    int32_t r_total_pp = (int32_t)((v_amp * 1000LL * id_mA) / i_sq_sum_pp);

    int32_t angle_q31 = CORDIC_Atan2(iq_raw, id_raw);          /* π = 0x7FFFFFFF */
    int32_t angle_deg = (int32_t)(((int64_t)angle_q31 * 180) / 2147483647LL);

    /* Оценка SNR: энергия фундаментала vs. остаточная энергия. */
    int64_t signal_energy = (i_sq_sum_pp * (int64_t)AT_RR_NPTS) / 2LL;
    if (signal_energy < 0) signal_energy = 0;
    int64_t noise_energy = i_sq_sum - signal_energy;
    if (noise_energy < 1) noise_energy = 1;
    int32_t snr_pct = (int32_t)((signal_energy * 100LL) / (signal_energy + noise_energy));

    UART_SendTelemetry("@AT:RR:LOCKIN:Id=%ld:Iq=%ld:Vamp=%ld:PHI=%ld:SNR=%d%%:OFFSET=%ld\r\n",
                       (long)id_mA, (long)iq_mA, (long)v_amp, (long)angle_deg,
                       (int)snr_pct, (long)i_offset);

    if (snr_pct < AT_RR_MIN_SNR_PCT) {
        UART_SendTelemetry("@AT:RR:ERROR:LOW_SNR:%d%%\r\n", (int)snr_pct);
        return -11;
    }

    /* OEW: r_total_pp = Rs + Rr'. Проверяем, что Rr положителен и в разумном
     * диапазоне 0.2·Rs .. 5·Rs. */
    int32_t rs_pp = g_motor_params.Rs_mOhm;
    if (r_total_pp <= rs_pp) {
        UART_SendTelemetry("@AT:RR:ERROR:RTOTAL_LTE_RS:Rtotal=%ld:Rs=%ld\r\n",
                           (long)r_total_pp, (long)rs_pp);
        return -12;
    }
    int32_t rr_pp = r_total_pp - rs_pp;
    int32_t rr_min = (rs_pp * AT_RR_RR_MIN_MUL_NUM) / AT_RR_RR_MIN_MUL_DEN;
    int32_t rr_max = rs_pp * AT_RR_RR_MAX_MUL;
    if (rr_pp < rr_min || rr_pp > rr_max) {
        UART_SendTelemetry("@AT:RR:ERROR:RR_OUT_OF_RANGE:Rr=%ld:MIN=%ld:MAX=%ld\r\n",
                           (long)rr_pp, (long)rr_min, (long)rr_max);
        return -13;
    }

    g_motor_params.Rr_mOhm = rr_pp;
    g_motor_params.measured_mask |= AT_VALID_RR;
    UART_SendTelemetry("@AT:RR:OK:Rr=%ld:Rtotal=%ld:SNR=%d%%:AMP=%ld\r\n",
                       (long)g_motor_params.Rr_mOhm, (long)r_total_pp,
                       (int)snr_pct, (long)rr_amp);
    Autotune_PrintParams();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  3. Lm/Lr: холостой ход с V/f разгоном до 50 Гц
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureNoLoad(void) {
    UART_SendStr("@AT:NOLOAD:START:FREE_ROTOR\r\n");
    g_autotune_abort = 0;

    /* Ревью AT-01: FOC_Stop до safety-проверки (был после проверок Rs/Rr/Ls). */
    if (FOC_IsRunning()) FOC_Stop();

    int8_t retcode = 0;
    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }
    if (g_motor_params.Ls_uH <= 0) {
        UART_SendStr("@AT:NOLOAD:ERROR:LS_NOT_MEASURED\r\n");
        return -5;
    }
    if (g_motor_params.Rs_mOhm <= 0) {
        UART_SendStr("@AT:NOLOAD:ERROR:RS_NOT_MEASURED\r\n");
        return -5;
    }
    if (g_motor_params.Rr_mOhm <= 0) {
        UART_SendStr("@AT:NOLOAD:ERROR:RR_NOT_MEASURED\r\n");
        return -5;
    }

    AT_TestSession session;
    AT_TestBegin(&session);

    PWM_SetDuty1(0,0,0);
    PWM_SetDuty2(100,100,100);
    both_enable();

    int32_t theta = 0;
    UART_SendStr("@AT:NOLOAD:RAMP:START\r\n");
    for (int32_t f_mHz = 0; f_mHz <= AT_NOLOAD_RAMP_FMAX_MHZ; f_mHz += AT_NOLOAD_RAMP_STEP_MHZ) {
        if (g_autotune_abort) { retcode = -6; goto noload_disable; }
        /* OEW диф-драйв (mode 2, d1=d2): V_U = 2·(sa·v_mag/32768)%·Vbus —
         * чистый AC без DC. v_mag растёт от 0 до AT_NOLOAD_RAMP_VMAG_MAX. */
        int32_t v_mag = (int32_t)(((int64_t)f_mHz * AT_NOLOAD_RAMP_VMAG_MAX) / AT_NOLOAD_RAMP_FMAX_MHZ);
        theta += (int32_t)(((int64_t)TWO_PI_X1000 * f_mHz) / AT_NOLOAD_RAMP_THETA_DEN);
        if (theta >= (int32_t)TWO_PI_X1000) theta -= (int32_t)TWO_PI_X1000;
        int32_t sa = at_sin_q15(theta);
        int32_t da = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sa * v_mag) / 32768);
        if (da < 0) { da = 0; }
        if (da > AT_RR_DUTY_MAX) { da = AT_RR_DUTY_MAX; }
        int32_t sb = at_sin_q15(theta - AT_TWO_PI_3_MRAD);
        int32_t sc = at_sin_q15(theta + AT_TWO_PI_3_MRAD);
        int32_t db = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sb * v_mag) / 32768);
        int32_t dc = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sc * v_mag) / 32768);
        if (db < 0) { db = 0; }
        if (db > AT_RR_DUTY_MAX) { db = AT_RR_DUTY_MAX; }
        if (dc < 0) { dc = 0; }
        if (dc > AT_RR_DUTY_MAX) { dc = AT_RR_DUTY_MAX; }
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        if (at_injected_sync(AT_NOLOAD_PWM_PERIODS) != 0) { retcode = -6; goto noload_disable; }
        int32_t im_ramp = at_abs32(AT_ReadCurrent_mA());
        if (im_ramp > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:NOLOAD:ERROR:RAMP_OVERCURRENT:F=%ld:I=%ld\r\n",
                               (long)(f_mHz/1000), (long)im_ramp);
            retcode = -8;
            goto noload_disable;
        }
        if ((f_mHz % 5000) == 0) {
            UART_SendTelemetry("@AT:NOLOAD:RAMP:F=%ld:V=%ld%%:VBUS=%ld\r\n",
                               (long)(f_mHz/1000), (long)v_mag, (long)ADC_GetVbus_mV());
        }
    }

    /* Стабилизация магнитного потока на 50 Гц. */
    UART_SendStr("@AT:NOLOAD:SETTLE\r\n");
    delay_us(AT_NOLOAD_FLUX_SETTLE_US);

    /* Vbus на момент измерения. */
    if (at_injected_sync(1) != 0) { retcode = -6; goto noload_disable; }
    int32_t vbus = ADC_GetVbus_mV();

    UART_SendStr("@AT:NOLOAD:MEASURE:START\r\n");
    int64_t i_sq_sum = 0;
    int64_t i_abs_sum = 0;
    int32_t i_max = 0;
    const int32_t n_meas = AT_NOLOAD_MEAS_N;
    for (int32_t i = 0; i < n_meas; i++) {
        if (g_autotune_abort) { retcode = -6; goto noload_disable; }
        theta += AT_NOLOAD_MEAS_THETA_STEP;
        if (theta >= (int32_t)TWO_PI_X1000) theta -= (int32_t)TWO_PI_X1000;
        int32_t sa = at_sin_q15(theta);
        int32_t da = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sa * AT_NOLOAD_MEAS_VMAG) / 32768);
        if (da < 0) { da = 0; }
        if (da > AT_RR_DUTY_MAX) { da = AT_RR_DUTY_MAX; }
        int32_t sb = at_sin_q15(theta - AT_TWO_PI_3_MRAD);
        int32_t sc = at_sin_q15(theta + AT_TWO_PI_3_MRAD);
        int32_t db = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sb * AT_NOLOAD_MEAS_VMAG) / 32768);
        int32_t dc = AT_RR_DUTY_BASE + (int32_t)(((int64_t)sc * AT_NOLOAD_MEAS_VMAG) / 32768);
        if (db < 0) { db = 0; }
        if (db > AT_RR_DUTY_MAX) { db = AT_RR_DUTY_MAX; }
        if (dc < 0) { dc = 0; }
        if (dc > AT_RR_DUTY_MAX) { dc = AT_RR_DUTY_MAX; }
        PWM_SetDuty1((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        PWM_SetDuty2((uint16_t)da,(uint16_t)db,(uint16_t)dc);
        if (at_injected_sync(AT_NOLOAD_PWM_PERIODS) != 0) { retcode = -6; goto noload_disable; }
        int32_t i_raw = AT_ReadCurrent_mA();
        int32_t im = at_abs32(i_raw);
        if (im > AUTOTUNE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@AT:NOLOAD:ERROR:OVERCURRENT I=%ld\r\n", (long)im);
            retcode = -9;
            goto noload_disable;
        }
        i_sq_sum  += (int64_t)im * im;
        i_abs_sum += im;
        if (im > i_max) i_max = im;
    }

noload_disable:
    AT_TestEnd(&session);
    if (retcode < 0) return retcode;

    if (i_sq_sum == 0) {
        UART_SendStr("@AT:NOLOAD:ERROR:NO_CURRENT\r\n");
        return -7;
    }

    /* Irms = sqrt(mean(i²)), справедливо для любой формы тока. */
    int32_t i_rms = (int32_t)isqrt_u64((uint64_t)(i_sq_sum / n_meas));

    /* Vrms: 50 Гц, v_mag = AT_NOLOAD_MEAS_VMAG.
     * V = 2·v_mag%·Vbus (амплитуда) → Vrms = V / √2. */
    int32_t v_rms = (int32_t)(((int64_t)vbus * 2 * AT_NOLOAD_MEAS_VMAG * 707) / (100 * 1000));

    /* |Z| = V/I (мОм). Вычитаем активную часть Rs:
     * X_total = sqrt(Z² − Rs²), L_total = X_total / ω. */
    int32_t z_total = (int32_t)(((int64_t)v_rms * 1000) / i_rms);
    if (z_total <= g_motor_params.Rs_mOhm) {
        UART_SendTelemetry("@AT:NOLOAD:ERROR:Z_LTE_RS:Z=%ld:Rs=%ld\r\n",
                           (long)z_total, (long)g_motor_params.Rs_mOhm);
        return -10;
    }
    uint64_t z_sq = (uint64_t)z_total * (uint64_t)z_total;
    uint64_t r_sq = (uint64_t)g_motor_params.Rs_mOhm * (uint64_t)g_motor_params.Rs_mOhm;
    int32_t x_total = (int32_t)isqrt_u64(z_sq - r_sq);
    int32_t l_total = (int32_t)(((int64_t)x_total * 1000) / AT_NOLOAD_OMEGA_50HZ);

    if (l_total <= g_motor_params.Ls_uH) {
        UART_SendTelemetry("@AT:NOLOAD:ERROR:LTOTAL_LTE_LS:Ltotal=%ld:Ls=%ld\r\n",
                           (long)l_total, (long)g_motor_params.Ls_uH);
        return -8;
    }

    int32_t lm_uH = l_total - g_motor_params.Ls_uH;

    /* Ревью AT-07: проверка Lm >= Ls ДО коммита — раньше при ошибке
     * оставались плохой Lm_uH и AT_VALID_LM в g_motor_params. */
    if (lm_uH < g_motor_params.Ls_uH) {
        UART_SendTelemetry("@AT:NOLOAD:ERROR:LM_TOO_SMALL:LM=%ld:Ls=%ld\r\n",
                           (long)lm_uH, (long)g_motor_params.Ls_uH);
        return -11;
    }
    g_motor_params.Lm_uH = lm_uH;
    g_motor_params.measured_mask |= AT_VALID_LM;

    int32_t Lr_uH = g_motor_params.Lm_uH + g_motor_params.Ls_uH / 2;
    if (g_motor_params.Rr_mOhm > 0) {
        g_motor_params.Tr_rotor_us = (int32_t)(((int64_t)Lr_uH * 1000) / g_motor_params.Rr_mOhm);
        g_motor_params.measured_mask |= AT_VALID_TR;
    }

    UART_SendTelemetry("@AT:NOLOAD:OK:Irms=%ld:Vrms=%ld:Z=%ld:X=%ld:Ltotal=%ld:Lm=%ld:Lr=%ld:Tr=%ld:IMAX=%ld:Imean=%ld\r\n",
                       (long)i_rms, (long)v_rms, (long)z_total, (long)x_total,
                       (long)l_total, (long)g_motor_params.Lm_uH, (long)Lr_uH,
                       (long)g_motor_params.Tr_rotor_us, (long)i_max,
                       (long)(i_abs_sum / n_meas));
    Autotune_PrintParams();
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  4. Осциллограмма: захват N точек с синхронизацией по PWM
 * ══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    uint32_t time_us;
    int32_t  i1_mA;
    int32_t  i2_mA;
    int32_t  i_res_mA;
    int32_t  vbus_mV;
    uint16_t duty;
} AtScopeSample;

int8_t Autotune_Scope(void) {
    UART_SendStr("@SCOPE:START\r\n");
    g_autotune_abort = 0;

    /* Ревью AT-01: FOC_Stop до safety-проверки (был после DetectChannel). */
    if (FOC_IsRunning()) FOC_Stop();

    int8_t rc = AT_SafetyCheck();
    if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) {
        if (Autotune_DetectChannel() < 0) return -4;
    }

    AT_TestSession session;
    AT_TestBegin(&session);

    PWM_SetDuty1(0,0,0);
    PWM_SetDuty2(100,100,100);
    both_enable();
    PWM_SetDuty1(AT_SCOPE_DUTY, 0, 0);

    uint32_t cycles_per_us = SystemCoreClock / 1000000U;
    if (cycles_per_us == 0) cycles_per_us = 1;

    static AtScopeSample s_buf[AT_SCOPE_N];
    uint32_t n_captured = 0;
    int8_t retcode = 0;

    for (uint32_t i = 0; i < AT_SCOPE_N; i++) {
        if (g_autotune_abort) { break; }

        /* Ждём один период ШИМ — выборки в одинаковой фазе. */
        if (pwm_wait_periods(1) != 0) { retcode = -6; goto scope_cleanup; }

        ADC_StartConversion();
        ADC_WaitForEOC();

        int32_t i1  = ADC_GetI1_mA();
        int32_t i2  = ADC_GetI2_mA();
        int32_t in  = ADC_GetIres_mA();
        int32_t vbus = ADC_GetVbus_mV();

        if (at_abs32(i1) > AT_SCOPE_MAX_CURRENT_MA ||
            at_abs32(i2) > AT_SCOPE_MAX_CURRENT_MA ||
            at_abs32(in) > AT_SCOPE_MAX_CURRENT_MA) {
            UART_SendTelemetry("@SCOPE:ERROR:OVERCURRENT I1=%ld:I2=%ld:IN=%ld\r\n",
                               (long)i1, (long)i2, (long)in);
            retcode = -5; goto scope_cleanup;
        }

        s_buf[i].time_us = DWT->CYCCNT / cycles_per_us;
        s_buf[i].i1_mA   = i1;
        s_buf[i].i2_mA   = i2;
        s_buf[i].i_res_mA = in;
        s_buf[i].vbus_mV = vbus;
        s_buf[i].duty    = AT_SCOPE_DUTY;
        n_captured++;
    }

scope_cleanup:
    AT_TestEnd(&session);

    if (retcode < 0) return retcode;
    if (g_autotune_abort) {
        UART_SendStr("@SCOPE:ABORTED\r\n");
        return -6;
    }

    /* Выгрузка CSV. Заголовок + строки. */
    UART_SendStr("@SCOPE:CSV:sample,time_us,i1_mA,i2_mA,ires_mA,vbus_mV,duty\r\n");
    for (uint32_t i = 0; i < n_captured; i++) {
        UART_SendTelemetry("@SCOPE,%u,%lu,%ld,%ld,%ld,%ld,%u\r\n",
                           (unsigned)i,
                           (unsigned long)s_buf[i].time_us,
                           (long)s_buf[i].i1_mA,
                           (long)s_buf[i].i2_mA,
                           (long)s_buf[i].i_res_mA,
                           (long)s_buf[i].vbus_mV,
                           (unsigned)s_buf[i].duty);
    }
    UART_SendTelemetry("@SCOPE:DONE:N=%u:CYC/us=%lu\r\n",
                       (unsigned)n_captured, (unsigned long)cycles_per_us);
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  5. Расчёт ПИ-регулятора из Ls и Rs
 * ══════════════════════════════════════════════════════════════════════════ */
void Autotune_CalcPI(int32_t bw_hz) {
    if (AT_SaneLs(g_motor_params.Ls_uH) == 0 || AT_SaneRs(g_motor_params.Rs_mOhm) == 0) {
        pi_calculated = 0;
        UART_SendStr("@AT:PI:ERROR:INVALID_RS_LS\r\n");
        return;
    }

    if (bw_hz < AT_PI_BW_MIN_HZ || bw_hz > AT_PI_BW_MAX_HZ) {
        pi_calculated = 0;
        UART_SendTelemetry("@AT:PI:ERROR:BW_RANGE:REQ=%ld:MIN=%d:MAX=%d\r\n",
                           (long)bw_hz, AT_PI_BW_MIN_HZ, AT_PI_BW_MAX_HZ);
        return;
    }

    /* Модульный оптимум (Антиучебник §3.4) — та же формула, что в
     * FOC_ComputePIGains() / FOC_SetMotorParams(). Параметр bw_hz
     * определяет коэффициент a = 1/(2·bw·Ts) модульного оптимума:
     *   bw=500 → a=5, bw=1250 → a=2 (default FOC_SetMotorParams).
     * Это гарантирует консистентность: pi=N → piapply и mp= → mpapply
     * дают одинаковые Kp/Ki при совпадении bw и a=2. */
    int32_t vbus_mv = (int32_t)ADC_GetVbus_mV();
    int32_t kp = 0, ki = 0;
    FOC_ComputePIGainsBW(g_motor_params.Rs_mOhm, g_motor_params.Ls_uH,
                         vbus_mv, bw_hz, &kp, &ki);

    if (kp <= 0 || ki <= 0) {
        pi_calculated = 0;
        UART_SendTelemetry("@AT:PI:ERROR:ZERO_GAIN:Kp=%ld:Ki=%ld\r\n",
                           (long)kp, (long)ki);
        return;
    }

    last_kp       = kp;
    last_ki       = ki;
    last_bw_hz    = bw_hz;
    last_ls_uH    = g_motor_params.Ls_uH;
    last_rs_mOhm  = g_motor_params.Rs_mOhm;
    pi_calculated = 1;

    int64_t tau_us = ((int64_t)g_motor_params.Ls_uH * 1000LL) /
                     g_motor_params.Rs_mOhm;

    UART_SendTelemetry("@AT:PI:BW=%ld:FS=%d:TS_US=%d:Kp=%ld:Ki=%ld:Ls=%ld:Rs=%ld:Tau_us=%ld\r\n",
                       (long)bw_hz, AT_PI_FOC_FS_HZ, AT_PI_FOC_TS_US,
                       (long)last_kp, (long)last_ki,
                       (long)g_motor_params.Ls_uH, (long)g_motor_params.Rs_mOhm,
                       (long)tau_us);
}

int Autotune_GetLastPI(int32_t *kp, int32_t *ki, int32_t *bw_hz) {
    if (!pi_calculated) return -1;
    if (g_motor_params.Ls_uH != last_ls_uH ||
        g_motor_params.Rs_mOhm != last_rs_mOhm) {
        pi_calculated = 0;
        return -2;
    }
    if (kp)    *kp    = last_kp;
    if (ki)    *ki    = last_ki;
    if (bw_hz) *bw_hz = last_bw_hz;
    return 0;
}

/* ══════════════════════════════════════════════════════════════════════════
 *  6. Ls от положения ротора: 5 замеров с ручным поворотом вала
 * ══════════════════════════════════════════════════════════════════════════ */
int8_t Autotune_MeasureLs_Position(void) {
    UART_SendStr("@AT:LSPOS:START:TURN_ROTOR\r\n"); g_autotune_abort = 0;
    if (FOC_IsRunning()) FOC_Stop();
    int8_t rc = AT_SafetyCheck(); if (rc < 0) return rc;
    if (g_motor_params.current_channel == AT_CH_UNKNOWN) { if (Autotune_DetectChannel() < 0) return -4; }
    if (AT_SaneRs(g_motor_params.Rs_mOhm) == 0) {
        UART_SendStr("@AT:LSPOS:ERROR:RS_NOT_MEASURED\r\n"); return -6;
    }
    AT_TestSession session;
    AT_TestBegin(&session);
    int8_t retcode = 0;
    int32_t ls_vals[AUTOTUNE_MAX_REPEATS]; uint8_t cnt = 0;
    for (uint8_t pos = 0; pos < AUTOTUNE_MAX_REPEATS; pos++) {
        if (g_autotune_abort) { UART_SendStr("@AT:LSPOS:ABORTED\r\n"); retcode = -5; goto lspos_cleanup; }
        UART_SendTelemetry("@AT:LSPOS:WAIT:POS=%u/5:TURN_ROTOR\r\n",(unsigned)(pos+1));
        for (uint32_t t = 0; t < 3000; t++) { if (g_autotune_abort) { UART_SendStr("@AT:LSPOS:ABORTED\r\n"); retcode = -5; goto lspos_cleanup; } delay_us(1000); }
        both_enable();
        PWM_SetDuty1(10,0,0); delay_us(AT_IDLE_SETTLE_US);
        int32_t I_ss = AT_ReadCurrentMedian_mA(); if (I_ss < 0) I_ss = -I_ss;
        if (I_ss > AUTOTUNE_MAX_CURRENT_MA || I_ss < 10) {
            UART_SendTelemetry("@AT:LSPOS:ERROR:BAD_CURRENT I=%ld\r\n",(long)I_ss);
            retcode = -7; goto lspos_cleanup;
        }
        int32_t vbus_idle = ADC_GetVbus_mV();
        int32_t U_applied = (int32_t)(((int64_t)vbus_idle * 10) / 100U);
        int32_t Ls_uH = AT_MeasureLs_uH(0, &TIM1->CCR1, U_applied, 10,
                                         g_motor_params.Rs_mOhm,
                                         g_motor_params.current_channel);
        both_disable();
        if (AT_SaneLs(Ls_uH) == 0) {
            UART_SendTelemetry("@AT:LSPOS:WARN:INVALID_LS:POS=%u:Ls=%ld\r\n",
                               (unsigned)(pos+1),(long)Ls_uH);
            continue;
        }
        if (cnt < AUTOTUNE_MAX_REPEATS) ls_vals[cnt++] = Ls_uH;
        UART_SendTelemetry("@AT:LSPOS:MEAS:POS=%u/5:Ls=%ld:I=%ld\r\n",(unsigned)(pos+1),(long)Ls_uH,(long)I_ss);
    }

lspos_cleanup:
    AT_TestEnd(&session);
    if (retcode != 0) return retcode;
    if (cnt < 3) {
        UART_SendTelemetry("@AT:LSPOS:ERROR:INSUFFICIENT_VALID:%u\r\n",
                           (unsigned)cnt);
        return -8;
    }
    AtStat32 stat = {0}; stat.count = cnt;
    for (uint8_t i = 0; i < cnt; i++) stat.values[i] = ls_vals[i];
    stat_compute(&stat);
    UART_SendTelemetry("@AT:LSPOS:OK:N=%u:MEDIAN=%ld:MIN=%ld:MAX=%ld:SPREAD=%ld%%\r\n",
                       (unsigned)cnt,(long)stat.median,(long)stat.min,(long)stat.max,(long)stat.spread_pct);
    if (stat.spread_pct > 20) {
        UART_SendTelemetry("@AT:LSPOS:WARN:HIGH_SPREAD:%ld%%\r\n",
                           (long)stat.spread_pct);
    }
    if (AT_SaneLs(stat.median) > 0) {
        g_motor_params.Ls_uH = stat.median;
        g_motor_params.measured_mask |= AT_VALID_LS;
        UART_SendTelemetry("@AT:LSPOS:SAVE:Ls=%ld\r\n", (long)g_motor_params.Ls_uH);
    } else {
        UART_SendTelemetry("@AT:LSPOS:WARN:LS_REJECT:%ld\r\n", (long)stat.median);
    }
    return 0;
}


void Autotune_Init(void) {
    memset(&g_motor_params, 0, sizeof(MotorParams));
    g_autotune_abort = 0;
    pi_calculated = 0;
    last_kp = 0;
    last_ki = 0;
    last_bw_hz = 0;
    last_ls_uH = 0;
    last_rs_mOhm = 0;
}

void Autotune_PrintParams(void) {
    UART_SendTelemetry(
        "@AT:PARAMS:Rs_mOhm=%ld:Ls_uH=%ld:Isat_mA=%ld:Rr_mOhm=%ld:Lm_uH=%ld:Tr_us=%ld:Ke_mV_rpm=%ld:p=%d:J_x1e6=%ld:CH=%d:VMASK=0x%lX\r\n",
        (long)g_motor_params.Rs_mOhm, (long)g_motor_params.Ls_uH, (long)g_motor_params.Isat_ma,
        (long)g_motor_params.Rr_mOhm, (long)g_motor_params.Lm_uH, (long)g_motor_params.Tr_rotor_us,
        (long)g_motor_params.Ke_mV_per_rpm, (int)g_motor_params.pole_pairs,
        (long)g_motor_params.J_kg_m2_x1e6, (int)g_motor_params.current_channel,
        (unsigned long)g_motor_params.measured_mask);
}

void Autotune_PrintCurve(void) {
    uint8_t count = g_motor_params.curve_count;
    if (count > AUTOTUNE_MAX_CURVE_POINTS) count = AUTOTUNE_MAX_CURVE_POINTS;
    UART_SendTelemetry("@CURVE:BEGIN:N=%u\r\n", (unsigned)count);
    for (uint8_t i = 0; i < count; i++) {
        UART_SendTelemetry("@CURVE:POINT:%u:I_mA=%ld:L_uH=%ld\r\n",
                           (unsigned)i,
                           (long)g_motor_params.curve[i].current_ma,
                           (long)g_motor_params.curve[i].inductance_uH);
    }
    UART_SendStr("@CURVE:END\r\n");
}

void Autotune_PrintPairs(void) {
    const char *name[] = {"A", "B", "C"};
    for (uint8_t p = 0; p < AUTOTUNE_NUM_PAIRS; p++) {
        UART_SendTelemetry("@AT:PAIR:%s:Rs_mOhm=%ld:Ls_uH=%ld:Isat_mA=%ld:VALID=%u\r\n",
                           name[p],
                           (long)g_motor_params.pairs[p].Rs_mOhm,
                           (long)g_motor_params.pairs[p].Ls_uH,
                           (long)g_motor_params.pairs[p].Isat_ma,
                           (unsigned)g_motor_params.pairs[p].valid);
    }
}

void Autotune_PrintStats(void) {
    UART_SendTelemetry(
        "@AT:STAT:Rs_COUNT=%u:Rs_MED_mOhm=%ld:Rs_MIN_mOhm=%ld:Rs_MAX_mOhm=%ld:Rs_SPREAD_PCT=%ld:"
        "Ls_COUNT=%u:Ls_MED_uH=%ld:Ls_MIN_uH=%ld:Ls_MAX_uH=%ld:Ls_SPREAD_PCT=%ld:Isat_mA=%ld\r\n",
        (unsigned)g_motor_params.Rs_stat.count,
        (long)g_motor_params.Rs_stat.median, (long)g_motor_params.Rs_stat.min,
        (long)g_motor_params.Rs_stat.max, (long)g_motor_params.Rs_stat.spread_pct,
        (unsigned)g_motor_params.Ls_stat.count,
        (long)g_motor_params.Ls_stat.median, (long)g_motor_params.Ls_stat.min,
        (long)g_motor_params.Ls_stat.max, (long)g_motor_params.Ls_stat.spread_pct,
        (long)g_motor_params.Isat_ma);
}
