#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#include "map_region_certifier.h"
#include "current_map_selector.h"

bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return frame != 0 && frame->status == ADC_FRAME_VALID;
}

static const int16_t reps[6][3] = {
    { 8000, 0, -8000 }, { 8000, -8000, 0 }, { 0, 8000, -8000 },
    { -8000, 8000, 0 }, { 0, -8000, 8000 }, { -8000, 0, 8000 }
};
static const int16_t high_reps[6][3] = {
    { 12000, 0, -12000 }, { 12000, -12000, 0 }, { 0, 12000, -12000 },
    { -12000, 12000, 0 }, { 0, -12000, 12000 }, { -12000, 0, 12000 }
};

static void q_make(MapRegionQualification *q)
{
    memset(q, 0, sizeof(*q));
    q->min_valid_cells = 4u;
    q->min_margin_ticks = 110u;
    q->use_geometry_bounds = true;
    q->geometry_window0_min_mod_q15 = 6000;
    q->geometry_window0_max_mod_q15 = 10000;
    q->geometry_window1_min_mod_q15 = 10000;
    q->geometry_window1_max_mod_q15 = 14000;
}

static void cells_make(MapGridCell cells[4], const int16_t rep[3])
{
    uint8_t i;
    for (i = 0u; i < 4u; ++i) {
        const int32_t scale = 100 + (int32_t)i * 5;
        cells[i].mu = (int16_t)(((int32_t)rep[0] * scale) / 100);
        cells[i].mv = (int16_t)(((int32_t)rep[1] * scale) / 100);
        cells[i].mw = (int16_t)(((int32_t)rep[2] * scale) / 100);
        cells[i].margin_ticks = 110u;
        cells[i].status = 1u;
    }
}

static void test_geometry_12_regions(void)
{
    MapRegionQualification q;
    MapGridCell cells[4];
    OewPwmRegion out;
    MapRegionReport report;
    uint8_t s, w;
    q_make(&q);
    for (s = 0u; s < 6u; ++s) {
        for (w = 0u; w < 2u; ++w) {
            cells_make(cells, w == 0u ? reps[s] : high_reps[s]);
            assert(MapRegionCertifyForSectorWindow(cells, 4u, s, w, &q, &out, &report) == MAP_CERT_OK);
            assert(report.ready && out.valid && out.reserved == 1u);
            assert(out.geometry_mod_min_q15 == (w == 0u ? 6000 : 10000));
            assert(out.geometry_mod_max_q15 == (w == 0u ? 10000 : 14000));
        }
    }
}

static void test_certified_outside_and_status(void)
{
    MapRegionQualification q;
    MapGridCell cells[4];
    OewPwmRegion out;
    MapRegionReport report;
    q_make(&q);
    cells_make(cells, reps[0]);
    cells[0].mu = 0; cells[0].mv = 8000; cells[0].mw = -8000;
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 0u, 0u, &q, &out, &report) == MAP_CERT_VALID_OUTSIDE);
    cells_make(cells, reps[0]); cells[0].status = 0u;
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 0u, 0u, &q, &out, &report) == MAP_CERT_UNTESTED_INSIDE);
    cells_make(cells, reps[0]); cells[0].status = 2u;
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 0u, 0u, &q, &out, &report) == MAP_CERT_INVALID_INSIDE);
}

static void test_boundary_ownership(void)
{
    MapRegionQualification q;
    MapGridCell cells[4];
    OewPwmRegion out;
    MapRegionReport report;
    q_make(&q);
    cells_make(cells, reps[0]);
    cells[0].mu = 4000; cells[0].mv = 4000; cells[0].mw = -8000;
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 0u, 0u, &q, &out, &report) == MAP_CERT_OK);
    cells_make(cells, reps[0]);
    cells[0].mu = 8000; cells[0].mv = -4000; cells[0].mw = -4000;
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 0u, 0u, &q, &out, &report) == MAP_CERT_OK);
    assert(MapRegionCertifyForSectorWindow(cells, 4u, 1u, 0u, &q, &out, &report) == MAP_CERT_VALID_OUTSIDE);
}

