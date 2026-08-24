#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "map_artifact_pipeline.h"

/* Per-row true transfer matrix (scaled by CURRENT_RECON_COEFF_SCALE=1000):
 *   phase_u = (a*idc1 + b*idc2)/1000, phase_v = (c*idc1 + d*idc2)/1000.
 * Coefficients differ per row so the test proves 12 independent solves. */
static int32_t row_a(uint8_t s, uint8_t w)
{ return (int32_t)(2000 + s * 100 + w * 50); }
static int32_t row_b(uint8_t s, uint8_t w)
{ (void)s; (void)w; return 1000; }
static int32_t row_c(uint8_t s, uint8_t w)
{ (void)s; (void)w; return -1000; }
static int32_t row_d(uint8_t s, uint8_t w)
{ return (int32_t)(3000 + s * 50 + w * 25); }

static MapReferenceManifest manifest_make(const OewMapIdentity *identity)
{
    MapReferenceManifest m;
    memset(&m, 0, sizeof(m));
    m.magic = MAP_REFERENCE_MAGIC;
    m.revision = MAP_REFERENCE_REVISION;
    m.board_revision = identity->board_revision;
    m.pwm_frequency_hz = identity->pwm_frequency_hz;
    m.timer_arr = identity->timer_arr;
    m.adc_trigger_id = identity->adc_trigger_id;
    m.adc_clock_hz = identity->adc_clock_hz;
    m.adc_sample_cycles_x2 = identity->adc_sample_cycles_x2;
    m.adc_resolution = identity->adc_resolution;
    m.deadtime_ticks = (uint8_t)identity->deadtime_ticks;
    m.source = MAP_REFERENCE_SOURCE_SCOPE;
    m.phase_a = 0u;
    m.phase_b = 1u;
    m.tool_build_id = 0x20260820u;
    m.record_count = OEW_CURRENT_MAP_SECTOR_COUNT *
                     OEW_CURRENT_MAP_WINDOW_COUNT * 8u;
    m.crc32 = MapReferenceManifest_CalculateCrc32(&m);
    return m;
}

static void identity_make(OewMapIdentity *identity)
{
    memset(identity, 0, sizeof(*identity));
    identity->board_revision = 7u;
    identity->pwm_frequency_hz = 20000u;
    identity->timer_arr = 8499u;
    identity->adc_trigger_id = 0x4F455731u;
    identity->trigger_offset_ticks = 0u;
    identity->deadtime_ticks = 85u;
    identity->adc_clock_hz = 42500000u;
    identity->adc_sample_cycles_x2 = 1281u;
    identity->adc_resolution = 0u;
    identity->adc_config_signature = 0x11223344u;
    identity->current_calibration_signature = 0x55667788u;
}

static OewMapProvenance provenance_make(void)
{
    OewMapProvenance p;
    memset(&p, 0, sizeof(p));
    p.characterization_id = 0x01020304u;
    p.dataset_crc32 = 0xA1B2C3D4u;
    p.tool_build_id = 0x20260820u;
    p.qualification_revision = 2u;
    p.solver_revision = 4u;
    p.certifier_revision = 2u;
    return p;
}

static void qualifications_make(MapPipelineQualifications *q)
{
    memset(q, 0, sizeof(*q));
    q->accum.phase_a = 0u;
    q->accum.phase_b = 1u;
    q->accum.min_samples = 8u;
    q->accum.mad_limit_ma = 1000;
    q->accum.kcl_limit_ma = 100;
    q->accum.min_margin_ticks = 1u;
    q->solver.min_samples = 8u;
    q->solver.holdout_samples = 2u;
    q->solver.residual_rms_limit_ma = 1000;
    q->solver.residual_max_limit_ma = 2000;
    q->solver.bias_limit_ma = 1000;
    q->solver.holdout_rms_limit_ma = 1000;
    q->solver.kcl_rms_limit_ma = 100;
    q->solver.max_condition_ratio = 100000u;
    q->solver.min_abs_determinant = 1;
    q->solver.min_abs_diagonal = 100;
    q->region.min_valid_cells = 4u;
    q->region.guard_q15 = 1;
    q->region.min_margin_ticks = 3u;
}

