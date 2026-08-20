#ifndef MAP_COMMISSIONING_H
#define MAP_COMMISSIONING_H

#include <stdbool.h>

#include "map_candidate.h"

typedef struct {
    bool (*capture_active)(void);
    bool (*foc_running)(void);
    bool (*vfc_running)(void);
    bool (*autotune_active)(void);
    bool (*pwm_enabled)(void);
    bool (*adc_injected_armed)(void);
    bool (*protect_fault)(void);
    bool (*get_identity)(OewMapIdentity *identity);
    bool (*load_measured)(const OewCurrentMap *map,
                          const OewMapIdentity *identity);
    bool (*map_ready)(void);
} MapCommissioningOps;

bool MapCommissioning_LoadMeasured(const OewCurrentMap *candidate,
                                   const MapReferenceManifest *manifest,
                                   const MapCommissioningOps *ops);

#endif /* MAP_COMMISSIONING_H */
