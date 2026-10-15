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

/* Квитирование «железа» ADC: мок регистров не меняет флаги сам, поэтому это
 * делает хук, который adc.c зовёт из циклов ожидания (см. src/adc.c и
 * docs/tz/TZ_ADC_WAIT_TIMEOUT.md). host_adc_wait_dead = 1 заставляет хук
 * молчать — так проверяется таймаут (tests/adc_wait_timeout_test.c). */
uint32_t host_adc_wait_dead = 0u;

void ADC_HostWaitHook(volatile uint32_t *reg, uint32_t mask)
{
    if (host_adc_wait_dead != 0u) return;

    if (mask == ADC_CR_ADEN) {              /* ADDIS снял ADEN */
        *reg &= ~(ADC_CR_ADEN | ADC_CR_ADDIS);
    } else if (mask == ADC_CR_ADCAL) {      /* калибровка завершилась */
        *reg &= ~ADC_CR_ADCAL;
    } else if (mask == ADC_ISR_ADRDY) {     /* ADC готов */
        *reg |= ADC_ISR_ADRDY;
    } else if (mask == ADC_CR_ADSTP || mask == ADC_CR_JADSTP) {
        *reg &= ~mask;                      /* останов преобразований завершён */
    }
}
