#include "stm32g474xx.h"
#include "encoder.h"

/* AS5048A PWM-выход — драйвер на TIM2 PWM Input Capture Mode.
 * SPI-режим удалён — используется только однопроводной PWM-выход.
 * Подробности протокола и распиновки — см. encoder.h.
 *
 * TIM2 PWM Input Mode (RM0440 §29.4.8):
 *   CH1 (прямой, TI1)  — rising edge, IC1 → CCR1 = ПЕРИОД (тики автосброса)
 *   CH2 (косвенный TI1) — falling edge, IC2 → CCR2 = ДЛИТЕЛЬНОСТЬ ИМПУЛЬСА
 *   Slave mode: Reset, триггер = TI1FP1 → счётчик сбрасывается на каждом
 *   rising edge, поэтому CCR1 после капчура = точный период между двумя
 *   последовательными rising-фронтами (в тиках PSC).
 *   PSC настроен на 1 МГц → 1 тик = 1 мкс. TIM2 32-битный — переполнение
 *   счётчика между сбросами (период ~1.1 мс << 2^32 мкс) невозможно. */

#define ENC_COUNTS_PER_REV   16384u
#define ENC_FILTER_SHIFT     3       /* IIR 1/8 */
#define ENC_TIMER_HZ         1000000u  /* 1 МГц после PSC */

/* Ожидаемый период AS5048A PWM ≈ 920 Гц (период ≈ 1.087 мс), duty линейно
 * 0..100% = angle/16384 (0° → 0%, 360° → ~100%, без мёртвой зоны на краях).
 * Разумный диапазон для валидации захваченного периода — если вне этого
 * окна, считаем захват мусором (шум/наводка/отключенный энкодер). */
#define ENC_PERIOD_MIN_US    700u
#define ENC_PERIOD_MAX_US    1500u

/* Заводские дефолты диапазона duty (доля от периода, Q16): полная шкала
 * 0..100%, без офсета на краях (в отличие от ранее принятого допущения
 * ~0.024%..99.98%). Используйте ENC_Calibrate() для уточнения под
 * конкретный экземпляр/ревизию, если реальные крайние значения отличаются. */
#define ENC_DUTY_MIN_Q16_DEFAULT   0u
#define ENC_DUTY_MAX_Q16_DEFAULT   65535u

/* Таймаут отсутствия новых импульсов — не более ~3 периодов подряд
 * (~3.3 мс при 920 Гц) прежде чем считать сигнал потерянным. */
#define ENC_TIMEOUT_MS       5u

static volatile uint32_t enc_period_us = 0;
static volatile uint32_t enc_pulse_us  = 0;
static volatile uint32_t enc_capture_count = 0;
static volatile uint8_t  enc_error = 0;

static volatile uint16_t enc_angle14 = 0;
static volatile int32_t  enc_speed_rpm = 0;
static uint16_t prev_angle14 = 0;
static uint8_t  first_capture = 1;

static uint32_t duty_min_q16 = ENC_DUTY_MIN_Q16_DEFAULT;
static uint32_t duty_max_q16 = ENC_DUTY_MAX_Q16_DEFAULT;

/* ── Тактовая частота APB1-таймеров (аналогично get_tim_ck_int в pwm.c) ── */
static uint32_t get_apb1_timer_clock(void) {
    uint32_t ppre1 = (RCC->CFGR & RCC_CFGR_PPRE1) >> RCC_CFGR_PPRE1_Pos;
    uint32_t apb_div;
    switch (ppre1) {
        case 0U: apb_div = 1U;  break;
        case 4U: apb_div = 2U;  break;
        case 5U: apb_div = 4U;  break;
        case 6U: apb_div = 8U;  break;
        case 7U: apb_div = 16U; break;
        default: apb_div = 1U;  break;
    }
    uint32_t pclk1 = SystemCoreClock / apb_div;
    if (apb_div != 1U) return pclk1 * 2U;
    return pclk1;
}

/* ── Public API ─────────────────────────────────────────────────── */

