/* Production port для map_capture (пакет OEW Service-Only Map Capture),
 * адаптированный под OEW-HS-1 PWM replacement API (PwmServiceCapturePattern,
 * PWM_ServiceCaptureStart, центральный PWM_Disable). Единственный путь
 * к силовым/защитным API; прямых регистровых ЗАПИСЕЙ нет (чтение CCR/ARR/
 * BDTR для снапшота — допустимо). */
#include "map_capture.h"
#include "map_capture_profiles.h"

#include "stm32g474xx.h"   /* чтение CCR/ARR/BDTR для снапшота */
#include "autotune.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "vf_control.h"

#ifndef PWM_OEW_BOARD_REVISION
#define PWM_OEW_BOARD_REVISION 0u
#endif

static uint32_t cap_pwm_frequency_hz(void)
{
    uint32_t psc;
    uint32_t tclk;
    uint32_t arr;
    uint64_t denominator;

    PWM_GetSysInfo(&psc, &tclk);
    arr = PWM_GetARR();
    denominator = 2ULL * (uint64_t)(psc + 1u) * (uint64_t)(arr + 1u);
    if (denominator == 0u) return 0u;
    return (uint32_t)(((uint64_t)tclk + denominator / 2u) / denominator);
}

static bool cap_controls_inactive(void)
{
    return !FOC_IsRunning() && !VFC_IsRunning() && !Autotune_IsActive();
}

static bool cap_fault_latched(void)
{
    return PROTECT_IsFault() != 0;
}

static bool cap_validate(const MapCaptureRequest *request)
{
    /* Compiled profile gate — единственная авторизация паттерна. Детальная
     * валидация CCR/trigger/revision выполняется внутри PWM_ServiceCaptureStart
     * (возвращает PWM_ENABLE_SERVICE_PATTERN_INVALID при несовпадении). */
    return MapCaptureProfile_IsApproved(request);
}

static bool cap_start(const MapCaptureRequest *request)
{
    PwmServiceCapturePattern pattern;

    if (request == 0) return false;
    pattern.tim1_ccr[0] = request->tim1_ccr[0];
    pattern.tim1_ccr[1] = request->tim1_ccr[1];
    pattern.tim1_ccr[2] = request->tim1_ccr[2];
    pattern.tim8_ccr[0] = request->tim8_ccr[0];
    pattern.tim8_ccr[1] = request->tim8_ccr[1];
    pattern.tim8_ccr[2] = request->tim8_ccr[2];
    pattern.sector_candidate = request->sector_candidate;
    pattern.window_candidate = request->window_candidate;
    pattern.trigger_revision = request->trigger_revision;
    return PWM_ServiceCaptureStart(&pattern) == PWM_ENABLE_OK;
}

static void cap_stop(void)
{
    /* OEW-HS-1 центральный stop: ARM_REQ первым → CEN/MOE/CCER → ADC stop. */
    PWM_Disable();
}

static bool cap_snapshot(MapCapturePwmSnapshot *out)
{
    if (out == 0) return false;
    out->tim1_ccr[0] = TIM1->CCR1;
    out->tim1_ccr[1] = TIM1->CCR2;
    out->tim1_ccr[2] = TIM1->CCR3;
    out->tim8_ccr[0] = TIM8->CCR1;
    out->tim8_ccr[1] = TIM8->CCR2;
    out->tim8_ccr[2] = TIM8->CCR3;
    out->tim1_arr = TIM1->ARR;
    out->trigger_offset_ticks = 0u;
    out->deadtime_ticks = (uint16_t)(TIM1->BDTR & 0xFFu);
    out->pwm_frequency_hz = cap_pwm_frequency_hz();
    out->trigger_revision = PWM_OEW_ADC_TRIGGER_REVISION;
    return true;
}

static void cap_latch(MapCaptureStatus reason)
{
    switch (reason) {
        case MAP_CAPTURE_TIMEOUT:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_TIMEOUT);
            break;
        case MAP_CAPTURE_BUFFER_OVERFLOW:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_BUFFER_OVERFLOW);
            break;
        case MAP_CAPTURE_LIMIT_EXCEEDED:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_LIMIT);
            break;
        case MAP_CAPTURE_ABORTED_BY_USER:
            /* Abort is a controlled stop, never a protection fault. */
            break;
        case MAP_CAPTURE_HW_INTERLOCK_MISSING:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_INTERLOCK);
            break;
        case MAP_CAPTURE_TRIGGER_MISMATCH:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_TRIGGER);
            break;
        default:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_ADC);
            break;
    }
}

bool MapCapturePort_Init(void)
{
    static const MapCaptureHooks hooks = {
        .hardware_interlock_healthy = PWM_HardwareInterlockHealthy,
        .control_paths_inactive     = cap_controls_inactive,
        .fault_latched              = cap_fault_latched,
        .validate_service_pattern   = cap_validate,
        .start_service_pwm          = cap_start,
        .stop_service_pwm           = cap_stop,
        .snapshot_service_pwm       = cap_snapshot,
        .latch_capture_fault        = cap_latch
    };

    return MapCapture_Init(&hooks);
}

void MapCapturePort_OnPwmPeriod(void)
{
    if (MapCapture_IsActive()) MapCapture_OnPeriod();
}

void MapCapturePort_OnProtectionLatched(void)
{
    MapCapture_OnProtectionFault();
}

bool MapCapturePort_GetMapIdentity(OewMapIdentity *out)
{
    if (out == 0) return false;
    out->board_revision = PWM_OEW_BOARD_REVISION;
    out->pwm_frequency_hz = cap_pwm_frequency_hz();
    out->timer_arr = PWM_GetARR();
    out->adc_trigger_id = PWM_OEW_ADC_TRIGGER_REVISION;
    return out->board_revision != 0u && out->pwm_frequency_hz != 0u &&
           out->timer_arr != 0u && out->adc_trigger_id != 0u;
}
