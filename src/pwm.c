#include "pwm.h"
#include "adc.h"
#include "pwm_board_pins.h"
#include "protect.h"
#include "stm32g474xx.h"

#if !defined(TIM_BDTR_BKE) || !defined(TIM_BDTR_BKP) || \
    !defined(TIM_BDTR_BK2E) || !defined(TIM_BDTR_AOE) || \
    !defined(TIM_SR_BIF) || !defined(TIM_SR_B2IF)
#error "OEW-HS-1 requires full STM32G474 CMSIS TIM break/AF1 definitions; do not use a reduced timer header."
#endif

#define FOC_PWM_FREQ        5000u
#define FOC_DEAD_TIME_NS    1500u

#ifndef OEW_HS1_COMMISSIONING_RELEASE
#define OEW_HS1_COMMISSIONING_RELEASE 0
#endif

/* SD monitor-only bench mode (explicit operator ТЗ, стенд 01.09): the BKIN
 * break is disabled (BKE=0) so a transient STEVAL FAULT_N pulse on SD1/SD2
 * (PB12/PD2) can no longer remove MOE and stop the run. The SD pins remain
 * AF inputs and are continuously monitored/logged (em_stop telemetry and
 * sd1/sd2 fields in @VFLOG). Start-time SD-high requirement is retained. */
#ifndef OEW_SD_MONITOR_ONLY
#define OEW_SD_MONITOR_ONLY 0
#endif

#ifndef PWM_OEW_ADC_TRIGGER_REVISION
#define PWM_OEW_ADC_TRIGGER_REVISION 0u
#endif

#define PWM_ALL_CCER (TIM_CCER_CC1E | TIM_CCER_CC1NE | \
                      TIM_CCER_CC2E | TIM_CCER_CC2NE | \
                      TIM_CCER_CC3E | TIM_CCER_CC3NE)
#define PWM_BREAK_STATUS_MASK (TIM_SR_BIF | TIM_SR_B2IF)
#define PWM_BDTR_REQUIRED (TIM_BDTR_BKE | TIM_BDTR_OSSR | TIM_BDTR_OSSI)
#define PWM_BDTR_FORBIDDEN (TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE)
/* RM0440 AF1: BKINE is bit 0; BKINP is bit 9. For BKP=0, BKINP=0
 * selects active-low external BKIN sensitivity. Generic literals are used
 * because the device header only exports these names under TIM1. */
#define PWM_AF1_BKINE  (1u << 0)
#define PWM_AF1_BKINP  (1u << 9)

static uint16_t pwm_arr = 99u;
static volatile PwmSampleContext pwm_pending_context = { 0u, 0u, false };

#ifdef PWM_HOST_TEST
unsigned int pwm_host_enable_call_count;
#endif

#if OEW_BENCH_APERTURE
static volatile bool pwm_bench_aperture_active;
#endif

static bool pwm_context_is_sane(const PwmSampleContext *context)
{
    return (context != 0) && (context->sector < 6u) && (context->window < 2u);
}

static void pwm_publish_context(const PwmSampleContext *context)
{
    static const PwmSampleContext invalid = { 0u, 0u, false };

    if (!pwm_context_is_sane(context)) {
        context = &invalid;
    }
    pwm_pending_context = *context;
    __DMB();
    ADC_SetExpectedWindow(context->sector, context->window, context->valid);
}

void PWM_InvalidateSampleContext(void)
{
    static const PwmSampleContext invalid = { 0u, 0u, false };
    pwm_publish_context(&invalid);
}

bool PWM_GetPendingSampleContext(PwmSampleContext *out)
{
    if (out == 0) {
        return false;
    }
    *out = pwm_pending_context;
    return true;
}

bool PWM_HasValidSampleContext(void)
{
    return pwm_pending_context.valid &&
           (pwm_pending_context.sector < 6u) &&
           (pwm_pending_context.window < 2u);
}

