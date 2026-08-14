#ifndef CONTROL_ISR_H
#define CONTROL_ISR_H

#include <stdbool.h>
#include <stdint.h>

/* Portable event bits. The STM32 adapter maps ADC_ISR_* flags to these bits. */
enum {
    CONTROL_ISR_EVT_OVR   = 1u << 0,
    CONTROL_ISR_EVT_JEOS  = 1u << 1,
    CONTROL_ISR_EVT_JQOVF = 1u << 2
};

typedef struct {
    bool (*foc_is_running)(void);
    bool (*pwm_is_enabled)(void);
    void (*adc_read_injected)(void);
    void (*protect_check)(void);
    bool (*protect_is_fault)(void);
    void (*foc_run)(void);
    void (*foc_stop)(void);
} ControlIsrOps;

typedef struct {
    uint32_t ovr_count;
    uint32_t jeos_count;
    uint32_t jqovf_count;
    uint32_t late_jeos_count;
    uint32_t skipped_foc_count;
} ControlIsrStats;

/*
 * Deterministic control-loop decision after ADC flags have been acknowledged
 * by the STM32-specific interrupt adapter.
 *
 * Ordering invariant:
 *  1. JQOVF is fatal for the active FOC sample and stops FOC immediately.
 *  2. JEOS data is read before PROTECT_Check.
 *  3. FOC_Run is called only when FOC and TIM1 PWM are still active.
 */
void ControlISR_Handle(uint32_t events,
                       ControlIsrStats *stats,
                       const ControlIsrOps *ops);

#endif /* CONTROL_ISR_H */
