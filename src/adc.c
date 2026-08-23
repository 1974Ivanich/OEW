#include "adc.h"
#include "stm32g474xx.h"

/* TIM1 TRGO is JEXTSEL=0 on STM32G474 ADC injected group. */
#define ADC_INJ_TRGO_SEL              0u
#define ADC_INJ_RISING_EDGE            1u
#define ADC_DUAL_INJ_SIMULT            (ADC_CCR_DUAL_2 | ADC_CCR_DUAL_0)

#define ADC_CH_SHUNT1                  1u /* PA0 / ADC1_IN1 */
#define ADC_CH_SHUNT2                  2u /* PA1 / ADC2_IN2 */
#define ADC_CH_CT                       3u /* PA6 / ADC2_IN3 */
#define ADC_CH_VBUS                     5u /* PC4 / ADC2_IN5 */
#define ADC_RAW_SAT_LOW                 1u
#define ADC_RAW_SAT_HIGH                4094u

/* All runtime control inputs must remain away from both rails. The two
 * DC-link shunt amplifiers are bipolar and biased at mid-scale (PA0/PA1).
 * Ires (PA6) is a residual-current CT input connected directly to the pin
 * (no op-amp, no mid-scale bias): its legitimate zero-current level is the
 * low rail (raw ≈ 0). Only the high rail remains a saturation indication,
 * so the CT is "zero-level qualified" by construction on this bench
 * (diagnostic zero-sequence, not used by protection). */
static bool adc_bipolar_sample_is_usable(uint16_t raw)
{
    return raw > ADC_RAW_SAT_LOW && raw < ADC_RAW_SAT_HIGH;
}

static bool adc_ct_sample_is_usable(uint16_t raw)
{
    return raw < ADC_RAW_SAT_HIGH;
}

/* A seqlock protects frame readers from observing a partial ISR update. */
static volatile uint32_t frame_lock;
static volatile uint32_t frame_sequence;
static volatile AdcFrame latest_frame;
static volatile AdcStats adc_stats;

static volatile uint16_t offset_idc1;
static volatile uint16_t offset_idc2;
static volatile uint16_t offset_ct;
static volatile uint8_t offsets_valid;
static volatile uint8_t control_admitted;
static volatile uint8_t expected_sector;
static volatile uint8_t expected_window;
static volatile uint8_t expected_window_valid;

static int adc_wait_set(volatile uint32_t *reg, uint32_t mask)
{
    uint32_t n = ADC_WAIT_CYCLES;
    while (((*reg & mask) == 0u) && (n-- != 0u)) { }
    return (n == 0u) ? -1 : 0;
}

static int adc_wait_clear(volatile uint32_t *reg, uint32_t mask)
{
    uint32_t n = ADC_WAIT_CYCLES;
    while (((*reg & mask) != 0u) && (n-- != 0u)) { }
    return (n == 0u) ? -1 : 0;
}

static int32_t calc_dc_shunt_ma(uint16_t raw, uint16_t offset)
{
    const int32_t diff = (int32_t)raw - (int32_t)offset;
    return (int32_t)(((int64_t)diff * ADC_VREF_MV * 1000000LL) /
                     ((int64_t)ADC_MAX_CODE * ADC_DC_SHUNT_UV_PER_A));
}

static int32_t calc_ct_ma(uint16_t raw, uint16_t offset)
{
    const int32_t diff = (int32_t)raw - (int32_t)offset;
    return (int32_t)(((int64_t)diff * ADC_VREF_MV * 1000000LL) /
                     ((int64_t)ADC_MAX_CODE * ADC_CT_UV_PER_A));
}

static int32_t calc_vbus_mv(uint16_t raw)
{
    return (int32_t)(((int64_t)raw * ADC_VREF_MV * ADC_VBUS_DIVIDER) /
                     ADC_MAX_CODE);
}

static uint32_t adc_timestamp_cycles(void)
{
    /* CYCCNT is optional diagnostic data. It may be zero until enabled. */
    return DWT->CYCCNT;
}

static void adc_publish(const AdcFrame *frame)
{
    AdcFrame local = *frame;

    frame_lock++;             /* odd: write in progress */
    __DMB();
    local.sequence = ++frame_sequence;
    latest_frame = local;
    __DMB();
    frame_lock++;             /* even: coherent frame available */
}