static void fill_geometry_map(OewCurrentMap *map, OewMapIdentity *id)
{
    uint8_t s, w;
    memset(map, 0, sizeof(*map)); memset(id, 0, sizeof(*id));
    id->board_revision = 7u; id->pwm_frequency_hz = 5000u; id->timer_arr = 999u;
    id->adc_trigger_id = 0x4F455731u; id->deadtime_ticks = 85u;
    id->adc_clock_hz = 42500000u; id->adc_sample_cycles_x2 = 1281u;
    id->adc_resolution = 0u; id->adc_config_signature = 0x11223344u;
    id->current_calibration_signature = 0x55667788u;
    map->magic = OEW_CURRENT_MAP_MAGIC; map->revision = OEW_CURRENT_MAP_REVISION;
    map->board_revision = id->board_revision; map->pwm_frequency_hz = id->pwm_frequency_hz;
    map->timer_arr = id->timer_arr; map->adc_trigger_id = id->adc_trigger_id;
    map->deadtime_ticks = id->deadtime_ticks; map->adc_clock_hz = id->adc_clock_hz;
    map->adc_sample_cycles_x2 = id->adc_sample_cycles_x2; map->adc_resolution = id->adc_resolution;
    map->adc_config_signature = id->adc_config_signature; map->current_calibration_signature = id->current_calibration_signature;
    map->provenance.characterization_id = 1u; map->provenance.dataset_crc32 = 2u;
    map->provenance.tool_build_id = 3u; map->provenance.qualification_revision = 4u;
    map->provenance.solver_revision = 5u; map->provenance.certifier_revision = 6u;
    map->startup_sector = 0u; map->startup_window = 0u; map->startup_hold_cycles = 1u;
    map->startup_mu = 8000; map->startup_mv = 0; map->startup_mw = -8000;
    for (s = 0u; s < 6u; ++s) for (w = 0u; w < 2u; ++w) {
        OewPwmRegion *r = &map->region[s][w]; CurrentReconEntry *c = &map->recon[s][w];
        r->mu_min = INT16_MIN; r->mu_max = INT16_MAX; r->mv_min = INT16_MIN; r->mv_max = INT16_MAX;
        r->mw_min = INT16_MIN; r->mw_max = INT16_MAX; r->min_margin_ticks = 110u;
        r->geometry_mod_min_q15 = w == 0u ? 6000 : 10000; r->geometry_mod_max_q15 = w == 0u ? 10000 : 14000;
        r->valid = 1u; r->reserved = 1u;
        c->valid = true; c->phase_a = 0u; c->phase_b = 1u; c->m00 = 1000; c->m01 = 0; c->m10 = 0; c->m11 = 1000;
    }
    map->crc32 = CurrentMap_CalculateCrc32(map);
}

static void test_geometry_admission_overlap(void)
{
    OewCurrentMap map; OewMapIdentity id; PwmSampleContext ctx = { 9u, 9u, false };
    fill_geometry_map(&map, &id); CurrentMap_Reset();
    assert(CurrentMap_LoadMeasured(&map, &id));
    assert(CurrentMap_SelectNextContext(8000, 0, -8000, &ctx)); assert(ctx.sector == 0u && ctx.window == 0u);
    assert(CurrentMap_SelectNextContext(12000, 0, -12000, &ctx)); assert(ctx.sector == 0u && ctx.window == 1u);
    assert(CurrentMap_SelectNextContext(10000, -5000, -5000, &ctx)); assert(ctx.sector == 0u && ctx.window == 1u);
    assert(!CurrentMap_SelectNextContext(5000, 0, -5000, &ctx));
    map.region[0][1].geometry_mod_min_q15 = 9000; map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(!CurrentMap_LoadMeasured(&map, &id)); assert(!CurrentMap_IsReady());
}

static void test_statistical_fallback(void)
{
    MapRegionQualification q; MapGridCell cells[4]; OewPwmRegion out; MapRegionReport report; uint8_t i;
    memset(&q, 0, sizeof(q)); q.min_valid_cells = 4u; q.guard_q15 = 1; q.min_margin_ticks = 3u;
    for (i = 0u; i < 4u; ++i) {
        cells[i].mu = (int16_t)(100 + i * 10); cells[i].mv = (int16_t)(-50 - i * 5);
        cells[i].mw = (int16_t)(-50 - i * 5); cells[i].margin_ticks = 5u; cells[i].status = 1u;
    }
    assert(MapRegionCertify(cells, 4u, &q, &out, &report) == MAP_CERT_OK);
    assert(out.reserved == 0u && out.geometry_mod_min_q15 == 0 && out.geometry_mod_max_q15 == 0);
}

int main(void)
{
    test_geometry_12_regions(); test_certified_outside_and_status(); test_boundary_ownership();
    test_geometry_admission_overlap(); test_statistical_fallback();
    puts("map_region_geometry_test: PASS"); return 0;
}
