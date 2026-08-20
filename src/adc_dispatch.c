#include "adc_dispatch.h"

void AdcDispatch_Handle(bool injected_event, const AdcDispatchOps *ops)
{
    AdcFrame frame;
    if (!injected_event || ops == 0) return;

    if (ops->capture_active != 0 && ops->capture_active()) {
        if (ops->get_latest_frame == 0 || !ops->get_latest_frame(&frame)) {
            if (ops->capture_on_missing != 0) ops->capture_on_missing();
        } else if (ops->capture_on_frame != 0) {
            ops->capture_on_frame(&frame);
        }
        return;
    }

    if (ops->get_latest_frame == 0 || !ops->get_latest_frame(&frame)) {
        if (ops->foc_running != 0 && ops->foc_running() &&
            ops->latch_frame_copy_failure != 0) {
            ops->latch_frame_copy_failure();
        }
        return;
    }
    if (ops->foc_running != 0 && ops->foc_running() &&
        ops->timer_enabled != 0 && ops->timer_enabled()) {
        if (ops->protect_check_frame != 0) ops->protect_check_frame(&frame);
        if (ops->protect_is_fault != 0 && ops->protect_is_fault()) {
            if (ops->foc_stop != 0) ops->foc_stop();
        } else if (ops->foc_run_frame != 0) {
            ops->foc_run_frame(&frame);
        }
    }
}
