#include "vf_control.h"
#include "encoder.h"
#include "cordic_math.h"
#include "pwm.h"
#include "adc.h"
#include "protect.h"
#include "autotune.h"
#include "foc.h"
#include <stdint.h>

VFCtrl vfc;
#define VFC_MAX_RPM          5000
#define VFC_MAX_FE_HZ        200
#define VFC_MAX_SLIP_HZ      5
#define VFC_MAX_VOLTAGE_PCT  95
#define VFC_MOD_MAX_Q15      31129
#define VFC_RAMP_TIME_MS     2000
#define VFC_DELTA_THETA_PER_HZ  4294967UL
#define VFC_120_DEG_Q31      0x55555555U
#define VFC_240_DEG_Q31      0xAAAAAAABU

/* Measured on PC-2: 50.1..54.3 us at a 10 MHz timer clock is 501..543
 * timer ticks. TRGO is calculated at underflow for RCR=1; window 0 is the
 * current map convention and remains a required energize-time confirmation.
 * CCR 135 is retained as the measured no-overlap lower bound: the measured margin
 * at CCR=250 is about 11 us (110 timer ticks), while CCR<135 enters I1/I2
 * conversion. ARR=999 is the qualified 10 MHz / 5.01 kHz configuration. */
const VfcApertureContractEntry VfcApertureContract[6] = {
    { true, 0u, 135u, 999u, 501u, 543u, 110u },
    { true, 0u, 135u, 999u, 501u, 543u, 110u },
    { true, 0u, 135u, 999u, 501u, 543u, 110u },
    { true, 0u, 135u, 999u, 501u, 543u, 110u },
    { true, 0u, 135u, 999u, 501u, 543u, 110u },
    { true, 0u, 135u, 999u, 501u, 543u, 110u }
};

static bool vfc_contract_valid(const VfcApertureContractEntry *contract)
{
    return contract != 0 && contract->measured && contract->window < 2u &&
           contract->modulation_min <= contract->modulation_max &&
           contract->modulation_max <= 999u &&
           contract->trgo_to_jeos_min_cycles <= contract->trgo_to_jeos_max_cycles &&
           contract->switching_margin_cycles >= 110u;
}

/* Strict ordering is intentional. Equality, zero vectors and any ambiguous
 * geometry are rejected rather than guessed. The sector order is the six
 * permutations of (mu,mv,mw), clockwise by phase-label ordering. */
static bool vfc_select_context(int16_t mu, int16_t mv, int16_t mw,
                               PwmSampleContext *out)
{
    uint8_t sector;
    uint16_t arr = PWM_GetARR();
    int32_t mid = ((int32_t)arr + 1) / 2;
    int32_t ccr_u = mid + ((int32_t)mu * mid) / 32768;
    int32_t ccr_v = mid + ((int32_t)mv * mid) / 32768;
    int32_t ccr_w = mid + ((int32_t)mw * mid) / 32768;
    const VfcApertureContractEntry *contract;

    if (out == 0 || (mu == 0 && mv == 0 && mw == 0) ||
        mu == mv || mu == mw || mv == mw) return false;
    if (mu > mv) {
        sector = (mv > mw) ? 0u : ((mu > mw) ? 1u : 4u);
    } else if (mv > mw) {
        sector = (mu > mw) ? 2u : 3u;
    } else {
        sector = 5u;
    }
    contract = &VfcApertureContract[sector];
    if (!vfc_contract_valid(contract) || arr == 0u || ccr_u < 0 || ccr_v < 0 || ccr_w < 0 ||
        ccr_u > (int32_t)arr || ccr_v > (int32_t)arr || ccr_w > (int32_t)arr ||
        ccr_u < contract->modulation_min || ccr_v < contract->modulation_min ||
        ccr_w < contract->modulation_min || ccr_u > contract->modulation_max ||
        ccr_v > contract->modulation_max || ccr_w > contract->modulation_max) return false;

    out->sector = sector;
    out->window = contract->window;
    out->valid = true;
    return true;
}

static void vfc_make_vector(uint32_t theta, int32_t magnitude,
                            int16_t *mu, int16_t *mv, int16_t *mw)
{
    int32_t su, cu, sv, cv, sw, cw;
    (void)cu; (void)cv; (void)cw;
    CORDIC_SinCos((int32_t)theta, &su, &cu);
    CORDIC_SinCos((int32_t)(theta + VFC_120_DEG_Q31), &sv, &cv);
    CORDIC_SinCos((int32_t)(theta + VFC_240_DEG_Q31), &sw, &cw);
    *mu = (int16_t)CLAMP((magnitude * su) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);
    *mv = (int16_t)CLAMP((magnitude * sv) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);
    *mw = (int16_t)CLAMP((magnitude * sw) / 100, -VFC_MOD_MAX_Q15, VFC_MOD_MAX_Q15);
}

static void vfc_start_cleanup(void)
{
    ADC_SetControlAdmission(false);
    PWM_InvalidateSampleContext();
    PWM_Disable();
    vfc.running = 0;
}

void VFC_Init(void) {
    vfc.target_rpm = 0; vfc.measured_rpm = 0; vfc.f_e_hz = 0; vfc.f_slip_hz = 0;
    vfc.voltage_mag = 0; vfc.theta_elec = 0; vfc.running = 0;
    vfc.v_boost_pct = 15; vfc.rated_freq_hz = 50; vfc.ramp_target_rpm = 0;
    vfc.ramp_current_rpm = 0; vfc.ramp_time_ms = VFC_RAMP_TIME_MS;
    vfc.ramp_tick = 0; vfc.ramp_rem = 0; vfc.duty_u = vfc.duty_v = vfc.duty_w = 50;
    PI_Init(&vfc.speed_pi, 50, 5, VFC_MAX_SLIP_HZ, -VFC_MAX_SLIP_HZ);
}

