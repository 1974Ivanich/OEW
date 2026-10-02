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
int PROTECT_fault_inject;
int PROTECT_IsFault(void) { return PROTECT_fault_inject; }

/* Report-only invariants: no control admission, sample context stays
 * invalid. PWM_IsEnabled legitimately reads 1 while the vector is on
 * the pins (MOE up, healthy interlock) - the align session holds the
 * output state, it just never closes a control loop. */
static void assert_report_only(void)
{
    assert(!host_control_admission);
    assert(!host_window_valid);
    assert(!PWM_HasValidSampleContext());
}

/* Physical differential phase-leg duty of the OEW production basis.
 * TIM1 is PWM mode 1 (avg ~= CCR/ARR), TIM8 is PWM mode 2
 * (avg ~= 1 - CCR/ARR): Vdiff/Vbus ~= 2*CCR/ARR - 1 with identical,
 * mid-scaled CCRs on both timers. Per-mille of Vbus, signed. */
static int diff_permille(uint16_t ccr, uint16_t arr)
{
    return (int)(2u * (uint32_t)ccr) * 1000 / (int)arr - 1000;
}

/* Align vector u = -6%, v = w = +3%: phase U must differ from V and W. */
static void assert_physical_vector(uint16_t arr)
{
    assert(diff_permille(host_tim1.CCR1, arr) <= -55);
    assert(diff_permille(host_tim1.CCR1, arr) >= -65);
    assert(diff_permille(host_tim8.CCR2, arr) >= 25);
    assert(diff_permille(host_tim8.CCR2, arr) <= 35);
    assert(diff_permille(host_tim8.CCR3, arr) >= 25);
    assert(diff_permille(host_tim8.CCR3, arr) <= 35);
    assert(host_tim1.CCR2 == host_tim8.CCR2);
    assert(host_tim1.CCR3 == host_tim8.CCR3);
}

/* Healthy break/interlock state: BKE+OSSR+OSSI set, forbidden bits clear,
 * BKINE set, BKINP clear (AF1 bit 0 / bit 9, RM0440), SD lines (PB12, PD2)
 * read high. Mirrors what PWM_Init + a healthy board provide. */
static void set_healthy_interlock(void)
{
    host_tim1.BDTR |= TIM_BDTR_BKE | TIM_BDTR_OSSR | TIM_BDTR_OSSI;
    host_tim8.BDTR |= TIM_BDTR_BKE | TIM_BDTR_OSSR | TIM_BDTR_OSSI;
    host_tim1.AF1 |= 1u;
    host_tim8.AF1 |= 1u;
    host_gpiob.IDR |= (1u << 12);
    host_gpiod.IDR |= (1u << 2);
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
    host_primask = 0u;
    host_adc_armed = false;
    host_control_admission = true;
    host_window_valid = true;
    g_clock_fail = 0u;
}

int main(void)
{
    uint16_t v1[3];
    uint16_t v2[3];
    uint16_t bad1[3];
    uint16_t bad2[3];

    host_reset();
    PWM_Init();
    set_healthy_interlock();
    assert(PWM_AlignApertureStart(999u) == 0);
    assert(host_adc_armed);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert(host_tim1.ARR == 999u && host_tim8.ARR == 999u);
    assert(host_tim1.PSC == 16u && host_tim8.PSC == 16u);
    /* Between Start and SetVector the pins must be dead. */
    assert(host_tim1.CCER == 0u && host_tim8.CCER == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert(PWM_IsEnabled() == 0u); /* MOE still dead before SetVector */
    assert_report_only();

    /* Static differential vector in the production basis: identical,
     * mid-scaled CCRs on both timers; U differs from V/W physically. */
    v1[0] = 470u; v1[1] = 515u; v1[2] = 515u;
    v2[0] = 470u; v2[1] = 515u; v2[2] = 515u;
    assert(PWM_AlignApertureSetVector(v1, v2) == 0);
    assert(host_tim1.CCR1 == 470u && host_tim1.CCR2 == 515u && host_tim1.CCR3 == 515u);
    assert(host_tim8.CCR1 == 470u && host_tim8.CCR2 == 515u && host_tim8.CCR3 == 515u);
    assert(host_tim1.CCER == (TIM_CCER_CC1E | TIM_CCER_CC1NE |
                              TIM_CCER_CC2E | TIM_CCER_CC2NE |
                              TIM_CCER_CC3E | TIM_CCER_CC3NE));
    assert(host_tim8.CCER == host_tim1.CCER);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) != 0u);
    assert_physical_vector(999u);
    /* With the interlock healthy, the raised MOE is a real enabled
     * output state - held only by the aperture, no control loop. */
    assert(PWM_IsEnabled() == 1u);
    assert_report_only();

    /* Out-of-range CCR cannot modify the running vector or touch output bits. */
    bad1[0] = 1000u; bad1[1] = 515u; bad1[2] = 515u;
    bad2[0] = 470u; bad2[1] = 515u; bad2[2] = 515u;
    assert(PWM_AlignApertureSetVector(bad1, bad2) != 0);
    assert(host_tim1.CCR1 == 470u && host_tim8.CCR1 == 470u);
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
    assert(PWM_IsEnabled() == 0u);
    assert_report_only();

    /* SetVector outside an active session must be rejected. */
    assert(PWM_AlignApertureSetVector(v1, v2) != 0);
    assert(host_tim1.CCER == 0u && (host_tim1.BDTR & TIM_BDTR_MOE) == 0u);

    /* Admission gates: the align path must fail closed exactly like
     * PWM_Enable when PROTECT latches a fault, the clock monitor fires,
     * or the hardware interlock (SD lines / break) opens. */
    host_reset();
    PWM_Init();
    set_healthy_interlock();
    PROTECT_fault_inject = 1;
    assert(PWM_AlignApertureStart(999u) == -1);
    assert(!host_adc_armed);
    assert(host_tim1.CCER == 0u && (host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    PROTECT_fault_inject = 0;

    g_clock_fail = 1u;
    assert(PWM_AlignApertureStart(999u) == -1);
    assert(!host_adc_armed);
    g_clock_fail = 0u;

    host_gpiob.IDR &= ~(1u << 12);
    assert(PWM_AlignApertureStart(999u) == -1);
    assert(!host_adc_armed);
    host_gpiob.IDR |= (1u << 12);

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
