#include "map_variant_writer.h"

#include <stdio.h>
#include <string.h>

#include "map_artifact_decoder.h"
#include "map_artifact_writer.h"

/* Должно совпадать с src/current_reconstruct.c:5 (CURRENT_RECON_MAX_COEFF). */
#define VARIANT_MAX_COEFF 10000L

const char *MapVariant_StatusName(MapVariantStatus status)
{
    switch (status) {
    case MAP_VARIANT_OK: return "OK";
    case MAP_VARIANT_ERR_ARGS: return "ERR_ARGS";
    case MAP_VARIANT_ERR_DECODE: return "ERR_DECODE";
    case MAP_VARIANT_ERR_TRANSFORM: return "ERR_TRANSFORM";
    case MAP_VARIANT_ERR_OVERFLOW: return "ERR_OVERFLOW";
    case MAP_VARIANT_ERR_ADMISSION: return "ERR_ADMISSION";
    case MAP_VARIANT_ERR_IDENTITY: return "ERR_IDENTITY";
    case MAP_VARIANT_ERR_ENCODE: return "ERR_ENCODE";
    default: return "ERR_UNKNOWN";
    }
}

MapVariantStatus MapVariant_Decode(const uint8_t *wire, size_t length,
                                   OewCurrentMap *out)
{
    if (wire == 0 || out == 0 || length != OEW_CURRENT_MAP_WIRE_SIZE) {
        return MAP_VARIANT_ERR_DECODE;
    }
    if (!MapArtifact_DecodeBinary(wire, length, out)) {
        return MAP_VARIANT_ERR_DECODE;
    }
    return MAP_VARIANT_OK;
}

/* Зеркало entry_is_sane() из src/current_reconstruct.c:11-30. */
static bool entry_is_sane(const CurrentReconEntry *entry)
{
    int64_t determinant;

    if (!entry->valid) return true;
    if (entry->phase_a > 2u || entry->phase_b > 2u ||
        entry->phase_a == entry->phase_b) return false;
    if (entry->m00 < -VARIANT_MAX_COEFF || entry->m00 > VARIANT_MAX_COEFF ||
        entry->m01 < -VARIANT_MAX_COEFF || entry->m01 > VARIANT_MAX_COEFF ||
        entry->m10 < -VARIANT_MAX_COEFF || entry->m10 > VARIANT_MAX_COEFF ||
        entry->m11 < -VARIANT_MAX_COEFF || entry->m11 > VARIANT_MAX_COEFF) {
        return false;
    }
    determinant = (int64_t)entry->m00 * (int64_t)entry->m11 -
                  (int64_t)entry->m01 * (int64_t)entry->m10;
    return determinant != 0;
}

/* Зеркало region_is_sane() + regions_overlap()-части map_regions_sane()
 * из src/current_map_selector.c:36-95. */
static bool regions_overlap(const OewPwmRegion *a, const OewPwmRegion *b)
{
    return !(a->mu_max < b->mu_min || b->mu_max < a->mu_min ||
             a->mv_max < b->mv_min || b->mv_max < a->mv_min ||
             a->mw_max < b->mw_min || b->mw_max < a->mw_min);
}

static bool region_is_sane(const OewPwmRegion *region)
{
    if (region == 0 || !region->valid || region->min_margin_ticks == 0u) return false;
    return region->mu_min <= region->mu_max &&
           region->mv_min <= region->mv_max &&
           region->mw_min <= region->mw_max;
}

static bool admission_ok(const OewCurrentMap *map, uint32_t *changed_hint)
{
    uint8_t s, w, os, ow;

    (void)changed_hint;
    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            if (!map->recon[s][w].valid || !entry_is_sane(&map->recon[s][w])) {
                fprintf(stderr, "reject: entry not sane (sector=%u window=%u valid=%u m=%d,%d,%d,%d)\n",
                        (unsigned)s, (unsigned)w, (unsigned)map->recon[s][w].valid,
                        (int)map->recon[s][w].m00, (int)map->recon[s][w].m01,
                        (int)map->recon[s][w].m10, (int)map->recon[s][w].m11);
                return false;
            }
            if (!region_is_sane(&map->region[s][w])) {
                fprintf(stderr, "reject: region not sane (sector=%u window=%u valid=%u margin=%u)\n",
                        (unsigned)s, (unsigned)w, (unsigned)map->region[s][w].valid,
                        (unsigned)map->region[s][w].min_margin_ticks);
                return false;
            }
            for (os = s; os < OEW_CURRENT_MAP_SECTOR_COUNT; ++os) {
                uint8_t start = (os == s) ? (uint8_t)(w + 1u) : 0u;
                for (ow = start; ow < OEW_CURRENT_MAP_WINDOW_COUNT; ++ow) {
                    if (regions_overlap(&map->region[s][w], &map->region[os][ow])) {
                        fprintf(stderr, "reject: regions overlap (%u,%u) vs (%u,%u)\n",
                                (unsigned)s, (unsigned)w, (unsigned)os, (unsigned)ow);
                        return false;
                    }
                }
            }
        }
    }
    return true;
}

