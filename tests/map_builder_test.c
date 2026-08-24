#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_builder.h"

bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return frame != 0 && frame->status == ADC_FRAME_VALID;
}

static int16_t q15_to_ccr(int16_t value, uint16_t arr)
{
    int32_t mid = ((int32_t)arr + 1) / 2;
    int32_t ccr = mid + ((int32_t)value * mid) / 32768;
    if (ccr < 0) ccr = 0;
    if (ccr > arr) ccr = arr;
    return (int16_t)ccr;
}

static void build_qualification(MapBuilderQualification *q,
                                OewMapIdentity *identity)
{
    uint8_t sector;
    uint8_t window;

    memset(q, 0, sizeof(*q));
    q->identity.board_revision = 7u;
    q->identity.pwm_frequency_hz = 5000u;
    q->identity.timer_arr = 99u;
    q->identity.adc_trigger_id = 0x01020304u;
    q->min_records_per_row = 2u;
    q->startup_hold_cycles = 4u;
    q->startup_sector = 0u;
    q->startup_window = 0u;
    q->startup_mu = -28500;
    q->startup_mv = 0;
    q->startup_mw = 0;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            uint8_t slot = (uint8_t)(sector * 2u + window);
            OewPwmRegion *region = &q->region[sector][window];
            CurrentReconEntry *recon = &q->recon[sector][window];
            int16_t min = (int16_t)(-30000 + (int16_t)slot * 5000);

            region->mu_min = min;
            region->mu_max = (int16_t)(min + 3000);
            region->mv_min = -1000;
            region->mv_max = 1000;
            region->mw_min = -1000;
            region->mw_max = 1000;
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
    *identity = q->identity;
}

static void make_record(MapCaptureRecord *record,
                        const MapBuilderQualification *q,
                        uint8_t sector, uint8_t window,
                        uint32_t sequence, int16_t mu)
{
    uint8_t i;
    memset(record, 0, sizeof(*record));
    record->capture_id = 1u;
    record->fault_reason = MAP_CAPTURE_OK;
    record->frame.sequence = sequence;
    record->frame.status = ADC_FRAME_WINDOW_INVALID;
    record->frame.tim1_sector = sector;
    record->frame.sample_window = window;
    record->pwm.tim1_arr = (uint16_t)q->identity.timer_arr;
    record->pwm.pwm_frequency_hz = q->identity.pwm_frequency_hz;
    record->pwm.trigger_revision = q->identity.adc_trigger_id;
    record->pwm.tim1_ccr[0] = (uint16_t)q15_to_ccr(mu, record->pwm.tim1_arr);
    record->pwm.tim1_ccr[1] = (uint16_t)q15_to_ccr(0, record->pwm.tim1_arr);
    record->pwm.tim1_ccr[2] = (uint16_t)q15_to_ccr(0, record->pwm.tim1_arr);
    for (i = 0u; i < 3u; ++i) {
        record->pwm.tim8_ccr[i] = record->pwm.tim1_ccr[i];
    }
}

int main(void)
{
    MapBuilderQualification q;
    OewMapIdentity identity;
    OewCurrentMap map;
    MapBuilderStats stats;
    MapCaptureRecord record;
    uint8_t sector;
    uint8_t window;
    uint32_t sequence = 1u;

    build_qualification(&q, &identity);
    assert(MapBuilder_Begin(&q));
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            int16_t base = q.region[sector][window].mu_min + 1500;
            make_record(&record, &q, sector, window, sequence++, base);
            assert(MapBuilder_AddRecord(&record));
            make_record(&record, &q, sector, window, sequence++, base + 500);
            assert(MapBuilder_AddRecord(&record));
        }
    }
    assert(!MapBuilder_AddRecord(&record)); /* duplicate sequence */
    assert(MapBuilder_Finalize(&map, &stats));
    assert(stats.records_seen == 24u);
    /* v2 loader contract requires traceable provenance; in the new pipeline
     * the artifact writer stamps it before the map is loaded. */
    map.provenance.characterization_id = 0x01020304u;
    map.provenance.dataset_crc32 = 0xA1B2C3D4u;
    map.provenance.tool_build_id = 0x10203040u;
    map.provenance.qualification_revision = 1u;
    map.provenance.solver_revision = 2u;
    map.provenance.certifier_revision = 3u;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(CurrentMap_LoadMeasured(&map, &identity));
    assert(CurrentMap_IsReady());

    MapBuilder_Reset();
    CurrentMap_Reset();
    assert(MapBuilder_Begin(&q));
    make_record(&record, &q, 0u, 0u, 100u, q.region[0][0].mu_min + 500);
    assert(MapBuilder_AddRecord(&record));
    assert(!MapBuilder_Finalize(&map, &stats));
    assert(!CurrentMap_IsReady());

    puts("map_builder_test: PASS");
    return 0;
}
