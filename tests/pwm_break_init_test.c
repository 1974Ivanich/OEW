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

bool ADC_InjectedIsArmed(void) { return false; }
void ADC_InjectedStop(void) { }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    (void)sector;
    (void)window;
    (void)valid;
}
void ADC_SetControlAdmission(bool enabled) { (void)enabled; }
int PROTECT_IsFault(void) { return 0; }

int main(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));

    PWM_Init();

    /* P0-A: hardware break source must request a CPU IRQ on both timers. */
    assert((host_tim1.DIER & TIM_DIER_BIE) != 0u);
    assert((host_tim8.DIER & TIM_DIER_BIE) != 0u);

    /* Direct SD low-active BKIN configuration; no BK2 and no auto-rearm. */
    assert((host_tim1.BDTR & TIM_BDTR_BKE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_BKE) != 0u);
    assert((host_tim1.BDTR & (TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE)) == 0u);
    assert((host_tim8.BDTR & (TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE)) == 0u);
    assert((host_tim1.AF1 & (1u << 0)) != 0u);
    assert((host_tim8.AF1 & (1u << 0)) != 0u);
    assert((host_tim1.AF1 & (1u << 9)) == 0u);
    assert((host_tim8.AF1 & (1u << 9)) == 0u);

    /* GPIO inputs are zeroed in this fixture: SD1/PB12 and SD2/PD2 low means
     * no physical permission, which must remain default-deny. */
    assert(!PWM_HardwareInterlockHealthy());
    assert(PWM_IsEnabled() == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) == 0u);
    /* Removed interposer pins PB4/PB5/PB11/PB13 are not configured/driven. */
    assert((host_gpiob.MODER & ((3u << (4u * 2u)) | (3u << (5u * 2u)) |
                                (3u << (11u * 2u)) | (3u << (13u * 2u)))) == 0u);

    puts("pwm_break_init_test: PASS");
    return 0;
}
