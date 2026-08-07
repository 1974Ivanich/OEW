#include "flux_weakening.h"
#include "cordic_math.h"

#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif

void FW_Init(FluxWeakening *fw, int32_t vdc_mv, int32_t kp, int32_t ki) {
    fw->vdc_mv = vdc_mv;
    fw->v_max_mv = vdc_mv * 577 / 1000; // 0.577*Vdc для SVM
    fw->v_threshold = fw->v_max_mv * 95 / 100; // 95%
    fw->v_max_q15 = 0;  /* будет установлен через FW_SetVmaxQ15 от VM */
    fw->kp = kp; fw->ki = ki;
    fw->integrator = 0;
    fw->id_fw_q15 = 0;
    fw->iq_max_q15 = 32767; // макс
    fw->active = 0;
    fw->out_max = 0;
    fw->out_min = -32768;
}

void FW_SetVmaxQ15(FluxWeakening *fw, int32_t v_max_q15) {
    fw->v_max_q15 = v_max_q15;
}

void FW_Update(FluxWeakening *fw, int32_t vd_q15, int32_t vq_q15, int32_t limit_scale_q15) {
    /* limit_scale_q15: 32767 = нет насыщения, <32767 = степень ограничения.
     * Используем как основной сигнал для FW: чем глубже насыщение,
     * тем сильнее ослабляем поле (отрицательный Id). */
    if (limit_scale_q15 >= 32767) {
        /* Нет насыщения — плавный сброс FW */
        fw->active = 0;
        fw->id_fw_q15 = 0;
        fw->integrator = 0;
        fw->iq_max_q15 = 32767;
        return;
    }

    fw->active = 1;
    /* err < 0: нужно увеличить ослабление поля.
     * Пропорционально степени насыщения + интегратор для устранения ошибки. */
    int32_t err = limit_scale_q15 - 32767;  /* отрицательное при насыщении */
    fw->integrator += (fw->ki * err) >> 15;
    fw->integrator = CLAMP(fw->integrator, fw->out_min, fw->out_max);
    fw->id_fw_q15 = ((fw->kp * err) >> 15) + fw->integrator;
    fw->id_fw_q15 = CLAMP(fw->id_fw_q15, fw->out_min, fw->out_max);

    /* Iq limit: available voltage for torque = sqrt(Vmax^2 - Vd_total^2),
     * где Vd_total = vd_q15 + id_fw (напряжение, затраченное на поток).
     * В Q15: vmax2 = Vmax^2>>15, vd2 = Vd^2>>15, iq_max = sqrt(vmax2-vd2). */
    int32_t vmax = fw->v_max_q15;
    if (vmax <= 0) {
        fw->iq_max_q15 = 0;
        return;
    }
    int32_t vd_total = vd_q15 + fw->id_fw_q15;
    if (vd_total < 0) vd_total = -vd_total;
    int32_t vmax2 = (int32_t)(((int64_t)vmax * vmax) >> 15);
    int32_t vd2 = (int32_t)(((int64_t)vd_total * vd_total) >> 15);
    if (vd2 >= vmax2) {
        fw->iq_max_q15 = 0;
    } else {
        /* sqrt_q15 inline (CORDIC_Sqrt expects q1.31) */
        int32_t diff = vmax2 - vd2;
        if (diff > 32767) diff = 32767;
        int32_t x_q31 = (int32_t)((int64_t)diff * 65536);
        int32_t sqrt_q31 = CORDIC_Sqrt(x_q31);
        fw->iq_max_q15 = (int32_t)(sqrt_q31 >> 16);
    }
}

int32_t FW_GetIdAdd(FluxWeakening *fw) { return fw->id_fw_q15; }
int32_t FW_GetIqLimit(FluxWeakening *fw) { return fw->iq_max_q15; }
int FW_IsActive(FluxWeakening *fw) { return fw->active; }
