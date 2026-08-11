#ifndef FOC_H
#define FOC_H

#include <stdint.h>

typedef struct { int32_t alpha, beta; } AlphaBeta;
typedef struct { int32_t d, q; } DQ;

AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw);
DQ Park_Transform(int32_t alpha, int32_t beta, int32_t theta_q31);
AlphaBeta InvPark_Transform(int32_t vd, int32_t vq, int32_t theta_q31);
void InvClarke_Transform(int32_t valpha, int32_t vbeta, int32_t *vu, int32_t *vv, int32_t *vw);

typedef struct {
    int32_t kp, ki;
    int32_t kw;
    int32_t integral;
    int32_t out_max, out_min;
} PIController;

void PI_Init(PIController *pi, int32_t kp, int32_t ki, int32_t max, int32_t min);
int32_t PI_Update(PIController *pi, int32_t error);
void PI_BackCalculation(PIController *pi, int32_t saturation_error);

#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif

#endif /* FOC_H */
