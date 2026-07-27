#ifndef OBSERVER_H
#define OBSERVER_H

#include <stdint.h>

typedef struct {
    int32_t R_mOhm;
    int32_t L_uH;
    int32_t Ts_us;
    int32_t Vdc_mV;     /* базис напряжения: 32767 (Q15) = Vdc */
    int32_t emf_alpha, emf_beta;
    int32_t prev_ia, prev_ib;
} BEMFObserver;

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us, int32_t vdc_mv);
void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia, int32_t ib);
int32_t BEMF_GetMagnitude(BEMFObserver *obs);  /* |EMF| — для проверки качества оценки */

#endif
