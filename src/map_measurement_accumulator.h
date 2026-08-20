#ifndef MAP_MEASUREMENT_ACCUMULATOR_H
#define MAP_MEASUREMENT_ACCUMULATOR_H

#include <stdbool.h>
#include <stdint.h>

#include "map_measurement_reference.h"

#ifndef MAP_ACCUM_MAX_SAMPLES_PER_ROW
#define MAP_ACCUM_MAX_SAMPLES_PER_ROW 32u
#endif
#ifndef MAP_ACCUM_MIN_SAMPLES_PER_ROW
#define MAP_ACCUM_MIN_SAMPLES_PER_ROW 8u
#endif
#ifndef MAP_ACCUM_MAD_LIMIT_MA
#define MAP_ACCUM_MAD_LIMIT_MA 250
#endif
#ifndef MAP_ACCUM_KCL_LIMIT_MA
#define MAP_ACCUM_KCL_LIMIT_MA 500
#endif
#ifndef MAP_ACCUM_MIN_MARGIN_TICKS
#define MAP_ACCUM_MIN_MARGIN_TICKS 1u
#endif

typedef enum {
    MAP_ACCUM_OK = 0,
    MAP_ACCUM_BAD_ARGUMENT,
    MAP_ACCUM_BAD_QUALIFICATION,
    MAP_ACCUM_ROW_OUT_OF_RANGE,
    MAP_ACCUM_SAMPLE_INVALID,
    MAP_ACCUM_SAMPLE_DUPLICATE,
    MAP_ACCUM_ROW_FULL,
    MAP_ACCUM_NOT_ENOUGH_SAMPLES,
    MAP_ACCUM_NOISE_TOO_HIGH,
    MAP_ACCUM_KCL_ERROR,
    MAP_ACCUM_NOT_FINALIZABLE
} MapAccumStatus;

typedef struct {
    uint8_t phase_a;
    uint8_t phase_b;
    uint16_t min_samples;
    int32_t mad_limit_ma;
    int32_t kcl_limit_ma;
    uint16_t min_margin_ticks;
} MapAccumQualification;

typedef struct {
    uint16_t accepted;
    uint16_t rejected;
    uint16_t outliers;
    uint16_t duplicates;
    uint16_t min_margin_ticks;
    int32_t rms_kcl_ma;
    int32_t max_abs_kcl_ma;
    uint8_t ready;
} MapAccumRowReport;

typedef struct {
    MapAccumQualification qualification;
    MapReferenceManifest manifest;
    MapMeasurementSample samples[MAP_ACCUM_MAX_SAMPLES_PER_ROW];
    uint32_t sequences[MAP_ACCUM_MAX_SAMPLES_PER_ROW];
    uint16_t sample_count;
    uint16_t rejected_count;
    uint16_t outlier_count;
    uint16_t duplicate_count;
    uint8_t sector;
    uint8_t window;
    uint8_t active;
} MapMeasurementAccumulator;

bool MapMeasurementAccumulator_Begin(
    MapMeasurementAccumulator *accumulator,
    const MapAccumQualification *qualification,
    const MapReferenceManifest *manifest,
    uint8_t sector, uint8_t window);

MapAccumStatus MapMeasurementAccumulator_Add(
    MapMeasurementAccumulator *accumulator,
    const MapMeasurementSample *sample);

MapAccumStatus MapMeasurementAccumulator_FinalizeRow(
    MapMeasurementAccumulator *accumulator,
    MapAccumRowReport *report);

void MapMeasurementAccumulator_Reset(MapMeasurementAccumulator *accumulator);

int16_t MapMeasurement_CcrToQ15(uint16_t ccr, uint16_t arr);
int16_t MapMeasurement_Q15Clamp(int32_t value);

#endif /* MAP_MEASUREMENT_ACCUMULATOR_H */
