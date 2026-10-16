#include "foc_slip_policy.h"
#include "autotune.h"   /* AT_VALID_TR / AT_VALID_TR_MANUAL: происхождение Tr */

#include <assert.h>
#include <stdio.h>

static void test_unknown_tr_is_declared_zero_slip(void)
{
    assert(!FocSlipPolicy_TrUsable(0, true));
    assert(!FocSlipPolicy_TrUsable(FOC_SLIP_POLICY_MIN_TR_US - 1, true));
    assert(FocSlipPolicy_ComputeDelta(0, true, 100, 100) == 0);
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US - 1, true, 100, -100) == 0);
}

/* Провенанс: число, введённое оператором через mp=, угол поля не двигает,
 * каким бы правдоподобным оно ни было. */
static void test_manual_tr_moves_no_angle(void)
{
    assert(!FocSlipPolicy_TrUsable(100000, false));
    assert(FocSlipPolicy_ComputeDelta(100000, false, 200, 200) == 0);

    assert(FocSlipPolicy_TrUsable(100000, true));
    assert(FocSlipPolicy_ComputeDelta(100000, true, 200, 200) != 0);

    /* Биты: замер без ручного ввода — измерено; ручной ввод перекрывает замер,
     * сделанный раньше, потому что к текущему числу он отношения не имеет. */
    assert(AT_TrIsFromMeasurement(AT_VALID_TR));
    assert(!AT_TrIsFromMeasurement(AT_VALID_TR | AT_VALID_TR_MANUAL));
    assert(!AT_TrIsFromMeasurement(AT_VALID_TR_MANUAL));
    assert(!AT_TrIsFromMeasurement(0u));
}

static void test_measured_tr_and_current_floor(void)
{
    int32_t positive;
    int32_t negative;

    assert(FocSlipPolicy_TrUsable(FOC_SLIP_POLICY_MIN_TR_US, true));
    assert(FocSlipPolicy_ComputeDelta(100000, true,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL - 1, 100) == 0);

    positive = FocSlipPolicy_ComputeDelta(100000, true, 100, 100);
    negative = FocSlipPolicy_ComputeDelta(100000, true, 100, -100);
    assert(positive > 0);
    assert(negative < 0);
    assert(positive == -negative);
}

static void test_measured_slip_clamps(void)
{
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US, true,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL,
                                      32767) == FOC_SLIP_POLICY_MAX_DT);
    assert(FocSlipPolicy_ComputeDelta(FOC_SLIP_POLICY_MIN_TR_US, true,
                                      FOC_SLIP_POLICY_MIN_ID_INTERNAL,
                                      -32767) == -FOC_SLIP_POLICY_MAX_DT);
}

int main(void)
{
    test_unknown_tr_is_declared_zero_slip();
    test_manual_tr_moves_no_angle();
    test_measured_tr_and_current_floor();
    test_measured_slip_clamps();
    puts("foc_slip_policy_test: PASS");
    return 0;
}
