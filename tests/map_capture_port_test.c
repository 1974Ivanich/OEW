#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_capture_port.h"
#include "map_capture_profiles.h"
#include "protect.h"
#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
RCC_TypeDef host_rcc;

static bool hw_interlock;
static bool foc_running;
static bool vf_running;
static bool autotune_active;
static bool protect_fault;
static bool profile_approved;
static bool pwm_pattern_valid;
static bool pwm_start_ok;
static bool offsets_valid;
static unsigned adc_start_count;
static unsigned adc_stop_count;
static unsigned pwm_start_count;
static unsigned pwm_stop_count;
static ProtectFaultReason latched_reason;
static MapCaptureRequest active_request;

int ADC_InjectedStart(void) { ++adc_start_count; return 0; }
void ADC_InjectedStop(void) { ++adc_stop_count; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{ (void)sector; (void)window; (void)valid; }
void ADC_SetControlAdmission(bool admitted) { (void)admitted; }
bool ADC_OffsetsAreValid(void) { return offsets_valid; }
uint16_t ADC_GetOffsetI1(void) { return 2048u; }
uint16_t ADC_GetOffsetI2(void) { return 2048u; }
uint16_t ADC_GetOffsetIres(void) { return 0u; }

bool PWM_HardwareInterlockHealthy(void) { return hw_interlock; }
bool PWM_BreakFaultActive(void) { return false; }
int PWM_ServiceCaptureStart(const PwmServiceCapturePattern *pattern)
{
    if (pattern != 0) {
        active_request.sector_candidate = pattern->sector_candidate;
        active_request.window_candidate = pattern->window_candidate;
        active_request.tim1_ccr[0] = pattern->tim1_ccr[0];
        active_request.tim1_ccr[1] = pattern->tim1_ccr[1];
        active_request.tim1_ccr[2] = pattern->tim1_ccr[2];
        active_request.tim8_ccr[0] = pattern->tim8_ccr[0];
        active_request.tim8_ccr[1] = pattern->tim8_ccr[1];
        active_request.tim8_ccr[2] = pattern->tim8_ccr[2];
        active_request.trigger_revision = pattern->trigger_revision;
        host_tim1.CCR1 = pattern->tim1_ccr[0];
        host_tim1.CCR2 = pattern->tim1_ccr[1];
        host_tim1.CCR3 = pattern->tim1_ccr[2];
        host_tim8.CCR1 = pattern->tim8_ccr[0];
        host_tim8.CCR2 = pattern->tim8_ccr[1];
        host_tim8.CCR3 = pattern->tim8_ccr[2];
        host_tim1.ARR = 5000u;
        host_tim8.ARR = 5000u;
    }
    ++pwm_start_count;
    return pwm_start_ok ? PWM_ENABLE_OK : PWM_ENABLE_INTERLOCK_OPEN;
}
void PWM_Disable(void) { ++pwm_stop_count; }
bool PWM_SafetyOkIsHigh(void) { return true; }
bool PWM_BreakInputsAreHigh(void) { return true; }
void PWM_HeartbeatToggle(void) { }
uint16_t PWM_GetARR(void) { return (uint16_t)host_tim1.ARR; }
void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk)
{
    if (psc != 0) *psc = 0u;
    if (tclk != 0) *tclk = 50000000u;
}

bool FOC_IsRunning(void) { return foc_running; }
bool VFC_IsRunning(void) { return vf_running; }
bool Autotune_IsActive(void) { return autotune_active; }
bool PROTECT_IsFault(void) { return protect_fault; }
void PROTECT_LatchFault(ProtectFaultReason reason)
{ protect_fault = true; latched_reason = reason; }
bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{ return profile_approved && request != 0; }

static void reset(void)
{
    hw_interlock = false;
    foc_running = vf_running = autotune_active = protect_fault = false;
    profile_approved = pwm_pattern_valid = pwm_start_ok = offsets_valid = true;
    adc_start_count = adc_stop_count = pwm_start_count = pwm_stop_count = 0u;
    latched_reason = PROTECT_FAULT_CAPTURE_ADC;
    memset(&active_request, 0, sizeof(active_request));
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
}

static MapCaptureRequest request(void)
{
    MapCaptureRequest value;
    memset(&value, 0, sizeof(value));
    value.capture_id = 1u;
    value.pulse_count = 1u;
    value.timeout_periods = 2u;
    value.max_abs_shunt_ma = 1000;
    value.min_vbus_mv = 10000u;
    value.max_vbus_mv = 100000u;
    value.sector_candidate = 2u;
    value.window_candidate = 0u;
    value.tim1_ccr[0] = 200u;
    value.tim8_ccr[0] = 200u;
    value.trigger_revision = PWM_OEW_ADC_TRIGGER_REVISION;
    return value;
}

static void send_good_frame(void)
{
    AdcFrame frame;
    memset(&frame, 0, sizeof(frame));
    frame.status = ADC_FRAME_WINDOW_INVALID;
    frame.tim1_sector = active_request.sector_candidate;
    frame.sample_window = active_request.window_candidate;
    frame.vbus_mv = 50000;
    MapCapture_OnAdcFrame(&frame);
}

int main(void)
{
    MapCaptureRequest req;

    reset();
    assert(MapCapturePort_Init());
    req = request();

    assert(MapCapture_Arm(&req) == MAP_CAPTURE_HW_INTERLOCK_MISSING);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    hw_interlock = true;
    foc_running = true;
    assert(MapCapture_Arm(&req) == MAP_CAPTURE_CONTROL_ACTIVE);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    foc_running = false;
    profile_approved = false;
    assert(MapCapture_Arm(&req) == MAP_CAPTURE_BAD_REQUEST);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    profile_approved = true;
    assert(MapCapture_Arm(&req) == MAP_CAPTURE_OK);
    assert(adc_start_count == 1u && pwm_start_count == 0u);
    assert(MapCapture_Run() == MAP_CAPTURE_OK);
    assert(pwm_start_count == 1u);
    send_good_frame();
    assert(MapCapture_GetStatus() == MAP_CAPTURE_OK);
    assert(pwm_stop_count == 1u && adc_stop_count == 1u);

    reset();
    assert(MapCapturePort_Init());
    req = request();
    hw_interlock = true;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    MapCapture_OnPeriod(); MapCapture_OnPeriod(); MapCapture_OnPeriod();
    assert(MapCapture_GetStatus() == MAP_CAPTURE_TIMEOUT);
    assert(latched_reason == PROTECT_FAULT_CAPTURE_TIMEOUT);

    reset();
    assert(MapCapturePort_Init());
    req = request();
    hw_interlock = true;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    protect_fault = true;
    MapCapturePort_OnPwmPeriod();
    assert(MapCapture_GetStatus() == MAP_CAPTURE_PROTECTION_FAULT);
    assert(pwm_stop_count == 1u && adc_stop_count == 1u);

    puts("map_capture_port_test: PASS");
    return 0;
}
