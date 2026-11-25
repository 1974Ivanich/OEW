/* ============================================================================
 *  BM1109 — измерительная прошивка R и L (ветка feature/bm1109-rl-measurement)
 *
 *  Что делает:
 *    - измеряет сопротивление «линия-линия» двух фаз методом двух-трёх
 *      рабочих точек с компенсацией постоянного смещения (U = R*I + U0);
 *    - измеряет индуктивность «линия-линия» по начальному участку
 *      ступенчатого отклика тока;
 *    - пересчитывает результат в фазные величины звезды (R_ph = R_LL/2);
 *    - управляется отдельным набором команд по UART2.
 *
 *  Чего здесь нет (сознательно):
 *    - режимов OEW, нулевой последовательности, профилирования ЭДС;
 *    - управления асинхронным двигателем;
 *    - зависимости от main.c: файл собирается сам по себе (bm1109/Makefile).
 *
 *  Ограничения метода (важно, а не «мелочи»):
 *    - R измеряется при ШИМ с программным дизерингом скважности: при
 *      Vbus = 24 В один шаг CCR = 1 % даёт 0,24 В, что на R = 0,075 Ом
 *      сравнимо с полезным сигналом. Средняя скважность при дизеринге
 *      задаётся с разрешением 1/65536 периода, поэтому наклон U(I)
 *      осмыслен, но абсолютная точность ограничена знанием шунта
 *      (BM1109_RSHUNT_MOHM) и усиления токовой цепи;
 *    - L измеряется только при пониженном напряжении шины
 *      (BM1109_L_MAX_VBUS_MV): при 24 В и L = 50 мкГн предел тока
 *      достигается за единицы микросекунд, достоверной выборки не будет.
 * ========================================================================== */

#include "stm32g474xx.h"
#include <stdio.h>

#include "bm1109_config.h"
#include "bm1109_measure_core.h"

/* ---------------------------------------------------------------------------
 *  UART2 (PA2/PA3, AF7, 115200 при HSI 16 МГц) — как в main.c
 * ------------------------------------------------------------------------- */
