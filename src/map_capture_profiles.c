#include "map_capture_profiles.h"

#include <string.h>

/*
 * Synthetic profile is deliberately impossible to enable in firmware builds.
 * It exists only to prove the board-profile API, exact-match admission and
 * qualification construction before real bench constants are available.
 */
#define MAP_CAPTURE_SYNTHETIC_PROFILE_ID 0x53594E54u /* "SYNT" */
#define MAP_CAPTURE_SYNTHETIC_BOARD_REV  0x7u
#define MAP_CAPTURE_SYNTHETIC_PWM_HZ     20000u
#define MAP_CAPTURE_SYNTHETIC_ARR        8499u
#define MAP_CAPTURE_SYNTHETIC_ADC_CLOCK  170000000u
#define MAP_CAPTURE_SYNTHETIC_SAMPLE_X2  257u
#define MAP_CAPTURE_SYNTHETIC_RESOLUTION 0u
#define MAP_CAPTURE_SYNTHETIC_ADC_SIG    0x13572468u
#define MAP_CAPTURE_SYNTHETIC_CAL_SIG    0x24681357u
#define MAP_CAPTURE_SYNTHETIC_TRIGGER    1u
#define MAP_CAPTURE_SYNTHETIC_OFFSET     12u
#define MAP_CAPTURE_SYNTHETIC_DEADTIME   68u
#define MAP_CAPTURE_SYNTHETIC_PULSES     1u
#define MAP_CAPTURE_SYNTHETIC_TIMEOUT    2u
#define MAP_CAPTURE_SYNTHETIC_SHUNT_MA   10000
#define MAP_CAPTURE_SYNTHETIC_VBUS_MIN   1000u
#define MAP_CAPTURE_SYNTHETIC_VBUS_MAX   70000u
#define MAP_CAPTURE_SYNTHETIC_CCR_U      4250u
#define MAP_CAPTURE_SYNTHETIC_CCR_V      4250u
#define MAP_CAPTURE_SYNTHETIC_CCR_W      4250u
#define MAP_CAPTURE_SYNTHETIC_MARGIN     1u

/* This guard is intentionally stronger than a normal feature flag: a
 * production firmware build cannot activate the synthetic profile merely by
 * defining OEW_MAP_SYNTHETIC_PROFILE. Host tests must explicitly identify
 * themselves with OEW_HOST_TEST. */
#if defined(OEW_MAP_SYNTHETIC_PROFILE) && OEW_MAP_SYNTHETIC_PROFILE && \
    defined(OEW_HOST_TEST) && OEW_HOST_TEST

static bool synthetic_request_matches(const MapCaptureRequest *request)
{
    uint32_t i;

    if (request == 0 || request->pulse_count != MAP_CAPTURE_SYNTHETIC_PULSES ||
        request->timeout_periods != MAP_CAPTURE_SYNTHETIC_TIMEOUT ||
        request->max_abs_shunt_ma != MAP_CAPTURE_SYNTHETIC_SHUNT_MA ||
        request->min_vbus_mv != MAP_CAPTURE_SYNTHETIC_VBUS_MIN ||
        request->max_vbus_mv != MAP_CAPTURE_SYNTHETIC_VBUS_MAX ||
        request->sector_candidate != 0u || request->window_candidate != 0u ||
        request->trigger_revision != MAP_CAPTURE_SYNTHETIC_TRIGGER) {
        return false;
    }

    for (i = 0u; i < 3u; ++i) {
        const uint16_t expected =
            (i == 0u) ? MAP_CAPTURE_SYNTHETIC_CCR_U :
            (i == 1u) ? MAP_CAPTURE_SYNTHETIC_CCR_V :
                        MAP_CAPTURE_SYNTHETIC_CCR_W;
        if (request->tim1_ccr[i] != expected || request->tim8_ccr[i] != expected) {
            return false;
        }
    }
    return true;
}

bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{
    return synthetic_request_matches(request);
}

bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out)
{
    uint8_t sector;
    uint8_t window;

    if (profile_id != MAP_CAPTURE_SYNTHETIC_PROFILE_ID || out == 0) return false;

    memset(out, 0, sizeof(*out));
    out->identity.board_revision = MAP_CAPTURE_SYNTHETIC_BOARD_REV;
    out->identity.pwm_frequency_hz = MAP_CAPTURE_SYNTHETIC_PWM_HZ;
    out->identity.timer_arr = MAP_CAPTURE_SYNTHETIC_ARR;
    out->identity.adc_trigger_id = MAP_CAPTURE_SYNTHETIC_TRIGGER;
    out->identity.trigger_offset_ticks = MAP_CAPTURE_SYNTHETIC_OFFSET;
    out->identity.deadtime_ticks = MAP_CAPTURE_SYNTHETIC_DEADTIME;
    out->identity.adc_clock_hz = MAP_CAPTURE_SYNTHETIC_ADC_CLOCK;
    out->identity.adc_sample_cycles_x2 = MAP_CAPTURE_SYNTHETIC_SAMPLE_X2;
    out->identity.adc_resolution = MAP_CAPTURE_SYNTHETIC_RESOLUTION;
    out->identity.adc_config_signature = MAP_CAPTURE_SYNTHETIC_ADC_SIG;
    out->identity.current_calibration_signature = MAP_CAPTURE_SYNTHETIC_CAL_SIG;
    out->min_records_per_row = 1u;
    out->startup_hold_cycles = 1u;
    out->startup_sector = 0u;
    out->startup_window = 0u;
    out->startup_mu = 0;
    out->startup_mv = 0;
    out->startup_mw = 0;
    
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            OewPwmRegion *region = &out->region[sector][window];
            CurrentReconEntry *recon = &out->recon[sector][window];

            region->mu_min = -32768;
            region->mu_max = 32767;
            region->mv_min = -32768;
            region->mv_max = 32767;
            region->mw_min = -32768;
            region->mw_max = 32767;
            region->min_margin_ticks = MAP_CAPTURE_SYNTHETIC_MARGIN;
            region->valid = 1u;

            recon->valid = true;
            recon->phase_a = 0u;
            recon->phase_b = 1u;
            recon->m00 = 1000;
            recon->m01 = 0;
            recon->m10 = 0;
            recon->m11 = 1000;
        }
    }
    return true;
}

bool MapCaptureProfile_BuildRequest(uint32_t profile_id, uint32_t capture_id,
                                    MapCaptureRequest *out)
{
    if (profile_id != MAP_CAPTURE_SYNTHETIC_PROFILE_ID || out == 0 ||
        capture_id == 0u) {
        return false;
    }

    memset(out, 0, sizeof(*out));
    out->capture_id = capture_id;
    out->pulse_count = MAP_CAPTURE_SYNTHETIC_PULSES;
    out->timeout_periods = MAP_CAPTURE_SYNTHETIC_TIMEOUT;
    out->max_abs_shunt_ma = MAP_CAPTURE_SYNTHETIC_SHUNT_MA;
    out->min_vbus_mv = MAP_CAPTURE_SYNTHETIC_VBUS_MIN;
    out->max_vbus_mv = MAP_CAPTURE_SYNTHETIC_VBUS_MAX;
    out->sector_candidate = 0u;
    out->window_candidate = 0u;
    out->trigger_revision = MAP_CAPTURE_SYNTHETIC_TRIGGER;
    out->tim1_ccr[0] = MAP_CAPTURE_SYNTHETIC_CCR_U;
    out->tim1_ccr[1] = MAP_CAPTURE_SYNTHETIC_CCR_V;
    out->tim1_ccr[2] = MAP_CAPTURE_SYNTHETIC_CCR_W;
    out->tim8_ccr[0] = MAP_CAPTURE_SYNTHETIC_CCR_U;
    out->tim8_ccr[1] = MAP_CAPTURE_SYNTHETIC_CCR_V;
    out->tim8_ccr[2] = MAP_CAPTURE_SYNTHETIC_CCR_W;
    return MapCaptureProfile_IsApproved(out);
}

#else

bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{
    (void)request;
    return false;
}

bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out)
{
    (void)profile_id;
    (void)out;
    return false;
}

bool MapCaptureProfile_BuildRequest(uint32_t profile_id, uint32_t capture_id,
                                    MapCaptureRequest *out)
{
    (void)profile_id;
    (void)capture_id;
    (void)out;
    return false;
}

#endif
