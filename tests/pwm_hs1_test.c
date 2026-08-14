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

static bool host_adc_armed;
static bool host_protect_fault;
static uint8_t host_window_sector;
static uint8_t host_window_id;
static bool host_window_valid;
static bool host_control_admission;
static uint32_t host_adc_stop_count;

bool ADC_InjectedIsArmed(void) { return host_adc_armed; }
void ADC_InjectedStop(void) { host_adc_armed = false; ++host_adc_stop_count; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    host_window_sector = sector;
    host_window_id = window;
    host_window_valid = valid;
}
void ADC_SetControlAdmission(bool enabled) { host_control_admission = enabled; }
int PROTECT_IsFault(void) { return host_protect_fault ? 1 : 0; }

static void host_reset(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));
    host_adc_armed = false;
    host_protect_fault = false;
    host_window_sector = 0u;
    host_window_id = 0u;
    host_window_valid = false;
    host_control_admission = false;
    host_adc_stop_count = 0u;
    g_clock_fail = 0u;
}

static void host_set_interlock(bool safety_ok, bool tim1_bkin_high, bool tim8_bkin_high)
{
    const uint32_t b_inputs = (safety_ok ? (1u << 11) : 0u) |
                              (tim1_bkin_high ? (1u << 12) : 0u);
    host_gpiob.IDR = (host_gpiob.IDR & ~((1u << 11) | (1u << 12))) | b_inputs;
    if (tim8_bkin_high) {
        host_gpiod.IDR |= (1u << 2);
    } else {
        host_gpiod.IDR &= ~(1u << 2);
    }
}

int main(void)
{
    PwmSampleContext valid = { 3u, 1u, true };
    PwmSampleContext pending;
    PwmServiceCapturePattern pattern = {
        { 100u, 200u, 300u },
        { 300u, 200u, 100u },
        4u, 0u, 0x4F455731u
    };

    host_reset();
    PWM_Init();
    assert((host_tim1.BDTR & TIM_BDTR_BKE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_BKE) != 0u);
    assert((host_tim1.BDTR & (TIM_BDTR_BKP | TIM_BDTR_AOE | TIM_BDTR_BK2E)) == 0u);
    assert((host_tim8.BDTR & (TIM_BDTR_BKP | TIM_BDTR_AOE | TIM_BDTR_BK2E)) == 0u);
    assert((host_tim1.AF1 & (1u << 0)) != 0u && (host_tim1.AF1 & (1u << 9)) == 0u);
    assert((host_tim8.AF1 & (1u << 0)) != 0u && (host_tim8.AF1 & (1u << 9)) == 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == 0u);
    assert((host_gpiob.ODR & (1u << 13)) == 0u);
    assert(PWM_Enable() == PWM_ENABLE_CONTEXT_INVALID);

    assert(PWM_SetControlVector(1000, -1000, 2000, &valid));
    host_adc_armed = true;
    assert(PWM_Enable() == PWM_ENABLE_INTERLOCK_OPEN);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == 0u);

    host_set_interlock(true, true, true);
    assert(PWM_HardwareInterlockHealthy());
    assert(PWM_Enable() == PWM_ENABLE_OK);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == ((1u << 4) | (1u << 5)));
    assert(PWM_IsEnabled() == 1u);

    /* A primary or secondary break event invalidates live permission. */
    host_tim1.SR |= TIM_SR_BIF;
    assert(PWM_BreakFaultActive());
    assert(!PWM_HardwareInterlockHealthy());
    assert(PWM_IsEnabled() == 0u);
    PWM_Disable();
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == 0u);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) == 0u);
    assert(host_adc_stop_count == 1u);

    host_tim1.SR = 0u;
    host_adc_armed = true;
    host_control_admission = true;
    assert(PWM_ServiceEnable(&valid) == PWM_ENABLE_SERVICE_PROFILE_REQUIRED);
    assert(!host_window_valid);
    assert(PWM_ServiceCaptureStart(0) == PWM_ENABLE_SERVICE_PATTERN_INVALID);
    assert(PWM_ServiceCaptureStart(&pattern) == PWM_ENABLE_OK);
    assert(!host_control_admission);
    assert(!host_window_valid);
    assert(host_window_sector == 4u && host_window_id == 0u);
    assert(PWM_GetPendingSampleContext(&pending));
    assert(!pending.valid && pending.sector == 4u && pending.window == 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == ((1u << 4) | (1u << 5)));
    PWM_Disable();

    assert((host_gpiob.ODR & (1u << 13)) == 0u);
    PWM_HeartbeatToggle();
    assert((host_gpiob.ODR & (1u << 13)) != 0u);
    PWM_HeartbeatToggle();
    assert((host_gpiob.ODR & (1u << 13)) == 0u);

    host_set_interlock(false, true, true);
    host_adc_armed = true;
    assert(PWM_ServiceCaptureStart(&pattern) == PWM_ENABLE_INTERLOCK_OPEN);

    puts("pwm_hs1_test: PASS");
    return 0;
}
