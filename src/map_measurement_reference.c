#include "map_measurement_reference.h"

#include <string.h>

static uint32_t crc32_bytes(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xFFFFFFFFu;
    size_t i;
    unsigned bit;
    for (i = 0u; i < length; ++i) {
        crc ^= data[i];
        for (bit = 0u; bit < 8u; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
        }
    }
    return ~crc;
}

uint32_t MapReferenceManifest_CalculateCrc32(
    const MapReferenceManifest *manifest)
{
    MapReferenceManifest copy;
    if (manifest == 0) return 0u;
    memset(&copy, 0, sizeof(copy));
    copy.magic = manifest->magic;
    copy.revision = manifest->revision;
    copy.board_revision = manifest->board_revision;
    copy.pwm_frequency_hz = manifest->pwm_frequency_hz;
    copy.timer_arr = manifest->timer_arr;
    copy.adc_trigger_id = manifest->adc_trigger_id;
    copy.source = manifest->source;
    copy.phase_a = manifest->phase_a;
    copy.phase_b = manifest->phase_b;
    copy.tool_build_id = manifest->tool_build_id;
    copy.record_count = manifest->record_count;
    return crc32_bytes((const uint8_t *)&copy, sizeof(copy));
}

bool MapReferenceManifest_IsValid(const MapReferenceManifest *manifest,
                                  const OewMapIdentity *identity)
{
    if (manifest == 0 || identity == 0 ||
        manifest->magic != MAP_REFERENCE_MAGIC ||
        manifest->revision != MAP_REFERENCE_REVISION ||
        manifest->board_revision == 0u || manifest->pwm_frequency_hz == 0u ||
        manifest->timer_arr == 0u || manifest->adc_trigger_id == 0u ||
        (manifest->source != MAP_REFERENCE_SOURCE_SCOPE &&
         manifest->source != MAP_REFERENCE_SOURCE_PROBE) ||
        manifest->phase_a >= 3u || manifest->phase_b >= 3u ||
        manifest->phase_a == manifest->phase_b || manifest->reserved != 0u ||
        manifest->record_count == 0u ||
        manifest->crc32 != MapReferenceManifest_CalculateCrc32(manifest)) {
        return false;
    }
    return manifest->board_revision == identity->board_revision &&
           manifest->pwm_frequency_hz == identity->pwm_frequency_hz &&
           manifest->timer_arr == identity->timer_arr &&
           manifest->adc_trigger_id == identity->adc_trigger_id;
}
