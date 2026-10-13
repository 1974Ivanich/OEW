#include "stm32g474xx.h"

/* adc.c в firmware читает sys_tick_ms из main.c; в хост-сборках определяем здесь. */
volatile uint32_t sys_tick_ms = 0u;
#ifdef ADC_HOST_TEST
int ADC_HostTickMs(void) { return (int)sys_tick_ms; }
#endif

ADC_TypeDef host_adc1;
ADC_TypeDef host_adc2;
ADC_Common_TypeDef host_adc12_common;
RCC_TypeDef host_rcc;
DWT_Type host_dwt;

/* Общий мок для тестов ADC: adc.c спрашивает состояние ШИМ, чтобы понять,
 * обновляются ли боевые кадры. По умолчанию ШИМ выключена; тест может
 * выставить host_pwm_enabled = 1, чтобы проверить путь через боевой кадр. */
uint32_t host_pwm_enabled = 0u;
uint32_t PWM_IsEnabled(void) { return host_pwm_enabled; }
