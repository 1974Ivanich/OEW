#include <stdio.h>
#include <string.h>

#include "foc_handoff_gate.h"

static int failures;

#define CHECK(expr) do { \
    if (!(expr)) { \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr); \
        failures++; \
    } \
} while (0)

static FocHandoffConfig default_config(void)
{
    FocHandoffConfig c;
    c.emf_min = 100;
    c.enc_min_rpm = 100;
    c.max_jerk_rpm_per_cycle = 200;
    c.min_id_internal = 20;
    c.required_consecutive = 3u;
    c.max_startup_cycles = 20u;
    c.max_handoff_cycles = 8u;
    return c;
}

static FocHandoffInput good_input(void)
{
    FocHandoffInput x;
    x.vf_complete = true;
    x.vf_erpm = 800;
    x.encoder_raw_rpm = 205;
    x.encoder_filtered_rpm = 200;
    x.pole_pairs = 4;
    x.emf_magnitude = 400;
    x.encoder_jerk_rpm = 10;
    x.previous_id_internal = 50;
    return x;
}

static void test_requires_three_stable_samples(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_READY);
    CHECK(g.latched_ready);
    CHECK(g.consecutive_good == 3u);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_READY);
}

static void test_bad_sample_resets_stability_counter(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);

    x.encoder_jerk_rpm = 200; /* strict < 200 requirement */
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(g.consecutive_good == 0u);

    x.encoder_jerk_rpm = 10;
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_READY);
}

static void test_wrong_direction_cannot_handoff(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    x.encoder_raw_rpm = -205;
    for (unsigned i = 0u; i < c.max_handoff_cycles; ++i) {
        CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    }
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_TIMEOUT);
    CHECK(g.latched_timeout);
}

static void test_low_emf_times_out_after_vf_complete(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    x.emf_magnitude = 100; /* requirement is strict > threshold */
    for (unsigned i = 0u; i < c.max_handoff_cycles; ++i) {
        CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    }
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_TIMEOUT);
}

static void test_startup_timeout_catches_vf_never_complete(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    x.vf_complete = false;
    for (unsigned i = 0u; i < c.max_startup_cycles; ++i) {
        CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    }
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_TIMEOUT);
    CHECK(g.handoff_cycles == 0u);
}

static void test_speed_mismatch_and_id_gate_are_strict(void)
{
    FocHandoffGate g;
    FocHandoffConfig c = default_config();
    FocHandoffInput x = good_input();

    FocHandoffGate_Init(&g);
    x.encoder_filtered_rpm = 300; /* enc_erpm=1200, mismatch=400; limit=266 */
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(g.consecutive_good == 0u);

    x = good_input();
    x.previous_id_internal = 19;
    CHECK(FocHandoffGate_Update(&g, &c, &x) == FOC_HANDOFF_PENDING);
    CHECK(g.consecutive_good == 0u);
}

int main(void)
{
    test_requires_three_stable_samples();
    test_bad_sample_resets_stability_counter();
    test_wrong_direction_cannot_handoff();
    test_low_emf_times_out_after_vf_complete();
    test_startup_timeout_catches_vf_never_complete();
    test_speed_mismatch_and_id_gate_are_strict();

    if (failures == 0) {
        puts("foc_handoff_gate_test: PASS");
        return 0;
    }
    printf("foc_handoff_gate_test: %d failure(s)\n", failures);
    return 1;
}
