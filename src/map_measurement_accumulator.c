#include "map_measurement_accumulator.h"

#include <limits.h>
#include <stddef.h>
#include <string.h>

int16_t MapMeasurement_Q15Clamp(int32_t value)
{
    if (value < -32768) return (int16_t)-32768;
    if (value > 32767) return (int16_t)32767;
    return (int16_t)value;
}

int16_t MapMeasurement_CcrToQ15(uint16_t ccr, uint16_t arr)
{
    int64_t mid;
    int64_t numerator;
    int64_t value;
    if (arr == 0u) return 0;
    mid = ((int64_t)arr + 1LL) / 2LL;
    if (mid <= 0) return 0;
    numerator = ((int64_t)ccr - mid) * 32768LL;
    if (numerator >= 0) value = (numerator + mid / 2LL) / mid;
    else value = (numerator - mid / 2LL) / mid;
    return MapMeasurement_Q15Clamp((int32_t)value);
}

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

static int32_t abs32(int32_t value)
{
    if (value == INT32_MIN) return INT32_MAX;
    return value < 0 ? -value : value;
}

static void sort_i32(int32_t *values, uint16_t count)
{
    uint16_t i;
    for (i = 1u; i < count; ++i) {
        int32_t value = values[i];
        uint16_t j = i;
        while (j > 0u && values[j - 1u] > value) {
            values[j] = values[j - 1u];
            --j;
        }
        values[j] = value;
    }
}

static int32_t median_i32(int32_t *values, uint16_t count)
{
    if (count == 0u) return 0;
    sort_i32(values, count);
    if ((count & 1u) != 0u) return values[count / 2u];
    return values[count / 2u - 1u] / 2 + values[count / 2u] / 2 +
           ((values[count / 2u - 1u] & 1) && (values[count / 2u] & 1));
}

static bool valid_phase_pair(uint8_t phase_a, uint8_t phase_b)
{
    return phase_a < 3u && phase_b < 3u && phase_a != phase_b;
}

void MapMeasurementAccumulator_Reset(MapMeasurementAccumulator *accumulator)
{
    if (accumulator != 0) memset(accumulator, 0, sizeof(*accumulator));
}

bool MapMeasurementAccumulator_Begin(
    MapMeasurementAccumulator *accumulator,
    const MapAccumQualification *qualification,
    const MapReferenceManifest *manifest,
    uint8_t sector, uint8_t window)
{
    OewMapIdentity identity;
    if (accumulator == 0 || qualification == 0 || manifest == 0 ||
        sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        window >= OEW_CURRENT_MAP_WINDOW_COUNT ||
        !valid_phase_pair(qualification->phase_a, qualification->phase_b) ||
        qualification->min_samples < MAP_ACCUM_MIN_SAMPLES_PER_ROW ||
        qualification->min_samples > MAP_ACCUM_MAX_SAMPLES_PER_ROW ||
        qualification->mad_limit_ma <= 0 || qualification->kcl_limit_ma <= 0 ||
        qualification->min_margin_ticks == 0u) {
        return false;
    }
    identity.board_revision = manifest->board_revision;
    identity.pwm_frequency_hz = manifest->pwm_frequency_hz;
    identity.timer_arr = manifest->timer_arr;
    identity.adc_trigger_id = manifest->adc_trigger_id;
    if (!MapReferenceManifest_IsValid(manifest, &identity) ||
        manifest->phase_a != qualification->phase_a ||
        manifest->phase_b != qualification->phase_b) {
        return false;
    }
    MapMeasurementAccumulator_Reset(accumulator);
    accumulator->qualification = *qualification;
    accumulator->manifest = *manifest;
    accumulator->sector = sector;
    accumulator->window = window;
    accumulator->active = 1u;
    return true;
}

