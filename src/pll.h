#ifndef PLL_H
#define PLL_H

#include <stdint.h>

/* Anti-windup limit for PLL integrator (Δθ q31 per cycle).
   0x01000000 ≈ 1/256 оборота/цикл → ~1950 эл. об/мин при Ts=200мкс. */
#define PLL_INTEGRATOR_MAX  0x01000000

typedef struct {
    int32_t kp, ki;
    uint32_t theta_u32;   /* угол q31 в uint32 — wrap-around определён стандартом */
    int32_t omega_q31;    /* скорость: Δθ (q31) за один FOC-цикл */
    int32_t integrator;
    int32_t ts_us;
} PLL;

void PLL_Init(PLL *pll, int32_t kp, int32_t ki, int32_t ts_us);
void PLL_Preset(PLL *pll, int32_t theta_q31, int32_t omega_q31);
void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta);
int32_t PLL_GetTheta(PLL *pll);
int32_t PLL_GetSpeed(PLL *pll);

#endif
