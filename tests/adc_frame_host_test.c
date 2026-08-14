#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "adc.h"
#include "stm32g474xx.h"

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

    /* An ADC overrun publishes an invalid frame and increments diagnostics. */
    host_adc1.ISR = ADC_ISR_OVR;
    host_adc2.ISR = 0u;
    assert(ADC_InjectedIrq());
    assert(ADC_GetLatestFrame(&frame));
    assert(frame.status == ADC_FRAME_OVERRUN);
    ADC_GetStats(&stats);
    assert(stats.ovr_count == 1u);
    assert(stats.desync_count == 0u);

    puts("adc_frame_host_test: PASS");
    return 0;
}
