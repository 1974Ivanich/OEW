#include "protect.h"
#include "pwm.h"
#include "pwm_board_pins.h"   /* PWM_SdLinesAreHigh: direct read-only SD health */
#include "stm32g474xx.h"      /* PRIMASK/DMB for atomic break-clear commit */

#define PROTECT_I_MAX_MA        12000
#define PROTECT_VBUS_MIN_MV     8000
#define PROTECT_VBUS_MAX_MV     350000
#define PROTECT_VBUS_OVERCNT    10u

static volatile int fault;
static volatile uint8_t vbus_over_count;
static volatile int fault_reason;

static int32_t protect_abs_i32(int32_t value)
{
    if (value == INT32_MIN) return INT32_MAX;
    return value < 0 ? -value : value;
}

static void protect_latch(ProtectFaultReason reason)
{
    if (fault) return;
    /* The central fault is the single terminal software latch. SD itself is
     * self-clearing in the IPM, but the break ISR latches this fault and
     * performs the terminal stop; neither interlock nor PWM can self-rearm. */
    fault = 1;
    fault_reason = (int)reason;
    /* Central terminal stop: CEN/MOE off, aperture invalidated and injected
     * acquisition disarmed. There is no external ARM/EN path in SD-direct. */
    PWM_Disable();
}

static bool protect_frame_status_reason(AdcFrameStatus status,
                                        ProtectFaultReason *reason)
{
    switch (status) {
        case ADC_FRAME_VALID:
            return false;
        case ADC_FRAME_OVERRUN:
            *reason = PROTECT_FAULT_ADC_OVERRUN;
            return true;
        case ADC_FRAME_QUEUE_OVERRUN:
            *reason = PROTECT_FAULT_ADC_QUEUE_OVERRUN;
            return true;
        case ADC_FRAME_DESYNCHRONIZED:
            *reason = PROTECT_FAULT_ADC_DESYNC;
            return true;
        case ADC_FRAME_JEOS_TIMEOUT:
        case ADC_FRAME_NOT_ARMED:
            *reason = PROTECT_FAULT_ADC_TIMEOUT;
            return true;
        case ADC_FRAME_WINDOW_INVALID:
            *reason = PROTECT_FAULT_SAMPLE_WINDOW;
            return true;
        case ADC_FRAME_MAPPING_UNVERIFIED:
        case ADC_FRAME_CALIBRATION_INVALID:
        case ADC_FRAME_ADC_SATURATED:
            *reason = PROTECT_FAULT_CURRENT_MAP;
            return true;
        default:
            *reason = PROTECT_FAULT_ADC_DESYNC;
            return true;
    }
}

static void protect_check_values(int32_t idc1_ma, int32_t idc2_ma,
                                 int32_t vbus_mv)
{
    if (protect_abs_i32(idc1_ma) > PROTECT_I_MAX_MA ||
        protect_abs_i32(idc2_ma) > PROTECT_I_MAX_MA) {
        protect_latch(PROTECT_FAULT_OVERCURRENT);
        return;
    }

    if (vbus_mv > PROTECT_VBUS_MAX_MV) {
        if (++vbus_over_count >= PROTECT_VBUS_OVERCNT) {
            protect_latch(PROTECT_FAULT_VBUS_HIGH);
        }
        return;
    }
    vbus_over_count = 0u;

    if (vbus_mv < PROTECT_VBUS_MIN_MV) {
        protect_latch(PROTECT_FAULT_VBUS_LOW);
    }
}

void PROTECT_Init(void)
{
    fault = 0;
    vbus_over_count = 0u;
    fault_reason = PROTECT_FAULT_NONE;
}

void PROTECT_CheckFrame(const AdcFrame *frame)
{
    ProtectFaultReason reason;

    if (fault) return;
    if (frame == 0) {
        protect_latch(PROTECT_FAULT_FRAME_COPY);
        return;
    }
    if (protect_frame_status_reason(frame->status, &reason)) {
        protect_latch(reason);
        return;
    }

    /* Both DC-link shunts and Vbus belong to this exact sequence. CT remains
     * diagnostic and is never substituted for a phase or link-current limit. */
    protect_check_values(frame->idc1_ma, frame->idc2_ma, frame->vbus_mv);
}

void PROTECT_LatchFrameCopyFailure(void)
{
    protect_latch(PROTECT_FAULT_FRAME_COPY);
}

