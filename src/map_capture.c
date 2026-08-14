#include "map_capture.h"

#include <limits.h>
#include <string.h>

/* Single ADC-ISR producer / single foreground consumer ring. The compiler
 * barrier prevents publishing an index before its complete record payload. */
#define MAP_CAPTURE_BARRIER() __asm volatile ("" ::: "memory")

static MapCaptureHooks g_hooks;
static MapCaptureRequest g_request;
static MapCaptureRecord g_ring[MAP_CAPTURE_RING_CAPACITY];
static volatile uint16_t g_write_index;
static volatile uint16_t g_read_index;
static volatile uint16_t g_accepted_frames;
static volatile uint16_t g_periods_elapsed;
static volatile uint16_t g_dropped_records;
static volatile MapCaptureState g_state = MAP_CAPTURE_IDLE;
static volatile MapCaptureStatus g_terminal_status = MAP_CAPTURE_OK;
static uint8_t g_initialized;

static uint16_t ring_next(uint16_t index)
{
    ++index;
    return (index == MAP_CAPTURE_RING_CAPACITY) ? 0u : index;
}

static bool abs_exceeds(int32_t value, int32_t limit)
{
    if (value == INT32_MIN) return true;
    return (value < 0 ? -value : value) > limit;
}

static bool request_is_sane(const MapCaptureRequest *request)
{
    if (request == 0 || request->capture_id == 0u ||
        request->pulse_count == 0u ||
        request->pulse_count > MAP_CAPTURE_MAX_PULSES ||
        request->timeout_periods == 0u ||
        request->timeout_periods < request->pulse_count ||
        request->max_abs_shunt_ma <= 0 ||
        request->min_vbus_mv == 0u ||
        request->min_vbus_mv > request->max_vbus_mv ||
        request->sector_candidate >= 6u || request->window_candidate >= 2u) {
        return false;
    }
    return true;
}

static void reset_ring(void)
{
    g_write_index = 0u;
    g_read_index = 0u;
    g_accepted_frames = 0u;
    g_periods_elapsed = 0u;
    g_dropped_records = 0u;
    memset(g_ring, 0, sizeof(g_ring));
}

static void terminal_stop(MapCaptureStatus status, MapCaptureState state,
                          bool latch_fault)
{
    /* stop_service_pwm is the sole port-provided owner of the physical
     * EN-low → MOE/CEN-off sequence. Never duplicate direct GPIO/TIM writes. */
    if (g_hooks.stop_service_pwm != 0) g_hooks.stop_service_pwm();
    ADC_InjectedStop();
    ADC_SetExpectedWindow(0u, 0u, false);
    ADC_SetControlAdmission(false);

    g_terminal_status = status;
    g_state = state;
    if (latch_fault && g_hooks.latch_capture_fault != 0) {
        g_hooks.latch_capture_fault(status);
    }
}

static bool snapshot_matches_request(const MapCapturePwmSnapshot *snapshot)
{
    uint32_t i;

    if (snapshot == 0 || snapshot->trigger_revision != g_request.trigger_revision) {
        return false;
    }
    for (i = 0u; i < 3u; ++i) {
        if (snapshot->tim1_ccr[i] != g_request.tim1_ccr[i] ||
            snapshot->tim8_ccr[i] != g_request.tim8_ccr[i]) {
            return false;
        }
    }
    return true;
}

static bool frame_is_capture_usable(const AdcFrame *frame)
{
    /* An unmeasured sector/window stays invalid for normal reconstruction.
     * This service path records only this explicit status and never invokes
     * ADC_FrameIsControlValid(), FOC_RunFrame(), or PROTECT_CheckFrame(). */
    return frame != 0 && frame->status == ADC_FRAME_WINDOW_INVALID &&
           frame->tim1_sector == g_request.sector_candidate &&
           frame->sample_window == g_request.window_candidate;
}

bool MapCapture_Init(const MapCaptureHooks *hooks)
{
    if (hooks == 0 || hooks->hardware_interlock_healthy == 0 ||
        hooks->control_paths_inactive == 0 || hooks->fault_latched == 0 ||
        hooks->validate_service_pattern == 0 || hooks->start_service_pwm == 0 ||
        hooks->stop_service_pwm == 0 ||
        hooks->snapshot_service_pwm == 0 || hooks->latch_capture_fault == 0 ||
        g_state == MAP_CAPTURE_ARMED || g_state == MAP_CAPTURE_RUNNING) {
        return false;
    }

    memset(&g_hooks, 0, sizeof(g_hooks));
    memcpy(&g_hooks, hooks, sizeof(g_hooks));
    memset(&g_request, 0, sizeof(g_request));
    reset_ring();
    g_terminal_status = MAP_CAPTURE_OK;
    g_state = MAP_CAPTURE_IDLE;
    g_initialized = 1u;
    return true;
}

