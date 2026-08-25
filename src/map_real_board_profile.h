#ifndef MAP_REAL_BOARD_PROFILE_H
#define MAP_REAL_BOARD_PROFILE_H

#include <stdbool.h>
#include <stdint.h>

#include "map_capture.h"
#include "current_map_selector.h"

#define MAP_REAL_PROFILE_REVISION 1u
#define MAP_REAL_PROFILE_MAX_ROWS 12u

typedef struct {
    uint16_t pulse_count;
    uint16_t timeout_periods;
    int32_t max_abs_shunt_ma;
    uint32_t min_vbus_mv;
    uint32_t max_vbus_mv;
    uint16_t min_margin_ticks;
    uint8_t sector;
    uint8_t window;
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
} MapRealProfileRow;

typedef struct {
    uint32_t revision;
    OewMapIdentity identity;
    uint16_t row_count;
    uint8_t timing_qualified;
    uint8_t external_reference_required;
    MapRealProfileRow row[MAP_REAL_PROFILE_MAX_ROWS];
} MapRealBoardProfile;

/* Validate a profile against the live board identity. No hardware is changed. */
bool MapRealBoardProfile_Validate(const MapRealBoardProfile *profile,
                                  const OewMapIdentity *live_identity);

/* Convert one reviewed row into an immutable MapCaptureRequest. The function
 * deliberately does not populate any reconstruction coefficients. */
bool MapRealBoardProfile_BuildRequest(const MapRealBoardProfile *profile,
                                     uint16_t row_index,
                                     uint32_t capture_id,
                                     MapCaptureRequest *out);

/* Require all 12 rows, unique sector/window pairs, explicit timing
 * qualification and a nonzero minimum margin. */
bool MapRealBoardProfile_IsComplete(const MapRealBoardProfile *profile);

#endif /* MAP_REAL_BOARD_PROFILE_H */
