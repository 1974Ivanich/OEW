#include "map_measurement_solver.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

static uint32_t isqrt_u64(uint64_t value)
{
    uint64_t result = 0u;
    uint64_t bit = (uint64_t)1u << 62;
    while (bit > value) bit >>= 2;
    while (bit != 0u) {
        if (value >= result + bit) {
            value -= result + bit;
            result = (result >> 1) + bit;
        } else {
            result >>= 1;
        }
        bit >>= 2;
    }
    return (uint32_t)result;
}

static int32_t abs32s(int32_t v)
{
    if (v == INT32_MIN) return INT32_MAX;
    return v < 0 ? -v : v;
}

static int32_t selected_phase(const MapPhaseReference *r, uint8_t phase)
{
    if (phase == 0u) return r->phase_u_ma;
    if (phase == 1u) return r->phase_v_ma;
    return r->phase_w_ma;
}

static int32_t clamp_i32(int64_t v)
{
    if (v > INT32_MAX) return INT32_MAX;
    if (v < INT32_MIN) return INT32_MIN;
    return (int32_t)v;
}

static int32_t scaled_ma(int32_t value)
{
    return value / 1000;
}

static int32_t predict_scaled(int32_t m0, int32_t m1,
                              int32_t x0, int32_t x1)
{
    return clamp_i32(((int64_t)m0 * x0 + (int64_t)m1 * x1) /
                     CURRENT_RECON_COEFF_SCALE);
}

