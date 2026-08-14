#include "foc_slip_policy.h"

#include <assert.h>
#include <stdio.h>

static void test_unknown_tr_is_declared_zero_slip(void)
{
    assert(!FocSlipPolicy_IsMeasuredTr(0));
    assert(!FocSlipPolicy_IsMeasuredTr(FOC_SLIP_POLICY_MIN_TR_US - 1));
    assert(FocSlipPolicy_ComputeDelta(0, 100, 100) == 0);
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US - 1, 100, -100) == 0);
}

static void test_measured_tr_and_current_floor(void)
{
    int32_t positive;
    int32_t negative;

    assert(FocSlipPolicy_IsMeasuredTr(FOC_SLIP_POLICY_MIN_TR_US));
    assert(FocSlipPolicy_ComputeDelta(100000,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL - 1, 100) == 0);

    positive = FocSlipPolicy_ComputeDelta(100000, 100, 100);
    negative = FocSlipPolicy_ComputeDelta(100000, 100, -100);
    assert(positive > 0);
    assert(negative < 0);
    assert(positive == -negative);
}

static void test_measured_slip_clamps(void)
{
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL,
                                      32767) == FOC_SLIP_POLICY_MAX_DT);
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL,
                                      -32767) == -FOC_SLIP_POLICY_MAX_DT);
}

int main(void)
{
    test_unknown_tr_is_declared_zero_slip();
    test_measured_tr_and_current_floor();
    test_measured_slip_clamps();
    puts("foc_slip_policy_test: PASS");
    return 0;
}
