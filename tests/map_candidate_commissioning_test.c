#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_candidate.h"
#include "map_commissioning.h"

static struct {
    bool active;
    bool fault;
    bool ready;
    unsigned loads;
    OewMapIdentity identity;
} g;

static MapReferenceManifest manifest_make(OewMapIdentity *identity)
{
    MapReferenceManifest m;
    memset(&m, 0, sizeof(m));
    identity->board_revision = 7u;
    identity->pwm_frequency_hz = 5000u;
    identity->timer_arr = 1000u;
    identity->adc_trigger_id = 0x4F455731u;
    identity->trigger_offset_ticks = 0u;
    identity->deadtime_ticks = 85u;
    identity->adc_clock_hz = 42500000u;
    identity->adc_sample_cycles_x2 = 1281u;
    identity->adc_resolution = 0u;
    identity->adc_config_signature = 0x11223344u;
    identity->current_calibration_signature = 0x55667788u;

    m.magic = MAP_REFERENCE_MAGIC;
    m.revision = MAP_REFERENCE_REVISION;
    m.board_revision = identity->board_revision;
    m.pwm_frequency_hz = identity->pwm_frequency_hz;
    m.timer_arr = identity->timer_arr;
    m.adc_trigger_id = identity->adc_trigger_id;
    m.adc_clock_hz = identity->adc_clock_hz;
    m.adc_sample_cycles_x2 = identity->adc_sample_cycles_x2;
    m.adc_resolution = identity->adc_resolution;
    m.deadtime_ticks = identity->deadtime_ticks;
    m.source = MAP_REFERENCE_SOURCE_SCOPE;
    m.phase_a = 0u;
    m.phase_b = 1u;
    m.tool_build_id = 0x20260820u;
    m.record_count = MAP_CANDIDATE_REQUIRED_ROWS;
    m.crc32 = MapReferenceManifest_CalculateCrc32(&m);
    return m;
}

static MapCandidateQualification qualification_make(void)
{
    MapCandidateQualification q;
    memset(&q, 0, sizeof(q));
    q.manifest = manifest_make(&q.identity);
    q.provenance.characterization_id = 0x01020304u;
    q.provenance.dataset_crc32 = 0xAABBCCDDu;
    q.provenance.tool_build_id = 0x10203040u;
    q.provenance.qualification_revision = 2u;
    q.provenance.solver_revision = 4u;
    q.provenance.certifier_revision = 2u;
    q.startup_sector = 0u;
    q.startup_window = 0u;
    q.startup_hold_cycles = 4u;
    q.startup_mu = 0;
    q.startup_mv = 0;
    q.startup_mw = 0;
    return q;
}

static void fill_tables(CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT],
                        OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT])
{
    uint8_t s, w;
    memset(recon, 0, sizeof(CurrentReconEntry) * OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT);
    memset(regions, 0, sizeof(OewPwmRegion) * OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT);
    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            recon[s][w].valid = true;
            recon[s][w].phase_a = 0u;
            recon[s][w].phase_b = 1u;
            recon[s][w].m00 = 1000;
            recon[s][w].m01 = 0;
            recon[s][w].m10 = 0;
            recon[s][w].m11 = 1000;
            regions[s][w].mu_min = -1000;
            regions[s][w].mu_max = 1000;
            regions[s][w].mv_min = -1000;
            regions[s][w].mv_max = 1000;
            regions[s][w].mw_min = -1000;
            regions[s][w].mw_max = 1000;
            regions[s][w].min_margin_ticks = 5u;
            regions[s][w].valid = 1u;
        }
    }
}

static OewCurrentMap build_map(MapCandidateQualification *q)
{
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewCurrentMap map;
    fill_tables(recon, regions);
    assert(MapCandidate_Build(q, recon, regions, &map) == MAP_CANDIDATE_OK);
    assert(MapCandidate_IsCanonical(&map, &q->identity, &q->manifest));
    return map;
}

static bool capture_active(void) { return g.active; }
static bool false_fn(void) { return false; }
static bool fault_fn(void) { return g.fault; }
static bool get_identity(OewMapIdentity *identity) { *identity = g.identity; return true; }
static bool load_map(const OewCurrentMap *map, const OewMapIdentity *identity)
{
    assert(map != 0 && identity != 0);
    ++g.loads;
    return true;
}
static bool map_ready(void) { return g.ready; }

static MapCommissioningOps ops_make(void)
{
    MapCommissioningOps ops;
    memset(&ops, 0, sizeof(ops));
    ops.capture_active = capture_active;
    ops.foc_running = false_fn;
    ops.vfc_running = false_fn;
    ops.autotune_active = false_fn;
    ops.pwm_enabled = false_fn;
    ops.adc_injected_armed = false_fn;
    ops.protect_fault = fault_fn;
    ops.get_identity = get_identity;
    ops.load_measured = load_map;
    ops.map_ready = map_ready;
    return ops;
}

static void test_candidate_and_crc_rejection(void)
{
    MapCandidateQualification q = qualification_make();
    OewCurrentMap map = build_map(&q);
    MapCommissioningOps ops = ops_make();
    g.identity = q.identity;
    g.ready = true;
    g.loads = 0u;
    g.active = false;
    g.fault = false;
    assert(MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
    assert(g.loads == 1u);
    map.crc32 ^= 1u;
    assert(!MapCandidate_IsCanonical(&map, &q.identity, &q.manifest));
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
}

static void test_active_control_and_identity_rejection(void)
{
    MapCandidateQualification q = qualification_make();
    OewCurrentMap map = build_map(&q);
    MapCommissioningOps ops = ops_make();
    g.identity = q.identity;
    g.ready = true;
    g.loads = 0u;
    g.active = true;
    g.fault = false;
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
    assert(g.loads == 0u);
    g.active = false;
    g.identity.board_revision++;
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
    assert(g.loads == 0u);

    g.identity = q.identity;
    g.identity.deadtime_ticks++;
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
    g.identity = q.identity;
    g.identity.adc_config_signature++;
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
    g.identity = q.identity;
    g.identity.current_calibration_signature++;
    assert(!MapCommissioning_LoadMeasured(&map, &q.manifest, &ops));
}

static void test_incomplete_candidate_rejected(void)
{
    MapCandidateQualification q = qualification_make();
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewCurrentMap map;
    fill_tables(recon, regions);
    recon[5][1].valid = false;
    assert(MapCandidate_Build(&q, recon, regions, &map) == MAP_CANDIDATE_RECON_BAD);
}

static void test_recomputed_crc_does_not_bypass_structure_validation(void)
{
    MapCandidateQualification q = qualification_make();
    OewCurrentMap map = build_map(&q);

    map.recon[0][0].valid = false;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(!MapCandidate_IsCanonical(&map, &q.identity, &q.manifest));

    map = build_map(&q);
    map.region[0][0].valid = 0u;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(!MapCandidate_IsCanonical(&map, &q.identity, &q.manifest));

    map = build_map(&q);
    map.startup_mu = (int16_t)(map.region[0][0].mu_max + 1);
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    assert(!MapCandidate_IsCanonical(&map, &q.identity, &q.manifest));
}

int main(void)
{
    test_candidate_and_crc_rejection();
    test_active_control_and_identity_rejection();
    test_incomplete_candidate_rejected();
    test_recomputed_crc_does_not_bypass_structure_validation();
    puts("map_candidate_commissioning_test: PASS");
    return 0;
}
