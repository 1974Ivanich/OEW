#include "autotune_math.h"
#include "cordic_math.h"
#include <string.h>
#include <limits.h>

int32_t AT_MathAbs32(int32_t value)
{
    if (value == INT32_MIN) return INT32_MAX;
    return value < 0 ? -value : value;
}

int32_t AT_SaneLs(int32_t l_uh)
{
    if (l_uh < AT_MATH_SANE_LS_MIN_UH || l_uh > AT_MATH_SANE_LS_MAX_UH) return 0;
    return l_uh;
}

int32_t AT_SaneRs(int32_t r_mohm)
{
    if (r_mohm < AT_MATH_SANE_RS_MIN_MOHM || r_mohm > AT_MATH_SANE_RS_MAX_MOHM) return 0;
    return r_mohm;
}

int32_t AT_MathSinQ15(int32_t angle_x1000)
{
    angle_x1000 %= AT_MATH_TWO_PI_X1000;
    if (angle_x1000 < 0) angle_x1000 += AT_MATH_TWO_PI_X1000;
    if (angle_x1000 > AT_MATH_PI_MRAD) angle_x1000 -= AT_MATH_TWO_PI_X1000;
    int32_t q31 = (int32_t)(((int64_t)angle_x1000 * 2147483647LL) / AT_MATH_PI_MRAD);
    int32_t sine, cosine;
    CORDIC_SinCos(q31, &sine, &cosine);
    return sine;
}

static void sort_small(int32_t *values, uint8_t count)
{
    for (uint8_t i = 1; i < count; ++i) {
        int32_t value = values[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && values[j] > value) {
            values[j + 1] = values[j];
            --j;
        }
        values[j + 1] = value;
    }
}

int32_t AT_MathMedianSmall(int32_t *values, uint8_t count)
{
    if (count == 0) return 0;
    sort_small(values, count);
    return values[count / 2];
}

void AT_MathCurveSortByCurrent(AtCurvePoint *curve, uint8_t count)
{
    for (uint8_t i = 1; i < count; ++i) {
        AtCurvePoint value = curve[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && curve[j].current_ma > value.current_ma) {
            curve[j + 1] = curve[j];
            --j;
        }
        curve[j + 1] = value;
    }
}

uint8_t AT_MathCurveFilterOutliers(AtCurvePoint *curve, uint8_t count)
{
    uint8_t valid = 0;
    for (uint8_t i = 0; i < count; ++i) {
        if (curve[i].inductance_uH > 0) curve[valid++] = curve[i];
    }
    if (valid < 5) return valid;

    AtCurvePoint filtered[AUTOTUNE_MAX_CURVE_POINTS];
    uint8_t kept = 0;
    for (uint8_t i = 0; i < valid; ++i) {
        int32_t window[5];
        uint8_t window_count = 0;
        for (int8_t offset = -2; offset <= 2; ++offset) {
            int16_t index = (int16_t)i + offset;
            if (index >= 0 && index < valid) window[window_count++] = curve[index].inductance_uH;
        }
        int32_t median = AT_MathMedianSmall(window, window_count);
        int32_t inductance = curve[i].inductance_uH;
        if (median > 0 && inductance >= median / 2 && inductance <= median * 2) {
            filtered[kept++] = curve[i];
        }
    }
    memcpy(curve, filtered, (size_t)kept * sizeof(curve[0]));
    return kept;
}

int32_t AT_MathCalcIsat(const AtCurvePoint *curve, uint8_t count,
                       int32_t l0_uH, int32_t threshold_pct)
{
    if (curve == 0 || count < 8 || l0_uH <= 0) return 0;
    int32_t threshold = l0_uH * threshold_pct / 100;
    uint8_t consecutive = 0;
    uint8_t first_below = 0xFF;
    for (uint8_t i = 0; i < count; ++i) {
        if (curve[i].inductance_uH <= threshold) {
            if (first_below == 0xFF) first_below = i;
            ++consecutive;
            if (consecutive >= 2) {
                if (first_below == 0) return curve[0].current_ma;
                uint8_t low = (uint8_t)(first_below - 1);
                uint8_t high = first_below;
                int32_t i_low = curve[low].current_ma;
                int32_t i_high = curve[high].current_ma;
                int32_t l_low = curve[low].inductance_uH;
                int32_t l_high = curve[high].inductance_uH;
                if (l_low == l_high) return i_low;
                return i_low + (int32_t)(((int64_t)(threshold - l_low) *
                                          (i_high - i_low)) / (l_high - l_low));
            }
        } else {
            consecutive = 0;
            first_below = 0xFF;
        }
    }
    return 0;
}

int32_t AT_LinearRegressionR(const int32_t *u_mv, const int32_t *i_ma, uint8_t count,
                             int32_t *out_r_mohm)
{
    if (u_mv == 0 || i_ma == 0 || out_r_mohm == 0 || count < 3u) {
        return -1;
    }

    int64_t sum_i = 0;
    int64_t sum_u = 0;
    for (uint8_t k = 0; k < count; k++) {
        sum_i += i_ma[k];
        sum_u += u_mv[k];
    }
    const int64_t mean_i = sum_i / count;
    const int64_t mean_u = sum_u / count;

    int64_t num = 0;
    int64_t den = 0;
    for (uint8_t k = 0; k < count; k++) {
        const int64_t di = (int64_t)i_ma[k] - mean_i;
        const int64_t du = (int64_t)u_mv[k] - mean_u;
        num += du * di;
        den += di * di;
    }

    if (den == 0) {
        return -2;
    }

    int64_t r_mohm = (num * 1000) / den;
    if (r_mohm > INT32_MAX) {
        r_mohm = INT32_MAX;
    } else if (r_mohm < INT32_MIN) {
        r_mohm = INT32_MIN;
    }
    *out_r_mohm = (int32_t)r_mohm;
    return 0;
}
