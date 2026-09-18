/*
 * CLI генератора вариантов карты (host/offline, TZ-02 experimental M-tuning).
 *
 *   map_variant_cli --emit-fixture <out.bin>
 *   map_variant_cli <base.bin> <out.bin> --variant NAME
 *                   (--scale NUM/DEN | --offset N | --sector S --window W --scale NUM/DEN)
 *                   [--clamp MIN MAX] [--expect-base <M0.bin>] [--manifest out.json]
 *
 * Коды выхода: 0 — OK, 1 — REJECT (печатается статус), 2 — ошибка использования/IO.
 * Артефакт грузится в стенд командой `mapload <994 hex>` (tools/map_upload.py),
 * firmware при этом НЕ пересобирается.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "map_artifact_writer.h"
#include "map_variant_writer.h"

#define VARIANT_MAX_COEFF 10000L

static int read_file(const char *path, uint8_t *buf, size_t cap, size_t *len)
{
    FILE *f = fopen(path, "rb");
    size_t n;

    if (f == 0) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    n = fread(buf, 1u, cap, f);
    if (ferror(f)) { fclose(f); fprintf(stderr, "read error %s\n", path); return 2; }
    fclose(f);
    *len = n;
    return 0;
}

static int write_file(const char *path, const uint8_t *buf, size_t len)
{
    FILE *f = fopen(path, "wb");

    if (f == 0) { fprintf(stderr, "cannot write %s\n", path); return 2; }
    if (fwrite(buf, 1u, len, f) != len) { fclose(f); fprintf(stderr, "write error %s\n", path); return 2; }
    fclose(f);
    return 0;
}

/* Детерминированная фикстура для тестов/демо. Это НЕ измеренная карта. */
static void build_fixture(OewCurrentMap *map)
{
    uint8_t s, w;

    memset(map, 0, sizeof(*map));
    map->magic = OEW_CURRENT_MAP_MAGIC;
    map->revision = OEW_CURRENT_MAP_REVISION;
    map->board_revision = 7u;
    map->pwm_frequency_hz = 20000u;
    map->timer_arr = 8499u;
    map->adc_trigger_id = 0x4F455731u;
    map->trigger_offset_ticks = 17u;
    map->deadtime_ticks = 85u;
    map->adc_clock_hz = 42500000u;
    map->adc_sample_cycles_x2 = 1281u;
    map->adc_resolution = 0u;
    map->adc_config_signature = 0x11223344u;
    map->current_calibration_signature = 0x55667788u;
    map->provenance.characterization_id = 0x01020304u;
    map->provenance.dataset_crc32 = 0xA1B2C3D4u;
    map->provenance.tool_build_id = 0x10203040u;
    map->provenance.qualification_revision = 2u;
    map->provenance.solver_revision = 4u;
    map->provenance.certifier_revision = 3u;
    map->startup_sector = 0u;
    map->startup_window = 0u;
    map->startup_hold_cycles = 100u;
    map->startup_mu = -30000;
    map->startup_mv = -20000;
    map->startup_mw = -10000;

    for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            CurrentReconEntry *e = &map->recon[s][w];
            OewPwmRegion *r = &map->region[s][w];

            e->valid = true;
            e->phase_a = 0u;
            e->phase_b = 1u;
            e->m00 = 1000;
            e->m01 = 100;
            e->m10 = -100;
            e->m11 = 1000;

            r->mu_min = (int16_t)(-30000 + s * 1000);
            r->mu_max = (int16_t)(-29500 + s * 1000);
            r->mv_min = (int16_t)(-20000 + w * 1000);
            r->mv_max = (int16_t)(-19500 + w * 1000);
            r->mw_min = -10000;
            r->mw_max = -9000;
            r->min_margin_ticks = 37u;
            r->valid = 1u;
        }
    }
    map->crc32 = 0u;
    map->crc32 = CurrentMap_CalculateCrc32(map);
}