static void adc_publish_error(AdcFrameStatus status)
{
    AdcFrame frame;

    frame.raw_idc1 = 0u;
    frame.raw_idc2 = 0u;
    frame.raw_ct = 0u;
    frame.raw_vbus = 0u;
    frame.idc1_ma = 0;
    frame.idc2_ma = 0;
    frame.ict_ma = 0;
    frame.vbus_mv = 0;
    frame.timestamp_cycles = adc_timestamp_cycles();
    frame.tim1_sector = expected_sector;
    frame.sample_window = expected_window;
    frame.status = status;
    adc_stats.invalid_frames++;
    adc_publish(&frame);
}

static void adc_clear_injected_flags(void)
{
    ADC1->ISR = ADC_ISR_JEOS | ADC_ISR_JQOVF | ADC_ISR_OVR;
    ADC2->ISR = ADC_ISR_JEOS | ADC_ISR_JQOVF | ADC_ISR_OVR;
}

static int adc_disable(ADC_TypeDef *adc)
{
    if ((adc->CR & ADC_CR_ADEN) == 0u) return 0;
    if (adc->CR & (ADC_CR_ADSTART | ADC_CR_JADSTART)) return -1;
    adc->CR |= ADC_CR_ADDIS;
    return adc_wait_clear(&adc->CR, ADC_CR_ADEN);
}

static int adc_calibrate_hw(ADC_TypeDef *adc)
{
    adc->CR &= ~ADC_CR_DEEPPWD;
    adc->CR |= ADC_CR_ADVREGEN;

    /* Datasheet regulator startup delay. The loop is only used at boot. */
    for (volatile uint32_t n = ADC_WAIT_CYCLES / 10u; n != 0u; --n) { }

    adc->CR |= ADC_CR_ADCAL;
    return adc_wait_clear(&adc->CR, ADC_CR_ADCAL);
}

static int adc_enable(ADC_TypeDef *adc)
{
    adc->ISR = ADC_ISR_ADRDY;
    adc->CR |= ADC_CR_ADEN;
    return adc_wait_set(&adc->ISR, ADC_ISR_ADRDY);
}

static void adc_set_sample_time(ADC_TypeDef *adc, uint32_t channel)
{
    /* Use long sampling initially. It must be shortened only after measuring
     * source settling and trigger-window timing on the physical board. */
    if (channel <= 9u) {
        const uint32_t shift = channel * 3u;
        adc->SMPR1 = (adc->SMPR1 & ~(7u << shift)) | (7u << shift);
    } else {
        const uint32_t shift = (channel - 10u) * 3u;
        adc->SMPR2 = (adc->SMPR2 & ~(7u << shift)) | (7u << shift);
    }
}

static uint32_t adc_jsqr(uint32_t jl, uint32_t q1, uint32_t q2, uint32_t q3)
{
    return (jl << ADC_JSQR_JL_Pos) |
           (ADC_INJ_TRGO_SEL << ADC_JSQR_JEXTSEL_Pos) |
           (ADC_INJ_RISING_EDGE << ADC_JSQR_JEXTEN_Pos) |
           (q1 << ADC_JSQR_JSQ1_Pos) |
           (q2 << ADC_JSQR_JSQ2_Pos) |
           (q3 << ADC_JSQR_JSQ3_Pos);
}

static int adc_regular_read(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
#ifdef ADC_HOST_TEST
    extern int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel,
                                   uint16_t *out);
    return ADC_HostRegularRead(adc, channel, out);
#else
    uint32_t n;

    if ((adc->CR & ADC_CR_JADSTART) != 0u) return -1;
    if ((adc->CR & ADC_CR_ADSTART) != 0u) return -1;

    adc->SQR1 = channel << ADC_SQR1_SQ1_Pos;
    adc->ISR = ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR;
    adc->CR |= ADC_CR_ADSTART;

    n = ADC_WAIT_CYCLES;
    while ((adc->ISR & ADC_ISR_EOC) == 0u) {
        if (n-- == 0u) {
            adc_stats.timeout_count++;
            adc->CR |= ADC_CR_ADSTP;
            (void)adc_wait_clear(&adc->CR, ADC_CR_ADSTP);
            return -1;
        }
    }
    *out = (uint16_t)adc->DR;
    return 0;
