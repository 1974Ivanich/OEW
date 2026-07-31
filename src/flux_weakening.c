#include "flux_weakening.h"
#include "cordic_math.h"

#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif

void FW_Init(FluxWeakening *fw, int32_t vdc_mv, int32_t kp, int32_t ki) {
    fw->vdc_mv = vdc_mv;
    fw->v_max_mv = vdc_mv * 577 / 1000; // 0.577*Vdc для SVM
    fw->v_threshold = fw->v_max_mv * 95 / 100; // 95%
    fw->kp = kp; fw->ki = ki;
    fw->integrator = 0;
    fw->id_fw_q15 = 0;
    fw->iq_max_q15 = 32767; // макс
    fw->active = 0;
    fw->out_max = 0;
    fw->out_min = -32768;
}

void FW_Update(FluxWeakening *fw, int32_t vd_q15, int32_t vq_q15) {
    int32_t mod, angle;
    /* vd/vq в Q15 (±32767) → q1.31 для CORDIC (сдвиг на 16) */
    CORDIC_Modulus(vd_q15 << 16, vq_q15 << 16, &mod, &angle);
    (void)angle;

    /* mod в q1.31 → Q15, затем в мВ: mod_q15 / 32768 * Vdc */
    uint32_t v_out_mv = (uint32_t)(mod >> 16) * (uint32_t)fw->vdc_mv / 32768u;

    if(v_out_mv > (uint32_t)fw->v_threshold) {
        fw->active = 1;
        int32_t err = (int32_t)(fw->v_max_mv - (int32_t)v_out_mv);
        fw->integrator += (fw->ki * err) >> 15;
        fw->integrator = CLAMP(fw->integrator, fw->out_min, fw->out_max);
        fw->id_fw_q15 = ((fw->kp * err) >> 15) + fw->integrator;
        fw->id_fw_q15 = CLAMP(fw->id_fw_q15, fw->out_min, fw->out_max);
    } else if(v_out_mv < (uint32_t)(fw->v_threshold * 90 / 100)) {
        fw->active = 0;
        fw->id_fw_q15 = 0;
        fw->integrator = 0;
    }
}

int32_t FW_GetIdAdd(FluxWeakening *fw) { return fw->id_fw_q15; }
int32_t FW_GetIqLimit(FluxWeakening *fw) { return fw->iq_max_q15; }
int FW_IsActive(FluxWeakening *fw) { return fw->active; }
