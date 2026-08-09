#include "vf_control.h"
#include "encoder.h"
#include "cordic_math.h"
#include "pwm.h"
#include "adc.h"
#include "protect.h"
#include "autotune.h"
#include "foc.h"        /* FOC_IsRunning() — mutual exclusion */
#include <stdint.h>

/* V/f closed-loop control for asynchronous motor.
 * Phase accumulator + slip frequency (not encoder angle directly).
 *
 * Architecture (ТЗ v3):
 *   encoder → measured_rpm
 *   speed PI → f_slip_hz
 *   f_e = p*n/60 + f_slip
 *   theta_elec += f_e * 2^32 / 1000  (phase accumulator, 1 kHz)
 *   V/f: voltage_mag = K_vf * |f_e| + V_boost
 *   3-phase sin → OEW PWM (same duty TIM1+TIM8) */

VFCtrl vfc;

/* Constants */
#define VFC_MAX_RPM          5000
#define VFC_MAX_FE_HZ        200
#define VFC_MAX_SLIP_HZ      5
#define VFC_MAX_VOLTAGE_PCT  95
#define VFC_RAMP_TIME_MS     2000

/* Phase accumulator: delta_theta(q31) per 1 ms = f_e * 2^32 / 1000 */
#define VFC_DELTA_THETA_PER_HZ  4294967UL  /* 2^32 / 1000 ≈ 4294967 */

/* 120° and 240° offsets in q31 */
#define VFC_120_DEG_Q31  0x55555555U
#define VFC_240_DEG_Q31  0xAAAAAAABU

void VFC_Init(void) {
    vfc.target_rpm = 0;
    vfc.measured_rpm = 0;
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
    vfc.voltage_mag = 0;
    vfc.theta_elec = 0;
    vfc.running = 0;
    vfc.v_boost_pct = 15;
    vfc.rated_freq_hz = 50;
    vfc.ramp_target_rpm = 0;
    vfc.ramp_current_rpm = 0;
    vfc.ramp_time_ms = VFC_RAMP_TIME_MS;
    vfc.ramp_tick = 0;
    vfc.ramp_rem = 0;
    vfc.duty_u = vfc.duty_v = vfc.duty_w = 50;
    PI_Init(&vfc.speed_pi, 50, 5, VFC_MAX_SLIP_HZ, -VFC_MAX_SLIP_HZ);
}

void VFC_Start(int32_t target_rpm) {
    if(vfc.running) return;
    if(FOC_IsRunning()) return;  /* не запускать поверх FOC */
    if(PROTECT_IsFault()) return;
    ADC_CalibrateOffsets();
    vfc.target_rpm = target_rpm;
    vfc.ramp_target_rpm = target_rpm;
    vfc.ramp_current_rpm = 0;
    vfc.ramp_tick = 0;
    vfc.ramp_rem = 0;
    vfc.theta_elec = 0;
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
    vfc.measured_rpm = 0;
    PI_Init(&vfc.speed_pi, 50, 5, VFC_MAX_SLIP_HZ, -VFC_MAX_SLIP_HZ);
    /* Сброс duty в midpoint ДО PWM_Enable(): CCR мог остаться от
     * предыдущего FOC-запуска (несимметричный вектор), а VFC_Update()
     * выполняется только из TIM6 (1 кГц) — иначе до 1 мс (5 периодов
     * PWM@5кГц) на выходе будет чужой вектор напряжения от FOC. */
    PWM_SetDuty1(50, 50, 50);
    PWM_SetDuty2(50, 50, 50);
    vfc.running = 1;
    PWM_Enable();
    /* ADC injected NOT started — V/f doesn't use FOC ISR */
}

void VFC_Stop(void) {
    vfc.running = 0;
    PWM_Disable();
    vfc.ramp_current_rpm = 0;
    vfc.ramp_rem = 0;
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
}

void VFC_SetTarget(int32_t target_rpm) {
    if(target_rpm > VFC_MAX_RPM) target_rpm = VFC_MAX_RPM;
    if(target_rpm < -VFC_MAX_RPM) target_rpm = -VFC_MAX_RPM;
    vfc.target_rpm = target_rpm;
    vfc.ramp_target_rpm = target_rpm;
    vfc.ramp_rem = 0;  /* reset remainder for new target */
}

int VFC_IsRunning(void) { return vfc.running; }
int32_t VFC_GetSpeed(void) { return vfc.measured_rpm; }
int32_t VFC_GetTarget(void) { return vfc.target_rpm; }

void VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz) {
    if(boost_pct >= 0 && boost_pct <= 30) vfc.v_boost_pct = boost_pct;
    if(rated_hz >= 10 && rated_hz <= 400) vfc.rated_freq_hz = rated_hz;
}

