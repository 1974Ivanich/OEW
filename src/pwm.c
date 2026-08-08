#include "pwm.h"

/* CLAMP отсутствует в pwm.h; foc.h его определяет, но не будем тянуть зависимость */
#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif
#include "stm32g474xx.h"

/* Целевая частота ШИМ и детерминированный Ts.
 * Ts = 1/f_pwm при center-aligned. FOC-математика использует этот период. */
#define FOC_PWM_FREQ        5000U
#define FOC_DEAD_TIME_NS    1500U

/* Фактический ARR — для пересчёта duty % → тики в PWM_SetDuty */
static uint16_t pwm_arr = 99;


/* ── Реальная частота t_CK_INT для TIM1/TIM8 (шина APB2, ДО prescaler PSC) ──
 * По RM0440: если APB2 prescaler = 1  → TIMx_CLK = HCLK
 *            если APB2 prescaler != 1 → TIMx_CLK = 2 * APB2_clk
 * Это и есть t_CK_INT, от которого считается t_DTS (при CKD=00). */
static uint32_t get_tim_ck_int(void) {
    uint32_t ppre2 = (RCC->CFGR & RCC_CFGR_PPRE2) >> RCC_CFGR_PPRE2_Pos;
    uint32_t apb_div;
    switch (ppre2) {
        case 0U: apb_div = 1U;  break;
        case 4U: apb_div = 2U;  break;
        case 5U: apb_div = 4U;  break;
        case 6U: apb_div = 8U;  break;
        case 7U: apb_div = 16U; break;
        default: apb_div = 1U;  break;
    }
    uint32_t pclk2 = SystemCoreClock / apb_div;
    /* RM0440: advanced timers TIM1/TIM8 clock = 2×PCLK2 when APB prescaler != 1 */
    if (apb_div != 1U) return pclk2 * 2U;
    return pclk2;
}

/* Кодирование тиков t_DTS в байт DTG[7:0] (RM0440 §27.4.10) */
static uint8_t encode_dtg_ticks(uint32_t n) {
    if(n <= 127U)  return (uint8_t)n;
    if(n <= 254U)  return (uint8_t)(0x80U | (((n + 1U) / 2U) - 64U));
    if(n <= 504U)  return (uint8_t)(0xC0U | (((n + 4U) / 8U) - 32U));
    if(n <= 1008U) return (uint8_t)(0xE0U | (((n + 8U) / 16U) - 32U));
    return 0xFFU;
}

/* Обратное декодирование DTG в тики t_DTS */
static uint32_t decode_dtg_ticks(uint8_t dtg) {
    if((dtg & 0x80) == 0)        return (uint32_t)(dtg & 0x7F);
    if((dtg & 0xC0) == 0x80)     return ((uint32_t)(dtg & 0x3F) + 64U) * 2U;
    if((dtg & 0xE0) == 0xC0)     return ((uint32_t)(dtg & 0x1F) + 32U) * 8U;
    return ((uint32_t)(dtg & 0x1F) + 32U) * 16U;
}

/* Публичная установка dead-time в НАНОСЕКУНДАХ */
void PWM_SetDeadTime_ns(uint32_t dt_ns) {
    uint32_t tck = get_tim_ck_int();
    uint32_t n = (uint32_t)(((uint64_t)dt_ns * tck + 500000000ULL) / 1000000000ULL);
    if(n < 1) n = 1;
    uint8_t enc = encode_dtg_ticks(n);
    TIM1->CR1 &= ~TIM_CR1_CEN;  TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM1->BDTR = (TIM1->BDTR & 0xFFFFFF00U) | enc;
    TIM8->BDTR = (TIM8->BDTR & 0xFFFFFF00U) | enc;
    TIM1->EGR |= TIM_EGR_UG;    TIM8->EGR |= TIM_EGR_UG;
    __DSB();
    TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1 |= TIM_CR1_CEN;   TIM8->CR1 |= TIM_CR1_CEN;
}