void PROTECT_LatchFault(ProtectFaultReason reason)
{
    protect_latch(reason);
}

void PROTECT_CheckCaptureFrame(const AdcFrame *frame)
{
    ProtectFaultReason reason;

    if (fault) return;
    if (frame == 0) {
        protect_latch(PROTECT_FAULT_FRAME_COPY);
        return;
    }
    /* Diagnostic capture: VALID и MAPPING_UNVERIFIED — допустимые статусы
     * (окно первого съёма как раз доказывается). Всё остальное —
     * аппаратный/data-quality сбой → latch. */
    if (frame->status == ADC_FRAME_VALID ||
        frame->status == ADC_FRAME_MAPPING_UNVERIFIED) {
        return;
    }
    if (protect_frame_status_reason(frame->status, &reason)) {
        protect_latch(reason);
    }
}

void PROTECT_Check(void)
{
    AdcFrame frame;

    /* Compatibility/service path. Normal FOC must call PROTECT_CheckFrame
     * from ADC1_2_IRQHandler; this function never constructs a fake frame. */
    if (!ADC_GetLatestFrame(&frame)) return;
    if (frame.status == ADC_FRAME_VALID || frame.status == ADC_FRAME_SERVICE_BUSY) {
        const int32_t vbus_mv = ADC_ReadVbusRegularMv();
        if (vbus_mv >= 0) protect_check_values(frame.idc1_ma, frame.idc2_ma, vbus_mv);
    }
}

int PROTECT_IsFault(void)
{
    return fault;
}

ProtectClearStatus PROTECT_RequestClear(void)
{
    int32_t vbus;
    int32_t idc1;
    int32_t idc2;

    if (!fault) return PROTECT_CLEAR_NOT_LATCHED;
    if (ADC_InjectedIsArmed()) return PROTECT_CLEAR_CONTROL_ACTIVE;
    /* Direct SD topology: a still-low SD line rejects recovery immediately.
     * Break evidence (BIF) is rechecked atomically in the commit block below;
     * a low SD or a pending break at commit time keeps the fault latched. */
    if (!PWM_SdLinesAreHigh()) {
        return PROTECT_CLEAR_VALUES_UNSAFE;
    }
    if (ADC_StartConversion() != 0) return PROTECT_CLEAR_SAMPLE_INVALID;

    /* With PWM/EN disabled, the only service sample is accepted solely for
     * recovery-window checking. It cannot re-enable PWM; an explicit FOC start
     * still needs fresh calibration, map and context admission. */
    vbus = ADC_GetVbus_mV();
    idc1 = ADC_GetI1_mA();
    idc2 = ADC_GetI2_mA();
    if (vbus < PROTECT_VBUS_MIN_MV || vbus > PROTECT_VBUS_MAX_MV) {
        return PROTECT_CLEAR_VALUES_UNSAFE;
    }
    if (protect_abs_i32(idc1) > PROTECT_I_MAX_MA / 2 ||
        protect_abs_i32(idc2) > PROTECT_I_MAX_MA / 2) {
        return PROTECT_CLEAR_VALUES_UNSAFE;
    }

    /* Commit is atomic with respect to break IRQ delivery. A hardware BKIN
     * event still removes MOE while IRQs are masked; BIF remains set and the
     * pending break ISR will relatch after the original PRIMASK is restored. */
    {
        const uint32_t primask = __get_PRIMASK();
        __disable_irq();
        if (!PWM_SdLinesAreHigh() ||
            ((TIM1->SR & (TIM_SR_BIF | TIM_SR_B2IF)) != 0u) ||
            ((TIM8->SR & (TIM_SR_BIF | TIM_SR_B2IF)) != 0u)) {
            __set_PRIMASK(primask);
            return PROTECT_CLEAR_VALUES_UNSAFE;
        }
#ifdef PWM_HOST_TEST
        /* Test-only pending-BKIN injection between PWM latch clear and central
         * fault clear. Production builds compile this hook out completely. */
        extern void PROTECT_HostClearCommitHook(void);
        PROTECT_HostClearCommitHook();
#endif
        fault = 0;
        vbus_over_count = 0u;
        fault_reason = PROTECT_FAULT_NONE;
        __DMB();
        __set_PRIMASK(primask);
    }
    return PROTECT_CLEAR_OK;
}

int PROTECT_GetFaultReason(void)
{
    return fault_reason;
}
