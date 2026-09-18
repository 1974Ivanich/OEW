/* M-OPT-0 no-HV baseline: hosted safety-logic regression for the MapCapture port.
 *
 * ── SCOPE / HONESTY CONTRACT (read before changing) ──────────────────────
 * This test does NOT produce the physical M-OPT-0 baseline and must never be
 * relabelled as one. A hosted build has no DC-link, no real ADC samples, no
 * real PWM burst and no real UART stream; fabricating any of those would be
 * worse than having no test at all.
 *
 * What it does prove is the fail-closed logic the physical baseline depends on:
 *   1. every admission gate refuses BEFORE any ADC/PWM start (interlock,
 *      active control path, latched fault, invalid offsets, unapproved
 *      pattern);
 *   2. a no-HV aperture (raw_vbus <= 9, vbus_mv < min_vbus_mv) terminates the
 *      session as FAULTED / LIMIT_EXCEEDED / VBUS_LOW, latches PROTECT and
 *      returns the bridge to the stopped state exactly once;
 *   3. after the terminal state the session cannot restart, no further frame is
 *      accepted and the record ring stays empty — a baseline run must not
 *      fabricate characterization rows;
 *   4. five consecutive arm→run→terminal cycles are independent: no state,
 *      counter or bridge state leaks from the previous cycle.
 *
 * The physical runs M0-R1..M0-R5 are produced on bench PC-3 by
 * `tools/mopt0_capture.py` from the real firmware UART stream. Gate evidence
 * (firmware SHA, map_id, map_crc32, run_id) must come from that real stream;
 * this test neither produces nor substitutes it.
 *
 * Build (see Makefile: tests/m_opt_0_nohv_test.exe):
 *   gcc -std=c99 -Wall -Wextra -Werror -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u
 *       -DPWM_OEW_BOARD_REVISION=7u -Itests/mapcap_mock -Itests/hs1_mock -Isrc
 *       src/map_capture.c src/map_capture_port.c tests/m_opt_0_nohv_test.c -o ...
 */

#include <stdio.h>
#include <string.h>

#include "map_capture_port.h"
#include "map_capture_profiles.h"
#include "protect.h"
#include "pwm.h"
#include "stm32g474xx.h"

/* ── Host register storage required by src/map_capture_port.c ───────────── */
TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
ADC_TypeDef host_adc1;
ADC_TypeDef host_adc2;
ADC_Common_TypeDef host_adc12_common;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;

/* ── Mock state ─────────────────────────────────────────────────────────── */
static bool mock_interlock;
static bool mock_foc_running;
static bool mock_vf_running;
static bool mock_autotune_active;
static bool mock_fault_latched;
static bool mock_offsets_valid;
static bool mock_pattern_approved;
static bool mock_pwm_start_ok;
static unsigned adc_start_count;
static unsigned adc_stop_count;
static unsigned pwm_start_count;
static unsigned pwm_stop_count;
static unsigned latch_count;
static unsigned trigger_high_count;
static unsigned trigger_low_count;
static bool trigger_high;
static ProtectFaultReason last_latched_reason;
static PwmServiceCapturePattern last_pattern;
static bool last_pattern_valid;
static MapCaptureRequest active_request;

/* ── Mock production dependencies ───────────────────────────────────────── */
int ADC_InjectedStart(void) { ++adc_start_count; return 0; }
void ADC_InjectedStop(void) { ++adc_stop_count; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{ (void)sector; (void)window; (void)valid; }
void ADC_SetControlAdmission(bool admitted) { (void)admitted; }
bool ADC_OffsetsAreValid(void) { return mock_offsets_valid; }
uint16_t ADC_GetOffsetI1(void) { return 2048u; }
uint16_t ADC_GetOffsetI2(void) { return 2048u; }
uint16_t ADC_GetOffsetIres(void) { return 0u; }

bool PWM_HardwareInterlockHealthy(void) { return mock_interlock; }
bool PWM_BreakFaultActive(void) { return false; }
int PWM_ServiceCaptureStart(const PwmServiceCapturePattern *pattern)
{
    ++pwm_start_count;
    if (pattern == 0) return PWM_ENABLE_INTERLOCK_OPEN;
    last_pattern = *pattern;
    last_pattern_valid = true;
    host_tim1.CCR1 = pattern->tim1_ccr[0];
    host_tim1.CCR2 = pattern->tim1_ccr[1];
    host_tim1.CCR3 = pattern->tim1_ccr[2];
    host_tim8.CCR1 = pattern->tim8_ccr[0];
    host_tim8.CCR2 = pattern->tim8_ccr[1];
    host_tim8.CCR3 = pattern->tim8_ccr[2];
    return mock_pwm_start_ok ? PWM_ENABLE_OK : PWM_ENABLE_INTERLOCK_OPEN;
}
void PWM_Disable(void) { ++pwm_stop_count; }
/* Service-path TRIG pin: raised for the bounded burst, released at terminal. */
void PWM_TriggerHigh(void) { ++trigger_high_count; trigger_high = true; }
void PWM_TriggerLow(void) { ++trigger_low_count; trigger_high = false; }
bool PWM_SafetyOkIsHigh(void) { return true; }
bool PWM_BreakInputsAreHigh(void) { return true; }
void PWM_HeartbeatToggle(void) { }
uint16_t PWM_GetARR(void) { return (uint16_t)host_tim1.ARR; }
void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk)
{
    if (psc != 0) *psc = 0u;
    if (tclk != 0) *tclk = 50000000u;
}

