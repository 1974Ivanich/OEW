#include "dac_debug.h"

/* Чистые функции DAC-моста: масштабирование и имена сигналов.
 * Без обращений к регистрам — собираются и проверяются hosted-тестом. */

static int32_t clamp_i32(int32_t v, int32_t lo, int32_t hi)
{
    if (v < lo) { return lo; }
    if (v > hi) { return hi; }
    return v;
}

/* Однополярный сигнал 0..full -> 0..4095. */
static uint16_t scale_unipolar(int32_t value, int32_t full)
{
    if (full <= 0) { return 0u; }
    const int32_t v = clamp_i32(value, 0, full);
    return (uint16_t)(((int64_t)v * DAC_DEBUG_FULL_SCALE) / full);
}

/* Двуполярный сигнал -limit..+limit -> 0..4095, ровно по краям шкалы:
 * -limit -> 0, 0 -> 2047, +limit -> 4095 (арифметика в int64, без переполнения). */
static uint16_t scale_bipolar(int32_t value, int32_t limit)
{
    if (limit <= 0) { return DAC_DEBUG_FULL_SCALE / 2u; }
    const int32_t v = clamp_i32(value, -limit, limit);
    return (uint16_t)(((int64_t)(v + limit) * DAC_DEBUG_FULL_SCALE) / ((int64_t)limit * 2));
}

uint16_t DacDebug_Scale(DacSignal sig, int32_t value)
{
    switch (sig) {
        case DAC_SIG_THETA:
            return scale_unipolar(value, DAC_DEBUG_THETA_MAX_MRAD);
        case DAC_SIG_ID:
        case DAC_SIG_IQ:
            return scale_bipolar(value, DAC_DEBUG_I_LIMIT_MA);
        case DAC_SIG_VBUS:
            return scale_unipolar(value, DAC_DEBUG_VBUS_LIMIT_MV);
        case DAC_SIG_SPEED:
            return scale_bipolar(value, DAC_DEBUG_SPEED_LIMIT_RPM);
        case DAC_SIG_OFF:
        case DAC_SIG_COUNT:
        default:
            return 0u;
    }
}

const char *DacDebug_SignalName(DacSignal sig)
{
    switch (sig) {
        case DAC_SIG_THETA: return "theta";
        case DAC_SIG_ID:    return "id";
        case DAC_SIG_IQ:    return "iq";
        case DAC_SIG_VBUS:  return "vbus";
        case DAC_SIG_SPEED: return "speed";
        case DAC_SIG_OFF:
        default:            return "off";
    }
}

static int name_eq(const char *a, const char *b)
{
    while (*a != '\0' && *b != '\0') {
        if (*a != *b) { return 0; }
        ++a;
        ++b;
    }
    return (*a == '\0') && (*b == '\0');
}

int DacDebug_SignalFromName(const char *name, DacSignal *out)
{
    if (name == 0 || out == 0) { return -1; }
    for (uint8_t i = 0u; i < (uint8_t)DAC_SIG_COUNT; i++) {
        if (name_eq(name, DacDebug_SignalName((DacSignal)i))) {
            *out = (DacSignal)i;
            return 0;
        }
    }
    return -2;
}
