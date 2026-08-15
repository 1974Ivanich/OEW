#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pwm.h"
#include "pwm_board_pins.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1; TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa; GPIO_TypeDef host_gpiob; GPIO_TypeDef host_gpioc; GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc; uint32_t SystemCoreClock = 170000000u; volatile uint8_t g_clock_fail;
bool ADC_InjectedIsArmed(void) { return false; }
void ADC_InjectedStop(void) { }
void ADC_SetExpectedWindow(uint8_t s, uint8_t w, bool v) { (void)s; (void)w; (void)v; }
void ADC_SetControlAdmission(bool v) { (void)v; }
int PROTECT_IsFault(void) { return 0; }

static void set_sd(bool sd1, bool sd2)
{
    host_gpiob.IDR = sd1 ? (1u << 12) : 0u;
    host_gpiod.IDR = sd2 ? (1u << 2) : 0u;
}
int main(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1)); memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa)); memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc)); memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));
    PWM_Init();
    set_sd(true, true); assert(PWM_SdLinesAreHigh()); assert(PWM_HardwareInterlockHealthy());
    set_sd(false, true); assert(!PWM_SdLinesAreHigh()); assert(!PWM_HardwareInterlockHealthy());
    set_sd(true, false); assert(!PWM_SdLinesAreHigh()); assert(!PWM_HardwareInterlockHealthy());
    set_sd(false, false); assert(!PWM_SdLinesAreHigh()); assert(!PWM_HardwareInterlockHealthy());
    puts("sd_interlock_test: PASS"); return 0;
}
