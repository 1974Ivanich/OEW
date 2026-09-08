#include "map_capture_profiles.h"

#include <string.h>

/*
 * Synthetic profile is deliberately impossible to enable in firmware builds.
 * It exists only to prove the board-profile API, exact-match admission and
 * qualification construction before real bench constants are available.
 */
#define MAP_CAPTURE_SYNTHETIC_PROFILE_ID 0x53594E54u /* "SYNT" */
#define MAP_CAPTURE_SYNTHETIC_BOARD_REV  0x7u
#define MAP_CAPTURE_SYNTHETIC_PWM_HZ     5000u
#define MAP_CAPTURE_SYNTHETIC_ARR        999u
#define MAP_CAPTURE_SYNTHETIC_ADC_CLOCK  170000000u
#define MAP_CAPTURE_SYNTHETIC_SAMPLE_X2  257u
#define MAP_CAPTURE_SYNTHETIC_RESOLUTION 0u
#define MAP_CAPTURE_SYNTHETIC_ADC_SIG    0x13572468u
#define MAP_CAPTURE_SYNTHETIC_CAL_SIG    0x24681357u
#define MAP_CAPTURE_SYNTHETIC_TRIGGER    0x4F455731u
#define MAP_CAPTURE_SYNTHETIC_OFFSET     12u
#define MAP_CAPTURE_SYNTHETIC_DEADTIME   68u
#define MAP_CAPTURE_SYNTHETIC_PULSES     16u
#define MAP_CAPTURE_SYNTHETIC_TIMEOUT    20u
#define MAP_CAPTURE_SYNTHETIC_SHUNT_MA   10000
#define MAP_CAPTURE_SYNTHETIC_VBUS_MIN   1000u
#define MAP_CAPTURE_SYNTHETIC_VBUS_MAX   70000u
#define MAP_CAPTURE_SYNTHETIC_CCR_U      500u
#define MAP_CAPTURE_SYNTHETIC_CCR_V      500u
#define MAP_CAPTURE_SYNTHETIC_CCR_W      500u
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

bool MapCaptureProfile_BuildUploadManifest(const OewMapIdentity *identity,
                                           MapReferenceManifest *out)
{
    if (identity == 0 || out == 0) return false;
    memset(out, 0, sizeof(*out));
    out->magic = MAP_REFERENCE_MAGIC;
    out->revision = MAP_REFERENCE_REVISION;
    out->board_revision = identity->board_revision;
    out->pwm_frequency_hz = identity->pwm_frequency_hz;
    out->timer_arr = identity->timer_arr;
    out->adc_trigger_id = identity->adc_trigger_id;
    out->adc_clock_hz = identity->adc_clock_hz;
    out->adc_sample_cycles_x2 = identity->adc_sample_cycles_x2;
    out->adc_resolution = identity->adc_resolution;
    out->deadtime_ticks = (uint8_t)identity->deadtime_ticks;
    out->source = MAP_REFERENCE_SOURCE_SCOPE;
    out->phase_a = 0u;
    out->phase_b = 1u;
    out->tool_build_id = 0x20260820u;
    out->record_count = OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT;
    out->crc32 = MapReferenceManifest_CalculateCrc32(out);
    return true;
}

#elif defined(OEW_MAP_CAPTURE) && OEW_MAP_CAPTURE && \
      defined(OEW_MAP_L3) && OEW_MAP_L3

/* ── Board-specific profile: STEVAL-IPM20B ×2 (STM32G474RE) ──────────────
 * Reviewed for bench capture (TZ_MAP_CAPTURE_BOARD_PROFILE, 01.09.2026).
 * Immutable allow-list: strict exact-match admission, no UART-provided CCR.
 * Profile id space: 12 approved variants, one per map row:
 *   id = MAP_CAPTURE_BOARD_PROFILE_ID + sector*2 + window   (0..11)
 * Identity fields below mirror the LIVE port identity
 * (MapCapturePort_GetMapIdentity), otherwise `mapcap build` fails
 * BLOCKED:IDENTITY. Do not copy SYNT constants — several differ from live
 * (see comments). */