#endif
}

int ADC_Init(void)
{
    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN;
    (void)RCC->AHB2ENR;

    /* ADC registers are accessed only after ADC12 clock enable. */
    if (adc_disable(ADC1) != 0 || adc_disable(ADC2) != 0) return -1;

    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST;
    RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST;

    /* CKMODE=11: HCLK/4 = 42.5 MHz at 170 MHz HCLK. DUAL is set only by
     * ADC_InjectedInit while both ADC instances remain disabled. */
    ADC12_COMMON->CCR = (3u << ADC_CCR_CKMODE_Pos);

    ADC1->CR = 0u;
    ADC2->CR = 0u;
    ADC1->CFGR = 0u;
    ADC2->CFGR = 0u;
    ADC1->CFGR2 = 0u;
    ADC2->CFGR2 = 0u;
    ADC1->SMPR1 = ADC1->SMPR2 = 0u;
    ADC2->SMPR1 = ADC2->SMPR2 = 0u;

    if (adc_calibrate_hw(ADC1) != 0 || adc_calibrate_hw(ADC2) != 0) return -1;

    frame_lock = 0u;
    frame_sequence = 0u;
    offsets_valid = 0u;
    control_admitted = 0u;
    expected_window_valid = 0u;
    expected_sector = 0u;
    expected_window = 0u;
    adc_stats.valid_frames = 0u;
    adc_stats.invalid_frames = 0u;
    adc_stats.jeos_count = 0u;
    adc_stats.ovr_count = 0u;
    adc_stats.jqovf_count = 0u;
    adc_stats.desync_count = 0u;
    adc_stats.timeout_count = 0u;
    adc_stats.calibration_fail_count = 0u;

    adc_publish_error(ADC_FRAME_NOT_ARMED);
    return 0;
}

int ADC_InjectedInit(void)
{
    if ((ADC1->CR & ADC_CR_ADEN) != 0u || (ADC2->CR & ADC_CR_ADEN) != 0u) {
        return -1; /* DUAL/JSQR configuration is allowed only while disabled. */
    }

    /* ADC1 rank 1 and ADC2 rank 1 are sampled at the same injected trigger.
     * ADC2 ranks 2/3 subsequently acquire CT and Vbus. */
    adc_set_sample_time(ADC1, ADC_CH_SHUNT1);
    adc_set_sample_time(ADC2, ADC_CH_SHUNT2);
    adc_set_sample_time(ADC2, ADC_CH_CT);
    adc_set_sample_time(ADC2, ADC_CH_VBUS);

    ADC1->CFGR |= ADC_CFGR_JQDIS;
    ADC2->CFGR |= ADC_CFGR_JQDIS;
    ADC1->JSQR = adc_jsqr(0u, ADC_CH_SHUNT1, 0u, 0u);
    ADC2->JSQR = adc_jsqr(2u, ADC_CH_SHUNT2, ADC_CH_CT, ADC_CH_VBUS);

    /* RM0440 dual injected simultaneous mode: ADC1 is master, ADC2 slave.
     * Never enable multimode DMA here: injected JDRs are read in the shared
     * ADC1_2 IRQ only after ADC2 JEOS confirms both sequences completed. */
    ADC12_COMMON->CCR = (3u << ADC_CCR_CKMODE_Pos) | ADC_DUAL_INJ_SIMULT;

    adc_clear_injected_flags();

    /* ADC1 JEOS is deliberately not enabled: its one-rank sequence ends before
     * ADC2 has completed CT/Vbus. ADC2 JEOS is the commit event for a frame. */
    ADC1->IER = ADC_IER_OVRIE | ADC_IER_JQOVFIE;
    ADC2->IER = ADC_IER_JEOSIE | ADC_IER_OVRIE | ADC_IER_JQOVFIE;

    if (adc_enable(ADC1) != 0 || adc_enable(ADC2) != 0) return -1;
    return 0;
}

