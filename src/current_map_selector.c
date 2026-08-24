#include "current_map_selector.h"

#include <stddef.h>
#include <string.h>

#define OEW_Q15_MIN (-32768)
#define OEW_Q15_MAX 32767

static OewCurrentMap g_map;
static uint8_t g_ready;

static uint32_t crc32_update(uint32_t crc, uint8_t byte)
{
    uint8_t bit;

    crc ^= byte;
    for (bit = 0u; bit < 8u; ++bit) {
        crc = (crc & 1u) ? ((crc >> 1u) ^ 0xEDB88320u) : (crc >> 1u);
    }
    return crc;
}

uint32_t CurrentMap_CalculateCrc32(const OewCurrentMap *map)
{
    const uint8_t *bytes;
    size_t i;
    const size_t crc_offset = offsetof(OewCurrentMap, crc32);
    uint32_t crc = 0xFFFFFFFFu;

    if (map == 0) return 0u;
    bytes = (const uint8_t *)map;
    for (i = 0u; i < sizeof(*map); ++i) {
        uint8_t byte = (i >= crc_offset && i < crc_offset + sizeof(map->crc32))
                     ? 0u : bytes[i];
        crc = crc32_update(crc, byte);
    }
    return crc ^ 0xFFFFFFFFu;
}

static bool q15_bounds_sane(int16_t min, int16_t max)
{
    /* int16_t itself is the full accepted Q15 storage domain. */
    return min <= max;
}

static bool region_is_sane(const OewPwmRegion *region)
{
    if (region == 0 || !region->valid || region->min_margin_ticks == 0u) {
        return false;
    }
    return q15_bounds_sane(region->mu_min, region->mu_max) &&
           q15_bounds_sane(region->mv_min, region->mv_max) &&
           q15_bounds_sane(region->mw_min, region->mw_max);
}

static bool region_contains(const OewPwmRegion *region,
                            int16_t mu, int16_t mv, int16_t mw)
{
    return region_is_sane(region) &&
           mu >= region->mu_min && mu <= region->mu_max &&
           mv >= region->mv_min && mv <= region->mv_max &&
           mw >= region->mw_min && mw <= region->mw_max;
}

static bool regions_overlap(const OewPwmRegion *a, const OewPwmRegion *b)
{
    return !(a->mu_max < b->mu_min || b->mu_max < a->mu_min ||
             a->mv_max < b->mv_min || b->mv_max < a->mv_min ||
             a->mw_max < b->mw_min || b->mw_max < a->mw_min);
}

static bool identity_matches(const OewCurrentMap *map,
                             const OewMapIdentity *identity)
{
    return map->board_revision == identity->board_revision &&
           map->pwm_frequency_hz == identity->pwm_frequency_hz &&
           map->timer_arr == identity->timer_arr &&
           map->adc_trigger_id == identity->adc_trigger_id &&
           map->adc_clock_hz == identity->adc_clock_hz &&
           map->adc_sample_cycles_x2 == identity->adc_sample_cycles_x2 &&
           map->adc_resolution == identity->adc_resolution &&
           map->deadtime_ticks == identity->deadtime_ticks;
}

static bool map_regions_sane(const OewCurrentMap *map)
{
    uint8_t sector;
    uint8_t window;
    uint8_t other_sector;
    uint8_t other_window;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const OewPwmRegion *current = &map->region[sector][window];

            if (!region_is_sane(current) || !map->recon[sector][window].valid) {
                return false;
            }
            for (other_sector = sector; other_sector < OEW_CURRENT_MAP_SECTOR_COUNT;
                 ++other_sector) {
                uint8_t start_window = (other_sector == sector) ? (uint8_t)(window + 1u) : 0u;
                for (other_window = start_window;
                     other_window < OEW_CURRENT_MAP_WINDOW_COUNT;
                     ++other_window) {
                    if (regions_overlap(current, &map->region[other_sector][other_window])) {
                        /* No priority rule is permitted: the next vector must
                         * select exactly one physical measurement aperture. */
                        return false;
                    }
                }
            }
        }
    }
    return true;
}

void CurrentMap_Reset(void)
{
    memset(&g_map, 0, sizeof(g_map));
    g_ready = 0u;
    CurrentRecon_Reset();
}

bool CurrentMap_LoadMeasured(const OewCurrentMap *map,
                             const OewMapIdentity *active_identity)
{
    const OewPwmRegion *startup_region;

    if (map == 0 || active_identity == 0) return false;

    /* A failed replacement must not silently leave an earlier map armable. The
     * caller contract already requires PWM/ADC control to be stopped. */
    CurrentMap_Reset();

    if (map->magic != OEW_CURRENT_MAP_MAGIC ||
        map->revision != OEW_CURRENT_MAP_REVISION ||
        !identity_matches(map, active_identity) ||
        map->crc32 != CurrentMap_CalculateCrc32(map)) {
        return false;
    }
    if (map->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        map->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT ||
        map->startup_hold_cycles == 0u ||
        !map_regions_sane(map)) {
        return false;
    }

    startup_region = &map->region[map->startup_sector][map->startup_window];
    if (!region_contains(startup_region, map->startup_mu,
                          map->startup_mv, map->startup_mw)) {
        return false;
    }

    /* CurrentRecon validates phase IDs, gains and determinants before it
     * accepts the same 6x2 reconstruction table. */
    if (!CurrentRecon_LoadMap(map->recon)) return false;

    memcpy(&g_map, map, sizeof(g_map));
    g_ready = 1u;
    return true;
}

bool CurrentMap_IsReady(void)
{
    return g_ready != 0u && CurrentRecon_IsReady();
}

uint16_t CurrentMap_GetStartupHoldCycles(void)
{
    return CurrentMap_IsReady() ? g_map.startup_hold_cycles : 0u;
}

bool CurrentMap_SelectInitialStartupContext(PwmSampleContext *context,
                                            int16_t *mu, int16_t *mv, int16_t *mw)
{
    const OewPwmRegion *region;

    if (context == 0 || mu == 0 || mv == 0 || mw == 0 || !CurrentMap_IsReady()) {
        return false;
    }
    region = &g_map.region[g_map.startup_sector][g_map.startup_window];
    if (!region_contains(region, g_map.startup_mu,
                         g_map.startup_mv, g_map.startup_mw)) {
        return false;
    }

    *mu = g_map.startup_mu;
    *mv = g_map.startup_mv;
    *mw = g_map.startup_mw;
    context->sector = g_map.startup_sector;
    context->window = g_map.startup_window;
    context->valid = true;
    return true;
}

bool CurrentMap_SelectNextContext(int16_t mu, int16_t mv, int16_t mw,
                                  PwmSampleContext *context)
{
    uint8_t sector;
    uint8_t window;
    uint8_t matches = 0u;
    PwmSampleContext selected = { 0u, 0u, false };

    if (context == 0 || !CurrentMap_IsReady()) return false;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (region_contains(&g_map.region[sector][window], mu, mv, mw)) {
                selected.sector = sector;
                selected.window = window;
                selected.valid = true;
                ++matches;
            }
        }
    }

    /* Holes and overlaps are both forbidden. Do not leak a partially selected
     * context to caller on failure. */
    if (matches != 1u) return false;
    *context = selected;
    return true;
}
