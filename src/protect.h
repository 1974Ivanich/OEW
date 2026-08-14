#ifndef PROTECT_H
#define PROTECT_H

#include <stdint.h>

typedef enum {
    PROTECT_FAULT_NONE = 0,
    PROTECT_FAULT_OVERCURRENT,
    PROTECT_FAULT_VBUS_HIGH,
    PROTECT_FAULT_VBUS_LOW
} ProtectFaultReason;

/* Ревью «План блокеров»: детальный статус request-clear (команда 'f').
 * Clear НЕ поднимает PWM/EN — запуск только явной командой после сброса. */
typedef enum {
    PROTECT_CLEAR_OK = 0,          /* latch снят (PWM остаётся выключенным) */
    PROTECT_CLEAR_NOT_LATCHED,     /* fault не был активен */
    PROTECT_CLEAR_CONTROL_ACTIVE,  /* FOC/V-f/autotune работают — нельзя снимать */
    PROTECT_CLEAR_SAMPLE_INVALID,  /* свежая выборка недоступна (ADC сбой/таймаут) */
    PROTECT_CLEAR_VALUES_UNSAFE    /* Vbus/токи вне recovery-окна */
} ProtectClearStatus;

void PROTECT_Init(void);
void PROTECT_Check(void);
int PROTECT_IsFault(void);
int PROTECT_GetFaultReason(void);
ProtectClearStatus PROTECT_RequestClear(void);  /* свежая выборка + recovery-окна */

#endif
