/* Hosted-тест bounded service-only capture path (методика §2 / «Готовность
 * к service-only capture»). Проверяет rejection-preconditions, bounded прогон
 * N периодов, immediate stop на OVR/лимитах, timeout, abort и reentry.
 * Стабы PWM/ADC/FOC/VFC/PROTECT/UART — здесь; map_capture.c использует API
 * (регистры TIM1/TIM8 — только чтение CCR/ARR в снапшоте). */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "map_capture.h"
#include "adc.h"
#include "pwm.h"
#include "protect.h"
#include "uart.h"

/* ── Стабы ─────────────────────────────────────────────────────────── */
static int st_pwm_enabled;
static int st_armed;
static int st_foc_running;
static int st_vfc_running;
static int st_fault;
static int st_fault_reason;
static int st_offsets_valid;
static int st_serv_enable_rc;
static int st_pwm_disable_calls;
static int st_set_control_vector_calls;
static AdcFrame st_frame;
static bool st_has_frame;
static int st_abort_after;
static uint32_t st_calls;
static uint32_t st_seq;

uint32_t PWM_IsEnabled(void) { return st_pwm_enabled != 0; }
bool ADC_InjectedIsArmed(void) { return st_armed != 0; }
int  FOC_IsRunning(void) { return st_foc_running; }
int  VFC_IsRunning(void) { return st_vfc_running; }
int  PROTECT_IsFault(void) { return st_fault; }
int  PROTECT_GetFaultReason(void) { return st_fault_reason; }
void PROTECT_CheckCaptureFrame(const AdcFrame *frame)
{
    if (frame->status != ADC_FRAME_VALID &&
        frame->status != ADC_FRAME_MAPPING_UNVERIFIED) {
        st_fault = 1;
        st_fault_reason = (int)PROTECT_FAULT_ADC_OVERRUN;
    }
}
void PROTECT_LatchFault(ProtectFaultReason reason)
{
    st_fault = 1;
    st_fault_reason = (int)reason;
}
bool ADC_OffsetsAreValid(void) { return st_offsets_valid != 0; }
bool ADC_GetLatestFrame(AdcFrame *out)
{
    if (!st_has_frame) return false;
    st_calls++;
    if (st_abort_after > 0 && (int)st_calls >= st_abort_after) MapCapture_Abort();
    /* Каждый вызов — «новый период»: sequence строго растёт. */
    st_frame.sequence = ++st_seq;
    *out = st_frame;
    return true;
}
bool PWM_SetControlVector(int16_t mu, int16_t mv, int16_t mw,
                          const PwmSampleContext *context)
{ (void)mu; (void)mv; (void)mw; (void)context; st_set_control_vector_calls++; return true; }
int  PWM_ServiceEnable(const PwmSampleContext *context)
{ (void)context; return st_serv_enable_rc; }
void PWM_Disable(void) { st_pwm_disable_calls++; }
void UART_SendTelemetry(const char *fmt, ...) { (void)fmt; }

/* Мок регистров (tests/pwm_mock/stm32g474xx.h) — CCR/ARR в снапшоте */
#include "stm32g474xx.h"
TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;

static void host_reset(void) {
    st_pwm_enabled = 0;
    st_armed = 0;
    st_foc_running = 0;
    st_vfc_running = 0;
    st_fault = 0;
    st_fault_reason = 0;
    st_offsets_valid = 1;
    st_serv_enable_rc = 0;   /* PWM_ENABLE_OK */
    st_pwm_disable_calls = 0;
    st_set_control_vector_calls = 0;
    st_has_frame = false;
    st_abort_after = 0;
    st_calls = 0u;
    st_seq = 0u;
    st_frame.status = ADC_FRAME_MAPPING_UNVERIFIED;
    st_frame.raw_idc1 = 2048u; st_frame.raw_idc2 = 2048u;
    st_frame.raw_ct = 2048u; st_frame.raw_vbus = 1500u;
    st_frame.idc1_ma = 0; st_frame.idc2_ma = 0; st_frame.ict_ma = 0;
    st_frame.vbus_mv = 150000;
    host_tim1.CCR1 = 500u; host_tim1.CCR2 = 500u; host_tim1.CCR3 = 500u;
    host_tim8.CCR1 = 500u; host_tim8.CCR2 = 500u; host_tim8.CCR3 = 500u;
    host_tim1.ARR = 999u;
}