uint32_t PWM_GetDeadTime_ns(void) {
    uint32_t tck = get_tim_ck_int();
    uint32_t n = decode_dtg_ticks((uint8_t)(TIM1->BDTR & 0xFF));
    return (uint32_t)(((uint64_t)n * 1000000000ULL + tck / 2U) / tck);
}

void PWM_Init(void) {
    /* Расчёт PSC/ARR/DTG от фактического SystemCoreClock.
     * Цель: f_PWM = 5 кГц, dead-time ≈ 1.5 мкс.
     * timer_clk выбираем ~10 МГц (PSC+1 = SystemCoreClock / 10 МГц),
     * ARR+1 = timer_clk / (2 * 5 кГц).
     * DTG = timer_clk * dead_time_ns / 1e9 (simple range, t_DTS = t_CK_INT).
     *
     * Примеры:
     *   16 МГц: PSC=0  (timer=16МГц), ARR=1599, DTG=24  (1.5 мкс)
     *   170МГц: PSC=16 (timer=10МГц), ARR=999,  DTG=15  (1.5 мкс) */
    uint32_t tck = get_tim_ck_int();
    uint32_t psc_plus1 = tck / 10000000UL;
    if(psc_plus1 == 0) psc_plus1 = 1;
    uint32_t timer_clk = tck / psc_plus1;
    uint32_t arr_plus1 = (timer_clk + FOC_PWM_FREQ) / (FOC_PWM_FREQ * 2);  /* округление */
    uint16_t psc = (uint16_t)(psc_plus1 - 1);
    uint16_t arr = (uint16_t)(arr_plus1 - 1);
    uint32_t dtg_ticks = (uint32_t)(((uint64_t)1500U * tck + 500000000ULL) / 1000000000ULL);
    uint8_t dtg8 = encode_dtg_ticks(dtg_ticks);
    pwm_arr = arr;

    /* TIM1 — Инвертор 1 (Master) */
    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN;
    TIM1->PSC = psc; TIM1->ARR = arr;
    TIM1->CR1 = TIM_CR1_CMS_1 | TIM_CR1_CMS_0 | TIM_CR1_ARPE;  /* Center-aligned mode 3 (both slopes) + ARR preload */
    TIM1->RCR = 1U;  /* UEV/TRGO 1× за полный период (center-aligned: 2 UEV/период, RCR делит на 2) — иначе TIM8 (Reset mode) считает полупериод → 100 кГц */
    /* Критически важно (RM0440): OSSR=1 + OSSI=1 + AOE=1 */
    TIM1->BDTR = dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE;
    TIM1->CCMR1 |= (6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM1->CCMR2 |= (6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM1->CCR1=0; TIM1->CCR2=0; TIM1->CCR3=0;
    TIM1->CCER=0;
    /* Master: TRGO = Update event → триггер ADC injected group.
     * TIM8 не слушает TRGO (SMCR=0), работает независимо. */
    TIM1->CR2 = (2U << TIM_CR2_MMS_Pos);  /* MMS=010: Update event = TRGO */
    TIM1->EGR |= TIM_EGR_UG;

    /* TIM8 — Инвертор 2 (независимый, center-aligned, тот же PSC/ARR).
     * Slave-синхронизация (SMCR) отключена: оба таймера работают
     * независимо с одинаковыми параметрами. Рассинхронизация на
     * несколько тактов при старте допустима — OEW-распределение
     * симметрично (dc_bias ± half_v), перекос не критичен.
     * Для строгой синхронизации можно включить SMS=Reset, TS=ITR0. */
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN;
    TIM8->PSC = psc; TIM8->ARR = arr;
    TIM8->CR1 = TIM_CR1_CMS_1 | TIM_CR1_CMS_0 | TIM_CR1_ARPE;  /* Center-aligned mode 3 (both slopes) + ARR preload */
    TIM8->BDTR = dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE;
    /* OEW: TIM8 в PWM mode 2 (OCxM=111, активен при CNT>CCR) — СИГНАЛЬНАЯ противофаза
     * при синфазных счётчиках. При одинаковом CCR: HIN_U2 активен при CNT>CCR,
     * значит LIN_U2 (CHxN) активен при CNT<CCR — ровно как HIN_U1 (TIM1, mode 1).
     * → HIN_U1=1 ⇔ LIN_U2=1 всегда: верхний Inv1 + нижний Inv2 открыты вместе,
     * ток по обмотке OEW течёт. Среднее V_U = (2·CCR − ARR)·VBUS/ARR.
     * (mode 1 на обоих давал HIN_U2 синфазно HIN_U1 → LIN_U2=0 при HIN_U1=1.) */
    TIM8->CCMR1 |= (7U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(7U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM8->CCMR2 |= (7U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM8->CCR1=0; TIM8->CCR2=0; TIM8->CCR3=0;
    TIM8->CCER=0;
    TIM8->SMCR = 0;  /* без slave sync */
    TIM8->EGR |= TIM_EGR_UG;
}

/* Вход — duty в процентах (0..100), пересчёт в тики по фактическому ARR.
 * Значения за пределами 0..100 ограничиваются (clamp), чтобы
 * гарантировать CCR ≤ ARR и CCR ≥ 0.
 * TODO(Q-унификация): перейти на Q15 duty для полного разрешения таймера. */
static inline uint16_t duty_clamp(uint16_t d) {
    if(d > 100U) d = 100U;
    return d;
}

void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w) {
    uint32_t p = (uint32_t)pwm_arr + 1;
    TIM1->CCR1 = (uint16_t)((duty_clamp(u) * p) / 100);
    TIM1->CCR2 = (uint16_t)((duty_clamp(v) * p) / 100);
    TIM1->CCR3 = (uint16_t)((duty_clamp(w) * p) / 100);
}

void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w) {
    uint32_t p = (uint32_t)pwm_arr + 1;
    TIM8->CCR1 = (uint16_t)((duty_clamp(u) * p) / 100);
    TIM8->CCR2 = (uint16_t)((duty_clamp(v) * p) / 100);
    TIM8->CCR3 = (uint16_t)((duty_clamp(w) * p) / 100);
}

void PWM_Enable(void) {
    GPIOB->BSRR = (1U<<4)|(1U<<5);  /* EN1, EN2 = HIGH */
    TIM1->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    TIM8->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    /* MOE включаем до CEN. TIM8 стартует первым, затем TIM1.
     * Независимый запуск (без slave sync) — рассинхрон до нескольких
     * тактов t_CK_INT, для OEW-симметрии некритично. */
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM8->CR1 |= TIM_CR1_CEN;
    TIM1->CR1 |= TIM_CR1_CEN;  /* TIM1 TRGO → ADC injected → JEOS → FOC_Run */
    /* FOC_Run вызывается из ADC1_2_IRQHandler по JEOS —
     * аппаратный триггер TIM1_TRGO → ADC → ISR. DIER UIE не нужен. */
}

void PWM_Disable(void) {
    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    GPIOB->BSRR = (1U<<(16+4))|(1U<<(16+5));  /* EN1, EN2 = LOW */
}

void PWM_SetDeadTimeComp(int32_t dt_ticks) {
    (void)dt_ticks; // будет реализовано
}

/* ── Debug tool: прямое управление TIM1/TIM8 ───────────────────── */
void PWM_DebugConfig(uint16_t arr, uint16_t duty, uint32_t dt_ns, uint8_t mask) {
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;
    NVIC_DisableIRQ(ADC1_2_IRQn);  /* исключить race с ISR */

    /* PSC для ~10 МГц таймера (как в PWM_Init) */
    uint32_t tck = get_tim_ck_int();
    uint32_t psc_plus1 = tck / 10000000UL;
    if(psc_plus1 == 0) psc_plus1 = 1;
    TIM1->PSC = (uint16_t)(psc_plus1 - 1);
    TIM8->PSC = (uint16_t)(psc_plus1 - 1);

    TIM1->ARR = arr; TIM8->ARR = arr;
    pwm_arr = arr;
    /* OEW: оба инвертора с ОДИНАКОВЫМ CCR (bias + duty/2), TIM8 в mode 2.
     * TIM8_CH1 (HIN_U2) активен при CNT>CCR, TIM8_CH1N (LIN_U2) при CNT<CCR —
     * ровно как HIN_U1 (TIM1 mode 1). → HIN_U1=1 ⇔ LIN_U2=1 всегда, ток течёт.
     * Среднее напряжение на обмотке = (2·CCR − ARR)·VBUS/ARR. */
    int32_t bias = ((int32_t)arr + 1) / 2;
    int32_t ccr = (int32_t)duty / 2;
    TIM1->CCR1 = TIM1->CCR2 = TIM1->CCR3 = (uint16_t)CLAMP(bias + ccr, 1, (int32_t)arr);
    TIM8->CCR1 = TIM8->CCR2 = TIM8->CCR3 = (uint16_t)CLAMP(bias + ccr, 1, (int32_t)arr);

    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;

    /* dt теперь в НАНОСЕКУНДАХ, кодируем от t_CK_INT (170 МГц, 5.88 нс/тик) */
    {
        uint32_t tck = get_tim_ck_int();
        uint32_t n = (uint32_t)(((uint64_t)dt_ns * tck + 500000000ULL) / 1000000000ULL);
        if(n < 1) n = 1;
        uint8_t enc = encode_dtg_ticks(n);
        TIM1->BDTR = (TIM1->BDTR & 0xFFFFFF00U) | enc;
        TIM8->BDTR = (TIM8->BDTR & 0xFFFFFF00U) | enc;
    }

    if(mask == 0) {
        TIM1->CCER = 0; TIM8->CCER = 0;
    } else {
        uint32_t ccer = 0;
        if(mask & 0x01) ccer |= TIM_CCER_CC1E;   /* PC0 */
        if(mask & 0x02) ccer |= TIM_CCER_CC1NE;  /* PA7 */
        if(mask & 0x04) ccer |= TIM_CCER_CC2E;   /* PC1 */
        if(mask & 0x08) ccer |= TIM_CCER_CC2NE;  /* PB0 */
        if(mask & 0x10) ccer |= TIM_CCER_CC3E;   /* PC2 */
        if(mask & 0x20) ccer |= TIM_CCER_CC3NE;  /* PB1 */
        TIM1->CCER = ccer;
        TIM8->CCER = ccer;
    }

    /* UG: transfer shadow registers (BDTR, CCER) immediately */
    TIM1->EGR |= TIM_EGR_UG; TIM8->EGR |= TIM_EGR_UG;
    TIM1->EGR &= ~TIM_EGR_UG; TIM8->EGR &= ~TIM_EGR_UG;

    if(mask) {
        TIM1->BDTR |= (TIM_BDTR_MOE | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE);
        TIM8->BDTR |= (TIM_BDTR_MOE | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE);
        TIM1->CR1 |= TIM_CR1_CEN; TIM8->CR1 |= TIM_CR1_CEN;
    }
    NVIC_EnableIRQ(ADC1_2_IRQn);
}

void PWM_DumpRegs8(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer) {
    *psc  = TIM8->PSC;
    *arr  = TIM8->ARR;
    *bdtr = TIM8->BDTR;
    *cr1  = TIM8->CR1;
    *cr2  = TIM8->CR2;
    *ccer = TIM8->CCER;
}

void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk) {
    *psc = TIM1->PSC;
    *tclk = get_tim_ck_int() / (TIM1->PSC + 1);
}





uint16_t PWM_GetARR(void) {
    return pwm_arr;
}

void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt) {
    *cr1  = TIM1->CR1;
    *ccer = TIM1->CCER;
    *bdtr = TIM1->BDTR;
    *cnt  = TIM1->CNT;
}

void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2, uint32_t *ccer) {
    *psc  = TIM1->PSC;
    *arr  = TIM1->ARR;
    *bdtr = TIM1->BDTR;
    *cr1  = TIM1->CR1;
    *cr2  = TIM1->CR2;
    *ccer = TIM1->CCER;
}
