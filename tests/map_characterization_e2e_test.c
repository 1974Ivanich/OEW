#include <assert.h>
#include <string.h>

#include "map_characterization_adapter.h"

static void test_rejects_missing_evidence(void)
{
    MapCaptureRecord capture;
    MapPhaseReference ref;
    MapTimingEvidence timing;
    MapMeasurementSample sample;

    memset(&capture, 0, sizeof(capture));
    memset(&ref, 0, sizeof(ref));
    memset(&timing, 0, sizeof(timing));
    memset(&sample, 0xA5, sizeof(sample));

    assert(!MapCharacterizationAdapter_FromCapture(&capture, &ref, &timing, &sample));
}

static void test_accepts_complete_evidence(void)
{
    MapCaptureRecord capture;
    MapPhaseReference ref;
    MapTimingEvidence timing;
    MapMeasurementSample sample;

    memset(&capture, 0, sizeof(capture));
    memset(&ref, 0, sizeof(ref));
    memset(&timing, 0, sizeof(timing));
    memset(&sample, 0, sizeof(sample));

    capture.pwm.timer_arr = 999u;
    capture.pwm.deadtime_ticks = 20u;
    ref.phase_u_ma = 100;
    ref.phase_v_ma = -40;
    ref.phase_w_ma = -60;
    timing.adc_settled = 1u;
    timing.scope_qualified = 1u;
    timing.margin_ticks = 80u;

    assert(MapCharacterizationAdapter_FromCapture(&capture, &ref, &timing, &sample));
    assert(sample.capture.pwm.timer_arr == 999u);
    assert(sample.capture.pwm.deadtime_ticks == 20u);
    assert(sample.reference.phase_u_ma == 100);
    assert(sample.reference.phase_v_ma == -40);
    assert(sample.reference.phase_w_ma == -60);
    assert(sample.timing.margin_ticks == 80u);
}

int main(void)
{
    test_rejects_missing_evidence();
    test_accepts_complete_evidence();
    return 0;
}
