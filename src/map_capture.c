/* Bounded service-only capture path — первый съём OEW sector/window карты.
 * См. map_capture.h. Компилируется всегда (каркас), активируется командой
 * только в commissioning build (OEW_MAP_CAPTURE=1, main.c). В production
 * (флаг 0) MapCapture_Run недоступен из CLI — fail-closed сохранён. */
#include "map_capture.h"

#include "stm32g474xx.h"   /* TIM1/TIM8 для реальных CCR в логе */
#include "adc.h"
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "uart.h"
#include "vf_control.h"

#define MAP_CAPTURE_FRAME_TIMEOUT  2000000u   /* bounded spin-лимит на период */

static volatile uint8_t  g_active;
static volatile uint8_t  g_abort;
static volatile uint32_t g_capture_id_counter;

uint32_t MapCapture_NextCaptureId(void)
{
    return ++g_capture_id_counter;
}

bool MapCapture_IsActive(void)
{
    return g_active != 0u;
}

void MapCapture_Abort(void)
{
    g_abort = 1u;
}

static void mc_log_frame(uint32_t capture_id, const AdcFrame *f, const MapCaptureRequest *req,
                         uint16_t pulse)
{
    /* Одна самодостаточная строка (поля из OEW_MAP_CAPTURE_RECORD_SCHEMA).
     * raw + engineering + реально запрограммированные CCR + статус. */
    UART_SendTelemetry(
        "@MC:cap=%lu:pulse=%u:seq=%lu:ts=%lu:pat=%d,%d,%d:sec=%u:win=%u:"
        "raw_i1=%u:raw_i2=%u:raw_ct=%u:raw_vbus=%u:"
        "i1=%ld:i2=%ld:ict=%ld:vbus=%ld:"
        "ccr1=%u,%u,%u:ccr8=%u,%u,%u:arr=%u:status=%d\r\n",
        (unsigned long)capture_id, (unsigned)pulse,
        (unsigned long)f->sequence, (unsigned long)f->timestamp_cycles,
        (int)req->mu, (int)req->mv, (int)req->mw,
        (unsigned)f->tim1_sector, (unsigned)f->sample_window,
        (unsigned)f->raw_idc1, (unsigned)f->raw_idc2,
        (unsigned)f->raw_ct, (unsigned)f->raw_vbus,
        (long)f->idc1_ma, (long)f->idc2_ma, (long)f->ict_ma, (long)f->vbus_mv,
        (unsigned)TIM1->CCR1, (unsigned)TIM1->CCR2, (unsigned)TIM1->CCR3,
        (unsigned)TIM8->CCR1, (unsigned)TIM8->CCR2, (unsigned)TIM8->CCR3,
        (unsigned)TIM1->ARR, (int)f->status);
}

int MapCapture_Run(const MapCaptureRequest *req)
{
    AdcFrame frame;
    uint32_t seq0;
    uint32_t timeout;
    uint16_t pulse;
    int rc;

    if (g_active) return -1;
    if (req == 0 || req->pulse_count == 0u || req->pulse_count > MAP_CAPTURE_MAX_PULSES ||
        req->sector_candidate >= 6u || req->window_candidate >= 2u) {
        return -1;
    }
    /* Preconditions (методика §1): PWM off, ADC не вооружён, control inactive,
     * fault clear, калибровка offsets валидна. */
    if (PWM_IsEnabled() || ADC_InjectedIsArmed()) return -1;
    if (FOC_IsRunning() || VFC_IsRunning()) return -1;
    if (PROTECT_IsFault()) return -2;
    if (!ADC_OffsetsAreValid()) return -1;

    /* Диагностический контекст: окно pattern известно, но control admission
     * остаётся false → фрейм будет ADC_FRAME_MAPPING_UNVERIFIED (raw evidence,
     * без права на PI/FOC). */
    {
        PwmSampleContext ctx = { req->sector_candidate, req->window_candidate, true };
        if (!PWM_SetControlVector(req->mu, req->mv, req->mw, &ctx)) return -1;
        if (PWM_ServiceEnable(&ctx) != PWM_ENABLE_OK) {
            PWM_Disable();
            return -2;
        }
    }

    g_active = 1u;
    g_abort = 0u;
    rc = 0;

    /* Базовый sequence: ждём первый фрейм нового периода. */
    if (!ADC_GetLatestFrame(&frame)) {
        rc = -3;
        goto done;
    }
    seq0 = frame.sequence;

    for (pulse = 0u; pulse < req->pulse_count; ++pulse) {
        /* Ждём НОВЫЙ фрейм (sequence увеличился) с bounded таймаутом. */
        timeout = 0u;
        for (;;) {
            if (g_abort) { rc = -4; goto done; }
            if (ADC_GetLatestFrame(&frame)) {
                if (frame.sequence != seq0) break;
            }
            if (++timeout >= MAP_CAPTURE_FRAME_TIMEOUT) { rc = -3; goto done; }
        }
        seq0 = frame.sequence;

        /* Любой non-clean статус → immediate stop (методика §2 abort). */
        if (frame.status != ADC_FRAME_VALID &&
            frame.status != ADC_FRAME_MAPPING_UNVERIFIED) {
            rc = -5;
            goto done;
        }
        mc_log_frame(req->capture_id, &frame, req, pulse);
    }

done:
    /* БЕЗУСЛОВНЫЙ stop: EN LOW первым, CEN/MOE off, injected disarmed. */
    PWM_Disable();
    g_active = 0u;
    g_abort = 0u;
    return rc;
}