int ADC_InjectedStart(void)
{
    if (!offsets_valid) return -1;
    if ((ADC1->CR & ADC_CR_ADEN) == 0u || (ADC2->CR & ADC_CR_ADEN) == 0u) {
        return -1;
    }

    adc_clear_injected_flags();
    /* ADC1 is the ADC12 multimode master. In dual injected simultaneous mode,
     * arming master starts the slave injected group as well; do not write
     * JADSTART to ADC2 independently. */
    ADC1->CR |= ADC_CR_JADSTART;
    return 0;
}

bool ADC_InjectedIsArmed(void)
{
    /* ADC1 is the ADC12 dual-injected master; ADC2 JADSTART must not be
     * used as an arm-state proxy in dual simultaneous mode. */
    return (ADC1->CR & ADC_CR_JADSTART) != 0u;
}

void ADC_InjectedStop(void)
{
    if (ADC_InjectedIsArmed()) {
        ADC1->CR |= ADC_CR_JADSTP;
        if (adc_wait_clear(&ADC1->CR, ADC_CR_JADSTP) != 0) adc_stats.timeout_count++;
    }
    adc_clear_injected_flags();
    adc_publish_error(ADC_FRAME_NOT_ARMED);
}

void ADC_SetExpectedWindow(uint8_t tim1_sector, uint8_t sample_window,
                           bool window_valid)
{
    expected_sector = tim1_sector;
    expected_window = sample_window;
    expected_window_valid = window_valid ? 1u : 0u;
}

void ADC_SetControlAdmission(bool admitted)
{
    control_admitted = admitted ? 1u : 0u;
}

bool ADC_ControlAdmission(void)
{
    return control_admitted != 0u;
}

int ADC_CalibrateOffsets_256(void)
{
    return ADC_CalibrateOffsets();
}

bool ADC_OffsetsAreValid(void)
{
    return offsets_valid != 0u;
}

static AdcFrameStatus adc_frame_status(uint16_t raw1, uint16_t raw2,
                                        uint16_t rawct, uint16_t rawvbus)
{
    if (!adc_bipolar_sample_is_usable(raw1) ||
        !adc_bipolar_sample_is_usable(raw2) ||
        !adc_ct_sample_is_usable(rawct) ||
        !adc_bipolar_sample_is_usable(rawvbus)) {
        return ADC_FRAME_ADC_SATURATED;
    }
    if (!offsets_valid) return ADC_FRAME_CALIBRATION_INVALID;
    if (!expected_window_valid) return ADC_FRAME_WINDOW_INVALID;
    if (!control_admitted) return ADC_FRAME_MAPPING_UNVERIFIED;
    return ADC_FRAME_VALID;
}

static void adc_commit_injected_frame(void)
{
    AdcFrame frame;

    frame.raw_idc1 = (uint16_t)ADC1->JDR1;
    frame.raw_idc2 = (uint16_t)ADC2->JDR1;
    frame.raw_ct = (uint16_t)ADC2->JDR2;
    frame.raw_vbus = (uint16_t)ADC2->JDR3;
    frame.idc1_ma = calc_dc_shunt_ma(frame.raw_idc1, offset_idc1);
    frame.idc2_ma = calc_dc_shunt_ma(frame.raw_idc2, offset_idc2);
    frame.ict_ma = calc_ct_ma(frame.raw_ct, offset_ct);
    frame.vbus_mv = calc_vbus_mv(frame.raw_vbus);
    frame.timestamp_cycles = adc_timestamp_cycles();
    frame.tim1_sector = expected_sector;
    frame.sample_window = expected_window;
    frame.status = adc_frame_status(frame.raw_idc1, frame.raw_idc2,
                                    frame.raw_ct, frame.raw_vbus);

    adc_stats.jeos_count++;
    if (frame.status == ADC_FRAME_VALID) adc_stats.valid_frames++;
    else adc_stats.invalid_frames++;
    adc_publish(&frame);
}

