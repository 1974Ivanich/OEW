#ifndef MAP_ARTIFACT_WRITER_H
#define MAP_ARTIFACT_WRITER_H

#include <stddef.h>
#include <stdint.h>

#include "current_map_selector.h"
#include "map_measurement_accumulator.h"
#include "map_measurement_solver.h"
#include "map_region_certifier.h"

#define OEW_MAP_ARTIFACT_FORMAT_VERSION 2u
#define OEW_MAP_ARTIFACT_JSON_VERSION    1u

/* Builds the firmware-consumable OewCurrentMap v2 artifact from the already
 * qualified host-side characterization results. The function does not invent
 * qualification data: identity/provenance must be supplied by the caller and
 * the solver/certifier results must already have passed their own checks. */
bool MapArtifactWriter_Build(const OewMapIdentity *identity,
                             const OewMapProvenance *provenance,
                             uint8_t startup_sector,
                             uint8_t startup_window,
                             uint16_t startup_hold_cycles,
                             int16_t startup_mu,
                             int16_t startup_mv,
                             int16_t startup_mw,
                             const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                                                     [OEW_CURRENT_MAP_WINDOW_COUNT],
                             const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT]
                                                    [OEW_CURRENT_MAP_WINDOW_COUNT],
                             OewCurrentMap *out);

/* Serializes an already validated map exactly as the firmware expects it.
 * No host ABI/padding representation is emitted; the output is the canonical
 * little-endian wire representation. Returns the number of bytes written. */
size_t MapArtifactWriter_EncodeBinary(const OewCurrentMap *map,
                                      uint8_t *dst,
                                      size_t capacity);

/* Writes audit metadata for a map. This JSON is deliberately non-authoritative
 * and is not consumed by firmware. */
size_t MapArtifactWriter_EncodeAuditJson(const OewCurrentMap *map,
                                         char *dst,
                                         size_t capacity);

#endif
