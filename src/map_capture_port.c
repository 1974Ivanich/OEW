/* Production port для map_capture (пакет OEW Service-Only Map Capture),
 * адаптированный под OEW-HS-1 PWM replacement API (PwmServiceCapturePattern,
 * PWM_ServiceCaptureStart, центральный PWM_Disable). Единственный путь
 * к силовым/защитным API; прямых регистровых ЗАПИСЕЙ нет (чтение CCR/ARR/
 * BDTR для снапшота — допустимо). */
#include "map_capture.h"
#include "map_capture_profiles.h"

#include "stm32g474xx.h"   /* чтение CCR/ARR/BDTR для снапшота */
#include "autotune.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "vf_control.h"

#ifndef PWM_OEW_BOARD_REVISION
#define PWM_OEW_BOARD_REVISION 0u
#endif

/* ADC channels used by the injected/regular sequences (adc.c keeps them
 * private; replicate the sampling-time computation for identity purposes). */
#define CAP_CH_SHUNT1  1u
#define CAP_CH_SHUNT2  2u
#define CAP_CH_CT      3u
#define CAP_CH_VBUS    5u

static uint32_t cap_adc_clock_hz(void)
{
    /* ADC12 CKMODE field (CCR bits 31:30): 00=PCLK2, 01=PLL2R, 10=PLL3R,
     * 11=HCLK/4. adc.c sets 11 for the dual injected acquisition. Use the
     * live register value; PLL sources cannot be resolved here and fail
     * closed so the identity can never be silently accepted. */
    uint32_t ckmode = (ADC12_COMMON->CCR >> ADC_CCR_CKMODE_Pos) & 0x3u;
    uint32_t hclk = SystemCoreClock;
    uint32_t ppre2 = (RCC->CFGR & RCC_CFGR_PPRE2) >> RCC_CFGR_PPRE2_Pos;
    uint32_t apb_div = 1u;

    switch (ppre2) {
    case 4u:  apb_div = 2u;  break;
    case 5u:  apb_div = 4u;  break;
    case 6u:  apb_div = 8u;  break;
    case 7u:  apb_div = 16u; break;
    default:  break; /* 0,1,2,3 → divider 1 (reset value) */
    }

    switch (ckmode) {
    case 0u: return hclk / apb_div;          /* PCLK2 */
    case 3u: return hclk / 4u;               /* HCLK/4 — what adc.c programs */
    default: return 0u;                      /* PLL2R/PLL3R — unresolved */
    }
}

/* STM32G4 SMPR sampling-time code → real cycles; encoded as 2× so that the
 * half-cycle values become integers (640.5 → 1281). */
static uint16_t smp_cycles_x2(uint32_t code)
{
    switch (code & 7u) {
    case 0u: return 3u;    /* 1.5 */
    case 1u: return 7u;    /* 3.5 */
    case 2u: return 15u;   /* 7.5 */
    case 3u: return 25u;   /* 12.5 */
    case 4u: return 39u;   /* 19.5 */
    case 5u: return 79u;   /* 39.5 */
    case 6u: return 185u;  /* 92.5 */
    default: return 1281u; /* 111 → 640.5 */
    }
}

/* Greatest sampling time (2× cycles) over the four used channels. A change
 * in any channel's SMPR invalidates the map identity. SMPR1 covers channels
 * 0..9 (adc.c uses this layout). */
static uint16_t cap_adc_sample_cycles_x2(void)
{
    uint32_t s[4];
    uint16_t max_cycles = 0u;
    uint16_t i;
    uint16_t v;

    s[0] = (ADC1->SMPR1 >> (CAP_CH_SHUNT1 * 3u)) & 7u;
    s[1] = (ADC2->SMPR1 >> (CAP_CH_SHUNT2 * 3u)) & 7u;
    s[2] = (ADC2->SMPR1 >> (CAP_CH_CT * 3u)) & 7u;
    s[3] = (ADC2->SMPR1 >> (CAP_CH_VBUS * 3u)) & 7u;
    for (i = 0u; i < 4u; ++i) {
        v = smp_cycles_x2(s[i]);
        if (v > max_cycles) max_cycles = v;
    }
    return max_cycles;
}

/* ADC resolution field (CFGR[RES]: 0=12-bit, 1=10-bit, 2=8-bit, 3=6-bit). */
static uint8_t cap_adc_resolution(void)
{
    return (uint8_t)((ADC1->CFGR & ADC_CFGR_RES_Msk) >> ADC_CFGR_RES_Pos);
}

static uint32_t cap_pwm_frequency_hz(void)
{
    uint32_t psc;
    uint32_t tclk;
    uint32_t arr;
    uint64_t denominator;

    PWM_GetSysInfo(&psc, &tclk);
    arr = PWM_GetARR();
    denominator = 2ULL * (uint64_t)(psc + 1u) * (uint64_t)(arr + 1u);
    if (denominator == 0u) return 0u;
    return (uint32_t)(((uint64_t)tclk + denominator / 2u) / denominator);
}

static bool cap_controls_inactive(void)
{
    return !FOC_IsRunning() && !VFC_IsRunning() && !Autotune_IsActive();
}

static bool cap_fault_latched(void)
{
    return PROTECT_IsFault() != 0;
}

