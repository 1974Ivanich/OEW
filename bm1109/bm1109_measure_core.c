#include "bm1109_measure_core.h"

#include <math.h>

/* ---------------------------------------------------------------------------
 *  МНК для U = R*I + U0.
 *
 *  R — наклон, U0 — свободный член. Именно наклон является сопротивлением:
 *  постоянные падения на ключах и потери на dead time складываются в U0 и
 *  на R не влияют.
 * ------------------------------------------------------------------------- */
bm1109_status bm1109_fit_resistance(const bm1109_point *pts, uint8_t n,
                                    bm1109_r_result *out)
{
    float si = 0.0f, su = 0.0f, sii = 0.0f, siu = 0.0f;
    float i_min, i_max, r, u0, ss = 0.0f;
    uint8_t k;

    if (pts == 0 || out == 0 || n < 2U) {
        return BM1109_ERR_ARG;
    }

    for (k = 0U; k < n; k++) {
        if (!(pts[k].i_a > 0.0f) || !(pts[k].u_v > 0.0f)) {
            return BM1109_ERR_NO_CURRENT;
        }
    }

    i_min = pts[0].i_a;
    i_max = pts[0].i_a;
    for (k = 0U; k < n; k++) {
        si += pts[k].i_a;
        su += pts[k].u_v;
        sii += pts[k].i_a * pts[k].i_a;
        siu += pts[k].i_a * pts[k].u_v;
        if (pts[k].i_a < i_min) i_min = pts[k].i_a;
        if (pts[k].i_a > i_max) i_max = pts[k].i_a;
    }

    /* Размах по току должен быть заметным, иначе наклон определяется шумом. */
    if (!((i_max - i_min) > 0.05f * i_max)) {
        return BM1109_ERR_NOT_LINEAR;
    }

    {
        float den = (float)n * sii - si * si;
        if (!(den > 0.0f)) {
            return BM1109_ERR_NOT_LINEAR;
        }
        r  = ((float)n * siu - si * su) / den;
        u0 = (su - r * si) / (float)n;
    }

    if (!(r > 0.0f)) {
        return BM1109_ERR_NOT_LINEAR;
    }

    for (k = 0U; k < n; k++) {
        float err = pts[k].u_v - (r * pts[k].i_a + u0);
        ss += err * err;
    }

    out->r_ohm        = r;
    out->u_offset_v   = u0;
    out->r_single_ohm = pts[n - 1U].u_v / pts[n - 1U].i_a;
    out->rms_v        = sqrtf(ss / (float)n);
    out->i_span_a     = i_max - i_min;
    out->points       = n;
    out->status       = BM1109_OK;
    return BM1109_OK;
}

/* ---------------------------------------------------------------------------
 *  Оценка L по начальному участку ступени.
 *
 *  На линейном участке i(t) ~ (u_eff/L)*t, поэтому наклон МНК по (t, i)
 *  даёт u_eff/L. u_eff = u_step - R*i_mean (падение на активном
 *  сопротивлении), падение на ключах задаётся отдельным параметром u_step.
 *
 *  Берутся только точки с i <= 0,5*i_ss: там экспонента ещё близка к прямой.
 * ------------------------------------------------------------------------- */