void VFC_Update(void) {
    if(!vfc.running) return;

    /* 1. Measure speed */
    vfc.measured_rpm = ENC_GetSpeed_rpm();

    /* 2. Speed ramp: exponential approach (ТЗ v3 formula: current += diff*dt/ramp_time).
     * Integer-safe: accumulate remainder in ramp_rem to avoid step=0 for small diff. */
    {
        int32_t diff = vfc.ramp_target_rpm - vfc.ramp_current_rpm;
        if(diff != 0) {
            int32_t num  = diff + vfc.ramp_rem;
            int32_t step = num / vfc.ramp_time_ms;
            vfc.ramp_rem = num % vfc.ramp_time_ms;
            vfc.ramp_current_rpm += step;
        } else {
            vfc.ramp_rem = 0;
        }
        vfc.ramp_tick++;
        if(vfc.ramp_current_rpm > VFC_MAX_RPM)  vfc.ramp_current_rpm = VFC_MAX_RPM;
        if(vfc.ramp_current_rpm < -VFC_MAX_RPM) vfc.ramp_current_rpm = -VFC_MAX_RPM;
    }

    /* 3. PI speed controller → slip frequency */
    int32_t error = vfc.ramp_current_rpm - vfc.measured_rpm;
    /* CLAMP after PI_Update — PI_Update only clamps integrator, not P+I sum */
    vfc.f_slip_hz = CLAMP(PI_Update(&vfc.speed_pi, error), -VFC_MAX_SLIP_HZ, VFC_MAX_SLIP_HZ);

    /* 4. Electrical stator frequency */
    int32_t pp = (int32_t)g_motor_params.pole_pairs;
    if(pp < 1) pp = 1;
    /* f_e = p * n_mech / 60 + f_slip */
    vfc.f_e_hz = (int32_t)(((int64_t)pp * vfc.measured_rpm) / 60) + vfc.f_slip_hz;
    vfc.f_e_hz = CLAMP(vfc.f_e_hz, -VFC_MAX_FE_HZ, VFC_MAX_FE_HZ);

    /* 5. Phase accumulator: theta += f_e * 2^32 / 1000 (per 1 ms) */
    /* int64 to avoid overflow before cast to uint32 */
    int64_t delta = (int64_t)vfc.f_e_hz * VFC_DELTA_THETA_PER_HZ;
    vfc.theta_elec += (uint32_t)delta;  /* wrap-around = mod 2*pi, signed via 2's complement */

    /* 6. V/f characteristic: V = (100 * |f_e| / rated_freq) + V_boost.
     * Одно деление в конце вместо усечённого k_vf=100/rated_freq_hz,
     * которое теряет точность (100/60=1 вместо 1.667 — ошибка 40%). */
    int32_t abs_fe = (vfc.f_e_hz >= 0) ? vfc.f_e_hz : -vfc.f_e_hz;
    int32_t vmag = (100 * abs_fe) / vfc.rated_freq_hz + vfc.v_boost_pct;
    if(abs_fe < 1) vmag = vfc.v_boost_pct;  /* start boost */
    if(vmag > VFC_MAX_VOLTAGE_PCT) vmag = VFC_MAX_VOLTAGE_PCT;
    if(vmag < 0) vmag = 0;
    vfc.voltage_mag = vmag;

    /* 7. Generate 3-phase sine via CORDIC */
    int32_t sin_u, cos_u, sin_v, cos_v, sin_w, cos_w;
    CORDIC_SinCos((int32_t)vfc.theta_elec, &sin_u, &cos_u);
    CORDIC_SinCos((int32_t)(vfc.theta_elec + VFC_120_DEG_Q31), &sin_v, &cos_v);
    CORDIC_SinCos((int32_t)(vfc.theta_elec + VFC_240_DEG_Q31), &sin_w, &cos_w);

    /* 8. OEW duty: 50% center ± voltage_mag/2, same duty TIM1+TIM8 */
    /* d = 50 + (vmag * sin) / (2 * 32768) */
    int32_t d_u = 50 + (vmag * sin_u) / (2 * 32768);
    int32_t d_v = 50 + (vmag * sin_v) / (2 * 32768);
    int32_t d_w = 50 + (vmag * sin_w) / (2 * 32768);

    /* CLAMP duty 2..98 (FOC_OEW_DUTY_MAX=49, margin 2%) */
    d_u = CLAMP(d_u, 2, 98);
    d_v = CLAMP(d_v, 2, 98);
    d_w = CLAMP(d_w, 2, 98);

    vfc.duty_u = d_u; vfc.duty_v = d_v; vfc.duty_w = d_w;
    PWM_SetDuty1((uint16_t)d_u, (uint16_t)d_v, (uint16_t)d_w);
    PWM_SetDuty2((uint16_t)d_u, (uint16_t)d_v, (uint16_t)d_w);
}
