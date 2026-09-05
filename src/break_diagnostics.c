#include "break_diagnostics.h"

#include "stm32g474xx.h"

static volatile BreakDiagnostics first_break;
static uint32_t next_sequence;

void BreakDiagnostics_RecordFromIsr(BreakDiagSource source,
                                    const BreakDiagnostics *snapshot)
{
    BreakDiagnostics pending;

    if (snapshot == 0 || source == BREAK_DIAG_SOURCE_NONE || first_break.valid) {
        return;
    }

    pending = *snapshot;
    pending.sequence = ++next_sequence;
    pending.source = (uint8_t)source;
    pending.valid = 0u;
    first_break = pending;
    __DMB();
    first_break.valid = 1u;
    __DMB();
}

bool BreakDiagnostics_Get(BreakDiagnostics *out)
{
    if (out == 0 || !first_break.valid) return false;
    __DMB();
    *out = first_break;
    return true;
}

bool BreakDiagnostics_Reset(bool pwm_enabled)
{
    uint32_t primask;

    if (pwm_enabled) return false;
    primask = __get_PRIMASK();
    __disable_irq();
    first_break = (BreakDiagnostics){0};
    __DMB();
    __set_PRIMASK(primask);
    return true;
}
