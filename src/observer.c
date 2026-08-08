#include "observer.h"
#include <stdint.h>

static inline int32_t clamp_q15(int64_t x) {
    if(x > 32767) return 32767;
    if(x < -32768) return -32768;
    return (int32_t)x;
}

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us, int32_t vdc_mv) {
    obs->R_mOhm = r_mohm;
    obs->L_uH = l_uh;
    obs->Ts_us = ts_us;
    obs->Vdc_mV = vdc_mv;
    obs->emf_alpha = 0;
    obs->emf_beta = 0;
    obs->prev_ia_ma = 0;
    obs->prev_ib_ma = 0;
    obs->prev_valid = 0;
}

void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia_ma, int32_t ib_ma) {
    /* Eα = Vα - R*Iα - L*dIα/dt — все слагаемые в едином базисе Q15,
     * где 32768 = Vdc_mV.
     *
     * Единицы тока: ia_ma в мА (не mA/100) — разрешение производной 100× выше.
     *
     * R*I:  U_мВ = R_мОм · I_А = R_mOhm · ia_ma / 1000
     *       Q15:  r_ia = R_mOhm · ia_ma · 32768 / (1000 · Vdc_mV)
     *       Пример: R=50мОм, ia=26000мА (26А), Vdc=24В → 1774 (1.3В в Q15). ✓
     *
     * L*dI/dt: U_мВ = L_мкГн · dI_А / Ts_мкс · 1000 = L_uH · dia_ma / Ts_us
     *       Q15:  l_dia = L_uH · dia_ma · 32768 / (Ts_us · Vdc_mV)
     *       Пример: L=100мкГн, dia=100мА (0.1А), Ts=200мкс → 50мВ → 68 в Q15. ✓
     *       Раньше dia=1 (mA/100) → теперь dia=100 (mA) — 100× точнее. */

    /* Первый вызов после Init: prev=0 → dia=ia_ma даёт огромный импульс.
     * Пропускаем, запоминаем ток. */
    if(!obs->prev_valid) {
        obs->prev_ia_ma = ia_ma;
        obs->prev_ib_ma = ib_ma;
        obs->emf_alpha = 0;
        obs->emf_beta = 0;
        obs->prev_valid = 1;
        return;
    }

    int32_t dia_ma = ia_ma - obs->prev_ia_ma;
    int32_t dib_ma = ib_ma - obs->prev_ib_ma;

    /* Защита от деления на ноль при обрыве питания */
    if(obs->Vdc_mV < 1000) return;

    int32_t r_ia = (int32_t)(((int64_t)obs->R_mOhm * ia_ma * 32768) / (1000 * (int64_t)obs->Vdc_mV));
    int32_t r_ib = (int32_t)(((int64_t)obs->R_mOhm * ib_ma * 32768) / (1000 * (int64_t)obs->Vdc_mV));

    int32_t l_dia = (int32_t)(((int64_t)obs->L_uH * dia_ma * 32768) / ((int64_t)obs->Ts_us * obs->Vdc_mV));
    int32_t l_dib = (int32_t)(((int64_t)obs->L_uH * dib_ma * 32768) / ((int64_t)obs->Ts_us * obs->Vdc_mV));

    /* Clamp EMF to Q15 range — downstream (PLL) expects ±32768 */
    obs->emf_alpha = clamp_q15((int64_t)valpha - r_ia - l_dia);
    obs->emf_beta  = clamp_q15((int64_t)vbeta  - r_ib - l_dib);

    obs->prev_ia_ma = ia_ma;
    obs->prev_ib_ma = ib_ma;
}

int32_t BEMF_GetMagnitude(BEMFObserver *obs) {
    /* Целочисленный sqrt из |EMF|² — без CORDIC, без UB, без насыщения.
     * emf_alpha/beta в Q15 (32768 = Vdc).
     * magnitude = sqrt(α² + β²) — результат в Q15.
     * 32767² + 32767² ≈ 2.147e9 — помещается в uint64_t с огромным запасом. */
    int64_t a = obs->emf_alpha;
    int64_t b = obs->emf_beta;
    uint64_t sum = (uint64_t)(a * a) + (uint64_t)(b * b);
    if(sum == 0) return 0;

    /* Алгоритм целочисленного sqrt (bit-by-bit) */
    uint64_t x = sum;
    uint64_t r = 0;
    uint64_t bit = 1ULL << 62;
    while(bit > x) bit >>= 2;
    while(bit != 0) {
        if(x >= r + bit) {
            x -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    if(r > INT32_MAX) return INT32_MAX;
    return (int32_t)r;
}
