#include "map_commissioning.h"

static bool active(bool (*fn)(void))
{
    return fn != 0 && fn();
}

bool MapCommissioning_LoadMeasured(const OewCurrentMap *candidate,
                                   const MapReferenceManifest *manifest,
                                   const MapCommissioningOps *ops)
{
    OewMapIdentity live_identity;
    if (candidate == 0 || manifest == 0 || ops == 0 ||
        ops->get_identity == 0 || ops->load_measured == 0 ||
        ops->map_ready == 0) {
        return false;
    }
    if (active(ops->capture_active) || active(ops->foc_running) ||
        active(ops->vfc_running) || active(ops->autotune_active) ||
        active(ops->pwm_enabled) || active(ops->adc_injected_armed) ||
        active(ops->protect_fault)) {
        return false;
    }
    if (!ops->get_identity(&live_identity) ||
        !MapCandidate_IsCanonical(candidate, &live_identity, manifest)) {
        return false;
    }
    if (!ops->load_measured(candidate, &live_identity)) return false;
    return ops->map_ready();
}
