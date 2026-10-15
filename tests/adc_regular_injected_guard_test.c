/* T1: regular-путь блокируется при armed injected (JADSTART=1 на ADC1).
 * Characterization test: проверяет, что все публичные regular-функции
 * возвращают -1 и не публикуют кадр при вооружённой injected. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "adc.h"
#include "stm32g474xx.h"

/* Мок regular-read (копия из adc_frame_host_test.c). */
static uint16_t host_regular_i1;
static uint16_t host_regular_i2;
static uint16_t host_regular_ires;
static uint16_t host_regular_vbus;
static int host_regular_vbus_fail;

int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    if (out == 0) return -1;
    if (adc == ADC1 && channel == 1u) { *out = host_regular_i1; return 0; }
    if (adc == ADC2 && channel == 2u) { *out = host_regular_i2; return 0; }
    if (adc == ADC2 && channel == 3u) { *out = host_regular_ires; return 0; }
    if (adc == ADC2 && channel == 5u) {
        if (host_regular_vbus_fail) return -1;
        *out = host_regular_vbus; return 0;
    }
    return -1;
}

int main(void)
{
    /* Инициализация моков. */
    memset(&host_adc1, 0, sizeof(host_adc1));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_adc12_common, 0, sizeof(host_adc12_common));
    memset(&host_rcc, 0, sizeof(host_rcc));
    memset(&host_dwt, 0, sizeof(host_dwt));

    /* Регистры обнулены — как после сброса. Предустановка ADEN здесь ничего
     * не проверяет и ничего не ломает: мок не моделирует ни сброс ADEN по
     * ADDIS, ни ADCAL, ни ADRDY, а ожидания в adc.c (safety-модуль) на
     * таймауте возвращают успех — `n--` при n == 0 уходит в 0xFFFFFFFF, и
     * проверка `n == 0u` перед `return -1` не срабатывает никогда. То есть
     * хост-тест не отличает «ADC выключился» от «не выключился»; проверка
     * «ADC_Init() на уже включённом ADC» требует мока с квитированием —
     * отдельная задача, не этот тест. */
    assert(ADC_Init() == 0);

    AdcFrame f;
    ADC_GetLatestFrame(&f);
    uint32_t seq0 = f.sequence;

    /* T1.1: ADC_StartConversion() при armed injected → −1, sequence не растёт. */
    host_adc1.CR |= ADC_CR_JADSTART; /* armed */
    assert(ADC_StartConversion() == -1);
    ADC_GetLatestFrame(&f);
    assert(f.sequence == seq0);

    /* T1.2: ADC_ReadVbusRegularMv() при armed → −1. */
    assert(ADC_ReadVbusRegularMv() == -1);

    /* T1.3: ADC_CalibrateOffsets() при armed → −1. */
    assert(ADC_CalibrateOffsets() == -1);

    /* T1.4: ADC_CiCollectWindow() при armed → −1. */
    ADC_CiWindow ci;
    assert(ADC_CiCollectWindow(2, &ci) == -1);

    /* T1.5: ADC_CalibrateVbusOffset() при armed → −1. */
    assert(ADC_CalibrateVbusOffset() == -1);

    /* T1.6: После stop injected regular-путь восстанавливается. */
    host_adc1.CR &= ~ADC_CR_JADSTART;
    host_regular_i1 = 2041; host_regular_i2 = 2073;
    host_regular_ires = 0; host_regular_vbus = 600;

    assert(ADC_StartConversion() == 0);
    ADC_GetLatestFrame(&f);
    assert(f.sequence == seq0 + 1);
    assert(f.raw_vbus == 600u);
    assert(f.status == ADC_FRAME_SERVICE_BUSY);

    puts("adc_regular_injected_guard_test: PASS");
    return 0;
}