#define MAP_CAPTURE_BOARD_PROFILE_ID  0x424F4152u /* "BOAR" */

/* Live identity (STEVAL bench, 170 MHz SYSCLK, PWM 10 MHz timer clock): */
#define MAP_CAPTURE_BOARD_BOARD_REV    7u
#define MAP_CAPTURE_BOARD_PWM_HZ       294u   /* cap_pwm_frequency_hz live:
                                               * tclk=10e6, denom=2*(PSC+1)*(ARR+1)
                                               * =2*17*1000 -> 294. NOTE: SYNT
                                               * 5000 is host-only; the live
                                               * formula double-counts PSC
                                               * (separate defect, not fixed
                                               * here to stay in lockstep). */
#define MAP_CAPTURE_BOARD_ARR          999u
#define MAP_CAPTURE_BOARD_ADC_CLOCK    42500000u /* CKMODE=11: HCLK/4 = 170e6/4 */
#define MAP_CAPTURE_BOARD_SAMPLE_X2    1281u  /* SMPR=111 -> 640.5 cyc *2 */
#define MAP_CAPTURE_BOARD_RESOLUTION   0u     /* 12-bit */
#define MAP_CAPTURE_BOARD_ADC_SIG      0x26B9B97Bu /* live from bench (ADC regs,
                                                   * stable across reboots);
                                                   * was placeholder 0x13572468 */
#define MAP_CAPTURE_BOARD_CAL_SIG      0x13552B12u /* scale constants + valid=1,
                                                   * NO raw offsets (they drift
                                                   * per calibration); was
                                                   * placeholder 0x24681357 */
#define MAP_CAPTURE_BOARD_TRIGGER      0x4F455731u
#define MAP_CAPTURE_BOARD_OFFSET       0u     /* scope-qualified stage */
#define MAP_CAPTURE_BOARD_DEADTIME     192u   /* dtg8 = encode(1500ns@170MHz)=0xC0 */
#define MAP_CAPTURE_BOARD_PULSES       8u     /* 4 grid points x 8 = 32/row:
                                               * MAP_ACCUM_MAX_SAMPLES_PER_ROW */
#define MAP_CAPTURE_BOARD_TIMEOUT      20u
#define MAP_CAPTURE_BOARD_SHUNT_MA     10000
#define MAP_CAPTURE_BOARD_VBUS_MIN     1000u
#define MAP_CAPTURE_BOARD_VBUS_MAX     70000u
#define MAP_CAPTURE_BOARD_MARGIN       110u   /* VfcApertureContract margin */
#define MAP_CAPTURE_BOARD_MIN_RECORDS  3u     /* aggregation per row */

/* Modulation vectors: strict phase orderings (mu>mv>mw etc.) matching
 * vfc_select_context, amplitude Q15 8192 -> CCR 375/500/625 (mid=500,
 * ARR=999), inside the qualified aperture 135..999. */
static const int16_t MAP_CAPTURE_BOARD_MOD[6][3] = {
    {  8192,     0, -8192 },  /* sector 0: mu>mv>mw */
    {  8192, -8192,     0 },  /* sector 1: mu>mw>mv */
    {     0,  8192, -8192 },  /* sector 2: mv>mu>mw */
    { -8192,  8192,     0 },  /* sector 3: mv>mw>mu */
    {     0, -8192,  8192 },  /* sector 4: mw>mu>mv */
    { -8192,     0,  8192 },  /* sector 5: mw>mv>mu */
};

