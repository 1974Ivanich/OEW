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

/* Canonical v2 serialization has no compiler padding: 32-byte identity,
 * 24-byte provenance, 10-byte startup, 12*19-byte recon entries,
 * 12*16-byte region entries and a 4-byte CRC. */
#define OEW_CURRENT_MAP_WIRE_SIZE 498u

/* Builds the firmware-consumable OewCurrentMap v2 artifact from already
 * qualified host-side characterization results. */
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

/* Canonical little-endian wire representation, independent of compiler ABI,
 * sizeof(OewCurrentMap), alignment and padding. */
size_t MapArtifactWriter_EncodeBinary(const OewCurrentMap *map,
                                      uint8_t *dst,
                                      size_t capacity);

/* Non-authoritative human-readable audit metadata; firmware never consumes it. */
size_t MapArtifactWriter_EncodeAuditJson(const OewCurrentMap *map,
                                         char *dst,
                                         size_t capacity);

#endif