MapAccumStatus MapMeasurementAccumulator_Add(
    MapMeasurementAccumulator *accumulator,
    const MapMeasurementSample *sample)
{
    uint16_t i;
    if (accumulator == 0 || sample == 0 || accumulator->active == 0u) {
        return MAP_ACCUM_BAD_ARGUMENT;
    }
    if (sample->capture.capture_id == 0u ||
        sample->capture.fault_reason != MAP_CAPTURE_OK ||
        sample->capture.frame.status != ADC_FRAME_WINDOW_INVALID ||
        sample->capture.frame.tim1_sector != accumulator->sector ||
        sample->capture.frame.sample_window != accumulator->window ||
        sample->reference.valid == 0u ||
        (sample->reference.source != MAP_REFERENCE_SOURCE_SCOPE &&
         sample->reference.source != MAP_REFERENCE_SOURCE_PROBE) ||
        sample->reference.source != accumulator->manifest.source ||
        sample->reference.sample_id != sample->capture.frame.sequence ||
        sample->timing.adc_settled == 0u ||
        sample->timing.scope_qualified == 0u ||
        sample->timing.margin_ticks < accumulator->qualification.min_margin_ticks ||
        abs32(sample->reference.phase_u_ma + sample->reference.phase_v_ma +
              sample->reference.phase_w_ma) > accumulator->qualification.kcl_limit_ma ||
        sample->capture.pwm.tim1_arr != accumulator->manifest.timer_arr ||
        sample->capture.pwm.pwm_frequency_hz != accumulator->manifest.pwm_frequency_hz ||
        sample->capture.pwm.trigger_revision != accumulator->manifest.adc_trigger_id) {
        ++accumulator->rejected_count;
        return MAP_ACCUM_SAMPLE_INVALID;
    }
    for (i = 0u; i < accumulator->sample_count; ++i) {
        if (accumulator->sequences[i] == sample->capture.frame.sequence) {
            ++accumulator->rejected_count;
            ++accumulator->duplicate_count;
            return MAP_ACCUM_SAMPLE_DUPLICATE;
        }
    }
    if (accumulator->sample_count >= MAP_ACCUM_MAX_SAMPLES_PER_ROW) {
        return MAP_ACCUM_ROW_FULL;
    }
    accumulator->samples[accumulator->sample_count] = *sample;
    accumulator->sequences[accumulator->sample_count] = sample->capture.frame.sequence;
    ++accumulator->sample_count;
    return MAP_ACCUM_OK;
}

MapAccumStatus MapMeasurementAccumulator_FinalizeRow(
    MapMeasurementAccumulator *accumulator,
    MapAccumRowReport *report)
{
    int32_t values[3][MAP_ACCUM_MAX_SAMPLES_PER_ROW];
    int32_t medians[3];
    int32_t mads[3];
    uint16_t inliers = 0u;
    uint16_t i;
    int64_t kcl_square = 0;
    int32_t max_kcl = 0;
    uint16_t min_margin = UINT16_MAX;
    if (accumulator == 0 || report == 0 || accumulator->active == 0u) {
        return MAP_ACCUM_BAD_ARGUMENT;
    }
    memset(report, 0, sizeof(*report));
    report->accepted = accumulator->sample_count;
    report->rejected = accumulator->rejected_count;
    report->duplicates = accumulator->duplicate_count;
    if (accumulator->sample_count < accumulator->qualification.min_samples) {
        return MAP_ACCUM_NOT_ENOUGH_SAMPLES;
    }
    for (i = 0u; i < accumulator->sample_count; ++i) {
        values[0][i] = accumulator->samples[i].reference.phase_u_ma;
        values[1][i] = accumulator->samples[i].reference.phase_v_ma;
        values[2][i] = accumulator->samples[i].reference.phase_w_ma;
    }
    for (i = 0u; i < 3u; ++i) {
        int32_t deviations[MAP_ACCUM_MAX_SAMPLES_PER_ROW];
        uint16_t j;
        medians[i] = median_i32(values[i], accumulator->sample_count);
        for (j = 0u; j < accumulator->sample_count; ++j) {
            deviations[j] = abs32(values[i][j] - medians[i]);
        }
        mads[i] = median_i32(deviations, accumulator->sample_count);
    }
    for (i = 0u; i < accumulator->sample_count; ++i) {
        bool inlier = true;
        uint8_t phase;
        for (phase = 0u; phase < 3u; ++phase) {
            int32_t limit = accumulator->qualification.mad_limit_ma + 3 * mads[phase];
            if (abs32(values[phase][i] - medians[phase]) > limit) inlier = false;
        }
        if (!inlier) {
            ++accumulator->outlier_count;
            continue;
        }
        {
            int32_t kcl = abs32(accumulator->samples[i].reference.phase_u_ma +
                                accumulator->samples[i].reference.phase_v_ma +
                                accumulator->samples[i].reference.phase_w_ma);
            if (kcl > max_kcl) max_kcl = kcl;
            kcl_square += (int64_t)kcl * kcl;
        }
        if (accumulator->samples[i].timing.margin_ticks < min_margin) {
            min_margin = accumulator->samples[i].timing.margin_ticks;
        }
        ++inliers;
    }
    report->accepted = inliers;
    report->outliers = accumulator->outlier_count;
    report->rejected = accumulator->rejected_count;
    report->min_margin_ticks = min_margin == UINT16_MAX ? 0u : min_margin;
    report->max_abs_kcl_ma = max_kcl;
    report->rms_kcl_ma = inliers == 0u ? 0 :
        (int32_t)isqrt_u64((uint64_t)(kcl_square / inliers));
    if (inliers < accumulator->qualification.min_samples) {
        return MAP_ACCUM_NOISE_TOO_HIGH;
    }
    if (report->rms_kcl_ma > accumulator->qualification.kcl_limit_ma ||
        report->max_abs_kcl_ma > accumulator->qualification.kcl_limit_ma) {
        return MAP_ACCUM_KCL_ERROR;
    }
    report->ready = 1u;
    return MAP_ACCUM_OK;
}
