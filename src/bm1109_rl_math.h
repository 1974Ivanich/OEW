#ifndef BM1109_RL_MATH_H
#define BM1109_RL_MATH_H

#include <stddef.h>
#include <stdint.h>

/*
 * BM1109 winding measurement math.
 *
 * This module is deliberately hardware-independent. The acquisition layer
 * must supply calibrated winding-terminal voltage and winding current,
 * not raw DC-link current or commanded PWM duty.
 *
 * Units:
 *   time_us, voltage_mV, current_mA, resistance_mOhm, inductance_uH.
 */

typedef struct {
    int32_t voltage_mV;
    int32_t current_mA;
} BM1109_RsSample;

typedef struct {
    uint32_t time_us;
    int32_t voltage_mV; /* signed voltage across the tested winding pair */
    int32_t current_mA; /* signed current through that same pair */
} BM1109_LsSample;

typedef struct {
    int32_t resistance_mOhm;
    int32_t voltage_offset_mV;
    uint32_t current_span_mA;
    uint16_t sample_count;
} BM1109_RsResult;

typedef struct {
    int32_t inductance_uH;
    int32_t delta_current_mA;
    uint32_t duration_us;
    uint16_t sample_count;
} BM1109_LsResult;

/*
 * Least-squares fit V = R*I + Voffset.
 * At least 3 samples and a non-zero current span are required.
 * Returns 0 on success, negative on invalid/degenerate input.
 */
int BM1109_RsFit(const BM1109_RsSample *samples, size_t count,
                 BM1109_RsResult *result);

/*
 * Estimate equivalent inductance from a sampled current ramp:
 * L = integral((V - R*I) dt) / delta_I.
 * Trapezoidal integration is used. Samples must have strictly increasing
 * timestamps and a non-zero net current change.
 * Returns 0 on success, negative on invalid/degenerate input.
 */
int BM1109_LsFromStep(const BM1109_LsSample *samples, size_t count,
                      int32_t resistance_mOhm, BM1109_LsResult *result);

#endif
