#include "observer.h"
#include "cordic_math.h"

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us, int32_t vdc_mv) {
    obs->R_mOhm = r_mohm;
    obs->L_uH = l_uh;
    obs->Ts_us = ts_us;
    obs->Vdc_mV = vdc_mv;
    obs->emf_alpha = 0;
    obs->emf_beta = 0;
    obs->prev_ia = 0;
    obs->prev_ib = 0;
}

void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia, int32_t ib) {
    /* Eα = Vα - R*Iα - L*dIα/dt — все слагаемые в едином базисе Q15,
     * где 32768 = Vdc_mV.
     *
     * Единицы тока: 1 код ia = 100 мА = 0.1 А (см. foc.c: i_ma/100).
     *
     * R*I:  U_мВ = R_мОм · I_А = R_mOhm · ia / 10
     *       Q15:  r_ia = R_mOhm · ia · 32768 / (10 · Vdc_mV)
     *       Пример: R=50мОм, ia=260 (26А), Vdc=24В → 1774 (1.3В в Q15). ✓
     *
     * L*dI/dt: U_мВ = L_мкГн · dI_А / Ts_мкс · 1000 = L_uH · dia · 100 / Ts_us
     *       Q15:  l_dia = L_uH · dia · 100 · 32768 / (Ts_us · Vdc_mV)
     *       Пример: L=100мкГн, dia=1 (0.1А), Ts=200мкс → 50мВ → 68 в Q15. ✓
     *
     * int64 — промежуточные произведения превышают int32. */
    int32_t dia = ia - obs->prev_ia;
    int32_t dib = ib - obs->prev_ib;

    /* Защита от деления на ноль при обрыве питания */
    if(obs->Vdc_mV < 1000) return;

    int32_t r_ia = (int32_t)(((int64_t)obs->R_mOhm * ia * 32768) / (10 * (int64_t)obs->Vdc_mV));
    int32_t r_ib = (int32_t)(((int64_t)obs->R_mOhm * ib * 32768) / (10 * (int64_t)obs->Vdc_mV));

    int32_t l_dia = (int32_t)(((int64_t)obs->L_uH * dia * 100 * 32768) / ((int64_t)obs->Ts_us * obs->Vdc_mV));
    int32_t l_dib = (int32_t)(((int64_t)obs->L_uH * dib * 100 * 32768) / ((int64_t)obs->Ts_us * obs->Vdc_mV));

    obs->emf_alpha = valpha - r_ia - l_dia;
    obs->emf_beta  = vbeta  - r_ib - l_dib;

    obs->prev_ia = ia;
    obs->prev_ib = ib;
}

int32_t BEMF_GetMagnitude(BEMFObserver *obs) {
    /* Точная евклидова норма через CORDIC (q1.31 → Q15). */
    int32_t mod = 0, ang = 0;
    CORDIC_Modulus(obs->emf_alpha << 16, obs->emf_beta << 16, &mod, &ang);
    if(mod == 0) {
        /* Fallback: манхэттенская норма */
        int32_t a = obs->emf_alpha; if(a < 0) a = -a;
        int32_t b = obs->emf_beta;  if(b < 0) b = -b;
        return a + b;
    }
    return mod >> 16;
}
