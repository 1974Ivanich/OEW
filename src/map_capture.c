/* Bounded service-only capture path — первый съём OEW sector/window карты.
 * См. map_capture.h. Компилируется всегда (каркас), активируется командой
 * только в commissioning build (OEW_MAP_CAPTURE=1, main.c). В production
 * (флаг 0) MapCapture_Run недоступен из CLI — fail-closed сохранён. */
#include "map_capture.h"

#include "stm32g474xx.h"   /* TIM1/TIM8 — реальные CCR/ARR в снапшоте */
#include "foc.h"
#include "protect.h"
#include "pwm.h"
#include "uart.h"
#include "vf_control.h"

/* ~5k итераций спина на период PWM при 170 МГц (период 200 мкс ≈ 34k циклов
 * CPU, итерация цикла ~5-10 инструкций). Бюджет = timeout_periods × 5000. */
#define MAP_CAPTURE_SPIN_PER_PERIOD  5000u

static volatile uint8_t  g_active;
static volatile uint8_t  g_abort;
static volatile uint32_t g_capture_id_counter;
static MapCaptureRing    g_ring;
static uint32_t          g_last_fault_reason;
static MapCaptureStatus  g_last_status;

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

const MapCaptureRing *MapCapture_GetRing(void)
{
    return &g_ring;
}

uint32_t MapCapture_LastFaultReason(void)
{
    return g_last_fault_reason;
}

MapCaptureStatus MapCapture_LastStatus(void)
{
    return g_last_status;
}

static void mc_snapshot(MapCaptureRecord *rec, const AdcFrame *frame,
                        const MapCaptureRequest *req)
{
    rec->frame = *frame;
    rec->capture_id = req->capture_id;
    rec->tim1_ccr[0] = TIM1->CCR1;
    rec->tim1_ccr[1] = TIM1->CCR2;
    rec->tim1_ccr[2] = TIM1->CCR3;
    rec->tim8_ccr[0] = TIM8->CCR1;
    rec->tim8_ccr[1] = TIM8->CCR2;
    rec->tim8_ccr[2] = TIM8->CCR3;
    rec->tim1_arr = TIM1->ARR;
    rec->trigger_revision = req->trigger_revision;
    rec->fault_reason = (uint32_t)PROTECT_GetFaultReason();
}

static void mc_log_record(const MapCaptureRecord *rec)
{
    /* Одна самодостаточная строка (поля OEW_MAP_CAPTURE_RECORD_SCHEMA):
     * raw + engineering + РЕАЛЬНО запрограммированные CCR + статус. */
    UART_SendTelemetry(
        "@MC:cap=%lu:seq=%lu:ts=%lu:pat=%d,%d,%d:sec=%u:win=%u:"
        "raw_i1=%u:raw_i2=%u:raw_ct=%u:raw_vbus=%u:"
        "i1=%ld:i2=%ld:ict=%ld:vbus=%ld:"
        "ccr1=%u,%u,%u:ccr8=%u,%u,%u:arr=%u:trig=%lu:status=%d:fault=%lu\r\n",
        (unsigned long)rec->capture_id,
        (unsigned long)rec->frame.sequence,
        (unsigned long)rec->frame.timestamp_cycles,
        (int)rec->frame.tim1_sector, (int)rec->frame.sample_window,
        (unsigned)rec->frame.raw_idc1, (unsigned)rec->frame.raw_idc2,
        (unsigned)rec->frame.raw_ct, (unsigned)rec->frame.raw_vbus,
        (long)rec->frame.idc1_ma, (long)rec->frame.idc2_ma,
        (long)rec->frame.ict_ma, (long)rec->frame.vbus_mv,
        (unsigned)rec->tim1_ccr[0], (unsigned)rec->tim1_ccr[1], (unsigned)rec->tim1_ccr[2],
        (unsigned)rec->tim8_ccr[0], (unsigned)rec->tim8_ccr[1], (unsigned)rec->tim8_ccr[2],
        (unsigned)rec->tim1_arr,
        (unsigned long)rec->trigger_revision,
        (int)rec->frame.status, (unsigned long)rec->fault_reason);
}

static int mc_push_ring(const MapCaptureRecord *rec)
{
    uint16_t idx;

    if ((uint32_t)(g_ring.produced - g_ring.consumed) >= MAP_CAPTURE_RING_SIZE) {
        g_ring.dropped++;
        return -1;   /* overflow → abort + fault */
    }
    idx = g_ring.produced % MAP_CAPTURE_RING_SIZE;
    g_ring.rec[idx] = *rec;
    g_ring.produced++;
    return 0;
}

static bool mc_limits_ok(const MapCaptureRequest *req, const AdcFrame *f)
{
    int32_t imax = (req->max_abs_shunt_ma > 0) ? req->max_abs_shunt_ma : 10000;
    uint32_t vmax = (req->max_vbus_mv > 0) ? req->max_vbus_mv : 350000u;
    uint32_t vmin = (req->min_vbus_mv > 0) ? req->min_vbus_mv : 10000u;

    if (f->idc1_ma > imax || f->idc1_ma < -imax ||
        f->idc2_ma > imax || f->idc2_ma < -imax) {
        return false;
    }
    if ((uint32_t)f->vbus_mv > vmax || (uint32_t)f->vbus_mv < vmin) {
        return false;
    }
    return true;
}

