/* Production port для map_capture (пакет OEW Service-Only Map Capture).
 * Единственный путь к силовым/защитным API; никаких прямых регистров. */
#include "map_capture.h"

#include "autotune.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "vf_control.h"

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
    return PWM_ServiceCaptureValidate(request);
}

static bool cap_start(const MapCaptureRequest *request)
{
    return PWM_ServiceCaptureStart(request);
}

static void cap_stop(void)
{
    PWM_ServiceCaptureStop();
}

static bool cap_snapshot(MapCapturePwmSnapshot *out)
{
    return PWM_ServiceCaptureSnapshot(out);
}

static void cap_latch(MapCaptureStatus reason)
{
    PROTECT_LatchCaptureFault((int)reason);
}

static const MapCaptureHooks g_cap_hooks = {
    .hardware_interlock_healthy = PWM_HardwareInterlockHealthy,
    .control_paths_inactive     = cap_controls_inactive,
    .fault_latched              = cap_fault_latched,
    .validate_service_pattern   = cap_validate,
    .start_service_pwm          = cap_start,
    .stop_service_pwm           = cap_stop,
    .snapshot_service_pwm       = cap_snapshot,
    .latch_capture_fault        = cap_latch
};

int MapCapturePort_Init(void)
{
    return MapCapture_Init(&g_cap_hooks) ? 0 : -1;
}
