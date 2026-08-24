#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_measurement_solver.h"
#include "map_region_certifier.h"

static MapReferenceManifest manifest_make(void)
{
    MapReferenceManifest m;
    memset(&m, 0, sizeof(m));
    m.magic = MAP_REFERENCE_MAGIC; m.revision = MAP_REFERENCE_REVISION;
    m.board_revision = 7u; m.pwm_frequency_hz = 5000u;
    m.timer_arr = 1000u; m.adc_trigger_id = 0x4F455731u;
    m.adc_clock_hz = 42500000u;
    m.adc_sample_cycles_x2 = 1281u;
    m.adc_resolution = 0u;
    m.deadtime_ticks = 0x0Fu;
    m.source = MAP_REFERENCE_SOURCE_SCOPE; m.phase_a = 0u; m.phase_b = 1u;
    m.tool_build_id = 1u; m.record_count = 8u;
    m.crc32 = MapReferenceManifest_CalculateCrc32(&m);
    return m;
}

static MapMeasurementAccumulator accumulator_make(void)
{
    MapMeasurementAccumulator a;
    MapAccumQualification q;
    MapReferenceManifest m = manifest_make();
    uint32_t i;
    memset(&q, 0, sizeof(q)); q.phase_a = 0u; q.phase_b = 1u;
    q.min_samples = 8u; q.mad_limit_ma = 1000; q.kcl_limit_ma = 100;
    q.min_margin_ticks = 1u;
    assert(MapMeasurementAccumulator_Begin(&a, &q, &m, 1u, 0u));
    for (i = 0u; i < 8u; ++i) {
        MapMeasurementSample s;
        int32_t x0 = (i == 0u) ? 10000 : (int32_t)(1000 + i * 1000);
        int32_t x1 = (i == 0u) ? 20000 : (int32_t)(2000 + i * 1000);
        memset(&s, 0, sizeof(s));
        s.capture.capture_id = 1u; s.capture.fault_reason = MAP_CAPTURE_OK;
        s.capture.frame.status = ADC_FRAME_WINDOW_INVALID;
        s.capture.frame.sequence = i + 1u; s.capture.frame.tim1_sector = 1u;
        s.capture.frame.sample_window = 0u; s.capture.frame.idc1_ma = x0;
        s.capture.frame.idc2_ma = x1; s.capture.pwm.tim1_arr = 1000u;
        s.capture.pwm.pwm_frequency_hz = 5000u;
        s.capture.pwm.trigger_revision = 0x4F455731u;
        s.capture.pwm.deadtime_ticks = 0x0Fu;
        s.reference.valid = 1u; s.reference.source = MAP_REFERENCE_SOURCE_SCOPE;
        s.reference.sample_id = i + 1u;
        s.reference.phase_u_ma = 2 * x0 + x1;
        s.reference.phase_v_ma = -x0 + 3 * x1;
        s.reference.phase_w_ma = -(s.reference.phase_u_ma + s.reference.phase_v_ma);
        s.timing.adc_settled = 1u; s.timing.scope_qualified = 1u;
        s.timing.margin_ticks = 4u;
        assert(MapMeasurementAccumulator_Add(&a, &s) == MAP_ACCUM_OK);
    }
    return a;
}

static void test_ols(void)
{
    MapMeasurementAccumulator a = accumulator_make();
    MapSolverQualification q;
    MapSolverReport r;
    CurrentReconEntry out;
    MapSolverStatus status;
    memset(&q, 0, sizeof(q)); q.min_samples = 8u; q.holdout_samples = 2u;
    q.residual_rms_limit_ma = 1000; q.residual_max_limit_ma = 2000;
    q.bias_limit_ma = 1000; q.holdout_rms_limit_ma = 1000;
    q.kcl_rms_limit_ma = 100; q.max_condition_ratio = 100000u;
    q.min_abs_determinant = 1; q.min_abs_diagonal = 100;
    status = MapMeasurement_SolveM(&a, &q, &out, &r);
    assert(status == MAP_SOLVER_OK);
    assert(out.valid && out.phase_a == 0u && out.phase_b == 1u);
    assert(out.m00 >= 1950 && out.m00 <= 2050);
    assert(out.m01 >= 950 && out.m01 <= 1050);
    assert(out.m10 <= -950 && out.m10 >= -1050);
    assert(out.m11 >= 2950 && out.m11 <= 3050);
    assert(r.ready && r.holdout_samples == 2u);
}

static MapSolverQualification qualification_make(uint32_t condition)
{
    MapSolverQualification q;
    memset(&q, 0, sizeof(q));
    q.min_samples = 8u; q.holdout_samples = 2u;
    q.residual_rms_limit_ma = 1000; q.residual_max_limit_ma = 2000;
    q.bias_limit_ma = 1000; q.holdout_rms_limit_ma = 1000;
    q.kcl_rms_limit_ma = 100; q.max_condition_ratio = condition;
    q.min_abs_determinant = 1; q.min_abs_diagonal = 100;
    return q;
}