bool FOC_IsRunning(void) { return mock_foc_running; }
bool VFC_IsRunning(void) { return mock_vf_running; }
bool Autotune_IsActive(void) { return mock_autotune_active; }
bool PROTECT_IsFault(void) { return mock_fault_latched; }
void PROTECT_LatchFault(ProtectFaultReason reason)
{
    ++latch_count;
    last_latched_reason = reason;
    mock_fault_latched = true;
}
bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{ return mock_pattern_approved && request != 0; }

/* ── Harness ────────────────────────────────────────────────────────────── */
static int checks;
static int failures;
static const char *stage = "init";

static void check(const char *name, int pass)
{
    ++checks;
    printf("  [%s] %s\n", pass ? "PASS" : "FAIL", name);
    if (!pass) ++failures;
}

#define CHECK(cond, name) check(name, (cond) ? 1 : 0)

/* Cycle-independent reset: safe/approved defaults, zero counters. */
static void cycle_reset(void)
{
    mock_interlock = true;
    mock_foc_running = false;
    mock_vf_running = false;
    mock_autotune_active = false;
    mock_fault_latched = false;
    mock_offsets_valid = true;
    mock_pattern_approved = true;
    mock_pwm_start_ok = true;
    adc_start_count = 0u;
    adc_stop_count = 0u;
    pwm_start_count = 0u;
    pwm_stop_count = 0u;
    latch_count = 0u;
    trigger_high_count = 0u;
    trigger_low_count = 0u;
    trigger_high = false;
    last_latched_reason = PROTECT_FAULT_NONE;
    last_pattern_valid = false;
    memset(&last_pattern, 0, sizeof(last_pattern));
    memset(&active_request, 0, sizeof(active_request));

    /* Live configuration the port snapshots for map identity. host_tim1/host_tim8
     * are initialized only here, never wiped after initialization. */
    host_tim1.ARR = 5000u;
    host_tim8.ARR = 5000u;
    host_tim1.BDTR = 0x0Fu;
    host_tim8.BDTR = 0x0Fu;
    host_adc1.SMPR1 = 7u << (1u * 3u);                              /* shunt1 */
    host_adc2.SMPR1 = (7u << (2u * 3u)) | (7u << (3u * 3u)) |
                      (7u << (5u * 3u));                            /* shunt2/CT/Vbus */
    host_adc12_common.CCR = 3u << ADC_CCR_CKMODE_Pos;               /* HCLK/4 */
}

/* SYNT-shaped request: the only profile the port can approve on this board. */
static MapCaptureRequest baseline_request(void)
{
    MapCaptureRequest value;
    memset(&value, 0, sizeof(value));
    value.capture_id = 1u;
    value.pulse_count = 16u;
    value.timeout_periods = 20u;
    value.max_abs_shunt_ma = 10000;
    value.min_vbus_mv = 1000u;
    value.max_vbus_mv = 70000u;
    value.sector_candidate = 0u;
    value.window_candidate = 0u;
    value.tim1_ccr[0] = 500u; value.tim1_ccr[1] = 500u; value.tim1_ccr[2] = 500u;
    value.tim8_ccr[0] = 500u; value.tim8_ccr[1] = 500u; value.tim8_ccr[2] = 500u;
    value.trigger_revision = PWM_OEW_ADC_TRIGGER_REVISION;
    return value;
}

/* Physical no-HV aperture: DC-link disconnected, sensors alive. */
static AdcFrame no_hv_frame(const MapCaptureRequest *request)
{
    AdcFrame frame;
    memset(&frame, 0, sizeof(frame));
    frame.status = ADC_FRAME_WINDOW_INVALID;
    frame.tim1_sector = request->sector_candidate;
    frame.sample_window = request->window_candidate;
    frame.raw_vbus = 2u;
    frame.vbus_mv = 201;
    frame.raw_idc1 = 2048u;
    frame.raw_idc2 = 2048u;
    frame.raw_ct = 2048u;
    frame.idc1_ma = 0;
    frame.idc2_ma = 0;
    frame.sequence = 1u;
    return frame;
}

