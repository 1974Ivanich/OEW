#include "foc_run_policy.h"

static int32_t abs_i32(int32_t value)
{
    return (value < 0) ? -value : value;
}

static bool opposite_nonzero_sign(int32_t a, int32_t b)
{
    return ((a > 0 && b < 0) || (a < 0 && b > 0));
}

void FocRunPolicy_Init(FocRunPolicy *policy)
{
    if (policy == 0) return;
    policy->encoder_bad_cycles = 0u;
    policy->reversal_zero_cycles = 0u;
    policy->reversal_pending = false;
    policy->pending_reverse_rpm = 0;
}

bool FocRunPolicy_RequestSpeed(FocRunPolicy *policy,
                               int32_t *active_speed_rpm,
                               int32_t measured_speed_rpm,
                               int32_t requested_rpm,
                               bool in_run)
{
    bool opposite_target;
    bool opposite_motion;

    if (policy == 0 || active_speed_rpm == 0) return false;

    opposite_target = opposite_nonzero_sign(*active_speed_rpm, requested_rpm);
    opposite_motion = opposite_nonzero_sign(measured_speed_rpm, requested_rpm);

    /* A new same-direction/zero request supersedes a queued reversal. The
     * measured-speed condition closes the case "command 0, then opposite
     * target while the rotor is still coasting". */
    if (!in_run || requested_rpm == 0 || (!opposite_target && !opposite_motion)) {
        policy->reversal_pending = false;
        policy->pending_reverse_rpm = 0;
        policy->reversal_zero_cycles = 0u;
        *active_speed_rpm = requested_rpm;
        return false;
    }

    /* Controlled reversal: command zero torque/speed first; the opposite
     * command becomes active only after shaft-speed evidence is stable. */
    policy->reversal_pending = true;
    policy->pending_reverse_rpm = requested_rpm;
    policy->reversal_zero_cycles = 0u;
    *active_speed_rpm = 0;
    return true;
}

FocRunPolicyResult FocRunPolicy_Update(FocRunPolicy *policy,
                                       int32_t *active_speed_rpm,
                                       int32_t encoder_raw_rpm,
                                       uint8_t encoder_error)
{
    bool encoder_bad;

    if (policy == 0 || active_speed_rpm == 0) return FOC_RUN_POLICY_ENCODER_LOST;

    /* A stopped shaft is legitimate only when zero was commanded. During a
     * pending reversal it is the desired transition evidence. */
    encoder_bad = (encoder_error != 0u) ||
                  ((*active_speed_rpm != 0) && (encoder_raw_rpm == 0));
    if (encoder_bad) {
        if (policy->encoder_bad_cycles < UINT8_MAX) {
            policy->encoder_bad_cycles++;
        }
        if (policy->encoder_bad_cycles >= FOC_RUN_POLICY_ENCODER_BAD_CYCLES) {
            return FOC_RUN_POLICY_ENCODER_LOST;
        }
    } else {
        policy->encoder_bad_cycles = 0u;
    }

    if (!policy->reversal_pending) return FOC_RUN_POLICY_OK;

    if (abs_i32(encoder_raw_rpm) <= FOC_RUN_POLICY_REVERSAL_ZERO_RPM) {
        if (policy->reversal_zero_cycles < UINT8_MAX) {
            policy->reversal_zero_cycles++;
        }
        if (policy->reversal_zero_cycles >= FOC_RUN_POLICY_REVERSAL_ZERO_CYCLES) {
            *active_speed_rpm = policy->pending_reverse_rpm;
            policy->pending_reverse_rpm = 0;
            policy->reversal_pending = false;
            policy->reversal_zero_cycles = 0u;
        }
    } else {
        policy->reversal_zero_cycles = 0u;
    }

    return FOC_RUN_POLICY_OK;
}

bool FocRunPolicy_IsReversalPending(const FocRunPolicy *policy)
{
    return (policy != 0) && policy->reversal_pending;
}
