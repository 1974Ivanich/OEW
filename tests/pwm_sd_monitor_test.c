#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pwm.h"
#include "stm32g474xx.h"

/* Built with -DOEW_SD_MONITOR_ONLY=1 -DOEW_HS1_COMMISSIONING_RELEASE=1. */
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

bool ADC_InjectedIsArmed(void) { return host_adc_armed; }
void ADC_InjectedStop(void) { host_adc_armed = false; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    host_window_sector = sector;
    host_window_id = window;
    host_window_valid = valid;
}
void ADC_SetControlAdmission(bool enabled) { host_control_admission = enabled; }
int PROTECT_IsFault(void) { return host_protect_fault ? 1 : 0; }

static void host_set_sd_lines(bool sd1_high, bool sd2_high)
{
    host_gpiob.IDR = (host_gpiob.IDR & ~(1u << 12)) |
                      (sd1_high ? (1u << 12) : 0u);
    host_gpiod.IDR = (host_gpiod.IDR & ~(1u << 2)) |
                      (sd2_high ? (1u << 2) : 0u);
}

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
    g_clock_fail = 0u;
}

int main(void)
{
    PwmSampleContext valid = { 3u, 1u, true };

    host_reset();
    PWM_Init();

    /* Monitor-only: BKIN break deliberately disabled (BKE=0), no BK2/AOE/BKP. */
    assert((host_tim1.BDTR & TIM_BDTR_BKE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_BKE) == 0u);
    assert((host_tim1.BDTR & (TIM_BDTR_BKP | TIM_BDTR_AOE | TIM_BDTR_BK2E)) == 0u);
    assert((host_tim8.BDTR & (TIM_BDTR_BKP | TIM_BDTR_AOE | TIM_BDTR_BK2E)) == 0u);
    assert((host_tim1.BDTR & (TIM_BDTR_OSSR | TIM_BDTR_OSSI)) == (TIM_BDTR_OSSR | TIM_BDTR_OSSI));
    assert((host_tim8.BDTR & (TIM_BDTR_OSSR | TIM_BDTR_OSSI)) == (TIM_BDTR_OSSR | TIM_BDTR_OSSI));

    /* A low SD line is an event to log, not a break fault. */
    host_set_sd_lines(false, false);
    assert(!PWM_BreakFaultActive());

    /* Start interlock still requires SD high (do not start into a fault). */
    assert(!PWM_HardwareInterlockHealthy());
    host_set_sd_lines(true, true);
    assert(PWM_HardwareInterlockHealthy());

    /* Normal enable still works in monitor-only mode. */
    assert(PWM_SetControlVector(1000, -1000, 2000, &valid));
    host_adc_armed = true;
    assert(PWM_Enable() == PWM_ENABLE_OK);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);
    assert(PWM_IsEnabled() == 1u);
    PWM_Disable();

    puts("pwm_sd_monitor_test: PASS");
    return 0;
}
