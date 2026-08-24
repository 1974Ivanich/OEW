#include "map_characterization_adapter.h"

#include <string.h>

bool MapCharacterizationAdapter_FromCapture(
    const MapCaptureRecord *capture,
    const MapPhaseReference *reference,
    const MapTimingEvidence *timing,
    MapMeasurementSample *out)
{
    if ((capture == NULL) || (reference == NULL) ||
        (timing == NULL) || (out == NULL)) {
        return false;
    }

    /* Do not synthesize evidence. The downstream accumulator has explicit
     * checks for these fields, but rejecting here prevents an accidental
     * zero/default structure from entering the characterization pipeline. */
    if (timing->adc_settled == 0u || timing->scope_qualified == 0u) {
        return false;
    }

    if (timing->margin_ticks == 0u) {
        return false;
    }

    memset(out, 0, sizeof(*out));
    out->capture = *capture;
    out->reference = *reference;
    out->timing = *timing;
    return true;
}