bool MapVariant_IdentityMatches(const OewCurrentMap *base, const OewCurrentMap *variant)
{
    if (base == 0 || variant == 0) return false;
    return base->board_revision == variant->board_revision &&
           base->pwm_frequency_hz == variant->pwm_frequency_hz &&
           base->timer_arr == variant->timer_arr &&
           base->adc_trigger_id == variant->adc_trigger_id &&
           base->trigger_offset_ticks == variant->trigger_offset_ticks &&
           base->deadtime_ticks == variant->deadtime_ticks &&
           base->adc_clock_hz == variant->adc_clock_hz &&
           base->adc_sample_cycles_x2 == variant->adc_sample_cycles_x2 &&
           base->adc_resolution == variant->adc_resolution &&
           base->adc_config_signature == variant->adc_config_signature &&
           base->current_calibration_signature == variant->current_calibration_signature;
}

static bool provenance_matches(const OewCurrentMap *base, const OewCurrentMap *variant)
{
    return memcmp(&base->provenance, &variant->provenance,
                  sizeof(OewMapProvenance)) == 0;
}

static bool regions_equal(const OewCurrentMap *base, const OewCurrentMap *variant)
{
    return memcmp(base->region, variant->region, sizeof(base->region)) == 0;
}

static bool startup_equal(const OewCurrentMap *base, const OewCurrentMap *variant)
{
    return base->startup_sector == variant->startup_sector &&
           base->startup_window == variant->startup_window &&
           base->startup_hold_cycles == variant->startup_hold_cycles &&
           base->startup_mu == variant->startup_mu &&
           base->startup_mv == variant->startup_mv &&
           base->startup_mw == variant->startup_mw;
}

/* Округление: к нулю (C-семантика целочисленного деления), сатурация по [min; max].
 * Детерминировано и документировано: два хоста получают одни и те же байты. */
static MapVariantStatus scale_coeff(int32_t in, const MapVariantTransform *t,
                                    int32_t *out, bool *clamped, bool *changed)
{
    int64_t exact = (int64_t)in * (int64_t)t->scale_num;

    exact /= (int64_t)t->scale_den;   /* усечение к нулю */
    if (exact > 2147483647LL || exact < -2147483648LL) {
        return MAP_VARIANT_ERR_OVERFLOW;
    }
    *clamped = false;
    if (exact > (int64_t)t->max_value) { exact = t->max_value; *clamped = true; }
    if (exact < (int64_t)t->min_value) { exact = t->min_value; *clamped = true; }
    *out = (int32_t)exact;
    *changed = (*out != in);
    return MAP_VARIANT_OK;
}

static MapVariantStatus shift_coeff(int32_t in, const MapVariantTransform *t,
                                    int32_t *out, bool *clamped, bool *changed)
{
    int64_t exact = (int64_t)in + (int64_t)t->offset;

    if (exact > 2147483647LL || exact < -2147483648LL) {
        return MAP_VARIANT_ERR_OVERFLOW;
    }
    *clamped = false;
    if (exact > (int64_t)t->max_value) { exact = t->max_value; *clamped = true; }
    if (exact < (int64_t)t->min_value) { exact = t->min_value; *clamped = true; }
    *out = (int32_t)exact;
    *changed = (*out != in);
    return MAP_VARIANT_OK;
}

