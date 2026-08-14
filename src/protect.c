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

/* Ревью PR-01: 25А было выше лимита конфигурации (модуль 10А) и выше
 * диапазона Ires (±16.5А — проверка никогда не сработала бы). 12А —
 * выше рабочего задания (FOC_I_MAX_MA=10А), ниже края АЦП шунтов (±26.2А). */
#define PROTECT_I_MAX_MA        12000    /* ±12 А — software trip */
#define PROTECT_VBUS_MIN_MV     8000     /*  8 В */
#define PROTECT_VBUS_MAX_MV     80000    /* 80 В */
#define PROTECT_VBUS_OVERCNT    10       /* подряд 10 измерений выше порога */

static volatile int   fault = 0;
static volatile uint8_t  vbus_over_count = 0;
static volatile int   fault_reason = PROTECT_FAULT_NONE;

void PROTECT_Init(void) {
    fault = 0;
    vbus_over_count = 0;
    fault_reason = PROTECT_FAULT_NONE;
}

void PROTECT_Check(void) {
    if(fault) return;

    /* Токовая защита: фазные шунты (FOC) + остаточный ток (диагностика).
     * Проверяем все три канала — защита сработает при любой топологии. */
    int32_t i1 = ADC_GetI1_mA();
    int32_t i2 = ADC_GetI2_mA();
    int32_t in = ADC_GetIres_mA();
    /* Ревью PR-08: |x| в int64_t — -INT32_MIN переполняет int32 (UB). */
    if(i1 < 0) i1 = (int32_t)(-(int64_t)i1);
    if(i2 < 0) i2 = (int32_t)(-(int64_t)i2);
    if(in < 0) in = (int32_t)(-(int64_t)in);
    if(i1 > PROTECT_I_MAX_MA || i2 > PROTECT_I_MAX_MA || in > PROTECT_I_MAX_MA) {
        fault = 1;
        fault_reason = PROTECT_FAULT_OVERCURRENT;
        PWM_Disable();
        return;
    }

    /* Vbus: верхний порог с гистерезисом по числу отсчётов,
     * нижний — однократно (просадка критична для ключей) */
    int32_t vbus = ADC_GetVbus_mV();
    if(vbus > PROTECT_VBUS_MAX_MV) {
        if(++vbus_over_count >= PROTECT_VBUS_OVERCNT) {
            fault = 1;
            fault_reason = PROTECT_FAULT_VBUS_HIGH;
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
        fault_reason = PROTECT_FAULT_VBUS_LOW;
        PWM_Disable();
        return;
    }
}

int PROTECT_IsFault(void) { return fault; }

/* Ревью PR-07: request-clear — latch сбрасывается ТОЛЬКО если условия
 * восстановились (Vbus в окне, токи ниже половины trip-порога); иначе -1.
 * PWM остаётся выключенным — запуск только через FOC_Start/VFC. */
int PROTECT_Clear(void) {
    /* Ревью MAIN-03: свежая выборка перед сбросом latch — injected-данные
     * могут быть устаревшими после PWM_Disable (JADSTART снят, regular ADC
     * не обновляется). При работающем FOC (JADSTART активен) вызов безопасно
     * выходит (guard в ADC_StartConversion) — используем последние данные. */
    ADC_StartConversion();
    int32_t vbus = ADC_GetVbus_mV();
    int32_t i1 = ADC_GetI1_mA();
    int32_t i2 = ADC_GetI2_mA();
    int32_t in = ADC_GetIres_mA();
    if(i1 < 0) i1 = (int32_t)(-(int64_t)i1);
    if(i2 < 0) i2 = (int32_t)(-(int64_t)i2);
    if(in < 0) in = (int32_t)(-(int64_t)in);
    if(vbus < PROTECT_VBUS_MIN_MV || vbus > PROTECT_VBUS_MAX_MV) return -1;
    if(i1 > PROTECT_I_MAX_MA / 2 || i2 > PROTECT_I_MAX_MA / 2 ||
       in > PROTECT_I_MAX_MA / 2) return -1;
    fault = 0;
    vbus_over_count = 0;
    fault_reason = PROTECT_FAULT_NONE;
    return 0;
}

int PROTECT_GetFaultReason(void) { return fault_reason; }
