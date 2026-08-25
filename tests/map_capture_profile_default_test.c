#include <assert.h>
#include <stdint.h>

#include "map_capture_profiles.h"

int main(void)
{
    MapCaptureRequest request = {0};
    MapBuilderQualification qualification = {0};

    assert(!MapCaptureProfile_IsApproved(&request));
    assert(!MapCaptureProfile_BuildRequest(0x53594E54u, 1u, &request));
    assert(!MapCaptureProfile_BuildQualification(0x53594E54u, &qualification));
    return 0;
}