static void uart_init(void)
{
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;

    GPIOA->MODER &= ~((3U << 4) | (3U << 6));
    GPIOA->MODER |= (2U << 4) | (2U << 6);
    GPIOA->AFR[0] &= ~((0xFU << 8) | (0xFU << 12));
    GPIOA->AFR[0] |= (7U << 8) | (7U << 12);

    USART2->BRR = BM1109_SYSCLK_HZ / BM1109_UART_BAUD;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void uart_send(const char *s)
{
    while (*s) {
        while (!(USART2->ISR & USART_ISR_TXE_TXFNF)) { }
        USART2->TDR = (uint8_t)(*s++);
    }
}

static char uart_recv(void)
{
    while (!(USART2->ISR & USART_ISR_RXNE_RXFNE)) { }
    return (char)USART2->RDR;
}

/* ---------------------------------------------------------------------------
 *  Время: DWT->CYCCNT даёт точный счётчик тактов 16 МГц (62,5 нс).
 *  Он же — независимый арбитр по максимальному времени включения.
 * ------------------------------------------------------------------------- */
static void dwt_init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint32_t now_cycles(void)
{
    return DWT->CYCCNT;
}

static uint32_t us_to_cycles(uint32_t us)
{
    return us * (BM1109_SYSCLK_HZ / 1000000U);
}

static uint32_t cycles_to_us(uint32_t cyc)
{
    return cyc / (BM1109_SYSCLK_HZ / 1000000U);
}

static void delay_us(uint32_t us)
{
    uint32_t t0 = now_cycles();
    uint32_t target = us_to_cycles(us);
    while ((now_cycles() - t0) < target) { }
}

/* ---------------------------------------------------------------------------
 *  GPIO: выводы моста + EN_1/EN_2, как в main.c
 * ------------------------------------------------------------------------- */
static void gpio_init(void)
{
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;

    /* TIM1: PA8, PA9, PA10 (AF1) */
    GPIOA->MODER &= ~((3U << 16) | (3U << 18) | (3U << 20));
    GPIOA->MODER |= (2U << 16) | (2U << 18) | (2U << 20);
    GPIOA->AFR[1] &= ~((0xFU << 0) | (0xFU << 4) | (0xFU << 8));
    GPIOA->AFR[1] |= (1U << 0) | (1U << 4) | (1U << 8);

    /* TIM1N: PB13, PB14, PB15 (AF1) */
    GPIOB->MODER &= ~((3U << 26) | (3U << 28) | (3U << 30));
    GPIOB->MODER |= (2U << 26) | (2U << 28) | (2U << 30);
    GPIOB->AFR[1] &= ~((0xFU << 20) | (0xFU << 24) | (0xFU << 28));
    GPIOB->AFR[1] |= (1U << 20) | (1U << 24) | (1U << 28);

    /* TIM8: PC6, PC7, PC8 (AF4) */
    GPIOC->MODER &= ~((3U << 12) | (3U << 14) | (3U << 16));
    GPIOC->MODER |= (2U << 12) | (2U << 14) | (2U << 16);
    GPIOC->AFR[0] &= ~((0xFU << 24) | (0xFU << 28));
    GPIOC->AFR[0] |= (4U << 24) | (4U << 28);
    GPIOC->AFR[1] &= ~(0xFU << 0);
    GPIOC->AFR[1] |= (4U << 0);

    /* TIM8N: PA5 (AF3), PB0 (AF3), PB1 (AF3) */
    GPIOA->MODER &= ~(3U << 10);
    GPIOA->MODER |= (2U << 10);
    GPIOA->AFR[0] &= ~(0xFU << 20);
    GPIOA->AFR[0] |= (3U << 20);

    GPIOB->MODER &= ~((3U << 0) | (3U << 2));
    GPIOB->MODER |= (2U << 0) | (2U << 2);
    GPIOB->AFR[0] &= ~((0xFU << 0) | (0xFU << 4));
    GPIOB->AFR[0] |= (3U << 0) | (3U << 4);

    /* EN_1 (PB4), EN_2 (PB5) — выходы, по умолчанию выключены */
    GPIOB->MODER &= ~((3U << 8) | (3U << 10));
    GPIOB->MODER |= (1U << 8) | (1U << 10);
    GPIOB->BSRR = (1U << 20) | (1U << 21);

    /* Аналоговые входы: PA0 (ток), PC0 (Vbus) */
    GPIOA->MODER |= (3U << 0);
    GPIOC->MODER |= (3U << 0);
}

/* ---------------------------------------------------------------------------
 *  ADC1: синхронное тактирование от HCLK.
 *  Канал тока — минимальное время выборки (нужно для импульса L),
 *  канал Vbus — максимальное (высокоомный делитель).
 * ------------------------------------------------------------------------- */
static void adc_init(void)
{
    uint32_t timeout;

    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN;
    delay_us(2U);

    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST;
    RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST;
    delay_us(1U);

    ADC12_COMMON->CCR = (1U << 16);   /* CKMODE = 01: такт АЦП = HCLK */

    ADC1->CR = 0;
    ADC1->CR &= ~ADC_CR_DEEPPWD;
    ADC1->CR |= ADC_CR_ADVREGEN;
    delay_us(20U);

    ADC1->CR |= ADC_CR_ADCAL;
    timeout = 2000000U;
    while (ADC1->CR & ADC_CR_ADCAL) {
        if (--timeout == 0U) {
            uart_send("ОШИБКА: таймаут калибровки ADC\r\n");
            return;
        }
    }

    ADC1->CFGR = 0;
    ADC1->SMPR1 = 0;
    ADC1->SMPR1 |= (1U << ADC_SMPR1_SMP1_Pos);   /* ток: 6,5 такта */
    ADC1->SMPR1 |= (7U << ADC_SMPR1_SMP6_Pos);   /* Vbus: 640,5 такта */

    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    timeout = 2000000U;
    while (!(ADC1->ISR & ADC_ISR_ADRDY)) {
        if (--timeout == 0U) {
            uart_send("ОШИБКА: таймаут включения ADC\r\n");
            return;
        }
    }
}

static int32_t adc_raw(uint32_t channel)
{
    uint32_t timeout = 200000U;

    if (!(ADC1->CR & ADC_CR_ADEN)) {
        return -1;
    }

    ADC1->SQR1 = (channel << ADC_SQR1_SQ1_Pos);
    ADC1->ISR = ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR;
    ADC1->CR |= ADC_CR_ADSTART;

    while (!(ADC1->ISR & ADC_ISR_EOC)) {
        if (--timeout == 0U) {
            return -1;
        }
    }

    return (int32_t)(ADC1->DR & 0x0FFFU);
}

/* Чтение с отметкой момента запуска преобразования. Задержка «запуск →
 * выборка» одинакова для всех чтений, поэтому в разности моментов она
 * сокращается — именно это нужно для di/dt. */
static int32_t adc_raw_timed(uint32_t channel, uint32_t *t_cycles)
{
    uint32_t timeout = 20000U;

    if (!(ADC1->CR & ADC_CR_ADEN)) {
        return -1;
    }

    ADC1->SQR1 = (channel << ADC_SQR1_SQ1_Pos);
    ADC1->ISR = ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR;
    ADC1->CR |= ADC_CR_ADSTART;
    *t_cycles = now_cycles();

    while (!(ADC1->ISR & ADC_ISR_EOC)) {
        if (--timeout == 0U) {
            return -1;
        }
    }

    return (int32_t)(ADC1->DR & 0x0FFFU);
}

static int32_t adc_avg(uint32_t channel, uint8_t samples)
{
    int32_t acc = 0;
    uint8_t k;

    for (k = 0U; k < samples; k++) {
        int32_t v = adc_raw(channel);
        if (v < 0) {
            return -1;
        }
        acc += v;
    }
    return acc / (int32_t)samples;
}

/* Код АЦП (разность с нулём) -> миллиамперы через шунт и усилитель. */
static int32_t code_to_ma(int32_t code_delta)
{
    int32_t mv = (code_delta * (int32_t)BM1109_ADC_VREF_MV) / (int32_t)BM1109_ADC_MAX_CODE;
    return (mv * 1000) / ((int32_t)BM1109_RSHUNT_MOHM * (int32_t)BM1109_CS_GAIN);
}

static int32_t read_current_ma(int32_t offset_code)
{
    int32_t code = adc_avg(BM1109_ADC_CH_CURRENT, 4U);
    if (code < 0) {
        return -0x7FFFFFFF;
    }
    return code_to_ma(code - offset_code);
}

/* Возвращает мВ или -1 при ошибке АЦП. */
static int32_t read_vbus_mv(void)
{
    int32_t code = adc_avg(BM1109_ADC_CH_VBUS, 16U);
    if (code < 0) {
        return -1;
    }
    return ((code * (int32_t)BM1109_ADC_VREF_MV) / (int32_t)BM1109_ADC_MAX_CODE)
           * (int32_t)BM1109_VBUS_DIV_NUM / (int32_t)BM1109_VBUS_DIV_DEN;
}

/* ---------------------------------------------------------------------------
 *  Силовой мост
 * ------------------------------------------------------------------------- */
static TIM_TypeDef *const pwm = BM1109_PWM_TIM;

#define CCER_CE(ch)  (1U << (4U * ((ch) - 1U)))
#define CCER_CNE(ch) (1U << (4U * ((ch) - 1U) + 2U))

static void pwm_set_ccr(uint8_t ch, uint32_t value)
{
    switch (ch) {
        case 1U: pwm->CCR1 = value; break;
        case 2U: pwm->CCR2 = value; break;
        case 3U: pwm->CCR3 = value; break;
        default: break;
    }
}

static void pwm_init(void)
{
    RCC->APB2ENR |= BM1109_PWM_TIM_RCC;

    pwm->PSC = BM1109_PWM_PSC;
    pwm->ARR = BM1109_PWM_ARR;
    pwm->CR1 = 0;                       /* edge-aligned, счёт вверх */
    pwm->CCMR1 = (6U << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE
               | (6U << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;
    pwm->CCMR2 = (6U << TIM_CCMR2_OC3M_Pos) | TIM_CCMR2_OC3PE;
    pwm->CCR1 = 0;
    pwm->CCR2 = 0;
    pwm->CCR3 = 0;
    pwm->CCER = 0;
    pwm->BDTR = BM1109_PWM_DEADTIME_DTG & 0x7FU;   /* MOE включается только на тест */
    pwm->EGR = TIM_EGR_UG;
    pwm->SR = 0;
}

/* Полное выключение: оба моста, оба EN, все ключи. */
static void power_off_all(void)
{
    pwm->CR1 &= ~TIM_CR1_CEN;
    pwm->BDTR &= ~TIM_BDTR_MOE;
    pwm->CCER = 0;
    pwm->CCR1 = 0;
    pwm->CCR2 = 0;
    pwm->CCR3 = 0;

    /* Второй мост OEW-платы держим выключенным всегда: для звезды он не нужен. */
#if BM1109_PWM_TIM_INSTANCE == 1
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN;
    TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM8->CCER = 0;
#else
    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN;
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER = 0;
#endif

    GPIOB->BSRR = (1U << 20) | (1U << 21);    /* EN_1, EN_2 -> OFF */
}

static void power_enable(void)
{
    GPIOB->BSRR = (1U << 4) | (1U << 5);      /* EN_1, EN_2 -> ON */
}

/* Включить пару: верхний ключ плеча hi (ШИМ), нижний ключ плеча lo (постоянно).
 * Третье плечо полностью закрыто, нейтраль мотора не соединена ни с чем. */
static void pair_on(uint8_t hi, uint8_t lo)
{
    pwm->CR1 &= ~TIM_CR1_CEN;
    pwm->BDTR &= ~TIM_BDTR_MOE;
    pwm->CCER = 0;
    pwm->CCR1 = 0;
    pwm->CCR2 = 0;
    pwm->CCR3 = 0;
    pwm->CNT = 0;

    pwm_set_ccr(lo, 0U);                     /* OCxREF = 0 -> нижний ключ открыт */
    pwm_set_ccr(hi, 0U);                     /* OCxREF = 0 -> верхний закрыт */

    pwm->CCER = CCER_CE(hi) | CCER_CNE(lo);
    pwm->EGR = TIM_EGR_UG;
    pwm->SR = 0;

    power_enable();
    pwm->BDTR |= TIM_BDTR_MOE;
    pwm->CR1 |= TIM_CR1_CEN;
}

static bm1109_status pwm_wait_period(void)
{
    uint32_t t0 = now_cycles();
    uint32_t limit = us_to_cycles(1000U);    /* 1 мс — заведомо больше периода */

    while (!(pwm->SR & TIM_SR_UIF)) {
        if ((now_cycles() - t0) > limit) {
            return BM1109_ERR_TIMEOUT;
        }
    }
    pwm->SR = ~TIM_SR_UIF;
    return BM1109_OK;
}

/* Дизеринг скважности: задаём среднюю скважность с разрешением 1/65536
 * периода, а CCR обновляем раз в период. Именно это даёт разрешение
 * напряжения, недостижимое при CCR = 0..99 напрямую. */
static uint32_t dither_frac;

static uint32_t pwm_dither_step(uint8_t ch, uint32_t duty_q16)
{
    uint32_t step = duty_q16 * (BM1109_PWM_ARR + 1U);
    uint32_t ccr;

    dither_frac += step;
    ccr = dither_frac >> 16;
    dither_frac &= 0xFFFFU;

    if (ccr > BM1109_PWM_ARR + 1U) {
        ccr = BM1109_PWM_ARR + 1U;
    }
    pwm_set_ccr(ch, ccr);
    return ccr;
}

/* ---------------------------------------------------------------------------
 *  Печать результатов (целые единицы: мОм, мкГн, мА, мВ — без float-printf)
 * ------------------------------------------------------------------------- */
static void print_r(const char *tag, const bm1109_r_result *r,
                    int32_t i_lo_ma, int32_t i_hi_ma)
{
    char buf[192];
    sprintf(buf, "  %s: R_LL=%ld мОм  U0=%ld мВ  СКО=%ld мВ  I=%ld..%ld мА\r\n",
            tag,
            (long)(r->r_ohm * 1000.0f),
            (long)(r->u_offset_v * 1000.0f),
            (long)(r->rms_v * 1000.0f),
            (long)i_lo_ma, (long)i_hi_ma);
    uart_send(buf);
}

/* ---------------------------------------------------------------------------
 *  Измерение R: одна рабочая точка по току.
 *
 *  Скважность подбирается итеративно, ток контролируется не реже, чем раз
 *  в 4 периода ШИМ. Итоговое напряжение — среднее по фактически выданным
 *  CCR, а не по последней команде.
 * ------------------------------------------------------------------------- */
static uint32_t duty_hint_q16 = BM1109_R_INIT_DUTY_Q16;

static bm1109_status measure_r_point(uint8_t hi, uint8_t lo, uint32_t target_ma,
                                     int32_t offset_code, float vbus_v,
                                     float *u_out, float *i_out)
{
    uint32_t duty_q16 = duty_hint_q16;
    uint32_t applied = 0U, periods = 0U;
    uint32_t t0;
    uint8_t stable = 0U;
    int32_t i_ma = 0;
    bm1109_status st = BM1109_OK;

    if (duty_q16 < 1U) {
        duty_q16 = 1U;
    }

    power_off_all();
    delay_us(BM1109_DISCHARGE_MS * 1000U);

    pair_on(hi, lo);
    dither_frac = 0U;
    t0 = now_cycles();

    for (uint32_t it = 0U; it < BM1109_R_MAX_ITER; it++) {
        st = pwm_wait_period();
        if (st != BM1109_OK) {
            break;
        }
        applied += pwm_dither_step(hi, duty_q16);
        periods++;

        if ((it & 3U) == 0U) {
            i_ma = read_current_ma(offset_code);
            if (i_ma == -0x7FFFFFFF) {
                st = BM1109_ERR_ADC;
                break;
            }
            if (i_ma > (int32_t)BM1109_I_PEAK_MA) {
                st = BM1109_ERR_OVER_CURRENT;
                break;
            }
            if (i_ma < 0) {
                i_ma = 0;
            }

            if ((uint32_t)i_ma < target_ma * (100U - BM1109_R_TOL_PCT) / 100U) {
                duty_q16 = duty_q16 + duty_q16 / 4U + 1U;
                stable = 0U;
            } else if ((uint32_t)i_ma > target_ma * (100U + BM1109_R_TOL_PCT) / 100U) {
                duty_q16 = duty_q16 * 4U / 5U;
                stable = 0U;
            } else if (stable < 200U) {
                stable++;
            }

            if (duty_q16 < 1U) {
                duty_q16 = 1U;
            }
            if (duty_q16 > BM1109_DUTY_MAX_Q16) {
                duty_q16 = BM1109_DUTY_MAX_Q16;
            }
        }

        if (stable >= 3U && it > BM1109_R_SETTLE_PERIODS) {
            break;
        }
        if (cycles_to_us(now_cycles() - t0) > (uint32_t)BM1109_R_MAX_ON_MS * 1000U) {
            st = BM1109_ERR_TIMEOUT;
            break;
        }
    }

    /* Итоговое усреднение: выборки сдвинуты по фазе внутри периода, чтобы
     * пульсации тока от ШИМ не давали систематического сдвига. */
    if (st == BM1109_OK) {
        int32_t acc = 0;
        uint32_t n = 0U;

        for (uint32_t k = 0U; k < 16U; k++) {
            st = pwm_wait_period();
            if (st != BM1109_OK) {
                break;
            }
            applied += pwm_dither_step(hi, duty_q16);
            periods++;
            delay_us((k & 3U) * 2U);

            for (uint8_t s = 0U; s < 4U; s++) {
                int32_t code = adc_raw(BM1109_ADC_CH_CURRENT);
                if (code < 0) {
                    st = BM1109_ERR_ADC;
                    break;
                }
                acc += code;
                n++;
            }
            if (st != BM1109_OK) {
                break;
            }
        }

        if (st == BM1109_OK && n > 0U) {
            int32_t i_final = code_to_ma((acc / (int32_t)n) - offset_code);
            if (i_final > (int32_t)BM1109_I_PEAK_MA) {
                st = BM1109_ERR_OVER_CURRENT;
            } else if (i_final <= 0) {
                st = BM1109_ERR_NO_CURRENT;
            } else {
                *i_out = (float)i_final / 1000.0f;
                *u_out = vbus_v * (float)applied
                         / ((float)periods * (float)(BM1109_PWM_ARR + 1U));
                duty_hint_q16 = duty_q16;
            }
        }
    }

    power_off_all();
    delay_us(BM1109_DISCHARGE_MS * 1000U);
    return st;
}

/* ---------------------------------------------------------------------------
 *  Измерение L: импульс постоянного напряжения, 3 выборки тока.
 *
 *  База времён умножается на 2^attempt, пока прирост тока не станет
 *  измеримым. Если и на базовых временах ток вышел за предел — импульс
 *  прекращается и результат не выдаётся: снижайте Vbus, а не порог.
 * ------------------------------------------------------------------------- */
static bm1109_status measure_l_pair(uint8_t hi, uint8_t lo, float r_ll,
                                    uint32_t vbus_mv, int32_t offset_code,
                                    float *l_out)
{
    static const uint16_t t_base_us[3] = BM1109_L_T_US;
    bm1109_status st = BM1109_ERR_TIMEOUT;
    uint8_t attempt;

    if (vbus_mv > BM1109_L_MAX_VBUS_MV) {
        return BM1109_ERR_VBUS_L_HIGH;
    }
    if (!(r_ll > 0.0f)) {
        return BM1109_ERR_ARG;
    }

    for (attempt = 0U; attempt <= BM1109_L_T_SCALE_MAX; attempt++) {
        bm1109_time_point pts[3];
        bm1109_l_result res;
        uint32_t cyc[3];
        int32_t code[3];
        float u_step = (float)vbus_mv / 1000.0f
                       - (float)BM1109_L_SWITCH_DROP_MV / 1000.0f;
        float i_ss = u_step / r_ll;
        uint32_t t0;
        uint8_t k;

        power_off_all();
        delay_us(BM1109_DISCHARGE_MS * 1000U);

        pair_on(hi, lo);
        pwm_set_ccr(hi, BM1109_PWM_ARR + 1U);    /* верхний ключ открыт постоянно */
        t0 = now_cycles();
        st = BM1109_OK;

        for (k = 0U; k < 3U; k++) {
            uint32_t target_us = (uint32_t)t_base_us[k] << attempt;

            if (target_us > BM1109_L_MAX_PULSE_US) {
                st = BM1109_ERR_TIMEOUT;
                break;
            }
            while ((now_cycles() - t0) < us_to_cycles(target_us)) {
                if (cycles_to_us(now_cycles() - t0) > BM1109_ABS_MAX_ON_US) {
                    st = BM1109_ERR_TIMEOUT;
                    break;
                }
            }
            if (st != BM1109_OK) {
                break;
            }

            code[k] = adc_raw_timed(BM1109_ADC_CH_CURRENT, &cyc[k]);
            if (code[k] < 0) {
                st = BM1109_ERR_ADC;
                break;
            }
            if (code_to_ma(code[k] - offset_code) > (int32_t)BM1109_I_PEAK_MA) {
                st = BM1109_ERR_OVER_CURRENT;
                break;
            }
        }

        power_off_all();
        delay_us(BM1109_DISCHARGE_MS * 1000U);

        if (st != BM1109_OK) {
            break;      /* короче уже нельзя: либо предел тока, либо отказ АЦП */
        }

        for (k = 0U; k < 3U; k++) {
            pts[k].t_s = (float)(cyc[k] - t0) / (float)BM1109_SYSCLK_HZ;
            pts[k].i_a = (float)code_to_ma(code[k] - offset_code) / 1000.0f;
        }

        st = bm1109_fit_inductance(pts, 3U, u_step, r_ll, i_ss,
                                   (float)BM1109_L_MIN_DELTA_MA / 1000.0f, &res);
        if (st == BM1109_OK) {
            *l_out = res.l_h;
            return BM1109_OK;
        }
        if (st != BM1109_ERR_LOW_DELTA_I) {
            break;
        }
        /* прирост мал — следующая попытка с удвоенными временами */
    }

    return st;
}
/* ---------------------------------------------------------------------------
 *  Полный цикл по паре фаз
 * ------------------------------------------------------------------------- */
static const char *const pair_name[3] = {"UV", "VW", "WU"};
static const uint8_t pair_hi[3] = {1U, 2U, 3U};
static const uint8_t pair_lo[3] = {2U, 3U, 1U};

static float   g_r_ll[3];
static float   g_l_ll[3];
static uint8_t g_r_ok[3];
static uint8_t g_l_ok[3];

static void results_reset(void)
{
    uint8_t k;
    for (k = 0U; k < 3U; k++) {
        g_r_ll[k] = 0.0f;
        g_l_ll[k] = 0.0f;
        g_r_ok[k] = 0U;
        g_l_ok[k] = 0U;
    }
}

static void measure_pair(uint8_t idx, int do_r, int do_l)
{
    char buf[192];
    int32_t offset_code;
    int32_t vbus_mv;
    bm1109_status st;

    g_r_ok[idx] = 0U;
    g_l_ok[idx] = 0U;

    power_off_all();
    delay_us(BM1109_DISCHARGE_MS * 1000U);

    offset_code = adc_avg(BM1109_ADC_CH_CURRENT, BM1109_ADC_AVG_SAMPLES);
    vbus_mv = read_vbus_mv();

    sprintf(buf, "\r\n== Пара %s ==\r\n", pair_name[idx]);
    uart_send(buf);

    if (offset_code < 0 || vbus_mv < 0) {
        uart_send("  ОШИБКА: АЦП не отвечает, измерение отменено\r\n");
        return;
    }

    sprintf(buf, "  Vbus=%ld мВ, нуль тока=%ld кодов\r\n", (long)vbus_mv, (long)offset_code);
    uart_send(buf);

    if (vbus_mv < (int32_t)BM1109_VBUS_MIN_MV || vbus_mv > (int32_t)BM1109_VBUS_MAX_MV) {
        uart_send("  ОТКАЗ: Vbus вне разрешённого окна, ключи не включались\r\n");
        return;
    }

    if (do_r) {
        static const uint16_t target_permille[BM1109_R_POINT_COUNT] = BM1109_R_TARGET_PERMILLE;
        bm1109_point pts[BM1109_R_POINT_COUNT];
        bm1109_r_result res;
        uint8_t n = 0U;
        uint8_t failures = 0U;
        int32_t i_lo = 0;
        int32_t i_hi = 0;

        uart_send("  R: точки U(I)\r\n");
        for (uint8_t k = 0U; k < BM1109_R_POINT_COUNT; k++) {
            uint32_t target = (uint32_t)BM1109_I_TARGET_MA * target_permille[k] / 1000U;
            float u_v = 0.0f;
            float i_a = 0.0f;

            st = measure_r_point(pair_hi[idx], pair_lo[idx], target,
                                 offset_code, (float)vbus_mv / 1000.0f, &u_v, &i_a);
            if (st != BM1109_OK) {
                sprintf(buf, "    точка %u (%ld мА): %s\r\n",
                        (unsigned)(k + 1U), (long)target, bm1109_status_str(st));
                uart_send(buf);
                failures++;
                continue;
            }

            sprintf(buf, "    точка %u: I=%ld мА, Uобм=%ld мВ\r\n",
                    (unsigned)(k + 1U),
                    (long)(i_a * 1000.0f + 0.5f),
                    (long)(u_v * 1000.0f + 0.5f));
            uart_send(buf);

            pts[n].u_v = u_v;
            pts[n].i_a = i_a;
            if (n == 0U) {
                i_lo = (int32_t)(i_a * 1000.0f + 0.5f);
                i_hi = i_lo;
            } else {
                int32_t im = (int32_t)(i_a * 1000.0f + 0.5f);
                if (im < i_lo) i_lo = im;
                if (im > i_hi) i_hi = im;
            }
            n++;
        }

        if (failures > 0U) {
            sprintf(buf, "  R_LL(%s): недействительно — получено %u из %u точек\r\n",
                    pair_name[idx], (unsigned)n, (unsigned)BM1109_R_POINT_COUNT);
            uart_send(buf);
        } else {
            st = bm1109_fit_resistance(pts, n, &res);
            if (st != BM1109_OK) {
                sprintf(buf, "  R_LL(%s): отказ МНК — %s\r\n",
                        pair_name[idx], bm1109_status_str(st));
                uart_send(buf);
            } else {
                g_r_ll[idx] = res.r_ohm;
                g_r_ok[idx] = 1U;
                print_r(pair_name[idx], &res, i_lo, i_hi);
            }
        }
    }

    if (do_l) {
        if (!g_r_ok[idx]) {
            uart_send("  L: пропуск — сначала нужен действительный R (он входит в формулу)\r\n");
        } else if (vbus_mv > (int32_t)BM1109_L_MAX_VBUS_MV) {
            sprintf(buf, "  L: ОТКАЗ — Vbus %ld мВ выше предела метода %u мВ\r\n",
                    (long)vbus_mv, (unsigned)BM1109_L_MAX_VBUS_MV);
            uart_send(buf);
            uart_send("     Для L нужен пониженный Vbus: лабораторный БП с ограничением тока.\r\n");
        } else {
            float l_h = 0.0f;
            st = measure_l_pair(pair_hi[idx], pair_lo[idx], g_r_ll[idx],
                                (uint32_t)vbus_mv, offset_code, &l_h);
            if (st != BM1109_OK) {
                sprintf(buf, "  L_LL(%s): %s\r\n", pair_name[idx], bm1109_status_str(st));
                uart_send(buf);
            } else {
                g_l_ll[idx] = l_h;
                g_l_ok[idx] = 1U;
                sprintf(buf, "  L_LL(%s)=%ld мкГн\r\n", pair_name[idx],
                        (long)(l_h * 1e6f + 0.5f));
                uart_send(buf);
            }
        }
    }
}

/* ---------------------------------------------------------------------------
 *  Сводка: фазные величины звезды и разброс между парами
 * ------------------------------------------------------------------------- */
static void print_summary(void)
{
    char buf[224];
    float r_ph[3];
    float l_ph[3];
    float r_valid[3];
    float l_valid[3];
    float r_mean = 0.0f;
    float l_mean = 0.0f;
    uint8_t r_num = 0U;
    uint8_t l_num = 0U;
    uint8_t k;

    bm1109_star_from_line(g_r_ll, g_l_ll, r_ph, l_ph);

    uart_send("\r\n=== Сводка (звезда, R_ph = R_LL/2) ===\r\n");

    for (k = 0U; k < 3U; k++) {
        if (g_r_ok[k]) {
            r_valid[r_num++] = r_ph[k];
            r_mean += r_ph[k];
        }
    }
    if (r_num == 0U) {
        uart_send("R: нет действительных измерений\r\n");
    } else {
        r_mean /= (float)r_num;
        sprintf(buf, "R_ph: UV=%ld VW=%ld WU=%ld мОм\r\n",
                (long)(r_ph[0] * 1000.0f + 0.5f),
                (long)(r_ph[1] * 1000.0f + 0.5f),
                (long)(r_ph[2] * 1000.0f + 0.5f));
        uart_send(buf);
        sprintf(buf, "      среднее по %u парам: %ld мОм, разброс %ld %%\r\n",
                (unsigned)r_num, (long)(r_mean * 1000.0f + 0.5f),
                (long)(bm1109_spread_percent(r_valid, r_num) + 0.5f));
        uart_send(buf);
    }

    for (k = 0U; k < 3U; k++) {
        if (g_l_ok[k]) {
            l_valid[l_num++] = l_ph[k];
            l_mean += l_ph[k];
        }
    }
    if (l_num == 0U) {
        uart_send("L: нет действительных измерений\r\n");
    } else {
        l_mean /= (float)l_num;
        sprintf(buf, "L_ph: UV=%ld VW=%ld WU=%ld мкГн\r\n",
                (long)(l_ph[0] * 1e6f + 0.5f),
                (long)(l_ph[1] * 1e6f + 0.5f),
                (long)(l_ph[2] * 1e6f + 0.5f));
        uart_send(buf);
        sprintf(buf, "      среднее по %u парам: %ld мкГн, разброс %ld %%\r\n",
                (unsigned)l_num, (long)(l_mean * 1e6f + 0.5f),
                (long)(bm1109_spread_percent(l_valid, l_num) + 0.5f));
        uart_send(buf);
        uart_send("      L_LL включает взаимную индуктивность пар; для точного L_ph\r\n");
        uart_send("      нужна паспортная поправка или измерение с разомкнутой третьей фазой.\r\n");
    }
}

/* ---------------------------------------------------------------------------
 *  Меню и конфигурация
 * ------------------------------------------------------------------------- */
static void show_menu(void)
{
    uart_send("\r\n--- BM1109: измерение R и L ---\r\n");
    uart_send("1. Пара UV: R и L\r\n");
    uart_send("2. Пара VW: R и L\r\n");
    uart_send("3. Пара WU: R и L\r\n");
    uart_send("A. Все пары: R и L + сводка\r\n");
    uart_send("R. Только R по всем парам\r\n");
    uart_send("L. Только L по всем парам\r\n");
    uart_send("C. Показать конфигурацию\r\n");
    uart_send("M. Меню\r\n");
    uart_send("Введите команду: ");
}

static void show_config(void)
{
    static const uint16_t t_cfg_us[3] = BM1109_L_T_US;
    char buf[224];

    sprintf(buf, "\r\n--- Конфигурация %s ---\r\n", BM1109_CFG_VERSION);
    uart_send(buf);
#if BM1109_PWM_TIM_INSTANCE == 1
    uart_send("Мост: TIM1 (BM1109_PWM_TIM_INSTANCE=1)\r\n");
#else
    uart_send("Мост: TIM8 (BM1109_PWM_TIM_INSTANCE=2)\r\n");
#endif
    sprintf(buf, "PWM: %lu Гц, ARR=%u, dead time DTG=%u\r\n",
            (unsigned long)BM1109_PWM_FREQ_HZ,
            (unsigned)BM1109_PWM_ARR, (unsigned)BM1109_PWM_DEADTIME_DTG);
    uart_send(buf);
    sprintf(buf, "Шунт: %u мОм, усиление %u; делитель Vbus: %u/%u\r\n",
            (unsigned)BM1109_RSHUNT_MOHM, (unsigned)BM1109_CS_GAIN,
            (unsigned)BM1109_VBUS_DIV_NUM, (unsigned)BM1109_VBUS_DIV_DEN);
    uart_send(buf);
    sprintf(buf, "Пределы: Vbus %u..%u мВ, I_peak %u мА, I_target %u мА\r\n",
            (unsigned)BM1109_VBUS_MIN_MV, (unsigned)BM1109_VBUS_MAX_MV,
            (unsigned)BM1109_I_PEAK_MA, (unsigned)BM1109_I_TARGET_MA);
    uart_send(buf);
    sprintf(buf, "R: %u точки, допуск %u %%, бюджет %u мс на точку\r\n",
            (unsigned)BM1109_R_POINT_COUNT, (unsigned)BM1109_R_TOL_PCT,
            (unsigned)BM1109_R_MAX_ON_MS);
    uart_send(buf);
    sprintf(buf, "L: Vbus <= %u мВ, выборки %u/%u/%u мкс, x2 до %u раза\r\n",
            (unsigned)BM1109_L_MAX_VBUS_MV,
            (unsigned)t_cfg_us[0], (unsigned)t_cfg_us[1], (unsigned)t_cfg_us[2],
            (unsigned)BM1109_L_T_SCALE_MAX);
    uart_send(buf);
    uart_send("ВНИМАНИЕ: RSHUNT/CS_GAIN/делитель помечены ПРОВЕРИТЬ — до сверки\r\n");
    uart_send("с BOM абсолютные значения R недействительны (справедлива только\r\n");
    uart_send("форма U(I) и разброс между парами).\r\n");
}

/* ---------------------------------------------------------------------------
 *  main
 * ------------------------------------------------------------------------- */
int main(void)
{
    char buf[64];
    char cmd;

    uart_init();
    dwt_init();
    gpio_init();
    adc_init();
    pwm_init();
    power_off_all();
    results_reset();

    uart_send("\r\nBM1109 R/L measurement " BM1109_CFG_VERSION "\r\n");
    sprintf(buf, "Система: %lu Гц, ключи выключены\r\n", (unsigned long)BM1109_SYSCLK_HZ);
    uart_send(buf);
    show_menu();

    for (;;) {
        cmd = uart_recv();

        if (cmd == '\r' || cmd == '\n') {
            continue;
        }

        if (cmd >= '1' && cmd <= '3') {
            measure_pair((uint8_t)(cmd - '1'), 1, 1);
            print_summary();
            show_menu();
        } else if (cmd == 'a' || cmd == 'A') {
            results_reset();
            measure_pair(0U, 1, 1);
            measure_pair(1U, 1, 1);
            measure_pair(2U, 1, 1);
            print_summary();
            show_menu();
        } else if (cmd == 'r' || cmd == 'R') {
            results_reset();
            measure_pair(0U, 1, 0);
            measure_pair(1U, 1, 0);
            measure_pair(2U, 1, 0);
            print_summary();
            show_menu();
        } else if (cmd == 'l' || cmd == 'L') {
            results_reset();
            measure_pair(0U, 0, 1);
            measure_pair(1U, 0, 1);
            measure_pair(2U, 0, 1);
            print_summary();
            show_menu();
        } else if (cmd == 'c' || cmd == 'C') {
            show_config();
            show_menu();
        } else if (cmd == 'm' || cmd == 'M') {
            show_menu();
        } else {
            uart_send("Неизвестная команда. M — меню.\r\n");
        }
    }
}
