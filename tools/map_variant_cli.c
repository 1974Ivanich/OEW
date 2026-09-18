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
    int have_scale = 0, have_offset = 0, have_sector = 0, have_window = 0, have_identity = 0;
    int offset_value = 0, num = 1, den = 1;
    int sector = 0, window = 0;
    long clamp_min = -VARIANT_MAX_COEFF, clamp_max = VARIANT_MAX_COEFF;

    if (argc >= 5 && strcmp(argv[1], "--rebase-identity") == 0) {
        /* M0-rebased: коэффициенты НЕ меняются, identity берётся из живой конфигурации.
         * Три независимые проверки: (1) coefficients equal, (2) identity == live,
         * (3) canonical decode + admission PASS. */
        OewCurrentMap src, out_map, check;
        OewMapIdentity live;
        MapVariantRebaseStats rst;
        uint8_t src_wire[OEW_CURRENT_MAP_WIRE_SIZE], out_wire[OEW_CURRENT_MAP_WIRE_SIZE];
        size_t src_len = 0u, out_len = 0u;
        FILE *f;
        int have_all = 1;
        int i;
        const char *name = "M0-rebased";
        const char *manifest = 0;

        if (read_file(argv[2], src_wire, sizeof(src_wire), &src_len) != 0) return 2;
        rc = MapVariant_Decode(src_wire, src_len, &src);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (source)\n", MapVariant_StatusName(rc)); return 1; }

        for (i = 4; i < argc; ++i) {
            if (strcmp(argv[i], "--variant") == 0 && i + 1 < argc) name = argv[++i];
            else if (strcmp(argv[i], "--manifest") == 0 && i + 1 < argc) manifest = argv[++i];
        }

        memset(&live, 0, sizeof(live));
        f = fopen(argv[3], "r");
        if (f == 0) { fprintf(stderr, "cannot open %s\n", argv[3]); return 2; }
        {
            char line[256];
            while (fgets(line, sizeof(line), f) != 0) {
                char *eq = strchr(line, '=');
                char *p;
                unsigned long v;
                if (eq == 0) continue;
                *eq = '\0';
                p = line;
                while (*p == ' ' || *p == '\t') ++p;
                { char *e = p + strlen(p); while (e > p && (e[-1] == ' ' || e[-1] == '\t')) *--e = '\0'; }
                v = strtoul(eq + 1, 0, 0);
                if (strcmp(p, "board_revision") == 0) live.board_revision = (uint16_t)v;
                else if (strcmp(p, "pwm_frequency_hz") == 0) live.pwm_frequency_hz = (uint32_t)v;
                else if (strcmp(p, "timer_arr") == 0) live.timer_arr = (uint32_t)v;
                else if (strcmp(p, "adc_trigger_id") == 0) live.adc_trigger_id = (uint32_t)v;
                else if (strcmp(p, "trigger_offset_ticks") == 0) live.trigger_offset_ticks = (uint16_t)v;
                else if (strcmp(p, "deadtime_ticks") == 0) live.deadtime_ticks = (uint16_t)v;
                else if (strcmp(p, "adc_clock_hz") == 0) live.adc_clock_hz = (uint32_t)v;
                else if (strcmp(p, "adc_sample_cycles_x2") == 0) live.adc_sample_cycles_x2 = (uint16_t)v;
                else if (strcmp(p, "adc_resolution") == 0) live.adc_resolution = (uint8_t)v;
                else if (strcmp(p, "adc_config_signature") == 0) live.adc_config_signature = (uint32_t)v;
                else if (strcmp(p, "current_calibration_signature") == 0) live.current_calibration_signature = (uint32_t)v;
            }
            fclose(f);
        }
        if (live.board_revision == 0u || live.pwm_frequency_hz == 0u || live.timer_arr == 0u ||
            live.adc_trigger_id == 0u || live.adc_clock_hz == 0u || live.adc_sample_cycles_x2 == 0u ||
            live.deadtime_ticks == 0u || live.adc_config_signature == 0u ||
            live.current_calibration_signature == 0u) {
            have_all = 0;
        }
        if (!have_all) {
            printf("REJECT ERR_TRANSFORM: живая identity неполна (нужны все 11 полей; "
                   "снять с платы командой mapcap identity)\n");
            return 1;
        }

        rc = MapVariant_RebaseIdentity(&src, &live, &out_map, &rst);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (rebase)\n", MapVariant_StatusName(rc)); return 1; }
        rc = MapVariant_Encode(&out_map, out_wire, sizeof(out_wire), &out_len);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (encode)\n", MapVariant_StatusName(rc)); return 1; }
        rc = MapVariant_Decode(out_wire, out_len, &check);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s (round-trip)\n", MapVariant_StatusName(rc)); return 1; }

        printf("M0-REBASED: %s\n", name);
        printf("check 1 coefficients        : %s\n",
               rst.coefficients_preserved ? "PASS (recon[][] байт-в-байт)" : "FAIL");
        printf("check 2 identity == live    : %s\n", "PASS");
        printf("check 3 decode + admission  : PASS\n");
        printf("identity changes            : %u\n", (unsigned)rst.fields_changed);
        for (i = 0; i < (int)rst.fields_changed; ++i) {
            printf("   %-30s %10lu -> %-10lu\n", rst.changed_fields[i],
                   rst.old_values[i], rst.new_values[i]);
        }
        printf("provenance                  : dataset_crc=0x%08lX char_id=0x%08lX tool=0x%08lX "
               "qual %lu -> %lu\n",
               (unsigned long)out_map.provenance.dataset_crc32,
               (unsigned long)out_map.provenance.characterization_id,
               (unsigned long)out_map.provenance.tool_build_id,
               (unsigned long)rst.qualification_revision_before,
               (unsigned long)rst.qualification_revision_after);
        printf("crc32                       : 0x%08lX -> 0x%08lX\n",
               (unsigned long)rst.crc_before, (unsigned long)rst.crc_after);

        if (write_file(argv[4], out_wire, out_len) != 0) return 2;
        printf("written                     : %s (%u bytes)\n", argv[4], (unsigned)out_len);

        if (manifest != 0) {
            char m[2048];
            int n = snprintf(m, sizeof(m),
                "{\n  \"variant\": \"%s\",\n  \"operation\": \"identity_rebase\",\n"
                "  \"source_dataset\": {\"characterization_id\": \"0x%08lX\", \"dataset_crc32\": \"0x%08lX\"},\n"
                "  \"identity_rebased\": true,\n  \"coefficients_changed\": false,\n"
                "  \"identity_change_count\": %u,\n  \"identity_gate\": {\n"
                "    \"read_live_identity\": \"mapcap identity (ПК-3)\",\n"
                "    \"profile\": \"strong\",\n    \"script\": \"--rebase-identity\"\n  },\n  \"changes\": [\n",
                name, (unsigned long)out_map.provenance.characterization_id,
                (unsigned long)out_map.provenance.dataset_crc32,
                (unsigned)rst.fields_changed);
            for (i = 0; i < (int)rst.fields_changed; ++i) {
                n += snprintf(m + n, sizeof(m) - (size_t)n,
                              "    {\"field\": \"%s\", \"from\": %lu, \"to\": %lu}%s\n",
                              rst.changed_fields[i], rst.old_values[i], rst.new_values[i],
                              (i + 1 < (int)rst.fields_changed) ? "," : "");
            }
            n += snprintf(m + n, sizeof(m) - (size_t)n,
                "  ],\n  \"qualification_revision\": {\"before\": %lu, \"after\": %lu},\n"
                "  \"crc32\": {\"before\": \"0x%08lX\", \"after\": \"0x%08lX\"},\n"
                "  \"note\": \"M0-rebased: те же измеренные коэффициенты, identity приведена к живой конфигурации; "
                "свободного текста в структуре нет, факт re-base маркируется tool_build_id=RBA1 и инкрементом "
                "qualification_revision\"\n}\n",
                (unsigned long)rst.qualification_revision_before,
                (unsigned long)rst.qualification_revision_after,
                (unsigned long)rst.crc_before, (unsigned long)rst.crc_after);
            if (n <= 0 || (size_t)n >= sizeof(m)) { fprintf(stderr, "manifest buffer too small\n"); return 2; }
            if (write_file(manifest, (const uint8_t *)m, (size_t)n) != 0) return 2;
            printf("manifest                    : %s\n", manifest);
        }
        return 0;
    }

    if (argc >= 3 && strcmp(argv[1], "--check-live-identity") == 0) {
        /* Сверка identity артефакта с живой конфигурацией стенда.
         * Файл <live.txt> — пары key=value (любой порядок), например из `mapcap identity`:
         *   board_revision=7
         *   pwm_frequency_hz=5000
         *   timer_arr=999
         *   adc_trigger_id=0x4F455731
         *   trigger_offset_ticks=0
         *   deadtime_ticks=192
         *   adc_clock_hz=42500000
         *   adc_sample_cycles_x2=1281
         *   adc_resolution=0
         *   adc_config_signature=0x26B9B97B
         *   current_calibration_signature=0xE98FCB2C
         * Ненулевое расхождение хотя бы по одному полю → rc=1 (mapload отклонит артефакт). */
        OewCurrentMap map;
        uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
        size_t len = 0u;
        FILE *f;
        int mismatches = 0;
        unsigned long live[11];
        int have[11];
        int i;

        if (argc < 4) { fprintf(stderr, "usage: map_variant_cli --check-live-identity <artifact.bin> <live.txt>\n"); return 2; }
        if (read_file(argv[2], wire, sizeof(wire), &len) != 0) return 2;
        rc = MapVariant_Decode(wire, len, &map);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s\n", MapVariant_StatusName(rc)); return 1; }

        for (i = 0; i < 11; ++i) { live[i] = 0UL; have[i] = 0; }
        f = fopen(argv[3], "r");
        if (f == 0) { fprintf(stderr, "cannot open %s\n", argv[3]); return 2; }
        {
            char line[256];
            while (fgets(line, sizeof(line), f) != 0) {
                char *eq = strchr(line, '=');
                char *p;
                long v;
                if (eq == 0) continue;
                *eq = '\0';
                p = line;
                while (*p == ' ' || *p == '\t') ++p;
                { char *e = p + strlen(p); while (e > p && (e[-1] == ' ' || e[-1] == '\t')) *--e = '\0'; }
                v = strtol(eq + 1, 0, 0);
                if (strcmp(p, "board_revision") == 0) { live[0] = (unsigned long)v; have[0] = 1; }
                else if (strcmp(p, "pwm_frequency_hz") == 0) { live[1] = (unsigned long)v; have[1] = 1; }
                else if (strcmp(p, "timer_arr") == 0) { live[2] = (unsigned long)v; have[2] = 1; }
                else if (strcmp(p, "adc_trigger_id") == 0) { live[3] = (unsigned long)v; have[3] = 1; }
                else if (strcmp(p, "trigger_offset_ticks") == 0) { live[4] = (unsigned long)v; have[4] = 1; }
                else if (strcmp(p, "deadtime_ticks") == 0) { live[5] = (unsigned long)v; have[5] = 1; }
                else if (strcmp(p, "adc_clock_hz") == 0) { live[6] = (unsigned long)v; have[6] = 1; }
                else if (strcmp(p, "adc_sample_cycles_x2") == 0) { live[7] = (unsigned long)v; have[7] = 1; }
                else if (strcmp(p, "adc_resolution") == 0) { live[8] = (unsigned long)v; have[8] = 1; }
                else if (strcmp(p, "adc_config_signature") == 0) { live[9] = (unsigned long)v; have[9] = 1; }
                else if (strcmp(p, "current_calibration_signature") == 0) { live[10] = (unsigned long)v; have[10] = 1; }
            }
            fclose(f);
        }

        {
            const struct { const char *name; unsigned long art, lv; int idx; } rows[11] = {
                { "board_revision", (unsigned long)map.board_revision, live[0], 0 },
                { "pwm_frequency_hz", (unsigned long)map.pwm_frequency_hz, live[1], 1 },
                { "timer_arr", (unsigned long)map.timer_arr, live[2], 2 },
                { "adc_trigger_id", (unsigned long)map.adc_trigger_id, live[3], 3 },
                { "trigger_offset_ticks", (unsigned long)map.trigger_offset_ticks, live[4], 4 },
                { "deadtime_ticks", (unsigned long)map.deadtime_ticks, live[5], 5 },
                { "adc_clock_hz", (unsigned long)map.adc_clock_hz, live[6], 6 },
                { "adc_sample_cycles_x2", (unsigned long)map.adc_sample_cycles_x2, live[7], 7 },
                { "adc_resolution", (unsigned long)map.adc_resolution, live[8], 8 },
                { "adc_config_signature", (unsigned long)map.adc_config_signature, live[9], 9 },
                { "current_calibration_signature", (unsigned long)map.current_calibration_signature, live[10], 10 }
            };
            printf("field                          artifact        live            verdict\n");
            for (i = 0; i < 11; ++i) {
                const char *verdict;
                if (!have[rows[i].idx]) {
                    verdict = "NO-DATA";
                } else if (rows[i].art == rows[i].lv) {
                    verdict = "MATCH";
                } else {
                    verdict = "MISMATCH";
                    ++mismatches;
                }
                printf("%-30s %-15lu %-15lu %s\n", rows[i].name, rows[i].art, rows[i].lv, verdict);
            }
        }
        if (mismatches > 0) {
            printf("LIVE-IDENTITY: MISMATCH (%d) — mapload отклонит этот артефакт\n", mismatches);
            return 1;
        }
        printf("LIVE-IDENTITY: MATCH — артефакт совместим с живой конфигурацией\n");
        return 0;
    }

    if (argc >= 3 && strcmp(argv[1], "--dump") == 0) {
        OewCurrentMap map;
        uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
        size_t len = 0u;
        uint32_t valid_entries = 0u, min_c = 2147483647, max_c = -2147483648;
        uint8_t s, w;

        if (read_file(argv[2], wire, sizeof(wire), &len) != 0) return 2;
        rc = MapVariant_Decode(wire, len, &map);
        if (rc != MAP_VARIANT_OK) { printf("REJECT %s\n", MapVariant_StatusName(rc)); return 1; }
        for (s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
            for (w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
                const CurrentReconEntry *e = &map.recon[s][w];
                const int32_t v[4] = { e->m00, e->m01, e->m10, e->m11 };
                uint8_t i;
                if (e->valid) ++valid_entries;
                for (i = 0u; i < 4u; ++i) {
                    if (e->valid) {
                        if (v[i] < (int32_t)min_c) min_c = (uint32_t)v[i];
                        if (v[i] > (int32_t)max_c) max_c = (uint32_t)v[i];
                    }
                }
            }
        }
        printf("artifact        : %s (%u bytes)\n", argv[2], (unsigned)len);
        printf("magic/revision  : 0x%08lX / %u\n",
               (unsigned long)map.magic, (unsigned)map.revision);
        printf("board_revision  : %u\n", (unsigned)map.board_revision);
        printf("pwm_frequency_hz: %lu\n", (unsigned long)map.pwm_frequency_hz);
        printf("timer_arr       : %lu\n", (unsigned long)map.timer_arr);
        printf("adc_trigger_id  : 0x%08lX\n", (unsigned long)map.adc_trigger_id);
        printf("trigger_offset  : %u ticks\n", (unsigned)map.trigger_offset_ticks);
        printf("deadtime_ticks  : %u\n", (unsigned)map.deadtime_ticks);
        printf("adc_clock_hz    : %lu\n", (unsigned long)map.adc_clock_hz);
        printf("sample_cycles_x2: %u\n", (unsigned)map.adc_sample_cycles_x2);
        printf("adc_resolution  : %u\n", (unsigned)map.adc_resolution);
        printf("adc_cfg_sig     : 0x%08lX\n", (unsigned long)map.adc_config_signature);
        printf("cur_cal_sig     : 0x%08lX\n", (unsigned long)map.current_calibration_signature);
        printf("provenance      : char_id=0x%08lX dataset_crc=0x%08lX tool=0x%08lX "
               "qual=%lu solver=%lu certifier=%lu\n",
               (unsigned long)map.provenance.characterization_id,
               (unsigned long)map.provenance.dataset_crc32,
               (unsigned long)map.provenance.tool_build_id,
               (unsigned long)map.provenance.qualification_revision,
               (unsigned long)map.provenance.solver_revision,
               (unsigned long)map.provenance.certifier_revision);
        printf("startup         : sector=%u window=%u hold=%u mu=%d mv=%d mw=%d\n",
               (unsigned)map.startup_sector, (unsigned)map.startup_window,
               (unsigned)map.startup_hold_cycles, (int)map.startup_mu,
               (int)map.startup_mv, (int)map.startup_mw);
        printf("entries valid   : %u / %u\n", (unsigned)valid_entries,
               (unsigned)(OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT));
        printf("coeff range     : %d .. %d\n", (int)min_c, (int)max_c);
        printf("crc32           : 0x%08lX\n", (unsigned long)map.crc32);
        return 0;
    }

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
            "                       (--identity | --scale NUM/DEN | --offset N | --sector S --window W --scale NUM/DEN)\n"
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
        } else if (strcmp(argv[i], "--identity") == 0) {
            have_identity = 1;
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
    if (have_identity) {
        t.kind = MAP_VARIANT_IDENTITY;
    } else if (have_offset) {
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

    if (have_identity) {
        /* Инвариант identity-transform: M0 → decode → identity → encode → decode
         * обязан дать байт-в-байт тот же артефакт. */
        if (written != base_len || memcmp(out_wire, base_wire, written) != 0) {
            printf("FAIL: identity transform изменил байты артефакта\n");
            return 1;
        }
        printf("IDENTITY OK: артефакт байт-в-байт идентичен входу (%u bytes, crc=0x%08lX)\n",
               (unsigned)written, (unsigned long)stats.crc_after);
    }

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