bm1109_status bm1109_fit_inductance(const bm1109_time_point *pts, uint8_t n,
                                    float u_step_v, float r_ohm, float i_ss_a,
                                    float min_delta_a, bm1109_l_result *out)
{
    float st = 0.0f, si = 0.0f, stt = 0.0f, sti = 0.0f;
    float i_limit, i_min = 0.0f, i_max = 0.0f, i_mean, slope, u_eff, l;
    uint8_t k, used = 0U, first = 1U;

    if (pts == 0 || out == 0 || n < 2U) {
        return BM1109_ERR_ARG;
    }
    if (!(u_step_v > 0.0f) || !(r_ohm > 0.0f)) {
        return BM1109_ERR_ARG;
    }

    i_limit = (i_ss_a > 0.0f) ? (0.5f * i_ss_a) : 1e9f;

    for (k = 0U; k < n; k++) {
        if (!(pts[k].t_s > 0.0f) || !(pts[k].i_a > 0.0f)) {
            continue;
        }
        if (pts[k].i_a > i_limit) {
            continue;
        }
        st  += pts[k].t_s;
        si  += pts[k].i_a;
        stt += pts[k].t_s * pts[k].t_s;
        sti += pts[k].t_s * pts[k].i_a;
        if (first) {
            i_min = pts[k].i_a;
            i_max = pts[k].i_a;
            first = 0U;
        } else {
            if (pts[k].i_a < i_min) i_min = pts[k].i_a;
            if (pts[k].i_a > i_max) i_max = pts[k].i_a;
        }
        used++;
    }

    if (used < 2U) {
        return BM1109_ERR_LOW_DELTA_I;
    }

    {
        float den = (float)used * stt - st * st;
        if (!(den > 0.0f)) {
            return BM1109_ERR_ARG;
        }
        slope = ((float)used * sti - st * si) / den;
        i_mean = si / (float)used;
    }

    if (!(slope > 0.0f)) {
        return BM1109_ERR_NOT_LINEAR;
    }
    if ((i_max - i_min) < min_delta_a) {
        return BM1109_ERR_LOW_DELTA_I;
    }

    u_eff = u_step_v - r_ohm * i_mean;
    if (!(u_eff > 0.0f)) {
        return BM1109_ERR_NOT_LINEAR;
    }

    l = u_eff / slope;

    out->l_h      = l;
    out->di_dt_a_s = slope;
    out->i_mean_a = i_mean;
    out->u_eff_v  = u_eff;
    out->points   = used;
    out->status   = BM1109_OK;
    return BM1109_OK;
}

void bm1109_star_from_line(const float r_ll[3], const float l_ll[3],
                           float r_ph[3], float l_ph[3])
{
    uint8_t k;
    if (r_ll == 0 || l_ll == 0 || r_ph == 0 || l_ph == 0) {
        return;
    }
    for (k = 0U; k < 3U; k++) {
        r_ph[k] = 0.5f * r_ll[k];
        l_ph[k] = 0.5f * l_ll[k];
    }
}

float bm1109_spread_percent(const float *values, uint8_t n)
{
    float vmin, vmax, sum = 0.0f, mean;
    uint8_t k;

    if (values == 0 || n == 0U) {
        return 0.0f;
    }

    vmin = values[0];
    vmax = values[0];
    for (k = 0U; k < n; k++) {
        sum += values[k];
        if (values[k] < vmin) vmin = values[k];
        if (values[k] > vmax) vmax = values[k];
    }

    mean = sum / (float)n;
    if (!(mean > 0.0f)) {
        return 0.0f;
    }
    return 100.0f * (vmax - vmin) / mean;
}

const char *bm1109_status_str(bm1109_status st)
{
    switch (st) {
        case BM1109_OK:             return "OK";
        case BM1109_ERR_ARG:        return "аргументы";
        case BM1109_ERR_ADC:        return "ошибка АЦП";
        case BM1109_ERR_NO_CURRENT: return "нет тока";
        case BM1109_ERR_OVER_CURRENT: return "ПРЕВЫШЕН ПРЕДЕЛ ТОКА";
        case BM1109_ERR_TIMEOUT:    return "таймаут";
        case BM1109_ERR_VBUS:       return "Vbus вне окна";
        case BM1109_ERR_NOT_LINEAR: return "нелинейность/вырожденный набор";
        case BM1109_ERR_LOW_DELTA_I: return "малый прирост тока";
        case BM1109_ERR_VBUS_L_HIGH: return "Vbus выше предела метода L";
        default:                    return "неизвестный статус";
    }
}