MapCaptureStatus MapCapture_Arm(const MapCaptureRequest *request)
{
    if (!g_initialized) return MAP_CAPTURE_HOOKS_INVALID;
    if (g_state == MAP_CAPTURE_ARMED || g_state == MAP_CAPTURE_RUNNING) {
        return MAP_CAPTURE_CONTROL_ACTIVE;
    }
    if (!request_is_sane(request)) return MAP_CAPTURE_BAD_REQUEST;
    if (!g_hooks.hardware_interlock_healthy()) {
        return MAP_CAPTURE_HW_INTERLOCK_MISSING;
    }
    if (!g_hooks.control_paths_inactive()) return MAP_CAPTURE_CONTROL_ACTIVE;
    if (g_hooks.fault_latched()) return MAP_CAPTURE_FAULT_LATCHED;
    if (!ADC_OffsetsAreValid()) return MAP_CAPTURE_OFFSET_INVALID;
    if (!g_hooks.validate_service_pattern(request)) return MAP_CAPTURE_BAD_REQUEST;

    /* A previous record set cannot mix with a new capture id. Normal control
     * admission remains false for the whole diagnostic session. */
    reset_ring();
    g_request = *request;
    g_terminal_status = MAP_CAPTURE_OK;
    g_state = MAP_CAPTURE_ARMED;
    ADC_SetControlAdmission(false);
    /* Do not claim a valid reconstruction window before the map exists. The
     * ADC publishes WINDOW_INVALID, accepted only by this service session and
     * still rejected by normal FOC/protection control logic. */
    ADC_SetExpectedWindow(request->sector_candidate, request->window_candidate, false);

    if (ADC_InjectedStart() != 0) {
        terminal_stop(MAP_CAPTURE_ADC_ARM_FAILED, MAP_CAPTURE_FAULTED, true);
        return MAP_CAPTURE_ADC_ARM_FAILED;
    }
    return MAP_CAPTURE_OK;
}

MapCaptureStatus MapCapture_Run(void)
{
    if (!g_initialized) return MAP_CAPTURE_HOOKS_INVALID;
    if (g_state != MAP_CAPTURE_ARMED) return MAP_CAPTURE_NOT_ACTIVE;

    /* Recheck conditions which may change between a CLI arm and explicit run. */
    if (!g_hooks.hardware_interlock_healthy()) {
        terminal_stop(MAP_CAPTURE_HW_INTERLOCK_MISSING, MAP_CAPTURE_FAULTED, true);
        return MAP_CAPTURE_HW_INTERLOCK_MISSING;
    }
    if (!g_hooks.control_paths_inactive()) {
        terminal_stop(MAP_CAPTURE_CONTROL_ACTIVE, MAP_CAPTURE_FAULTED, true);
        return MAP_CAPTURE_CONTROL_ACTIVE;
    }
    if (g_hooks.fault_latched()) {
        terminal_stop(MAP_CAPTURE_FAULT_LATCHED, MAP_CAPTURE_FAULTED, false);
        return MAP_CAPTURE_FAULT_LATCHED;
    }
    if (!ADC_OffsetsAreValid()) {
        terminal_stop(MAP_CAPTURE_OFFSET_INVALID, MAP_CAPTURE_FAULTED, true);
        return MAP_CAPTURE_OFFSET_INVALID;
    }

    /* The state must be visible before the first PWM-triggered JEOS can occur.
     * The PWM hook applies requested preloads and starts the bounded service
     * burst; per-frame CCR snapshots are taken in MapCapture_OnAdcFrame(). */
    g_state = MAP_CAPTURE_RUNNING;
    if (!g_hooks.start_service_pwm(&g_request)) {
        terminal_stop(MAP_CAPTURE_PWM_START_FAILED, MAP_CAPTURE_FAULTED, true);
        return MAP_CAPTURE_PWM_START_FAILED;
    }
    return MAP_CAPTURE_OK;
}

MapCaptureStatus MapCapture_Start(const MapCaptureRequest *request)
{
    MapCaptureStatus status = MapCapture_Arm(request);
    return status == MAP_CAPTURE_OK ? MapCapture_Run() : status;
}

