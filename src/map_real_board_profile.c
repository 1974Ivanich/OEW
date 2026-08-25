#include "map_real_board_profile.h"

#include <string.h>

static bool identity_equal(const OewMapIdentity *a, const OewMapIdentity *b)
{
    return a != 0 && b != 0 &&
           a->board_revision == b->board_revision &&
           a->pwm_frequency_hz == b->pwm_frequency_hz &&
           a->timer_arr == b->timer_arr &&
           a->adc_trigger_id == b->adc_trigger_id &&
           a->trigger_offset_ticks == b->trigger_offset_ticks &&
           a->deadtime_ticks == b->deadtime_ticks &&
           a->adc_clock_hz == b->adc_clock_hz &&
           a->adc_sample_cycles_x2 == b->adc_sample_cycles_x2 &&
           a->adc_resolution == b->adc_resolution &&
           a->adc_config_signature == b->adc_config_signature &&
           a->current_calibration_signature == b->current_calibration_signature;
}

static bool row_valid(const MapRealProfileRow *row)
{
    if (row == 0 || row->pulse_count == 0u || row->timeout_periods == 0u ||
        row->max_abs_shunt_ma <= 0 || row->min_vbus_mv >= row->max_vbus_mv ||
        row->min_margin_ticks == 0u || row->sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        row->window >= OEW_CURRENT_MAP_WINDOW_COUNT) {
        return false;
    }

    /* The profile deliberately does not impose a synthetic relationship
     * between CCR1/2/3. Exact service-pattern limits remain board-profile data
     * and are checked by PWM_ServiceCaptureStart/validate_service_pattern(). */
    return true;
}

bool MapRealBoardProfile_IsComplete(const MapRealBoardProfile *profile)
{
    uint8_t seen[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    uint16_t i;

    if (profile == 0 || profile->revision != MAP_REAL_PROFILE_REVISION ||
        profile->row_count != MAP_REAL_PROFILE_MAX_ROWS ||
        profile->timing_qualified == 0u ||
        profile->external_reference_required != 1u) {
        return false;
    }

    memset(seen, 0, sizeof(seen));
    for (i = 0u; i < profile->row_count; ++i) {
        const MapRealProfileRow *row = &profile->row[i];
        if (!row_valid(row) || seen[row->sector][row->window] != 0u) {
            return false;
        }
        seen[row->sector][row->window] = 1u;
    }

    for (i = 0u; i < MAP_REAL_PROFILE_MAX_ROWS; ++i) {
        const uint8_t sector = (uint8_t)(i / OEW_CURRENT_MAP_WINDOW_COUNT);
        const uint8_t window = (uint8_t)(i % OEW_CURRENT_MAP_WINDOW_COUNT);
        if (seen[sector][window] == 0u) return false;
    }
    return true;
}

bool MapRealBoardProfile_Validate(const MapRealBoardProfile *profile,
                                  const OewMapIdentity *live_identity)
{
    if (!MapRealBoardProfile_IsComplete(profile) || live_identity == 0) {
        return false;
    }
    return identity_equal(&profile->identity, live_identity);
}

bool MapRealBoardProfile_BuildRequest(const MapRealBoardProfile *profile,
                                     uint16_t row_index,
                                     uint32_t capture_id,
                                     MapCaptureRequest *out)
{
    const MapRealProfileRow *row;

    if (!MapRealBoardProfile_IsComplete(profile) || out == 0 ||
        capture_id == 0u || row_index >= profile->row_count) {
        return false;
    }

    row = &profile->row[row_index];
    memset(out, 0, sizeof(*out));
    out->capture_id = capture_id;
    out->pulse_count = row->pulse_count;
    out->timeout_periods = row->timeout_periods;
    out->max_abs_shunt_ma = row->max_abs_shunt_ma;
    out->min_vbus_mv = row->min_vbus_mv;
    out->max_vbus_mv = row->max_vbus_mv;
    out->sector_candidate = row->sector;
    out->window_candidate = row->window;
    out->tim1_ccr[0] = row->tim1_ccr[0];
    out->tim1_ccr[1] = row->tim1_ccr[1];
    out->tim1_ccr[2] = row->tim1_ccr[2];
    out->tim8_ccr[0] = row->tim8_ccr[0];
    out->tim8_ccr[1] = row->tim8_ccr[1];
    out->tim8_ccr[2] = row->tim8_ccr[2];
    out->trigger_revision = profile->identity.adc_trigger_id;
    return true;
}
