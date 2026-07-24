#include "pll.h"
#include "cordic_math.h"

void PLL_Init(PLL *pll, int32_t kp, int32_t ki, int32_t ts_us) {
    pll->kp = kp;
    pll->ki = ki;
    pll->ts_us = ts_us;
    pll->theta_q31 = 0;
    pll->speed_q15 = 0;
    pll->integrator = 0;
}

void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta) {
    /* Нормализуем EMF */
    int32_t mod, angle;
    CORDIC_Modulus(emf_alpha, emf_beta, &mod, &angle);
    if(mod == 0) return;

    int32_t e_norm_a = (emf_alpha << 15) / mod;
    int32_t e_norm_b = (emf_beta  << 15) / mod;

    /* Ошибка PLL: err = -Eα*sin(θ) + Eβ*cos(θ) */
    int32_t s = CORDIC_Sin(pll->theta_q31);
    int32_t c = CORDIC_Cos(pll->theta_q31);
    int32_t err = (-e_norm_a * s + e_norm_b * c) >> 15;

    /* PI */
    pll->integrator += (pll->ki * err * pll->ts_us) >> 20;
    int32_t omega = ((pll->kp * err) >> 15) + (pll->integrator >> 10);
    pll->speed_q15 = omega;

    /* Интегрирование угла */
    pll->theta_q31 += (omega * pll->ts_us) >> 10;
}

int32_t PLL_GetTheta(PLL *pll) { return pll->theta_q31; }
int32_t PLL_GetSpeed(PLL *pll) { return pll->speed_q15; }
