#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "current_map_selector.h"

bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return frame != 0 && frame->status == ADC_FRAME_VALID;
}

static void build_map(OewCurrentMap *map, OewMapIdentity *identity)
{
    uint8_t sector;
    uint8_t window;

    memset(map, 0, sizeof(*map));
    map->magic = OEW_CURRENT_MAP_MAGIC;
    map->revision = OEW_CURRENT_MAP_REVISION;
    map->board_revision = 7u;
    map->pwm_frequency_hz = 5000u;
    map->timer_arr = 999u;
    map->adc_trigger_id = 0x01020304u;
    map->adc_clock_hz = 42500000u;
    map->adc_sample_cycles_x2 = 1281u;
    map->adc_resolution = 0u;
    map->deadtime_ticks = 0x0Fu;
    map->startup_sector = 0u;
    map->startup_window = 0u;
    map->startup_hold_cycles = 4u;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const uint8_t slot = (uint8_t)(sector * 2u + window);
            OewPwmRegion *region = &map->region[sector][window];
            CurrentReconEntry *recon = &map->recon[sector][window];
            const int16_t min = (int16_t)(-32768 + (int16_t)slot * 5000);

            region->mu_min = min;
            region->mu_max = (int16_t)(min + 4000);
            region->mv_min = -32768;
            region->mv_max = 32767;
            region->mw_min = -32768;
            region->mw_max = 32767;
            region->min_margin_ticks = 10u;
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
    map->startup_mu = -30000;
    map->startup_mv = 0;
    map->startup_mw = 0;
    map->crc32 = CurrentMap_CalculateCrc32(map);

    identity->board_revision = map->board_revision;
    identity->pwm_frequency_hz = map->pwm_frequency_hz;
    identity->timer_arr = map->timer_arr;
    identity->adc_trigger_id = map->adc_trigger_id;
    identity->adc_clock_hz = map->adc_clock_hz;
    identity->adc_sample_cycles_x2 = map->adc_sample_cycles_x2;
    identity->adc_resolution = map->adc_resolution;
    identity->deadtime_ticks = map->deadtime_ticks;
}

int main(void)
{
    OewCurrentMap map;
    OewMapIdentity identity;
    PwmSampleContext context;
    int16_t mu;
    int16_t mv;
    int16_t mw;

    CurrentMap_Reset();
    build_map(&map, &identity);
    assert(CurrentMap_LoadMeasured(&map, &identity));
    assert(CurrentMap_IsReady());

    assert(CurrentMap_SelectInitialStartupContext(&context, &mu, &mv, &mw));
    assert(context.valid && context.sector == 0u && context.window == 0u);
    assert(mu == -30000 && mv == 0 && mw == 0);

    assert(CurrentMap_SelectNextContext(-7000, 0, 0, &context));
    assert(context.valid && context.sector == 2u && context.window == 1u);
    assert(!CurrentMap_SelectNextContext(-28000, 0, 0, &context)); /* gap */

    build_map(&map, &identity);
    map.region[0][1] = map.region[0][0]; /* overlap must be rejected */
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(!CurrentMap_LoadMeasured(&map, &identity));
    assert(!CurrentMap_IsReady());

    build_map(&map, &identity);
    map.crc32 ^= 1u;
    assert(!CurrentMap_LoadMeasured(&map, &identity));
    assert(!CurrentMap_IsReady());

    puts("current_map_selector_test: PASS");
    return 0;
}