void MapCapture_OnAdcFrame(const AdcFrame *frame)
{
    uint16_t next;
    MapCaptureRecord record;

    if (g_state != MAP_CAPTURE_RUNNING) return;
    if (!frame_is_capture_usable(frame)) {
        terminal_stop(MAP_CAPTURE_ADC_FAULT, MAP_CAPTURE_FAULTED, true);
        return;
    }
    if (abs_exceeds(frame->idc1_ma, g_request.max_abs_shunt_ma) ||
        abs_exceeds(frame->idc2_ma, g_request.max_abs_shunt_ma) ||
        frame->vbus_mv < (int32_t)g_request.min_vbus_mv ||
        frame->vbus_mv > (int32_t)g_request.max_vbus_mv) {
        terminal_stop(MAP_CAPTURE_LIMIT_EXCEEDED, MAP_CAPTURE_FAULTED, true);
        return;
    }

    next = ring_next(g_write_index);
    if (next == g_read_index) {
        ++g_dropped_records;
        terminal_stop(MAP_CAPTURE_BUFFER_OVERFLOW, MAP_CAPTURE_FAULTED, true);
        return;
    }

    memset(&record, 0, sizeof(record));
    record.frame = *frame;
    record.capture_id = g_request.capture_id;
    if (!g_hooks.snapshot_service_pwm(&record.pwm)) {
        terminal_stop(MAP_CAPTURE_SNAPSHOT_FAILED, MAP_CAPTURE_FAULTED, true);
        return;
    }
    if (!snapshot_matches_request(&record.pwm)) {
        terminal_stop(MAP_CAPTURE_TRIGGER_MISMATCH, MAP_CAPTURE_FAULTED, true);
        return;
    }
    record.fault_reason = MAP_CAPTURE_OK;

    g_ring[g_write_index] = record;
    MAP_CAPTURE_BARRIER();
    g_write_index = next;
    ++g_accepted_frames;

    if (g_accepted_frames >= g_request.pulse_count) {
        terminal_stop(MAP_CAPTURE_OK, MAP_CAPTURE_COMPLETE, false);
    }
}

void MapCapture_OnPeriod(void)
{
    if (g_state != MAP_CAPTURE_RUNNING) return;
    if (g_hooks.fault_latched != 0 && g_hooks.fault_latched()) {
        /* The central protection path owns its reason and shutdown. */
        terminal_stop(MAP_CAPTURE_PROTECTION_FAULT, MAP_CAPTURE_FAULTED, false);
        return;
    }
    ++g_periods_elapsed;
    if (g_periods_elapsed > g_request.timeout_periods) {
        terminal_stop(MAP_CAPTURE_TIMEOUT, MAP_CAPTURE_FAULTED, true);
    }
}

void MapCapture_OnProtectionFault(void)
{
    if (g_state == MAP_CAPTURE_ARMED || g_state == MAP_CAPTURE_RUNNING) {
        /* Protection owns its own latch; do not call the hook again. */
        terminal_stop(MAP_CAPTURE_PROTECTION_FAULT, MAP_CAPTURE_FAULTED, false);
    }
}

MapCaptureStatus MapCapture_Abort(void)
{
    if (g_state != MAP_CAPTURE_ARMED && g_state != MAP_CAPTURE_RUNNING) {
        return MAP_CAPTURE_NOT_ACTIVE;
    }
    terminal_stop(MAP_CAPTURE_ABORTED_BY_USER, MAP_CAPTURE_ABORTED, false);
    return MAP_CAPTURE_ABORTED_BY_USER;
}

bool MapCapture_IsActive(void)
{
    return g_state == MAP_CAPTURE_ARMED || g_state == MAP_CAPTURE_RUNNING;
}

MapCaptureStatus MapCapture_GetStatus(void)
{
    return g_terminal_status;
}

bool MapCapture_ConsumeRecord(MapCaptureRecord *out)
{
    uint16_t index;

    if (out == 0 || g_read_index == g_write_index) return false;
    index = g_read_index;
    *out = g_ring[index];
    MAP_CAPTURE_BARRIER();
    g_read_index = ring_next(index);
    return true;
}

void MapCapture_GetStats(MapCaptureStats *out)
{
    uint16_t write_index;
    uint16_t read_index;

    if (out == 0) return;
    write_index = g_write_index;
    read_index = g_read_index;
    out->state = g_state;
    out->terminal_status = g_terminal_status;
    out->capture_id = g_request.capture_id;
    out->accepted_frames = g_accepted_frames;
    out->dropped_records = g_dropped_records;
    out->periods_elapsed = g_periods_elapsed;
    out->records_available = (write_index >= read_index)
        ? (uint16_t)(write_index - read_index)
        : (uint16_t)(MAP_CAPTURE_RING_CAPACITY - read_index + write_index);
}

bool MapCapture_PopRecord(MapCaptureRecord *out)
{
    return MapCapture_ConsumeRecord(out);
}

void MapCapture_GetInfo(MapCaptureInfo *out)
{
    MapCapture_GetStats(out);
}