MapVariantStatus MapVariant_Apply(const OewCurrentMap *base,
                                  const MapVariantTransform *t,
                                  OewCurrentMap *out,
                                  MapVariantStats *stats)
{
    MapVariantStats local;
    uint8_t s, w;
    MapVariantStatus rc = MAP_VARIANT_OK;

    if (base == 0 || t == 0 || out == 0) return MAP_VARIANT_ERR_ARGS;
    if (base->magic != OEW_CURRENT_MAP_MAGIC ||
        base->revision != OEW_CURRENT_MAP_REVISION ||
        base->crc32 != CurrentMap_CalculateCrc32(base)) {
        return MAP_VARIANT_ERR_DECODE;
    }
    if (t->min_value > t->max_value ||
        t->min_value < -VARIANT_MAX_COEFF || t->max_value > VARIANT_MAX_COEFF) {
        return MAP_VARIANT_ERR_TRANSFORM;
    }
    if (t->kind == MAP_VARIANT_GLOBAL_SCALE || t->kind == MAP_VARIANT_SECTOR_WINDOW) {
        if (t->scale_den <= 0 || t->scale_num == 0) return MAP_VARIANT_ERR_TRANSFORM;
        if (t->kind == MAP_VARIANT_SECTOR_WINDOW &&
            (t->target_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
             t->target_window >= OEW_CURRENT_MAP_WINDOW_COUNT)) {
            return MAP_VARIANT_ERR_TRANSFORM;
        }
    } else if (t->kind == MAP_VARIANT_COMMON_OFFSET) {
        if (t->offset == 0) return MAP_VARIANT_ERR_TRANSFORM;
    } else {
        return MAP_VARIANT_ERR_TRANSFORM;
    }

    memset(&local, 0, sizeof(local));
    local.crc_before = base->crc32;
    local.coeff_min_before = 2147483647;
    local.coeff_max_before = -2147483648;
    *out = *base;

    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            CurrentReconEntry *e = &out->recon[s][w];
            const CurrentReconEntry *b = &base->recon[s][w];
            int32_t coeffs[4];
            int32_t result[4];
            bool changed_any = false;
            uint8_t i;

            coeffs[0] = b->m00; coeffs[1] = b->m01;
            coeffs[2] = b->m10; coeffs[3] = b->m11;
            result[0] = e->m00; result[1] = e->m01;
            result[2] = e->m10; result[3] = e->m11;

            for (i = 0u; i < 4u; ++i) {
                bool clamped = false;
                bool changed = false;
                const bool touched =
                    (t->kind == MAP_VARIANT_GLOBAL_SCALE) ||
                    (t->kind == MAP_VARIANT_COMMON_OFFSET) ||
                    (t->kind == MAP_VARIANT_SECTOR_WINDOW &&
                     s == t->target_sector && w == t->target_window);

                if (coeffs[i] < local.coeff_min_before) local.coeff_min_before = coeffs[i];
                if (coeffs[i] > local.coeff_max_before) local.coeff_max_before = coeffs[i];

                if (!touched) {
                    result[i] = coeffs[i];
                } else if (t->kind == MAP_VARIANT_COMMON_OFFSET) {
                    rc = shift_coeff(coeffs[i], t, &result[i], &clamped, &changed);
                    if (rc != MAP_VARIANT_OK) return rc;
                } else {
                    rc = scale_coeff(coeffs[i], t, &result[i], &clamped, &changed);
                    if (rc != MAP_VARIANT_OK) return rc;
                }
                if (clamped) ++local.coefficients_clamped;
                if (changed) changed_any = true;
            }

            e->m00 = result[0]; e->m01 = result[1];
            e->m10 = result[2]; e->m11 = result[3];
            ++local.entries_total;
            if (changed_any) ++local.entries_changed;
        }
    }

    if (local.entries_changed == 0u) return MAP_VARIANT_ERR_TRANSFORM;

    local.coeff_min_after = 2147483647;
    local.coeff_max_after = -2147483648;
    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            const CurrentReconEntry *e = &out->recon[s][w];
            const int32_t v[4] = { e->m00, e->m01, e->m10, e->m11 };
            uint8_t i;
            for (i = 0u; i < 4u; ++i) {
                if (v[i] < local.coeff_min_after) local.coeff_min_after = v[i];
                if (v[i] > local.coeff_max_after) local.coeff_max_after = v[i];
            }
        }
    }

    /* Admission-сторона: то же, что проверит прошивка при mapload. */
    if (!admission_ok(out, &local.entries_changed)) return MAP_VARIANT_ERR_ADMISSION;

    local.identity_preserved = MapVariant_IdentityMatches(base, out);
    local.provenance_preserved = provenance_matches(base, out);
    local.regions_preserved = regions_equal(base, out);
    local.startup_preserved = startup_equal(base, out);
    if (!local.identity_preserved || !local.provenance_preserved ||
        !local.regions_preserved || !local.startup_preserved) {
        return MAP_VARIANT_ERR_IDENTITY;
    }

    /* CRC — только после трансформации и до канонического кодирования. */
    out->crc32 = 0u;
    out->crc32 = CurrentMap_CalculateCrc32(out);
    local.crc_after = out->crc32;

    if (stats != 0) *stats = local;
    return MAP_VARIANT_OK;
}

