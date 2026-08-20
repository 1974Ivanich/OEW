#ifndef MAP_CANDIDATE_H
#define MAP_CANDIDATE_H

#include <stdbool.h>
#include <stdint.h>

#include "current_map_selector.h"
#include "map_measurement_reference.h"

#define MAP_CANDIDATE_REQUIRED_ROWS \
    (OEW_CURRENT_MAP_SECTOR_COUNT * OEW_CURRENT_MAP_WINDOW_COUNT)

typedef enum {
    MAP_CANDIDATE_OK = 0,
    MAP_CANDIDATE_BAD_ARGUMENT,
    MAP_CANDIDATE_INCOMPLETE,
    MAP_CANDIDATE_PROVENANCE_BAD,
    MAP_CANDIDATE_IDENTITY_BAD,
    MAP_CANDIDATE_REGION_BAD,
    MAP_CANDIDATE_RECON_BAD,
    MAP_CANDIDATE_CRC_BAD
} MapCandidateStatus;

typedef struct {
    OewMapIdentity identity;
    MapReferenceManifest manifest;
    uint8_t startup_sector;
    uint8_t startup_window;
    uint16_t startup_hold_cycles;
    int16_t startup_mu;
    int16_t startup_mv;
    int16_t startup_mw;
} MapCandidateQualification;

MapCandidateStatus MapCandidate_Build(
    const MapCandidateQualification *qualification,
    const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                                      [OEW_CURRENT_MAP_WINDOW_COUNT],
    const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT]
                              [OEW_CURRENT_MAP_WINDOW_COUNT],
    OewCurrentMap *out);

bool MapCandidate_IsCanonical(const OewCurrentMap *map,
                              const OewMapIdentity *identity,
                              const MapReferenceManifest *manifest);

#endif /* MAP_CANDIDATE_H */
