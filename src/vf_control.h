#ifndef VF_CONTROL_H
#define VF_CONTROL_H

#include <stdint.h>
#include "foc.h"  /* PIController, PI_Init, PI_Update, CLAMP */

/* V/f start gate: V/f deliberately refuses to run until it owns a verified
 * OEW sector/window selector for every next PWM/TRGO state (sample-context
 * contract). CONTEXT_UNVERIFIED is returned instead of bypassing PWM. */
#define VFC_START_OK                   0
#define VFC_START_ALREADY_RUNNING     -1
#define VFC_START_FOC_ACTIVE          -2
#define VFC_START_FAULT_LATCHED       -3
#define VFC_START_CONTEXT_UNVERIFIED  -4

typedef struct {
    int32_t  target_rpm;        /* target mechanical speed, rpm (signed) */
    int32_t  measured_rpm;      /* measured speed from encoder (signed) */
    int32_t  f_e_hz;            /* electrical stator frequency, Hz (signed) */
    int32_t  f_slip_hz;         /* slip frequency, Hz (signed, PI output) */
    int32_t  voltage_mag;       /* voltage amplitude, % of Vbus (0..95) */
    uint32_t theta_elec;        /* phase accumulator: q31, 0..2^32 = 0..2*pi */
    int      running;
    /* V/f parameters */
    int32_t  v_boost_pct;       /* voltage boost, % (0..30) */
    int32_t  rated_freq_hz;     /* rated frequency, Hz (50) */
    int32_t  ramp_target_rpm;   /* ramp target */
    int32_t  ramp_current_rpm;  /* current ramp value */
    int32_t  ramp_time_ms;      /* ramp time, ms (2000) */
    uint32_t ramp_tick;         /* ramp tick counter (1 tick = 1 ms) */
    int32_t  ramp_rem;          /* remainder for integer ramp precision */
    int32_t  duty_u, duty_v, duty_w;  /* последний заданный duty, % (для телеметрии) */
    PIController speed_pi;      /* VF-01: speed PI (Гц slip), общий Q15-PI */
} VFCtrl;

void     VFC_Init(void);
int      VFC_Start(int32_t target_rpm);   /* VFC_START_* gate; CONTEXT_UNVERIFIED пока нет карты */
void     VFC_Stop(void);
void     VFC_SetTarget(int32_t target_rpm);
void     VFC_Update(void);       /* TIM6 ISR (1 kHz) */
int      VFC_IsRunning(void);
int32_t  VFC_GetSpeed(void);     /* measured_rpm */
int32_t  VFC_GetTarget(void);   /* target_rpm */
void     VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz);

extern VFCtrl vfc;

#endif
