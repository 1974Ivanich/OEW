#include "map_candidate.h"

#include <string.h>

static bool region_valid(const OewPwmRegion *r)
{
    return r != 0 && r->valid != 0u && r->reserved == 0u &&
           r->mu_min <= r->mu_max && r->mv_min <= r->mv_max &&
           r->mw_min <= r->mw_max && r->min_margin_ticks != 0u;
}

static bool recon_valid(const CurrentReconEntry *r)
{
    return r != 0 && r->valid && r->phase_a < 3u && r->phase_b < 3u &&
           r->phase_a != r->phase_b && r->m00 != 0 && r->m11 != 0;
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
    if (!MapReferenceManifest_IsValid(&qualification->manifest,
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
                              const MapReferenceManifest *manifest)
{
    if (map == 0 || identity == 0 || manifest == 0 ||
        !MapReferenceManifest_IsValid(manifest, identity) ||
        map->crc32 != CurrentMap_CalculateCrc32(map) ||
        map->magic != OEW_CURRENT_MAP_MAGIC ||
        map->revision != OEW_CURRENT_MAP_REVISION ||
        map->board_revision != identity->board_revision ||
        map->pwm_frequency_hz != identity->pwm_frequency_hz ||
        map->timer_arr != identity->timer_arr ||
        map->adc_trigger_id != identity->adc_trigger_id) {
        return false;
    }
    return true;
}
