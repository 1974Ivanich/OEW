#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "current_reconstruct.h"

int main(void)
{
    CurrentReconEntry map[CURRENT_RECON_MAX_SECTORS][CURRENT_RECON_MAX_WINDOWS];
    AdcFrame frame;
    PhaseCurrents currents;

    memset(map, 0, sizeof(map));
    for (uint8_t sector = 0u; sector < CURRENT_RECON_MAX_SECTORS; ++sector) {
        for (uint8_t window = 0u; window < CURRENT_RECON_MAX_WINDOWS; ++window) {
            map[sector][window].valid = true;
            map[sector][window].phase_a = 0u; /* U */
            map[sector][window].phase_b = 1u; /* V */
            map[sector][window].m00 = CURRENT_RECON_COEFF_SCALE;
            map[sector][window].m01 = 0;
            map[sector][window].m10 = 0;
            map[sector][window].m11 = CURRENT_RECON_COEFF_SCALE;
        }
    }

    CurrentRecon_Reset();
    assert(!CurrentRecon_IsReady());
    assert(CurrentRecon_LoadMap(map));
    assert(CurrentRecon_IsReady());

    memset(&frame, 0, sizeof(frame));
    frame.idc1_ma = 1000;
    frame.idc2_ma = -2000;
    frame.ict_ma = 777;
    frame.sequence = 42u;
    frame.tim1_sector = 4u;
    frame.sample_window = 1u;
    frame.status = ADC_FRAME_VALID;

    assert(Current_Reconstruct(&frame, &currents));
    assert(currents.iu_ma == 1000);
    assert(currents.iv_ma == -2000);
    assert(currents.iw_ma == 1000);
    assert(currents.iu_ma + currents.iv_ma + currents.iw_ma == 0);
    assert(currents.ict_ma == 777);

    frame.status = ADC_FRAME_WINDOW_INVALID;
    assert(!Current_Reconstruct(&frame, &currents));

    map[0][0].phase_b = 0u; /* invalid: duplicate phase */
    CurrentRecon_Reset();
    assert(!CurrentRecon_LoadMap(map));
    assert(!CurrentRecon_IsReady());

    puts("current_reconstruct_test: PASS");
    return 0;
}
