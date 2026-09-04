#ifndef BREAK_DIAGNOSTICS_H
#define BREAK_DIAGNOSTICS_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    BREAK_DIAG_SOURCE_NONE = 0,
    BREAK_DIAG_SOURCE_TIM1 = 1,
    BREAK_DIAG_SOURCE_TIM8 = 2
} BreakDiagSource;

typedef struct {
    uint32_t sequence;
    uint32_t timestamp_cycles;
    uint32_t tim1_sr;
    uint32_t tim8_sr;
    uint32_t tim1_bdtr;
    uint32_t tim8_bdtr;
    uint32_t tim1_ccer;
    uint32_t tim8_ccer;
    uint16_t tim1_cnt;
    uint16_t tim8_cnt;
    uint32_t capture_id;
    uint16_t capture_frames;
    uint8_t source;
    uint8_t sd1_high;
    uint8_t sd2_high;
    uint8_t capture_state;
    uint8_t valid;
} BreakDiagnostics;

void BreakDiagnostics_RecordFromIsr(BreakDiagSource source,
                                    const BreakDiagnostics *snapshot);
bool BreakDiagnostics_Get(BreakDiagnostics *out);
bool BreakDiagnostics_Reset(bool pwm_enabled);

#endif /* BREAK_DIAGNOSTICS_H */
