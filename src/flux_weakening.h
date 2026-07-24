#ifndef FLUX_WEAKENING_H
#define FLUX_WEAKENING_H

#include <stdint.h>

typedef struct {
    int32_t vdc_mv;
    int32_t v_max_mv;
    int32_t v_threshold;
    int32_t id_fw_q15;
    int32_t iq_max_q15;
    int32_t active;
    int32_t kp, ki;
    int32_t integrator;
    int32_t out_max, out_min;
} FluxWeakening;

void FW_Init(FluxWeakening *fw, int32_t vdc_mv, int32_t kp, int32_t ki);
void FW_Update(FluxWeakening *fw, int32_t vd_q15, int32_t vq_q15);
int32_t FW_GetIdAdd(FluxWeakening *fw);
int32_t FW_GetIqLimit(FluxWeakening *fw);
int FW_IsActive(FluxWeakening *fw);

#endif