MapSolverStatus MapMeasurement_SolveM(
    const MapMeasurementAccumulator *accumulator,
    const MapSolverQualification *qualification,
    CurrentReconEntry *out,
    MapSolverReport *report)
{
    int64_t s00 = 0, s01 = 0, s11 = 0;
    int64_t t00 = 0, t01 = 0, t10 = 0, t11 = 0;
    int64_t determinant;
    int64_t trace;
    int32_t m00, m01, m10, m11;
    uint16_t holdout;
    uint16_t fit_count;
    uint16_t i;
    int64_t residual_sq = 0;
    int64_t holdout_sq = 0;
    int64_t kcl_sq = 0;
    int64_t residual_sum = 0;
    int32_t residual_max = 0;
    uint16_t holdout_count = 0u;

    if (report != 0) memset(report, 0, sizeof(*report));
    if (accumulator == 0 || qualification == 0 || out == 0 ||
        accumulator->active == 0u || qualification->min_samples < 4u ||
        qualification->holdout_samples == 0u ||
        qualification->holdout_samples >= qualification->min_samples ||
        qualification->residual_rms_limit_ma <= 0 ||
        qualification->residual_max_limit_ma <= 0 ||
        qualification->bias_limit_ma <= 0 ||
        qualification->holdout_rms_limit_ma <= 0 ||
        qualification->kcl_rms_limit_ma <= 0 ||
        qualification->max_condition_ratio == 0u ||
        qualification->min_abs_determinant <= 0 ||
        qualification->min_abs_diagonal <= 0) {
        return MAP_SOLVER_BAD_ARGUMENT;
    }
    if (accumulator->sample_count < qualification->min_samples) {
        return MAP_SOLVER_NOT_ENOUGH_SAMPLES;
    }
    holdout = qualification->holdout_samples;
    fit_count = (uint16_t)(accumulator->sample_count - holdout);
    if (fit_count < 2u) return MAP_SOLVER_NOT_ENOUGH_SAMPLES;

    for (i = 0u; i < fit_count; ++i) {
        const MapMeasurementSample *sample = &accumulator->samples[i];
        int32_t x0 = scaled_ma(sample->capture.frame.idc1_ma);
        int32_t x1 = scaled_ma(sample->capture.frame.idc2_ma);
        int32_t y0 = scaled_ma(selected_phase(&sample->reference,
                                               accumulator->qualification.phase_a));
        int32_t y1 = scaled_ma(selected_phase(&sample->reference,
                                               accumulator->qualification.phase_b));
        s00 += (int64_t)x0 * x0;
        s01 += (int64_t)x0 * x1;
        s11 += (int64_t)x1 * x1;
        t00 += (int64_t)y0 * x0;
        t01 += (int64_t)y0 * x1;
        t10 += (int64_t)y1 * x0;
        t11 += (int64_t)y1 * x1;
    }
    determinant = s00 * s11 - s01 * s01;
    if (determinant <= 0 || determinant < qualification->min_abs_determinant) {
        return MAP_SOLVER_SINGULAR;
    }
    trace = s00 + s11;
    if (trace <= 0 || (trace * trace) / determinant > qualification->max_condition_ratio) {
        return MAP_SOLVER_CONDITION_BAD;
    }
    m00 = clamp_i32(((t00 * s11 - t01 * s01) * CURRENT_RECON_COEFF_SCALE) / determinant);
    m01 = clamp_i32(((t01 * s00 - t00 * s01) * CURRENT_RECON_COEFF_SCALE) / determinant);
    m10 = clamp_i32(((t10 * s11 - t11 * s01) * CURRENT_RECON_COEFF_SCALE) / determinant);
    m11 = clamp_i32(((t11 * s00 - t10 * s01) * CURRENT_RECON_COEFF_SCALE) / determinant);
    if (abs32s(m00) < qualification->min_abs_diagonal ||
        abs32s(m11) < qualification->min_abs_diagonal) {
        return MAP_SOLVER_POLARITY_BAD;
    }

    for (i = 0u; i < accumulator->sample_count; ++i) {
        const MapMeasurementSample *sample = &accumulator->samples[i];
        int32_t x0 = scaled_ma(sample->capture.frame.idc1_ma);
        int32_t x1 = scaled_ma(sample->capture.frame.idc2_ma);
        int32_t y0 = scaled_ma(selected_phase(&sample->reference, accumulator->qualification.phase_a));
        int32_t y1 = scaled_ma(selected_phase(&sample->reference, accumulator->qualification.phase_b));
        int32_t e0 = (predict_scaled(m00, m01, x0, x1) - y0) * 1000;
        int32_t e1 = (predict_scaled(m10, m11, x0, x1) - y1) * 1000;
        int32_t e = abs32s(e0) > abs32s(e1) ? abs32s(e0) : abs32s(e1);
        int32_t kcl = abs32s(sample->reference.phase_u_ma +
                             sample->reference.phase_v_ma +
                             sample->reference.phase_w_ma);
        kcl_sq += (int64_t)kcl * kcl;
        if (i >= fit_count) {
            holdout_sq += (int64_t)e * e;
            ++holdout_count;
        } else {
            residual_sq += (int64_t)e * e;
            residual_sum += (int64_t)e0 + e1;
            if (e > residual_max) residual_max = e;
        }
    }
    if (fit_count == 0u || residual_sq / fit_count < 0) return MAP_SOLVER_OVERFLOW;
    {
        int32_t rms = clamp_i32((int64_t)isqrt_u64((uint64_t)(residual_sq / fit_count)));
        int32_t bias = clamp_i32(residual_sum / (int64_t)(fit_count * 2u));
        int32_t holdout_rms = holdout_count == 0u ? INT32_MAX :
            clamp_i32((int64_t)isqrt_u64((uint64_t)(holdout_sq / holdout_count)));
        int32_t kcl_rms = clamp_i32((int64_t)isqrt_u64((uint64_t)(kcl_sq / accumulator->sample_count)));
        if (report != 0) {
            report->residual_rms_ma = rms;
            report->residual_max_ma = residual_max;
            report->residual_bias_ma = abs32s(bias);
            report->holdout_rms_ma = holdout_rms;
            report->kcl_rms_ma = kcl_rms;
            report->determinant_scaled = clamp_i32(determinant);
            report->condition_ratio = (uint32_t)((trace * trace) / determinant);
            report->fit_samples = fit_count;
            report->holdout_samples = holdout_count;
        }
        if (rms > qualification->residual_rms_limit_ma ||
            residual_max > qualification->residual_max_limit_ma ||
            abs32s(bias) > qualification->bias_limit_ma) return MAP_SOLVER_RESIDUAL_BAD;
        if (holdout_rms > qualification->holdout_rms_limit_ma) return MAP_SOLVER_HOLDOUT_BAD;
        if (kcl_rms > qualification->kcl_rms_limit_ma) return MAP_SOLVER_RESIDUAL_BAD;
    }
    memset(out, 0, sizeof(*out));
    out->valid = true;
    out->phase_a = accumulator->qualification.phase_a;
    out->phase_b = accumulator->qualification.phase_b;
    out->m00 = m00;
    out->m01 = m01;
    out->m10 = m10;
    out->m11 = m11;
    if (report != 0) report->ready = 1u;
    return MAP_SOLVER_OK;
}
