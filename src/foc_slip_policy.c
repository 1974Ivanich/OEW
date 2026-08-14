#include "foc_slip_policy.h"

static int32_t abs_i32(int32_t value)
{
    return (value < 0) ? -value : value;
}

bool FocSlipPolicy_IsMeasuredTr(int32_t tr_us)
{
    return tr_us >= FOC_SLIP_POLICY_MIN_TR_US;
}

int32_t FocSlipPolicy_ComputeDelta(int32_t tr_us,
                                   int32_t id_internal,
                                   int32_t iq_internal)
{
    int64_t slip;

    if (!FocSlipPolicy_IsMeasuredTr(tr_us) ||
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
