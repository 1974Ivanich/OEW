/* Roundtrip decode test and hosted admission tests for TZ_MAP_UPLOAD_AND_ADMISSION §2.4.
 *
 * Tests:
 *   1. Encode → Decode → semantic field equality + CRC
 *   2. Wire byte mutation → decode fails
 *   3. Wrong length → decode fails
 *   4. Zero magic → decode fails
 *   5. Admission: valid artifact → MapCommissioning_LoadMeasured succeeds
 *   6. Admission negative: wrong identity, provenance, region, startup,
 *      determinant, PWM, fault, FOC/V-f/autotune → each fails, active map unchanged
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_artifact_decoder.h"
#include "map_artifact_writer.h"
#include "map_candidate.h"
#include "map_commissioning.h"

/* ── Test helpers: build a valid map ─────────────────────────────────── */

static void identity_make(OewMapIdentity *id)
{
    memset(id, 0, sizeof(*id));
    id->board_revision = 7u;
    id->pwm_frequency_hz = 20000u;
    id->timer_arr = 8499u;
    id->adc_trigger_id = 0x4F455731u;
    id->trigger_offset_ticks = 0u;
    id->deadtime_ticks = 85u;
    id->adc_clock_hz = 42500000u;
    id->adc_sample_cycles_x2 = 1281u;
    id->adc_resolution = 0u;
    id->adc_config_signature = 0x11223344u;
    id->current_calibration_signature = 0x55667788u;
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
    m.record_count = MAP_CANDIDATE_REQUIRED_ROWS;
    m.crc32 = MapReferenceManifest_CalculateCrc32(&m);
    return m;
}

static void fill_tables(CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                                               [OEW_CURRENT_MAP_WINDOW_COUNT],
                        OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT]
                                            [OEW_CURRENT_MAP_WINDOW_COUNT])
{
    uint8_t s, w;
    memset(recon, 0, sizeof(CurrentReconEntry) * OEW_CURRENT_MAP_SECTOR_COUNT *
                     OEW_CURRENT_MAP_WINDOW_COUNT);
    memset(regions, 0, sizeof(OewPwmRegion) * OEW_CURRENT_MAP_SECTOR_COUNT *
                       OEW_CURRENT_MAP_WINDOW_COUNT);
    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            recon[s][w].valid = true;
            recon[s][w].phase_a = 0u;
            recon[s][w].phase_b = 1u;
            recon[s][w].m00 = (int32_t)(1000 + s * 100 + w * 50);
            recon[s][w].m01 = (int32_t)(200 + s * 10);
            recon[s][w].m10 = (int32_t)(-300 - w * 20);
            recon[s][w].m11 = (int32_t)(2000 + s * 50 + w * 25);
            /* Regions must be pairwise disjoint (no overlap check in
             * CurrentMap_LoadMeasured). Space them along mu. */
            regions[s][w].mu_min = (int16_t)(-30000 + (s * 2 + w) * 5000);
            regions[s][w].mu_max = (int16_t)(-26000 + (s * 2 + w) * 5000);
            regions[s][w].mv_min = -20000;
            regions[s][w].mv_max = -19000;
            regions[s][w].mw_min = -10000;
            regions[s][w].mw_max = -9000;
            regions[s][w].min_margin_ticks = 5u;
            regions[s][w].valid = 1u;
        }
    }
}

static OewCurrentMap build_valid_map(OewMapIdentity *identity,
                                     MapReferenceManifest *manifest)
{
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewCurrentMap map;
    MapCandidateQualification q;

    identity_make(identity);
    *manifest = manifest_make(identity);

    memset(&q, 0, sizeof(q));
    q.identity = *identity;
    q.provenance = provenance_make();
    q.manifest = *manifest;
    q.startup_sector = 0u;
    q.startup_window = 0u;
    q.startup_hold_cycles = 25u;
    /* Must be contained in region[0][0]: mu -30000..-26000, mv -20000..-19000, mw -10000..-9000 */
    q.startup_mu = -28000;
    q.startup_mv = -19500;
    q.startup_mw = -9500;

    fill_tables(recon, regions);
    assert(MapCandidate_Build(&q, recon, regions, &map) == MAP_CANDIDATE_OK);
    return map;
}

/* ── Semantic field comparison (NOT memcmp) ──────────────────────────── */

