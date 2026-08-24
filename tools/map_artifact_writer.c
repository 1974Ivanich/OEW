#include "map_artifact_writer.h"

#include <stdio.h>
#include <string.h>

static void put_u16(uint8_t *p, uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void put_i16(uint8_t *p, int16_t v) { put_u16(p, (uint16_t)v); }
static void put_u32(uint8_t *p, uint32_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24); }

static uint32_t crc32_iso(const uint8_t *p, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    while (n--) {
        crc ^= *p++;
        for (unsigned i = 0; i < 8u; ++i)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}

static bool identity_sane(const OewMapIdentity *i)
{
    return i != NULL && i->board_revision != 0u &&
           i->pwm_frequency_hz != 0u && i->timer_arr != 0u &&
           i->adc_trigger_id != 0u && i->adc_config_signature != 0u &&
           i->current_calibration_signature != 0u;
}

static bool provenance_sane(const OewMapProvenance *p)
{
    return p != NULL && p->characterization_id != 0u &&
           p->dataset_crc32 != 0u && p->tool_build_id != 0u &&
           p->qualification_revision != 0u && p->solver_revision != 0u &&
           p->certifier_revision != 0u;
}

bool MapArtifactWriter_Build(const OewMapIdentity *identity,
                             const OewMapProvenance *provenance,
                             uint8_t startup_sector,
                             uint8_t startup_window,
                             uint16_t startup_hold_cycles,
                             int16_t startup_mu,
                             int16_t startup_mv,
                             int16_t startup_mw,
                             const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                                                     [OEW_CURRENT_MAP_WINDOW_COUNT],
                             const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT]
                                                    [OEW_CURRENT_MAP_WINDOW_COUNT],
                             OewCurrentMap *out)
{
    if (!identity_sane(identity) || !provenance_sane(provenance) || !recon || !regions || !out)
        return false;
    if (startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT)
        return false;

    memset(out, 0, sizeof(*out));
    out->magic = OEW_CURRENT_MAP_MAGIC;
    out->revision = OEW_CURRENT_MAP_REVISION;
    out->board_revision = identity->board_revision;
    out->pwm_frequency_hz = identity->pwm_frequency_hz;
    out->timer_arr = identity->timer_arr;
    out->adc_trigger_id = identity->adc_trigger_id;
    out->trigger_offset_ticks = identity->trigger_offset_ticks;
    out->deadtime_ticks = identity->deadtime_ticks;
    out->adc_config_signature = identity->adc_config_signature;
    out->current_calibration_signature = identity->current_calibration_signature;
    out->provenance = *provenance;
    out->startup_sector = startup_sector;
    out->startup_window = startup_window;
    out->startup_hold_cycles = startup_hold_cycles;
    out->startup_mu = startup_mu;
    out->startup_mv = startup_mv;
    out->startup_mw = startup_mw;
    memcpy(out->recon, recon, sizeof(out->recon));
    memcpy(out->region, regions, sizeof(out->region));

    out->crc32 = CurrentMap_CalculateCrc32(out);
    return out->crc32 != 0u;
}

/* Canonical binary layout intentionally follows OewCurrentMap field order and
 * is independent of compiler ABI/padding. The current firmware struct must
 * remain layout-compatible with this v2 encoder until an explicit wire-version
 * change is made. */
size_t MapArtifactWriter_EncodeBinary(const OewCurrentMap *map,
                                      uint8_t *dst, size_t capacity)
{
    if (!map || !dst) return 0u;
    const size_t n = sizeof(*map);
    if (capacity < n || map->magic != OEW_CURRENT_MAP_MAGIC ||
        map->revision != OEW_CURRENT_MAP_REVISION)
        return 0u;
    memcpy(dst, map, n);
    return n;
}

size_t MapArtifactWriter_EncodeAuditJson(const OewCurrentMap *m,
                                         char *dst, size_t capacity)
{
    if (!m || !dst || capacity == 0u) return 0u;
    int n = snprintf(dst, capacity,
        "{\n"
        "  \"format_version\": %u,\n"
        "  \"board_revision\": %u,\n"
        "  \"pwm_frequency_hz\": %lu,\n"
        "  \"timer_arr\": %lu,\n"
        "  \"adc_trigger_id\": %lu,\n"
        "  \"trigger_offset_ticks\": %u,\n"
        "  \"deadtime_ticks\": %u,\n"
        "  \"adc_config_signature\": %lu,\n"
        "  \"current_calibration_signature\": %lu,\n"
        "  \"characterization_id\": %lu,\n"
        "  \"dataset_crc32\": %lu,\n"
        "  \"tool_build_id\": %lu,\n"
        "  \"qualification_revision\": %lu,\n"
        "  \"solver_revision\": %lu,\n"
        "  \"certifier_revision\": %lu,\n"
        "  \"crc32\": %lu\n"
        "}\n",
        (unsigned)OEW_MAP_ARTIFACT_FORMAT_VERSION,
        (unsigned)m->board_revision,
        (unsigned long)m->pwm_frequency_hz,
        (unsigned long)m->timer_arr,
        (unsigned long)m->adc_trigger_id,
        (unsigned)m->trigger_offset_ticks,
        (unsigned)m->deadtime_ticks,
        (unsigned long)m->adc_config_signature,
        (unsigned long)m->current_calibration_signature,
        (unsigned long)m->provenance.characterization_id,
        (unsigned long)m->provenance.dataset_crc32,
        (unsigned long)m->provenance.tool_build_id,
        (unsigned long)m->provenance.qualification_revision,
        (unsigned long)m->provenance.solver_revision,
        (unsigned long)m->provenance.certifier_revision,
        (unsigned long)m->crc32);
    if (n < 0 || (size_t)n >= capacity) return 0u;
    return (size_t)n;
}