int main(int argc, char **argv)
{
    uint8_t base_wire[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t out_wire[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t expect_wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap base, variant, expected;
    MapVariantTransform t;
    MapVariantStats stats;
    MapVariantStatus rc;
    size_t base_len = 0u, expect_len = 0u, written = 0u;
    const char *variant_name = "unnamed";
    const char *manifest_path = 0;
    const char *expect_path = 0;
    int i;
    int have_scale = 0, have_offset = 0, have_sector = 0, have_window = 0;
    int offset_value = 0, num = 1, den = 1;
    int sector = 0, window = 0;
    long clamp_min = -VARIANT_MAX_COEFF, clamp_max = VARIANT_MAX_COEFF;

    if (argc >= 3 && strcmp(argv[1], "--emit-fixture") == 0) {
        build_fixture(&base);
        rc = MapVariant_Encode(&base, out_wire, sizeof(out_wire), &written);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s\n", MapVariant_StatusName(rc)); return 1; }
        if (write_file(argv[2], out_wire, written) != 0) return 2;
        printf("OK fixture written: %s (%u bytes, crc=0x%08lX)\n", argv[2],
               (unsigned)written, (unsigned long)base.crc32);
        printf("NOTE: фикстура НЕ является измеренной картой — только для тестов/демо\n");
        return 0;
    }

    if (argc < 5) {
        fprintf(stderr,
            "usage: map_variant_cli --emit-fixture <out.bin>\n"
            "       map_variant_cli <base.bin> <out.bin> --variant NAME\n"
            "                       (--scale NUM/DEN | --offset N | --sector S --window W --scale NUM/DEN)\n"
            "                       [--clamp MIN MAX] [--expect-base <M0.bin>] [--manifest out.json]\n");
        return 2;
    }

    for (i = 3; i < argc; ++i) {
        if (strcmp(argv[i], "--variant") == 0 && i + 1 < argc) {
            variant_name = argv[++i];
        } else if (strcmp(argv[i], "--scale") == 0 && i + 1 < argc) {
            if (sscanf(argv[++i], "%d/%d", &num, &den) != 2) {
                fprintf(stderr, "bad --scale (expected NUM/DEN)\n"); return 2;
            }
            have_scale = 1;
        } else if (strcmp(argv[i], "--offset") == 0 && i + 1 < argc) {
            offset_value = atoi(argv[++i]);
            have_offset = 1;
        } else if (strcmp(argv[i], "--sector") == 0 && i + 1 < argc) {
            sector = atoi(argv[++i]);
            have_sector = 1;
        } else if (strcmp(argv[i], "--window") == 0 && i + 1 < argc) {
            window = atoi(argv[++i]);
            have_window = 1;
        } else if (strcmp(argv[i], "--clamp") == 0 && i + 2 < argc) {
            clamp_min = strtol(argv[++i], 0, 10);
            clamp_max = strtol(argv[++i], 0, 10);
        } else if (strcmp(argv[i], "--expect-base") == 0 && i + 1 < argc) {
            expect_path = argv[++i];
        } else if (strcmp(argv[i], "--manifest") == 0 && i + 1 < argc) {
            manifest_path = argv[++i];
        } else {
            fprintf(stderr, "unknown argument: %s\n", argv[i]); return 2;
        }
    }

    if (read_file(argv[1], base_wire, sizeof(base_wire), &base_len) != 0) return 2;
    rc = MapVariant_Decode(base_wire, base_len, &base);
    if (rc != MAP_VARIANT_OK) { printf("REJECT %s (base)\n", MapVariant_StatusName(rc)); return 1; }

    if (expect_path != 0) {
        if (read_file(expect_path, expect_wire, sizeof(expect_wire), &expect_len) != 0) return 2;
        rc = MapVariant_Decode(expect_wire, expect_len, &expected);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (expect-base)\n", MapVariant_StatusName(rc)); return 1; }
        if (!MapVariant_IdentityMatches(&expected, &base)) {
            printf("REJECT ERR_IDENTITY (identity differs from %s)\n", expect_path);
            return 1;
        }
    }

    memset(&t, 0, sizeof(t));
    t.min_value = (int32_t)clamp_min;
    t.max_value = (int32_t)clamp_max;
    if (have_offset) {
        t.kind = MAP_VARIANT_COMMON_OFFSET;
        t.offset = offset_value;
    } else if (have_sector || have_window) {
        t.kind = MAP_VARIANT_SECTOR_WINDOW;
        t.scale_num = num; t.scale_den = den;
        t.target_sector = (uint8_t)sector; t.target_window = (uint8_t)window;
    } else if (have_scale) {
        t.kind = MAP_VARIANT_GLOBAL_SCALE;
        t.scale_num = num; t.scale_den = den;
    } else {
        fprintf(stderr, "no transform given (--scale/--offset/--sector+--window)\n");
        return 2;
    }

    rc = MapVariant_Apply(&base, &t, &variant, &stats);
    if (rc != MAP_VARIANT_OK) { printf("REJECT %s\n", MapVariant_StatusName(rc)); return 1; }

    rc = MapVariant_Encode(&variant, out_wire, sizeof(out_wire), &written);
    if (rc != MAP_VARIANT_OK) { printf("REJECT %s (encode)\n", MapVariant_StatusName(rc)); return 1; }

    /* Выход обязан декодироваться каноническим декодером (CRC/магия/ревизия). */
    {
        OewCurrentMap check;
        rc = MapVariant_Decode(out_wire, written, &check);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (round-trip)\n", MapVariant_StatusName(rc)); return 1; }
        if (!MapVariant_IdentityMatches(&base, &check)) {
            printf("REJECT ERR_IDENTITY (round-trip)\n"); return 1;
        }
    }

    if (write_file(argv[2], out_wire, written) != 0) return 2;

    printf("OK %s -> %s (%u bytes)\n", variant_name, argv[2], (unsigned)written);
    printf("   entries_changed=%u clamped=%u coeffs %ld..%ld -> %ld..%ld\n",
           (unsigned)stats.entries_changed, (unsigned)stats.coefficients_clamped,
           (long)stats.coeff_min_before, (long)stats.coeff_max_before,
           (long)stats.coeff_min_after, (long)stats.coeff_max_after);
    printf("   crc 0x%08lX -> 0x%08lX | identity=%s provenance=%s regions=%s startup=%s\n",
           (unsigned long)stats.crc_before, (unsigned long)stats.crc_after,
           stats.identity_preserved ? "ok" : "FAIL",
           stats.provenance_preserved ? "ok" : "FAIL",
           stats.regions_preserved ? "ok" : "FAIL",
           stats.startup_preserved ? "ok" : "FAIL");

    if (manifest_path != 0) {
        char manifest[2048];
        size_t n = MapVariant_WriteManifestJson(&base, &variant, &t, &stats, variant_name,
                                                manifest, sizeof(manifest));
        if (n == 0u) { fprintf(stderr, "manifest buffer too small\n"); return 2; }
        if (write_file(manifest_path, (const uint8_t *)manifest, n) != 0) return 2;
        printf("   manifest: %s\n", manifest_path);
    }
    return 0;
}
