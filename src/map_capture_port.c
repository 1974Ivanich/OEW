#include "map_capture_port.h"

#include "map_capture_profiles.h"
#include "autotune.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "vf_control.h"

static bool cap_hardware_interlock_healthy(void)
{
    /* Must remain false until the independent FAULT_N/BKIN(BKIN2) chain is
     * implemented, electrically verified and reported by the PWM board layer. */
    return PWM_HardwareInterlockHealthy();
}

static bool cap_controls_inactive(void)
{
    return !FOC_IsRunning() && !VFC_IsRunning() && !Autotune_IsActive();
}

static bool cap_fault_latched(void)
{
    return PROTECT_IsFault() != 0;
}

static bool cap_validate_pattern(const MapCaptureRequest *request)
{
    return MapCaptureProfile_IsApproved(request) &&
           PWM_ServiceCaptureValidate(request);
}

static bool cap_start_service_pwm(const MapCaptureRequest *request)
{
    return PWM_ServiceCaptureStart(request) == PWM_ENABLE_OK;
}

static void cap_stop_service_pwm(void)
{
    /* PWM owns the EN-low -> MOE/CEN-off implementation. */
    PWM_ServiceCaptureStop();
}

static bool cap_snapshot_service_pwm(MapCapturePwmSnapshot *out)
{
    return PWM_ServiceCaptureSnapshot(out);
}

static void cap_latch_fault(MapCaptureStatus status)
{
    switch (status) {
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
        .hardware_interlock_healthy = cap_hardware_interlock_healthy,
        .control_paths_inactive = cap_controls_inactive,
        .fault_latched = cap_fault_latched,
        .validate_service_pattern = cap_validate_pattern,
        .start_service_pwm = cap_start_service_pwm,
        .stop_service_pwm = cap_stop_service_pwm,
        .snapshot_service_pwm = cap_snapshot_service_pwm,
        .latch_capture_fault = cap_latch_fault
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
