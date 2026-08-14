#ifndef MAP_CAPTURE_H
#define MAP_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

#include "adc.h"

#ifndef MAP_CAPTURE_RING_CAPACITY
#define MAP_CAPTURE_RING_CAPACITY 64u
#endif

#ifndef MAP_CAPTURE_MAX_PULSES
/* Explicit session ceiling; foreground may drain records while capture runs.
 * The ring itself remains bounded and terminal-stops on consumer starvation. */
#define MAP_CAPTURE_MAX_PULSES 256u
#endif

/* Trigger identity of the TIM1 TRGO → ADC12 injected aperture. Recorded in
 * every MapCapturePwmSnapshot and matched against the request; a mismatch
 * (revision drift after a config change) terminates the session. */
#ifndef OEW_ADC_TRIGGER_REVISION
#define OEW_ADC_TRIGGER_REVISION 1u
#endif

typedef enum {
    MAP_CAPTURE_IDLE = 0,
    MAP_CAPTURE_ARMED,
    MAP_CAPTURE_RUNNING,
    MAP_CAPTURE_COMPLETE,
    MAP_CAPTURE_ABORTED,
    MAP_CAPTURE_FAULTED
} MapCaptureState;

typedef enum {
    MAP_CAPTURE_OK = 0,
    MAP_CAPTURE_HW_INTERLOCK_MISSING = -1,
    MAP_CAPTURE_CONTROL_ACTIVE = -2,
    MAP_CAPTURE_FAULT_LATCHED = -3,
    MAP_CAPTURE_OFFSET_INVALID = -4,
    MAP_CAPTURE_BAD_REQUEST = -5,
    MAP_CAPTURE_ADC_ARM_FAILED = -6,
    MAP_CAPTURE_PWM_START_FAILED = -7,
    MAP_CAPTURE_SNAPSHOT_FAILED = -8,
    MAP_CAPTURE_TIMEOUT = -9,
    MAP_CAPTURE_ABORTED_BY_USER = -10,
    MAP_CAPTURE_ADC_FAULT = -11,
    MAP_CAPTURE_LIMIT_EXCEEDED = -12,
    MAP_CAPTURE_BUFFER_OVERFLOW = -13,
    MAP_CAPTURE_NOT_ACTIVE = -14,
    MAP_CAPTURE_HOOKS_INVALID = -15,
    MAP_CAPTURE_PROTECTION_FAULT = -16,
    MAP_CAPTURE_TRIGGER_MISMATCH = -17
} MapCaptureStatus;

typedef struct {
    uint32_t capture_id;
    uint16_t pulse_count;       /* exact maximum number of accepted frames */
    uint16_t timeout_periods;   /* foreground/period tick watchdog budget */
    int32_t max_abs_shunt_ma;   /* applies independently to idc1/idc2 */
    uint32_t min_vbus_mv;
    uint32_t max_vbus_mv;
    uint8_t sector_candidate;   /* diagnostic label only; no control admission */
    uint8_t window_candidate;   /* physical, scope-verified timing window */
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
    uint32_t trigger_revision;
} MapCaptureRequest;

typedef struct {
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
    uint16_t tim1_arr;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
    uint32_t pwm_frequency_hz;
    uint32_t trigger_revision;
} MapCapturePwmSnapshot;

typedef struct {
    AdcFrame frame;                 /* immutable raw + engineering ADC data */
    uint32_t capture_id;
    MapCapturePwmSnapshot pwm;      /* CCR state paired with this aperture */
    MapCaptureStatus fault_reason;   /* MAP_CAPTURE_OK for accepted records */
} MapCaptureRecord;

typedef struct {
    MapCaptureState state;
    MapCaptureStatus terminal_status;
    uint32_t capture_id;
    uint16_t accepted_frames;
    uint16_t dropped_records;
    uint16_t periods_elapsed;
    uint16_t records_available;
} MapCaptureStats;

typedef MapCaptureStats MapCaptureInfo; /* compatibility alias */

/* Target glue. These callbacks are the only permitted route to service PWM.
 * start_service_pwm() must use an approved PWM service API, preserve the
 * central EN-low/MOE/CEN shutdown order, emit at most request->pulse_count
 * periods, and must not call PWM_Enable().
 * stop_service_pwm() is idempotent and physically disables the bridge.
 * `hardware_interlock_healthy` must report the actual independent FAULT_N/break
 * capability, not a software estimate. */
typedef struct {
    bool (*hardware_interlock_healthy)(void);
    bool (*control_paths_inactive)(void); /* FOC, V/f, autotune all false */
    bool (*fault_latched)(void);
    /* Validate CCR/dead-time/trigger bounds against live configuration without
     * writing hardware. It must reject every uncharacterised pulse pattern. */
    bool (*validate_service_pattern)(const MapCaptureRequest *request);
    bool (*start_service_pwm)(const MapCaptureRequest *request);
    void (*stop_service_pwm)(void);
    bool (*snapshot_service_pwm)(MapCapturePwmSnapshot *out);
    void (*latch_capture_fault)(MapCaptureStatus reason);
} MapCaptureHooks;

/* Call once before any session. Hooks are immutable while a session is active. */
bool MapCapture_Init(const MapCaptureHooks *hooks);

/* Validates all preconditions and arms only the ADC12 injected master. It
 * deliberately keeps ADC_SetControlAdmission(false) and cannot arm normal FOC. */
MapCaptureStatus MapCapture_Arm(const MapCaptureRequest *request);

/* Begins the previously armed, bounded service PWM burst. */
MapCaptureStatus MapCapture_Run(void);

/* Optional convenience wrapper: MapCapture_Arm(request), then MapCapture_Run(). */
MapCaptureStatus MapCapture_Start(const MapCaptureRequest *request);

/* Call exactly once for each coherent injected AdcFrame from ADC1_2_IRQHandler.
 * Does not send UART, allocate memory or call a normal FOC/protection path. */
void MapCapture_OnAdcFrame(const AdcFrame *frame);

/* Call once per capture PWM period from a low-priority deterministic tick or
 * an explicit service-period event. It enforces the bounded timeout. */
void MapCapture_OnPeriod(void);

/* Call from the central protection/fault path after its own latch. */
void MapCapture_OnProtectionFault(void);

/* Idempotent foreground/user abort. */
MapCaptureStatus MapCapture_Abort(void);

bool MapCapture_IsActive(void);
MapCaptureStatus MapCapture_GetStatus(void);
void MapCapture_GetStats(MapCaptureStats *out);

/* Foreground-only drain. A record is copied atomically before its slot is freed. */
bool MapCapture_ConsumeRecord(MapCaptureRecord *out);

/* Compatibility aliases for early integration code. */
bool MapCapture_PopRecord(MapCaptureRecord *out);
void MapCapture_GetInfo(MapCaptureInfo *out);

#endif /* MAP_CAPTURE_H */