static uint32_t get_tim_ck_int(void)
{
    uint32_t ppre2 = (RCC->CFGR & RCC_CFGR_PPRE2) >> RCC_CFGR_PPRE2_Pos;
    uint32_t apb_div;

    switch (ppre2) {
    case 0u: apb_div = 1u; break;
    case 4u: apb_div = 2u; break;
    case 5u: apb_div = 4u; break;
    case 6u: apb_div = 8u; break;
    case 7u: apb_div = 16u; break;
    default: apb_div = 1u; break;
    }
    if (apb_div != 1u) {
        return (SystemCoreClock / apb_div) * 2u;
    }
    return SystemCoreClock;
}

static uint8_t encode_dtg_ticks(uint32_t n)
{
    if (n <= 127u)  return (uint8_t)n;
    if (n <= 254u)  return (uint8_t)(0x80u | (((n + 1u) / 2u) - 64u));
    if (n <= 504u)  return (uint8_t)(0xC0u | (((n + 4u) / 8u) - 32u));
    if (n <= 1008u) return (uint8_t)(0xE0u | (((n + 8u) / 16u) - 32u));
    return 0xFFu;
}

static uint32_t decode_dtg_ticks(uint8_t dtg)
{
    if ((dtg & 0x80u) == 0u) return (uint32_t)(dtg & 0x7Fu);
    if ((dtg & 0xC0u) == 0x80u) return ((uint32_t)(dtg & 0x3Fu) + 64u) * 2u;
    if ((dtg & 0xE0u) == 0xC0u) return ((uint32_t)(dtg & 0x1Fu) + 32u) * 8u;
    return ((uint32_t)(dtg & 0x1Fu) + 32u) * 16u;
}

static uint16_t mod_to_ccr(int16_t mod)
{
    int32_t mid = ((int32_t)pwm_arr + 1) / 2;
    int32_t prod = (int32_t)mod * mid;
    int32_t ccr;

    if (prod >= 0) prod += 16384; else prod -= 16384;
    ccr = mid + prod / 32768;
    if (ccr < 0) ccr = 0;
    if (ccr > (int32_t)pwm_arr) ccr = (int32_t)pwm_arr;
    return (uint16_t)ccr;
}

static uint16_t duty_to_ccr(uint16_t duty)
{
    if (duty > 100u) duty = 100u;
    return (uint16_t)(((uint32_t)duty * pwm_arr + 50u) / 100u);
}

static bool timer_break_configured(const TIM_TypeDef *tim)
{
#if OEW_SD_MONITOR_ONLY
    /* Monitor-only: the BKIN break is deliberately disabled. The SD pins are
     * pure monitored inputs (IDR); there is nothing to validate on the break
     * path. The release interlock still requires SD high at start. */
    (void)tim;
    return true;
#else
    const uint32_t bdtr = tim->BDTR;
    return ((bdtr & PWM_BDTR_REQUIRED) == PWM_BDTR_REQUIRED) &&
           ((bdtr & PWM_BDTR_FORBIDDEN) == 0u) &&
           ((tim->AF1 & PWM_AF1_BKINE) != 0u) &&
           ((tim->AF1 & PWM_AF1_BKINP) == 0u);
#endif
}

bool PWM_BreakFaultActive(void)
{
#if OEW_SD_MONITOR_ONLY
    /* Monitor-only: a low SD line is an event to be logged, not a break
     * fault. BIF/B2IF cannot be set with BKE=0 but are checked defensively. */
    return ((TIM1->SR & PWM_BREAK_STATUS_MASK) != 0u) ||
           ((TIM8->SR & PWM_BREAK_STATUS_MASK) != 0u);
#else
    /* Physical state only: SD low or a still-set BIF/B2IF. The terminal
     * software latch is the central PROTECT fault (set by the break ISR),
     * which is the single explicit-recovery gate. */
    return !PWM_SdLinesAreHigh() ||
           ((TIM1->SR & PWM_BREAK_STATUS_MASK) != 0u) ||
           ((TIM8->SR & PWM_BREAK_STATUS_MASK) != 0u);
#endif
}

