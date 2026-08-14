#ifndef ADC_H
#define ADC_H

#include <stdbool.h>
#include <stdint.h>

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
    uint16_t raw_idc1;
    uint16_t raw_idc2;
    uint16_t raw_ct;
    uint16_t raw_vbus;
    int32_t idc1_ma;
    int32_t idc2_ma;
    int32_t ict_ma;
    int32_t vbus_mv;
    uint32_t sequence;
    uint32_t timestamp_cycles;
    uint8_t tim1_sector;
    uint8_t sample_window;
    AdcFrameStatus status;
} AdcFrame;

int ADC_InjectedStart(void);
void ADC_InjectedStop(void);
bool ADC_InjectedIsArmed(void);
bool ADC_FrameIsControlValid(const AdcFrame *frame);
int ADC_CalibrateOffsets(void);
bool ADC_OffsetsAreValid(void);
int ADC_StartConversion(void);
int32_t ADC_GetVbus_mV(void);
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid);
void ADC_SetControlAdmission(bool enabled);

#endif /* ADC_H */
