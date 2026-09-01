#ifndef MAP_MEASUREMENT_SOLVER_H
#define MAP_MEASUREMENT_SOLVER_H

#include <stdbool.h>
#include <stdint.h>

#include "map_measurement_accumulator.h"
#include "current_reconstruct.h"

typedef enum {
    MAP_SOLVER_OK = 0,
    MAP_SOLVER_BAD_ARGUMENT,
    MAP_SOLVER_NOT_ENOUGH_SAMPLES,
    MAP_SOLVER_SINGULAR,
    MAP_SOLVER_CONDITION_BAD,
    MAP_SOLVER_RESIDUAL_BAD,
    MAP_SOLVER_HOLDOUT_BAD,
    MAP_SOLVER_POLARITY_BAD,
    MAP_SOLVER_OVERFLOW
} MapSolverStatus;

typedef struct {
    int32_t residual_rms_ma;
    int32_t residual_max_ma;
    int32_t residual_bias_ma;
    int32_t holdout_rms_ma;
    int32_t kcl_rms_ma;
    int32_t determinant_scaled;
    uint32_t condition_ratio;
    uint16_t fit_samples;
    uint16_t holdout_samples;
    uint8_t ready;
} MapSolverReport;

typedef struct {
    uint16_t min_samples;
    uint16_t holdout_samples;
    int32_t residual_rms_limit_ma;
    int32_t residual_max_limit_ma;
    int32_t bias_limit_ma;
    int32_t holdout_rms_limit_ma;
    int32_t kcl_rms_limit_ma;
    uint32_t max_condition_ratio;
    /* Minimum relative determinant det(S)/(S00*S11) in ppm (1e-6).
     * Scale-free excitation quality: 0 = rank-1 (idc1 ~ idc2), 1e6 =
     * orthogonal shunts. The bench default is 10000 (1%). */
    int32_t min_abs_determinant;
    int32_t min_abs_diagonal;
} MapSolverQualification;

MapSolverStatus MapMeasurement_SolveM(
    const MapMeasurementAccumulator *accumulator,
    const MapSolverQualification *qualification,
    CurrentReconEntry *out,
    MapSolverReport *report);

#endif /* MAP_MEASUREMENT_SOLVER_H */
