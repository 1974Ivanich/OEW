#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "map_capture_profiles.h"

#define BOARD_PROFILE_ID 0x424F4152u /* "BOAR" */

static void test_build_request(void)
{
    MapCaptureRequest request;
    uint8_t sector;
    uint8_t window;

    /* All 12 approved variants build a request that passes exact-match. */
    for (uint32_t variant = 0u; variant < 12u; ++variant) {
        memset(&request, 0xA5, sizeof(request));
        assert(MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID + variant,
                                              1000u + variant, &request));
        assert(request.capture_id == 1000u + variant);
        assert(MapCaptureProfile_IsApproved(&request));

        sector = (uint8_t)(variant / 2u);
        window = (uint8_t)(variant & 1u);
        assert(request.sector_candidate == sector);
        assert(request.window_candidate == window);

        /* Strict match: any single field change must reject. */
        MapCaptureRequest mutated = request;
        mutated.tim1_ccr[0]++;
        assert(!MapCaptureProfile_IsApproved(&mutated));
        mutated = request;
        mutated.sector_candidate = (uint8_t)((sector + 1u) % 6u);
        assert(!MapCaptureProfile_IsApproved(&mutated));
        mutated = request;
        mutated.trigger_revision++;
        assert(!MapCaptureProfile_IsApproved(&mutated));
        mutated = request;
        mutated.pulse_count++;
        assert(!MapCaptureProfile_IsApproved(&mutated));
        mutated = request;
        mutated.max_abs_shunt_ma++;
        assert(!MapCaptureProfile_IsApproved(&mutated));
        mutated = request;
        mutated.tim8_ccr[2]--;
        assert(!MapCaptureProfile_IsApproved(&mutated));
    }

    /* Out-of-range ids and zero capture_id are rejected. */
    memset(&request, 0, sizeof(request));
    assert(!MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID - 1u, 1u, &request));
    assert(!MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID + 12u, 1u, &request));
    assert(!MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID, 0u, &request));
    assert(!MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID, 1u, NULL));
}

static void test_qualification(void)
{
    MapBuilderQualification qualification;
    uint8_t sector;
    uint8_t window;

    for (uint32_t variant = 0u; variant < 12u; ++variant) {
        memset(&qualification, 0xA5, sizeof(qualification));
        assert(MapCaptureProfile_BuildQualification(BOARD_PROFILE_ID + variant,
                                                    &qualification));
        assert(qualification.identity.board_revision == 7u);
        assert(qualification.identity.timer_arr == 999u);
        assert(qualification.identity.adc_trigger_id == 0x4F455731u);
        assert(qualification.identity.adc_resolution == 0u);
        assert(qualification.min_records_per_row == 3u);
        assert(qualification.min_margin_ticks == 110u);

        for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
            for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
                assert(qualification.region[sector][window].valid == 1u);
                assert(qualification.region[sector][window].min_margin_ticks == 110u);
                /* FAIL-CLOSED: recon must stay invalid until the offline
                 * solver/certifier qualification. */
                assert(!qualification.recon[sector][window].valid);
                assert(qualification.recon[sector][window].m00 == 0);
                assert(qualification.recon[sector][window].m11 == 0);
            }
        }
    }

    assert(!MapCaptureProfile_BuildQualification(BOARD_PROFILE_ID - 1u,
                                                 &qualification));
    assert(!MapCaptureProfile_BuildQualification(BOARD_PROFILE_ID + 12u,
                                                 &qualification));
    assert(!MapCaptureProfile_BuildQualification(BOARD_PROFILE_ID, NULL));
}

/* Modulation vectors must be strict phase orderings (like vfc_select_context)
 * so that a real sector is selectable and the aperture contract can accept
 * the resulting CCRs (mid=500, ARR=999 -> 375/500/625, in 135..999). */
static void test_modulation_orderings(void)
{
    MapCaptureRequest request;
    uint8_t sector;
    int16_t mu, mv, mw;

    for (uint32_t variant = 0u; variant < 12u; ++variant) {
        assert(MapCaptureProfile_BuildRequest(BOARD_PROFILE_ID + variant,
                                              1u, &request));
        sector = (uint8_t)(variant / 2u);
        mu = (int16_t)(((int32_t)request.tim1_ccr[0] - 500) * 32768 / 500);
        mv = (int16_t)(((int32_t)request.tim1_ccr[1] - 500) * 32768 / 500);
        mw = (int16_t)(((int32_t)request.tim1_ccr[2] - 500) * 32768 / 500);
        /* Reconstruct the sector decision exactly as vfc_select_context. */
        uint8_t decided;
        if (mu > mv) {
            decided = (uint8_t)((mv > mw) ? 0u : ((mu > mw) ? 1u : 4u));
        } else if (mv > mw) {
            decided = (uint8_t)((mu > mw) ? 2u : 3u);
        } else {
            decided = 5u;
        }
        assert(decided == sector);
        /* CCRs inside the qualified aperture 135..999 and not equal. */
        assert(request.tim1_ccr[0] >= 135u && request.tim1_ccr[0] <= 999u);
        assert(request.tim1_ccr[1] >= 135u && request.tim1_ccr[1] <= 999u);
        assert(request.tim1_ccr[2] >= 135u && request.tim1_ccr[2] <= 999u);
        assert(request.tim1_ccr[0] != request.tim1_ccr[1]);
        assert(request.tim1_ccr[1] != request.tim1_ccr[2]);
        assert(request.tim1_ccr[0] != request.tim1_ccr[2]);
    }
}

int main(void)
{
    test_build_request();
    test_qualification();
    test_modulation_orderings();
    return 0;
}
