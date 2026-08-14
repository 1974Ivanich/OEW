#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "control_isr.h"

typedef struct {
    bool foc_running;
    bool pwm_enabled;
    bool protect_fault;
    unsigned read_calls;
    unsigned protect_calls;
    unsigned foc_run_calls;
    unsigned foc_stop_calls;
} Fake;

static Fake g;
static int failures;

static bool foc_is_running(void)   { return g.foc_running; }
static bool pwm_is_enabled(void)   { return g.pwm_enabled; }
static void adc_read_injected(void){ g.read_calls++; }
static void protect_check(void)    { g.protect_calls++; }
static bool protect_is_fault(void) { return g.protect_fault; }
static void foc_run(void)          { g.foc_run_calls++; }
static void foc_stop(void) {
    g.foc_stop_calls++;
    g.foc_running = false;
    g.pwm_enabled = false;
}

static const ControlIsrOps ops = {
    .foc_is_running = foc_is_running,
    .pwm_is_enabled = pwm_is_enabled,
    .adc_read_injected = adc_read_injected,
    .protect_check = protect_check,
    .protect_is_fault = protect_is_fault,
    .foc_run = foc_run,
    .foc_stop = foc_stop
};

static void reset_fake(void)
{
    g.foc_running = true;
    g.pwm_enabled = true;
    g.protect_fault = false;
    g.read_calls = 0u;
    g.protect_calls = 0u;
    g.foc_run_calls = 0u;
    g.foc_stop_calls = 0u;
}

#define CHECK(expr) do { \
    if (!(expr)) { \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr); \
        failures++; \
    } \
} while (0)

static void test_normal_jeos_runs_once(void)
{
    ControlIsrStats stats = {0};
    reset_fake();

    ControlISR_Handle(CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(stats.jeos_count == 1u);
    CHECK(g.read_calls == 1u);
    CHECK(g.protect_calls == 1u);
    CHECK(g.foc_run_calls == 1u);
    CHECK(g.foc_stop_calls == 0u);
}

static void test_protection_trip_stops_before_foc_run(void)
{
    ControlIsrStats stats = {0};
    reset_fake();
    g.protect_fault = true;

    ControlISR_Handle(CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(g.read_calls == 1u);
    CHECK(g.protect_calls == 1u);
    CHECK(g.foc_stop_calls == 1u);
    CHECK(g.foc_run_calls == 0u);
    CHECK(!g.foc_running);
    CHECK(!g.pwm_enabled);
}

static void test_late_jeos_never_runs_stopped_pwm(void)
{
    ControlIsrStats stats = {0};
    reset_fake();
    g.pwm_enabled = false;

    ControlISR_Handle(CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(stats.jeos_count == 1u);
    CHECK(stats.late_jeos_count == 1u);
    CHECK(g.read_calls == 1u);
    CHECK(g.protect_calls == 0u);
    CHECK(g.foc_run_calls == 0u);
    CHECK(g.foc_stop_calls == 0u);
}

static void test_jeos_when_foc_stopped_is_diagnostic_only(void)
{
    ControlIsrStats stats = {0};
    reset_fake();
    g.foc_running = false;
    g.pwm_enabled = false;

    ControlISR_Handle(CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(stats.skipped_foc_count == 1u);
    CHECK(g.read_calls == 1u);
    CHECK(g.protect_calls == 0u);
    CHECK(g.foc_run_calls == 0u);
}

static void test_jqovf_is_fatal_and_does_not_consume_jdr(void)
{
    ControlIsrStats stats = {0};
    reset_fake();

    ControlISR_Handle(CONTROL_ISR_EVT_JQOVF | CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(stats.jqovf_count == 1u);
    CHECK(stats.jeos_count == 0u);
    CHECK(g.foc_stop_calls == 1u);
    CHECK(g.read_calls == 0u);
    CHECK(g.protect_calls == 0u);
    CHECK(g.foc_run_calls == 0u);
}

static void test_ovr_is_counted_without_changing_valid_jeos_path(void)
{
    ControlIsrStats stats = {0};
    reset_fake();

    ControlISR_Handle(CONTROL_ISR_EVT_OVR | CONTROL_ISR_EVT_JEOS, &stats, &ops);

    CHECK(stats.ovr_count == 1u);
    CHECK(stats.jeos_count == 1u);
    CHECK(g.foc_run_calls == 1u);
}

int main(void)
{
    test_normal_jeos_runs_once();
    test_protection_trip_stops_before_foc_run();
    test_late_jeos_never_runs_stopped_pwm();
    test_jeos_when_foc_stopped_is_diagnostic_only();
    test_jqovf_is_fatal_and_does_not_consume_jdr();
    test_ovr_is_counted_without_changing_valid_jeos_path();

    if (failures == 0) {
        puts("control_isr_test: PASS");
        return 0;
    }
    printf("control_isr_test: %d failure(s)\n", failures);
    return 1;
}
