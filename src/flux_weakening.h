#ifndef FLUX_WEAKENING_H
#define FLUX_WEAKENING_H

#include <stdint.h>

typedef struct {
    int32_t v_max_q15;       /* Vmax в Q15 — от VoltageManager, единый источник */
    int32_t id_fw_q15;       /* добавка ослабления поля, мА (1 код = 1 мА) */
    int32_t iq_max_q15;      /* остаток напряжения q = sqrt(Vmax²−Vd²), Q15 — диагностика */
    int32_t active;
    int32_t kp, ki;
    int32_t integrator;
    int32_t out_max, out_min;
    int32_t recovery_step;   /* плавное разматывание: кодов/цикл (FW-05) */
    int32_t base_speed_rpm;  /* speed gate (FW-01): FW активен выше этой скорости */
    int32_t speed_gate;      /* 1 = скорость выше базы (с гистерезисом 5%) */
} FluxWeakening;

void FW_Init(FluxWeakening *fw, int32_t kp, int32_t ki);
void FW_SetVmaxQ15(FluxWeakening *fw, int32_t v_max_q15);  /* от VM — единый источник */
void FW_SetBaseSpeedRpm(FluxWeakening *fw, int32_t rpm);   /* speed gate, из GUI (fwbase=N) */
void FW_Update(FluxWeakening *fw, int32_t vd_q15, int32_t vq_q15,
               int32_t limit_scale_q15, int32_t id_base_q15, int32_t speed_rpm);
int32_t FW_GetIdAdd(FluxWeakening *fw);
int32_t FW_GetIqLimit(FluxWeakening *fw);
int FW_IsActive(FluxWeakening *fw);

#endif
