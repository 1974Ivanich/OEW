#include "map_capture_profiles.h"

bool MapCaptureProfile_IsApproved(const MapCaptureRequest *request)
{
    (void)request;

    /* Deliberately fail closed. A future board-specific revision may replace
     * this with exact equality checks against a finite reviewed table; it must
     * not become a range check or accept a user-supplied pattern. */
    return false;
}

bool MapCaptureProfile_BuildQualification(uint32_t profile_id,
                                          MapBuilderQualification *out)
{
    (void)profile_id;
    (void)out;
    /* A map may reach MAP_READY only after a board-specific, reviewed
     * qualification supplies all twelve regions and reconstruction rows. */
    return false;
}

bool MapCaptureProfile_BuildRequest(uint32_t profile_id, uint32_t capture_id,
                                    MapCaptureRequest *out)
{
    (void)profile_id;
    (void)capture_id;
    (void)out;

    /* No approved physical pulse exists in a generic source package. */
    return false;
}
