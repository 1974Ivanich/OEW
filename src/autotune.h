#ifndef AUTOTUNE_H
#define AUTOTUNE_H

#include <stdint.h>

#define AUTOTUNE_MAX_CURRENT_MA      8000
#define AUTOTUNE_MAX_CURVE_POINTS    64
#define AUTOTUNE_MAX_REPEATS         5
#define AUTOTUNE_NUM_PAIRS           3

#define AT_DI_TARGET_MIN_MA          100
#define AT_DI_TARGET_MAX_MA          500
#define AT_ASYMMETRY_WARN_PCT        10
#define AT_SPREAD_WARN_PCT           15

typedef enum {
    AT_CH_UNKNOWN = 0,
    AT_CH_I1,
    AT_CH_I2,
    AT_CH_IN
} AtCurrentChannel;

typedef struct {
    int32_t current_ma;
    int32_t inductance_uH;
} AtCurvePoint;

typedef struct {
    int32_t median;
    int32_t min;
    int32_t max;
    int32_t spread_pct;
    int32_t values[5];
    uint8_t count;
} AtStat32;

typedef struct {
    int32_t Rs_mOhm;
    int32_t Ls_uH;
    int32_t Isat_ma;
    uint8_t valid;
} AtPairResult;

typedef struct {
    int32_t Rs_mOhm;
    int32_t Ls_uH;
    int32_t Isat_ma;
    AtStat32 Rs_stat;
    AtStat32 Ls_stat;
    AtStat32 Isat_stat;
    AtPairResult pairs[3];
    AtCurvePoint curve[64];
    uint8_t    curve_count;
    AtCurrentChannel current_channel;
    int32_t          current_sign;
    int32_t Rr_mOhm;
    int32_t Lm_uH;
    int32_t Tr_us;
    int32_t Ke_mV_rpm;
    int32_t pole_pairs;
    int32_t J_kg_m2_x1e6;
} MotorParams;

extern MotorParams g_motor_params;
extern volatile uint8_t g_autotune_abort;

void    Autotune_Init(void);
int8_t  Autotune_Idle(void);
int8_t  Autotune_MeasureRs_IV(void);
int8_t  Autotune_DetectChannel(void);
int8_t  Autotune_MeasureAllPairs(void);
int8_t  Autotune_Irot(void);
int8_t  Autotune_Inertia(void);
void    Autotune_PrintParams(void);
void    Autotune_PrintCurve(void);
void    Autotune_PrintPairs(void);
void    Autotune_PrintStats(void);
int8_t  Autotune_MeasureLs_OEW(void);
int8_t  Autotune_MeasureRr(void);
int8_t  Autotune_MeasureNoLoad(void);
int8_t  Autotune_Scope(void);
void    Autotune_CalcPI(int32_t bandwidth_hz);
int     Autotune_GetLastPI(int32_t *kp, int32_t *ki);
int8_t  Autotune_MeasureLs_Position(void);


#endif
