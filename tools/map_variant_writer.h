#ifndef MAP_VARIANT_WRITER_H
#define MAP_VARIANT_WRITER_H

/*
 * TZ-02 experimental M-tuning: host/offline генератор вариантов карты.
 *
 * Инварианты (см. docs/tools/map_variant_writer.md и EXPERIMENT_M_TUNING_PROTOCOL.md):
 *  - генератор работает ТОЛЬКО офлайн, firmware/control algorithm не изменяются;
 *  - вход и выход — канонический артефакт v2 (OEW_CURRENT_MAP_WIRE_SIZE = 497 байт);
 *  - неизменяемые поля (identity, provenance, регионы, startup) проверяются, а не предполагаются;
 *  - CRC считается каноническим энкодером после трансформации (CurrentMap_CalculateCrc32);
 *  - выход проходит admission-проверки прошивки (entry_is_sane/map_regions_sane/CurrentRecon_LoadMap);
 *  - трансформация детерминирована: целочисленная арифметика, явное округление и явная сатурация.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "current_map_selector.h"

typedef enum {
    MAP_VARIANT_GLOBAL_SCALE = 0,  /* M1 = M0 * num / den, все 12 записей */
    MAP_VARIANT_COMMON_OFFSET,     /* M2 = M1 + offset, все 12 записей */
    MAP_VARIANT_SECTOR_WINDOW      /* точечно: только указанная запись sector/window */
} MapVariantKind;

typedef struct {
    MapVariantKind kind;
    int32_t scale_num;      /* для GLOBAL_SCALE/SECTOR_WINDOW; != 0 */
    int32_t scale_den;      /* > 0 */
    int32_t offset;         /* для COMMON_OFFSET */
    uint8_t target_sector;  /* для SECTOR_WINDOW: < OEW_CURRENT_MAP_SECTOR_COUNT */
    uint8_t target_window;  /* для SECTOR_WINDOW: < OEW_CURRENT_MAP_WINDOW_COUNT */
    int32_t min_value;      /* сатурация; обязана лежать в [-MAX_COEFF; +MAX_COEFF] */
    int32_t max_value;
} MapVariantTransform;

typedef enum {
    MAP_VARIANT_OK = 0,
    MAP_VARIANT_ERR_ARGS,       /* некорректные аргументы */
    MAP_VARIANT_ERR_DECODE,     /* wire → struct: размер/магия/ревизия/CRC */
    MAP_VARIANT_ERR_TRANSFORM,  /* параметры трансформации вне допустимого */
    MAP_VARIANT_ERR_OVERFLOW,   /* результат не представим: |значение| > INT32 */
    MAP_VARIANT_ERR_ADMISSION,  /* запись/регион не проходят sanity прошивки */
    MAP_VARIANT_ERR_IDENTITY,   /* изменились неизменяемые поля */
    MAP_VARIANT_ERR_ENCODE      /* канонический энкодер отказал */
} MapVariantStatus;

typedef struct {
    uint32_t entries_total;
    uint32_t entries_changed;
    uint32_t coefficients_clamped;   /* сколько коэффициентов упёрлось в сатурацию */
    int32_t coeff_min_before;
    int32_t coeff_max_before;
    int32_t coeff_min_after;
    int32_t coeff_max_after;
    uint32_t crc_before;
    uint32_t crc_after;
    bool identity_preserved;
    bool provenance_preserved;
    bool regions_preserved;
    bool startup_preserved;
} MapVariantStats;

const char *MapVariant_StatusName(MapVariantStatus status);

/* Канонический декодер (src/map_artifact_decoder.c): магия, ревизия, CRC. */
MapVariantStatus MapVariant_Decode(const uint8_t *wire, size_t length,
                                   OewCurrentMap *out);

/* Трансформация с проверками: параметры, переполнение, сатурация, admission, инварианты. */
MapVariantStatus MapVariant_Apply(const OewCurrentMap *base,
                                  const MapVariantTransform *transform,
                                  OewCurrentMap *out,
                                  MapVariantStats *stats);

/* Канонический энкодер; written должен получиться равным OEW_CURRENT_MAP_WIRE_SIZE. */
MapVariantStatus MapVariant_Encode(const OewCurrentMap *map,
                                   uint8_t *dst, size_t capacity, size_t *written);

/* Сравнение неизменяемых групп полей (для тестов identity-preservation). */
bool MapVariant_IdentityMatches(const OewCurrentMap *base, const OewCurrentMap *variant);

/* JSON-манифест варианта (аудит; прошивкой не потребляется). Возвращает длину строки. */
size_t MapVariant_WriteManifestJson(const OewCurrentMap *base,
                                    const OewCurrentMap *variant,
                                    const MapVariantTransform *transform,
                                    const MapVariantStats *stats,
                                    const char *variant_name,
                                    char *dst, size_t capacity);

#endif /* MAP_VARIANT_WRITER_H */
