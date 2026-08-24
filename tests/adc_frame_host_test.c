#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "adc.h"
#include "stm32g474xx.h"

static uint16_t host_regular_i1;
static uint16_t host_regular_i2;
static uint16_t host_regular_ires;

int ADC_HostRegularRead(ADC_TypeDef *adc, uint32_t channel, uint16_t *out)
{
    if (out == 0) return -1;
    if (adc == ADC1 && channel == 1u) {
        *out = host_regular_i1;
        return 0;
    }
    if (adc == ADC2 && channel == 2u) {
        *out = host_regular_i2;
        return 0;
    }
    if (adc == ADC2 && channel == 3u) {
        *out = host_regular_ires;
        return 0;
    }
    return -1;
}

int main(void)
{
    AdcFrame frame;
    AdcStats stats;

    memset(&host_adc1, 0, sizeof(host_adc1));
    memset(&host_adc2, 0, sizeof(host_adc2));
    memset(&host_adc12_common, 0, sizeof(host_adc12_common));
    memset(&host_rcc, 0, sizeof(host_rcc));
    memset(&host_dwt, 0, sizeof(host_dwt));

    /* A complete pair is committed only when ADC2 JEOS and ADC1 JEOS exist. */
    ADC_SetExpectedWindow(4u, 2u, true);
    host_adc1.JDR1 = 2048u;
    host_adc2.JDR1 = 2050u;
    host_adc2.JDR2 = 2049u;
    host_adc2.JDR3 = 1000u;
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;

    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.raw_idc1 == 2048u);
    assert(frame.raw_idc2 == 2050u);
    assert(frame.raw_ct == 2049u);
    assert(frame.raw_vbus == 1000u);
    assert(frame.tim1_sector == 4u);
    assert(frame.sample_window == 2u);
    /* No calibration/admission in a host test: control is intentionally blocked. */
    assert(frame.status == ADC_FRAME_CALIBRATION_INVALID);
    assert(!ADC_FrameIsControlValid(&frame));

    /* CT is connected directly to PA6 (no op-amp, no mid-scale bias): its
     * legitimate zero-current level is the low rail (raw ≈ 0).  Calibration
     * therefore succeeds with offset_ires≈0, and raw_ct=0 no longer causes
     * ADC_SATURATED. Protection ignores Ires; fail-closed for I1/I2/VBUS
     * remains intact via the separate admission gate. */
    host_regular_i1 = 2041u;
    host_regular_i2 = 2073u;
    host_regular_ires = 0u;
    assert(ADC_CalibrateOffsets() == 0);
    assert(ADC_OffsetsAreValid());
    assert(ADC_GetOffsetI1() == 2041u);
    assert(ADC_GetOffsetI2() == 2073u);
    assert(ADC_GetOffsetIres() == 0u);

    ADC_SetControlAdmission(true);
    host_adc1.JDR1 = 2041u;
    host_adc2.JDR1 = 2073u;
    host_adc2.JDR2 = 0u;
    host_adc2.JDR3 = 1000u;
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.raw_ct == 0u);
    assert(frame.status == ADC_FRAME_VALID);
    assert(ADC_FrameIsControlValid(&frame));
    ADC_SetControlAdmission(false);

    /* A mid-scale Ires also calibrates and produces a valid frame. */
    host_regular_ires = 2049u;
    assert(ADC_CalibrateOffsets() == 0);
    assert(ADC_OffsetsAreValid());
    assert(ADC_GetOffsetI1() == 2041u);
    assert(ADC_GetOffsetI2() == 2073u);
    assert(ADC_GetOffsetIres() == 2049u);

    ADC_SetControlAdmission(true);
    host_adc2.JDR2 = 2049u;
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.status == ADC_FRAME_VALID);
    assert(ADC_FrameIsControlValid(&frame));
    ADC_SetControlAdmission(false);

    /* Full-scale Ires remains a hardware saturation indication. */
    ADC_SetControlAdmission(true);
    host_adc2.JDR2 = 4094u;
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.status == ADC_FRAME_ADC_SATURATED);
    ADC_SetControlAdmission(false);

    /* A low-rail DC-link shunt remains fail-closed in both injected-frame and
     * calibration paths. Its previous offset remains observable but invalid. */
    host_adc1.JDR1 = 1u;
    host_adc2.JDR2 = 2049u;
    host_adc1.ISR = ADC_ISR_JEOS;
    host_adc2.ISR = ADC_ISR_JEOS;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.status == ADC_FRAME_ADC_SATURATED);

    host_regular_i1 = 1u;
    assert(ADC_CalibrateOffsets() == -1);
    assert(!ADC_OffsetsAreValid());
    assert(ADC_GetOffsetI1() == 2041u);
    assert(ADC_GetOffsetI2() == 2073u);
    assert(ADC_GetOffsetIres() == 2049u);

    /* An ADC overrun publishes an invalid frame and increments diagnostics. */
    host_adc1.ISR = ADC_ISR_OVR;
    host_adc2.ISR = 0u;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.status == ADC_FRAME_OVERRUN);
    ADC_GetStats(&stats);
    assert(stats.ovr_count == 1u);
    assert(stats.desync_count == 0u);
<<<<<<< HEAD
    assert(stats.calibration_fail_count == 1u);
=======
    assert(stats.calibration_fail_count == 2u);
>>>>>>> origin/ai4/adc-calibration-ct-boundary

    puts("adc_frame_host_test: PASS");
    return 0;
}
