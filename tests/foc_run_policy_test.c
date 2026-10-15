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

/* Замер 15.10.2026 на стенде: прямой старт снимался за 62 мс, потому что
 * политика считала «сырая скорость == 0 при ненулевой уставке» потерей
 * датчика с самого первого цикла, а 14-битный энкодер (кадр ~896 мкс) читает
 * РОВНО НОЛЬ ниже ~16 об/мин — то есть любой разгон с места был обречён.
 * Критерий теперь взводится после первого ненулевого отсчёта; защита от
 * настоящей потери датчика при этом сохраняется. */
static void test_startup_from_rest_is_not_encoder_lost(void)
{
    FocRunPolicy policy;
    int32_t active = 800;
    unsigned i;

    FocRunPolicy_Init(&policy);
    /* Разгон с места: нулевая скорость держится долго — это не потеря. */
    for (i = 0u; i < 1000u; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
    /* Ротор тронулся — критерий взведён. */
    assert(FocRunPolicy_Update(&policy, &active, 40, 0u) == FOC_RUN_POLICY_OK);
    /* И теперь нулевая скорость снова фатальна (после дебаунса): взведение
     * не отменяет защиту, а лишь откладывает её до первого движения. */
    for (i = 0u; i + 1u < FOC_RUN_POLICY_ENCODER_BAD_CYCLES; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
    assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_ENCODER_LOST);
}

/* Если за окно старта не появилось ни одного ненулевого отсчёта, привод
 * обязан остановиться: окно терпимости — не отключение защиты. */
static void test_never_moving_shaft_is_stopped_by_time(void)
{
    FocRunPolicy policy;
    int32_t active = 800;
    uint32_t i;

    FocRunPolicy_Init(&policy);
    for (i = 0u; i + 1u < FOC_RUN_POLICY_STARTUP_CYCLES; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
    assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_ENCODER_LOST);
}

/* Ноль, скомандованный оператором (или выставленный на время реверса), — это
 * законный покой: окно старта по нему срабатывать не должно, сколько бы
 * привод ни стоял. */
static void test_zero_command_never_moving_is_not_encoder_lost(void)
{
    FocRunPolicy policy;
    int32_t active = 0;
    uint32_t i;

    FocRunPolicy_Init(&policy);
    for (i = 0u; i < FOC_RUN_POLICY_STARTUP_CYCLES + 10u; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
}

/* Дрожание угла на стоящем валу (±20 отсчётов при кадре 896 мкс) даёт ±4 об/мин:
 * такие отсчёты НЕ должны взводить критерий, иначе защита возвращается сразу и
 * старт с места снова обречён (замер 15.10.2026: отказ через 31 мс). Порог
 * взведения — тот же, что у реверса (FOC_RUN_POLICY_REVERSAL_ZERO_RPM = 30). */
static void test_resting_jitter_does_not_arm_the_criterion(void)
{
    FocRunPolicy policy;
    int32_t active = 800;
    unsigned i;

    FocRunPolicy_Init(&policy);
    /* Короткий «блип» дрожания (±4 об/мин) — ниже порога, взводить не должен. */
    assert(FocRunPolicy_Update(&policy, &active, 4, 0u) == FOC_RUN_POLICY_OK);
    assert(FocRunPolicy_Update(&policy, &active, -4, 0u) == FOC_RUN_POLICY_OK);
    assert(FocRunPolicy_Update(&policy, &active, 8, 0u) == FOC_RUN_POLICY_OK);
    /* Затем длинная череда ТОЧНЫХ нулей — так и выглядит покой: дельта угла
     * равна нулю много кадров подряд. Со взведением по «!= 0» здесь приходила
     * ложная потеря датчика (замер: отказ через 31 мс), с порогом — не приходит. */
    for (i = 0u; i < 200u; ++i) {
        assert(FocRunPolicy_Update(&policy, &active, 0, 0u) == FOC_RUN_POLICY_OK);
    }
}

int main(void)
{
    test_encoder_fault_debounce();
    test_controlled_reversal();
    test_coasting_after_zero_command_cannot_bypass_gate();
    test_startup_from_rest_is_not_encoder_lost();
    test_never_moving_shaft_is_stopped_by_time();
    test_zero_command_never_moving_is_not_encoder_lost();
    test_resting_jitter_does_not_arm_the_criterion();
    puts("foc_run_policy_test: PASS");
    return 0;
}
