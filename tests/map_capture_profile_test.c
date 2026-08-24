#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "map_capture_profiles.h"

#define SYNTHETIC_PROFILE_ID 0x53594E54u

static void test_build_request(void)
{
    MapCaptureRequest request;

    memset(&request, 0xA5, sizeof(request));
    assert(MapCaptureProfile_BuildRequest(SYNTHETIC_PROFILE_ID, 123u, &request));
    assert(request.capture_id == 123u);
    assert(MapCaptureProfile_IsApproved(&request));

    request.tim1_ccr[0]++;
    assert(!MapCaptureProfile_IsApproved(&request));

    assert(!MapCaptureProfile_BuildRequest(SYNTHETIC_PROFILE_ID + 1u, 123u, &request));
    assert(!MapCaptureProfile_BuildRequest(SYNTHETIC_PROFILE_ID, 0u, &request));
}

static void test_qualification(void)
{
    MapBuilderQualification qualification;
    uint8_t sector;
    uint8_t window;

    memset(&qualification, 0xA5, sizeof(qualification));
    assert(MapCaptureProfile_BuildQualification(SYNTHETIC_PROFILE_ID, &qualification));
    assert(qualification.identity.board_revision == 7u);
    assert(qualification.identity.pwm_frequency_hz == 5000u);
    assert(qualification.identity.timer_arr == 999u);
    assert(qualification.min_records_per_row == 1u);

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            assert(qualification.region[sector][window].valid == 1u);
            assert(qualification.region[sector][window].min_margin_ticks == 1u);
            assert(qualification.recon[sector][window].valid);
            assert(qualification.recon[sector][window].phase_a == 0u);
            assert(qualification.recon[sector][window].phase_b == 1u);
        }
    }

    assert(!MapCaptureProfile_BuildQualification(SYNTHETIC_PROFILE_ID + 1u,
                                                 &qualification));
    assert(!MapCaptureProfile_BuildQualification(SYNTHETIC_PROFILE_ID, NULL));
}

int main(void)
{
    test_build_request();
    test_qualification();
    return 0;
}
