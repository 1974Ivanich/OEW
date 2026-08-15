#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "protect.h"
#include "../src/adc.h"
#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1; TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa; GPIO_TypeDef host_gpiob; GPIO_TypeDef host_gpioc; GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc; uint32_t SystemCoreClock = 170000000u; volatile uint8_t g_clock_fail;
static bool injected_armed;
uint32_t host_primask;
static bool inject_break_on_clear_commit;

void PROTECT_HostClearCommitHook(void)
{
    if (inject_break_on_clear_commit) {
        host_tim1.SR |= TIM_SR_BIF; /* completed short SD pulse while IRQ masked */
    }
}

void HostIrqRestoreHook(uint32_t restored_primask)
{
    if ((restored_primask == 0u) && inject_break_on_clear_commit) {
        inject_break_on_clear_commit = false;
        PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
    }
}

bool ADC_GetLatestFrame(AdcFrame *out) { (void)out; return false; }
bool ADC_InjectedIsArmed(void) { return injected_armed; }
void ADC_InjectedStop(void) { injected_armed = false; }
void ADC_SetExpectedWindow(uint8_t s, uint8_t w, bool v) { (void)s; (void)w; (void)v; }
void ADC_SetControlAdmission(bool v) { (void)v; }
int ADC_StartConversion(void) { return 0; }
int32_t ADC_GetVbus_mV(void) { return 24000; }
int32_t ADC_GetI1_mA(void) { return 0; }
int32_t ADC_GetI2_mA(void) { return 0; }

static void set_sd(bool sd1, bool sd2)
{
    host_gpiob.IDR = sd1 ? (1u << 12) : 0u;
    host_gpiod.IDR = sd2 ? (1u << 2) : 0u;
}
static void reset_all(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1)); memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa)); memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc)); memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc)); injected_armed = false;
    host_primask = 0u; inject_break_on_clear_commit = false;
    PWM_Init(); PROTECT_Init();
}
int main(void)
{
    reset_all(); set_sd(true, true);
    set_sd(false, true); host_tim1.SR |= TIM_SR_BIF;
    PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
    assert(PROTECT_IsFault()); assert(PWM_BreakFaultActive());
    host_tim1.SR &= ~TIM_SR_BIF; set_sd(true, true);
    assert(PWM_BreakFaultActive());
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_OK);
    assert(!PROTECT_IsFault()); assert(!PWM_BreakFaultActive());

    set_sd(false, true); host_tim1.SR |= TIM_SR_BIF;
    PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_VALUES_UNSAFE);
    host_tim1.SR &= ~TIM_SR_BIF; set_sd(true, true);
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_OK);

    /* P0: an SD pulse that completes after PWM latch clear but before central
     * fault clear must be delivered once PRIMASK is restored and re-latch. */
    set_sd(false, true); host_tim1.SR |= TIM_SR_BIF;
    PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
    host_tim1.SR &= ~TIM_SR_BIF; set_sd(true, true);
    inject_break_on_clear_commit = true;
    assert(PROTECT_RequestClear() == PROTECT_CLEAR_OK);
    assert(PROTECT_IsFault());
    assert(PWM_BreakFaultActive());
    assert(host_primask == 0u);

    puts("sd_latch_test: PASS"); return 0;
}