void ENC_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    RCC->APB1ENR1 |= RCC_APB1ENR1_TIM2EN;
    (void)RCC->APB1ENR1;

    /* PA15 — TIM2_CH1 (AF1), вход, без подтяжки (сигнал активно управляется
     * выходным каскадом AS5048A). */
    GPIOA->MODER &= ~(3U << (15 * 2));
    GPIOA->MODER |=  (2U << (15 * 2));   /* AF mode */
    GPIOA->AFR[1] &= ~(0xFU << ((15 - 8) * 4));
    GPIOA->AFR[1] |=  (1U   << ((15 - 8) * 4));  /* AF1 = TIM2_CH1 */
    GPIOA->PUPDR  &= ~(3U << (15 * 2));   /* без подтяжки */
    GPIOA->OSPEEDR |= (3U << (15 * 2));

    uint32_t tim_clk = get_apb1_timer_clock();
    uint32_t psc = tim_clk / ENC_TIMER_HZ;
    if (psc == 0) psc = 1;
    TIM2->PSC = (uint16_t)(psc - 1);
    TIM2->ARR = 0xFFFFFFFFU;  /* не используется как auto-reload для сброса —
                                 сброс делает slave mode по TI1FP1 */

    /* CC1S=01: IC1 = TI1 (прямой). CC2S=10: IC2 = TI1 (косвенный). */
    TIM2->CCMR1 = (1U << TIM_CCMR1_CC1S_Pos) | (2U << TIM_CCMR1_CC2S_Pos)
                | (3U << TIM_CCMR1_IC1F_Pos) | (3U << TIM_CCMR1_IC2F_Pos);  /* фильтр N=8 */

    /* CC1P=0 (rising, период), CC2P=1 (falling, длительность импульса) */
    TIM2->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E | TIM_CCER_CC2P;

    /* Slave mode: Reset, триггер TI1FP1 (TS=101) */
    TIM2->SMCR = (5U << TIM_SMCR_TS_Pos) | (4U << TIM_SMCR_SMS_Pos);

    TIM2->SR = 0;                 /* очистка флагов перед разрешением IRQ */
    /* IRQ по CC1IF (rising), НЕ по CC2IF (falling): на rising-фронте CCR1
     * (период) и CCR2 (импульс, захваченный ДО этого фронта) относятся к
     * ОДНОМУ И ТОМУ ЖЕ только что завершённому циклу. При триггере на
     * CC2IF в момент чтения CCR1 содержал бы период ПРЕДЫДУЩЕГО цикла
     * (захвачен на предыдущем rising, до текущего reset), а CCR2 — импульс
     * ТЕКУЩЕГО — рассинхронизация пары period/pulse на один цикл при
     * изменении частоты/джиттере сигнала. */
    TIM2->DIER = TIM_DIER_CC1IE;

    NVIC_SetPriority(TIM2_IRQn, 1);  /* тот же приоритет, что и TIM6 (encoder/V-f loop) */
    NVIC_EnableIRQ(TIM2_IRQn);

    enc_error = ENC_ERR_TIMEOUT;  /* пока не пришёл первый валидный захват */
    first_capture = 1;

    TIM2->CR1 |= TIM_CR1_CEN;
}

void TIM2_IRQHandler(void) {
    uint32_t sr = TIM2->SR;

    /* Overcapture: если между двумя обслуживаниями IRQ произошёл ещё один
     * capture до чтения CCRx — единственный аппаратный механизм, способный
     * разрушить атомарность пары period/pulse. На ~920 Гц при приоритете 1
     * маловероятно, но не исключено при загрузке CPU/задержке IRQ. */
    if (sr & (TIM_SR_CC1OF | TIM_SR_CC2OF)) {
        TIM2->SR = ~(TIM_SR_CC1OF | TIM_SR_CC2OF | TIM_SR_CC1IF | TIM_SR_CC2IF);
        enc_error = ENC_ERR_BAD_PERIOD;
        first_capture = 1;
        return;
    }

    if (sr & TIM_SR_CC1IF) {
        TIM2->SR = ~(TIM_SR_CC2IF | TIM_SR_CC1IF);  /* rc_w0: очищает только эти два бита,
                                                        остальные — no-op (запись 1) */
        uint32_t period = TIM2->CCR1;
        uint32_t pulse  = TIM2->CCR2;

        if (period < ENC_PERIOD_MIN_US || period > ENC_PERIOD_MAX_US || pulse > period) {
            enc_error = ENC_ERR_BAD_PERIOD;
            first_capture = 1;  /* переприйм после мусорного захвата */
            return;
        }

        enc_period_us = period;
        enc_pulse_us  = pulse;
        enc_capture_count++;
        enc_error = 0;

        /* duty_q16 = pulse * 65536 / period */
        uint32_t duty_q16 = (uint32_t)(((uint64_t)pulse << 16) / period);
        if (duty_q16 < duty_min_q16) duty_q16 = duty_min_q16;
        if (duty_q16 > duty_max_q16) duty_q16 = duty_max_q16;

        uint32_t span = duty_max_q16 - duty_min_q16;
        uint16_t angle = (uint16_t)(((uint64_t)(duty_q16 - duty_min_q16) * ENC_COUNTS_PER_REV) / span);
        if (angle >= ENC_COUNTS_PER_REV) angle = ENC_COUNTS_PER_REV - 1;
        enc_angle14 = angle;

        if (first_capture) {
            first_capture = 0;
            prev_angle14 = angle;
            return;  /* сохранить последнюю enc_speed_rpm, не сбрасывать */
        }

        int32_t delta = (int32_t)angle - (int32_t)prev_angle14;
        if (delta >  (int32_t)(ENC_COUNTS_PER_REV / 2)) delta -= ENC_COUNTS_PER_REV;
        if (delta < -(int32_t)(ENC_COUNTS_PER_REV / 2)) delta += ENC_COUNTS_PER_REV;

        /* rpm = delta_rev * (60e6 / период_мкс), delta_rev = delta/16384 */
        int32_t rpm = (int32_t)(((int64_t)delta * 60000000LL) / ((int64_t)ENC_COUNTS_PER_REV * (int64_t)period));

        enc_speed_rpm += (rpm - enc_speed_rpm) >> ENC_FILTER_SHIFT;
        prev_angle14 = angle;
    }
}