/* Grid sweep per row (TZ_MAP_GRID_PROFILE, 02.09.2026): 4 approved vectors
 * per sector x window row — the center modulation point plus three +-4 CCR
 * offsets (262 Q15). The single-vector campaign of 01.09 was rejected by the
 * offline pipeline even with perfect scope data: solver MAP_SOLVER_SINGULAR
 * (shunt excitation ~ rank-1 within one vector) and certifier
 * MAP_CERT_DEGENERATE (all row cells at one modulation point). Multiple
 * vectors per row give the solver 2-D (idc1,idc2) excitation and the
 * certifier a non-degenerate cell grid. Offsets preserve the strict phase
 * ordering of the sector and stay inside the qualified aperture 135..999
 * and the row region bounds (+-2048 Q15). */
static const int16_t MAP_CAPTURE_BOARD_GRID[4][3] = {
    {  0,  0,  0 },
    {  4, -4,  0 },
    {  0,  4, -4 },
    { -4,  0,  4 },
};
#define MAP_CAPTURE_BOARD_POINTS 4u

/* Window separation: the two windows of a sector must not share modulation
 * points — otherwise both certified regions coincide and
 * CurrentMap_LoadMeasured rejects the artifact on overlap (verified 02.09).
 * Window 1 shifts the cluster +16 CCR on the max phase and -16 on the min
 * phase: sum invariant (mu+mv+mw = 3*500) and phase ordering are preserved,
 * and all 12 cluster boxes are pairwise disjoint (checked 66/66 pairs). */
#define MAP_CAPTURE_BOARD_WINDOW_SHIFT 16

static bool board_id_valid(uint32_t profile_id)
{
    return profile_id >= MAP_CAPTURE_BOARD_PROFILE_ID &&
           profile_id <  MAP_CAPTURE_BOARD_PROFILE_ID +
                         12u * MAP_CAPTURE_BOARD_POINTS;
}

/* CCR for (sector, window, grid point, phase i). int32 arithmetic (the
 * Q15->CCR conversion wraps for negative mod values; a single final uint16_t
 * cast yields the same value without relying on the wrap). */
static uint16_t board_ccr(uint8_t sector, uint8_t window, uint8_t point,
                          uint8_t i)
{
    int32_t ccr = 500 + (int32_t)MAP_CAPTURE_BOARD_MOD[sector][i] * 500 / 32768;
    if (window == 1u) {
        uint8_t mx = 0u;
        uint8_t mn = 0u;
        uint8_t j;
        for (j = 1u; j < 3u; ++j) {
            if (MAP_CAPTURE_BOARD_MOD[sector][j] > MAP_CAPTURE_BOARD_MOD[sector][mx]) {
                mx = j;
            }
            if (MAP_CAPTURE_BOARD_MOD[sector][j] < MAP_CAPTURE_BOARD_MOD[sector][mn]) {
                mn = j;
            }
        }
        if (i == mx) ccr += MAP_CAPTURE_BOARD_WINDOW_SHIFT;
        if (i == mn) ccr -= MAP_CAPTURE_BOARD_WINDOW_SHIFT;
    }
    return (uint16_t)(ccr + MAP_CAPTURE_BOARD_GRID[point][i]);
}

/* TIM8 drives the opposite ends of the same open-winding phases. To force
 * current through the windings the TIM8 vector must differ from TIM1. A
 * cyclic shift of the TIM1 CCRs gives a valid 3-phase vector, keeps all CCRs
 * inside the qualified aperture, and guarantees a non-zero differential on
 * every phase. The exact shift is part of the immutable board-qualified
 * allow-list and is matched exactly by MapCaptureProfile_IsApproved. */
static uint16_t board_tim8_ccr(uint8_t sector, uint8_t window, uint8_t point,
                               uint8_t i)
{
    return board_ccr(sector, window, point, (i + 1u) % 3u);
}

