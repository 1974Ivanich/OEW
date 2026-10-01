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
}

/* Report-only invariants: admission stays false, sample context stays
 * invalid, the align session itself never counts as PWM_IsEnabled. */
static void assert_report_only(void)
{
    assert(!host_control_admission);
    assert(!host_window_valid);
    assert(!PWM_HasValidSampleContext());
    assert(PWM_IsEnabled() == 0u);
}

int main(void)
{
    uint16_t v1[3];
    uint16_t v2[3];
    uint16_t bad1[3];
    uint16_t bad2[3];

    host_reset();
    PWM_Init();
    assert(PWM_AlignApertureStart(999u) == 0);
    assert(host_adc_armed);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert(host_tim1.ARR == 999u && host_tim8.ARR == 999u);
    /* Between Start and SetVector the pins must be dead. */
    assert(host_tim1.CCER == 0u && host_tim8.CCER == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert_report_only();

    /* Static differential vector (open winding): TIM1 low / TIM8 high. */
    v1[0] = 439u; v1[1] = 559u; v1[2] = 559u;
    v2[0] = 559u; v2[1] = 439u; v2[2] = 439u;
    assert(PWM_AlignApertureSetVector(v1, v2) == 0);
    assert(host_tim1.CCR1 == 439u && host_tim1.CCR2 == 559u && host_tim1.CCR3 == 559u);
    assert(host_tim8.CCR1 == 559u && host_tim8.CCR2 == 439u && host_tim8.CCR3 == 439u);
    assert(host_tim1.CCER == (TIM_CCER_CC1E | TIM_CCER_CC1NE |
                              TIM_CCER_CC2E | TIM_CCER_CC2NE |
                              TIM_CCER_CC3E | TIM_CCER_CC3NE));
    assert(host_tim8.CCER == host_tim1.CCER);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) != 0u);
    assert_report_only();

    /* Out-of-range CCR cannot modify the running vector or touch output bits. */
    bad1[0] = 1000u; bad1[1] = 559u; bad1[2] = 559u;
    bad2[0] = 559u; bad2[1] = 439u; bad2[2] = 439u;
    assert(PWM_AlignApertureSetVector(bad1, bad2) != 0);
    assert(host_tim1.CCR1 == 439u && host_tim8.CCR1 == 559u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);

    /* The align session is not the control path: PWM_Enable stays blocked
     * (no valid sample context), only Stop returns to the safe idle. */
    assert(PWM_Enable() != PWM_ENABLE_OK);
    assert_report_only();

    PWM_AlignApertureStop();
    assert(!host_adc_armed);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) == 0u);
    assert(host_tim1.CCER == 0u && host_tim8.CCER == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert_report_only();

    /* SetVector outside an active session must be rejected. */
    assert(PWM_AlignApertureSetVector(v1, v2) != 0);
    assert(host_tim1.CCER == 0u && (host_tim1.BDTR & TIM_BDTR_MOE) == 0u);

    /* Restart works and re-arms; the terminal stop (PWM_Disable) must also
     * end the session: CCRs can no longer be changed afterwards. */
    assert(PWM_AlignApertureStart(999u) == 0);
    assert(host_adc_armed);
    PWM_Disable();
    assert(!host_adc_armed);
    assert(host_tim1.CCER == 0u && host_tim8.CCER == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert(PWM_AlignApertureSetVector(v1, v2) != 0);
    assert_report_only();

    puts("pwm_align_test: PASS");
    return 0;
}
