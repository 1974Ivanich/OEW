#include "map_candidate.h"
#include <string.h>

static int32_t abs_i32(int32_t value) { return value < 0 ? -value : value; }
static int32_t geometry_modulus(int16_t mu, int16_t mv, int16_t mw)
{
    int32_t a = abs_i32((int32_t)mu), b = abs_i32((int32_t)mv), c = abs_i32((int32_t)mw);
    return a > b ? (a > c ? a : c) : (b > c ? b : c);
}
static uint8_t geometry_sector_for_vector(int16_t mu, int16_t mv, int16_t mw)
{
    if ((int32_t)mu + (int32_t)mv + (int32_t)mw != 0 || (mu == 0 && mv == 0 && mw == 0)) return 0xFFu;
    if (mu >= mv) {
        if (mv >= mw) return 0u;
        if (mu >= mw) return 1u;
        return 4u;
    }
    if (mv >= mw) return (mu >= mw) ? 2u : 3u;
    return 5u;
}

static bool region_valid(const OewPwmRegion *r)
{
    if (r == 0 || r->valid == 0u || r->reserved > 1u || r->min_margin_ticks == 0u ||
        r->mu_min > r->mu_max || r->mv_min > r->mv_max || r->mw_min > r->mw_max) return false;
    if (r->reserved == 1u && (r->geometry_mod_min_q15 <= 0 ||
                              r->geometry_mod_max_q15 <= r->geometry_mod_min_q15)) return false;
    return true;
}

#define MAP_CANDIDATE_MAX_COEFF  10000L
static bool recon_valid(const CurrentReconEntry *r)
{
    int64_t determinant;
    if (r == 0 || !r->valid || r->phase_a >= 3u || r->phase_b >= 3u || r->phase_a == r->phase_b ||
        r->m00 < -MAP_CANDIDATE_MAX_COEFF || r->m00 > MAP_CANDIDATE_MAX_COEFF ||
        r->m01 < -MAP_CANDIDATE_MAX_COEFF || r->m01 > MAP_CANDIDATE_MAX_COEFF ||
        r->m10 < -MAP_CANDIDATE_MAX_COEFF || r->m10 > MAP_CANDIDATE_MAX_COEFF ||
        r->m11 < -MAP_CANDIDATE_MAX_COEFF || r->m11 > MAP_CANDIDATE_MAX_COEFF) return false;
    determinant = (int64_t)r->m00 * r->m11 - (int64_t)r->m01 * r->m10;
    return determinant != 0;
}

static bool region_contains(const OewPwmRegion *r, uint8_t sector, uint8_t window,
                            int16_t mu, int16_t mv, int16_t mw)
{
    if (!region_valid(r)) return false;
    if (r->reserved == 1u) {
        const int32_t mod = geometry_modulus(mu, mv, mw);
        const uint8_t selected = geometry_sector_for_vector(mu, mv, mw);
        return selected == sector && mod >= r->geometry_mod_min_q15 &&
               (window == 0u ? mod < r->geometry_mod_max_q15 : mod <= r->geometry_mod_max_q15);
    }
    return mu >= r->mu_min && mu <= r->mu_max && mv >= r->mv_min && mv <= r->mv_max &&
           mw >= r->mw_min && mw <= r->mw_max;
}

static bool map_structure_valid(const OewCurrentMap *map)
{
    uint8_t sector, window;
    uint8_t geometry = map->region[0][0].reserved;
    if (geometry > 1u || map->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        map->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT || map->startup_hold_cycles == 0u) return false;
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const OewPwmRegion *r = &map->region[sector][window];
            if (!recon_valid(&map->recon[sector][window]) || !region_valid(r) || r->reserved != geometry) return false;
            if (geometry == 1u && window == 0u &&
                r->geometry_mod_max_q15 > map->region[sector][1].geometry_mod_min_q15) return false;
        }
    }
    return region_contains(&map->region[map->startup_sector][map->startup_window],
                           map->startup_sector, map->startup_window,
                           map->startup_mu, map->startup_mv, map->startup_mw);
}

