#ifndef MAP_CAPTURE_H
#define MAP_CAPTURE_H

#include <stdbool.h>
#include <stdint.h>

/* Bounded service-only capture path for the first OEW sector/window map
 * (методика первого съёма OEW карты, §2). НЕ является control-путём:
 *  - никогда не вызывает FOC_RunFrame и не устанавливает control admission;
 *  - публикует diagnostic raw frames (ADC_FRAME_MAPPING_UNVERIFIED);
 *  - применяет ровно N фиксированных PWM-периодов и БЕЗУСЛОВНО возвращает
 *    EN/MOE/CEN/ADC в stop state (PWM_Disable — EN LOW первым);
 *  - IRQ не маскируется; OVR/JQOVF/desync/timeout/abort → immediate stop.
 * Команда доступна только в commissioning build (OEW_MAP_CAPTURE=1). */

#define MAP_CAPTURE_MAX_PULSES  4095u

typedef struct {
    int16_t  mu;                 /* фиксированный OEW pattern, Q15 */
    int16_t  mv;
    int16_t  mw;
    uint8_t  sector_candidate;   /* 0..5 — кандидат, НЕ доказательство */
    uint8_t  window_candidate;   /* 0..1 */
    uint16_t pulse_count;        /* 1..MAP_CAPTURE_MAX_PULSES */
    uint32_t capture_id;         /* автоинкремент сессии */
} MapCaptureRequest;

/* Возврат:
 *  0  — N периодов выполнены, всё остановлено (success)
 * -1  — недопустимый запрос / не выполнены preconditions (PWM/ADC/FOC/Vf/offsets)
 * -2  — fault-latch или отказ PWM_ServiceEnable
 * -3  — timeout (нет новых фреймов)
 * -4  -  abort извне (MapCapture_Abort)
 * -5  -  невалидный статус фрейма (OVR/JQOVF/desync/window/...) — immediate stop */
int MapCapture_Run(const MapCaptureRequest *req);
void MapCapture_Abort(void);          /* из main loop / watchdog-контекста */
bool MapCapture_IsActive(void);
uint32_t MapCapture_NextCaptureId(void);

#endif /* MAP_CAPTURE_H */
