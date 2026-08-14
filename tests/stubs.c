#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
ADC_TypeDef host_adc2;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;
volatile uint8_t g_clock_fail;

static int host_fault;
static bool host_gates;
uint8_t host_window_sector;
uint8_t host_window_id;
bool host_window_valid;
uint32_t host_injected_stop_count;

void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    host_window_sector = sector;
    host_window_id = window;
    host_window_valid = valid;
}
void ADC_InjectedStop(void) { host_injected_stop_count++; }
bool ADC_InjectedIsArmed(void) { return false; }
int ADC_InjectedStart(void) { return 0; }
int PROTECT_IsFault(void) { return host_fault; }
void PWM_BoardPins_Init(void) { host_gates = false; }
void PWM_GatesEnable(void) { host_gates = true; }
void PWM_GatesDisable(void) { host_gates = false; }
bool PWM_GatesAreEnabled(void) { return host_gates; }

void host_pwm_reset(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_rcc, 0, sizeof(host_rcc));
    host_fault = 0;
    host_gates = false;
    host_window_sector = 0u;
    host_window_id = 0u;
    host_window_valid = false;
    host_injected_stop_count = 0u;
    g_clock_fail = 0u;
}
void host_set_fault(int value) { host_fault = value; }
