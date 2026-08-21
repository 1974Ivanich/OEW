#include "stm32g474xx.h"
TIM_TypeDef host_tim2;
GPIO_TypeDef host_gpioa;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;
volatile uint32_t sys_tick_ms;
int FOC_IsRunning(void) { return 0; }
int VFC_IsRunning(void) { return 0; }
