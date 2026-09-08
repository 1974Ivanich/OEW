#ifndef MAP_ARTIFACT_DECODER_H
#define MAP_ARTIFACT_DECODER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "current_map_selector.h"

/* Canonical v2 wire size (must match tools/map_artifact_writer.h). */
#define OEW_CURRENT_MAP_WIRE_SIZE 497u

/* Decode the canonical little-endian wire representation produced by
 * MapArtifactWriter_EncodeBinary back into the firmware OewCurrentMap struct.
 *
 * Returns true only if:
 *   - src is non-null and length == OEW_CURRENT_MAP_WIRE_SIZE (497)
 *   - out is non-null
 *   - wire magic == OEW_CURRENT_MAP_MAGIC
 *   - wire revision == OEW_CURRENT_MAP_REVISION
 *   - wire CRC matches CurrentMap_CalculateCrc32 of the reconstructed struct
 *
 * On failure the contents of *out are undefined. The caller MUST NOT read
 * any field of *out after a false return. */
bool MapArtifact_DecodeBinary(const uint8_t *src, size_t length,
                              OewCurrentMap *out);

#endif /* MAP_ARTIFACT_DECODER_H */
