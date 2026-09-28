/* Hosted-тест: преобразование VBUS raw -> мВ со смещением и насыщением.
 * Парный к ADC_VbusMvFromRaw (src/adc.c); поведение при offset = 0 обязано
 * совпадать с прежней формулой raw * 3300 * 125 / 4095. */
#include <stdio.h>
#include "adc.h"

static int fails;

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
    /* без смещения: тождественно прежней формуле */
    expect_eq("raw=595 off=0 (60 V)", ADC_VbusMvFromRaw(595u, 0u), (595L * 3300L * 125L) / 4095L);
    expect_eq("raw=4095 off=0", ADC_VbusMvFromRaw(4095u, 0u), (4095L * 3300L * 125L) / 4095L);
    expect_eq("raw=0 off=0", ADC_VbusMvFromRaw(0u, 0u), 0L);

    /* измеренное смещение канала стенда: 22 отсч (2.215 В) */
    expect_eq("raw=416 off=22 (40.0 V)", ADC_VbusMvFromRaw(416u, 22u), (394L * 125L * 3300L) / 4095L);
    expect_eq("raw=419 off=22", ADC_VbusMvFromRaw(419u, 22u), (397L * 125L * 3300L) / 4095L);

    /* raw <= offset: ноль снизу, без unsigned underflow */
    expect_eq("raw=22 off=22 (ноль)", ADC_VbusMvFromRaw(22u, 22u), 0L);
    expect_eq("raw=20 off=22 (ниже смещения)", ADC_VbusMvFromRaw(20u, 22u), 0L);
    expect_eq("raw=0 off=22", ADC_VbusMvFromRaw(0u, 22u), 0L);

    /* монотонность по raw при фиксированном смещении */
    {
        long prev = ADC_VbusMvFromRaw(30u, 22u);
        for (uint32_t raw = 31u; raw < 1200u; raw += 37u) {
            long cur = ADC_VbusMvFromRaw((uint16_t)raw, 22u);
            if (cur < prev) {
                printf("FAIL немонотонность при raw=%u\n", raw);
                fails++;
                break;
            }
            prev = cur;
        }
        printf("ok   монотонность 30..1200\n");
    }

    /* до калибровки смещение не задано: getter 0, флаг снят */
    expect_eq("ADC_GetOffsetVbus() до калибровки", ADC_GetOffsetVbus(), 0L);
    expect_eq("ADC_VbusOffsetIsValid() до калибровки", ADC_VbusOffsetIsValid() ? 1L : 0L, 0L);

    if (fails != 0) {
        printf("VBUS SCALE: %d FAILED\n", fails);
        return 1;
    }
    printf("VBUS SCALE: ALL PASS\n");
    return 0;
}