bool PWM_HardwareInterlockHealthy(void)
{
#if OEW_HS1_COMMISSIONING_RELEASE
    return PWM_SdLinesAreHigh() &&
           !PWM_BreakFaultActive() &&
           timer_break_configured(TIM1) &&
           timer_break_configured(TIM8);
#else
    /* Keep the static configuration validator referenced in default-deny
     * builds without reading hardware or weakening the release gate. */
    (void)timer_break_configured;
    return false;
#endif
}

static int pwm_common_arm_preconditions(bool require_context)
{
    extern volatile uint8_t g_clock_fail;

    if (PWM_IsEnabled()) {
        return PWM_ENABLE_ALREADY_ACTIVE;
    }
    if (require_context && !PWM_HasValidSampleContext()) {
        return PWM_ENABLE_CONTEXT_INVALID;
    }
    if (PROTECT_IsFault()) {
        return PWM_ENABLE_FAULT_LATCHED;
    }
    if (g_clock_fail != 0u) {
        return PWM_ENABLE_CLOCK_FAILED;
    }
    if (!ADC_InjectedIsArmed()) {
        return PWM_ENABLE_ADC_NOT_ARMED;
    }
    if (!PWM_HardwareInterlockHealthy()) {
        return PWM_ENABLE_INTERLOCK_OPEN;
    }
    return PWM_ENABLE_OK;
}

static void pwm_start_timers_direct(void)
{
    /* No interposer/output buffers remain: timer outputs drive the IPMs
     * directly, while each low SD asynchronously removes the corresponding
     * timer MOE through BKIN. Preconditions were checked before this point. */
    TIM1->CCER = PWM_ALL_CCER;
    TIM8->CCER = PWM_ALL_CCER;
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM1->CNT = 0u;
    TIM8->CNT = 0u;
    TIM8->CR1 |= TIM_CR1_CEN;
    TIM1->CR1 |= TIM_CR1_CEN;
    __DSB();
}

