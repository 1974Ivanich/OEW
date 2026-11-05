#ifndef DAC_DEBUG_H
#define DAC_DEBUG_H

#include <stdint.h>

/* Диагностический вывод внутренних сигналов FOC/V-f на два канала DAC1
 * (PA4 = DAC1_OUT1, PA5 = DAC1_OUT2). Не участвует в защите и управлении:
 * только наблюдение осциллографом в реальном времени.
 * Масштабирование — чистая функция DacDebug_Scale(), проверяется hosted-тестом. */

typedef enum {
    DAC_SIG_OFF = 0,
    DAC_SIG_THETA,   /* эл. угол 0..6283 мрад -> 0..4095 */
    DAC_SIG_ID,      /* Id, мА, ±DAC_DEBUG_I_LIMIT_MA -> 0..4095, 2048 = 0 мА */
    DAC_SIG_IQ,      /* Iq, мА, то же */
    DAC_SIG_VBUS,    /* VBUS, мВ, 0..DAC_DEBUG_VBUS_LIMIT_MV -> 0..4095 */
    DAC_SIG_SPEED,   /* мех. скорость, об/мин, ±DAC_DEBUG_SPEED_LIMIT_RPM */
    DAC_SIG_COUNT
} DacSignal;

#define DAC_DEBUG_THETA_MAX_MRAD      6283
#define DAC_DEBUG_I_LIMIT_MA          10000
#define DAC_DEBUG_VBUS_LIMIT_MV       60000
#define DAC_DEBUG_SPEED_LIMIT_RPM     3000
#define DAC_DEBUG_FULL_SCALE          4095

void DacDebug_Init(void);
void DacDebug_SetSignals(DacSignal ch1, DacSignal ch2);
void DacDebug_GetSignals(DacSignal *ch1, DacSignal *ch2);
uint8_t DacDebug_IsActive(void);
/* Вызывается из ISR (5 кГц). Если оба канала OFF — сразу выходит. */
void DacDebug_Update(void);

/* Чистые функции (тестируются без железа). */
uint16_t DacDebug_Scale(DacSignal sig, int32_t value);
const char *DacDebug_SignalName(DacSignal sig);
int DacDebug_SignalFromName(const char *name, DacSignal *out);

#endif