static void assert_maps_equal(const OewCurrentMap *a, const OewCurrentMap *b)
{
    uint8_t s, w;
    assert(a->magic == b->magic);
    assert(a->revision == b->revision);
    assert(a->board_revision == b->board_revision);
    assert(a->pwm_frequency_hz == b->pwm_frequency_hz);
    assert(a->timer_arr == b->timer_arr);
    assert(a->adc_trigger_id == b->adc_trigger_id);
    assert(a->trigger_offset_ticks == b->trigger_offset_ticks);
    assert(a->deadtime_ticks == b->deadtime_ticks);
    assert(a->adc_clock_hz == b->adc_clock_hz);
    assert(a->adc_sample_cycles_x2 == b->adc_sample_cycles_x2);
    assert(a->adc_resolution == b->adc_resolution);
    assert(a->adc_config_signature == b->adc_config_signature);
    assert(a->current_calibration_signature == b->current_calibration_signature);

    assert(a->provenance.characterization_id == b->provenance.characterization_id);
    assert(a->provenance.dataset_crc32 == b->provenance.dataset_crc32);
    assert(a->provenance.tool_build_id == b->provenance.tool_build_id);
    assert(a->provenance.qualification_revision == b->provenance.qualification_revision);
    assert(a->provenance.solver_revision == b->provenance.solver_revision);
    assert(a->provenance.certifier_revision == b->provenance.certifier_revision);

    assert(a->startup_sector == b->startup_sector);
    assert(a->startup_window == b->startup_window);
    assert(a->startup_hold_cycles == b->startup_hold_cycles);
    assert(a->startup_mu == b->startup_mu);
    assert(a->startup_mv == b->startup_mv);
    assert(a->startup_mw == b->startup_mw);

    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            const CurrentReconEntry *ra = &a->recon[s][w];
            const CurrentReconEntry *rb = &b->recon[s][w];
            assert(ra->valid == rb->valid);
            assert(ra->phase_a == rb->phase_a);
            assert(ra->phase_b == rb->phase_b);
            assert(ra->m00 == rb->m00);
            assert(ra->m01 == rb->m01);
            assert(ra->m10 == rb->m10);
            assert(ra->m11 == rb->m11);
        }
    }
    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            const OewPwmRegion *ra = &a->region[s][w];
            const OewPwmRegion *rb = &b->region[s][w];
            assert(ra->mu_min == rb->mu_min);
            assert(ra->mu_max == rb->mu_max);
            assert(ra->mv_min == rb->mv_min);
            assert(ra->mv_max == rb->mv_max);
            assert(ra->mw_min == rb->mw_min);
            assert(ra->mw_max == rb->mw_max);
            assert(ra->min_margin_ticks == rb->min_margin_ticks);
            assert(ra->valid == rb->valid);
            assert(ra->reserved == rb->reserved);
        }
    }
    assert(a->crc32 == b->crc32);
}

/* ── Commissioning test harness ─────────────────────────────────────── */

static struct {
    bool capture_active;
    bool foc_running;
    bool vfc_running;
    bool autotune_active;
    bool pwm_enabled;
    bool adc_armed;
    bool fault;
    bool ready;
    unsigned loads;
    OewMapIdentity identity;
} g;

static bool stub_capture(void)  { return g.capture_active; }
static bool stub_foc(void)      { return g.foc_running; }
static bool stub_vfc(void)      { return g.vfc_running; }
static bool stub_autotune(void) { return g.autotune_active; }
static bool stub_pwm(void)      { return g.pwm_enabled; }
static bool stub_adc(void)      { return g.adc_armed; }
static bool stub_fault(void)    { return g.fault; }
static bool stub_identity(OewMapIdentity *id)
{
    *id = g.identity;
    return true;
}
static bool stub_load(const OewCurrentMap *map, const OewMapIdentity *id)
{
    (void)id;
    /* Use the real loader so readiness is set properly. */
    return CurrentMap_LoadMeasured(map, id);
}
static bool stub_ready(void) { return CurrentMap_IsReady(); }

static MapCommissioningOps ops_make(void)
{
    MapCommissioningOps ops;
    memset(&ops, 0, sizeof(ops));
    ops.capture_active = stub_capture;
    ops.foc_running = stub_foc;
    ops.vfc_running = stub_vfc;
    ops.autotune_active = stub_autotune;
    ops.pwm_enabled = stub_pwm;
    ops.adc_injected_armed = stub_adc;
    ops.protect_fault = stub_fault;
    ops.get_identity = stub_identity;
    ops.load_measured = stub_load;
    ops.map_ready = stub_ready;
    return ops;
}

static void reset_stubs(void)
{
    memset(&g, 0, sizeof(g));
}

/* ── Tests ───────────────────────────────────────────────────────────── */

static void test_roundtrip(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap original = build_valid_map(&identity, &manifest);
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap decoded;

    assert(MapArtifactWriter_EncodeBinary(&original, wire, sizeof(wire)) ==
           OEW_CURRENT_MAP_WIRE_SIZE);
    assert(MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE, &decoded));
    assert_maps_equal(&original, &decoded);
    assert(CurrentMap_CalculateCrc32(&decoded) == decoded.crc32);
    puts("  roundtrip: PASS");
}

static void test_mutated_byte(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap original = build_valid_map(&identity, &manifest);
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap decoded;

    assert(MapArtifactWriter_EncodeBinary(&original, wire, sizeof(wire)) ==
           OEW_CURRENT_MAP_WIRE_SIZE);
    wire[100] ^= 0x01u;
    assert(!MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE, &decoded));
    puts("  mutated byte: PASS");
}

