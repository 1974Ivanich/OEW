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

    {
            const bool foc = ops->foc_running != 0 && ops->foc_running();
            const bool vf = ops->vf_running != 0 && ops->vf_running();
            const bool control = foc || vf;

            if (ops->get_latest_frame == 0 || !ops->get_latest_frame(&frame)) {
                if (control && ops->latch_frame_copy_failure != 0) {
                    ops->latch_frame_copy_failure();
                }
                return;
            }
            if (control && ops->protect_check_frame != 0) {
                ops->protect_check_frame(&frame);
            }
            if (ops->protect_is_fault != 0 && ops->protect_is_fault()) {
                if (foc && ops->foc_stop != 0) ops->foc_stop();
                if (vf && ops->vf_stop != 0) ops->vf_stop();
            } else if (foc && ops->timer_enabled != 0 && ops->timer_enabled() && ops->foc_run_frame != 0) {
                ops->foc_run_frame(&frame);
            }
        }
}
