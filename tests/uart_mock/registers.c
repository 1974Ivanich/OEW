#include "stm32g474xx.h"
USART_TypeDef host_usart2;
GPIO_TypeDef host_gpioa;
RCC_TypeDef host_rcc;
uint32_t host_primask;
uint32_t SystemCoreClock = 170000000u;
volatile uint8_t g_autotune_abort;
