#include "dac_debug.h"

#include "stm32g474xx.h"

#include "adc.h"
#include "foc.h"

/* PA4 = DAC1_OUT1, PA5 = DAC1_OUT2 (RM0440, таблица Alternate function 11). */
#define DAC_DEBUG_MODER_PA4    (3u << (4u * 2u))   /* MODER4 = 11: analog */
#define DAC_DEBUG_MODER_PA5    (3u << (5u * 2u))   /* MODER5 = 11: analog */

static DacSignal g_ch1 = DAC_SIG_OFF;
static DacSignal g_ch2 = DAC_SIG_OFF;

/* Чистые функции вынесены в dac_debug_math.c (тестируются без железа). */

void DacDebug_Init(void)
{
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_DAC1EN;
    (void)RCC->AHB2ENR;

    /* PA4/PA5 -> analog mode (MODER = 11), без pull-up (PUPDR = 00). */
    GPIOA->MODER = (GPIOA->MODER & ~(DAC_DEBUG_MODER_PA4 | DAC_DEBUG_MODER_PA5)) |
                   DAC_DEBUG_MODER_PA4 | DAC_DEBUG_MODER_PA5;

    /* Без триггера (TENx = 0): запись в DHR сразу обновляет выход. */
    DAC1->CR = DAC_CR_EN1 | DAC_CR_EN2;
    DAC1->DHR12RD = 0u;
}

void DacDebug_SetSignals(DacSignal ch1, DacSignal ch2)
{
    g_ch1 = (ch1 < DAC_SIG_COUNT) ? ch1 : DAC_SIG_OFF;
    g_ch2 = (ch2 < DAC_SIG_COUNT) ? ch2 : DAC_SIG_OFF;
    if (g_ch1 == DAC_SIG_OFF && g_ch2 == DAC_SIG_OFF) {
        DAC1->DHR12RD = 0u;
    }
}

void DacDebug_GetSignals(DacSignal *ch1, DacSignal *ch2)
{
    if (ch1 != 0) { *ch1 = g_ch1; }
    if (ch2 != 0) { *ch2 = g_ch2; }
}

uint8_t DacDebug_IsActive(void)
{
    return (g_ch1 != DAC_SIG_OFF || g_ch2 != DAC_SIG_OFF) ? 1u : 0u;
}

static int32_t signal_value(DacSignal sig)
{
    switch (sig) {
        case DAC_SIG_THETA: return FOC_GetThetaMilliRad();
        case DAC_SIG_ID:    return FOC_GetIdMeasured_mA();
        case DAC_SIG_IQ:    return FOC_GetIqMeasured_mA();
        case DAC_SIG_VBUS:  return ADC_GetVbus_mV();
        case DAC_SIG_SPEED: return FOC_GetMeasSpeedRPM();
        case DAC_SIG_OFF:
        case DAC_SIG_COUNT:
        default:            return 0;
    }
}

void DacDebug_Update(void)
{
    if (g_ch1 == DAC_SIG_OFF && g_ch2 == DAC_SIG_OFF) {
        return;
    }
    const uint32_t v1 = DacDebug_Scale(g_ch1, signal_value(g_ch1));
    const uint32_t v2 = DacDebug_Scale(g_ch2, signal_value(g_ch2));
    DAC1->DHR12RD = (v1 & 0x0FFFu) | ((v2 & 0x0FFFu) << 16);
}
