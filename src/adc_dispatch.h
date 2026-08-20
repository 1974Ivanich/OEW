#ifndef ADC_DISPATCH_H
#define ADC_DISPATCH_H

#include <stdbool.h>
#include "adc.h"

typedef struct {
    bool (*capture_active)(void);
    bool (*get_latest_frame)(AdcFrame *frame);
    void (*capture_on_frame)(const AdcFrame *frame);
    void (*capture_on_missing)(void);
    bool (*foc_running)(void);
    bool (*timer_enabled)(void);
    void (*latch_frame_copy_failure)(void);
    void (*protect_check_frame)(const AdcFrame *frame);
    bool (*protect_is_fault)(void);
    void (*foc_stop)(void);
    void (*foc_run_frame)(const AdcFrame *frame);
} AdcDispatchOps;

void AdcDispatch_Handle(bool injected_event, const AdcDispatchOps *ops);

#endif /* ADC_DISPATCH_H */
