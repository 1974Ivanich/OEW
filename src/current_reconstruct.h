#ifndef CURRENT_RECONSTRUCT_H
#define CURRENT_RECONSTRUCT_H

#include <stdbool.h>
#include <stdint.h>

#include "adc.h"

#define CURRENT_RECON_MAX_SECTORS  6u
#define CURRENT_RECON_MAX_WINDOWS  2u

typedef struct {
    int32_t iu_ma;
    int32_t iv_ma;
    int32_t iw_ma;

    /* CT is diagnostic only unless separately qualified. */
    int32_t ict_ma; /* independent diagnostic value; not a phase current */

    uint8_t sector;
    uint8_t sample_window;
} PhaseCurrents;

/*
 * Each row describes a single experimentally proven PWM sector/window.
 *
 * The two DC-link shunts provide x = [idc1, idc2]^T. Matrix M maps them
 * directly to two independent phase currents y = [phase_a, phase_b]^T:
 *
 *   phase_a = (m00 * idc1 + m01 * idc2) / CURRENT_RECON_COEFF_SCALE
 *   phase_b = (m10 * idc1 + m11 * idc2) / CURRENT_RECON_COEFF_SCALE
 *   phase_c = -phase_a - phase_b
 *
 * phase_a/phase_b are phase IDs 0=U, 1=V, 2=W. The coefficients include the
 * measured shunt polarity and any calibrated gain correction. A row is valid
 * only if the oscilloscope/timing/calibration evidence proves two independent
 * equations inside that exact window. The default table is all invalid.
 */
typedef struct {
    bool valid;
    uint8_t phase_a;
    uint8_t phase_b;
    int32_t m00;
    int32_t m01;
    int32_t m10;
    int32_t m11;
} CurrentReconEntry;

#define CURRENT_RECON_COEFF_SCALE 1000L

/* Replaces all map rows atomically with a validated copy while control is off.
 * It rejects invalid phase pairs, zero determinant and unreasonable bounds. */
bool CurrentRecon_LoadMap(const CurrentReconEntry map[CURRENT_RECON_MAX_SECTORS]
                                                    [CURRENT_RECON_MAX_WINDOWS]);

/* Returns false unless AdcFrame itself is control-valid and its exact map row
 * is proven. `out` is never partially updated on failure. */
bool Current_Reconstruct(const AdcFrame *frame, PhaseCurrents *out);

/* True only after every control state the scheduler can request has a proven
 * table row. With the default empty map this is false. */
bool CurrentRecon_IsReady(void);

/* Clears all rows and immediately returns control to fail-closed state. */
void CurrentRecon_Reset(void);

#endif /* CURRENT_RECON_H */
