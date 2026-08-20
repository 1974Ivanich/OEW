#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "adc_dispatch.h"

static struct {
    bool capture_active;
    bool foc_running;
    bool timer_enabled;
    bool frame_available;
    bool fault;
    unsigned capture_frame;
    unsigned capture_missing;
    unsigned copy_failure;
    unsigned protect;
    unsigned stop;
    unsigned run;
    AdcFrame frame;
} g;

static bool capture_active(void) { return g.capture_active; }
static bool get_frame(AdcFrame *out) { if (!g.frame_available) return false; *out = g.frame; return true; }
static void capture_frame(const AdcFrame *frame) { assert(frame != 0); ++g.capture_frame; }
static void capture_missing(void) { ++g.capture_missing; }
static bool foc_running(void) { return g.foc_running; }
static bool timer_enabled(void) { return g.timer_enabled; }
static void copy_failure(void) { ++g.copy_failure; }
static void protect_frame(const AdcFrame *frame) { assert(frame != 0); ++g.protect; }
static bool protect_fault(void) { return g.fault; }
static void stop_foc(void) { ++g.stop; }
static void run_foc(const AdcFrame *frame) { assert(frame != 0); ++g.run; }

static const AdcDispatchOps ops = {
    capture_active, get_frame, capture_frame, capture_missing,
    foc_running, timer_enabled, copy_failure, protect_frame,
    protect_fault, stop_foc, run_foc
};

static void reset(void)
{
    memset(&g, 0, sizeof(g));
    g.frame.status = ADC_FRAME_VALID;
}

static void test_non_event(void)
{
    reset(); AdcDispatch_Handle(false, &ops);
    assert(g.capture_frame == 0u && g.run == 0u && g.protect == 0u);
}

static void test_capture_precedes_foc(void)
{
    reset(); g.capture_active = true; g.frame_available = true;
    g.foc_running = true; g.timer_enabled = true;
    AdcDispatch_Handle(true, &ops);
    assert(g.capture_frame == 1u && g.protect == 0u && g.run == 0u);
}

static void test_capture_missing_frame(void)
{
    reset(); g.capture_active = true;
    AdcDispatch_Handle(true, &ops);
    assert(g.capture_missing == 1u && g.run == 0u && g.copy_failure == 0u);
}

static void test_copy_failure_latches_while_running(void)
{
    reset(); g.foc_running = true;
    AdcDispatch_Handle(true, &ops);
    assert(g.copy_failure == 1u && g.protect == 0u && g.run == 0u);
}

static void test_fault_stops_before_run(void)
{
    reset(); g.frame_available = true; g.foc_running = true;
    g.timer_enabled = true; g.fault = true;
    AdcDispatch_Handle(true, &ops);
    assert(g.protect == 1u && g.stop == 1u && g.run == 0u);
}

static void test_timer_off_skips_protection_and_run(void)
{
    reset(); g.foc_running = true; g.timer_enabled = false;
    AdcDispatch_Handle(true, &ops);
    assert(g.protect == 0u && g.run == 0u && g.stop == 0u);
}

static void test_normal_frame_runs_once(void)
{
    reset(); g.frame_available = true; g.foc_running = true;
    g.timer_enabled = true;
    AdcDispatch_Handle(true, &ops);
    assert(g.protect == 1u && g.run == 1u && g.stop == 0u);
}

int main(void)
{
    test_non_event(); test_capture_precedes_foc(); test_capture_missing_frame();
    test_copy_failure_latches_while_running(); test_fault_stops_before_run();
    test_timer_off_skips_protection_and_run(); test_normal_frame_runs_once();
    puts("adc_dispatch_test: PASS");
    return 0;
}