void PWM_Init(void)
{
    uint32_t tck = get_tim_ck_int();
    uint32_t psc_plus1 = tck / 10000000u;
    uint32_t timer_clk;
    uint32_t arr_plus1;
    uint32_t dtg_ticks;
    uint16_t psc;
    uint8_t dtg8;
#if OEW_SD_MONITOR_ONLY
    const uint32_t pwm_break_bits = 0u;
#else
    const uint32_t pwm_break_bits = TIM_BDTR_BKE;
#endif

    PWM_BoardPins_Init();
    if (psc_plus1 == 0u) psc_plus1 = 1u;
    timer_clk = tck / psc_plus1;
    arr_plus1 = (timer_clk + FOC_PWM_FREQ) / (FOC_PWM_FREQ * 2u);
    psc = (uint16_t)(psc_plus1 - 1u);
    pwm_arr = (uint16_t)(arr_plus1 - 1u);
    dtg_ticks = (uint32_t)(((uint64_t)FOC_DEAD_TIME_NS * tck + 500000000ULL) /
                           1000000000ULL);
    dtg8 = encode_dtg_ticks(dtg_ticks);

    PWM_InvalidateSampleContext();

    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN | RCC_APB2ENR_TIM8EN;
    (void)RCC->APB2ENR;

    TIM1->PSC = psc;
    TIM1->ARR = pwm_arr;
    TIM1->CR1 = TIM_CR1_CMS_1 | TIM_CR1_CMS_0 | TIM_CR1_ARPE;
    TIM1->RCR = 1u;
    /* BKE primary external fault, active low (BKP=0), no BK2, no AOE.
     * Monitor-only (OEW_SD_MONITOR_ONLY=1): pwm_break_bits = 0 — BKIN break
     * disabled, SD pins are monitored inputs only. */
    TIM1->BDTR = (uint32_t)dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | pwm_break_bits;
    TIM1->AF1 = (TIM1->AF1 & ~PWM_AF1_BKINP) | PWM_AF1_BKINE;
    TIM1->CCMR1 = (6u << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE |
                  (6u << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;
    TIM1->CCMR2 = (6u << TIM_CCMR2_OC3M_Pos) | TIM_CCMR2_OC3PE;
    TIM1->CCR1 = 0u; TIM1->CCR2 = 0u; TIM1->CCR3 = 0u;
    TIM1->CCER = 0u;
    TIM1->CR2 = (2u << TIM_CR2_MMS_Pos);
    TIM1->CNT = 0u;
    TIM1->SR = 0u;
    TIM1->DIER |= TIM_DIER_BIE;
    TIM1->EGR = TIM_EGR_UG;

    TIM8->PSC = psc;
    TIM8->ARR = pwm_arr;
    TIM8->CR1 = TIM_CR1_CMS_1 | TIM_CR1_CMS_0 | TIM_CR1_ARPE;
    TIM8->RCR = 1u;
    /* Same break policy as TIM1: BKE in production, off in monitor-only. */
    TIM8->BDTR = (uint32_t)dtg8 | TIM_BDTR_OSSR | TIM_BDTR_OSSI | pwm_break_bits;
    TIM8->AF1 = (TIM8->AF1 & ~PWM_AF1_BKINP) | PWM_AF1_BKINE;
    TIM8->CCMR1 = (7u << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE |
                  (7u << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;
    TIM8->CCMR2 = (7u << TIM_CCMR2_OC3M_Pos) | TIM_CCMR2_OC3PE;
    TIM8->CCR1 = 0u; TIM8->CCR2 = 0u; TIM8->CCR3 = 0u;
    TIM8->CCER = 0u;
    TIM8->SMCR = 4u; /* Reset mode, TS=ITR0 TIM1_TRGO. */
    TIM8->CNT = 0u;
    TIM8->SR = 0u;
    TIM8->DIER |= TIM_DIER_BIE;
    TIM8->EGR = TIM_EGR_UG;

}

bool PWM_SetControlVector(int16_t mu, int16_t mv, int16_t mw,
                          const PwmSampleContext *context)
{
    if (!pwm_context_is_sane(context)) {
        PWM_InvalidateSampleContext();
        return false;
    }
    TIM1->CCR1 = mod_to_ccr(mu);
    TIM1->CCR2 = mod_to_ccr(mv);
    TIM1->CCR3 = mod_to_ccr(mw);
    TIM8->CCR1 = mod_to_ccr(mu);
    TIM8->CCR2 = mod_to_ccr(mv);
    TIM8->CCR3 = mod_to_ccr(mw);
    __DMB();
    pwm_publish_context(context);
    return context->valid;
}

void PWM_SetMod1(int16_t mu, int16_t mv, int16_t mw)
{
    TIM1->CCR1 = mod_to_ccr(mu);
    TIM1->CCR2 = mod_to_ccr(mv);
    TIM1->CCR3 = mod_to_ccr(mw);
    PWM_InvalidateSampleContext();
}

void PWM_SetMod2(int16_t mu, int16_t mv, int16_t mw)
{
    TIM8->CCR1 = mod_to_ccr(mu);
    TIM8->CCR2 = mod_to_ccr(mv);
    TIM8->CCR3 = mod_to_ccr(mw);
    PWM_InvalidateSampleContext();
}

void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w)
{
    TIM1->CCR1 = duty_to_ccr(u);
    TIM1->CCR2 = duty_to_ccr(v);
    TIM1->CCR3 = duty_to_ccr(w);
    PWM_InvalidateSampleContext();
}

void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w)
{
    TIM8->CCR1 = duty_to_ccr(u);
    TIM8->CCR2 = duty_to_ccr(v);
    TIM8->CCR3 = duty_to_ccr(w);
    PWM_InvalidateSampleContext();
}

int PWM_Enable(void)
{
#ifdef PWM_HOST_TEST
    ++pwm_host_enable_call_count;
#endif
    const int rc = pwm_common_arm_preconditions(true);
    if (rc != PWM_ENABLE_OK) {
        return rc;
    }
    pwm_start_timers_direct();
    return PWM_ENABLE_OK;
}

int PWM_ServiceCaptureStart(const PwmServiceCapturePattern *pattern)
{
    PwmSampleContext diagnostic_context;
    int rc;

    if ((pattern == 0) || (pattern->sector_candidate >= 6u) ||
        (pattern->window_candidate >= 2u) ||
        (pattern->trigger_revision == 0u) ||
        (pattern->trigger_revision != PWM_OEW_ADC_TRIGGER_REVISION) ||
        (pattern->tim1_ccr[0] > pwm_arr) || (pattern->tim1_ccr[1] > pwm_arr) ||
        (pattern->tim1_ccr[2] > pwm_arr) || (pattern->tim8_ccr[0] > pwm_arr) ||
        (pattern->tim8_ccr[1] > pwm_arr) || (pattern->tim8_ccr[2] > pwm_arr)) {
        PWM_InvalidateSampleContext();
        return PWM_ENABLE_SERVICE_PATTERN_INVALID;
    }
    rc = pwm_common_arm_preconditions(false);
    if (rc != PWM_ENABLE_OK) {
        return rc;
    }

    TIM1->CCR1 = pattern->tim1_ccr[0];
    TIM1->CCR2 = pattern->tim1_ccr[1];
    TIM1->CCR3 = pattern->tim1_ccr[2];
    TIM8->CCR1 = pattern->tim8_ccr[0];
    TIM8->CCR2 = pattern->tim8_ccr[1];
    TIM8->CCR3 = pattern->tim8_ccr[2];
    diagnostic_context.sector = pattern->sector_candidate;
    diagnostic_context.window = pattern->window_candidate;
    diagnostic_context.valid = false;
    __DMB();
    /* Explicitly retain diagnostic-only admission; this is not an ADC bypass. */
    ADC_SetControlAdmission(false);
    pwm_publish_context(&diagnostic_context);
    pwm_start_timers_direct();
    return PWM_ENABLE_OK;
}

int PWM_ServiceEnable(const PwmSampleContext *context)
{
    (void)context;
    PWM_InvalidateSampleContext();
    return PWM_ENABLE_SERVICE_PROFILE_REQUIRED;
}

void PWM_Disable(void)
{
    uint16_t mid = (uint16_t)((pwm_arr + 1u) / 2u);

#if OEW_BENCH_APERTURE
    pwm_bench_aperture_active = false;
#endif
    PWM_InvalidateSampleContext();
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER = 0u;
    TIM8->CCER = 0u;
    TIM1->CCR1 = mid; TIM1->CCR2 = mid; TIM1->CCR3 = mid;
    TIM8->CCR1 = mid; TIM8->CCR2 = mid; TIM8->CCR3 = mid;
    ADC_InjectedStop();
}

uint32_t PWM_IsEnabled(void)
{
    return ((TIM1->CR1 & TIM_CR1_CEN) != 0u &&
            (TIM8->CR1 & TIM_CR1_CEN) != 0u &&
            (TIM1->BDTR & TIM_BDTR_MOE) != 0u &&
            (TIM8->BDTR & TIM_BDTR_MOE) != 0u &&
            PWM_HardwareInterlockHealthy()) ? 1u : 0u;
}

int PWM_SetDeadTime_ns(uint32_t dt_ns)
{
    uint32_t tck;
    uint32_t ticks;
    uint8_t encoded;

    /* No IRQ masking, ADC stop/restart or live timer mutation. */
    if (PWM_IsEnabled() || ADC_InjectedIsArmed()) {
        return -1;
    }
    tck = get_tim_ck_int();
    ticks = (uint32_t)(((uint64_t)dt_ns * tck + 500000000ULL) / 1000000000ULL);
    if (ticks == 0u) ticks = 1u;
    encoded = encode_dtg_ticks(ticks);
    TIM1->BDTR = (TIM1->BDTR & ~0xFFu) | encoded;
    TIM8->BDTR = (TIM8->BDTR & ~0xFFu) | encoded;
    PWM_InvalidateSampleContext();
    return 0;
}

uint32_t PWM_GetDeadTime_ns(void)
{
    const uint32_t ticks = decode_dtg_ticks((uint8_t)(TIM1->BDTR & 0xFFu));
    const uint32_t tck = get_tim_ck_int();
    return (uint32_t)(((uint64_t)ticks * 1000000000ULL + tck / 2u) / tck);
}

void PWM_SetDeadTimeComp(int32_t dt_ticks)
{
    (void)dt_ticks;
}

void PWM_DebugSetModulation(uint16_t arr, uint16_t mod_pct, uint32_t dt_ns, uint8_t mask)
{
    (void)arr;
    (void)mod_pct;
    (void)dt_ns;
    (void)mask;
    /* The former debug path could start CEN/MOE without a measured map.
     * OEW-HS-1 makes it a permanent no-op safe state. */
    PWM_InvalidateSampleContext();
}

#if OEW_BENCH_APERTURE
/* This path is intentionally separate from PWM_Enable and service capture.
 * It starts timer counters only to expose fixed TRGO/ADC timing. PWM pins stay
 * electrically disabled: no CCER bit and no MOE bit is ever raised here. */
static void pwm_bench_force_no_output(void)
{
    TIM1->CCER = 0u;
    TIM8->CCER = 0u;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
}

static bool pwm_bench_ccr_is_sane(uint16_t ccr_u, uint16_t ccr_v, uint16_t ccr_w)
{
    return ccr_u <= pwm_arr && ccr_v <= pwm_arr && ccr_w <= pwm_arr;
}

int PWM_BenchApertureStart(uint16_t arr, uint16_t ccr_u,
                           uint16_t ccr_v, uint16_t ccr_w)
{
    uint32_t psc_plus1;
    uint16_t psc;
    uint32_t saved_primask;

    if (arr == 0u || ccr_u > arr || ccr_v > arr || ccr_w > arr ||
        PWM_IsEnabled() || ADC_InjectedIsArmed()) return -1;
    saved_primask = __get_PRIMASK();
    __disable_irq();

    /* Stop any timer activity before modifying its timebase. This can only
     * make the state safer if the request follows an interrupted session. */
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;
    pwm_bench_force_no_output();
    pwm_bench_aperture_active = false;
    ADC_SetControlAdmission(false);
    PWM_InvalidateSampleContext();

    psc_plus1 = get_tim_ck_int() / 10000000u;
    if (psc_plus1 == 0u) psc_plus1 = 1u;
    psc = (uint16_t)(psc_plus1 - 1u);
    pwm_arr = arr;

    TIM1->PSC = psc;
    TIM8->PSC = psc;
    TIM1->ARR = arr;
    TIM8->ARR = arr;
    TIM1->CCR1 = ccr_u; TIM1->CCR2 = ccr_v; TIM1->CCR3 = ccr_w;
    TIM8->CCR1 = ccr_u; TIM8->CCR2 = ccr_v; TIM8->CCR3 = ccr_w;
    TIM1->CNT = 0u;
    TIM8->CNT = 0u;
    TIM1->EGR = TIM_EGR_UG;
    TIM8->EGR = TIM_EGR_UG;
    TIM1->SR = 0u;
    TIM8->SR = 0u;
    TIM1->DIER |= TIM_DIER_UIE;
    /* ADC is armed only to observe the injected JEOS marker. Admission stays
     * false, so no control consumer may treat this as a valid sample. */
    if (ADC_InjectedStart() != 0) {
        TIM1->DIER &= ~TIM_DIER_UIE;
        pwm_bench_force_no_output();
        __set_PRIMASK(saved_primask);
        return -1;
    }

    /* TIM8 remains synchronised to TIM1 TRGO; counters are the only active
     * bench feature. Force no-output both before and after CEN. */
    pwm_bench_force_no_output();
    TIM8->CR1 |= TIM_CR1_CEN;
    TIM1->CR1 |= TIM_CR1_CEN;
    pwm_bench_force_no_output();
    pwm_bench_aperture_active = true;
    __set_PRIMASK(saved_primask);
    return 0;
}

int PWM_BenchApertureSetVector(uint16_t ccr_u, uint16_t ccr_v, uint16_t ccr_w)
{
    uint32_t saved_primask;

    if (!pwm_bench_aperture_active ||
        (TIM1->CR1 & TIM_CR1_CEN) == 0u ||
        (TIM8->CR1 & TIM_CR1_CEN) == 0u ||
        !pwm_bench_ccr_is_sane(ccr_u, ccr_v, ccr_w)) {
        return -1;
    }
    saved_primask = __get_PRIMASK();
    __disable_irq();
    pwm_bench_force_no_output();
    TIM1->CCR1 = ccr_u; TIM1->CCR2 = ccr_v; TIM1->CCR3 = ccr_w;
    TIM8->CCR1 = ccr_u; TIM8->CCR2 = ccr_v; TIM8->CCR3 = ccr_w;
    PWM_InvalidateSampleContext();
    ADC_SetControlAdmission(false);
    pwm_bench_force_no_output();
    __set_PRIMASK(saved_primask);
    return 0;
}

void PWM_BenchApertureStop(void)
{
    uint32_t saved_primask = __get_PRIMASK();
    __disable_irq();
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM8->CR1 &= ~TIM_CR1_CEN;
    TIM1->DIER &= ~TIM_DIER_UIE;
    pwm_bench_force_no_output();
    ADC_InjectedStop();
    PWM_InvalidateSampleContext();
    ADC_SetControlAdmission(false);
    pwm_bench_aperture_active = false;
    __set_PRIMASK(saved_primask);
}
#endif

uint16_t PWM_GetARR(void) { return pwm_arr; }

void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt)
{
    if (cr1 != 0) *cr1 = TIM1->CR1;
    if (ccer != 0) *ccer = TIM1->CCER;
    if (bdtr != 0) *bdtr = TIM1->BDTR;
    if (cnt != 0) *cnt = TIM1->CNT;
}

void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk)
{
    if (psc != 0) *psc = TIM1->PSC;
    if (tclk != 0) *tclk = get_tim_ck_int() / (TIM1->PSC + 1u);
}

void PWM_DumpRegs8(uint32_t *psc, uint32_t *arr, uint32_t *bdtr,
                   uint32_t *cr1, uint32_t *cr2, uint32_t *ccer)
{
    if (psc != 0) *psc = TIM8->PSC;
    if (arr != 0) *arr = TIM8->ARR;
    if (bdtr != 0) *bdtr = TIM8->BDTR;
    if (cr1 != 0) *cr1 = TIM8->CR1;
    if (cr2 != 0) *cr2 = TIM8->CR2;
    if (ccer != 0) *ccer = TIM8->CCER;
}

void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr,
                  uint32_t *cr1, uint32_t *cr2, uint32_t *ccer)
{
    if (psc != 0) *psc = TIM1->PSC;
    if (arr != 0) *arr = TIM1->ARR;
    if (bdtr != 0) *bdtr = TIM1->BDTR;
    if (cr1 != 0) *cr1 = TIM1->CR1;
    if (cr2 != 0) *cr2 = TIM1->CR2;
    if (ccer != 0) *ccer = TIM1->CCER;
}
