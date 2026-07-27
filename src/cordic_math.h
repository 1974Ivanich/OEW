#ifndef CORDIC_MATH_H
#define CORDIC_MATH_H

#include <stdint.h>

void CORDIC_Init(void);
int32_t CORDIC_Sin(int32_t angle_q31);
int32_t CORDIC_Cos(int32_t angle_q31);
void CORDIC_SinCos(int32_t angle_q31, int32_t *sin_q15, int32_t *cos_q15);
int32_t CORDIC_Atan2(int32_t y, int32_t x);
void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle);

#endif
