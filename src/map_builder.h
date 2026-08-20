#ifndef MAP_BUILDER_H
#define MAP_BUILDER_H

#include <stdbool.h>
#include <stdint.h>

#include "current_map_selector.h"
#include "map_capture.h"

#ifndef MAP_BUILDER_MAX_RECORDS
#define MAP_BUILDER_MAX_RECORDS MAP_CAPTURE_MAX_PULSES
#endif

typedef struct {
    OewMapIdentity identity;
    uint16_t min_records_per_row;
    uint16_t startup_hold_cycles;
    uint8_t startup_sector;
    uint8_t startup_window;
    int16_t startup_mu;
    int16_t startup_mv;
    int16_t startup_mw;
    OewPwmRegion region[OEW_CURRENT_MAP_SECTOR_COUNT]
                        [OEW_CURRENT_MAP_WINDOW_COUNT];
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                           [OEW_CURRENT_MAP_WINDOW_COUNT];
} MapBuilderQualification;

typedef struct {
    uint32_t records_seen;
    uint16_t row_records[OEW_CURRENT_MAP_SECTOR_COUNT]
                         [OEW_CURRENT_MAP_WINDOW_COUNT];
    uint8_t initialized;
} MapBuilderStats;

/* Begin a board-qualified aggregation session. The qualification is immutable
 * for the lifetime of the builder and must come from a compiled profile. */
bool MapBuilder_Begin(const MapBuilderQualification *qualification);

/* Consume one immutable mapcap record. No record is accepted unless its exact
 * sector/window, trigger identity, PWM ARR and measured vector fit the
 * qualified row. */
bool MapBuilder_AddRecord(const MapCaptureRecord *record);

/* Construct a CRC-protected map only after every row has enough records. This
 * function never installs the map or changes control admission. */
bool MapBuilder_Finalize(OewCurrentMap *out, MapBuilderStats *stats);

void MapBuilder_Reset(void);
void MapBuilder_GetStats(MapBuilderStats *out);

#endif /* MAP_BUILDER_H */
