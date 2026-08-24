#include "map_candidate.h"

#include <string.h>

static bool region_valid(const OewPwmRegion *r)
{
    return r != 0 && r->valid != 0u && r->reserved == 0u &&
           r->mu_min <= r->mu_max && r->mv_min <= r->mv_max &&
           r->mw_min <= r->mw_max && r->min_margin_ticks != 0u;
}

#define MAP_CANDIDATE_MAX_COEFF  10000L

static bool recon_valid(const CurrentReconEntry *r)
{
    int64_t determinant;

    if (r == 0 || !r->valid || r->phase_a >= 3u || r->phase_b >= 3u ||
        r->phase_a == r->phase_b ||
        r->m00 < -MAP_CANDIDATE_MAX_COEFF || r->m00 > MAP_CANDIDATE_MAX_COEFF ||
        r->m01 < -MAP_CANDIDATE_MAX_COEFF || r->m01 > MAP_CANDIDATE_MAX_COEFF ||
        r->m10 < -MAP_CANDIDATE_MAX_COEFF || r->m10 > MAP_CANDIDATE_MAX_COEFF ||
        r->m11 < -MAP_CANDIDATE_MAX_COEFF || r->m11 > MAP_CANDIDATE_MAX_COEFF) {
        return false;
    }
    determinant = (int64_t)r->m00 * r->m11 - (int64_t)r->m01 * r->m10;
    return determinant != 0;
}

static bool region_contains(const OewPwmRegion *r,
                            int16_t mu, int16_t mv, int16_t mw)
{
    return region_valid(r) &&
           mu >= r->mu_min && mu <= r->mu_max &&
           mv >= r->mv_min && mv <= r->mv_max &&
           mw >= r->mw_min && mw <= r->mw_max;
}

static bool map_structure_valid(const OewCurrentMap *map)
{
    uint8_t sector;
    uint8_t window;

    if (map->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        map->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT ||
        map->startup_hold_cycles == 0u) {
        return false;
    }
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (!recon_valid(&map->recon[sector][window]) ||
                !region_valid(&map->region[sector][window])) {
                return false;
            }
        }
    }
    return region_contains(&map->region[map->startup_sector][map->startup_window],
                           map->startup_mu, map->startup_mv, map->startup_mw);
}

static bool identity_matches(const OewCurrentMap *map,
                             const OewMapIdentity *identity)
{
    return map->board_revision == identity->board_revision &&
           map->pwm_frequency_hz == identity->pwm_frequency_hz &&
           map->timer_arr == identity->timer_arr &&
           map->adc_trigger_id == identity->adc_trigger_id &&
           map->trigger_offset_ticks == identity->trigger_offset_ticks &&
           map->deadtime_ticks == identity->deadtime_ticks &&
           map->adc_config_signature == identity->adc_config_signature &&
           map->current_calibration_signature == identity->current_calibration_signature;
}

static bool provenance_valid(const OewMapProvenance *provenance)
{
    return provenance != 0 &&
           provenance->characterization_id != 0u &&
           provenance->dataset_crc32 != 0u &&
           provenance->tool_build_id != 0u &&
           provenance->qualification_revision != 0u &&
           provenance->solver_revision != 0u &&
           provenance->certifier_revision != 0u;
}

MapCandidateStatus MapCandidate_Build(
    const MapCandidateQualification *qualification,
    const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                                      [OEW_CURRENT_MAP_WINDOW_COUNT],
    const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT]
                              [OEW_CURRENT_MAP_WINDOW_COUNT],
    OewCurrentMap *out)
{
    uint8_t sector;
    uint8_t window;

    if (qualification == 0 || recon == 0 || regions == 0 || out == 0) {
        return MAP_CANDIDATE_BAD_ARGUMENT;
    }
    if (!provenance_valid(&qualification->provenance) ||
        !MapReferenceManifest_IsValid(&qualification->manifest,
                                      &qualification->identity) ||
        qualification->manifest.record_count < MAP_CANDIDATE_REQUIRED_ROWS ||
        qualification->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        qualification->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT ||
        qualification->startup_hold_cycles == 0u) {
        return MAP_CANDIDATE_PROVENANCE_BAD;
    }

    memset(out, 0, sizeof(*out));
    out->magic = OEW_CURRENT_MAP_MAGIC;
    out->revision = OEW_CURRENT_MAP_REVISION;
    out->board_revision = qualification->identity.board_revision;
    out->pwm_frequency_hz = qualification->identity.pwm_frequency_hz;
    out->timer_arr = qualification->identity.timer_arr;
    out->adc_trigger_id = qualification->identity.adc_trigger_id;
    out->trigger_offset_ticks = qualification->identity.trigger_offset_ticks;
    out->deadtime_ticks = qualification->identity.deadtime_ticks;
    out->adc_config_signature = qualification->identity.adc_config_signature;
    out->current_calibration_signature = qualification->identity.current_calibration_signature;
    out->provenance = qualification->provenance;
    out->startup_sector = qualification->startup_sector;
    out->startup_window = qualification->startup_window;
    out->startup_hold_cycles = qualification->startup_hold_cycles;
    out->startup_mu = qualification->startup_mu;
    out->startup_mv = qualification->startup_mv;
    out->startup_mw = qualification->startup_mw;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (!recon_valid(&recon[sector][window])) {
                memset(out, 0, sizeof(*out));
                return MAP_CANDIDATE_RECON_BAD;
            }
            if (!region_valid(&regions[sector][window])) {
                memset(out, 0, sizeof(*out));
                return MAP_CANDIDATE_REGION_BAD;
            }
            out->recon[sector][window] = recon[sector][window];
            out->region[sector][window] = regions[sector][window];
        }
    }

    out->crc32 = CurrentMap_CalculateCrc32(out);
    return MAP_CANDIDATE_OK;
}

bool MapCandidate_IsCanonical(const OewCurrentMap *map,
                              const OewMapIdentity *identity,
                              const OewMapProvenance *provenance,
                              const MapReferenceManifest *manifest)
{
    if (map == 0 || identity == 0 || provenance == 0 || manifest == 0 ||
        !provenance_valid(provenance) ||
        !MapReferenceManifest_IsValid(manifest, identity) ||
        map->crc32 != CurrentMap_CalculateCrc32(map) ||
        map->magic != OEW_CURRENT_MAP_MAGIC ||
        map->revision != OEW_CURRENT_MAP_REVISION ||
        !identity_matches(map, identity) ||
        memcmp(&map->provenance, provenance, sizeof(*provenance)) != 0 ||
        !map_structure_valid(map)) {
        return false;
    }
    return true;
}
