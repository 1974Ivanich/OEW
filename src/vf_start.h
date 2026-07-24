#ifndef VF_START_H
#define VF_START_H

#include <stdint.h>

typedef struct {
    int32_t target_speed;
    int32_t current_speed;
    int32_t ramp_time_ms;
    int32_t v_per_hz;
    int32_t boost_voltage;
    int32_t theta_q31;
    int32_t iq_ref;
    int32_t id_ref;
    uint32_t tick_counter;
    int complete;
} VFStart;

void VF_Init(VFStart *vf, int32_t target_rpm, int32_t ramp_ms, int32_t vf_mv_hz);
void VF_Update(VFStart *vf);
int VF_IsComplete(VFStart *vf);
int32_t VF_GetTheta(VFStart *vf);
int32_t VF_GetSpeed(VFStart *vf);

#endif
