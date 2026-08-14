#include "current_reconstruct.h"

#include <string.h>

#define CURRENT_RECON_MAX_COEFF  10000L

static CurrentReconEntry g_map[CURRENT_RECON_MAX_SECTORS][CURRENT_RECON_MAX_WINDOWS];
static uint8_t g_ready;

static bool entry_is_sane(const CurrentReconEntry *entry)
{
    int64_t determinant;

    if (!entry->valid) return true;
    if (entry->phase_a > 2u || entry->phase_b > 2u ||
        entry->phase_a == entry->phase_b) return false;
    if (entry->m00 < -CURRENT_RECON_MAX_COEFF || entry->m00 > CURRENT_RECON_MAX_COEFF ||
        entry->m01 < -CURRENT_RECON_MAX_COEFF || entry->m01 > CURRENT_RECON_MAX_COEFF ||
        entry->m10 < -CURRENT_RECON_MAX_COEFF || entry->m10 > CURRENT_RECON_MAX_COEFF ||
        entry->m11 < -CURRENT_RECON_MAX_COEFF || entry->m11 > CURRENT_RECON_MAX_COEFF) {
        return false;
    }

    determinant = (int64_t)entry->m00 * entry->m11 -
                  (int64_t)entry->m01 * entry->m10;
    return determinant != 0;
}

void CurrentRecon_Reset(void)
{
    memset(g_map, 0, sizeof(g_map));
    g_ready = 0u;
}

bool CurrentRecon_LoadMap(const CurrentReconEntry map[CURRENT_RECON_MAX_SECTORS]
                                                    [CURRENT_RECON_MAX_WINDOWS])
{
    uint8_t sector;
    uint8_t window;

    if (map == 0) return false;

    /* The caller must execute this only with PWM disabled and the ADC IRQ
     * disarmed. A partial map is explicitly rejected: the FOC scheduler must
     * never enter an unproven sector/window after control admission. */
    for (sector = 0u; sector < CURRENT_RECON_MAX_SECTORS; ++sector) {
        for (window = 0u; window < CURRENT_RECON_MAX_WINDOWS; ++window) {
            if (!map[sector][window].valid ||
                !entry_is_sane(&map[sector][window])) {
                return false;
            }
        }
    }

    memcpy(g_map, map, sizeof(g_map));
    g_ready = 1u;
    return true;
}

bool CurrentRecon_IsReady(void)
{
    return g_ready != 0u;
}

bool Current_Reconstruct(const AdcFrame *frame, PhaseCurrents *out)
{
    const CurrentReconEntry *entry;
    int32_t pair_a;
    int32_t pair_b;
    int32_t phase[3];

    if (frame == 0 || out == 0 || !g_ready || !ADC_FrameIsControlValid(frame)) {
        return false;
    }
    if (frame->tim1_sector >= CURRENT_RECON_MAX_SECTORS ||
        frame->sample_window >= CURRENT_RECON_MAX_WINDOWS) {
        return false;
    }

    entry = &g_map[frame->tim1_sector][frame->sample_window];
    if (!entry->valid || !entry_is_sane(entry)) return false;

    pair_a = (int32_t)(((int64_t)entry->m00 * frame->idc1_ma +
                        (int64_t)entry->m01 * frame->idc2_ma) /
                       CURRENT_RECON_COEFF_SCALE);
    pair_b = (int32_t)(((int64_t)entry->m10 * frame->idc1_ma +
                        (int64_t)entry->m11 * frame->idc2_ma) /
                       CURRENT_RECON_COEFF_SCALE);

    phase[0] = 0;
    phase[1] = 0;
    phase[2] = 0;
    phase[entry->phase_a] = pair_a;
    phase[entry->phase_b] = pair_b;
    phase[3u - entry->phase_a - entry->phase_b] = -pair_a - pair_b;

    /* CT is intentionally not used to derive a phase current. Its gain, phase
     * response and physical role require a separate diagnostic qualification. */
    out->iu_ma = phase[0];
    out->iv_ma = phase[1];
    out->iw_ma = phase[2];
    out->ict_ma = frame->ict_ma;
    out->sector = frame->tim1_sector;
    out->sample_window = frame->sample_window;
    return true;
}
