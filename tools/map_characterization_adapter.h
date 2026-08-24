#ifndef MAP_CHARACTERIZATION_ADAPTER_H
#define MAP_CHARACTERIZATION_ADAPTER_H

#include <stdbool.h>
#include <stdint.h>

#include "map_measurement_accumulator.h"
#include "map_capture.h"

/* The adapter is intentionally strict: a capture record alone is not enough
 * to become a characterization sample. Timing and phase-reference evidence
 * must be supplied by the bench. */
bool MapCharacterizationAdapter_FromCapture(
    const MapCaptureRecord *capture,
    const MapPhaseReference *reference,
    const MapTimingEvidence *timing,
    MapMeasurementSample *out);

#endif
