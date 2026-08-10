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

/* Кодирование тиков t_DTS в байт DTG[7:0] (RM0440 §27.4.10).
 * Округляет к ближайшему представимому DTG значению.
 * Диапазоны: 0..127, 128..254, 256..504, 512..1008.
 * Разрывы (255, 505..511) — аппаратно непредставимы, округляются вверх. */
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

/* Публичная установка dead-time в НАНОСЕКУНДАХ.
 * ВНИМАНИЕ: SERVICE-ONLY — не вызывать во время работы FOC/V/f control loop.
 * Dead-time — статический параметр силового каскада, устанавливается в PWM_Init().
 * Runtime компенсация dead-time делается в FOC (voltage compensation), не через BDTR.
 *
 * Функция использует __disable_irq() — глобальную маску ВСЕХ maskable IRQ, включая
 * ADC1_2_IRQHandler (priority 0, 5кГц FOC). Внутри критической секции находится
 * bounded busy-wait (while(ADC2->CR & JADSTP)) — длительность непредсказуема.
 * Поэтому функция НЕ realtime-safe: вызов из main во время активного FOC задержит
 * control loop на неопределённое время. Допустима только при остановленном
 * силовом управлении (debug, калибровка, инициализация). */
void PWM_SetDeadTime_ns(uint32_t dt_ns) {
    /* Полная транзакция: IRQ off → ADC stop → TIM stop → change DT → UG → TIM start → ADC arm → IRQ on.
     * Один PWM-цикл будет пропущен. */
    uint32_t tck = get_tim_ck_int();
    uint32_t n = (uint32_t)(((uint64_t)dt_ns * tck + 500000000ULL) / 1000000000ULL);
    if(n < 1) n = 1;
    uint8_t enc = encode_dtg_ticks(n);
    uint32_t was_armed = ADC2->CR & ADC_CR_JADSTART;
    __disable_irq();
    if(was_armed) {
        ADC2->CR |= ADC_CR_JADSTP;
        uint32_t tj = 100000;
        while(ADC2->CR & ADC_CR_JADSTP) { if(--tj == 0) break; }
    }
    ADC2->ISR = ADC_ISR_JEOS | ADC_ISR_OVR;  /* очистить флаги перед UG */
    TIM1->CR1 &= ~TIM_CR1_CEN;  TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM1->BDTR = (TIM1->BDTR & 0xFFFFFF00U) | enc;
    TIM8->BDTR = (TIM8->BDTR & 0xFFFFFF00U) | enc;
    TIM1->EGR = TIM_EGR_UG;    TIM8->EGR = TIM_EGR_UG;
    ADC2->ISR = ADC_ISR_JEOS | ADC_ISR_OVR;  /* очистить ложный JEOS от UG-TRGO */
    __DSB();
    TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1 |= TIM_CR1_CEN;   TIM8->CR1 |= TIM_CR1_CEN;
    if(was_armed) {
        ADC2->ISR = ADC_ISR_JEOS;
        ADC2->CR |= ADC_CR_JADSTART;  /* реарм injected */
    }
    __enable_irq();
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
     * DTG is specified in t_DTS ticks. With CKD=00: t_DTS = 1 / TIMx_CK_INT.
     * PSC does NOT affect dead-time clock. DTG ticks = dt_ns * t_CK_INT / 1e9.
     *
     * Примеры:
     *   16 МГц: PSC=0  (timer=16МГц), ARR=1599, DTG=24  (1.5 мкс)
     *   170МГц: PSC=16 (timer=10МГц), ARR=999,  DTG=0xC0 (256 ticks, ~1.506 мкс) */
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
    TIM1->RCR = 1U;  /* RM0440 §27.4.22: update_rate = UEV_rate / (RCR+1).
     * Center-aligned mode 3 → 2 UEV/период (overflow + underflow).
     * RCR=1 → 2/(1+1) = 1 TRGO за полный период = 5 кГц.
     * RCR=0 → 2 TRGO/период = 10 кГц (полупериод) — TIM8 reset на каждом полупериоде. */
    /* Критически важно (RM0440): OSSR=1 + OSSI=1 + AOE=1 */
    TIM1->BDTR = dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE;
    TIM1->CCMR1 = (6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM1->CCMR2 = (6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM1->CCR1=0; TIM1->CCR2=0; TIM1->CCR3=0;
    TIM1->CCER=0;
    /* Master: TRGO = Update event → триггер ADC injected group + TIM8 slave reset. */
    TIM1->CR2 = (2U << TIM_CR2_MMS_Pos);  /* MMS=010: Update event = TRGO */
    TIM1->CNT = 0;  /* детерминированный старт */
    TIM1->EGR = TIM_EGR_UG;  /* preload → shadow. Безопасно: ADC injected ещё не настроен */

    /* TIM8 — Инвертор 2 (slave, center-aligned, тот же PSC/ARR).
     * Аппаратная синхронизация от TIM1: Reset mode по ITR0 (TIM1_TRGO).
     * TIM1 — единый временной master: TRGO → ADC injected + TIM8 reset.
     * Рассинхрон на старте исключён: оба CNT=0, TIM8 сбрасывается каждый
     * период по TRGO от TIM1 (update event, RCR=1 → 1× за период). */
    RCC->APB2ENR |= RCC_APB2ENR_TIM8EN;
    TIM8->PSC = psc; TIM8->ARR = arr;
    TIM8->CR1 = TIM_CR1_CMS_1 | TIM_CR1_CMS_0 | TIM_CR1_ARPE;  /* Center-aligned mode 3 (both slopes) + ARR preload */
    TIM8->RCR = 1U;  /* идентично TIM1 — update 1× за полный период */
    TIM8->BDTR = dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE;
    /* OEW: TIM8 в PWM mode 2 (OCxM=111, активен при CNT>CCR) — СИГНАЛЬНАЯ противофаза
     * при синфазных счётчиках. При одинаковом CCR: HIN_U2 активен при CNT>CCR,
     * значит LIN_U2 (CHxN) активен при CNT<CCR — ровно как HIN_U1 (TIM1, mode 1).
     * → HIN_U1=1 ⇔ LIN_U2=1 всегда: верхний Inv1 + нижний Inv2 открыты вместе,
     * ток по обмотке OEW течёт. Среднее V_U = (2·CCR − ARR)·VBUS/ARR.
     * (mode 1 на обоих давал HIN_U2 синфазно HIN_U1 → LIN_U2=0 при HIN_U1=1.) */
    TIM8->CCMR1 = (7U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(7U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM8->CCMR2 = (7U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM8->CCR1=0; TIM8->CCR2=0; TIM8->CCR3=0;
    TIM8->CCER=0;
    TIM8->SMCR = 4U;  /* SMS=Reset mode, TS=ITR0 (TIM1_TRGO) — аппаратная синхронизация */
    TIM8->CNT = 0;    /* детерминированный старт */
    TIM8->EGR = TIM_EGR_UG;
}

/* Вход — duty в процентах (0..100), пересчёт в тики по фактическому ARR.
 * Формула: CCR = (duty * ARR + 50) / 100 — округление к ближайшему,
 * гарантия CCR ≤ ARR при duty=100%: (100*999+50)/100 = 999.
 * Это АБСОЛЮТНЫЙ timer duty: 0%→CCR=0, 50%→CCR=ARR/2, 100%→CCR=ARR.
 * FOC использует dc_bias=50 как midpoint для OEW bipolar modulation.
 * PWM_DebugConfig использует другую шкалу (OEW modulation index).
 * TODO(Q-унификация): перейти на Q15 duty для полного разрешения таймера. */
static inline uint16_t duty_to_ccr(uint16_t duty) {
    if(duty > 100U) duty = 100U;
    return (uint16_t)(((uint32_t)duty * pwm_arr + 50U) / 100U);
}

void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w) {
    TIM1->CCR1 = duty_to_ccr(u);
    TIM1->CCR2 = duty_to_ccr(v);
    TIM1->CCR3 = duty_to_ccr(w);
}

void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w) {
    TIM8->CCR1 = duty_to_ccr(u);
    TIM8->CCR2 = duty_to_ccr(v);
    TIM8->CCR3 = duty_to_ccr(w);
}

void PWM_Enable(void) {
    GPIOB->BSRR = (1U<<4)|(1U<<5);  /* EN1, EN2 = HIGH */
    TIM1->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    TIM8->CCER = TIM_CCER_CC1E|TIM_CCER_CC1NE|TIM_CCER_CC2E|TIM_CCER_CC2NE|TIM_CCER_CC3E|TIM_CCER_CC3NE;
    /* MOE включаем до CEN. TIM8 — slave (Reset mode по ITR0),
     * стартует одновременно с TIM1, синхронизация — аппаратная. */
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM8->CR1 |= TIM_CR1_CEN;
    TIM1->CR1 |= TIM_CR1_CEN;  /* TIM1 master → TRGO → ADC injected + TIM8 sync */
    /* FOC_Run вызывается из ADC1_2_IRQHandler по JEOS —
     * аппаратный триггер TIM1_TRGO → ADC → ISR. DIER UIE не нужен. */
}

void PWM_Disable(void) {
    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;
    /* Снять JADSTART (injected вооружён). Иначе ADC_StartConversion() выходит
     * сразу (adc.c: if(CR & JADSTART) return) — кеш adc_data[] застывает на
     * последнем значении и VBUS/токи не обновляются после остановки PWM. */
    if(ADC2->CR & ADC_CR_JADSTART) {
        ADC2->CR |= ADC_CR_JADSTP;
        uint32_t tj = 100000;
        while(ADC2->CR & ADC_CR_JADSTP) { if(--tj == 0) break; }
        ADC2->ISR = ADC_ISR_JEOS | ADC_ISR_OVR;
    }
    GPIOB->BSRR = (1U<<(16+4))|(1U<<(16+5));  /* EN1, EN2 = LOW */
}

void PWM_SetDeadTimeComp(int32_t dt_ticks) {
    (void)dt_ticks; // будет реализовано
}

/* ── Debug tool: прямое управление TIM1/TIM8 ─────────────────────
 * duty — OEW modulation index 0..100%:
 *   0%  → CCR = ARR/2  → V_phase = 0 (нулевое напряжение обмотки)
 *   100% → CCR = ARR    → V_phase = +Vbus (максимальное положительное)
 * Формула: CCR = ARR/2 + duty·ARR/200 — сохраняет полное разрешение.
 * (Старый код duty/2 терял половину разрядности.)
 *
 * ВНИМАНИЕ: SERVICE/DEBUG ONLY. __disable_irq() маскирует ВСЕ IRQ включая
 * ADC1_2_IRQHandler (priority 0). Не вызывать во время активного FOC. */
void PWM_DebugConfig(uint16_t arr, uint16_t duty, uint32_t dt_ns, uint8_t mask) {
    __disable_irq();  /* глобальная маска ДО любых изменений timer state */
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;

    /* PSC для ~10 МГц таймера (как в PWM_Init) */
    uint32_t tck = get_tim_ck_int();
    uint32_t psc_plus1 = tck / 10000000UL;
    if(psc_plus1 == 0) psc_plus1 = 1;
    TIM1->PSC = (uint16_t)(psc_plus1 - 1);
    TIM8->PSC = (uint16_t)(psc_plus1 - 1);

    TIM1->ARR = arr; TIM8->ARR = arr;
    pwm_arr = arr;
    /* OEW: оба инвертора с ОДИНАКОВЫМ CCR, TIM8 в mode 2.
     * CCR = bias + duty·arr/200 — полное разрешение, без потери от duty/2.
     * duty=0 → CCR=bias (V=0), duty=100 → CCR=arr (V=+Vbus). */
    int32_t bias = ((int32_t)arr + 1) / 2;
    int32_t ccr = bias + (int32_t)(((uint32_t)duty * arr + 100U) / 200U);
    TIM1->CCR1 = TIM1->CCR2 = TIM1->CCR3 = (uint16_t)CLAMP(ccr, 1, (int32_t)arr);
    TIM8->CCR1 = TIM8->CCR2 = TIM8->CCR3 = (uint16_t)CLAMP(ccr, 1, (int32_t)arr);

    TIM1->CR1 &= ~TIM_CR1_CEN; TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE; TIM8->BDTR &= ~TIM_BDTR_MOE;

    /* dt в НАНОСЕКУНДАХ, кодируем от t_CK_INT (определяется get_tim_ck_int(), не хардкод) */
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

    /* UG: transfer shadow registers (BDTR, CCER) immediately.
     * EGR — командный регистр, пишем напрямую (=), не read-modify-write (|=). */
    TIM1->EGR = TIM_EGR_UG; TIM8->EGR = TIM_EGR_UG;

    if(mask) {
        TIM1->BDTR |= (TIM_BDTR_MOE | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE);
        TIM8->BDTR |= (TIM_BDTR_MOE | TIM_BDTR_OSSR | TIM_BDTR_OSSI | TIM_BDTR_AOE);
        TIM1->CR1 |= TIM_CR1_CEN; TIM8->CR1 |= TIM_CR1_CEN;
    }
    __enable_irq();
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
