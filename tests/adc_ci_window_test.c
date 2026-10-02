/* Hosted-тест окна сбора калибровочных данных шунта (команда ci).
 * Парный к ADC_CiCollectWindow (src/adc.c): среднее/мин/макс по ровно
 * ADC_CI_WINDOW_SAMPLES сэмплам, отказ по чужому каналу и null out,
 * статические окна dt_ms=0 при замороженном тике. */
#include <stdio.h>
#include "adc.h"
#include "stm32g474xx.h"

static int fails;

/* Хук хост-сборки ADC (тот же паттерн, что tests/adc_frame_host_test.c). */
int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    static uint16_t cnt1, cnt2;

    if (out == 0) return -1;
    if (adc == ADC1 && channel == 1u) {
        *out = (uint16_t)(99u + (cnt1++ % 3u));       /* 99..101, среднее 100 */
        return 0;
    }
    if (adc == ADC2 && channel == 2u) {
        *out = (uint16_t)(199u + (cnt2++ % 3u));      /* 199..201, среднее 200 */
        return 0;
    }
    if (adc == ADC2 && channel == 5u) {
        *out = 1234u;
        return 0;
    }
    return -1;
}

/* Замороженный тик -> детерминированное окно; dt_ms обязан быть 0.
 * ADC_HostTickMs предоставляется tests/mocks_adc/registers.c (ADC_HOST_TEST). */

static void expect_eq(const char *what, long got, long want)
{
    if (got != want) {
        printf("FAIL %s: got %ld want %ld\n", what, got, want);
        fails++;
    } else {
        printf("ok   %s = %ld\n", what, got);
    }
}

int main(void)
{
    ADC_CiWindow w;

    /* Окно Inv1: ровно 256 сэмплов 99..101, среднее 100. */
    expect_eq("Inv1 rc", ADC_CiCollectWindow(1u, &w), 0);
    expect_eq("Inv1 samples", (long)w.samples, (long)ADC_CI_WINDOW_SAMPLES);
    expect_eq("Inv1 avg", (long)w.raw_avg, 100L);
    expect_eq("Inv1 min", (long)w.raw_min, 99L);
    expect_eq("Inv1 max", (long)w.raw_max, 101L);
    expect_eq("Inv1 dt_ms (замороженный тик)", (long)w.dt_ms, 0L);
    expect_eq("Inv1 vbus_mv", (long)w.vbus_mv, (long)ADC_VbusMvFromRaw(1234u, 0u));

    /* Окно Inv2: ровно 256 сэмплов 199..201, среднее 200. */
    expect_eq("Inv2 rc", ADC_CiCollectWindow(2u, &w), 0);
    expect_eq("Inv2 samples", (long)w.samples, (long)ADC_CI_WINDOW_SAMPLES);
    expect_eq("Inv2 avg", (long)w.raw_avg, 200L);
    expect_eq("Inv2 min", (long)w.raw_min, 199L);
    expect_eq("Inv2 max", (long)w.raw_max, 201L);

    /* Чужой канал и null out отклоняются. */
    expect_eq("channel 3 rejected", ADC_CiCollectWindow(3u, &w), -1);
    expect_eq("channel 0 rejected", ADC_CiCollectWindow(0u, &w), -1);
    expect_eq("null out rejected", ADC_CiCollectWindow(1u, 0), -1);

    if (fails != 0) {
        printf("CI WINDOW: %d FAILED\n", fails);
        return 1;
    }
    printf("CI WINDOW: ALL PASS\n");
    return 0;
}
