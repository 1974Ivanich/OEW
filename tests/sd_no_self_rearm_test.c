#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1; TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa; GPIO_TypeDef host_gpiob; GPIO_TypeDef host_gpioc; GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc; uint32_t SystemCoreClock = 170000000u; volatile uint8_t g_clock_fail;
static bool adc_armed; static bool protect_fault;
bool ADC_InjectedIsArmed(void) { return adc_armed; }
void ADC_InjectedStop(void) { adc_armed = false; }
void ADC_SetExpectedWindow(uint8_t s, uint8_t w, bool v) { (void)s; (void)w; (void)v; }
void ADC_SetControlAdmission(bool v) { (void)v; }
int PROTECT_IsFault(void) { return protect_fault ? 1 : 0; }
static void set_sd(bool a, bool b) { host_gpiob.IDR = a ? (1u << 12) : 0u; host_gpiod.IDR = b ? (1u << 2) : 0u; }
int main(void)
{
    PwmSampleContext context = { 0u, 0u, true };
    memset(&host_tim1, 0, sizeof(host_tim1)); memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa)); memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc)); memset(&host_gpiod, 0, sizeof(host_gpiod)); memset(&host_rcc, 0, sizeof(host_rcc));
    PWM_Init(); set_sd(true, true); assert(PWM_SetControlVector(0, 0, 0, &context)); adc_armed = true;
    assert(PWM_Enable() == PWM_ENABLE_OK);
    set_sd(false, true); host_tim1.SR |= TIM_SR_BIF; PWM_LatchBreakFault(); protect_fault = true; PWM_Disable();
    host_tim1.SR &= ~TIM_SR_BIF; set_sd(true, true);
    /* Restore every non-fault precondition; refusal must now be the retained
     * protection latch rather than context invalidation caused by Disable. */
    assert(PWM_SetControlVector(0, 0, 0, &context)); adc_armed = true;
    assert(PWM_Enable() == PWM_ENABLE_FAULT_LATCHED);
    assert(!PWM_IsEnabled()); assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    puts("sd_no_self_rearm_test: PASS"); return 0;
}