/* ── Gates: every refusal must precede any ADC/PWM start ────────────────── */
static void check_admission_gates(const MapCaptureRequest *request, unsigned cycle)
{
    char name[96];
    unsigned i;

    struct { const char *label; MapCaptureStatus expected; } cases[5] = {
        { "interlock missing", MAP_CAPTURE_HW_INTERLOCK_MISSING },
        { "control path active", MAP_CAPTURE_CONTROL_ACTIVE },
        { "fault latched", MAP_CAPTURE_FAULT_LATCHED },
        { "offsets invalid", MAP_CAPTURE_OFFSET_INVALID },
        { "pattern not approved", MAP_CAPTURE_BAD_REQUEST }
    };

    stage = "admission gates";
    for (i = 0u; i < 5u; ++i) {
        MapCaptureStatus rc;

        cycle_reset();
        if (!MapCapturePort_Init()) { CHECK(0, "MapCapturePort_Init (gate)"); return; }
        switch (i) {
            case 0u: mock_interlock = false; break;
            case 1u: mock_foc_running = true; break;
            case 2u: mock_fault_latched = true; break;
            case 3u: mock_offsets_valid = false; break;
            default: mock_pattern_approved = false; break;
        }

        rc = MapCapture_Arm(request);

        snprintf(name, sizeof(name), "cycle %u gate %s -> rc=%d", cycle, cases[i].label, (int)rc);
        CHECK(rc == cases[i].expected, name);
        snprintf(name, sizeof(name), "cycle %u gate %s: no ADC/PWM start", cycle, cases[i].label);
        CHECK(adc_start_count == 0u && pwm_start_count == 0u && pwm_stop_count == 0u, name);
        snprintf(name, sizeof(name), "cycle %u gate %s: no fault latch", cycle, cases[i].label);
        CHECK(latch_count == 0u, name);
    }
}

/* ── One complete baseline cycle ────────────────────────────────────────── */
static void check_baseline_cycle(unsigned cycle)
{
    char name[96];
    MapCaptureRequest request = baseline_request();
    MapCaptureStats stats;
    AdcFrame frame;

    stage = "cycle admission";
    cycle_reset();
    if (!MapCapturePort_Init()) { CHECK(0, "MapCapturePort_Init (cycle)"); return; }

    snprintf(name, sizeof(name), "cycle %u: armed with exactly one injected start", cycle);
    CHECK(MapCapture_Arm(&request) == MAP_CAPTURE_OK &&
          adc_start_count == 1u && adc_stop_count == 0u &&
          pwm_start_count == 0u, name);

    snprintf(name, sizeof(name), "cycle %u: run starts the bounded service burst once", cycle);
    CHECK(MapCapture_Run() == MAP_CAPTURE_OK && pwm_start_count == 1u &&
          pwm_stop_count == 0u, name);

    snprintf(name, sizeof(name), "cycle %u: TRIG raised for the burst", cycle);
    CHECK(trigger_high && trigger_high_count == 1u && trigger_low_count == 0u, name);

    snprintf(name, sizeof(name), "cycle %u: pattern carries the requested aperture", cycle);
    CHECK(last_pattern_valid &&
          last_pattern.sector_candidate == request.sector_candidate &&
          last_pattern.window_candidate == request.window_candidate &&
          last_pattern.trigger_revision == request.trigger_revision &&
          last_pattern.tim1_ccr[0] == request.tim1_ccr[0] &&
          last_pattern.tim8_ccr[2] == request.tim8_ccr[2], name);

    /* The no-HV aperture is the evidence: raw VBUS far below the profile gate. */
    stage = "no-HV terminal";
    frame = no_hv_frame(&request);
    MapCapture_OnAdcFrame(&frame);
    MapCapture_GetStats(&stats);

    snprintf(name, sizeof(name), "cycle %u: no-HV aperture -> FAULTED", cycle);
    CHECK(stats.state == MAP_CAPTURE_FAULTED, name);
    snprintf(name, sizeof(name), "cycle %u: terminal LIMIT_EXCEEDED (-12)", cycle);
    CHECK(stats.terminal_status == MAP_CAPTURE_LIMIT_EXCEEDED, name);
    snprintf(name, sizeof(name), "cycle %u: fault detail VBUS_LOW (7)", cycle);
    CHECK(stats.fault_detail == MAP_CAPTURE_FAULT_DETAIL_VBUS_LOW, name);
    snprintf(name, sizeof(name), "cycle %u: PROTECT latched with CAPTURE_LIMIT reason", cycle);
    CHECK(latch_count == 1u && last_latched_reason == PROTECT_FAULT_CAPTURE_LIMIT, name);

    snprintf(name, sizeof(name), "cycle %u: bridge and injected sequence stopped once", cycle);
    CHECK(pwm_stop_count == 1u && adc_stop_count == 1u && pwm_start_count == 1u, name);

    snprintf(name, sizeof(name), "cycle %u: TRIG released at terminal", cycle);
    CHECK(!trigger_high && trigger_low_count == 1u && trigger_high_count == 1u, name);

    snprintf(name, sizeof(name), "cycle %u: terminal evidence preserves the no-HV frame", cycle);
    CHECK(stats.terminal_raw_vbus == 2u && stats.terminal_vbus_mv == 201 &&
          stats.terminal_adc_status == ADC_FRAME_WINDOW_INVALID, name);

    snprintf(name, sizeof(name), "cycle %u: no map row fabricated by a baseline run", cycle);
    CHECK(stats.accepted_frames == 0u && stats.records_available == 0u &&
          stats.dropped_records == 0u, name);
    {
        MapCaptureRecord record;
        snprintf(name, sizeof(name), "cycle %u: record ring stays empty", cycle);
        CHECK(!MapCapture_ConsumeRecord(&record), name);
    }

    /* Post-terminal containment: no restart, no extra frame, no extra stop. */
    stage = "post-terminal containment";
    snprintf(name, sizeof(name), "cycle %u: restart refused after terminal", cycle);
    CHECK(MapCapture_Run() != MAP_CAPTURE_OK && pwm_start_count == 1u, name);

    frame.sequence = 2u;
    MapCapture_OnAdcFrame(&frame);
    MapCapture_GetStats(&stats);
    snprintf(name, sizeof(name), "cycle %u: post-terminal frame ignored", cycle);
    CHECK(stats.accepted_frames == 0u && pwm_stop_count == 1u && adc_stop_count == 1u, name);

    snprintf(name, sizeof(name), "cycle %u: abort is idempotent and cannot re-arm", cycle);
    MapCapture_Abort();
    CHECK(MapCapture_Run() != MAP_CAPTURE_OK && pwm_start_count == 1u, name);
}

