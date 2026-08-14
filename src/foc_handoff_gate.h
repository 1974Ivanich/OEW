#ifndef FOC_HANDOFF_GATE_H
#define FOC_HANDOFF_GATE_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    FOC_HANDOFF_PENDING = 0,
    FOC_HANDOFF_READY,
    FOC_HANDOFF_TIMEOUT
} FocHandoffResult;

typedef struct {
    int32_t emf_min;
    int32_t enc_min_rpm;
    int32_t max_jerk_rpm_per_cycle;
    int32_t min_id_internal;
    uint16_t required_consecutive;
    uint32_t max_startup_cycles;
    uint32_t max_handoff_cycles;
} FocHandoffConfig;

typedef struct {
    bool vf_complete;
    int32_t vf_erpm;
    int32_t encoder_raw_rpm;
    int32_t encoder_filtered_rpm;
    int32_t pole_pairs;
    int32_t emf_magnitude;
    int32_t encoder_jerk_rpm;
    int32_t previous_id_internal;
} FocHandoffInput;

typedef struct {
    uint32_t startup_cycles;
    uint32_t handoff_cycles;
    uint16_t consecutive_good;
    bool latched_ready;
    bool latched_timeout;
} FocHandoffGate;

void FocHandoffGate_Init(FocHandoffGate *gate);
FocHandoffResult FocHandoffGate_Update(FocHandoffGate *gate,
                                        const FocHandoffConfig *cfg,
                                        const FocHandoffInput *in);

#endif /* FOC_HANDOFF_GATE_H */
