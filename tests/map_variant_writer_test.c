/* Hosted-тесты генератора вариантов карты (TZ-02 experimental M-tuning).
 *
 * Acceptance-набор (по требованиям приёмки):
 *   M0 round-trip byte-identical · identity preservation · canonical encoding ·
 *   CRC compatibility · negative admission tests · M1/M2 детерминизм.
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "map_artifact_decoder.h"
#include "map_artifact_writer.h"
#include "map_variant_writer.h"

static int checks;

#define CHECK(cond) do { assert(cond); ++checks; } while (0)

static void fixture_common(OewCurrentMap *map)
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

            /* Непересекающиеся боксы: sector задаёт полосу mu, window — полосу mv.
             * Достаточно непересечения по одной оси, но полосы разведены и здесь. */
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
}

static void build_fixture(OewCurrentMap *map)
{
    fixture_common(map);
    map->crc32 = 0u;
    map->crc32 = CurrentMap_CalculateCrc32(map);
}

/* Фикстура с другой identity — для проверок identity-preservation. */
static void build_fixture_identity(OewCurrentMap *map, uint32_t timer_arr,
                                   uint16_t board_revision)
{
    fixture_common(map);
    map->timer_arr = timer_arr;
    map->board_revision = board_revision;
    map->crc32 = 0u;
    map->crc32 = CurrentMap_CalculateCrc32(map);
}

static void scale_transform(MapVariantTransform *t, int32_t num, int32_t den)
{
    memset(t, 0, sizeof(*t));
    t->kind = MAP_VARIANT_GLOBAL_SCALE;
    t->scale_num = num;
    t->scale_den = den;
    t->min_value = -10000;
    t->max_value = 10000;
}

