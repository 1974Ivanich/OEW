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
#define VFC_MOD_MAX_Q15      31129  /* 95% от 32768 (VFC_MAX_VOLTAGE_PCT) — запас на линейность PWM */
#define VFC_RAMP_TIME_MS     2000

/* Phase accumulator: delta_theta(q31) per 1 ms = f_e * 2^32 / 1000 */
#define VFC_DELTA_THETA_PER_HZ  4294967UL  /* 2^32 / 1000 ≈ 4294967 */

/* 120° and 240° offsets in q31 */
#define VFC_120_DEG_Q31  0x55555555U
#define VFC_240_DEG_Q31  0xAAAAAAABU

/* ── VF-01: speed PI в физических единицах (rpm → Hz) ────────────────────
 * Общий PI_Update (foc.c) — Q15: kp·err>>15. При kp=50 первый 1 Гц slip
 * появлялся только при error≈656 rpm, интегратор — при 6554 rpm → на пуске
 * f_slip=0, АД не получал момента (вращающееся поле без скольжения).
 * Здесь: p_term = kp·error (Гц), интегратор накапливает мГц/такт (1 кГц):
 *   kp = 0.005 Гц/rpm (Q16=328):  error=100 → 0.5 Гц, error=500 → 2.5 Гц
 *   ki = 0.02 Гц/rpm/с (Q16=1311): error=100 → +2 мГц/такт → 2 Гц за 1 с
 * Коэффициенты — стартовые, настраиваются на стенде. */
#define VFC_SPD_KP_Q16  328    /* 0.005 Гц/rpm в Q16 */
#define VFC_SPD_KI_Q16  1311   /* 0.02 Гц/rpm/с в Q16 (мГц/такт) */
static int32_t vfc_slip_int_mhz = 0;

static int32_t vfc_speed_pi(int32_t error_rpm) {
    int32_t p_q16 = (int32_t)(((int64_t)VFC_SPD_KP_Q16 * error_rpm) >> 16); /* Гц·65536 */
    vfc_slip_int_mhz += (int32_t)(((int64_t)VFC_SPD_KI_Q16 * error_rpm) >> 16); /* мГц */
    if(vfc_slip_int_mhz > VFC_MAX_SLIP_HZ * 1000) vfc_slip_int_mhz = VFC_MAX_SLIP_HZ * 1000;
    if(vfc_slip_int_mhz < -VFC_MAX_SLIP_HZ * 1000) vfc_slip_int_mhz = -VFC_MAX_SLIP_HZ * 1000;
    return (p_q16 >> 16) + vfc_slip_int_mhz / 1000;   /* Hz */
}

/* Ревью VF-02: мех. потолок из VFC_MAX_FE_HZ и pole pairs: 200·60/p
 * (p=4 → 3000 rpm; 5000 rpm было бы 333 Гц > 200 Гц лимита). */
int32_t VFC_GetMaxRPM(void) {
    int32_t pp = (int32_t)g_motor_params.pole_pairs;
    if(pp < 1) pp = 1;
    int32_t m = (int32_t)((int64_t)VFC_MAX_FE_HZ * 60 / pp);
    return (m > VFC_MAX_RPM) ? VFC_MAX_RPM : m;
}

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
    vfc_slip_int_mhz = 0;   /* VF-01: свой speed PI (не общий Q15 PI_Update) */
}