bool ADC_InjectedIrq(void)
{
    const uint32_t isr1 = ADC1->ISR;
    const uint32_t isr2 = ADC2->ISR;
    const uint32_t err1 = isr1 & (ADC_ISR_OVR | ADC_ISR_JQOVF);
    const uint32_t err2 = isr2 & (ADC_ISR_OVR | ADC_ISR_JQOVF);

    if ((err1 | err2 | (isr2 & ADC_ISR_JEOS)) == 0u) return false;

    if ((err1 | err2) != 0u) {
        if ((err1 | err2) & ADC_ISR_OVR) adc_stats.ovr_count++;
        if ((err1 | err2) & ADC_ISR_JQOVF) adc_stats.jqovf_count++;
        /* A conversion error invalidates this frame. Clear JEOS from both ADCs
         * as well, so a partly completed sequence can never be committed on
         * the next ADC2 interrupt as a false simultaneous pair. */
        ADC1->ISR = err1 | (isr1 & ADC_ISR_JEOS);
        ADC2->ISR = err2 | (isr2 & ADC_ISR_JEOS);
        adc_publish_error(((err1 | err2) & ADC_ISR_JQOVF) ?
                          ADC_FRAME_QUEUE_OVERRUN : ADC_FRAME_OVERRUN);
        return true;
    }

    /* ADC2 JEOS is the commit interrupt. ADC1 must already have completed its
     * one-rank shunt conversion; otherwise no simultaneous pair is published. */
    if ((isr1 & ADC_ISR_JEOS) == 0u) {
        ADC2->ISR = ADC_ISR_JEOS;
        adc_stats.desync_count++;
        adc_publish_error(ADC_FRAME_DESYNCHRONIZED);
        return true;
    }

    ADC1->ISR = ADC_ISR_JEOS;
    ADC2->ISR = ADC_ISR_JEOS;
    adc_commit_injected_frame();
    return true;
}

bool ADC_GetLatestFrame(AdcFrame *out)
{
    if (out == 0) return false;

    for (uint32_t attempt = 0u; attempt < 3u; ++attempt) {
        const uint32_t before = frame_lock;
        if (before & 1u) continue;
        __DMB();
        *out = latest_frame;
        __DMB();
        if (before == frame_lock && (frame_lock & 1u) == 0u) return true;
    }
    return false;
}

bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return (frame != 0) && (frame->status == ADC_FRAME_VALID) &&
           (frame->sequence != 0u);
}

void ADC_GetStats(AdcStats *out)
{
    if (out == 0) return;
    *out = adc_stats;
}

int ADC_CalibrateOffsets(void)
{
    uint32_t sum1 = 0u, sum2 = 0u, sumct = 0u;
    uint32_t valid_dc = 0u, valid_ct = 0u;

    if ((ADC1->CR & ADC_CR_JADSTART) || (ADC2->CR & ADC_CR_JADSTART)) {
        return -1;
    }

    for (uint32_t i = 0u; i < ADC_OFFSET_SAMPLES; ++i) {
        uint16_t r1, r2, rct;
        if (adc_regular_read(ADC1, ADC_CH_SHUNT1, &r1) != 0 ||
            adc_regular_read(ADC2, ADC_CH_SHUNT2, &r2) != 0 ||
            adc_regular_read(ADC2, ADC_CH_CT, &rct) != 0) {
            continue;
        }
        if (!adc_bipolar_sample_is_usable(r1) ||
            !adc_bipolar_sample_is_usable(r2)) {
            continue;
        }

        sum1 += r1;
        sum2 += r2;
        valid_dc++;
        if (adc_ct_sample_is_usable(rct)) {
            sumct += rct;
            valid_ct++;
        }
    }

    if (valid_dc < (ADC_OFFSET_SAMPLES / 2u)) {
        offsets_valid = 0u;
        adc_stats.calibration_fail_count++;
        adc_publish_error(ADC_FRAME_CALIBRATION_INVALID);
        return -1;
    }

    /* DC-link current readings remain useful to service/protection telemetry
     * even if the independent residual-current CT is presently at a rail.
     * Do not mark offsets valid until all control inputs, including Ires, are
     * qualified: PWM/FOC admission stays fail-closed. */
    offset_idc1 = (uint16_t)(sum1 / valid_dc);
    offset_idc2 = (uint16_t)(sum2 / valid_dc);
    if (valid_ct < (ADC_OFFSET_SAMPLES / 2u)) {
        offsets_valid = 0u;
        adc_stats.calibration_fail_count++;
        adc_publish_error(ADC_FRAME_CALIBRATION_INVALID);
        return -1;
    }

    offset_ct = (uint16_t)(sumct / valid_ct);
    offsets_valid = 1u;
    return 0;
}

