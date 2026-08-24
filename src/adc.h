#ifndef ADC_H
#define ADC_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Board mapping assumed by this module:
 *   ADC1 injected rank 1: PA0 / ADC1_IN1  -> DC-link shunt #1
 *   ADC2 injected rank 1: PA1 / ADC2_IN2  -> DC-link shunt #2
 *   ADC2 injected rank 2: PA6 / ADC2_IN3  -> CT (diagnostic until qualified)
 *   ADC2 injected rank 3: PC4 / ADC2_IN5  -> Vbus
 *
 * ADC1 and ADC2 operate in ADC12 dual injected simultaneous mode. The two
 * shunts therefore share one trigger aperture; CT and Vbus complete later in
 * the ADC2 injected sequence. PA0/PA1 mapping and all scale factors must be
 * verified against the actual board before ADC_SetControlAdmission(true).
 */

#ifndef ADC_VREF_MV
#define ADC_VREF_MV                 3300L
#endif
#ifndef ADC_MAX_CODE
#define ADC_MAX_CODE                4095L
#endif
#ifndef ADC_DC_SHUNT_UV_PER_A
#define ADC_DC_SHUNT_UV_PER_A       63000L
#endif
#ifndef ADC_CT_UV_PER_A
#define ADC_CT_UV_PER_A             100000L
#endif
#ifndef ADC_VBUS_DIVIDER
#define ADC_VBUS_DIVIDER            125L
#endif
#ifndef ADC_OFFSET_SAMPLES
#define ADC_OFFSET_SAMPLES          256U
#endif
#ifndef ADC_WAIT_CYCLES
#define ADC_WAIT_CYCLES             1000000UL
#endif

typedef enum {
    ADC_FRAME_NONE = 0,
    ADC_FRAME_VALID,
    ADC_FRAME_NOT_ARMED,
    ADC_FRAME_JEOS_TIMEOUT,
    ADC_FRAME_OVERRUN,
    ADC_FRAME_QUEUE_OVERRUN,
    ADC_FRAME_DESYNCHRONIZED,
    ADC_FRAME_WINDOW_INVALID,
    ADC_FRAME_ADC_SATURATED,
    ADC_FRAME_CALIBRATION_INVALID,
    ADC_FRAME_MAPPING_UNVERIFIED,
    ADC_FRAME_SERVICE_BUSY
} AdcFrameStatus;

typedef struct {
    /* Raw ADC samples. raw_idc1/raw_idc2 are sampled simultaneously. */
    uint16_t raw_idc1;
    uint16_t raw_idc2;
    uint16_t raw_ct;
    uint16_t raw_vbus;

    /* Offset-corrected engineering values. CT is diagnostic by default. */
    int32_t idc1_ma;
    int32_t idc2_ma;
    int32_t ict_ma;
    int32_t vbus_mv;

    /* Monotonic even sequence plus ISR timestamp. A new good or bad frame
     * increments sequence; consumers reject an unchanged sequence. */
    uint32_t sequence;
    uint32_t timestamp_cycles;

    /* Context supplied by the PWM/modulation scheduler before its trigger. */
    uint8_t tim1_sector;
    uint8_t sample_window;
    AdcFrameStatus status;
} AdcFrame;

typedef struct {
    uint32_t valid_frames;
    uint32_t invalid_frames;
    uint32_t jeos_count;
    uint32_t ovr_count;
    uint32_t jqovf_count;
    uint32_t desync_count;
    uint32_t timeout_count;
    uint32_t calibration_fail_count;
} AdcStats;

/* Initialise ADC1/ADC2, but do not arm injected conversion or admit FOC. */
int ADC_Init(void);

/* Configure dual injected simultaneous acquisition. ADC1=shunt1;
 * ADC2=shunt2, CT, Vbus. Call only while both ADCs are disabled. */
int ADC_InjectedInit(void);

/* Arm/disarm the hardware-triggered injected groups. Start is safe only after
 * successful offset calibration. Stop makes the next frame invalid. */
int  ADC_InjectedStart(void);
void ADC_InjectedStop(void);
bool ADC_InjectedIsArmed(void);   /* ADC1 — master dual-injected (JADSTART только у master) */

/* Call from ADC1_2_IRQHandler. It captures both JEOS flags, records errors
 * and atomically publishes a complete frame. Returns true if IRQ was owned. */
bool ADC_InjectedIrq(void);

/* Read a coherent snapshot. Returns false only if writer contention persists.
 * `sequence` permits caller-side freshness checks across control cycles. */
bool ADC_GetLatestFrame(AdcFrame *out);
bool ADC_FrameIsControlValid(const AdcFrame *frame);
void ADC_GetStats(AdcStats *out);

/* The modulation scheduler must set the context before arming the trigger for
 * that PWM period. A false `window_valid` blocks FOC even if conversion works. */
void ADC_SetExpectedWindow(uint8_t tim1_sector, uint8_t sample_window,
                           bool window_valid);

/* Remains false until shunt mapping, gain, polarity and valid windows are
 * proven. This is intentionally a separate gate from offset calibration. */
void ADC_SetControlAdmission(bool admitted);
bool ADC_ControlAdmission(void);

/* Offset calibration is allowed only when PWM and injected conversion are off.
 * It returns 0 only after enough qualified zero-current samples for both
 * DC-link shunts and Ires. If Ires is unqualified, the measured DC offsets
 * may still be retained for service telemetry, but offsets-valid remains false
 * and all PWM/FOC admission remains fail-closed. */
int  ADC_CalibrateOffsets(void);
int  ADC_CalibrateOffsets_256(void); /* compatibility alias; uses ADC_OFFSET_SAMPLES */
bool ADC_OffsetsAreValid(void);

/* Foreground-only regular conversion for service/boot telemetry. It returns
 * -1 without touching ADC if injected acquisition is armed. It must not be
 * used for FOC, current reconstruction or energised autotune sampling. */
int ADC_StartConversion(void);
int ADC_ServiceReadVbus(void);

/* Compatibility getters expose the most recently published frame only. They
 * do not imply a valid control current and must not be new FOC inputs. */
void     ADC_ReadInjected(void);
uint16_t ADC_GetRawI1(void);
uint16_t ADC_GetRawI2(void);
uint16_t ADC_GetRawIres(void);
uint16_t ADC_GetRawVbus(void);
uint16_t ADC_GetOffsetI1(void);
uint16_t ADC_GetOffsetI2(void);
uint16_t ADC_GetOffsetIres(void);
int32_t  ADC_GetI1_mA(void);
int32_t  ADC_GetI2_mA(void);
int32_t  ADC_GetIres_mA(void);
int32_t  ADC_GetVbus_mV(void);
uint32_t ADC_GetOvrCount(void);
uint32_t ADC_GetJeosCount(void);
uint32_t ADC_GetJqovfCount(void);
uint32_t ADC_GetTimeoutCount(void);
void     ADC_WaitForEOC(void);

#endif /* ADC_H */