static void test_wrong_length(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap original = build_valid_map(&identity, &manifest);
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE + 1];
    OewCurrentMap decoded;

    assert(MapArtifactWriter_EncodeBinary(&original, wire, OEW_CURRENT_MAP_WIRE_SIZE) ==
           OEW_CURRENT_MAP_WIRE_SIZE);
    assert(!MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE - 1, &decoded));
    assert(!MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE + 1, &decoded));
    assert(!MapArtifact_DecodeBinary(wire, 0, &decoded));
    puts("  wrong length: PASS");
}

static void test_zero_magic(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap original = build_valid_map(&identity, &manifest);
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap decoded;

    assert(MapArtifactWriter_EncodeBinary(&original, wire, sizeof(wire)) ==
           OEW_CURRENT_MAP_WIRE_SIZE);
    wire[0] = 0u; wire[1] = 0u; wire[2] = 0u; wire[3] = 0u;
    assert(!MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE, &decoded));
    puts("  zero magic: PASS");
}

static void test_null_pointers(void)
{
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap decoded;
    assert(!MapArtifact_DecodeBinary(0, OEW_CURRENT_MAP_WIRE_SIZE, &decoded));
    assert(!MapArtifact_DecodeBinary(wire, OEW_CURRENT_MAP_WIRE_SIZE, 0));
    puts("  null pointers: PASS");
}

/* ── Admission tests ─────────────────────────────────────────────────── */

static void test_admission_happy_path(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;

    assert(MapCommissioning_LoadMeasured(&map, &manifest, &ops));
    assert(CurrentMap_IsReady());
    puts("  admission happy path: PASS");
}

/* Helper: run a negative admission test and verify readiness is unchanged. */
static void assert_admission_fails_cleanly(OewCurrentMap *map,
                                           MapReferenceManifest *manifest,
                                           MapCommissioningOps *ops,
                                           bool ready_before)
{
    assert(!MapCommissioning_LoadMeasured(map, manifest, ops));
    /* Readiness state must not change after failed admission. The real
     * CurrentMap_LoadMeasured resets state early, but commissioning ops should
     * reject BEFORE reaching it when the map is structurally invalid. */
    assert(CurrentMap_IsReady() == ready_before);
}

static void test_admission_wrong_identity(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.identity.board_revision = 99u;
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission wrong identity: PASS");
}

static void test_admission_wrong_provenance(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    map.provenance.characterization_id = 0u;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission wrong provenance: PASS");
}

static void test_admission_region_overlap(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    /* Invalidate a region */
    map.region[0][0].valid = 0u;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission region violation: PASS");
}

static void test_admission_startup_containment(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    /* Push startup mu outside region[0][0] bounds */
    map.startup_mu = (int16_t)(map.region[map.startup_sector][map.startup_window].mu_max + 100);
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission startup containment: PASS");
}

static void test_admission_determinant(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    /* Make recon[0][0] singular: m00*m11 - m01*m10 = 0 */
    map.recon[0][0].m00 = 1000;
    map.recon[0][0].m01 = 1000;
    map.recon[0][0].m10 = 1000;
    map.recon[0][0].m11 = 1000;
    map.crc32 = CurrentMap_CalculateCrc32(&map);
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission determinant violation: PASS");
}

static void test_admission_pwm_enabled(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.pwm_enabled = true;
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission PWM enabled: PASS");
}

static void test_admission_fault(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.fault = true;
    ready_before = CurrentMap_IsReady();

    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);
    puts("  admission fault active: PASS");
}

static void test_admission_foc_vf_autotune(void)
{
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    OewCurrentMap map = build_valid_map(&identity, &manifest);
    MapCommissioningOps ops = ops_make();
    bool ready_before;

    /* FOC running */
    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.foc_running = true;
    ready_before = CurrentMap_IsReady();
    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);

    /* V/f running */
    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.vfc_running = true;
    ready_before = CurrentMap_IsReady();
    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);

    /* Autotune active */
    CurrentMap_Reset();
    reset_stubs();
    g.identity = identity;
    g.autotune_active = true;
    ready_before = CurrentMap_IsReady();
    assert_admission_fails_cleanly(&map, &manifest, &ops, ready_before);

    puts("  admission FOC/V-f/autotune: PASS");
}

int main(void)
{
    puts("map_artifact_decode_test:");
    test_roundtrip();
    test_mutated_byte();
    test_wrong_length();
    test_zero_magic();
    test_null_pointers();
    test_admission_happy_path();
    test_admission_wrong_identity();
    test_admission_wrong_provenance();
    test_admission_region_overlap();
    test_admission_startup_containment();
    test_admission_determinant();
    test_admission_pwm_enabled();
    test_admission_fault();
    test_admission_foc_vf_autotune();
    puts("map_artifact_decode_test: PASS");
    return 0;
}
