#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_capture.h"

static bool mock_interlock;
static bool mock_controls_inactive;
static bool mock_fault_latched;
static bool mock_offsets_valid;
static bool mock_pwm_start_ok;
static bool mock_snapshot_ok;
static bool mock_pattern_valid;
static uint32_t mock_snapshot_trigger_revision;
static int mock_adc_start_rc;
static unsigned adc_start_count;
static unsigned adc_stop_count;
static unsigned pwm_start_count;
static unsigned pwm_stop_count;
static unsigned latch_count;
static MapCaptureStatus last_latch;
static bool control_admission;
static uint8_t expected_sector;
static uint8_t expected_window;
static bool expected_window_valid;

int ADC_InjectedStart(void) { ++adc_start_count; return mock_adc_start_rc; }
void ADC_InjectedStop(void) { ++adc_stop_count; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{ expected_sector = sector; expected_window = window; expected_window_valid = valid; }
void ADC_SetControlAdmission(bool admitted) { control_admission = admitted; }
bool ADC_OffsetsAreValid(void) { return mock_offsets_valid; }

static bool hook_interlock(void) { return mock_interlock; }
static bool hook_controls_inactive(void) { return mock_controls_inactive; }
static bool hook_fault_latched(void) { return mock_fault_latched; }
static bool hook_validate_pattern(const MapCaptureRequest *request)
{ return mock_pattern_valid && request != 0; }
static bool hook_start_pwm(const MapCaptureRequest *request)
{ (void)request; ++pwm_start_count; return mock_pwm_start_ok; }
static void hook_stop_pwm(void) { ++pwm_stop_count; }
static bool hook_snapshot(MapCapturePwmSnapshot *out)
{
    if (!mock_snapshot_ok || out == 0) return false;
    memset(out, 0, sizeof(*out));
    out->tim1_ccr[0] = 101u;
    out->tim8_ccr[2] = 909u;
    out->tim1_arr = 999u;
    out->trigger_revision = mock_snapshot_trigger_revision;
    return true;
}
static void hook_latch(MapCaptureStatus reason) { ++latch_count; last_latch = reason; }

static MapCaptureRequest request(void)
{
    MapCaptureRequest value;
    memset(&value, 0, sizeof(value));
    value.capture_id = 42u;
    value.pulse_count = 2u;
    value.timeout_periods = 4u;
    value.max_abs_shunt_ma = 5000;
    value.min_vbus_mv = 1000u;
    value.max_vbus_mv = 30000u;
    value.sector_candidate = 3u;
    value.window_candidate = 1u;
    value.tim1_ccr[0] = 101u;
    value.tim8_ccr[2] = 909u;
    value.trigger_revision = 7u;
    return value;
}

static AdcFrame frame(AdcFrameStatus status, int32_t i1, int32_t i2, int32_t vbus)
{
    AdcFrame value;
    memset(&value, 0, sizeof(value));
    value.status = status;
    value.idc1_ma = i1;
    value.idc2_ma = i2;
        value.vbus_mv = vbus;
    value.raw_vbus = 2u;
    value.sequence = 12u;

    value.tim1_sector = 3u;
    value.sample_window = 1u;
    return value;
}

static void send_frame(AdcFrameStatus status, int32_t i1, int32_t i2, int32_t vbus)
{
    AdcFrame value = frame(status, i1, i2, vbus);
    MapCapture_OnAdcFrame(&value);
}

static void reset_mocks(void)
{
    mock_interlock = true;
    mock_controls_inactive = true;
    mock_fault_latched = false;
    mock_offsets_valid = true;
    mock_pwm_start_ok = true;
    mock_snapshot_ok = true;
    mock_pattern_valid = true;
    mock_snapshot_trigger_revision = 7u;
    mock_adc_start_rc = 0;
    adc_start_count = adc_stop_count = pwm_start_count = pwm_stop_count = latch_count = 0u;
    last_latch = MAP_CAPTURE_OK;
    control_admission = true;
    expected_sector = expected_window = 0u;
    expected_window_valid = false;
}

int main(void)
{
    MapCaptureHooks hooks = {
        hook_interlock, hook_controls_inactive, hook_fault_latched,
        hook_validate_pattern, hook_start_pwm, hook_stop_pwm, hook_snapshot, hook_latch
    };
    MapCaptureRequest req;
    MapCaptureInfo info;
    MapCaptureRecord record;
    AdcFrame bad_frame;
    uint16_t i;

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    req = request();

    mock_interlock = false;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_HW_INTERLOCK_MISSING);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    mock_interlock = true;
    mock_fault_latched = true;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_FAULT_LATCHED);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    mock_fault_latched = false;
    mock_controls_inactive = false;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_CONTROL_ACTIVE);
    assert(adc_start_count == 0u && pwm_start_count == 0u);

    mock_controls_inactive = true;
    mock_pattern_valid = false;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_BAD_REQUEST);
    assert(adc_start_count == 0u && pwm_start_count == 0u);
    mock_pattern_valid = true;
    assert(MapCapture_Arm(&req) == MAP_CAPTURE_OK);
    assert(adc_start_count == 1u && pwm_start_count == 0u);
    assert(!control_admission && !expected_window_valid);
    assert(MapCapture_Run() == MAP_CAPTURE_OK);
    assert(pwm_start_count == 1u);
    assert(expected_sector == 3u && expected_window == 1u);

    send_frame(ADC_FRAME_WINDOW_INVALID, 100, -200, 24000);
    MapCapture_GetInfo(&info);
    assert(info.state == MAP_CAPTURE_RUNNING && info.accepted_frames == 1u);
    send_frame(ADC_FRAME_WINDOW_INVALID, 300, -400, 24000);
    MapCapture_GetInfo(&info);
    assert(info.state == MAP_CAPTURE_COMPLETE && info.accepted_frames == 2u);
    assert(pwm_stop_count == 1u && adc_stop_count == 1u && !expected_window_valid);
    assert(MapCapture_PopRecord(&record));
    assert(record.capture_id == 42u && record.frame.idc1_ma == 100);
    assert(record.pwm.tim1_ccr[0] == 101u && record.pwm.tim8_ccr[2] == 909u);
    assert(MapCapture_PopRecord(&record));
    assert(!MapCapture_PopRecord(&record));

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_OVERRUN, 0, 0, 24000);
    MapCapture_GetInfo(&info);
    assert(info.state == MAP_CAPTURE_FAULTED && info.terminal_status == MAP_CAPTURE_ADC_FAULT);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_ADC_STATUS_INVALID);
    assert(info.terminal_raw_vbus == 2u && info.terminal_adc_status == ADC_FRAME_OVERRUN);
    assert(latch_count == 1u && last_latch == MAP_CAPTURE_ADC_FAULT);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_WINDOW_INVALID, 5001, 0, 24000);
    MapCapture_GetInfo(&info);
        assert(info.terminal_status == MAP_CAPTURE_LIMIT_EXCEEDED);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_I1_LIMIT);
    assert(info.terminal_idc1_ma == 5001 && info.terminal_vbus_mv == 24000);
    assert(last_latch == MAP_CAPTURE_LIMIT_EXCEEDED);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_WINDOW_INVALID, 0, -5001, 24000);
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_LIMIT_EXCEEDED);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_I2_LIMIT);
    assert(info.terminal_idc2_ma == -5001);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 999);
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_LIMIT_EXCEEDED);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW);
    assert(info.terminal_raw_vbus == 2u && info.terminal_vbus_mv == 999);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 30001);
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_LIMIT_EXCEEDED);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_VBUS_HIGH);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    send_frame(ADC_FRAME_WINDOW_INVALID, 5001, -5001, 999);
    MapCapture_GetStats(&info);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_I1_LIMIT);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    MapCapture_OnAdcFrame(0);
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_ADC_FAULT);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_ADC_FRAME_NULL);
    assert(info.terminal_raw_vbus == 0u);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    bad_frame = frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 24000);
    bad_frame.tim1_sector = 2u;
    MapCapture_OnAdcFrame(&bad_frame);
    MapCapture_GetStats(&info);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_ADC_SECTOR_MISMATCH);
    assert(info.terminal_tim1_sector == 2u);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    bad_frame = frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 24000);
    bad_frame.sample_window = 0u;
    MapCapture_OnAdcFrame(&bad_frame);
    MapCapture_GetStats(&info);
    assert(info.fault_detail == MAP_CAPTURE_FAULT_DETAIL_ADC_WINDOW_MISMATCH);
    assert(info.terminal_sample_window == 0u);

    /* A bounded session may exceed ring capacity only if foreground drains it.

     * Without drain, the first unusable slot immediately faults and stops PWM. */
    reset_mocks();
    assert(MapCapture_Init(&hooks));
    req = request();
    req.pulse_count = MAP_CAPTURE_RING_CAPACITY;
    req.timeout_periods = MAP_CAPTURE_RING_CAPACITY;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    for (i = 0u; i < MAP_CAPTURE_RING_CAPACITY; ++i) {
        send_frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 24000);
    }
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_BUFFER_OVERFLOW);
    assert(info.accepted_frames == (MAP_CAPTURE_RING_CAPACITY - 1u));
    assert(info.dropped_records == 1u && last_latch == MAP_CAPTURE_BUFFER_OVERFLOW);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    req = request();
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    mock_snapshot_trigger_revision = 8u;
    send_frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 24000);
    MapCapture_GetStats(&info);
    assert(info.terminal_status == MAP_CAPTURE_TRIGGER_MISMATCH);
    assert(last_latch == MAP_CAPTURE_TRIGGER_MISMATCH);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    req = request(); req.timeout_periods = 2u;
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    MapCapture_OnPeriod(); MapCapture_OnPeriod(); MapCapture_OnPeriod();
    MapCapture_GetInfo(&info);
    assert(info.terminal_status == MAP_CAPTURE_TIMEOUT && last_latch == MAP_CAPTURE_TIMEOUT);

    reset_mocks();
    assert(MapCapture_Init(&hooks));
    req = request();
    assert(MapCapture_Start(&req) == MAP_CAPTURE_OK);
    MapCapture_OnProtectionFault();
    MapCapture_GetInfo(&info);
    assert(info.terminal_status == MAP_CAPTURE_PROTECTION_FAULT);
    assert(latch_count == 0u); /* protection already owns its latch */

    puts("map_capture_test: PASS");
    return 0;
}
