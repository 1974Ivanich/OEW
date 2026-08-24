/* Production port for map_capture. The port is the single bridge between
 * map characterization and the board-specific PWM/ADC configuration. */
#include "map_capture.h"
#include "map_capture_profiles.h"
#include "adc.h"

#include "stm32g474xx.h"
#include "autotune.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "vf_control.h"

#ifndef PWM_OEW_BOARD_REVISION
#define PWM_OEW_BOARD_REVISION 0u
#endif

static uint32_t cap_crc32_update(uint32_t crc, uint32_t value)
{
    uint8_t byte;
    uint8_t bit;

    for (byte = 0u; byte < 4u; ++byte) {
        crc ^= (value >> (byte * 8u)) & 0xFFu;
        for (bit = 0u; bit < 8u; ++bit) {
            crc = (crc & 1u) ? ((crc >> 1u) ^ 0xEDB88320u) : (crc >> 1u);
        }
    }
    return crc;
}

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

static uint32_t cap_adc_config_signature(void)
{
    uint32_t crc = 0xFFFFFFFFu;

    /* Include the actual runtime registers, not merely compile-time nominal
     * values. This makes a sample-time/sequence/ADC-clock change invalidate a
     * previously characterized map. */
    crc = cap_crc32_update(crc, ADC12_COMMON->CCR);
    crc = cap_crc32_update(crc, ADC1->CFGR);
    crc = cap_crc32_update(crc, ADC2->CFGR);
    crc = cap_crc32_update(crc, ADC1->CFGR2);
    crc = cap_crc32_update(crc, ADC2->CFGR2);
    crc = cap_crc32_update(crc, ADC1->SMPR1);
    crc = cap_crc32_update(crc, ADC1->SMPR2);
    crc = cap_crc32_update(crc, ADC2->SMPR1);
    crc = cap_crc32_update(crc, ADC2->SMPR2);
    crc = cap_crc32_update(crc, ADC1->JSQR);
    crc = cap_crc32_update(crc, ADC2->JSQR);
    crc = cap_crc32_update(crc, ADC_VREF_MV);
    crc = cap_crc32_update(crc, ADC_MAX_CODE);
    return crc ^ 0xFFFFFFFFu;
}

static uint32_t cap_current_calibration_signature(void)
{
    uint32_t crc = 0xFFFFFFFFu;

    /* Offset values are part of the current transfer function used when the
     * characterization samples are converted to mA. Scale constants are also
     * included so an Rshunt/gain change cannot reuse an old map. */
    crc = cap_crc32_update(crc, ADC_GetOffsetI1());
    crc = cap_crc32_update(crc, ADC_GetOffsetI2());
    crc = cap_crc32_update(crc, ADC_GetOffsetIres());
    crc = cap_crc32_update(crc, ADC_DC_SHUNT_UV_PER_A);
    crc = cap_crc32_update(crc, ADC_CT_UV_PER_A);
    crc = cap_crc32_update(crc, ADC_VBUS_DIVIDER);
    crc = cap_crc32_update(crc, ADC_OFFSET_SAMPLES);
    crc = cap_crc32_update(crc, ADC_OffsetsAreValid() ? 1u : 0u);
    return crc ^ 0xFFFFFFFFu;
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
        .stop_service_pwm            = cap_stop,
        .snapshot_service_pwm        = cap_snapshot,
        .latch_capture_fault         = cap_latch
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
    out->trigger_offset_ticks = 0u;
    out->deadtime_ticks = (uint16_t)(TIM1->BDTR & 0xFFu);
    out->adc_config_signature = cap_adc_config_signature();
    out->current_calibration_signature = cap_current_calibration_signature();

    return out->board_revision != 0u && out->pwm_frequency_hz != 0u &&
           out->timer_arr != 0u && out->adc_trigger_id != 0u &&
           out->adc_config_signature != 0u &&
           out->current_calibration_signature != 0u;
}
