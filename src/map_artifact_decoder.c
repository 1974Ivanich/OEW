#include "map_artifact_decoder.h"

#include <string.h>

static uint16_t get_u16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }
static int16_t get_i16(const uint8_t *p) { return (int16_t)get_u16(p); }
static uint32_t get_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static int32_t get_i32(const uint8_t *p) { return (int32_t)get_u32(p); }

bool MapArtifact_DecodeBinary(const uint8_t *src, size_t length, OewCurrentMap *out)
{
    size_t off = 0u;
    uint8_t sector;
    uint8_t window;
    uint32_t wire_crc;
    uint32_t computed_crc;
    if (src == 0 || out == 0 || length != OEW_CURRENT_MAP_WIRE_SIZE) return false;
    memset(out, 0, sizeof(*out));
#define U8()  (src[off++])
#define U16() (off += 2u, get_u16(src + off - 2u))
#define I16() (off += 2u, get_i16(src + off - 2u))
#define U32() (off += 4u, get_u32(src + off - 4u))
#define I32() (off += 4u, get_i32(src + off - 4u))
    out->magic = U32(); out->revision = U16(); out->board_revision = U16();
    out->pwm_frequency_hz = U32(); out->timer_arr = U32(); out->adc_trigger_id = U32();
    out->trigger_offset_ticks = U16(); out->deadtime_ticks = U16(); out->adc_clock_hz = U32();
    out->adc_sample_cycles_x2 = U16(); out->adc_resolution = U8();
    out->adc_config_signature = U32(); out->current_calibration_signature = U32();
    if (out->magic != OEW_CURRENT_MAP_MAGIC || out->revision != OEW_CURRENT_MAP_REVISION) return false;
    out->provenance.characterization_id = U32(); out->provenance.dataset_crc32 = U32();
    out->provenance.tool_build_id = U32(); out->provenance.qualification_revision = U32();
    out->provenance.solver_revision = U32(); out->provenance.certifier_revision = U32();
    out->startup_sector = U8(); out->startup_window = U8(); out->startup_hold_cycles = U16();
    out->startup_mu = I16(); out->startup_mv = I16(); out->startup_mw = I16();
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            CurrentReconEntry *r = &out->recon[sector][window];
            r->valid = U8() != 0u; r->phase_a = U8(); r->phase_b = U8();
            r->m00 = I32(); r->m01 = I32(); r->m10 = I32(); r->m11 = I32();
        }
    }
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            OewPwmRegion *r = &out->region[sector][window];
            r->mu_min = I16(); r->mu_max = I16(); r->mv_min = I16(); r->mv_max = I16();
            r->mw_min = I16(); r->mw_max = I16(); r->min_margin_ticks = U16();
            r->geometry_mod_min_q15 = I16(); r->geometry_mod_max_q15 = I16();
            r->valid = U8() != 0u; r->reserved = U8();
        }
    }
    wire_crc = U32();
#undef I32
#undef U32
#undef I16
#undef U16
#undef U8
    computed_crc = CurrentMap_CalculateCrc32(out);
    if (computed_crc != wire_crc) return false;
    out->crc32 = wire_crc;
    return true;
}