MapCaptureStatus MapCapture_Run(const MapCaptureRequest *req)
{
    AdcFrame frame;
    uint32_t seq0;
    uint32_t budget;
    uint32_t spin;
    uint16_t pulse;
    MapCaptureRecord rec;
    uint16_t timeout_periods;

    g_last_status = MAP_CAPTURE_OK;
    g_last_fault_reason = 0u;

    if (g_active) return MAP_CAPTURE_HW_INTERLOCK_MISSING;
    if (req == 0 || req->pulse_count == 0u || req->pulse_count > MAP_CAPTURE_MAX_PULSES ||
        req->sector_candidate >= 6u || req->window_candidate >= 2u) {
        return MAP_CAPTURE_BAD_PATTERN;
    }
    /* Preconditions (методика §1): PWM off, ADC не вооружён, control inactive,
     * fault clear, калибровка offsets валидна. */
    if (PWM_IsEnabled() || ADC_InjectedIsArmed()) return MAP_CAPTURE_HW_INTERLOCK_MISSING;
    if (FOC_IsRunning() || VFC_IsRunning()) return MAP_CAPTURE_CONTROL_ACTIVE;
    if (PROTECT_IsFault()) return MAP_CAPTURE_FAULT_LATCHED;
    if (!ADC_OffsetsAreValid()) return MAP_CAPTURE_OFFSET_INVALID;

    /* Диагностический контекст: окно pattern известно, но control admission
     * остаётся false → фрейм будет ADC_FRAME_MAPPING_UNVERIFIED (raw evidence,
     * без права на PI/FOC). */
    {
        PwmSampleContext ctx = { req->sector_candidate, req->window_candidate, true };
        if (!PWM_SetControlVector(req->mu, req->mv, req->mw, &ctx)) {
            return MAP_CAPTURE_BAD_PATTERN;
        }
        if (PWM_ServiceEnable(&ctx) != PWM_ENABLE_OK) {
            PWM_Disable();
            return MAP_CAPTURE_FAULT_LATCHED;
        }
    }

    g_active = 1u;
    g_abort = 0u;
    g_ring.produced = 0u;
    g_ring.consumed = 0u;
    g_ring.dropped = 0u;
    timeout_periods = (req->timeout_periods > 0u) ? req->timeout_periods : 100u;

    /* Базовый sequence: первый фрейм нового периода. */
    if (!ADC_GetLatestFrame(&frame)) {
        g_last_status = MAP_CAPTURE_TIMEOUT;
        goto done;
    }
    seq0 = frame.sequence;

    for (pulse = 0u; pulse < req->pulse_count; ++pulse) {
        /* Ждём НОВЫЙ фрейм (sequence увеличился) с bounded бюджетом. */
        budget = (uint32_t)timeout_periods * MAP_CAPTURE_SPIN_PER_PERIOD;
        spin = 0u;
        for (;;) {
            if (g_abort) {
                g_last_status = MAP_CAPTURE_ABORTED;
                goto done;
            }
            if (ADC_GetLatestFrame(&frame)) {
                if (frame.sequence != seq0) break;
            }
            if (++spin >= budget) {
                g_last_status = MAP_CAPTURE_TIMEOUT;
                PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_TIMEOUT);
                goto done;
            }
        }
        seq0 = frame.sequence;

        /* Capture-specific protection: OVR/JQOVF/desync/... → latch + stop.
         * MAPPING_UNVERIFIED допустим (окно доказывается этим съёмом). */
        PROTECT_CheckCaptureFrame(&frame);
        if (PROTECT_IsFault()) {
            g_last_status = MAP_CAPTURE_FRAME_FAULT;
            goto done;
        }

        /* Лимиты сессии: оба шунта + Vbus из ТОГО ЖЕ фрейма. */
        if (!mc_limits_ok(req, &frame)) {
            g_last_status = MAP_CAPTURE_LIMIT;
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_LIMIT);
            goto done;
        }

        /* Immutable снапшот в RAM ring; overflow → abort + fault. */
        mc_snapshot(&rec, &frame, req);
        if (mc_push_ring(&rec) != 0) {
            g_last_status = MAP_CAPTURE_BUFFER_OVERFLOW;
            PROTECT_LatchFault(PROTECT_FAULT_CAPTURE_BUFFER_OVERFLOW);
            goto done;
        }
        mc_log_record(&rec);   /* foreground drain (main loop), не из ISR */
    }

done:
    /* БЕЗУСЛОВНЫЙ stop: EN LOW первым, CEN/MOE off, injected disarmed. */
    PWM_Disable();
    g_active = 0u;
    g_abort = 0u;
    g_last_fault_reason = (uint32_t)PROTECT_GetFaultReason();
    return g_last_status;
}
