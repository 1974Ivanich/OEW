#ifndef VF_CONTROL_H
#define VF_CONTROL_H

#include <stdbool.h>
#include <stdint.h>
#include "foc.h"  /* PIController, PI_Init, PI_Update, CLAMP */

/* V/f start gate and fail-closed reasons. */
#define VFC_START_OK                    0
#define VFC_START_ALREADY_RUNNING      -1
#define VFC_START_FOC_ACTIVE           -2
#define VFC_START_FAULT_LATCHED        -3
#define VFC_START_CONTEXT_UNVERIFIED   -4
#define VFC_START_SELECTOR_FAILED      -5
#define VFC_START_VECTOR_FAILED        -6
#define VFC_START_ADC_ARM_FAILED       -7
#define VFC_START_PWM_ENABLE_FAILED    -8

/* One measured reconstruction aperture for one of the six strict phase
 * orderings. modulation_* are timer compare counts (ARR is 999 in the
 * qualified 10 MHz / 5.01 kHz configuration). Timing values are timer ticks
 * at 10 MHz, where one tick is 0.1 us. */
typedef struct {
    bool measured;
    uint8_t window;
    uint16_t modulation_min;
    uint16_t modulation_max;
    uint32_t trgo_to_jeos_min_cycles;
    uint32_t trgo_to_jeos_max_cycles;
    uint32_t switching_margin_cycles;
} VfcApertureContractEntry;

extern const VfcApertureContractEntry VfcApertureContract[6];

typedef struct {
    int32_t  target_rpm;        /* target mechanical speed, rpm (signed) */
    int32_t  measured_rpm;      /* measured speed from encoder (signed) */
    int32_t  f_e_hz;            /* electrical stator frequency, Hz (signed) */
    int32_t  f_slip_hz;         /* slip frequency, Hz (signed, PI output) */
    int32_t  voltage_mag;       /* voltage amplitude, % of Vbus (0..95) */
    uint32_t theta_elec;        /* phase accumulator: q31, 0..2^32 = 0..2*pi */
    int      running;
    int32_t  v_boost_pct;       /* voltage boost, % (0..30) */
    int32_t  rated_freq_hz;     /* rated frequency, Hz (50) */
    int32_t  ramp_target_rpm;   /* ramp target */
    int32_t  ramp_current_rpm;  /* current ramp value */
    int32_t  ramp_time_ms;      /* ramp time, ms (2000) */
    uint32_t ramp_tick;         /* ramp tick counter (1 tick = 1 ms) */
    int32_t  ramp_rem;          /* remainder for integer ramp precision */
    int32_t  duty_u, duty_v, duty_w;  /* последний заданный duty, % */
    PIController speed_pi;      /* speed PI (Гц slip) */
    uint32_t start_ticks;       /* ticks since start for swing and watchdog */
    int32_t  swing_offset_q31;  /* start swing: ±30° el oscillation offset */
} VFCtrl;

void     VFC_Init(void);
int      VFC_Start(int32_t target_rpm);
void     VFC_Stop(void);
void     VFC_SetTarget(int32_t target_rpm);
void     VFC_Update(void);       /* TIM6 ISR (1 kHz) */
int      VFC_IsRunning(void);
int32_t  VFC_GetSpeed(void);
int32_t  VFC_GetTarget(void);
void     VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz);

extern VFCtrl vfc;

#endif
