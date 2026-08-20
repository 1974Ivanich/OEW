#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_measurement_accumulator.h"

static MapReferenceManifest manifest_make(void)
{
    MapReferenceManifest m;
    OewMapIdentity identity;
    memset(&m, 0, sizeof(m));
    m.magic = MAP_REFERENCE_MAGIC;
    m.revision = MAP_REFERENCE_REVISION;
    m.board_revision = 7u;
    m.pwm_frequency_hz = 5000u;
    m.timer_arr = 1000u;
    m.adc_trigger_id = 0x4F455731u;
    m.source = MAP_REFERENCE_SOURCE_SCOPE;
    m.phase_a = 0u;
    m.phase_b = 1u;
    m.tool_build_id = 0x20260820u;
    m.record_count = 8u;
    identity.board_revision = m.board_revision;
    identity.pwm_frequency_hz = m.pwm_frequency_hz;
    identity.timer_arr = m.timer_arr;
    identity.adc_trigger_id = m.adc_trigger_id;
    m.crc32 = MapReferenceManifest_CalculateCrc32(&m);
    assert(MapReferenceManifest_IsValid(&m, &identity));
    return m;
}

static MapMeasurementSample sample_make(const MapReferenceManifest *m,
                                        uint32_t sequence,
                                        int32_t u, int32_t v, int32_t w)
{
    MapMeasurementSample sample;
    memset(&sample, 0, sizeof(sample));
    sample.capture.frame.status = ADC_FRAME_WINDOW_INVALID;
    sample.capture.frame.sequence = sequence;
    sample.capture.frame.tim1_sector = 1u;
    sample.capture.frame.sample_window = 0u;
    sample.capture.capture_id = 100u;
    sample.capture.fault_reason = MAP_CAPTURE_OK;
    sample.capture.pwm.tim1_arr = (uint16_t)m->timer_arr;
    sample.capture.pwm.pwm_frequency_hz = m->pwm_frequency_hz;
    sample.capture.pwm.trigger_revision = m->adc_trigger_id;
    sample.reference.phase_u_ma = u;
    sample.reference.phase_v_ma = v;
    sample.reference.phase_w_ma = w;
    sample.reference.valid = 1u;
    sample.reference.source = m->source;
    sample.reference.sample_id = sequence;
    sample.timing.margin_ticks = 5u;
    sample.timing.adc_settled = 1u;
    sample.timing.scope_qualified = 1u;
    return sample;
}

static MapAccumQualification qualification_make(void)
{
    MapAccumQualification q;
    q.phase_a = 0u;
    q.phase_b = 1u;
    q.min_samples = 8u;
    q.mad_limit_ma = 250;
    q.kcl_limit_ma = 50;
    q.min_margin_ticks = 3u;
    return q;
}

static void test_manifest_and_ccr(void)
{
    MapReferenceManifest m = manifest_make();
    OewMapIdentity wrong;
    wrong.board_revision = 8u;
    wrong.pwm_frequency_hz = m.pwm_frequency_hz;
    wrong.timer_arr = m.timer_arr;
    wrong.adc_trigger_id = m.adc_trigger_id;
    assert(!MapReferenceManifest_IsValid(&m, &wrong));
    assert(MapMeasurement_CcrToQ15(500u, 1000u) == 0);
    assert(MapMeasurement_CcrToQ15(0u, 1000u) < -32700);
    assert(MapMeasurement_CcrToQ15(1000u, 1000u) > 32700);
    assert(MapMeasurement_CcrToQ15(0u, 0u) == 0);
}

static void test_row_pass_and_duplicate(void)
{
    MapReferenceManifest m = manifest_make();
    MapAccumQualification q = qualification_make();
    MapMeasurementAccumulator acc;
    MapAccumRowReport report;
    uint32_t i;
    assert(MapMeasurementAccumulator_Begin(&acc, &q, &m, 1u, 0u));
    for (i = 0u; i < 8u; ++i) {
        MapMeasurementSample s = sample_make(&m, i + 1u,
                                             1000 + (int32_t)i,
                                             -400 + (int32_t)i,
                                             -600);
        assert(MapMeasurementAccumulator_Add(&acc, &s) == MAP_ACCUM_OK);
    }
    {
        MapMeasurementSample duplicate = sample_make(&m, 1u, 1000, -400, -600);
        assert(MapMeasurementAccumulator_Add(&acc, &duplicate) ==
               MAP_ACCUM_SAMPLE_DUPLICATE);
    }
    assert(MapMeasurementAccumulator_FinalizeRow(&acc, &report) == MAP_ACCUM_OK);
    assert(report.ready == 1u);
    assert(report.accepted == 8u);
    assert(report.outliers == 0u);
    assert(report.min_margin_ticks == 5u);
}

static void test_filter_and_kcl_rejection(void)
{
    MapReferenceManifest m = manifest_make();
    MapAccumQualification q = qualification_make();
    MapMeasurementAccumulator acc;
    MapAccumRowReport report;
    uint32_t i;
    assert(MapMeasurementAccumulator_Begin(&acc, &q, &m, 1u, 0u));
    for (i = 0u; i < 8u; ++i) {
        int32_t u = (i == 7u) ? 5000 : 1000;
        int32_t v = (i == 7u) ? -2400 : -400;
        int32_t w = (i == 7u) ? -2600 : -600;
        MapMeasurementSample s = sample_make(&m, i + 1u, u, v, w);
        assert(MapMeasurementAccumulator_Add(&acc, &s) == MAP_ACCUM_OK);
    }
    assert(MapMeasurementAccumulator_FinalizeRow(&acc, &report) ==
           MAP_ACCUM_NOISE_TOO_HIGH);
    assert(report.ready == 0u);
}

int main(void)
{
    test_manifest_and_ccr();
    test_row_pass_and_duplicate();
    test_filter_and_kcl_rejection();
    puts("map_measurement_accumulator_test: PASS");
    return 0;
}
