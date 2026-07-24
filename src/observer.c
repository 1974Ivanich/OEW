#include "observer.h"

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us) {
    obs->R_mOhm = r_mohm;
    obs->L_uH = l_uh;
    obs->Ts_us = ts_us;
    obs->emf_alpha = 0;
    obs->emf_beta = 0;
    obs->prev_ia = 0;
    obs->prev_ib = 0;
}

void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia, int32_t ib) {
    /* Eα = Vα - R*Iα - L*(Iα - Iα_prev)/Ts */
    int32_t dia = ia - obs->prev_ia;
    int32_t dib = ib - obs->prev_ib;

    /* R*Iα в Q15: R(мОм) * I(коды АЦП) / масштаб */
    int32_t r_ia = (obs->R_mOhm * ia) >> 12;  // грубая аппроксимация
    int32_t r_ib = (obs->R_mOhm * ib) >> 12;

    /* L*dI/dt в Q15: L(мкГн) * dI(коды) / Ts(мкс) */
    int32_t l_dia = (obs->L_uH * dia * 1000) / obs->Ts_us >> 12;
    int32_t l_dib = (obs->L_uH * dib * 1000) / obs->Ts_us >> 12;

    obs->emf_alpha = valpha - r_ia - l_dia;
    obs->emf_beta  = vbeta  - r_ib - l_dib;

    obs->prev_ia = ia;
    obs->prev_ib = ib;
}