static bool board_request_matches(const MapCaptureRequest *request)
{
    uint8_t sector;
    uint8_t window;

    if (request == 0 ||
        request->pulse_count != MAP_CAPTURE_BOARD_PULSES ||
        request->timeout_periods != MAP_CAPTURE_BOARD_TIMEOUT ||
        request->max_abs_shunt_ma != MAP_CAPTURE_BOARD_SHUNT_MA ||
        request->min_vbus_mv != MAP_CAPTURE_BOARD_VBUS_MIN ||
        request->max_vbus_mv != MAP_CAPTURE_BOARD_VBUS_MAX ||
        request->trigger_revision != MAP_CAPTURE_BOARD_TRIGGER) {
        return false;
    }
    sector = request->sector_candidate;
    window = request->window_candidate;
    if (sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        window >= OEW_CURRENT_MAP_WINDOW_COUNT) {
        return false;
    }
    /* Exact-match against one of the 4 grid vectors of the (sector, window)
     * row. TIM1 and TIM8 patterns are stored independently: TIM8 is the
     * cyclic-shifted version that drives the far end of the open-winding. */
    {
        uint8_t point;
        uint8_t i;
        for (point = 0u; point < MAP_CAPTURE_BOARD_POINTS; ++point) {
            for (i = 0u; i < 3u; ++i) {
                if (request->tim1_ccr[i] != board_ccr(sector, window, point, i)) {
                    break;
                }
                if (request->tim8_ccr[i] != board_tim8_ccr(sector, window, point, i)) {
                    break;
                }
            }
            if (i == 3u) return true;
        }
    }
    return false;
}

bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{
    return board_request_matches(request);
}

bool MapCaptureProfile_BuildRequest(uint32_t profile_id, uint32_t capture_id,
                                    MapCaptureRequest *out)
{
    uint8_t sector;
    uint8_t window;
    uint8_t point;
    uint8_t i;

    if (!board_id_valid(profile_id) || out == 0 || capture_id == 0u) {
        return false;
    }
    point = (uint8_t)((profile_id - MAP_CAPTURE_BOARD_PROFILE_ID) %
                      MAP_CAPTURE_BOARD_POINTS);
    sector = (uint8_t)((profile_id - MAP_CAPTURE_BOARD_PROFILE_ID) /
                       (MAP_CAPTURE_BOARD_POINTS * 2u));
    window = (uint8_t)(((profile_id - MAP_CAPTURE_BOARD_PROFILE_ID) /
                        MAP_CAPTURE_BOARD_POINTS) & 1u);

    memset(out, 0, sizeof(*out));
    out->capture_id = capture_id;
    out->pulse_count = MAP_CAPTURE_BOARD_PULSES;
    out->timeout_periods = MAP_CAPTURE_BOARD_TIMEOUT;
    out->max_abs_shunt_ma = MAP_CAPTURE_BOARD_SHUNT_MA;
    out->min_vbus_mv = MAP_CAPTURE_BOARD_VBUS_MIN;
    out->max_vbus_mv = MAP_CAPTURE_BOARD_VBUS_MAX;
    out->sector_candidate = sector;
    out->window_candidate = window;
    out->trigger_revision = MAP_CAPTURE_BOARD_TRIGGER;
    for (i = 0u; i < 3u; ++i) {
        out->tim1_ccr[i] = board_ccr(sector, window, point, i);
        out->tim8_ccr[i] = board_tim8_ccr(sector, window, point, i);
    }
    return MapCaptureProfile_IsApproved(out);
}

bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out)
{
    uint8_t sector;
    uint8_t window;

    if (!board_id_valid(profile_id) || out == 0) return false;

    memset(out, 0, sizeof(*out));
    out->identity.board_revision = MAP_CAPTURE_BOARD_BOARD_REV;
    out->identity.pwm_frequency_hz = MAP_CAPTURE_BOARD_PWM_HZ;
    out->identity.timer_arr = MAP_CAPTURE_BOARD_ARR;
    out->identity.adc_trigger_id = MAP_CAPTURE_BOARD_TRIGGER;
    out->identity.trigger_offset_ticks = MAP_CAPTURE_BOARD_OFFSET;
    out->identity.deadtime_ticks = MAP_CAPTURE_BOARD_DEADTIME;
    out->identity.adc_clock_hz = MAP_CAPTURE_BOARD_ADC_CLOCK;
    out->identity.adc_sample_cycles_x2 = MAP_CAPTURE_BOARD_SAMPLE_X2;
    out->identity.adc_resolution = MAP_CAPTURE_BOARD_RESOLUTION;
    out->identity.adc_config_signature = MAP_CAPTURE_BOARD_ADC_SIG;
    out->identity.current_calibration_signature = MAP_CAPTURE_BOARD_CAL_SIG;
    out->min_records_per_row = MAP_CAPTURE_BOARD_MIN_RECORDS;
    out->startup_hold_cycles = 1u;
    out->startup_sector = 0u;
    out->startup_window = 0u;
    out->startup_mu = 0;
    out->startup_mv = 0;
    out->startup_mw = 0;
    out->min_margin_ticks = MAP_CAPTURE_BOARD_MARGIN;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            OewPwmRegion *region = &out->region[sector][window];
            CurrentReconEntry *recon = &out->recon[sector][window];

            region->mu_min = MAP_CAPTURE_BOARD_MOD[sector][0] - 2048;
            region->mu_max = MAP_CAPTURE_BOARD_MOD[sector][0] + 2048;
            region->mv_min = MAP_CAPTURE_BOARD_MOD[sector][1] - 2048;
            region->mv_max = MAP_CAPTURE_BOARD_MOD[sector][1] + 2048;
            region->mw_min = MAP_CAPTURE_BOARD_MOD[sector][2] - 2048;
            region->mw_max = MAP_CAPTURE_BOARD_MOD[sector][2] + 2048;
            region->min_margin_ticks = MAP_CAPTURE_BOARD_MARGIN;
            region->valid = 1u;

            /* FAIL-CLOSED: reconstruction coefficients are unknown until the
             * offline characterization (solver/certifier). A false identity
             * matrix here would make the map "ready" with fake recon —
             * deliberately rejected. */
            recon->valid = false;
            recon->phase_a = 0u;
            recon->phase_b = 1u;
            recon->m00 = 0;
            recon->m01 = 0;
            recon->m10 = 0;
            recon->m11 = 0;
        }
    }
    return true;
}

bool MapCaptureProfile_BuildUploadManifest(const OewMapIdentity *identity,
                                           MapReferenceManifest *out)
{
    if (identity == 0 || out == 0) return false;
    memset(out, 0, sizeof(*out));
    out->magic = MAP_REFERENCE_MAGIC;
    out->revision = MAP_REFERENCE_REVISION;
    out->board_revision = identity->board_revision;
    out->pwm_frequency_hz = identity->pwm_frequency_hz;
    out->timer_arr = identity->timer_arr;
    out->adc_trigger_id = identity->adc_trigger_id;
    out->adc_clock_hz = identity->adc_clock_hz;
    out->adc_sample_cycles_x2 = identity->adc_sample_cycles_x2;
    out->adc_resolution = identity->adc_resolution;
    out->deadtime_ticks = (uint8_t)identity->deadtime_ticks;
    out->source = MAP_REFERENCE_SOURCE_SCOPE;
    out->phase_a = 0u;
    out->phase_b = 1u;
    out->tool_build_id = 0x20260820u;
    out->record_count = OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT;
    out->crc32 = MapReferenceManifest_CalculateCrc32(out);
    return true;
}

#else

/* Default-deny: without the commissioning defines (OEW_MAP_CAPTURE &&
 * OEW_MAP_L3) no pattern is ever approved and no profile can be built. */
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

bool MapCaptureProfile_BuildUploadManifest(const OewMapIdentity *identity,
                                           MapReferenceManifest *out)
{
    (void)identity;
    (void)out;
    return false;
}

#endif