int ADC_StartConversion(void)
{
    uint16_t r1, r2, rct, rvbus;
    AdcFrame frame;

    if ((ADC1->CR & ADC_CR_JADSTART) || (ADC2->CR & ADC_CR_JADSTART)) {
        return -1;
    }
    if (adc_regular_read(ADC1, ADC_CH_SHUNT1, &r1) != 0 ||
        adc_regular_read(ADC2, ADC_CH_SHUNT2, &r2) != 0 ||
        adc_regular_read(ADC2, ADC_CH_CT, &rct) != 0 ||
        adc_regular_read(ADC2, ADC_CH_VBUS, &rvbus) != 0) {
        adc_publish_error(ADC_FRAME_SERVICE_BUSY);
        return -1;
    }

    frame.raw_idc1 = r1;
    frame.raw_idc2 = r2;
    frame.raw_ct = rct;
    frame.raw_vbus = rvbus;
    frame.idc1_ma = calc_dc_shunt_ma(r1, offset_idc1);
    frame.idc2_ma = calc_dc_shunt_ma(r2, offset_idc2);
    frame.ict_ma = calc_ct_ma(rct, offset_ct);
    frame.vbus_mv = calc_vbus_mv(rvbus);
    frame.timestamp_cycles = adc_timestamp_cycles();
    frame.tim1_sector = expected_sector;
    frame.sample_window = expected_window;
    frame.status = ADC_FRAME_SERVICE_BUSY;
    adc_stats.invalid_frames++;
    adc_publish(&frame);
    return 0;
}

int ADC_ServiceReadVbus(void)
{
    uint16_t raw;
    if ((ADC1->CR & ADC_CR_JADSTART) || (ADC2->CR & ADC_CR_JADSTART)) return -1;
    if (adc_regular_read(ADC2, ADC_CH_VBUS, &raw) != 0) return -1;

    /* Preserve current raw fields but make this value explicitly service-only. */
    AdcFrame frame;
    if (!ADC_GetLatestFrame(&frame)) return -1;
    frame.raw_vbus = raw;
    frame.vbus_mv = calc_vbus_mv(raw);
    frame.timestamp_cycles = adc_timestamp_cycles();
    frame.status = ADC_FRAME_SERVICE_BUSY;
    adc_stats.invalid_frames++;
    adc_publish(&frame);
    return 0;
}

void ADC_ReadInjected(void)
{
    if ((ADC1->ISR & ADC_ISR_JEOS) && (ADC2->ISR & ADC_ISR_JEOS)) {
        ADC1->ISR = ADC_ISR_JEOS;
        ADC2->ISR = ADC_ISR_JEOS;
        adc_commit_injected_frame();
    } else {
        adc_stats.desync_count++;
        adc_publish_error(ADC_FRAME_DESYNCHRONIZED);
    }
}

uint16_t ADC_GetRawI1(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.raw_idc1 : 0u; }
uint16_t ADC_GetRawI2(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.raw_idc2 : 0u; }
uint16_t ADC_GetRawIres(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.raw_ct : 0u; }
uint16_t ADC_GetRawVbus(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.raw_vbus : 0u; }
uint16_t ADC_GetOffsetI1(void) { return offset_idc1; }
uint16_t ADC_GetOffsetI2(void) { return offset_idc2; }
uint16_t ADC_GetOffsetIres(void) { return offset_ct; }
int32_t ADC_GetI1_mA(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.idc1_ma : 0; }
int32_t ADC_GetI2_mA(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.idc2_ma : 0; }
int32_t ADC_GetIres_mA(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.ict_ma : 0; }
int32_t ADC_GetVbus_mV(void) { AdcFrame f; return ADC_GetLatestFrame(&f) ? f.vbus_mv : 0; }
uint32_t ADC_GetOvrCount(void) { return adc_stats.ovr_count; }
uint32_t ADC_GetJeosCount(void) { return adc_stats.jeos_count; }
uint32_t ADC_GetJqovfCount(void) { return adc_stats.jqovf_count; }
uint32_t ADC_GetTimeoutCount(void) { return adc_stats.timeout_count; }
void ADC_WaitForEOC(void) { }