static void test_low_current_regression(void)
{
    MapMeasurementAccumulator a = accumulator_make();
    MapSolverQualification q = qualification_make(100000u);
    MapSolverReport r; CurrentReconEntry out;
    uint16_t i;
    for (i = 0u; i < a.sample_count; ++i) {
        int32_t x0 = 200 + (int32_t)i * 100;
        int32_t x1 = 800 - (int32_t)i * 100;
        a.samples[i].capture.frame.idc1_ma = x0;
        a.samples[i].capture.frame.idc2_ma = x1;
        a.samples[i].reference.phase_u_ma = 2 * x0 + x1;
        a.samples[i].reference.phase_v_ma = -x0 + 3 * x1;
        a.samples[i].reference.phase_w_ma =
            -(a.samples[i].reference.phase_u_ma + a.samples[i].reference.phase_v_ma);
    }
    assert(MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_OK);
    assert(r.residual_rms_ma < 50);
    assert(out.m00 >= 1800 && out.m00 <= 2200);
    assert(out.m01 >= 900 && out.m01 <= 1100);
    assert(r.determinant_scaled > 0);
}

static void test_high_current_wide_arithmetic(void)
{
    MapMeasurementAccumulator a = accumulator_make();
    MapSolverQualification q = qualification_make(100000u);
    MapSolverReport r; CurrentReconEntry out;
    uint16_t i;
    for (i = 0u; i < a.sample_count; ++i) {
        int32_t x0 = 15000;
        int32_t x1 = (i < 4u) ? 15000 : -15000;
        a.samples[i].capture.frame.idc1_ma = x0;
        a.samples[i].capture.frame.idc2_ma = x1;
        a.samples[i].reference.phase_u_ma = 2 * x0 + x1;
        a.samples[i].reference.phase_v_ma = -x0 + 3 * x1;
        a.samples[i].reference.phase_w_ma =
            -(a.samples[i].reference.phase_u_ma + a.samples[i].reference.phase_v_ma);
    }
    assert(MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_OK);
    assert(out.m00 >= 1950 && out.m00 <= 2050);
    assert(out.m01 >= 950 && out.m01 <= 1050);
    assert(out.m10 <= -950 && out.m10 >= -1050);
    assert(out.m11 >= 2950 && out.m11 <= 3050);
}

static void test_near_singular_boundary(void)
{
    MapMeasurementAccumulator a = accumulator_make();
    MapSolverQualification q = qualification_make(10u);
    MapSolverReport r; CurrentReconEntry out;
    uint16_t i;
    for (i = 0u; i < a.sample_count; ++i) {
        int32_t x0 = 5000 + (int32_t)i * 100;
        int32_t x1 = x0 + (int32_t)(i & 1u);
        a.samples[i].capture.frame.idc1_ma = x0;
        a.samples[i].capture.frame.idc2_ma = x1;
        a.samples[i].reference.phase_u_ma = 2 * x0 + x1;
        a.samples[i].reference.phase_v_ma = -x0 + 3 * x1;
        a.samples[i].reference.phase_w_ma =
            -(a.samples[i].reference.phase_u_ma + a.samples[i].reference.phase_v_ma);
    }
    assert(MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_SINGULAR ||
           MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_CONDITION_BAD);
}

static void test_singular(void)
{
    MapMeasurementAccumulator a = accumulator_make();
    MapSolverQualification q;
    MapSolverReport r; CurrentReconEntry out;
    uint16_t i;
    memset(&q, 0, sizeof(q)); q.min_samples = 8u; q.holdout_samples = 2u;
    q.residual_rms_limit_ma = 1000; q.residual_max_limit_ma = 2000;
    q.bias_limit_ma = 1000; q.holdout_rms_limit_ma = 1000;
    q.kcl_rms_limit_ma = 100; q.max_condition_ratio = 10u;
    q.min_abs_determinant = 1; q.min_abs_diagonal = 100;
    for (i = 0u; i < a.sample_count; ++i) {
        a.samples[i].capture.frame.idc2_ma = a.samples[i].capture.frame.idc1_ma;
    }
    assert(MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_SINGULAR ||
           MapMeasurement_SolveM(&a, &q, &out, &r) == MAP_SOLVER_CONDITION_BAD);
}

static void test_region_certifier(void)
{
    MapGridCell cells[9]; MapRegionQualification q; OewPwmRegion out;
    MapRegionReport r; uint16_t i = 0u; int16_t v;
    memset(&q, 0, sizeof(q)); q.min_valid_cells = 4u; q.guard_q15 = 1; q.min_margin_ticks = 3u;
    for (v = -2; v <= 2; v += 2) {
        int16_t w;
        for (w = -2; w <= 2; w += 2) {
            cells[i].mu = v; cells[i].mv = w; cells[i].mw = (int16_t)(-v-w);
            cells[i].margin_ticks = 5u; cells[i].status = 1u; ++i;
        }
    }
    assert(MapRegionCertify(cells, 9u, &q, &out, &r) == MAP_CERT_OK);
    assert(out.valid && r.ready && out.min_margin_ticks == 5u);
    cells[4].status = 2u;
    assert(MapRegionCertify(cells, 9u, &q, &out, &r) == MAP_CERT_INVALID_INSIDE);
}

int main(void)
{
    test_ols();
    test_low_current_regression();
    test_high_current_wide_arithmetic();
    test_near_singular_boundary();
    test_singular();
    test_region_certifier();
    puts("map_solver_certifier_test: PASS");
    return 0;
}
