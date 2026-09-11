#ifndef FOC_H
#define FOC_H

#include <stdint.h>
#include "adc.h"    /* AdcFrame — FOC_RunFrame(const AdcFrame*) */

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
#define FOC_EMF_MIN_THRESHOLD  100  /* мин. модуль EMF для перехода V/f→closed-loop.
                                       Единицы: Q15-модуль из BEMF_GetMagnitude(),
                                       диапазон 0..46340 (√(32768²+32768²)).
                                       100 ≈ ~0.3% шкалы — порог на «есть ли ЭДС»,
                                       не на величину (ревью Grok: документировать). */

/* Run FOC cycle */
void FOC_Init(void);
/* Ревью «два DC-link shunt + CT»: FOC_Run принимает свежий AdcFrame;
 * Current_Reconstruct() отдаёт фазные токи ТОЛЬКО из доказанных строк карты.
 * Legacy no-argument FOC_Run больше НЕ существует — вызовы из control-пути
 * (ADC1_2_IRQHandler) обязаны передавать фрейм. */
void FOC_RunFrame(const AdcFrame *frame);

/* FOC_Start возвращает код результата: PWM/EN включаются ТОЛЬКО при
 * загруженной карте реконструкции (иначе fail-closed). */
#define FOC_START_OK                  0
#define FOC_START_CLOCK_OR_FAULT     -1
#define FOC_START_MAP_UNVERIFIED     -2
#define FOC_START_CALIBRATION_FAILED -3
#define FOC_START_ADC_ARM_FAILED     -4
#define FOC_START_PWM_ENABLE_FAILED  -5
/* Ревью TZ-01: Rs/Ls вне окна AT_MATH_SANE_* — отдельный код, чтобы
 * оператор мог отличить причину отказа от fault/карты/контекста. */
#define FOC_START_PARAMS_OUT_OF_RANGE -6
int FOC_Start(void);
void FOC_Stop(void);
int  FOC_IsRunning(void);
void FOC_SetSpeed(int32_t rpm);
int32_t FOC_GetSpeed(void);        /* ЗАДАННАЯ скорость (reference), rpm.
                                      НЕ путать с FOC_GetMeasSpeedRPM() (feedback) —
                                      ревью Grok foc.h п.5. */
void FOC_SetIdRef(int32_t ma);
void FOC_SetIqRef(int32_t ma);       /* 0 = контур скорости, иначе ручное задание Iq */
int32_t FOC_GetMeasSpeedRPM(void);   /* измеренная механическая скорость, об/мин */
int32_t FOC_GetThetaMilliRad(void);  /* эл. угол, миллирадианы 0..6283 */
int  FOC_SetPolePairs(int32_t pp);   /* 0 = OK, -1 = ошибка (FOC запущен / вне 1..24) */
int32_t FOC_GetPolePairs(void);
int  FOC_SetBaseSpeed(int32_t rpm);  /* FW-01: базовая скорость ослабления поля (100..5000) */
int32_t FOC_GetBaseSpeed(void);
int32_t FOC_GetMaxSpeedRPM(void);  /* FOC-04: лимит мех. скорости из f_e=200Гц/pole_pairs */
uint8_t FOC_GetState(void);  /* 0=startup, 1=run */

/* Ревью VFS-02/04: причина неудачного I-f → RUN handoff (0 = OK). */
typedef enum {
    FOC_STARTUP_OK = 0,
    FOC_STARTUP_FAIL_NO_ROTATION,     /* encoder ниже FOC_ENC_MIN_RPM — мотор не крутится */
    FOC_STARTUP_FAIL_ENC_DIRECTION,   /* направление V/f vs encoder не совпало */
    FOC_STARTUP_FAIL_EMF_INVALID,     /* EMF < порога / observer невалиден (glitch/sat) */
    FOC_STARTUP_FAIL_SPEED_MISMATCH,  /* |Vf−enc| > Vf/3 */
    FOC_STARTUP_FAIL_UNSTABLE,        /* jerk ≥ 200 rpm или недостаточный |Id| */
    FOC_STARTUP_FAIL_TIMEOUT,         /* handoff не состоялся за 5 с после рампы */
    FOC_STARTUP_FAIL_ENCODER_LOST     /* P1: encoder error/dead speed persisted in RUN */
} FOCStartupFail;
int FOC_GetStartupFailReason(void);

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
int FOC_SetVdcMv(int32_t mv);   /* номинал шины для PI-расчётов, мВ (10..400 В) */
int32_t FOC_GetVdcMv(void);
void FOC_GetMotorParams(int32_t *r_mohm, int32_t *l_uh, int32_t *kp, int32_t *ki);

#endif
