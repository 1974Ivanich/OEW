#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "break_diagnostics.h"

uint32_t host_primask;
void HostIrqRestoreHook(uint32_t restored_primask) { (void)restored_primask; }

static BreakDiagnostics sample(uint32_t timestamp, uint32_t tim1_sr,
                               uint32_t tim8_sr, uint8_t sd1, uint8_t sd2)
{
    BreakDiagnostics value;
    memset(&value, 0, sizeof(value));
    value.timestamp_cycles = timestamp;
    value.tim1_sr = tim1_sr;
    value.tim8_sr = tim8_sr;
    value.tim1_bdtr = 0x1CC0u;
    value.tim8_bdtr = 0x1CC0u;
    value.tim1_ccer = 0x555u;
    value.tim8_ccer = 0x555u;
    value.tim1_cnt = 12u;
    value.tim8_cnt = 13u;
    value.capture_id = 42u;
    value.capture_frames = 7u;
    value.capture_state = 2u;
    value.sd1_high = sd1;
    value.sd2_high = sd2;
    return value;
}

int main(void)
{
    BreakDiagnostics out;
    BreakDiagnostics first = sample(100u, 0x80u, 0u, 0u, 1u);
    BreakDiagnostics second = sample(200u, 0u, 0x80u, 1u, 0u);

    host_primask = 0u;
    assert(!BreakDiagnostics_Get(&out));
    assert(!BreakDiagnostics_Get(0));
    assert(BreakDiagnostics_Reset(false));
    assert(host_primask == 0u);

    BreakDiagnostics_RecordFromIsr(BREAK_DIAG_SOURCE_TIM1, &first);
    assert(BreakDiagnostics_Get(&out));
    assert(out.valid == 1u);
    assert(out.sequence == 1u);
    assert(out.source == BREAK_DIAG_SOURCE_TIM1);
    assert(out.timestamp_cycles == 100u);
    assert(out.tim1_sr == 0x80u && out.tim8_sr == 0u);
    assert(out.sd1_high == 0u && out.sd2_high == 1u);
    assert(out.capture_id == 42u && out.capture_frames == 7u);
    assert(out.capture_state == 2u);

    BreakDiagnostics_RecordFromIsr(BREAK_DIAG_SOURCE_TIM8, &second);
    assert(BreakDiagnostics_Get(&out));
    assert(out.sequence == 1u);
    assert(out.source == BREAK_DIAG_SOURCE_TIM1);
    assert(out.timestamp_cycles == 100u);

    assert(!BreakDiagnostics_Reset(true));
    assert(BreakDiagnostics_Get(&out));
    assert(BreakDiagnostics_Reset(false));
    assert(!BreakDiagnostics_Get(&out));

    BreakDiagnostics_RecordFromIsr(BREAK_DIAG_SOURCE_TIM8, &second);
    assert(BreakDiagnostics_Get(&out));
    assert(out.sequence == 2u);
    assert(out.source == BREAK_DIAG_SOURCE_TIM8);
    assert(out.tim8_sr == 0x80u);
    assert(out.sd1_high == 1u && out.sd2_high == 0u);

    assert(BreakDiagnostics_Reset(false));
    BreakDiagnostics_RecordFromIsr(BREAK_DIAG_SOURCE_NONE, &first);
    assert(!BreakDiagnostics_Get(&out));
    BreakDiagnostics_RecordFromIsr(BREAK_DIAG_SOURCE_TIM1, 0);
    assert(!BreakDiagnostics_Get(&out));
    return 0;
}
