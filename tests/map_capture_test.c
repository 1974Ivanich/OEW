/* Hosted-тест bounded service-only capture path (методика первого съёма карты §2).
 * Проверяет rejection-preconditions, bounded прогон N периодов, immediate stop
 * на невалидном статусе фрейма и abort. Стабы PWM/ADC/FOC/VFC/PROTECT/UART —
 * здесь; map_capture.c использует только API (регистры TIM1/TIM8 — только
 * чтение CCR/ARR в лог-стабе). */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "map_capture.h"
#include "adc.h"
#include "pwm.h"
#include "protect.h"
#include "uart.h"

/* Мок регистров (tests/pwm_mock/stm32g474xx.h) — для чтения CCR/ARR в логе */
#include "stm32g474xx.h"
TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;

/* ── Стабы ─────────────────────────────────────────────────────────── */
static int st_pwm_enabled;
static int st_armed;
static int st_foc_running;
static int st_vfc_running;
static int st_fault;
static int st_offsets_valid;
static int st_serv_enable_rc;
static int st_pwm_disable_calls;
static int st_set_control_vector_calls;
static AdcFrame st_frame;
static bool st_has_frame;
static int st_abort_after;   /* >0: вызвать MapCapture_Abort() на этом вызове GetLatestFrame */
static uint32_t st_calls;
static uint32_t st_seq;

uint32_t PWM_IsEnabled(void) { return st_pwm_enabled != 0; }
bool ADC_InjectedIsArmed(void) { return st_armed != 0; }
int  FOC_IsRunning(void) { return st_foc_running; }
int  VFC_IsRunning(void) { return st_vfc_running; }
int  PROTECT_IsFault(void) { return st_fault; }
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

static void host_reset(void) {
    st_pwm_enabled = 0;
    st_armed = 0;
    st_foc_running = 0;
    st_vfc_running = 0;
    st_fault = 0;
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
}

static MapCaptureRequest make_req(void) {
    MapCaptureRequest r;
    r.mu = -30000; r.mv = 0; r.mw = 0;
    r.sector_candidate = 0u;
    r.window_candidate = 0u;
    r.pulse_count = 4u;
    r.capture_id = 1u;
    return r;
}

int main(void) {
    MapCaptureRequest req;

    /* 1. Preconditions-rejection */
    host_reset();
    assert(MapCapture_Run(0) == -1);                       /* NULL */
    req = make_req();
    req.pulse_count = 0u;  assert(MapCapture_Run(&req) == -1);
    req = make_req();
    req.sector_candidate = 6u; assert(MapCapture_Run(&req) == -1);
    req = make_req();
    req.window_candidate = 2u; assert(MapCapture_Run(&req) == -1);
    req = make_req();
    req.pulse_count = 5000u; assert(MapCapture_Run(&req) == -1);  /* > MAX */
    host_reset(); st_pwm_enabled = 1; req = make_req();
    assert(MapCapture_Run(&req) == -1);                    /* PWM on */
    host_reset(); st_armed = 1; req = make_req();
    assert(MapCapture_Run(&req) == -1);                    /* ADC armed */
    host_reset(); st_foc_running = 1; req = make_req();
    assert(MapCapture_Run(&req) == -1);                    /* FOC running */
    host_reset(); st_offsets_valid = 0; req = make_req();
    assert(MapCapture_Run(&req) == -1);                    /* offsets invalid */
    host_reset(); st_fault = 1; req = make_req();
    assert(MapCapture_Run(&req) == -2);                    /* fault-latch */
    host_reset(); st_serv_enable_rc = -3; req = make_req();
    assert(MapCapture_Run(&req) == -2);                    /* ServiceEnable отказ */
    assert(st_pwm_disable_calls == 1);                     /* безусловный stop */
    host_reset(); st_has_frame = false; req = make_req();
    assert(MapCapture_Run(&req) == -3);                    /* нет фрейма → timeout */

    /* 2. Успешный bounded прогон: 4 новых фрейма (MAPPING_UNVERIFIED),
     * ровно 4 лога, ровно 1 SetControlVector + 1 Disable, rc=0 */
    host_reset();
    st_has_frame = true;
    req = make_req();
    assert(MapCapture_Run(&req) == 0);
    assert(st_set_control_vector_calls == 1);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_IsActive() == false);

    /* 3. Невалидный статус фрейма (OVERRUN) → immediate stop rc=-5 */
    host_reset();
    st_has_frame = true;
    st_frame.status = ADC_FRAME_OVERRUN;
    req = make_req();
    assert(MapCapture_Run(&req) == -5);
    assert(st_pwm_disable_calls == 1);

    /* 4. Abort извне (watchdog/user из main loop): стаб ставит g_abort на
     * 2-м новом фрейме → следующая итерация цикла → rc=-4, стоп выполнен */
    host_reset();
    st_has_frame = true;
    st_abort_after = 2;
    req = make_req();
    assert(MapCapture_Run(&req) == -4);
    assert(st_pwm_disable_calls == 1);
    assert(MapCapture_IsActive() == false);

    /* 5. Reentry после завершения допустим */
    host_reset();
    st_has_frame = true;
    req = make_req();
    assert(MapCapture_Run(&req) == 0);
    assert(MapCapture_Run(&req) == 0);
    assert(st_pwm_disable_calls == 2);

    puts("map_capture_test: PASS");
    return 0;
}