static void row_samples_make(uint8_t sector, uint8_t window,
                             MapMeasurementSample *samples, uint16_t count,
                             const MapReferenceManifest *manifest,
                             bool degenerate)
{
    uint16_t i;
    const int32_t a = row_a(sector, window);
    const int32_t b = row_b(sector, window);
    const int32_t c = row_c(sector, window);
    const int32_t d = row_d(sector, window);

    for (i = 0u; i < count; ++i) {
        MapMeasurementSample *s = &samples[i];
        int32_t x0 = (int32_t)(1000 + i * 1000);
        int32_t x1 = (int32_t)(2000 + i * 500);
        int32_t refu, refv, refw;
        if (degenerate) x1 = x0; /* identical columns -> singular M */

        refu = (int32_t)(((int64_t)a * x0 + (int64_t)b * x1) / 1000);
        refv = (int32_t)(((int64_t)c * x0 + (int64_t)d * x1) / 1000);
        refw = (int32_t)(-((int64_t)refu + refv));

        memset(s, 0, sizeof(*s));
        s->capture.capture_id = 1u;
        s->capture.fault_reason = MAP_CAPTURE_OK;
        s->capture.frame.status = ADC_FRAME_WINDOW_INVALID;
        s->capture.frame.sequence = (uint32_t)i + 1u;
        s->capture.frame.tim1_sector = sector;
        s->capture.frame.sample_window = window;
        s->capture.frame.idc1_ma = x0;
        s->capture.frame.idc2_ma = x1;
        s->capture.frame.ict_ma = 0;
        s->capture.frame.vbus_mv = 12000;
        s->capture.pwm.tim1_arr = manifest->timer_arr;
        s->capture.pwm.pwm_frequency_hz = manifest->pwm_frequency_hz;
        s->capture.pwm.trigger_revision = manifest->adc_trigger_id;
        s->capture.pwm.deadtime_ticks = manifest->deadtime_ticks;
        s->reference.valid = 1u;
        s->reference.source = manifest->source;
        s->reference.sample_id = (uint32_t)i + 1u;
        s->reference.phase_u_ma = refu;
        s->reference.phase_v_ma = refv;
        s->reference.phase_w_ma = refw;
        s->timing.adc_settled = 1u;
        s->timing.scope_qualified = 1u;
        s->timing.margin_ticks = 4u;
    }
}

/* Modulation-space centre of row (s,w). Rows are spaced 1000/500 q15 apart
 * so their certified 2x2 regions are pairwise disjoint (the loader forbids
 * overlapping regions). */
static int32_t row_cmu(uint8_t s, uint8_t w)
{ return (int32_t)(-3000 + s * 1000 + w * 500); }

static void row_cells_make(uint8_t sector, uint8_t window,
                           MapGridCell *cells, uint16_t count)
{
    uint16_t i = 0u;
    int16_t v;
    (void)count;
    for (v = -2; v <= 2; v += 2) {
        int16_t w;
        for (w = -2; w <= 2; w += 2) {
            cells[i].mu = (int16_t)(row_cmu(sector, window) + v);
            cells[i].mv = w;
            cells[i].mw = (int16_t)(-cells[i].mu - cells[i].mv);
            cells[i].margin_ticks = 5u;
            cells[i].status = 1u; /* VALID */
            ++i;
        }
    }
}

