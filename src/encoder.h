#ifndef ENCODER_H
#define ENCODER_H

#include <stdint.h>

/* AS5048A PWM-output driver (SPI-режим удалён — используется только
 * однопроводной PWM-выход датчика).
 *
 * Физика сигнала (AMS AS5048A, "PWM output"):
 *   - Один провод (OUT/PWM), скважность (duty) линейно пропорциональна углу.
 *   - Частота PWM ≈ 920 Гц, период ≈ 1.087 мс.
 *   - duty = angle/16384 (0° → 0%, 360° → ~100%, без офсета на краях).
 *
 * ВНИМАНИЕ: точные крайние значения могут отличаться в зависимости от
 * ревизии чипа/OTP-настроек. Используйте ENC_Calibrate() для калибровки
 * под конкретный экземпляр — прокрутить вал на полный оборот вручную
 * во время вызова.
 *
 * Аппаратная реализация — TIM2 PWM Input Capture Mode (RM0440 §29):
 * один физический пин (PA15, TIM2_CH1, AF1), IC1 (rising, прямой TI1)
 * сбрасывает счётчик и захватывает ПЕРИОД в CCR1, IC2 (falling, косвенный
 * TI1) захватывает ДЛИТЕЛЬНОСТЬ ИМПУЛЬСА в CCR2. PSC → 1 МГц (1 тик = 1 мкс).
 *
 * Пин: PA15 (TIM2_CH1, AF1). GND — общий с MCU. */

/* Error flag bits returned by ENC_GetError() */
#define ENC_ERR_TIMEOUT     0x01u   /* нет новых импульсов дольше ожидаемого периода */
#define ENC_ERR_BAD_PERIOD  0x02u   /* захваченный период вне разумного диапазона */

void     ENC_Init(void);              /* TIM2 CH1/CH2 PWM input capture (PA15) */
uint16_t ENC_GetAngle14(void);        /* 0..16383 (14-bit), 0xFFFF при ошибке */
int32_t  ENC_GetSpeed_rpm(void);      /* механическая скорость, об/мин (signed) */
int32_t  ENC_GetAngle_deg(void);      /* 0..360, -1 при ошибке (см. ENC_GetError) */
void     ENC_Update(void);            /* TIM6 ISR (1 кГц): проверка таймаута (захват — по TIM2 IRQ) */
uint8_t  ENC_GetError(void);          /* битовая маска ENC_ERR_* */
uint32_t ENC_GetPulseWidth_us(void);
uint32_t ENC_GetPeriod_us(void);

/* Самокалибровка диапазона duty. Вызывать, вручную прокручивая вал на
 * полный оборот в течение calib_ms миллисекунд — обновляет внутренние
 * min/max duty по факту принятых импульсов. */
void     ENC_Calibrate(uint32_t calib_ms);

#endif
