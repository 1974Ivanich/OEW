#ifndef CORDIC_MATH_H
#define CORDIC_MATH_H

#include <stdint.h>

/*
 * CORDIC Q-format conventions (RM0440, AN5325):
 *
 * angle_q31:  angle / PI в Q1.31
 *   0x00000000 = 0°
 *   0x40000000 = +90°  (+PI/2)
 *   0x7FFFFFFF ≈ +180° (+PI)
 *   0x80000000 = -180° (-PI)
 *   Phase accumulator (uint32, 2^32 = 2π) совместим по модулю 2π.
 *
 * sin/cos:    Q1.15 [-32768..32767] ↔ [-1.0..+1.0)
 *
 * Atan2:      возвращает angle/PI в Q1.31
 *
 * Modulus:    Q1.31, входы нормализуются внутри функции.
 *             |v| > 1.0 обрабатывается автоматически.
 *
 * Sqrt:       Q1.31, вход ∈ [0, 1), выход = sqrt(x) в Q1.31.
 */

/* Sin/Cos возвращают Q15 [-32768..32767] в int32_t.
 * SinCos записывает Q15 в *sin_q15, *cos_q15. */
void CORDIC_Init(void);
int32_t CORDIC_Sin(int32_t angle_q31);       /* returns Q15 */
int32_t CORDIC_Cos(int32_t angle_q31);       /* returns Q15 */
void CORDIC_SinCos(int32_t angle_q31, int32_t *sin_q15, int32_t *cos_q15);
int32_t CORDIC_Atan2(int32_t y, int32_t x);
void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle);
int32_t CORDIC_Sqrt(int32_t x_q31);

#endif
