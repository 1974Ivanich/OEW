/* Hosted-тест frame-aware protection (ревью «Согласованная ревизия», P0 protect.c).
 * Проверяет: статус-маппинг фрейма → fault reason, латч по току/Vbus из ОДНОГО
 * фрейма (без legacy-геттеров), единый стоп-путь (PWM_Disable ровно один раз),
 * request-clear с проверкой свежей выборки (ADC_InjectedIsArmed/StartConversion).
 * Собирается: gcc -std=c99 -Wall -Wextra -Werror -Isrc src/protect.c this.c
 * (стабы ADC/PWM — здесь; protect.c читает host_tim1/tim8 SR на clear). */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "stm32g474xx.h"
#include "adc.h"
#include "protect.h"
#include "pwm.h"

/* ── Стабы ADC/PWM ─────────────────────────────────────────────────── */
uint32_t host_primask;
void HostIrqRestoreHook(uint32_t restored_primask) { (void)restored_primask; }
void PROTECT_HostClearCommitHook(void) { }
TIM_TypeDef host_tim1; TIM_TypeDef host_tim8;

static AdcFrame host_frame;

static bool host_has_frame;
static bool host_armed;
static int host_conv_rc;
static int host_pwm_disable_calls;
static int host_invalidate_calls;
static bool host_sd_high;
static int32_t host_regular_vbus_mv;

bool ADC_GetLatestFrame(AdcFrame *out) { if (!host_has_frame) return false; *out = host_frame; return true; }
bool ADC_InjectedIsArmed(void) { return host_armed; }
int  ADC_StartConversion(void) { return host_conv_rc; }
int32_t ADC_GetVbus_mV(void) { return host_frame.vbus_mv; }
int32_t ADC_ReadVbusRegularMv(void) { return host_regular_vbus_mv; }
int32_t ADC_GetI1_mA(void) { return host_frame.idc1_ma; }
int32_t ADC_GetI2_mA(void) { return host_frame.idc2_ma; }
void PWM_Disable(void) { host_pwm_disable_calls++; }
void PWM_InvalidateSampleContext(void) { host_invalidate_calls++; }
/* Direct SD health doubles for RequestClear. */
bool PWM_SdLinesAreHigh(void) { return host_sd_high; }
bool PWM_BreakFaultActive(void) { return !host_sd_high; }

static void host_reset(void) {
    host_has_frame = false;
    host_armed = false;
    host_conv_rc = 0;
    host_pwm_disable_calls = 0;
    host_invalidate_calls = 0;
    host_sd_high = true;
    host_regular_vbus_mv = 60000;
    PROTECT_Init();
}

static AdcFrame make_frame(AdcFrameStatus status, int32_t i1, int32_t i2, int32_t vbus) {
    AdcFrame f;
    f.status = status;
    f.idc1_ma = i1;
    f.idc2_ma = i2;
    f.vbus_mv = vbus;
    f.sequence = 1u;
    f.tim1_sector = 0u;
    f.sample_window = 0u;
    return f;
}

int main(void) {
    /* 1. VALID + idc1=15 А (>12 А) → OVERCURRENT latch + ровно один PWM_Disable */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 15000, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault());
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_OVERCURRENT);
    assert(host_pwm_disable_calls == 1);
    /* повторный CheckFrame при уже залатченном fault — без второго PWM_Disable */
    PROTECT_CheckFrame(&host_frame);
    assert(host_pwm_disable_calls == 1);

    /* 2. VALID + idc2 = -13 А → OVERCURRENT (оба шунта, знак не важен) */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 0, -13000, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_OVERCURRENT);

    /* 3. VALID + vbus=400 В (debounce 10) → VBUS_HIGH */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 400000);
    for (int i = 0; i < 9; i++) { PROTECT_CheckFrame(&host_frame); assert(!PROTECT_IsFault()); }
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_VBUS_HIGH);

    /* 4. VALID + vbus=5 В → VBUS_LOW (мгновенно) */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 5000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_VBUS_LOW);

    /* 5. Статус-маппинг: OVERRUN → ADC_OVERRUN, JQOVF → QUEUE_OVERRUN,
     *      DESYNC → ADC_DESYNC, TIMEOUT → ADC_TIMEOUT, WINDOW_INVALID → SAMPLE_WINDOW,
     *      MAPPING_UNVERIFIED → CURRENT_MAP */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_OVERRUN, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_ADC_OVERRUN);
    host_reset(); host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_QUEUE_OVERRUN, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_ADC_QUEUE_OVERRUN);
    host_reset(); host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_DESYNCHRONIZED, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_ADC_DESYNC);
    host_reset(); host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_JEOS_TIMEOUT, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_ADC_TIMEOUT);
    host_reset(); host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_WINDOW_INVALID, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_SAMPLE_WINDOW);
    host_reset(); host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_MAPPING_UNVERIFIED, 0, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_GetFaultReason() == PROTECT_FAULT_CURRENT_MAP);

    /* 6. NULL-фрейм → FRAME_COPY */
    host_reset();
    PROTECT_CheckFrame(NULL);
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_FRAME_COPY);
    host_reset();
    PROTECT_LatchFrameCopyFailure();
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_FRAME_COPY);

    /* 7. RequestClear: fault без armed, свежая service-выборка в recovery-окне → OK */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 15000, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault());
    host_armed = false;
    host_conv_rc = 0;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 150000);
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_OK);
    assert(!PROTECT_IsFault());

    /* 8. RequestClear: injected armed (контур работает) → CONTROL_ACTIVE, latch жив */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 5000);
    PROTECT_CheckFrame(&host_frame);
    assert(PROTECT_IsFault());
    host_armed = true;
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_CONTROL_ACTIVE);
    assert(PROTECT_IsFault());

    /* 9. RequestClear: service-выборка отказала → SAMPLE_INVALID */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 15000, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    host_armed = false;
    host_conv_rc = -1;
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_SAMPLE_INVALID);
    assert(PROTECT_IsFault());

    /* 10. RequestClear: Vbus вне recovery-окна → VALUES_UNSAFE */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 15000, 0, 150000);
    PROTECT_CheckFrame(&host_frame);
    host_armed = false;
    host_conv_rc = 0;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 380000);
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_VALUES_UNSAFE);
    assert(PROTECT_IsFault());

    /* 11. Compatibility PROTECT_Check: VALID frame checks values. */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_VALID, 0, 0, 0);
    host_regular_vbus_mv = 60000;
    PROTECT_Check();
    assert(!PROTECT_IsFault());
    host_regular_vbus_mv = 5000;
    PROTECT_Check();
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_VBUS_LOW);

    /* 12. P1: service frame is not control-valid but its regular Vbus is checked. */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_SERVICE_BUSY, 0, 0, 0);
    host_regular_vbus_mv = 60000;
    PROTECT_Check();
    assert(!PROTECT_IsFault());
    host_regular_vbus_mv = 5000;
    PROTECT_Check();
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_VBUS_LOW);

    /* 13. P1: service frame must apply the same DC-link overcurrent limit. */
    host_reset();
    host_has_frame = true;
    host_frame = make_frame(ADC_FRAME_SERVICE_BUSY, 0, -13000, 150000);
    PROTECT_Check();
    assert(PROTECT_IsFault() && PROTECT_GetFaultReason() == PROTECT_FAULT_OVERCURRENT);

    puts("protect_frame_host_test: PASS");
    return 0;
}