static void input_make(MapPipelineInput *input)
{
    static MapMeasurementSample samples[OEW_CURRENT_MAP_SECTOR_COUNT]
                                       [OEW_CURRENT_MAP_WINDOW_COUNT][8u];
    static MapGridCell cells[OEW_CURRENT_MAP_SECTOR_COUNT]
                            [OEW_CURRENT_MAP_WINDOW_COUNT][9u];
    uint8_t sector;
    uint8_t window;

    memset(input, 0, sizeof(*input));
    identity_make(&input->identity);
    input->provenance = provenance_make();
    input->startup_sector = 2u;
    input->startup_window = 1u;
    input->startup_hold_cycles = 25u;
    input->startup_mu = (int16_t)row_cmu(2u, 1u);
    input->startup_mv = 0;
    input->startup_mw = (int16_t)(-input->startup_mu);
    input->manifest = manifest_make(&input->identity);
    qualifications_make(&input->qualifications);

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            row_samples_make(sector, window, samples[sector][window], 8u,
                             &input->manifest, false);
            row_cells_make(sector, window, cells[sector][window], 9u);
            input->rows[sector][window].samples = samples[sector][window];
            input->rows[sector][window].sample_count = 8u;
            input->rows[sector][window].cells = cells[sector][window];
            input->rows[sector][window].cell_count = 9u;
        }
    }
}

int main(void)
{
    MapPipelineInput input;
    MapPipelineReport report;
    OewCurrentMap map;
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t sector;
    uint8_t window;

    /* Happy path: all 12 rows qualify -> artifact is produced, serializes to
     * the canonical 497 bytes and loads back into the firmware consumer. */
    input_make(&input);
    assert(MapArtifactPipeline_Run(&input, &map, &report) == MAP_PIPELINE_OK);
    assert(report.ready == 1u);
    assert(MapArtifactWriter_EncodeBinary(&map, wire, sizeof(wire)) ==
           OEW_CURRENT_MAP_WIRE_SIZE);
    assert(CurrentMap_LoadMeasured(&map, &input.identity));
    assert(CurrentMap_IsReady());

    /* Every row must carry its own solved matrix: verify three distinct rows. */
    assert(map.recon[0][0].m00 >= row_a(0, 0) - 50 &&
           map.recon[0][0].m00 <= row_a(0, 0) + 50);
    assert(map.recon[0][0].m11 >= row_d(0, 0) - 50 &&
           map.recon[0][0].m11 <= row_d(0, 0) + 50);
    assert(map.recon[3][1].m00 >= row_a(3, 1) - 50 &&
           map.recon[3][1].m00 <= row_a(3, 1) + 50);
    assert(map.recon[5][1].m00 >= row_a(5, 1) - 50 &&
           map.recon[5][1].m00 <= row_a(5, 1) + 50);

    /* Regions certified from the grid cells: each row owns a disjoint
     * 2x2 region centred at row_cmu(s,w). */
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            assert(map.region[sector][window].valid == 1u);
            assert(map.region[sector][window].min_margin_ticks == 5u);
            assert(map.region[sector][window].mu_min ==
                   (int16_t)(row_cmu(sector, window) - 1));
            assert(map.region[sector][window].mu_max ==
                   (int16_t)(row_cmu(sector, window) + 1));
            assert(map.region[sector][window].mv_min == -1);
            assert(map.region[sector][window].mv_max == 1);
        }
    }

    /* Fail-closed: an under-sampled row aborts the whole pipeline. */
    input_make(&input);
    input.rows[0][0].sample_count = 4u;
    assert(MapArtifactPipeline_Run(&input, &map, &report) ==
           MAP_PIPELINE_ACCUM_FAILED);
    assert(report.failed_sector == 0u && report.failed_window == 0u);
    assert(report.accum_status == MAP_ACCUM_NOT_ENOUGH_SAMPLES);
    assert(report.ready == 0u);

    /* Fail-closed: a singular row cannot contribute coefficients. */
    input_make(&input);
    row_samples_make(2u, 0u, (MapMeasurementSample *)input.rows[2][0].samples,
                     8u, &input.manifest, true);
    assert(MapArtifactPipeline_Run(&input, &map, &report) ==
           MAP_PIPELINE_SOLVER_FAILED);
    assert(report.failed_sector == 2u && report.failed_window == 0u);
    assert(report.ready == 0u);

    /* Fail-closed: empty row is a dataset error, not a map. */
    input_make(&input);
    input.rows[1][1].samples = 0;
    assert(MapArtifactPipeline_Run(&input, &map, &report) ==
           MAP_PIPELINE_BAD_ARGUMENT);
    assert(report.ready == 0u);

    puts("map_artifact_pipeline_test: PASS");
    return 0;
}