static MapCaptureRequest make_req(void) {
    MapCaptureRequest r;
    r.mu = -30000; r.mv = 0; r.mw = 0;
    r.sector_candidate = 0u;
    r.window_candidate = 0u;
    r.pulse_count = 4u;
    r.timeout_periods = 100u;
    r.max_abs_shunt_ma = 10000;
    r.max_vbus_mv = 350000u;
    r.min_vbus_mv = 10000u;
    r.trigger_revision = 1u;
    r.capture_id = 1u;
    return r;
}

int main(void) {
    MapCaptureRequest req;

    /* 1. Preconditions-rejection */
    host_reset();
    assert(MapCapture_Run(0) == MAP_CAPTURE_BAD_PATTERN);
    req = make_req();
    req.pulse_count = 0u;  assert(MapCapture_Run(&req) == MAP_CAPTURE_BAD_PATTERN);
    req = make_req();
    req.sector_candidate = 6u; assert(MapCapture_Run(&req) == MAP_CAPTURE_BAD_PATTERN);
    req = make_req();
    req.window_candidate = 2u; assert(MapCapture_Run(&req) == MAP_CAPTURE_BAD_PATTERN);
    req = make_req();
    req.pulse_count = 5000u; assert(MapCapture_Run(&req) == MAP_CAPTURE_BAD_PATTERN);
    host_reset(); st_pwm_enabled = 1; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_HW_INTERLOCK_MISSING);
    host_reset(); st_armed = 1; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_HW_INTERLOCK_MISSING);
    host_reset(); st_foc_running = 1; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_CONTROL_ACTIVE);
    host_reset(); st_offsets_valid = 0; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_OFFSET_INVALID);
    host_reset(); st_fault = 1; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_FAULT_LATCHED);
    host_reset(); st_serv_enable_rc = -3; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_FAULT_LATCHED);
    assert(st_pwm_disable_calls == 1);
    host_reset(); st_has_frame = false; req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_TIMEOUT);

    /* 2. Успешный bounded прогон: 4 новых фрейма (MAPPING_UNVERIFIED),
     * ровно 1 SetControlVector + 1 Disable, ring заполнен, rc=OK */
    host_reset();
    st_has_frame = true;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_OK);
    assert(st_set_control_vector_calls == 1);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_IsActive() == false);
    assert(MapCapture_GetRing()->produced == 4u);
    assert(MapCapture_GetRing()->consumed == 0u);
    assert(MapCapture_GetRing()->dropped == 0u);

    /* 3. Невалидный статус фрейма (OVERRUN) → PROTECT_CheckCaptureFrame
     * латчит → MAP_CAPTURE_FRAME_FAULT + stop */
    host_reset();
    st_has_frame = true;
    st_frame.status = ADC_FRAME_OVERRUN;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_FRAME_FAULT);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_LastFaultReason() == (uint32_t)PROTECT_FAULT_ADC_OVERRUN);

    /* 4. Лимит сессии: |idc1| > max_abs_shunt_ma → MAP_CAPTURE_LIMIT + latch */
    host_reset();
    st_has_frame = true;
    st_frame.idc1_ma = 12000;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_LIMIT);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_LastFaultReason() == (uint32_t)PROTECT_FAULT_CAPTURE_LIMIT);

    /* 5. Лимит Vbus: vbus вне окна → MAP_CAPTURE_LIMIT */
    host_reset();
    st_has_frame = true;
    st_frame.vbus_mv = 8000;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_LIMIT);

    /* 6. Abort извне (watchdog/user): g_abort на 2-м фрейме → -ABORTED */
    host_reset();
    st_has_frame = true;
    st_abort_after = 2;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_ABORTED);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_IsActive() == false);

    /* 7. Reentry после завершения допустим; новый capture_id инкрементится */
    host_reset();
    st_has_frame = true;
    req = make_req();
    assert(MapCapture_Run(&req) == MAP_CAPTURE_OK);
    assert(MapCapture_Run(&req) == MAP_CAPTURE_OK);
    assert(st_pwm_disable_calls == 2);
    assert(MapCapture_NextCaptureId() >= 1u);
    assert(MapCapture_NextCaptureId() >= 2u);   /* строго растёт */

    puts("map_capture_test: PASS");
    return 0;
}
