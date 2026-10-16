#include "foc_slip_policy.h"

static int32_t abs_i32(int32_t value)
{
    return (value < 0) ? -value : value;
}

bool FocSlipPolicy_TrUsable(int32_t tr_us, bool tr_from_measurement)
{
    return tr_from_measurement && (tr_us >= FOC_SLIP_POLICY_MIN_TR_US);
}

int32_t FocSlipPolicy_ComputeDelta(int32_t tr_us,
                                   bool tr_from_measurement,
                                   int32_t id_internal,
                                   int32_t iq_internal)
{
    int64_t slip;

    /* tr_from_measurement == false means the value was typed by an operator
     * (mp=) or left over from a previous session. Feeding it to the field angle
     * is what produced the ±1 A torque oscillation on the 15.10 bench run, so
     * manual Tr is treated exactly like an unknown one: zero slip,
     * encoder-only angle. */
    if (!FocSlipPolicy_TrUsable(tr_us, tr_from_measurement) ||
        abs_i32(id_internal) < FOC_SLIP_POLICY_MIN_ID_INTERNAL) {
        return 0;
    }

    slip = ((int64_t)FOC_SLIP_POLICY_2PI_INV *
            FOC_SLIP_POLICY_PHASE_PER_HZ * iq_internal) /
           ((int64_t)tr_us * id_internal);
    if (slip > FOC_SLIP_POLICY_MAX_DT) return FOC_SLIP_POLICY_MAX_DT;
    if (slip < -FOC_SLIP_POLICY_MAX_DT) return -FOC_SLIP_POLICY_MAX_DT;
    return (int32_t)slip;
}