static bool identity_matches(const OewCurrentMap *map, const OewMapIdentity *identity)
{
    return map->board_revision == identity->board_revision && map->pwm_frequency_hz == identity->pwm_frequency_hz &&
           map->timer_arr == identity->timer_arr && map->adc_trigger_id == identity->adc_trigger_id &&
           map->trigger_offset_ticks == identity->trigger_offset_ticks && map->deadtime_ticks == identity->deadtime_ticks &&
           map->adc_clock_hz == identity->adc_clock_hz && map->adc_sample_cycles_x2 == identity->adc_sample_cycles_x2 &&
           map->adc_resolution == identity->adc_resolution && map->adc_config_signature == identity->adc_config_signature &&
           map->current_calibration_signature == identity->current_calibration_signature;
}
static bool provenance_valid(const OewMapProvenance *p)
{
    return p != 0 && p->characterization_id != 0u && p->dataset_crc32 != 0u && p->tool_build_id != 0u &&
           p->qualification_revision != 0u && p->solver_revision != 0u && p->certifier_revision != 0u;
}

MapCandidateStatus MapCandidate_Build(const MapCandidateQualification *qualification,
    const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT],
    const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT],
    OewCurrentMap *out)
{
    uint8_t sector, window;
    if (qualification == 0 || recon == 0 || regions == 0 || out == 0) return MAP_CANDIDATE_BAD_ARGUMENT;
    if (!provenance_valid(&qualification->provenance) ||
        !MapReferenceManifest_IsValid(&qualification->manifest, &qualification->identity) ||
        qualification->manifest.record_count < MAP_CANDIDATE_REQUIRED_ROWS ||
        qualification->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        qualification->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT || qualification->startup_hold_cycles == 0u) return MAP_CANDIDATE_PROVENANCE_BAD;
    memset(out, 0, sizeof(*out));
    out->magic = OEW_CURRENT_MAP_MAGIC; out->revision = OEW_CURRENT_MAP_REVISION;
    out->board_revision = qualification->identity.board_revision; out->pwm_frequency_hz = qualification->identity.pwm_frequency_hz;
    out->timer_arr = qualification->identity.timer_arr; out->adc_trigger_id = qualification->identity.adc_trigger_id;
    out->trigger_offset_ticks = qualification->identity.trigger_offset_ticks; out->deadtime_ticks = qualification->identity.deadtime_ticks;
    out->adc_clock_hz = qualification->identity.adc_clock_hz; out->adc_sample_cycles_x2 = qualification->identity.adc_sample_cycles_x2;
    out->adc_resolution = qualification->identity.adc_resolution; out->adc_config_signature = qualification->identity.adc_config_signature;
    out->current_calibration_signature = qualification->identity.current_calibration_signature; out->provenance = qualification->provenance;
    out->startup_sector = qualification->startup_sector; out->startup_window = qualification->startup_window;
    out->startup_hold_cycles = qualification->startup_hold_cycles; out->startup_mu = qualification->startup_mu;
    out->startup_mv = qualification->startup_mv; out->startup_mw = qualification->startup_mw;
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (!recon_valid(&recon[sector][window])) { memset(out, 0, sizeof(*out)); return MAP_CANDIDATE_RECON_BAD; }
            if (!region_valid(&regions[sector][window])) { memset(out, 0, sizeof(*out)); return MAP_CANDIDATE_REGION_BAD; }
            out->recon[sector][window] = recon[sector][window];
            out->region[sector][window] = regions[sector][window];
        }
    }
    if (!map_structure_valid(out)) { memset(out, 0, sizeof(*out)); return MAP_CANDIDATE_REGION_BAD; }
    out->crc32 = CurrentMap_CalculateCrc32(out);
    return MAP_CANDIDATE_OK;
}

bool MapCandidate_IsCanonical(const OewCurrentMap *map, const OewMapIdentity *identity,
                              const MapReferenceManifest *manifest)
{
    return map != 0 && identity != 0 && manifest != 0 && MapReferenceManifest_IsValid(manifest, identity) &&
           provenance_valid(&map->provenance) && map->crc32 == CurrentMap_CalculateCrc32(map) &&
           map->magic == OEW_CURRENT_MAP_MAGIC && map->revision == OEW_CURRENT_MAP_REVISION &&
           identity_matches(map, identity) && map_structure_valid(map);
}
