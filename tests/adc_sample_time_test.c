#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "adc.h"
#include "stm32g474xx.h"

/* Каналы и хост-заглушка regular-read: при ADC_HOST_TEST adc.c зовёт её вместо
 * регистрового пути (см. src/adc.c). Этот тест regular не читает — заглушка
 * нужна только для линковки, а возврат -1 делает случайное чтение заметным. */
#define CH_SHUNT1   1u
#define CH_SHUNT2   2u
#define CH_CT        3u
#define CH_VBUS      5u

int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    (void)adc; (void)channel; (void)out;
    return -1;
}

static uint32_t smp_mask(uint32_t channel)
{
    return 7u << (channel * 3u);
}

int main(void)
{
    memset(&host_adc1, 0, sizeof(host_adc1));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_adc12_common, 0, sizeof(host_adc12_common));
    memset(&host_rcc, 0, sizeof(host_rcc));
    memset(&host_dwt, 0, sizeof(host_dwt));

    assert(ADC_Init() == 0);

    /* ADC1: only shunt1 (channel 1) must have SMP=111. */
    assert((host_adc1.SMPR1 & smp_mask(CH_SHUNT1)) == smp_mask(CH_SHUNT1));

    /* ADC2: shunt2 (ch2), CT (ch3), Vbus (ch5) must all have SMP=111. */
    assert((host_adc2.SMPR1 & smp_mask(CH_SHUNT2)) == smp_mask(CH_SHUNT2));
    assert((host_adc2.SMPR1 & smp_mask(CH_CT))     == smp_mask(CH_CT));
    assert((host_adc2.SMPR1 & smp_mask(CH_VBUS))   == smp_mask(CH_VBUS));

    /* Channels not in use must remain at 0 (1.5 cycles). */
    assert((host_adc1.SMPR1 & smp_mask(0u)) == 0u);
    assert((host_adc2.SMPR1 & smp_mask(0u)) == 0u);
    assert((host_adc1.SMPR2) == 0u);
    assert((host_adc2.SMPR2) == 0u);

    puts("adc_sample_time_test: PASS");
    return 0;
}
