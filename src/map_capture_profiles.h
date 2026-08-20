#ifndef MAP_CAPTURE_PROFILES_H
#define MAP_CAPTURE_PROFILES_H

#include <stdbool.h>

#include "map_capture.h"
#include "map_builder.h"
#include "map_builder.h"

/* Board-specific commissioning policy. The shipped implementation rejects all
 * patterns. Replace it only with a reviewed, immutable allow-list whose CCR,
 * limits, sector/window and trigger revision were approved for this board.
 * It must never accept arbitrary UART-provided CCR values. */
bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request);

/* Populate one complete bounded request from a compiled, board-specific profile.
 * The shipped implementation rejects every profile. */
bool MapCaptureProfile_BuildRequest(uint32_t profile_id, uint32_t capture_id,
                                    MapCaptureRequest *out);

/* Populate immutable board-qualified map geometry and reconstruction
 * coefficients. The generic package rejects every profile. */
bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out);

/* Populate immutable board-qualified map geometry and reconstruction
 * coefficients. The generic package rejects every profile. */
bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out);

#endif /* MAP_CAPTURE_PROFILES_H */