/* ── Map identity: measurable live config, fail-closed on a dead stage ──── */
static void check_map_identity(void)
{
    OewMapIdentity id;

    stage = "map identity";
    cycle_reset();
    if (!MapCapturePort_Init()) { CHECK(0, "MapCapturePort_Init (identity)"); return; }

    CHECK(MapCapturePort_GetMapIdentity(&id), "identity: live configuration is measurable");
    CHECK(id.board_revision == 7u, "identity: board revision 7");
    CHECK(id.timer_arr == 5000u, "identity: TIM1 ARR matches live register");
    CHECK(id.adc_trigger_id == PWM_OEW_ADC_TRIGGER_REVISION, "identity: ADC trigger revision");
    CHECK(id.adc_clock_hz == 42500000u, "identity: ADC clock = HCLK/4");
    CHECK(id.adc_sample_cycles_x2 == 1281u, "identity: ADC sample time encoded 2x");
    CHECK(id.adc_resolution == 0u, "identity: 12-bit resolution");
    CHECK(id.deadtime_ticks == 0x0Fu, "identity: dead-time dead code is nonzero");
    CHECK(id.adc_config_signature != 0u &&
          id.current_calibration_signature != 0u, "identity: signatures measurable");

    host_tim1.BDTR = 0u;
    CHECK(!MapCapturePort_GetMapIdentity(&id), "identity: zero dead-time fails closed");
}

int main(void)
{
    unsigned cycle;
    MapCaptureRequest gate_request;

    printf("=== M-OPT-0 no-HV baseline: hosted safety-logic regression ===\n");
    printf("NOTE: hosted logic test only; the physical M0-R1..R5 baseline is\n");
    printf("      captured on PC-3 by tools/mopt0_capture.py, not here.\n");

    gate_request = baseline_request();
    for (cycle = 1u; cycle <= 5u; ++cycle) {
        printf("--- cycle %u/5 ---\n", cycle);
        check_admission_gates(&gate_request, cycle);
        check_baseline_cycle(cycle);
    }
    check_map_identity();

    printf("=== %s (%d checks, %d failed) [stage: %s] ===\n",
           failures == 0 ? "ALL PASS" : "FAILURES", checks, failures, stage);
    return failures ? 1 : 0;
}
