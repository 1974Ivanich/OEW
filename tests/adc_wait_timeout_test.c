/* Таймаут ожиданий ADC (ТЗ docs/tz/TZ_ADC_WAIT_TIMEOUT.md).
 *
 * adc_wait_set/adc_wait_clear обязаны различать «флаг изменился» и «флаг не
 * дождались». До правки они возвращали 0 в обоих случаях (счётчик уходил в
 * 0xFFFFFFFF, и проверка `n == 0u` не срабатывала), поэтому несостоявшаяся
 * калибровка (ADCAL не снялся) или несостоявшееся включение (ADRDY не пришёл)
 * читались как успех — то есть fail-closed путь main.c при отказе ADC был
 * недостижим.
 *
 * Проверяется через публичную поверхность (ADC_Init / ADC_InjectedInit), а не
 * через статические функции: мок молчит по флагу host_adc_wait_dead.
 *
 * Проверка обратным ходом: на ревизии до правки этот файл падает на первом
 * assert пункта 2 (ADC_Init() возвращает 0 вместо -1). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "adc.h"
#include "stm32g474xx.h"

extern uint32_t host_adc_wait_dead;   /* мок: «железо не отвечает» */

/* adc.c при ADC_HOST_TEST зовёт хост-реализацию regular-read. Этот тест её не
 * использует: заглушка нужна только для линковки. */
int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    (void)adc; (void)channel; (void)out;
    return -1;
}

static void reset_regs(void)
{
    memset(&host_adc1, 0, sizeof(host_adc1));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_adc12_common, 0, sizeof(host_adc12_common));
    memset(&host_rcc, 0, sizeof(host_rcc));
    memset(&host_dwt, 0, sizeof(host_dwt));
}

int main(void)
{
    /* 1. Квитирование наступает: обе функции проходят. */
    reset_regs();
    host_adc_wait_dead = 0u;
    assert(ADC_Init() == 0);
    assert(ADC_InjectedInit() == 0);

    /* 2. ADCAL не снялся (калибровка не завершилась) → ADC_Init обязан
     *    отказать, а не продолжить на некалиброванном АЦП. */
    reset_regs();
    host_adc_wait_dead = 1u;
    assert(ADC_Init() == -1);

    /* 3. ADEN не снялся по ADDIS → ADC_Init обязан отказать. */
    reset_regs();
    host_adc1.CR = ADC_CR_ADEN;
    host_adc2.CR = ADC_CR_ADEN;
    host_adc_wait_dead = 1u;
    assert(ADC_Init() == -1);

    /* 4. Ветка ADRDY (`adc_wait_set`) на этом моке не проверяется: мок не
     *    моделирует W1C — запись `ISR = ADC_ISR_ADRDY` в adc_enable снимает
     *    флаг на железе, а в моке ВЫСТАВЛЯЕТ его, поэтому ожидание готовности
     *    удовлетворяется ещё до входа в цикл. Здесь фиксируется именно это
     *    состояние: если мок научат W1C, проверка ниже начнёт падать — тогда её
     *    надо заменить на ожидание -1 (см. ТЗ, раздел «Смежное»). */
    reset_regs();
    host_adc_wait_dead = 0u;
    assert(ADC_Init() == 0);
    host_adc_wait_dead = 1u;
    assert(ADC_InjectedInit() == 0);   /* мок не умеет W1C — см. комментарий */

    /* 5. То же для типового пути сброса флага: ADEN не снялся по ADDIS (пункт 3)
     *    уже покрыт; здесь проверяется, что «мёртвый» мок не оставляет ADC в
     *    состоянии, которое следующий ADC_Init принимает за норму. */
    host_adc_wait_dead = 0u;
    reset_regs();
    assert(ADC_Init() == 0);
    assert(ADC_InjectedInit() == 0);

    puts("adc_wait_timeout_test: PASS");
    return 0;
}
