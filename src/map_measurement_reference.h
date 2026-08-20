#ifndef MAP_MEASUREMENT_REFERENCE_H
#define MAP_MEASUREMENT_REFERENCE_H

#include <stdbool.h>
#include <stdint.h>

#include "current_map_selector.h"
#include "map_capture.h"

#define MAP_REFERENCE_MAGIC       0x4D524546u /* "MREF" */
#define MAP_REFERENCE_REVISION    1u
#define MAP_REFERENCE_SOURCE_SCOPE 1u
#define MAP_REFERENCE_SOURCE_PROBE 2u

typedef struct {
    int32_t phase_u_ma;
    int32_t phase_v_ma;
    int32_t phase_w_ma;
    uint8_t valid;
    uint8_t source;
    uint16_t reserved;
    uint32_t sample_id;
    uint32_t timestamp_cycles;
} MapPhaseReference;

typedef struct {
    uint16_t margin_ticks;
    uint16_t blanking_ticks;
    uint8_t adc_settled;
    uint8_t scope_qualified;
    uint16_t reserved;
} MapTimingEvidence;

typedef struct {
    uint32_t magic;
    uint16_t revision;
    uint16_t board_revision;
    uint32_t pwm_frequency_hz;
    uint32_t timer_arr;
    uint32_t adc_trigger_id;
    uint8_t source;
    uint8_t phase_a;
    uint8_t phase_b;
    uint8_t reserved;
    uint32_t tool_build_id;
    uint32_t record_count;
    uint32_t crc32;
} MapReferenceManifest;

typedef struct {
    MapCaptureRecord capture;
    MapPhaseReference reference;
    MapTimingEvidence timing;
} MapMeasurementSample;

bool MapReferenceManifest_IsValid(const MapReferenceManifest *manifest,
                                  const OewMapIdentity *identity);
uint32_t MapReferenceManifest_CalculateCrc32(
    const MapReferenceManifest *manifest);

#endif /* MAP_MEASUREMENT_REFERENCE_H */
