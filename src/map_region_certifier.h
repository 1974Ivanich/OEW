#ifndef MAP_REGION_CERTIFIER_H
#define MAP_REGION_CERTIFIER_H

#include <stdbool.h>
#include <stdint.h>

#include "current_map_selector.h"

#ifndef MAP_CERT_MAX_CELLS
#define MAP_CERT_MAX_CELLS 64u
#endif

typedef enum {
    MAP_CERT_OK = 0,
    MAP_CERT_BAD_ARGUMENT,
    MAP_CERT_NOT_ENOUGH_VALID,
    MAP_CERT_UNTESTED_INSIDE,
    MAP_CERT_INVALID_INSIDE,
    MAP_CERT_MARGIN_BAD,
    MAP_CERT_DEGENERATE
} MapCertStatus;

typedef struct {
    int16_t mu;
    int16_t mv;
    int16_t mw;
    uint16_t margin_ticks;
    uint8_t status; /* 0=UNTESTED, 1=VALID, 2=INVALID */
} MapGridCell;

typedef struct {
    uint16_t min_valid_cells;
    int16_t guard_q15;
    uint16_t min_margin_ticks;
} MapRegionQualification;

typedef struct {
    uint16_t valid_cells;
    uint16_t invalid_cells;
    uint16_t untested_cells;
    uint16_t min_margin_ticks;
    uint8_t ready;
} MapRegionReport;

MapCertStatus MapRegionCertify(
    const MapGridCell *cells,
    uint16_t cell_count,
    const MapRegionQualification *qualification,
    OewPwmRegion *out,
    MapRegionReport *report);

#endif /* MAP_REGION_CERTIFIER_H */