uint16_t ENC_GetAngle14(void) {
    if (enc_error) return 0xFFFFu;
    return enc_angle14;
}

int32_t ENC_GetAngle_deg(void) {
    /* Согласовано с ENC_GetAngle14(): при ошибке не отдаём старый угол. */
    if (enc_error) return -1;
    return (int32_t)((uint32_t)enc_angle14 * 360 / 16384);
}

int32_t ENC_GetSpeed_rpm(void) {
    /* При потере сигнала (watchdog в ENC_Update()) НЕ отдаём последнее
     * отфильтрованное значение — иначе вызывающий код (V/f, FOC) продолжит
     * считать, что вал всё ещё вращается с прежней скоростью. */
    if (enc_error) return 0;
    return enc_speed_rpm;
}

uint8_t ENC_GetError(void) {
    return enc_error;
}

uint32_t ENC_GetPulseWidth_us(void) { return enc_pulse_us; }
uint32_t ENC_GetPeriod_us(void)     { return enc_period_us; }

/* Watchdog отсутствия импульсов — вызывается из TIM6 1 кГц ISR.
 * Переводит в ошибку, если новые захваты не приходят дольше ENC_TIMEOUT_MS. */
void ENC_Update(void) {
    static uint32_t last_count = 0xFFFFFFFFU;
    static uint32_t stale_ms = 0;
    if (enc_capture_count != last_count) {
        last_count = enc_capture_count;
        stale_ms = 0;
    } else {
        stale_ms++;
        if (stale_ms >= ENC_TIMEOUT_MS) {
            enc_error = ENC_ERR_TIMEOUT;
            enc_speed_rpm = 0;      /* не оставлять IIR-фильтр на старом значении */
            first_capture = 1;      /* переприм угла при восстановлении сигнала */
        }
    }
}

extern volatile uint32_t sys_tick_ms;  /* main.c: SysTick 1 кГц, внешняя линковка */

void ENC_Calibrate(uint32_t calib_ms) {
    /* Простая калибровка: слушаем захваты в течение calib_ms и по факту
     * min/max duty_q16 уточняем границы. Вызывающий код должен прокрутить
     * вал вручную на полный оборот за это время для корректного результата.
     * Таймирование — по реальному sys_tick_ms (SysTick 1 кГц), НЕ по
     * количеству итераций busy-loop: длительность instruction-based цикла
     * зависит от оптимизации компилятора/flash wait states/прерываний и
     * не является надёжным миллисекундным интервалом. */
    uint32_t local_min = 0xFFFFFFFFU;
    uint32_t local_max = 0;
    uint32_t last_count = enc_capture_count;

    uint32_t t_start = sys_tick_ms;
    while ((sys_tick_ms - t_start) < calib_ms) {
        if (enc_capture_count != last_count) {
            last_count = enc_capture_count;
            uint32_t period = enc_period_us;
            uint32_t pulse  = enc_pulse_us;
            if (period > 0) {
                uint32_t duty_q16 = (uint32_t)(((uint64_t)pulse << 16) / period);
                if (duty_q16 < local_min) local_min = duty_q16;
                if (duty_q16 > local_max) local_max = duty_q16;
            }
        }
    }

    if (local_min < local_max) {
        duty_min_q16 = local_min;
        duty_max_q16 = local_max;
    }
    /* Если за calib_ms не было достаточного диапазона (вал не крутили) —
     * дефолтные границы остаются в силе. */
}