static bool cap_validate(const MapCaptureRequest *request)
{
    /* Compiled profile gate — единственная авторизация паттерна. Детальная
     * валидация CCR/trigger/revision выполняется внутри PWM_ServiceCaptureStart
     * (возвращает PWM_ENABLE_SERVICE_PATTERN_INVALID при несовпадении). */
    return MapCaptureProfile_IsApproved(request);
}

static bool cap_start(const MapCaptureRequest *request)
{
    PwmServiceCapturePattern pattern;

    if (request == 0) return false;
    pattern.tim1_ccr[0] = request->tim1_ccr[0];
    pattern.tim1_ccr[1] = request->tim1_ccr[1];
    pattern.tim1_ccr[2] = request->tim1_ccr[2];
    pattern.tim8_ccr[0] = request->tim8_ccr[0];
    pattern.tim8_ccr[1] = request->tim8_ccr[1];
    pattern.tim8_ccr[2] = request->tim8_ccr[2];
    pattern.sector_candidate = request->sector_candidate;
    pattern.window_candidate = request->window_candidate;
    pattern.trigger_revision = request->trigger_revision;
    return PWM_ServiceCaptureStart(&pattern) == PWM_ENABLE_OK;
}

static void cap_stop(void)
{
    /* OEW-HS-1 центральный stop: ARM_REQ первым → CEN/MOE/CCER → ADC stop. */
    PWM_Disable();
}

static bool cap_snapshot(MapCapturePwmSnapshot *out)
{
    if (out == 0) return false;
    out->tim1_ccr[0] = TIM1->CCR1;
    out->tim1_ccr[1] = TIM1->CCR2;
    out->tim1_ccr[2] = TIM1->CCR3;
    out->tim8_ccr[0] = TIM8->CCR1;
    out->tim8_ccr[1] = TIM8->CCR2;
    out->tim8_ccr[2] = TIM8->CCR3;
    out->tim1_arr = TIM1->ARR;
    /* Trigger offset from the TRGO edge to the injected aperture is not
     * measurable from timer registers (scope qualification required). It is
     * deliberately kept 0 until the timing-verification stage; a nonzero
     * value would otherwise mislead the map builder about physical timing. */
    out->trigger_offset_ticks = 0u;
    out->deadtime_ticks = (uint16_t)(TIM1->BDTR & 0xFFu);
    out->pwm_frequency_hz = cap_pwm_frequency_hz();
    out->trigger_revision = PWM_OEW_ADC_TRIGGER_REVISION;
    return true;
}

static void cap_latch(MapCaptureStatus reason)
{
    switch (reason) {
        case MAP_CAPTURE_TIMEOUT:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_TIMEOUT);
            break;
        case MAP_CAPTURE_BUFFER_OVERFLOW:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_BUFFER_OVERFLOW);
            break;
        case MAP_CAPTURE_LIMIT_EXCEEDED:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_LIMIT);
            break;
        case MAP_CAPTURE_ABORTED_BY_USER:
            /* Abort is a controlled stop, never a protection fault. */
            break;
        case MAP_CAPTURE_HW_INTERLOCK_MISSING:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_INTERLOCK);
            break;
        case MAP_CAPTURE_TRIGGER_MISMATCH:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_TRIGGER);
            break;
        default:
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_ADC);
            break;
    }
}

bool MapCapturePort_Init(void)
{
    static const MapCaptureHooks hooks = {
        .hardware_interlock_healthy = PWM_HardwareInterlockHealthy,
        .control_paths_inactive     = cap_controls_inactive,
        .fault_latched              = cap_fault_latched,
        .validate_service_pattern   = cap_validate,
        .start_service_pwm          = cap_start,
        .stop_service_pwm           = cap_stop,
        .snapshot_service_pwm       = cap_snapshot,
        .latch_capture_fault        = cap_latch
    };

    return MapCapture_Init(&hooks);
}

void MapCapturePort_OnPwmPeriod(void)
{
    if (MapCapture_IsActive()) MapCapture_OnPeriod();
}

void MapCapturePort_OnProtectionLatched(void)
{
    MapCapture_OnProtectionFault();
}

bool MapCapturePort_GetMapIdentity(OewMapIdentity *out)
{
    const uint16_t sample_cycles = cap_adc_sample_cycles_x2();
    const uint32_t adc_clock = cap_adc_clock_hz();

    if (out == 0) return false;
    out->board_revision = PWM_OEW_BOARD_REVISION;
    out->pwm_frequency_hz = cap_pwm_frequency_hz();
    out->timer_arr = PWM_GetARR();
    out->adc_trigger_id = PWM_OEW_ADC_TRIGGER_REVISION;
    out->adc_clock_hz = adc_clock;
    out->adc_sample_cycles_x2 = sample_cycles;
    out->adc_resolution = cap_adc_resolution();
    out->deadtime_ticks = (uint8_t)(TIM1->BDTR & 0xFFu);
    /* Dead-time must be nonzero for a real power stage; ADC signature must be
     * measurable (ADVREGEN implies ADC clock). A zero signature fails closed. */
    return out->board_revision != 0u && out->pwm_frequency_hz != 0u &&
           out->timer_arr != 0u && out->adc_trigger_id != 0u &&
           adc_clock != 0u && sample_cycles != 0u &&
           out->deadtime_ticks != 0u;
}
