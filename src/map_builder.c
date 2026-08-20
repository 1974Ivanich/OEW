#include "map_builder.h"

#include <string.h>

#define MAP_BUILDER_Q15_MIN (-32768)
#define MAP_BUILDER_Q15_MAX 32767

static MapBuilderQualification g_qualification;
static MapBuilderStats g_stats;
static uint32_t g_last_sequence[OEW_CURRENT_MAP_SECTOR_COUNT]
                               [OEW_CURRENT_MAP_WINDOW_COUNT];

static bool phase_pair_sane(const CurrentReconEntry *entry)
{
    return entry != 0 && entry->valid && entry->phase_a < 3u &&
           entry->phase_b < 3u && entry->phase_a != entry->phase_b;
}

static bool region_sane(const OewPwmRegion *region)
{
    return region != 0 && region->valid && region->min_margin_ticks != 0u &&
           region->mu_min <= region->mu_max &&
           region->mv_min <= region->mv_max &&
           region->mw_min <= region->mw_max;
}

static bool qualification_sane(const MapBuilderQualification *qualification)
{
    uint8_t sector;
    uint8_t window;

    if (qualification == 0 || qualification->identity.board_revision == 0u ||
        qualification->identity.pwm_frequency_hz == 0u ||
        qualification->identity.timer_arr == 0u ||
        qualification->identity.adc_trigger_id == 0u ||
        qualification->min_records_per_row == 0u ||
        qualification->startup_hold_cycles == 0u ||
        qualification->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        qualification->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT) {
        return false;
    }

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (!region_sane(&qualification->region[sector][window]) ||
                !phase_pair_sane(&qualification->recon[sector][window])) {
                return false;
            }
        }
    }
    return true;
}

static int16_t ccr_to_q15(uint16_t ccr, uint32_t timer_arr)
{
    const int32_t mid = (int32_t)((timer_arr + 1u) / 2u);
    const int32_t delta = (int32_t)ccr - mid;
    int64_t value;

    if (mid <= 0) return 0;
    value = ((int64_t)delta * 32768LL) / mid;
    if (value < MAP_BUILDER_Q15_MIN) value = MAP_BUILDER_Q15_MIN;
    if (value > MAP_BUILDER_Q15_MAX) value = MAP_BUILDER_Q15_MAX;
    return (int16_t)value;
}

static bool vector_in_region(const OewPwmRegion *region,
                             int16_t mu, int16_t mv, int16_t mw)
{
    return region_sane(region) &&
           mu >= region->mu_min && mu <= region->mu_max &&
           mv >= region->mv_min && mv <= region->mv_max &&
           mw >= region->mw_min && mw <= region->mw_max;
}

void MapBuilder_Reset(void)
{
    memset(&g_qualification, 0, sizeof(g_qualification));
    memset(&g_stats, 0, sizeof(g_stats));
    memset(g_last_sequence, 0, sizeof(g_last_sequence));
}

bool MapBuilder_Begin(const MapBuilderQualification *qualification)
{
    if (!qualification_sane(qualification)) {
        MapBuilder_Reset();
        return false;
    }
    MapBuilder_Reset();
    g_qualification = *qualification;
    g_stats.initialized = 1u;
    return true;
}

bool MapBuilder_AddRecord(const MapCaptureRecord *record)
{
    uint8_t sector;
    uint8_t window;
    int16_t mu;
    int16_t mv;
    int16_t mw;

    if (!g_stats.initialized || record == 0 ||
        record->fault_reason != MAP_CAPTURE_OK || record->capture_id == 0u ||
        record->frame.status != ADC_FRAME_WINDOW_INVALID ||
        record->frame.tim1_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        record->frame.sample_window >= OEW_CURRENT_MAP_WINDOW_COUNT) {
        return false;
    }

    sector = record->frame.tim1_sector;
    window = record->frame.sample_window;
    if (record->pwm.trigger_revision != g_qualification.identity.adc_trigger_id ||
        record->pwm.tim1_arr != g_qualification.identity.timer_arr ||
        record->pwm.pwm_frequency_hz != g_qualification.identity.pwm_frequency_hz ||
        record->pwm.tim1_ccr[0] != record->pwm.tim8_ccr[0] ||
        record->pwm.tim1_ccr[1] != record->pwm.tim8_ccr[1] ||
        record->pwm.tim1_ccr[2] != record->pwm.tim8_ccr[2]) {
        return false;
    }
    if (record->frame.sequence == 0u ||
        record->frame.sequence == g_last_sequence[sector][window]) {
        return false;
    }

    mu = ccr_to_q15(record->pwm.tim1_ccr[0], record->pwm.tim1_arr);
    mv = ccr_to_q15(record->pwm.tim1_ccr[1], record->pwm.tim1_arr);
    mw = ccr_to_q15(record->pwm.tim1_ccr[2], record->pwm.tim1_arr);
    if (!vector_in_region(&g_qualification.region[sector][window], mu, mv, mw)) {
        return false;
    }
    if (g_stats.records_seen >= MAP_BUILDER_MAX_RECORDS) return false;

    g_last_sequence[sector][window] = record->frame.sequence;
    ++g_stats.row_records[sector][window];
    ++g_stats.records_seen;
    return true;
}

bool MapBuilder_Finalize(OewCurrentMap *out, MapBuilderStats *stats)
{
    uint8_t sector;
    uint8_t window;

    if (!g_stats.initialized || out == 0) return false;
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (g_stats.row_records[sector][window] <
                g_qualification.min_records_per_row) {
                if (stats != 0) *stats = g_stats;
                return false;
            }
        }
    }

    memset(out, 0, sizeof(*out));
    out->magic = OEW_CURRENT_MAP_MAGIC;
    out->revision = OEW_CURRENT_MAP_REVISION;
    out->board_revision = g_qualification.identity.board_revision;
    out->pwm_frequency_hz = g_qualification.identity.pwm_frequency_hz;
    out->timer_arr = g_qualification.identity.timer_arr;
    out->adc_trigger_id = g_qualification.identity.adc_trigger_id;
    out->startup_sector = g_qualification.startup_sector;
    out->startup_window = g_qualification.startup_window;
    out->startup_hold_cycles = g_qualification.startup_hold_cycles;
    out->startup_mu = g_qualification.startup_mu;
    out->startup_mv = g_qualification.startup_mv;
    out->startup_mw = g_qualification.startup_mw;
    memcpy(out->region, g_qualification.region, sizeof(out->region));
    memcpy(out->recon, g_qualification.recon, sizeof(out->recon));
    out->crc32 = CurrentMap_CalculateCrc32(out);
    if (stats != 0) *stats = g_stats;
    return true;
}

void MapBuilder_GetStats(MapBuilderStats *out)
{
    if (out != 0) *out = g_stats;
}
