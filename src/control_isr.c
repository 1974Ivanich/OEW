#include "control_isr.h"

void ControlISR_Handle(uint32_t events,
                       ControlIsrStats *stats,
                       const ControlIsrOps *ops)
{
    if ((stats == 0) || (ops == 0)) {
        return;
    }

    if ((events & CONTROL_ISR_EVT_OVR) != 0u) {
        stats->ovr_count++;
    }

    /* Queue overflow means the injected sequence can no longer be trusted.
     * Stop the active FOC session; do not consume JDR values as a valid sample. */
    if ((events & CONTROL_ISR_EVT_JQOVF) != 0u) {
        stats->jqovf_count++;
        if ((ops->foc_is_running != 0) && ops->foc_is_running()) {
            if (ops->foc_stop != 0) {
                ops->foc_stop();
            }
        }
        return;
    }

    if ((events & CONTROL_ISR_EVT_JEOS) == 0u) {
        return;
    }

    stats->jeos_count++;
    if (ops->adc_read_injected != 0) {
        ops->adc_read_injected();
    }

    if ((ops->foc_is_running == 0) || !ops->foc_is_running()) {
        stats->skipped_foc_count++;
        return;
    }

    /* A JEOS from the PWM cycle immediately preceding PWM_Disable is legal.
     * It must never execute another FOC step after CEN/MOE were removed. */
    if ((ops->pwm_is_enabled == 0) || !ops->pwm_is_enabled()) {
        stats->late_jeos_count++;
        return;
    }

    if (ops->protect_check != 0) {
        ops->protect_check();
    }

    if ((ops->protect_is_fault != 0) && ops->protect_is_fault()) {
        if (ops->foc_stop != 0) {
            ops->foc_stop();
        }
        return;
    }

    if (ops->foc_run != 0) {
        ops->foc_run();
    }
}
