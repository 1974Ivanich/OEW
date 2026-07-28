#include "pwm.h"
#include "stm32g474xx.h"

/* Целевая частота ШИМ и детерминированный Ts.
 * Ts = 1/f_pwm при center-aligned. FOC-математика использует этот период. */
#define FOC_PWM_FREQ        5000U
#define FOC_DEAD_TIME_NS    1500U

/* Фактический ARR — для пересчёта duty % → тики в PWM_SetDuty */
static uint16_t pwm_arr = 99;

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
    uint32_t psc_plus1 = SystemCoreClock / 10000000UL;
    if(psc_plus1 == 0) psc_plus1 = 1;
    uint32_t timer_clk = SystemCoreClock / psc_plus1;
    uint32_t arr_plus1 = (timer_clk + FOC_PWM_FREQ) / (FOC_PWM_FREQ * 2);  /* округление */
    uint32_t dtg = (uint32_t)(((uint64_t)timer_clk * FOC_DEAD_TIME_NS + 500000000ULL) / 1000000000ULL);
    if(dtg < 1) dtg = 1;
    if(dtg > 127) dtg = 127;  /* simple DTG range */
    uint16_t psc = (uint16_t)(psc_plus1 - 1);
    uint16_t arr = (uint16_t)(arr_plus1 - 1);
    uint8_t  dtg8 = (uint8_t)dtg;
    pwm_arr = arr;

    /* TIM1 — Инвертор 1 (Master) */
    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN;
    TIM1->PSC = psc; TIM1->ARR = arr;
    TIM1->CR1 = TIM_CR1_CMS_1 | TIM_CR1_ARPE;  /* center-aligned + ARR preload */
    /* AOE=0: после break-события MOE не восстанавливается автоматически —
     * требуется программный перезапуск (безопасность силовой части). */
    TIM1->BDTR = dtg8;  /* dead-time, без AOE */
    TIM1->CCMR1 |= (6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM1->CCMR2 |= (6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM1->CCR1=0; TIM1->CCR2=0; TIM1->CCR3=0;
    TIM1->CCER=0;
    /* Master: TRGO update event → синхронный старт TIM8 */
    TIM1->CR2 = (2U << TIM_CR2_MMS_Pos);  /* MMS=010: Update event = TRGO */
    TIM1->EGR |= TIM_EGR_UG;

    /* TIM8 — Инвертор 2 (Slave, синхронизирован с TIM1 через ITR0) */
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN;
    TIM8->PSC = psc; TIM8->ARR = arr;
    TIM8->CR1 = TIM_CR1_CMS_1 | TIM_CR1_ARPE;  /* center-aligned + ARR preload */
    TIM8->BDTR = dtg8;  /* dead-time, без AOE */
    TIM8->CCMR1 |= (6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM8->CCMR2 |= (6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM8->CCR1=0; TIM8->CCR2=0; TIM8->CCR3=0;
    TIM8->CCER=0;
    /* Slave: старт по TRGO от TIM1 (ITR0 для TIM8 на STM32G4 = TIM1)
     * TS=000: ITR0, SMS=100: Reset mode — счётчик TIM8 сбрасывается
     * по TRGO от TIM1 (update event), обеспечивая синхронность. */
    TIM8->SMCR = (0U << TIM_SMCR_TS_Pos)   /* TS=000: ITR0 (TIM1_TRGO) */
               | (4U << TIM_SMCR_SMS_Pos); /* SMS=100: Reset mode */
    TIM8->EGR |= TIM_EGR_UG;
}

/* Вход — duty в процентах (0..100), пересчёт в тики по фактическому ARR.
 * TODO(Q-унификация): перейти на Q15 duty для полного разрешения таймера. */
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w) {
    uint32_t p = (uint32_t)pwm_arr + 1;
    TIM1->CCR1 = (uint16_t)((u * p) / 100);
    TIM1->CCR2 = (uint16_t)((v * p) / 100);
    TIM1->CCR3 = (uint16_t)((w * p) / 100);
}

void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w) {
    uint32_t p = (uint32_t)pwm_arr + 1;
    TIM8->CCR1 = (uint16_t)((u * p) / 100);
    TIM8->CCR2 = (uint16_t)((v * p) / 100);
    TIM8->CCR3 = (uint16_t)((w * p) / 100);
}

void PWM_Enable(void) {
    TIM1->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    TIM8->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    /* MOE включаем до CEN — оба таймера стартуют синхронно через master/slave */
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    /* CEN TIM1 → TRGO → reset TIM8 (синхронный старт) + ADC injected trigger.
     * TIM8 также включаем явно, чтобы был готов к первому TRGO. */
    TIM8->CR1 |= TIM_CR1_CEN;
    TIM1->CR1 |= TIM_CR1_CEN;
    /* FOC_Run вызывается из ADC1_2_IRQHandler по JEOS —
     * аппаратный триггер TIM1_TRGO → ADC → ISR. DIER UIE не нужен. */
}

void PWM_Disable(void) {
    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
}

void PWM_SetDeadTimeComp(int32_t dt_ticks) {
    (void)dt_ticks; // будет реализовано
}

/* ── Debug tool: прямое управление TIM1/TIM8 ───────────────────── */
void PWM_DebugConfig(uint16_t arr, uint16_t duty, uint8_t dt, uint8_t mask) {
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;

    /* PSC для ~10 МГц таймера (как в PWM_Init) */
    uint32_t psc_plus1 = SystemCoreClock / 10000000UL;
    if(psc_plus1 == 0) psc_plus1 = 1;
    TIM1->PSC = (uint16_t)(psc_plus1 - 1);
    TIM8->PSC = (uint16_t)(psc_plus1 - 1);

    TIM1->ARR = arr; TIM8->ARR = arr;
    TIM1->CCR1 = TIM1->CCR2 = TIM1->CCR3 = duty;
    TIM8->CCR1 = TIM8->CCR2 = TIM8->CCR3 = duty;

    TIM1->BDTR = (TIM1->BDTR & 0xFFFFFF00) | (dt & 0xFF);
    TIM8->BDTR = (TIM8->BDTR & 0xFFFFFF00) | (dt & 0xFF);

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
        TIM1->BDTR |= TIM_BDTR_MOE;
        TIM8->BDTR |= TIM_BDTR_MOE;
        TIM1->CR1 |= TIM_CR1_CEN;
        TIM8->CR1 |= TIM_CR1_CEN;
    }
}

void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk) {
    *psc = TIM1->PSC;
    *tclk = SystemCoreClock / (TIM1->PSC + 1);
}

void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt) {
    *cr1  = TIM1->CR1;
    *ccer = TIM1->CCER;
    *bdtr = TIM1->BDTR;
    *cnt  = TIM1->CNT;
}

void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr, uint32_t *cr1, uint32_t *cr2) {
    *psc  = TIM1->PSC;
    *arr  = TIM1->ARR;
    *bdtr = TIM1->BDTR;
    *cr1  = TIM1->CR1;
    *cr2  = TIM1->CR2;
}
