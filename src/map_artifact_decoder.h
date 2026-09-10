#ifndef MAP_ARTIFACT_DECODER_H
#define MAP_ARTIFACT_DECODER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "current_map_selector.h"

/* Canonical v3 wire size (must match tools/map_artifact_writer.h). */
#define OEW_CURRENT_MAP_WIRE_SIZE 545u

/* Decode the canonical little-endian v3 wire representation produced by
 * MapArtifactWriter_EncodeBinary back into OewCurrentMap. */
bool MapArtifact_DecodeBinary(const uint8_t *src, size_t length,
                              OewCurrentMap *out);

#endif /* MAP_ARTIFACT_DECODER_H */
