#ifndef AUTOTUNE_H
#define AUTOTUNE_H

#include <stdint.h>

#define AUTOTUNE_MAX_CURVE_POINTS  50
#define AUTOTUNE_MAX_CURRENT_MA    8000

typedef struct {
    int32_t current_ma;
    int32_t inductance_uh;
} AutotuneSatPoint;

typedef struct {
    int32_t Rs_mOhm;
    int32_t Ls_uH;
    int32_t Isat_ma;
    int32_t Rr_mOhm;
    int32_t Lm_uH;
    int32_t Tr_us;
    int32_t Ke_mV_rpm;
    uint8_t pole_pairs;
    int32_t J_kg_m2_x1e6;
    AutotuneSatPoint curve[AUTOTUNE_MAX_CURVE_POINTS];
    uint8_t curve_count;
} MotorParams;

extern MotorParams g_motor_params;

void    Autotune_Init(void);
int8_t  Autotune_Idle(void);
int8_t  Autotune_Irot(void);
int8_t  Autotune_Inertia(void);
void    Autotune_PrintParams(void);
void    Autotune_PrintCurve(void);

#endif
