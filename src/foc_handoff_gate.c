#include "foc_handoff_gate.h"

static int32_t abs_i32(int32_t x)
{
    /* Inputs are bounded physical values; avoid using abs() with int32_t. */
    return (x < 0) ? -x : x;
}

void FocHandoffGate_Init(FocHandoffGate *gate)
{
    if (gate == 0) {
        return;
    }
    gate->startup_cycles = 0u;
    gate->handoff_cycles = 0u;
    gate->consecutive_good = 0u;
    gate->latched_ready = false;
    gate->latched_timeout = false;
}

FocHandoffResult FocHandoffGate_Update(FocHandoffGate *gate,
                                        const FocHandoffConfig *cfg,
                                        const FocHandoffInput *in)
{
    int32_t pp;
    int32_t enc_erpm;
    int32_t mismatch;
    int32_t mismatch_limit;
    bool direction_ok;
    bool sample_ok;

    if ((gate == 0) || (cfg == 0) || (in == 0)) {
        return FOC_HANDOFF_TIMEOUT;
    }
    if (gate->latched_ready) {
        return FOC_HANDOFF_READY;
    }
    if (gate->latched_timeout) {
        return FOC_HANDOFF_TIMEOUT;
    }

    gate->startup_cycles++;
    if ((cfg->max_startup_cycles != 0u) &&
        (gate->startup_cycles > cfg->max_startup_cycles)) {
        gate->latched_timeout = true;
        return FOC_HANDOFF_TIMEOUT;
    }

    if (!in->vf_complete) {
        gate->consecutive_good = 0u;
        return FOC_HANDOFF_PENDING;
    }

    gate->handoff_cycles++;
    if ((cfg->max_handoff_cycles != 0u) &&
        (gate->handoff_cycles > cfg->max_handoff_cycles)) {
        gate->latched_timeout = true;
        return FOC_HANDOFF_TIMEOUT;
    }

    pp = (in->pole_pairs < 1) ? 1 : in->pole_pairs;
    enc_erpm = in->encoder_filtered_rpm * pp;
    mismatch = abs_i32(in->vf_erpm - enc_erpm);
    mismatch_limit = abs_i32(in->vf_erpm) / 3;

    direction_ok = ((in->vf_erpm > 0 && in->encoder_raw_rpm > 0) ||
                    (in->vf_erpm < 0 && in->encoder_raw_rpm < 0));

    sample_ok = (in->emf_magnitude > cfg->emf_min) &&
                (abs_i32(in->encoder_filtered_rpm) > cfg->enc_min_rpm) &&
                direction_ok &&
                (mismatch < mismatch_limit) &&
                (abs_i32(in->encoder_jerk_rpm) < cfg->max_jerk_rpm_per_cycle) &&
                (abs_i32(in->previous_id_internal) >= cfg->min_id_internal);

    if (!sample_ok) {
        gate->consecutive_good = 0u;
        return FOC_HANDOFF_PENDING;
    }

    if (gate->consecutive_good < UINT16_MAX) {
        gate->consecutive_good++;
    }
    if (gate->consecutive_good >= cfg->required_consecutive) {
        gate->latched_ready = true;
        return FOC_HANDOFF_READY;
    }
    return FOC_HANDOFF_PENDING;
}
