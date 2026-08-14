#include "foc_run_policy.h"

#include <assert.h>
#include <stdio.h>

static void test_encoder_fault_debounce(void)
{
    FocRunPolicy policy;
    int32_t active = 800;
    unsigned i;

    FocRunPolicy_Init(&policy);
    for (i = 0u; i + 1u < FOC_RUN_POLICY_ENCODER_BAD_CYCLES; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 800, 1u) == FOC_RUN_POLICY_OK);
    }
    assert(FocRunPolicy_Update(&policy, &active, 800, 1u) ==
           FOC_RUN_POLICY_ENCODER_LOST);

    FocRunPolicy_Init(&policy);
    active = 800;
    assert(FocRunPolicy_Update(&policy, &active, 800, 1u) == FOC_RUN_POLICY_OK);
    assert(FocRunPolicy_Update(&policy, &active, 800, 0u) == FOC_RUN_POLICY_OK);
    for (i = 0u; i + 1u < FOC_RUN_POLICY_ENCODER_BAD_CYCLES; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
    assert(FocRunPolicy_Update(&policy, &active, 0, 0u) ==
           FOC_RUN_POLICY_ENCODER_LOST);
}

static void test_controlled_reversal(void)
{
    FocRunPolicy policy;
    int32_t active = 1000;
    unsigned i;

    FocRunPolicy_Init(&policy);
    assert(FocRunPolicy_RequestSpeed(&policy, &active, 900, -1000, true));
    assert(active == 0);
    assert(FocRunPolicy_IsReversalPending(&policy));

    /* Shaft is still moving: the opposite setpoint must remain withheld. */
    for (i = 0u; i < FOC_RUN_POLICY_REVERSAL_ZERO_CYCLES + 2u; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 100, 0u) == FOC_RUN_POLICY_OK);
        assert(active == 0);
        assert(FocRunPolicy_IsReversalPending(&policy));
    }

    /* Stable near-zero speed releases precisely the queued target. */
    for (i = 0u; i + 1u < FOC_RUN_POLICY_REVERSAL_ZERO_CYCLES; ++i) {
        assert(FocRunPolicy_Update(&policy, &active,
                                   FOC_RUN_POLICY_REVERSAL_ZERO_RPM, 0u) == FOC_RUN_POLICY_OK);
        assert(active == 0);
    }
    assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    assert(active == -1000);
    assert(!FocRunPolicy_IsReversalPending(&policy));
}

static void test_coasting_after_zero_command_cannot_bypass_gate(void)
{
    FocRunPolicy policy;
    int32_t active = 0;

    FocRunPolicy_Init(&policy);
    /* A positive shaft still coasting after a prior 0-rpm command needs the
     * same controlled transition before a negative target is accepted. */
    assert(FocRunPolicy_RequestSpeed(&policy, &active, 250, -500, true));
    assert(active == 0);
    assert(FocRunPolicy_IsReversalPending(&policy));
}

int main(void)
{
    test_encoder_fault_debounce();
    test_controlled_reversal();
    test_coasting_after_zero_command_cannot_bypass_gate();
    puts("foc_run_policy_test: PASS");
    return 0;
}
