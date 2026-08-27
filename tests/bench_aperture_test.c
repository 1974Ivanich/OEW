#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpiob;
GPIO_TypeDef host_gpioc;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;
volatile uint8_t g_clock_fail;
uint32_t host_primask;
extern unsigned int pwm_host_enable_call_count;

static bool host_adc_armed;
static bool host_control_admission;
static bool host_window_valid;

void HostIrqRestoreHook(uint32_t restored_primask) { (void)restored_primask; }
bool ADC_InjectedIsArmed(void) { return host_adc_armed; }
int ADC_InjectedStart(void)
{
    if (host_adc_armed) return -1;
    host_adc_armed = true;
    return 0;
}
void ADC_InjectedStop(void) { host_adc_armed = false; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    (void)sector;
    (void)window;
    host_window_valid = valid;
}
void ADC_SetControlAdmission(bool enabled) { host_control_admission = enabled; }
int PROTECT_IsFault(void) { return 0; }

static void host_reset(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));
    host_primask = 0u;
    host_adc_armed = false;
    host_control_admission = true;
    host_window_valid = true;
    g_clock_fail = 0u;
    pwm_host_enable_call_count = 0u;
}

static void assert_no_output(void)
{
    assert(host_tim1.CCER == 0u);
    assert(host_tim8.CCER == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert(PWM_IsEnabled() == 0u);
    assert(!host_control_admission);
    assert(!host_window_valid);
    assert(!PWM_HasValidSampleContext());
}

int main(void)
{
    host_reset();
    PWM_Init();

    /* Start force-clears stale output bits before and after CEN. */
    host_tim1.CCER = UINT32_MAX;
    host_tim8.CCER = UINT32_MAX;
    host_tim1.BDTR |= TIM_BDTR_MOE;
    host_tim8.BDTR |= TIM_BDTR_MOE;
    assert(PWM_BenchApertureStart(999u, 750u, 0u, 0u) == 0);
    assert(pwm_host_enable_call_count == 0u);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert(host_adc_armed);
    assert(host_tim1.ARR == 999u && host_tim8.ARR == 999u);
    assert(host_tim1.CCR1 == 750u && host_tim1.CCR2 == 0u && host_tim1.CCR3 == 0u);
    assert(host_tim8.CCR1 == 750u && host_tim8.CCR2 == 0u && host_tim8.CCR3 == 0u);
    assert_no_output();

    assert(PWM_BenchApertureSetVector(100u, 500u, 900u) == 0);
    assert(pwm_host_enable_call_count == 0u);
    assert(host_tim1.CCR1 == 100u && host_tim1.CCR2 == 500u && host_tim1.CCR3 == 900u);
    assert(host_tim8.CCR1 == 100u && host_tim8.CCR2 == 500u && host_tim8.CCR3 == 900u);
    assert_no_output();

    /* Invalid CCR cannot modify a running bench session or enable output. */
    assert(PWM_BenchApertureSetVector(1000u, 0u, 0u) != 0);
    assert(host_tim1.CCR1 == 100u && host_tim1.CCR2 == 500u && host_tim1.CCR3 == 900u);
    assert_no_output();

    /* Central terminal stop must end the bench session as well. */
    PWM_Disable();
    assert(PWM_BenchApertureSetVector(100u, 500u, 900u) != 0);
    assert(!host_adc_armed);
    assert_no_output();

    PWM_BenchApertureStop();
    assert(pwm_host_enable_call_count == 0u);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) == 0u);
    assert(!host_adc_armed);
    assert_no_output();

    puts("bench_aperture_test: PASS");
    return 0;
}
