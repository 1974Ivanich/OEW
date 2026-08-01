#include "protect.h"
#include "pwm.h"
#include "adc.h"

/*
 * Базовая защита силовой части.
 *
 *   I1, I2: токи шунтов (мА)   — порог ±25А (с запасом от макс. 26.2А)
 *   Vbus:   напряжение шины (мВ) — диапазон 8..80В
 *   Vbus_overcount: число последовательных измерений выше порога —
 *                   чтобы не срабатывать от коротких импульсов.
 *
 * На время fault ШИМ принудительно выключается (MOE=0, CEN=0).
 * Сброс — командой по UART (PROTECT_Clear()).
 */

#define PROTECT_I_MAX_MA        25000    /* ±25 А */
#define PROTECT_VBUS_MIN_MV     8000     /*  8 В */
#define PROTECT_VBUS_MAX_MV     80000    /* 80 В */
#define PROTECT_VBUS_OVERCNT    10       /* подряд 10 измерений выше порога */

static int   fault = 0;
static uint8_t  vbus_over_count = 0;

void PROTECT_Init(void) {
    fault = 0;
    vbus_over_count = 0;
}

void PROTECT_Check(void) {
    if(fault) return;

    /* Токовая защита: фазные шунты (FOC) + остаточный ток (диагностика).
     * Проверяем все три канала — защита сработает при любой топологии. */
    int32_t i1 = ADC_GetI1_mA();
    int32_t i2 = ADC_GetI2_mA();
    int32_t in = ADC_GetIres_mA();
    if(i1 < 0) i1 = -i1;
    if(i2 < 0) i2 = -i2;
    if(in < 0) in = -in;
    if(i1 > PROTECT_I_MAX_MA || i2 > PROTECT_I_MAX_MA || in > PROTECT_I_MAX_MA) {
        fault = 1;
        PWM_Disable();
        return;
    }

    /* Vbus: верхний порог с гистерезисом по числу отсчётов,
     * нижний — однократно (просадка критична для ключей) */
    int32_t vbus = ADC_GetVbus_mV();
    if(vbus > PROTECT_VBUS_MAX_MV) {
        if(++vbus_over_count >= PROTECT_VBUS_OVERCNT) {
            fault = 1;
            PWM_Disable();
            return;
        }
    } else {
        vbus_over_count = 0;
    }
    if(vbus < PROTECT_VBUS_MIN_MV) {
        /* Vbus ниже минимума — просадка или пропадание питания.
         * Vbus=0 тоже аварийная ситуация: PROTECT_Check вызывается только
         * во время работы FOC (после полной инициализации АЦП). */
        fault = 1;
        PWM_Disable();
        return;
    }
}

int PROTECT_IsFault(void) { return fault; }

void PROTECT_Clear(void) {
    fault = 0;
    vbus_over_count = 0;
    /* PWM остаётся выключенным — запуск только через FOC_Start */
}
