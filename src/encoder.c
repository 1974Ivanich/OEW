#include "stm32g474xx.h"
#include "encoder.h"
#include "foc.h"       /* FOC_IsRunning — guard калибровки (ревью ENC-04) */
#include "vf_control.h" /* VFC_IsRunning — guard калибровки (ревью ENC-04) */

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

/* Ожидаемый период AS5048A PWM ≈ 920 Гц (период ≈ 1.087 мс = 4119 тактов
 * кадра: 12 init + 4 error + 4095 data + 8 exit). PWM — 12-битный:
 * 4095 уровней data (~0.088°/шаг); 14 бит есть только в SPI/I²C — API
 * GetAngle14 возвращает масштабированное значение, не реальную точность.
 * Разумный диапазон для валидации захваченного периода — если вне этого
 * окна, считаем захват мусором (шум/наводка/отключенный энкодер). */
#define ENC_PERIOD_MIN_US    700u
#define ENC_PERIOD_MAX_US    1500u

/* Заводские дефолты диапазона duty (доля от периода, Q16) — из формулы
 * кадра AS5048A PWM (ревью ENC-02; раньше 0..65535 давало систематическое
 * смещение угла ~1.05° без калибровки):
 *   min = 12/4119·65536 ≈ 191,  max = (12+4095)/4119·65536 ≈ 65348.
 * Точные крайние значения экземпляра могут отличаться (OTP/ревизия) —
 * сверить осциллографом и/или применить ENC_Calibrate(). */
#define ENC_DUTY_MIN_Q16_DEFAULT   191u
#define ENC_DUTY_MAX_Q16_DEFAULT   65348u

/* Таймаут отсутствия новых импульсов: ENC_Update() вызывается из TIM6 1 кГц,
 * stale_ms инкрементируется раз в мс. 5 мс при сигнале ~920 Гц (период
 * 1.087 мс) ≈ 4.6 периода — корректно (ревью Bolt P2: комментарий «~3
 * периода» был неточен, исправлено). */
#define ENC_TIMEOUT_MS       5u

static volatile uint32_t enc_period_us = 0;
static volatile uint32_t enc_pulse_us  = 0;
static volatile uint32_t enc_capture_count = 0;
static volatile uint8_t  enc_error = 0;

static volatile uint16_t enc_angle14 = 0;
static volatile int32_t  enc_speed_rpm = 0;
static int32_t  enc_speed_q3 = 0;   /* IIR-аккумулятор rpm×8 (П.8: без потери
                                     * дробной части при сдвиге — убирает
                                     * мёртвую зону ±7 rpm и асимметрию около 0) */
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

    /* Ревью Bolt P2: TIM2 приоритетнее TIM6. Оба были =1 → не прерывают
     * друг друга: при длинном TIM6 (VFC_Update + ADC_StartConversion с
     * ожиданием EOC) захват TIM2 откладывался → риск CC1OF/CC2OF.
     * TIM2 (920 Гц, короткий) = 1, TIM6 (1 кГц, V/f loop) = 2 в main.c.
     * ADC (5 кГц, FOC) остаётся = 0 — самый высокий. */
    NVIC_SetPriority(TIM2_IRQn, 1);
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
        enc_speed_rpm = 0;  /* ревью arena P1: не публиковать старый rpm после ошибки */
        enc_speed_q3 = 0;
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
            enc_speed_rpm = 0;  /* ревью arena P1: иначе первый хороший кадр отдаст старый IIR */
            enc_speed_q3 = 0;
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

        /* П.8: IIR в Q3-аккумуляторе (rpm×8). Старый вариант
         * `enc_speed_rpm += (rpm - enc_speed_rpm) >> 3` имел мёртвую зону:
         * положительная ошибка <8 rpm давала инкремент 0 (залипание
         * до 7 rpm ниже цели), отрицательная — всегда ≥1 (арифм.
         * сдвиг к −∞) — асимметрия около нуля. В Q3 остаток ≤ 1/8 rpm. */
        enc_speed_q3 += ((rpm << 3) - enc_speed_q3) >> ENC_FILTER_SHIFT;
        /* Публикация с округлением к ближайшему (симметрично для ±) */
        enc_speed_rpm = (enc_speed_q3 + ((enc_speed_q3 >= 0) ? 4 : -4)) / 8;
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
            /* Ревью ENC-05: перечитать счётчик ПЕРЕД публикацией timeout —
             * TIM2 ISR (prio 1) может вклиниться между проверкой выше и этим
             * местом; иначе при живом сигнале возможен ложный
             * ENC_ERR_TIMEOUT на один тик (нулевая скорость на ~1 мс). */
            uint32_t count_now = enc_capture_count;
            if (count_now == last_count) {
                enc_error = ENC_ERR_TIMEOUT;
                enc_speed_rpm = 0;  /* не оставлять IIR-фильтр на старом значении */
                enc_speed_q3 = 0;
                first_capture = 1;  /* переприм угла при восстановлении сигнала */
            } else {
                last_count = count_now;
                stale_ms = 0;
            }
        }
    }
}

extern volatile uint32_t sys_tick_ms;  /* main.c: SysTick 1 кГц, внешняя линковка */

int8_t ENC_Calibrate(uint32_t calib_ms) {
    /* Ревью ENC-04: калибровка разрешена только при остановленном приводе —
     * при работающем FOC/V-f commit новых границ дал бы скачок угла
     * (фазовый скачок position-feedback) и исказил бы статистику шумом. */
    if (FOC_IsRunning() || VFC_IsRunning()) {
        return -1;   /* ENC_CAL_DRIVE_ACTIVE */
    }
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
            /* П.5: защита от разорванной пары period/pulse. TIM2 ISR (prio 1)
             * пишет enc_period_us и enc_pulse_us РАЗДЕЛЬНЫМИ записями, затем
             * инкрементирует enc_capture_count. Если ISR сработал между
             * нашими двумя чтениями — получили бы period[n]+pulse[n+1]
             * (ложный duty). Seqlock: перечитываем счётчик ПОСЛЕ чтения
             * пары — если изменился, выборка отбрасывается (следующая
             * итерация прочтёт свежую консистентную пару). volatile
             * гарантирует порядок чтений. */
            uint32_t period = enc_period_us;
            uint32_t pulse  = enc_pulse_us;
            if (enc_capture_count != last_count) continue;  /* пара разорвана ISR */
            if (period > 0) {
                uint32_t duty_q16 = (uint32_t)(((uint64_t)pulse << 16) / period);
                if (duty_q16 < local_min) local_min = duty_q16;
                if (duty_q16 > local_max) local_max = duty_q16;
            }
        }
    }

    /* Ревью Bolt P1: принимать калибровку только при ДОСТАТОЧНОМ покрытии
     * диапазона duty. Если вал прошёл лишь часть оборота, local_min/max
     * описывают только этот участок → полный угол растянется на неполный
     * диапазон. Требуем span ≥ 90% полной шкалы (0..65535 Q16); иначе
     * сохраняем предыдущие границы (дефолт 0..65535). */
    if (local_min < local_max &&
        ((uint64_t)local_max - local_min) >= (65535u * 9u / 10u)) {
        duty_min_q16 = local_min;
        duty_max_q16 = local_max;
        return 0;    /* ENC_CAL_OK */
    }
    /* Если за calib_ms не было достаточного диапазона (вал не крутили или
     * оборот неполный) — предыдущие границы остаются в силе. */
    return -2;       /* ENC_CAL_INSUFFICIENT_SPAN */
}