MapVariantStatus MapVariant_Encode(const OewCurrentMap *map,
                                   uint8_t *dst, size_t capacity, size_t *written)
{
    size_t n;

    if (map == 0 || dst == 0) return MAP_VARIANT_ERR_ARGS;
    n = MapArtifactWriter_EncodeBinary(map, dst, capacity);
    if (n != OEW_CURRENT_MAP_WIRE_SIZE) return MAP_VARIANT_ERR_ENCODE;
    if (written != 0) *written = n;
    return MAP_VARIANT_OK;
}

size_t MapVariant_WriteManifestJson(const OewCurrentMap *base,
                                    const OewCurrentMap *variant,
                                    const MapVariantTransform *t,
                                    const MapVariantStats *st,
                                    const char *variant_name,
                                    char *dst, size_t capacity)
{
    char transform[96];
    int n;
    const char *kind = "unknown";

    if (base == 0 || variant == 0 || t == 0 || st == 0 || dst == 0 || capacity == 0u) {
        return 0u;
    }
    switch (t->kind) {
    case MAP_VARIANT_GLOBAL_SCALE: kind = "global_scale"; break;
    case MAP_VARIANT_COMMON_OFFSET: kind = "common_offset"; break;
    case MAP_VARIANT_SECTOR_WINDOW: kind = "sector_window_scale"; break;
    default: break;
    }
    if (t->kind == MAP_VARIANT_COMMON_OFFSET) {
        (void)snprintf(transform, sizeof(transform), "{\"kind\":\"%s\",\"offset\":%ld}",
                       kind, (long)t->offset);
    } else if (t->kind == MAP_VARIANT_SECTOR_WINDOW) {
        (void)snprintf(transform, sizeof(transform),
                       "{\"kind\":\"%s\",\"num\":%ld,\"den\":%ld,\"sector\":%u,\"window\":%u}",
                       kind, (long)t->scale_num, (long)t->scale_den,
                       (unsigned)t->target_sector, (unsigned)t->target_window);
    } else {
        (void)snprintf(transform, sizeof(transform), "{\"kind\":\"%s\",\"num\":%ld,\"den\":%ld}",
                       kind, (long)t->scale_num, (long)t->scale_den);
    }

    n = snprintf(dst, capacity,
        "{\n"
        "  \"variant\": \"%s\",\n"
        "  \"transform\": %s,\n"
        "  \"clamp\": {\"min\": %ld, \"max\": %ld},\n"
        "  \"identity_preserved\": %s,\n"
        "  \"provenance_preserved\": %s,\n"
        "  \"regions_preserved\": %s,\n"
        "  \"startup_preserved\": %s,\n"
        "  \"entries_total\": %u,\n"
        "  \"entries_changed\": %u,\n"
        "  \"coefficients_clamped\": %u,\n"
        "  \"coeff_min_before\": %ld, \"coeff_max_before\": %ld,\n"
        "  \"coeff_min_after\": %ld, \"coeff_max_after\": %ld,\n"
        "  \"crc_before\": \"0x%08lX\", \"crc_after\": \"0x%08lX\",\n"
        "  \"firmware_note\": \"артефакт грузится в рантайме командой mapload; firmware не пересобирается\"\n"
        "}\n",
        variant_name != 0 ? variant_name : "unnamed",
        transform,
        (long)t->min_value, (long)t->max_value,
        st->identity_preserved ? "true" : "false",
        st->provenance_preserved ? "true" : "false",
        st->regions_preserved ? "true" : "false",
        st->startup_preserved ? "true" : "false",
        (unsigned)st->entries_total, (unsigned)st->entries_changed,
        (unsigned)st->coefficients_clamped,
        (long)st->coeff_min_before, (long)st->coeff_max_before,
        (long)st->coeff_min_after, (long)st->coeff_max_after,
        (unsigned long)st->crc_before, (unsigned long)st->crc_after);
    if (n < 0 || (size_t)n >= capacity) return 0u;
    (void)base;
    (void)variant;
    return (size_t)n;
}