int main(void)
{
    OewCurrentMap base, variant, variant2, other, rt;
    uint8_t wire0[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t wire1[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t wire2[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t tmp[OEW_CURRENT_MAP_WIRE_SIZE];
    MapVariantTransform t;
    MapVariantStats st, st2;
    MapVariantStatus rc;
    size_t n, m;

    build_fixture(&base);
    CHECK(MapVariant_Encode(&base, wire0, sizeof(wire0), &n) == MAP_VARIANT_OK);
    CHECK(n == OEW_CURRENT_MAP_WIRE_SIZE);

    /* 1. M0 round-trip: decode → encode → байт-в-байт идентично */
    CHECK(MapVariant_Decode(wire0, n, &rt) == MAP_VARIANT_OK);
    CHECK(MapVariant_Encode(&rt, tmp, sizeof(tmp), &m) == MAP_VARIANT_OK);
    CHECK(m == n);
    CHECK(memcmp(tmp, wire0, n) == 0);
    CHECK(rt.crc32 == base.crc32);

    /* 1b. Инвариант identity transform: decode → identity → encode байт-в-байт */
    memset(&t, 0, sizeof(t));
    t.kind = MAP_VARIANT_IDENTITY;
    t.min_value = -10000;
    t.max_value = 10000;
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_OK);
    CHECK(st.entries_changed == 0u);
    CHECK(st.coefficients_clamped == 0u);
    CHECK(variant.crc32 == base.crc32);
    CHECK(st.identity_preserved && st.provenance_preserved &&
          st.regions_preserved && st.startup_preserved);
    CHECK(MapVariant_Encode(&variant, wire2, sizeof(wire2), &m) == MAP_VARIANT_OK);
    CHECK(m == n);
    CHECK(memcmp(wire2, wire0, n) == 0);

    /* 2. M1 = M0 × 11/10: значения, инварианты, CRC */
    scale_transform(&t, 11, 10);
    rc = MapVariant_Apply(&base, &t, &variant, &st);
    CHECK(rc == MAP_VARIANT_OK);
    CHECK(st.identity_preserved);
    CHECK(st.provenance_preserved);
    CHECK(st.regions_preserved);
    CHECK(st.startup_preserved);
    CHECK(MapVariant_IdentityMatches(&base, &variant));
    CHECK(variant.recon[0][0].m00 == 1100);
    CHECK(variant.recon[0][0].m01 == 110);
    CHECK(variant.recon[0][0].m10 == -110);
    CHECK(variant.recon[0][0].m11 == 1100);
    CHECK(variant.recon[5][1].m00 == 1100);
    CHECK(variant.crc32 == CurrentMap_CalculateCrc32(&variant));
    CHECK(variant.crc32 != base.crc32);
    CHECK(st.entries_changed == (uint32_t)(OEW_CURRENT_MAP_SECTOR_COUNT *
                                           OEW_CURRENT_MAP_WINDOW_COUNT));

    /* 2b. канонический encode + совместимость CRC с декодером прошивки */
    CHECK(MapVariant_Encode(&variant, wire1, sizeof(wire1), &m) == MAP_VARIANT_OK);
    CHECK(m == n);
    CHECK(MapVariant_Decode(wire1, m, &rt) == MAP_VARIANT_OK);
    CHECK(rt.crc32 == variant.crc32);
    CHECK(memcmp(wire1, wire0, n) != 0);

    /* 3. детерминизм: повторный apply → те же байты */
    CHECK(MapVariant_Apply(&base, &t, &variant2, &st2) == MAP_VARIANT_OK);
    CHECK(MapVariant_Encode(&variant2, wire2, sizeof(wire2), &m) == MAP_VARIANT_OK);
    CHECK(memcmp(wire1, wire2, m) == 0);
    CHECK(st2.crc_after == st.crc_after);
    CHECK(st2.coefficients_clamped == st.coefficients_clamped);

    /* 4. M2 = M1 + Δcommon */
    memset(&t, 0, sizeof(t));
    t.kind = MAP_VARIANT_COMMON_OFFSET;
    t.offset = 50;
    t.min_value = -10000;
    t.max_value = 10000;
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_OK);
    CHECK(variant.recon[0][0].m01 == 150);
    CHECK(variant.recon[5][1].m11 == 1050);
    CHECK(st.identity_preserved && st.regions_preserved);

    /* 5. Точечная правка sector/window */
    memset(&t, 0, sizeof(t));
    t.kind = MAP_VARIANT_SECTOR_WINDOW;
    t.scale_num = 2;
    t.scale_den = 1;
    t.target_sector = 3u;
    t.target_window = 1u;
    t.min_value = -10000;
    t.max_value = 10000;
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_OK);
    CHECK(st.entries_changed == 1u);
    CHECK(variant.recon[3][1].m00 == 2000);
    CHECK(variant.recon[2][1].m00 == 1000);

    /* 6. Негативные проверки */
    /* 6a повреждённый CRC */
    memcpy(tmp, wire0, n);
    tmp[n - 1u] ^= 0xFFu;
    CHECK(MapVariant_Decode(tmp, n, &rt) == MAP_VARIANT_ERR_DECODE);
    /* 6b неверная длина */
    CHECK(MapVariant_Decode(wire0, n - 1u, &rt) == MAP_VARIANT_ERR_DECODE);
    CHECK(MapVariant_Decode(wire0, n + 1u, &rt) == MAP_VARIANT_ERR_DECODE);
    /* 6c повреждённая магия */
    memcpy(tmp, wire0, n);
    tmp[0] ^= 0x01u;
    CHECK(MapVariant_Decode(tmp, n, &rt) == MAP_VARIANT_ERR_DECODE);
    /* 6d чужая identity: другой ARR, другая ревизия платы */
    build_fixture_identity(&other, base.timer_arr + 1u, 7u);
    CHECK(!MapVariant_IdentityMatches(&base, &other));
    build_fixture_identity(&other, base.timer_arr, 6u);
    CHECK(!MapVariant_IdentityMatches(&base, &other));
    build_fixture_identity(&other, base.timer_arr, 7u);
    CHECK(MapVariant_IdentityMatches(&base, &other));
    /* 6e параметры трансформации вне допустимого */
    scale_transform(&t, 1, 0);
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_TRANSFORM);
    scale_transform(&t, 0, 1);
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_TRANSFORM);
    scale_transform(&t, 1, 1);
    t.min_value = -20000;               /* clamp вне допустимого диапазона */
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_TRANSFORM);
    memset(&t, 0, sizeof(t));
    t.kind = MAP_VARIANT_SECTOR_WINDOW;
    t.scale_num = 2;
    t.scale_den = 1;
    t.target_sector = OEW_CURRENT_MAP_SECTOR_COUNT;  /* вне диапазона */
    t.min_value = -10000;
    t.max_value = 10000;
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_TRANSFORM);
    /* 6f переполнение */
    scale_transform(&t, 300000000, 1);
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_OVERFLOW);
    /* 6g admission: сильный downscale обнуляет матрицу → det == 0 */
    scale_transform(&t, 1, 100000);
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_ADMISSION);
    /* 6h отсутствие изменений */
    memset(&t, 0, sizeof(t));
    t.kind = MAP_VARIANT_COMMON_OFFSET;
    t.offset = 0;
    t.min_value = -10000;
    t.max_value = 10000;
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_ERR_TRANSFORM);
    /* 6i база с испорченным CRC */
    {
        OewCurrentMap bad = base;
        bad.crc32 ^= 1u;
        scale_transform(&t, 11, 10);
        CHECK(MapVariant_Apply(&bad, &t, &variant, &st) == MAP_VARIANT_ERR_DECODE);
    }
    /* 6j сатурация: явная, считается, и видна в статистике */
    scale_transform(&t, 100, 1);
    CHECK(MapVariant_Apply(&base, &t, &variant, &st) == MAP_VARIANT_OK);
    CHECK(st.coefficients_clamped > 0u);
    CHECK(variant.recon[0][0].m00 == 10000);
    CHECK(variant.recon[0][0].m10 == -10000);
    CHECK(st.coeff_min_after == -10000);
    CHECK(st.coeff_max_after == 10000);

    /* 7. Re-base identity (M0-rebased): коэффициенты байт-в-байт, identity из живой */
    {
        OewMapIdentity live;
        MapVariantRebaseStats rst;
        OewCurrentMap reb;

        memset(&live, 0, sizeof(live));
        live.board_revision = 7u;
        live.pwm_frequency_hz = 5000u;      /* истинные 5000 Гц вместо D3-значения 294 */
        live.timer_arr = 999u;
        live.adc_trigger_id = 0x4F455731u;
        live.trigger_offset_ticks = 0u;
        live.deadtime_ticks = 192u;
        live.adc_clock_hz = 42500000u;
        live.adc_sample_cycles_x2 = 1281u;
        live.adc_resolution = 0u;
        live.adc_config_signature = 0x26B9B97Bu;
        live.current_calibration_signature = 0xE98FCB2Cu;

        CHECK(MapVariant_RebaseIdentity(&base, &live, &reb, &rst) == MAP_VARIANT_OK);
        CHECK(rst.coefficients_preserved);                 /* проверка №1 acceptance */
        CHECK(rst.regions_preserved && rst.startup_preserved);
        CHECK(memcmp(reb.recon, base.recon, sizeof(base.recon)) == 0);
        CHECK(reb.board_revision == live.board_revision);  /* проверка №2 acceptance */
        CHECK(reb.pwm_frequency_hz == 5000u);
        CHECK(reb.adc_config_signature == live.adc_config_signature);
        CHECK(reb.current_calibration_signature == live.current_calibration_signature);
        CHECK(reb.provenance.dataset_crc32 == base.provenance.dataset_crc32); /* связь с датасетом */
        CHECK(reb.provenance.characterization_id == base.provenance.characterization_id);
        CHECK(reb.provenance.tool_build_id == MAP_VARIANT_REBASE_TOOL_ID);
        CHECK(reb.provenance.qualification_revision == base.provenance.qualification_revision + 1u);
        CHECK(reb.crc32 == CurrentMap_CalculateCrc32(&reb));
        CHECK(reb.crc32 != base.crc32);
        CHECK(rst.fields_changed >= 2u);                   /* pwm_frequency_hz + 2 подписи */
        /* проверка №3 acceptance: канонический encode + decode */
        CHECK(MapVariant_Encode(&reb, wire2, sizeof(wire2), &m) == MAP_VARIANT_OK);
        CHECK(MapVariant_Decode(wire2, m, &rt) == MAP_VARIANT_OK);
        CHECK(rt.pwm_frequency_hz == 5000u && rt.crc32 == reb.crc32);

        /* негатив: неполная живая identity (нулевая подпись) → отказ */
        live.adc_config_signature = 0u;
        CHECK(MapVariant_RebaseIdentity(&base, &live, &reb, &rst) == MAP_VARIANT_ERR_TRANSFORM);
        live.adc_config_signature = 0x26B9B97Bu;
        /* негатив: источник с битым CRC → отказ */
        {
            OewCurrentMap bad = base;
            bad.crc32 ^= 1u;
            CHECK(MapVariant_RebaseIdentity(&bad, &live, &reb, &rst) == MAP_VARIANT_ERR_DECODE);
        }
    }

    printf("map_variant_writer_test: PASS (%d checks)\n", checks);
    return 0;
}
