/* T4: frame_sequence монотонна (инкремент на 1) при каждом publish.
 * Проверяет: valid кадр, error-кадр (OVR), desync-кадр.
 * Regression test: защищает от случайного пропуска инкремента sequence. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "adc.h"
#include "stm32g474xx.h"

/* Хост-реализация regular-read: при ADC_HOST_TEST adc.c зовёт её вместо
 * регистрового пути (та же форма, что в adc_frame_host_test.c и в
 * adc_regular_injected_guard_test.c). Этот тест regular не читает, но символ
 * обязан существовать, иначе сборка с -DADC_HOST_TEST не слинкуется. */
int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    (void)adc; (void)channel;
    if (out == 0) return -1;
    *out = 0u;
    return 0;
}

int main(void)
{
    memset(&host_adc1, 0, sizeof(host_adc1));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_adc12_common, 0, sizeof(host_adc12_common));
    memset(&host_rcc, 0, sizeof(host_rcc));
    memset(&host_dwt, 0, sizeof(host_dwt));

    /* См. adc_regular_injected_guard_test.c: предустановка ADEN ничего не
     * проверяет — мок не моделирует квитирование ADC, а ожидания в adc.c
     * возвращают успех на таймауте. */
    assert(ADC_Init() == 0);

    AdcFrame f;
    ADC_GetLatestFrame(&f);
    uint32_t seq = f.sequence; /* после Init это 1 (NOT_ARMED). */

    /* T4.1: Каждый successful injected publish увеличивает sequence на 1. */
    for (int i = 0; i < 100; i++) {
        host_adc1.ISR = ADC_ISR_JEOS;
        host_adc2.ISR = ADC_ISR_JEOS;
        host_adc1.JDR1 = 2041; host_adc2.JDR1 = 2073;
        host_adc2.JDR2 = 0; host_adc2.JDR3 = 600;
        assert(ADC_InjectedIrq());
        ADC_GetLatestFrame(&f);
        assert(f.sequence == seq + 1);
        seq++;
    }

    /* T4.2: Error-кадр (OVR) тоже увеличивает sequence. */
    host_adc1.ISR = ADC_ISR_OVR;
    host_adc2.ISR = 0;
    assert(ADC_InjectedIrq());
    ADC_GetLatestFrame(&f);
    assert(f.sequence == seq + 1);
    assert(f.status == ADC_FRAME_OVERRUN);
    seq++;

    /* T4.3: Desync (ADC1 JEOS=0, ADC2 JEOS=1) тоже увеличивает sequence. */
    host_adc1.ISR = 0;
    host_adc2.ISR = ADC_ISR_JEOS;
    assert(ADC_InjectedIrq());
    ADC_GetLatestFrame(&f);
    assert(f.sequence == seq + 1);
    assert(f.status == ADC_FRAME_DESYNCHRONIZED);
    seq++;

    /* T4.4: Последняя проверка — sequence строго +1 от начальной. */
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;
    host_adc1.JDR1 = 2041; host_adc2.JDR1 = 2073;
    host_adc2.JDR2 = 0; host_adc2.JDR3 = 600;
    assert(ADC_InjectedIrq());
    ADC_GetLatestFrame(&f);
    assert(f.sequence == seq + 1);

    /* Примечание: переполнение uint32_t (2^32 → 0) обрабатывается стандартным
     * поведением C (`++`). Монотонность понимается как строгое возрастание
     * по модулю 2^32. Тест проверяет инкремент на 1, что гарантирует
     * отсутствие пропусков и дублей sequence. */

    puts("adc_sequence_monotonic_test: PASS");
    return 0;
}
