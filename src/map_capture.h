#ifndef MAP_CAPTURE_H
#define MAP_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

#include "adc.h"   /* AdcFrame */

/* Bounded service-only capture path for the first OEW sector/window map
 * (методика первого съёма OEW карты §2 / «Готовность к service-only capture»).
 * НЕ является control-путём:
 *  - никогда не вызывает FOC_RunFrame и не устанавливает control admission;
 *  - публикует diagnostic raw frames (ADC_FRAME_MAPPING_UNVERIFIED допустим);
 *  - применяет ровно N фиксированных PWM-периодов и БЕЗУСЛОВНО возвращает
 *    EN/MOE/CEN/ADC в stop state (PWM_Disable — EN LOW первым);
 *  - IRQ не маскируется; OVR/JQOVF/desync/timeout/лимиты/abort → immediate stop;
 *  - raw records — immutable снапшоты в RAM ring (overflow → abort + fault),
 *    UART-выгрузка только из foreground (main loop), никогда из ADC ISR.
 * Команда доступна только в commissioning build (OEW_MAP_CAPTURE=1). */

#define MAP_CAPTURE_MAX_PULSES      4095u
#define MAP_CAPTURE_MAX_TIMEOUT_PER 4000u
#define MAP_CAPTURE_RING_SIZE       32u

/* Идентичность триггера выборки (схема TIM1 TRGO → ADC12 injected).
 * Меняется при изменении конфигурации триггера/апертуры — попадает в
 * MapCaptureRecord и в identity карты (CurrentMap_LoadMeasured). */
#ifndef OEW_ADC_TRIGGER_REVISION
#define OEW_ADC_TRIGGER_REVISION 1u
#endif

typedef enum {
    MAP_CAPTURE_OK = 0,
    MAP_CAPTURE_HW_INTERLOCK_MISSING = -1,   /* preconditions не выполнены */
    MAP_CAPTURE_CONTROL_ACTIVE       = -2,   /* FOC/V-f работают */
    MAP_CAPTURE_FAULT_LATCHED        = -3,
    MAP_CAPTURE_OFFSET_INVALID       = -4,
    MAP_CAPTURE_BAD_PATTERN          = -5,
    MAP_CAPTURE_ADC_ARM_FAILED       = -6,
    MAP_CAPTURE_TIMEOUT              = -7,
    MAP_CAPTURE_ABORTED              = -8,
    MAP_CAPTURE_LIMIT                = -9,   /* ток/Vbus вне окна сессии */
    MAP_CAPTURE_BUFFER_OVERFLOW      = -10,
    MAP_CAPTURE_FRAME_FAULT          = -11   /* OVR/JQOVF/desync/... → latch */
} MapCaptureStatus;

typedef struct {
    uint32_t capture_id;
    uint16_t pulse_count;            /* 1..MAP_CAPTURE_MAX_PULSES */
    uint16_t timeout_periods;        /* бюджет ожидания фрейма (0 → 100) */
    int16_t  mu, mv, mw;             /* фиксированный OEW pattern, Q15 */
    uint8_t  sector_candidate;       /* 0..5 — кандидат, НЕ доказательство */
    uint8_t  window_candidate;       /* 0..1 */
    int32_t  max_abs_shunt_ma;       /* лимит |idc1|/|idc2| (0 → 10000) */
    uint32_t max_vbus_mv;            /* лимит Vbus (0 → 350000) */
    uint32_t min_vbus_mv;            /* лимит Vbus (0 → 10000) */
    uint32_t trigger_revision;       /* OEW_ADC_TRIGGER_REVISION */
} MapCaptureRequest;

/* Immutable raw record: снапшот в момент публикации фрейма (статические
 * pattern: CCR стабильны между периодами; для динамических паттернов
 * снапшот должен заполняться из ADC ISR — см. отчёт, P1 step 2). */
typedef struct {
    AdcFrame frame;
    uint32_t capture_id;
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
    uint16_t tim1_arr;
    uint32_t trigger_revision;
    uint32_t fault_reason;
} MapCaptureRecord;

/* Ring: foreground-заполнение + foreground drain (mapcap dump/status). */
typedef struct {
    MapCaptureRecord rec[MAP_CAPTURE_RING_SIZE];
    uint16_t produced;               /* всего записано за сессию */
    uint16_t consumed;               /* всего выгружено */
    uint16_t dropped;                /* перезаписи (overflow) — abort */
} MapCaptureRing;

MapCaptureStatus MapCapture_Run(const MapCaptureRequest *req);
void MapCapture_Abort(void);          /* из main loop / watchdog-контекста */
bool MapCapture_IsActive(void);
uint32_t MapCapture_NextCaptureId(void);

/* Foreground: последний снапшот сессии + кольцо (для mcdump/mcstatus). */
const MapCaptureRing *MapCapture_GetRing(void);
uint32_t MapCapture_LastFaultReason(void);
MapCaptureStatus MapCapture_LastStatus(void);

#endif /* MAP_CAPTURE_H */