int VFC_Start(int32_t target_rpm)
{
    int16_t mu, mv, mw;
    PwmSampleContext context;
    int rc;
    VFC_SetTarget(target_rpm);
    if (vfc.running) return VFC_START_ALREADY_RUNNING;
    if (FOC_IsRunning()) return VFC_START_FOC_ACTIVE;
    if (PROTECT_IsFault()) return VFC_START_FAULT_LATCHED;

    vfc_make_vector(vfc.theta_elec, vfc.v_boost_pct, &mu, &mv, &mw);
    if (!vfc_select_context(mu, mv, mw, &context)) {
        vfc_start_cleanup();
        return VFC_START_SELECTOR_FAILED;
    }
    if (!PWM_SetControlVector(mu, mv, mw, &context)) {
        vfc_start_cleanup();
        return VFC_START_VECTOR_FAILED;
    }
    ADC_SetControlAdmission(true);
    rc = ADC_InjectedStart();
    if (rc != 0) {
        vfc_start_cleanup();
        return VFC_START_ADC_ARM_FAILED;
    }
    rc = PWM_Enable();
    if (rc != PWM_ENABLE_OK) {
        vfc_start_cleanup();
        return VFC_START_PWM_ENABLE_FAILED;
    }
    vfc.running = 1;
    return VFC_START_OK;
}

void VFC_Stop(void) {
    vfc.running = 0;
    ADC_SetControlAdmission(false);
    PWM_Disable();
    vfc.ramp_current_rpm = 0; vfc.ramp_rem = 0; vfc.f_e_hz = 0; vfc.f_slip_hz = 0;
}

void VFC_SetTarget(int32_t target_rpm) {
    if(target_rpm > VFC_MAX_RPM) target_rpm = VFC_MAX_RPM;
    if(target_rpm < -VFC_MAX_RPM) target_rpm = -VFC_MAX_RPM;
    vfc.target_rpm = target_rpm; vfc.ramp_target_rpm = target_rpm; vfc.ramp_rem = 0;
}
int VFC_IsRunning(void) { return vfc.running; }
int32_t VFC_GetSpeed(void) { return vfc.measured_rpm; }
int32_t VFC_GetTarget(void) { return vfc.target_rpm; }
void VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz) {
    if(boost_pct >= 0 && boost_pct <= 30) vfc.v_boost_pct = boost_pct;
    if(rated_hz >= 10 && rated_hz <= 400) vfc.rated_freq_hz = rated_hz;
}

void VFC_Update(void) {
    int16_t mod_u, mod_v, mod_w;
    PwmSampleContext context;
    if(!vfc.running) return;
    if (PROTECT_IsFault()) { VFC_Stop(); return; }
    vfc.measured_rpm = ENC_GetSpeed_rpm();
    {
        int32_t diff = vfc.ramp_target_rpm - vfc.ramp_current_rpm;
        if(diff != 0) { int32_t num = diff + vfc.ramp_rem; int32_t step = num / vfc.ramp_time_ms;
            vfc.ramp_rem = num % vfc.ramp_time_ms; vfc.ramp_current_rpm += step;
        } else vfc.ramp_rem = 0;
        vfc.ramp_tick++;
        if(vfc.ramp_current_rpm > VFC_MAX_RPM) vfc.ramp_current_rpm = VFC_MAX_RPM;
        if(vfc.ramp_current_rpm < -VFC_MAX_RPM) vfc.ramp_current_rpm = -VFC_MAX_RPM;
    }
    {
        int32_t error = vfc.ramp_current_rpm - vfc.measured_rpm;
        vfc.f_slip_hz = CLAMP(PI_Update(&vfc.speed_pi, error), -VFC_MAX_SLIP_HZ, VFC_MAX_SLIP_HZ);
    }
    { int32_t pp = FOC_GetPolePairs(); if(pp < 1) pp = 1;
      vfc.f_e_hz = (int32_t)(((int64_t)pp * vfc.measured_rpm) / 60) + vfc.f_slip_hz;
      vfc.f_e_hz = CLAMP(vfc.f_e_hz, -VFC_MAX_FE_HZ, VFC_MAX_FE_HZ); }
    vfc.theta_elec += (uint32_t)((int64_t)vfc.f_e_hz * VFC_DELTA_THETA_PER_HZ);
    { int32_t abs_fe = vfc.f_e_hz >= 0 ? vfc.f_e_hz : -vfc.f_e_hz;
      int32_t vmag = (100 * abs_fe) / vfc.rated_freq_hz + vfc.v_boost_pct;
      if(abs_fe < 1) vmag = vfc.v_boost_pct;
      if(vmag > VFC_MAX_VOLTAGE_PCT) vmag = VFC_MAX_VOLTAGE_PCT;
      if(vmag < 0) vmag = 0;
      vfc.voltage_mag = vmag; }
    vfc_make_vector(vfc.theta_elec, vfc.voltage_mag, &mod_u, &mod_v, &mod_w);
    vfc.duty_u = 50 + ((int32_t)mod_u * 50) / 32768;
    vfc.duty_v = 50 + ((int32_t)mod_v * 50) / 32768;
    vfc.duty_w = 50 + ((int32_t)mod_w * 50) / 32768;
    if (!vfc_select_context(mod_u, mod_v, mod_w, &context) ||
        !PWM_SetControlVector(mod_u, mod_v, mod_w, &context)) VFC_Stop();
}
