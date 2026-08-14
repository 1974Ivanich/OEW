#ifndef PROTECT_H
#define PROTECT_H

#include <stdint.h>
#include "adc.h"   /* AdcFrame, AdcFrameStatus — frame-aware protection */

typedef enum {
    PROTECT_FAULT_NONE = 0,
    PROTECT_FAULT_OVERCURRENT,
    PROTECT_FAULT_VBUS_HIGH,
    PROTECT_FAULT_VBUS_LOW,
    PROTECT_FAULT_ADC_OVERRUN,
    PROTECT_FAULT_ADC_QUEUE_OVERRUN,
    PROTECT_FAULT_ADC_DESYNC,
    PROTECT_FAULT_ADC_TIMEOUT,
    PROTECT_FAULT_SAMPLE_WINDOW,
    PROTECT_FAULT_CURRENT_MAP,
    PROTECT_FAULT_FRAME_COPY,
    PROTECT_FAULT_CAPTURE_TIMEOUT,
    PROTECT_FAULT_CAPTURE_BUFFER_OVERFLOW,
    PROTECT_FAULT_CAPTURE_LIMIT,
    PROTECT_FAULT_CAPTURE_ABORT,
    PROTECT_FAULT_CAPTURE_ADC,
    PROTECT_FAULT_CAPTURE_TRIGGER,
    PROTECT_FAULT_CAPTURE_INTERLOCK
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
void PROTECT_Check(void);                 /* совместимость/сервис — normal FOC зовёт CheckFrame */
void PROTECT_CheckFrame(const AdcFrame *frame);   /* единый путь: статус фрейма → latch → PWM_Disable */
void PROTECT_LatchFrameCopyFailure(void);
int PROTECT_IsFault(void);
int PROTECT_GetFaultReason(void);
ProtectClearStatus PROTECT_RequestClear(void);  /* свежая выборка + recovery-окна */

/* Capture-specific protection (map capture session): latch'ит ТОЛЬКО
 * hardware/data-quality сбои фрейма (OVR/JQOVF/desync/timeout/saturation).
 * MAPPING_UNVERIFIED и VALID — НЕ ошибки для diagnostic capture: окно на
 * первом съёме как раз и доказывается. Лимиты сессии (токи/Vbus/таймаут/
 * overflow) латчит вызывающий через PROTECT_LatchFault(). */
void PROTECT_CheckCaptureFrame(const AdcFrame *frame);
void PROTECT_LatchFault(ProtectFaultReason reason);

#endif
