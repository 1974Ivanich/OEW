#ifndef OBSERVER_H
#define OBSERVER_H

#include <stdint.h>

typedef struct {
    int32_t R_mOhm;
    int32_t L_uH;
    int32_t Ts_us;
    int32_t Vdc_mV;     /* базис напряжения: 32767 (Q15) = Vdc */
    int32_t emf_alpha, emf_beta;  /* Q15, 32768 = Vdc */
    int32_t prev_ia_ma, prev_ib_ma;  /* токи в мА для высокоразрешающей производной */
    uint8_t prev_valid; /* 0 после Init — первый вызов пропускает dI/dt */
} BEMFObserver;

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us, int32_t vdc_mv);
/* ia_ma, ib_ma — токи в мА (не mA/100). Vα/Vβ — Q15 (32768 = Vdc). */
void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia_ma, int32_t ib_ma);
int32_t BEMF_GetMagnitude(BEMFObserver *obs);  /* |EMF| — для проверки качества оценки */

#endif
