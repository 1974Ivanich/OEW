#ifndef FOC_H
#define FOC_H

#include <stdint.h>

typedef struct { int32_t alpha, beta; } AlphaBeta;
typedef struct { int32_t d, q; } DQ;

AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw);
DQ Park_Transform(int32_t alpha, int32_t beta, int32_t theta_q31);
AlphaBeta InvPark_Transform(int32_t vd, int32_t vq, int32_t theta_q31);
void InvClarke_Transform(int32_t valpha, int32_t vbeta, int32_t *vu, int32_t *vv, int32_t *vw);

/* PI controller */
typedef struct {
    int32_t kp, ki;
    int32_t kw;        /* anti-windup coefficient, Q15: 32768=1.0, 16384=0.5, 0=disabled */
    int32_t integral;
    int32_t out_max, out_min;
} PIController;

void PI_Init(PIController *pi, int32_t kp, int32_t ki, int32_t max, int32_t min);
int32_t PI_Update(PIController *pi, int32_t error);
void PI_BackCalculation(PIController *pi, int32_t saturation_error);

/* Минимальный модуль EMF для перехода V/f → closed-loop (в ед. observer) */
#define FOC_EMF_MIN_THRESHOLD  100

/* Run FOC cycle */
void FOC_Init(void);
void FOC_Run(void);
void FOC_Start(void);
void FOC_Stop(void);
int  FOC_IsRunning(void);
void FOC_SetSpeed(int32_t rpm);
int32_t FOC_GetSpeed(void);
void FOC_SetIdRef(int32_t ma);
void FOC_SetIqRef(int32_t ma);       /* 0 = контур скорости, иначе ручное задание Iq */
int32_t FOC_GetMeasSpeedRPM(void);   /* измеренная механическая скорость, об/мин */
int32_t FOC_GetThetaMilliRad(void);  /* эл. угол, миллирадианы 0..6283 */
int  FOC_SetPolePairs(int32_t pp);   /* 0 = OK, -1 = ошибка (FOC запущен / вне 1..24) */
int32_t FOC_GetPolePairs(void);
uint8_t FOC_GetState(void);  /* 0=startup, 1=run */

#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif

/* ── Применение параметров автотюнинга (tz_foc_params) ─────────────── */
int FOC_SetMotorParams(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv);
int FOC_SetPIGains(int32_t kp, int32_t ki);
int FOC_IsParamsApplied(void);
void FOC_ComputePIGains(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv,
                        int32_t *kp_out, int32_t *ki_out);
void FOC_ComputePIGainsBW(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv,
                          int32_t bw_hz, int32_t *kp_out, int32_t *ki_out);
int32_t FOC_GetSigmaL_uH(void);
void FOC_GetMotorParams(int32_t *r_mohm, int32_t *l_uh, int32_t *kp, int32_t *ki);

#endif
