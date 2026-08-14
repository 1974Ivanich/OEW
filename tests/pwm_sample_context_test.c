#include <assert.h>
#include <stdbool.h>
#include <stdio.h>

#include "pwm.h"
#include "stm32g474xx.h"

extern void host_pwm_reset(void);
extern void host_set_fault(int value);
extern uint8_t host_window_sector;
extern uint8_t host_window_id;
extern bool host_window_valid;
extern volatile uint8_t g_clock_fail;

int main(void)
{
    PwmSampleContext context = { 3u, 1u, true };
    PwmSampleContext readback;

    host_pwm_reset();
    PWM_Init();
    assert(!PWM_HasValidSampleContext());
    assert(PWM_Enable() == PWM_ENABLE_CONTEXT_INVALID);
    assert((TIM1->CR1 & TIM_CR1_CEN) == 0u);

    assert(PWM_SetControlVector(1000, -2000, 3000, &context));
    assert(PWM_HasValidSampleContext());
    assert(host_window_sector == 3u);
    assert(host_window_id == 1u);
    assert(host_window_valid);
    assert(PWM_GetPendingSampleContext(&readback));
    assert(readback.sector == 3u && readback.window == 1u && readback.valid);

    assert(PWM_Enable() == PWM_ENABLE_OK);
    assert((TIM1->CR1 & TIM_CR1_CEN) != 0u);
    assert((TIM8->CR1 & TIM_CR1_CEN) != 0u);

    PWM_SetMod1(0, 0, 0);
    assert(!PWM_HasValidSampleContext());
    assert(!host_window_valid);
    PWM_Disable();

    assert(PWM_SetControlVector(0, 0, 0, &context));
    host_set_fault(1);
    assert(PWM_Enable() == PWM_ENABLE_FAULT_LATCHED);
    host_set_fault(0);
    g_clock_fail = 1u;
    assert(PWM_Enable() == PWM_ENABLE_CLOCK_FAILED);

    puts("pwm_sample_context_test: PASS");
    return 0;
}