void VFC_Start(int32_t target_rpm) {
    if(vfc.running) return;
    if(FOC_IsRunning()) return;  /* не запускать поверх FOC */
    if(PROTECT_IsFault()) return;
    ADC_CalibrateOffsets();
    /* Ревью VF-02: цель клэмпнута к мех. потолку из f_e/pole_pairs. */
    if(target_rpm > VFC_GetMaxRPM()) target_rpm = VFC_GetMaxRPM();
    if(target_rpm < -VFC_GetMaxRPM()) target_rpm = -VFC_GetMaxRPM();
    vfc.target_rpm = target_rpm;
    vfc.ramp_target_rpm = target_rpm;
    vfc.ramp_current_rpm = 0;
    vfc.ramp_tick = 0;
    vfc.ramp_rem = 0;
    vfc.theta_elec = 0;
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
    vfc.measured_rpm = 0;
    vfc_slip_int_mhz = 0;   /* VF-01 */
    /* Сброс duty в midpoint ДО PWM_Enable(): CCR мог остаться от
     * предыдущего FOC-запуска (несимметричный вектор), а VFC_Update()
     * выполняется только из TIM6 (1 кГц) — иначе до 1 мс (5 периодов
     * PWM@5кГц) на выходе будет чужой вектор напряжения от FOC.
     * mod=0 → CCR=ARR/2 → 0 В по фазе. */
    PWM_SetMod1(0, 0, 0);
    PWM_SetMod2(0, 0, 0);
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
    /* Ревью VF-02: потолок из f_e/pole_pairs, а не жёсткие 5000 rpm. */
    if(target_rpm > VFC_GetMaxRPM()) target_rpm = VFC_GetMaxRPM();
    if(target_rpm < -VFC_GetMaxRPM()) target_rpm = -VFC_GetMaxRPM();
    vfc.target_rpm = target_rpm;
    vfc.ramp_target_rpm = target_rpm;
    vfc.ramp_rem = 0;  /* reset remainder for new target */
}

int VFC_IsRunning(void) { return vfc.running; }
int32_t VFC_GetSpeed(void) { return vfc.measured_rpm; }
int32_t VFC_GetTarget(void) { return vfc.target_rpm; }

void VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz) {
    if(boost_pct >= 0 && boost_pct <= 30) vfc.v_boost_pct = boost_pct;
    /* Ревью VF-07: rated_hz не выше достижимого VFC_MAX_FE_HZ (200). */
    if(rated_hz >= 10 && rated_hz <= VFC_MAX_FE_HZ) vfc.rated_freq_hz = rated_hz;
}

void VFC_Update(void) {
    if(!vfc.running) return;

    /* Ревью VF-03/04: software fault → немедленный стоп V/f (раньше
     * vfc.running оставался 1 после внешнего PWM_Disable, TIM6 продолжал
     * писать CCR, а VFC_Start() отказывался перезапуском). */
    if(PROTECT_IsFault()) {
        VFC_Stop();
        return;
    }

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
        /* Ревью VF-02: clamp к мех. потолку из f_e/pole_pairs. */
        if(vfc.ramp_current_rpm > VFC_GetMaxRPM())  vfc.ramp_current_rpm = VFC_GetMaxRPM();
        if(vfc.ramp_current_rpm < -VFC_GetMaxRPM()) vfc.ramp_current_rpm = -VFC_GetMaxRPM();
    }

    /* 3. Speed controller → slip frequency (VF-01: физические единицы,
     * работает при малых error — раньше Q15-PI давал 0 до 656 rpm). */
    int32_t error = vfc.ramp_current_rpm - vfc.measured_rpm;
    vfc.f_slip_hz = CLAMP(vfc_speed_pi(error), -VFC_MAX_SLIP_HZ, VFC_MAX_SLIP_HZ);

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

    /* 8. OEW модуляция Q15: mod = vmag·sin/100 → CCR = mid + mod·mid (V ≈ mod·Vbus).
     * Эквивалент старой %-формулы d = 50 + vmag·sin/65536: mod = 2d−1 = vmag·sin/100.
     * vmag ≤ 95 → |mod| ≤ 95% (VFC_MOD_MAX_Q15): запас на линейность PWM. */
    int32_t mod_u = CLAMP((vmag * sin_u) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);
    int32_t mod_v = CLAMP((vmag * sin_v) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);
    int32_t mod_w = CLAMP((vmag * sin_w) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);

    /* Телеметрия (main.c @VF) ожидает duty в %: d = 50 + mod·50/32768
     * (тождественно старой d = 50 + vmag·sin/65536). */
    vfc.duty_u = 50 + (mod_u * 50) / 32768;
    vfc.duty_v = 50 + (mod_v * 50) / 32768;
    vfc.duty_w = 50 + (mod_w * 50) / 32768;
    PWM_SetMod1((int16_t)mod_u, (int16_t)mod_v, (int16_t)mod_w);
    PWM_SetMod2((int16_t)mod_u, (int16_t)mod_v, (int16_t)mod_w);
}
