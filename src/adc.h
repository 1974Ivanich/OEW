#ifndef ADC_H
#define ADC_H

#include <stdbool.h>
#include <stdint.h>

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
/* Предел raw для калибровки смещения VBUS: вход должен быть на нуле.
 * 40 отсч ~ 4 В. Выше - калибровка отклоняется (rc=-2), прежнее значение
 * не изменяется. */
#ifndef ADC_VBUS_OFFSET_MAX_RAW
#define ADC_VBUS_OFFSET_MAX_RAW     40U
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

int ADC_Init(void);
int ADC_InjectedInit(void);
int ADC_InjectedStart(void);
void ADC_InjectedStop(void);
bool ADC_InjectedIsArmed(void);
bool ADC_InjectedIrq(void);
bool ADC_GetLatestFrame(AdcFrame *out);
bool ADC_FrameIsControlValid(const AdcFrame *frame);
void ADC_GetStats(AdcStats *out);

void ADC_SetExpectedWindow(uint8_t tim1_sector, uint8_t sample_window,
                           bool window_valid);
void ADC_SetControlAdmission(bool admitted);
bool ADC_ControlAdmission(void);

int ADC_CalibrateOffsets(void);
int ADC_CalibrateOffsets_256(void);
bool ADC_OffsetsAreValid(void);

/* Аддитивное смещение канала VBUS. Измеряется при 0 В на входе отдельной
 * командой (не вместе с c: калибровка токов идёт при поданном звене).
 * Пока смещение не измерено, преобразование идёт с offset = 0, то есть
 * поведение совпадает с прежним. */
int      ADC_CalibrateVbusOffset(void);
bool     ADC_VbusOffsetIsValid(void);
uint16_t ADC_GetOffsetVbus(void);
/* Чистое преобразование raw -> мВ: (raw - offset) * VREF * DIVIDER / MAX_CODE,
 * со знаковым промежуточным и насыщением снизу в 0. */
int32_t  ADC_VbusMvFromRaw(uint16_t raw, uint16_t offset);

/* Окно сбора калибровочных данных шунта (команда ci, TZ_CURRENT_SHUNT_CALIBRATION.md):
 * только агрегация сэмплов, коэффициенты считаются offline. rc: 0 = OK,
 * -1 = ADC занят/не тот канал. */
#define ADC_CI_WINDOW_SAMPLES       256U
typedef struct {
    uint32_t samples;
    uint32_t raw_avg;    /* среднее за окно (округление к ближайшему) */
    uint16_t raw_min;
    uint16_t raw_max;
    uint32_t dt_ms;      /* длительность окна по sys_tick_ms */
    int32_t  vbus_mv;
} ADC_CiWindow;
int      ADC_CiCollectWindow(uint32_t adc_channel, ADC_CiWindow *out);

/* Canonical signatures used by OewMapIdentity. The configuration signature
 * covers the active ADC clock/mode, resolution, sample-time registers and
 * injected channel sequence. The calibration signature covers engineering
 * scale constants and the currently qualified offset calibration. */
uint32_t ADC_GetConfigSignature(void);
uint32_t ADC_GetCurrentCalibrationSignature(void);

int ADC_StartConversion(void);
int ADC_ServiceReadVbus(void);
/* Regular software-triggered VBUS conversion, in mV; -1 on busy/timeout. */
int32_t ADC_ReadVbusRegularMv(void);

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
