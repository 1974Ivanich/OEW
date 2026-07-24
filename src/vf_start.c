#include "vf_start.h"

void VF_Init(VFStart *vf, int32_t target_rpm, int32_t ramp_ms, int32_t vf_mv_hz) {
    vf->target_speed = target_rpm;
    vf->current_speed = 0;
    vf->ramp_time_ms = ramp_ms;
    vf->v_per_hz = vf_mv_hz;
    vf->boost_voltage = 500; // 0.5V boost
    vf->theta_q31 = 0;
    vf->iq_ref = 500; // малый Iq
    vf->id_ref = 2000; // Id намагничивания
    vf->tick_counter = 0;
    vf->complete = 0;
}

void VF_Update(VFStart *vf) {
    if(vf->complete) return;

    vf->tick_counter++;
    uint32_t elapsed_ms = vf->tick_counter / 5; // 200us * 5 = 1ms

    if(elapsed_ms < (uint32_t)vf->ramp_time_ms) {
        vf->current_speed = vf->target_speed * (int32_t)elapsed_ms / vf->ramp_time_ms;
    } else {
        vf->current_speed = vf->target_speed;
        vf->complete = 1;
    }

    /* Интегрирование угла: speed_rpm -> rad/s -> угол */
    int32_t speed_rad = vf->current_speed * 2 * 3.14159 / 60; // float, но для заглушки норм
    (void)speed_rad;
    vf->theta_q31 += (vf->current_speed * 200) / 60; // грубая аппроксимация
}

int VF_IsComplete(VFStart *vf) { return vf->complete; }
int32_t VF_GetTheta(VFStart *vf) { return vf->theta_q31; }
int32_t VF_GetSpeed(VFStart *vf) { return vf->current_speed; }
