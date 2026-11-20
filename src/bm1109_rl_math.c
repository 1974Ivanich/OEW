#include "bm1109_rl_math.h"

#include <limits.h>

static int32_t clamp_i64_to_i32(int64_t value)
{
    if (value > INT32_MAX) return INT32_MAX;
    if (value < INT32_MIN) return INT32_MIN;
    return (int32_t)value;
}

int BM1109_RsFit(const BM1109_RsSample *samples, size_t count,
                 BM1109_RsResult *result)
{
    int64_t sum_i = 0, sum_v = 0;
    int64_t numerator = 0, denominator = 0;
    int32_t min_i, max_i;
    int64_t mean_i, mean_v;

    if (samples == 0 || result == 0 || count < 3u || count > 65535u)
        return -1;

    min_i = max_i = samples[0].current_mA;
    for (size_t k = 0; k < count; ++k) {
        sum_i += samples[k].current_mA;
        sum_v += samples[k].voltage_mV;
        if (samples[k].current_mA < min_i) min_i = samples[k].current_mA;
        if (samples[k].current_mA > max_i) max_i = samples[k].current_mA;
    }
    if (max_i == min_i) return -2;

    mean_i = sum_i / (int64_t)count;
    mean_v = sum_v / (int64_t)count;
    for (size_t k = 0; k < count; ++k) {
        const int64_t di = (int64_t)samples[k].current_mA - mean_i;
        const int64_t dv = (int64_t)samples[k].voltage_mV - mean_v;
        numerator += di * dv;
        denominator += di * di;
    }
    if (denominator == 0) return -2;

    /* (mV/mA) * 1000 = mOhm. */
    result->resistance_mOhm =
        clamp_i64_to_i32((numerator * 1000LL) / denominator);
    result->voltage_offset_mV =
        clamp_i64_to_i32(mean_v -
            ((int64_t)result->resistance_mOhm * mean_i) / 1000LL);
    result->current_span_mA = (uint32_t)((int64_t)max_i - min_i);
    result->sample_count = (uint16_t)count;
    if (result->resistance_mOhm <= 0) return -3;
    return 0;
}

int BM1109_LsFromStep(const BM1109_LsSample *samples, size_t count,
                      int32_t resistance_mOhm, BM1109_LsResult *result)
{
    int64_t integral_mV_us = 0;
    int64_t delta_i;
    uint32_t duration;

    if (samples == 0 || result == 0 || count < 2u || count > 65535u ||
        resistance_mOhm < 0)
        return -1;

    for (size_t k = 1; k < count; ++k) {
        const uint32_t dt = samples[k].time_us - samples[k - 1u].time_us;
        int64_t v0, v1, vavg;
        if (dt == 0u) return -2;
        /* Unsigned wrap/backwards timestamps are rejected. */
        if (samples[k].time_us < samples[k - 1u].time_us) return -2;

        v0 = (int64_t)samples[k - 1u].voltage_mV -
             ((int64_t)resistance_mOhm * samples[k - 1u].current_mA) / 1000LL;
        v1 = (int64_t)samples[k].voltage_mV -
             ((int64_t)resistance_mOhm * samples[k].current_mA) / 1000LL;
        vavg = (v0 + v1) / 2LL;
        if (dt > (uint32_t)(INT64_MAX / 2LL)) return -3;
        integral_mV_us += vavg * (int64_t)dt;
    }

    delta_i = (int64_t)samples[count - 1u].current_mA -
              samples[0].current_mA;
    if (delta_i == 0) return -3;

    /* mV * us / mA is numerically uH. */
    result->inductance_uH =
        clamp_i64_to_i32(integral_mV_us / delta_i);
    result->delta_current_mA = clamp_i64_to_i32(delta_i);
    duration = samples[count - 1u].time_us - samples[0].time_us;
    result->duration_us = duration;
    result->sample_count = (uint16_t)count;
    if (result->inductance_uH <= 0) return -4;
    return 0;
}
