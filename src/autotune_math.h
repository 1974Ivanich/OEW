#ifndef AUTOTUNE_MATH_H
#define AUTOTUNE_MATH_H

#include <stdint.h>
#include "autotune.h"

#define AT_MATH_TWO_PI_X1000 6283
#define AT_MATH_PI_MRAD 3142
#define AT_MATH_PI_2_MRAD 1571
#define AT_MATH_TWO_PI_3_MRAD 2094
#define AT_MATH_SANE_LS_MIN_UH 500
#define AT_MATH_SANE_LS_MAX_UH 500000
#define AT_MATH_SANE_RS_MIN_MOHM 10
#define AT_MATH_SANE_RS_MAX_MOHM 100000

int32_t AT_MathSinQ15(int32_t angle_x1000);
int32_t AT_MathAbs32(int32_t value);
int32_t AT_MathMedianSmall(int32_t *values, uint8_t count);
void AT_MathCurveSortByCurrent(AtCurvePoint *curve, uint8_t count);
uint8_t AT_MathCurveFilterOutliers(AtCurvePoint *curve, uint8_t count);
int32_t AT_MathCalcIsat(const AtCurvePoint *curve, uint8_t count,
                       int32_t l0_uH, int32_t threshold_pct);
int32_t AT_SaneLs(int32_t l_uh);
int32_t AT_SaneRs(int32_t r_mohm);

#endif
