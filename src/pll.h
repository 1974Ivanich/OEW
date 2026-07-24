#ifndef PLL_H
#define PLL_H

#include <stdint.h>

typedef struct {
    int32_t kp, ki;
    int32_t theta_q31;
    int32_t speed_q15;
    int32_t integrator;
    int32_t ts_us;
} PLL;

void PLL_Init(PLL *pll, int32_t kp, int32_t ki, int32_t ts_us);
void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta);
int32_t PLL_GetTheta(PLL *pll);
int32_t PLL_GetSpeed(PLL *pll);

#endif
