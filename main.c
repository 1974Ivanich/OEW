#include "stm32g474xx.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include "uart.h"
#include "cli.h"
#include "pwm.h"
#include "map_capture.h"   /* service-only capture path (OEW_MAP_CAPTURE) */
#include "map_capture_port.h"  /* hooks-порт к PWM/FOC/Vf/protect */
#include "map_capture_profiles.h"  /* compiled profile gate (fail-closed) */
#include "adc_dispatch.h"
#include "map_builder.h"
#include "map_candidate.h"
#include "map_commissioning.h"
#include "current_map_selector.h"

#ifndef OEW_MAP_CAPTURE
#define OEW_MAP_CAPTURE 0   /* commissioning only: 1 — включает команду mc= */
#endif
#ifndef OEW_MAP_L3
#define OEW_MAP_L3 0       /* board-qualified map builder/loader, default-deny */
#endif
#include "adc.h"
#include "foc.h"
#include "protect.h"
#include "autotune.h"
#include "cordic_math.h"
#include "encoder.h"
#include "vf_control.h"
#include "swo.h"
/**/
#include "pwm_board_pins.h" /* TRIG_High/Low → PWM_Trigger* (единый источник) */
/* ── SWO-дублёр отладочных сообщений ────────────────────────────────────────
 * Меню/ошибки/статусы идут И в UART (GUI), И в SWO (отладчик).
 * Телеметрия (@FOC/@ADC/@PWM/@TRIG) и промпт "> " НЕ дублируются — это
 * GUI-протокол, SWO засоряется. */
#define DBG_STR(s)      do { UART_SendStr(s); SWO_SendStr(s); } while(0)

/* IWDG-рефреш (см. IWDG_Init ниже): 1 кГц из TIM6 ISR. */
#define IWDG_REFRESH() do { IWDG->KR = 0xAAAAU; } while(0)
#define DBG_FMT(fmt, ...) do { UART_SendTelemetry(fmt, ##__VA_ARGS__); \
                                SWO_Printf(fmt, ##__VA_ARGS__); } while(0)


volatile uint32_t sys_tick_ms = 0;   /* внешняя линковка — используется encoder.c (ENC_Calibrate) */
void SysTick_Handler(void) { sys_tick_ms++; }

/* Ревью pinmux: GPIO-инициализация силовой части (TIM1/TIM8 AF-пины,
 * EN1/EN2, TRIG PB6, ADC-analog) вынесена в единый источник —
 * PWM_BoardPins_Init() (src/pwm_board_pins.c), вызывается из PWM_Init().
 * EN1/EN2 остаются LOW до PWM_Enable(); TRIG — LOW. USART2 (PA2/PA3) —
 * в uart.c, TIM2 encoder (PA15) — в encoder.c. */
static inline void TRIG_High(void) { PWM_TriggerHigh(); }
static inline void TRIG_Low(void)  { PWM_TriggerLow(); }

/* ── Ревью ADC-2S-01..04 (топология «2 DC-link shunt + CT»): ADC-решения —
 * на AdcFrame (adc.c): ADC1/ADC2 dual injected simultaneous (I1/I2 в одном
 * апертуре), CT/Vbus следом; фрейм публикуется ISR с status. FOC_Run
 * допускается ТОЛЬКО на ADC_FRAME_VALID (control admission + валидное окно
 * выборки — по умолчанию оба выключены, FOC заблокирован до валидации
 * датчиков и карты секторов). Невалидный фрейм при работающем FOC — стоп.
 * Ошибки ADC (OVR/JQOVF/desync) обрабатываются в ADC_InjectedIrq. */
/* Bounded service burst: UIF (1×/период, RCR=1) → timeout watchdog
 * capture-сессии. No-op вне RUNNING; UIE выключен вне capture. */
void TIM1_UP_TIM16_IRQHandler(void) {
    if(TIM1->SR & TIM_SR_UIF) {
            TIM1->SR &= ~TIM_SR_UIF;
        MapCapturePort_OnPwmPeriod();
    }
}

/* Direct SD1/SD2 → TIM1/TIM8 break. Latch причины, центральный
 * terminal stop, БЕЗ авто-реарма (MOE/CEN/ADC не поднимаем). */
void TIM1_BRK_TIM15_IRQHandler(void) {
    const uint32_t flags = TIM1->SR & (TIM_SR_BIF | TIM_SR_B2IF);
    if(flags != 0u) {
        TIM1->SR &= ~flags;
        PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
        PWM_Disable();
    }
}

void TIM8_BRK_IRQHandler(void) {
    const uint32_t flags = TIM8->SR & (TIM_SR_BIF | TIM_SR_B2IF);
    if(flags != 0u) {
        TIM8->SR &= ~flags;
        PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK);
        PWM_Disable();
    }
}

static bool adc_dispatch_capture_active(void) { return MapCapture_IsActive(); }
static bool adc_dispatch_get_frame(AdcFrame *frame) { return ADC_GetLatestFrame(frame); }
static void adc_dispatch_capture_frame(const AdcFrame *frame) { MapCapture_OnAdcFrame(frame); }
static void adc_dispatch_capture_missing(void) { MapCapture_OnAdcFrame(0); }
static bool adc_dispatch_foc_running(void) { return FOC_IsRunning(); }
static bool adc_dispatch_timer_enabled(void) { return (TIM1->CR1 & TIM_CR1_CEN) != 0u; }
static void adc_dispatch_latch_copy_failure(void) { PROTECT_LatchFrameCopyFailure(); }
static void adc_dispatch_protect_frame(const AdcFrame *frame) { PROTECT_CheckFrame(frame); }
static bool adc_dispatch_fault(void) { return PROTECT_IsFault(); }
static void adc_dispatch_stop(void) { FOC_Stop(); }
static void adc_dispatch_run(const AdcFrame *frame) { FOC_RunFrame(frame); }

void ADC1_2_IRQHandler(void) {
    const bool injected_event = ADC_InjectedIrq();
    const AdcDispatchOps ops = {
        adc_dispatch_capture_active,
        adc_dispatch_get_frame,
        adc_dispatch_capture_frame,
        adc_dispatch_capture_missing,
        adc_dispatch_foc_running,
        adc_dispatch_timer_enabled,
        adc_dispatch_latch_copy_failure,
        adc_dispatch_protect_frame,
        adc_dispatch_fault,
        adc_dispatch_stop,
        adc_dispatch_run
    };
    AdcDispatch_Handle(injected_event, &ops);
}

/* TIM6 1 kHz ISR — encoder read + V/f control loop.
 * Priority 2: ниже ADC(0) и TIM2(1) (encoder capture, Bolt P2); равен USART2. */
static void TIM6_Init_1kHz(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_TIM6EN;
    (void)RCC->APB1ENR1;  /* sync after clock enable */
    TIM6->PSC = 169;   /* 170 MHz / 170 = 1 MHz */
    TIM6->ARR = 999;   /* 1 MHz / 1000 = 1 kHz */
    TIM6->SR  = 0;     /* clear UIF before enable — prevent spurious IRQ */
    TIM6->CR1 |= TIM_CR1_ARPE;  /* preload ARR (RM0440 recommendation) */
    TIM6->DIER |= TIM_DIER_UIE;
    TIM6->CR1 |= TIM_CR1_CEN;
    NVIC_SetPriority(TIM6_DAC_IRQn, 2);  /* ниже TIM2 (encoder capture, ревью Bolt P2) */
    NVIC_EnableIRQ(TIM6_DAC_IRQn);
}

/* vflog: единый телеметрический пакет V/f-сессии (ТЗ TZ_VF_DATA_LOGGING.md).
 * Публикуется из TIM6_DAC_IRQHandler (приоритет 1) — ОБЯЗАТЕЛЬНО через
 * UART_TrySendTelemetry() (неблокирующий), а не UART_SendTelemetry(), иначе
 * при заполнении UART TX-буфера возможен priority-inversion deadlock
 * (TIM6_DAC_IRQn=2 — равен USART2_IRQn=2, вытеснения между ними нет). */
static volatile uint32_t vflog_period_ms = 0;
static volatile uint32_t vflog_last_ms = 0;
#if OEW_MAP_CAPTURE && OEW_MAP_L3
static MapBuilderQualification mapcap_qualification;
static uint32_t mapcap_builder_profile_id = 0u;
static uint8_t mapcap_builder_active = 0u;
#endif

#define VFLOG_DEFAULT_PERIOD_MS  20u  /* 50 Гц — запас от лимита UART 115200 бод */

void TIM6_DAC_IRQHandler(void) {
    IWDG_REFRESH();   /* 1 кГц — watchdog жив, пока работает хотя бы TIM6 */
    if(TIM6->SR & TIM_SR_UIF) {
        TIM6->SR &= ~TIM_SR_UIF;  /* &= — не записывать 1 в прочие биты (ревью п.12) */
        ENC_Update();
        if(VFC_IsRunning()) {
            ADC_StartConversion();  /* regular group — refresh adc_data for PROTECT_Check */
            VFC_Update();
            PROTECT_Check();
            if(PROTECT_IsFault()) {
                VFC_Stop(); vflog_period_ms = 0; TRIG_Low();
                /* Ревью GUI-14: уведомление GUI о принудительной остановке
                 * V/f — GUI закрывает CSV-сессию с reason (иначе файл и
                 * session остаются активными после fault). */
                UART_TrySendTelemetry("@VF:STOPPED:REASON=FAULT\r\n");
            }
            else if(vflog_period_ms > 0 && (sys_tick_ms - vflog_last_ms) >= vflog_period_ms) {
                vflog_last_ms = sys_tick_ms;
                UART_TrySendTelemetry(
                    "@VFLOG:t=%lu:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld:theta=%lu:"
                    "du=%ld:dv=%ld:dw=%ld:i1=%u:i2=%u:ires=%u:vbus=%u:"
                    "eangle=%u:espeed=%ld:eerr=%u:fault=%d:drp=%lu\r\n",
                    (unsigned long)sys_tick_ms,
                    (long)vfc.target_rpm, (long)vfc.measured_rpm, (long)vfc.f_e_hz,
                    (long)vfc.f_slip_hz, (long)vfc.voltage_mag, (unsigned long)vfc.theta_elec,
                    (long)vfc.duty_u, (long)vfc.duty_v, (long)vfc.duty_w,
                    (unsigned)ADC_GetRawI1(), (unsigned)ADC_GetRawI2(),
                    (unsigned)ADC_GetRawIres(), (unsigned)ADC_GetRawVbus(),
                    (unsigned)ENC_GetAngle14(), (long)ENC_GetSpeed_rpm(),
                    (unsigned)ENC_GetError(), (int)PROTECT_GetFaultReason(),
                    (unsigned long)UART_GetDroppedCount());
            }
        }
    }
}

static void print_help(void) {
    DBG_STR("1=start 0=stop s=500=spd i=id,iq f=clear m=menu\r\n"
                 "idle  curve  irot  inertia  params\r\n"
                 "ch       - detect current channel\r\n"
                 "iv       - multi-point Rs (I-V)\r\n"
                 "pairs    - measure A/B/C\r\n"
                 "abort    - abort running autotune\r\n"
                 "stats    - print Rs/Ls/Isat statistics\r\n"
                 "oew      - Ls via both inverters (OEW)\r\n"
                 "rr       - Rr test (5 Hz, locked rotor)\r\n"
                 "noload   - Lm/Lr test (V/f, free rotor)\r\n"
                 "scope    - current oscilloscope (100 pts)\r\n"
                 "pi=N     - calc PI gains (N=bandwidth Hz) + apply\r\n"
                 "piapply  - apply last calculated Kp/Ki to FOC\r\n"
                 "mp=R,L,Rr,Lm,Tr,Ke,p,J - apply motor params to FOC\r\n"
                 "mpapply  - apply g_motor_params to FOC (no args)\r\n"
                 "lspos    - Ls vs rotor position (6 pts)\r\n"
#if OEW_MAP_CAPTURE && OEW_MAP_L3
                 "mapcap build=<profile> - build/load measured map (commissioning)\r\n"
#endif
                 "DBG: p=arr,duty,dt[,mask] a a=N c p? dump dump8 pdump\r\n");
}

#if OEW_MAP_CAPTURE && OEW_MAP_L3
static bool map_identity_equal(const OewMapIdentity *a, const OewMapIdentity *b)
{
    return a != 0 && b != 0 &&
           a->board_revision == b->board_revision &&
           a->pwm_frequency_hz == b->pwm_frequency_hz &&
           a->timer_arr == b->timer_arr &&
           a->adc_trigger_id == b->adc_trigger_id;
}

/* MapCommissioningOps ожидает bool(*)(void); часть production-геттеров
 * возвращает int/uint32_t — обёртки приводят типы (как в adc_dispatch). */
static bool mapcap_foc_running(void) { return FOC_IsRunning() != 0; }
static bool mapcap_vfc_running(void) { return VFC_IsRunning() != 0; }
static bool mapcap_autotune_active(void) { return Autotune_IsActive() != 0; }
static bool mapcap_pwm_enabled(void) { return PWM_IsEnabled() != 0; }
static bool mapcap_protect_fault(void) { return PROTECT_IsFault() != 0; }

static void mapcap_build_and_load(uint32_t profile_id)
{
    MapCaptureStats capture_stats;
    MapBuilderStats builder_stats;
    MapCaptureRecord record;
    OewCurrentMap map;
    MapCandidateQualification candidate_qualification;
    OewMapIdentity live_identity;
    uint32_t records = 0u;

    if (MapCapture_IsActive() || FOC_IsRunning() || VFC_IsRunning() ||
        Autotune_IsActive() || PWM_IsEnabled() || ADC_InjectedIsArmed()) {
        UART_SendStr("@MAP:BUILD:BLOCKED:CONTROL_ACTIVE\r\n> ");
        return;
    }
    if (PROTECT_IsFault()) {
        UART_SendStr("@MAP:BUILD:BLOCKED:FAULT\r\n> ");
        return;
    }
    MapCapture_GetStats(&capture_stats);
    if (capture_stats.state != MAP_CAPTURE_COMPLETE ||
        capture_stats.terminal_status != MAP_CAPTURE_OK ||
        capture_stats.records_available == 0u) {
        UART_SendTelemetry("@MAP:BUILD:BLOCKED:CAPTURE_STATE=%d:TERM=%d:AVAILABLE=%u\r\n> ",
                           (int)capture_stats.state, (int)capture_stats.terminal_status,
                           (unsigned)capture_stats.records_available);
        return;
    }
    if (!mapcap_builder_active) {
        if (!MapCaptureProfile_BuildQualification(profile_id, &mapcap_qualification)) {
            UART_SendStr("@MAP:BUILD:BLOCKED:PROFILE\r\n> ");
            return;
        }
        if (!MapCapturePort_GetMapIdentity(&live_identity) ||
            !map_identity_equal(&mapcap_qualification.identity, &live_identity)) {
            UART_SendStr("@MAP:BUILD:BLOCKED:IDENTITY\r\n> ");
            return;
        }
        if (!MapBuilder_Begin(&mapcap_qualification)) {
            UART_SendStr("@MAP:BUILD:ERROR:QUALIFICATION\r\n> ");
            return;
        }
        mapcap_builder_profile_id = profile_id;
        mapcap_builder_active = 1u;
    } else if (profile_id != mapcap_builder_profile_id) {
        UART_SendStr("@MAP:BUILD:BLOCKED:PROFILE_SESSION_MISMATCH\r\n> ");
        return;
    }
    if (!MapCapturePort_GetMapIdentity(&live_identity) ||
        !map_identity_equal(&mapcap_qualification.identity, &live_identity)) {
        UART_SendStr("@MAP:BUILD:BLOCKED:IDENTITY_CHANGED\r\n> ");
        MapBuilder_Reset();
        mapcap_builder_active = 0u;
        return;
    }
    while (MapCapture_ConsumeRecord(&record)) {
        ++records;
        if (!MapBuilder_AddRecord(&record)) {
            MapBuilder_GetStats(&builder_stats);
            UART_SendTelemetry("@MAP:BUILD:ERROR:RECORD=%lu:SECTOR=%u:WINDOW=%u\r\n> ",
                               (unsigned long)records,
                               (unsigned)record.frame.tim1_sector,
                               (unsigned)record.frame.sample_window);
            MapBuilder_Reset();
            mapcap_builder_active = 0u;
            return;
        }
    }
    if (!MapBuilder_Finalize(&map, &builder_stats)) {
        UART_SendTelemetry("@MAP:BUILD:PARTIAL:SESSION_RECORDS=%lu:TOTAL_RECORDS=%lu\r\n> ",
                           (unsigned long)records,
                           (unsigned long)builder_stats.records_seen);
        return;
    }
    memset(&candidate_qualification, 0, sizeof(candidate_qualification));
    candidate_qualification.identity = mapcap_qualification.identity;
    candidate_qualification.manifest = mapcap_qualification.manifest;
    candidate_qualification.startup_sector = mapcap_qualification.startup_sector;
    candidate_qualification.startup_window = mapcap_qualification.startup_window;
    candidate_qualification.startup_hold_cycles = mapcap_qualification.startup_hold_cycles;
    candidate_qualification.startup_mu = mapcap_qualification.startup_mu;
    candidate_qualification.startup_mv = mapcap_qualification.startup_mv;
    candidate_qualification.startup_mw = mapcap_qualification.startup_mw;
    if (MapCandidate_Build(&candidate_qualification,
                           mapcap_qualification.recon,
                           mapcap_qualification.region,
                           &map) != MAP_CANDIDATE_OK) {
        UART_SendStr("@MAP:BUILD:ERROR:CANDIDATE_REJECTED\r\n> ");
        MapBuilder_Reset();
        mapcap_builder_active = 0u;
        return;
    }
    {
        const MapCommissioningOps commissioning_ops = {
            MapCapture_IsActive,
            mapcap_foc_running,
            mapcap_vfc_running,
            mapcap_autotune_active,
            mapcap_pwm_enabled,
            ADC_InjectedIsArmed,
            mapcap_protect_fault,
            MapCapturePort_GetMapIdentity,
            CurrentMap_LoadMeasured,
            CurrentMap_IsReady
        };
        if (!MapCommissioning_LoadMeasured(&map, &mapcap_qualification.manifest,
                                           &commissioning_ops)) {
            UART_SendStr("@MAP:LOAD:ERROR:VALIDATION\r\n> ");
            MapBuilder_Reset();
            mapcap_builder_active = 0u;
            return;
        }
    }
    if (!CurrentMap_IsReady()) {
        UART_SendStr("@MAP:LOAD:ERROR:NOT_READY\r\n> ");
        MapBuilder_Reset();
        mapcap_builder_active = 0u;
        return;
    }
    UART_SendTelemetry("@MAP:READY:records=%lu:rows=%u\r\n> ",
                       (unsigned long)builder_stats.records_seen,
                       (unsigned)(OEW_CURRENT_MAP_SECTOR_COUNT *
                                  OEW_CURRENT_MAP_WINDOW_COUNT));
    MapBuilder_Reset();
    mapcap_builder_active = 0u;
}
#endif

/* ── Ревью «План блокеров»: bounded clock bring-up + fallback на HSI16.
 * Бесконечные while(PLLRDY) при неисправном PLL вешали boot. Таймаут →
 * остаёмся на HSI16 (16 МГц), g_clock_fail=1 → PWM_Enable() запрещён. */
#define CLOCK_TIMEOUT_CYCLES 1000000UL
volatile uint8_t g_clock_fail = 0;   /* 1 = PLL не поднялся — силовая часть запрещена */

static int clock_wait_set(volatile uint32_t *reg, uint32_t mask) {
    uint32_t n = CLOCK_TIMEOUT_CYCLES;
    while(((*reg & mask) == 0U) && (n-- != 0U)) { }
    return (n == 0U) ? -1 : 0;
}
static int clock_wait_clear(volatile uint32_t *reg, uint32_t mask) {
    uint32_t n = CLOCK_TIMEOUT_CYCLES;
    while(((*reg & mask) != 0U) && (n-- != 0U)) { }
    return (n == 0U) ? -1 : 0;
}

/* ── IWDG: аппаратный сторожевой таймер (дельта OEW, раздел 1) ──────────
 * Частично закрывает hazard «зависание MCU»: при lockup/hard fault MCU
 * сбрасывается через ~1 с, boot держит EN1/EN2 LOW (PWM_BoardPins_Init).
 * Рефреш — из TIM6 ISR (1 кГц): работает даже при занятом autotune-тестами
 * main loop. Freeze при halt в отладчике (DBGMCU). НЕ заменяет hardware
 * FAULT_N — это лишь supplementary measure (как и требует addendum). */
static void IWDG_Init(void) {
    DBGMCU->APB1FZR1 |= DBGMCU_APB1FZR1_DBG_IWDG_STOP;  /* не сбрасываться при debug halt */
    IWDG->KR = 0x5555U;   /* разблокировка записи */
    IWDG->PR  = 6U;       /* /256: 32 кГц LSI / 256 = 125 Гц */
    IWDG->RLR = 125U;     /* 125 тиков = 1.0 с */
    IWDG->KR  = 0xCCCCU;  /* запуск (остановить нельзя) */
}

static void cli_send_dbg(const char *text) { DBG_STR(text); }
static void cli_send_dbg_fmt(const char *fmt, ...)
{
    char text[256];
    va_list ap;
    va_start(ap, fmt);
    (void)vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    DBG_STR(text);
}
static uint32_t cli_tick_ms(void) { return sys_tick_ms; }
static void cli_adc_raw(CLI_AdcRaw *out) { out->i1=ADC_GetRawI1(); out->i2=ADC_GetRawI2(); out->ires=ADC_GetRawIres(); out->vbus=ADC_GetRawVbus(); }
static void cli_adc_offsets(CLI_AdcOffsets *out) { out->offset_i1=ADC_GetOffsetI1(); out->offset_i2=ADC_GetOffsetI2(); out->offset_ires=ADC_GetOffsetIres(); }
static void cli_adc_irq_disable(void) { NVIC_DisableIRQ(ADC1_2_IRQn); }
static void cli_adc_irq_enable(void) { NVIC_EnableIRQ(ADC1_2_IRQn); }
static void cli_adc_diag(uint32_t out[12]) { out[0]=ADC2->SQR1; out[1]=ADC2->CFGR; out[2]=ADC2->SMPR1; out[3]=ADC2->JSQR; out[4]=ADC2->DIFSEL; out[5]=ADC2->CR; out[6]=ADC2->ISR; out[7]=ADC2->DR; out[8]=ADC2->JDR1; out[9]=ADC2->JDR2; out[10]=ADC2->JDR3; out[11]=ADC2->JDR4; }
static void cli_adc_counts(uint32_t out[4]) { out[0]=ADC_GetOvrCount(); out[1]=ADC_GetJeosCount(); out[2]=ADC_GetTimeoutCount(); out[3]=ADC_GetJqovfCount(); }
static void cli_pwm_status(CLI_PwmStatus *out) { PWM_GetStatus(&out->cr1,&out->ccer,&out->bdtr,&out->cnt); }
static void cli_pwm_dump(CLI_PwmDump *out) { PWM_DumpRegs(&out->psc,&out->arr,&out->bdtr,&out->cr1,&out->cr2,&out->ccer); }
static void cli_pwm_dump8(CLI_PwmDump *out) { PWM_DumpRegs8(&out->psc,&out->arr,&out->bdtr,&out->cr1,&out->cr2,&out->ccer); }
static void cli_pwm_full(uint32_t out[22]) {
    out[0]=SystemCoreClock; out[1]=RCC->CFGR;
    out[2]=TIM1->PSC; out[3]=TIM1->ARR; out[4]=TIM1->CCR1; out[5]=TIM1->CCR2; out[6]=TIM1->CCR3; out[7]=TIM1->BDTR; out[8]=TIM1->CCER; out[9]=TIM1->CR1; out[10]=TIM1->CNT;
    out[11]=TIM8->PSC; out[12]=TIM8->ARR; out[13]=TIM8->CCR1; out[14]=TIM8->CCR2; out[15]=TIM8->CCR3; out[16]=TIM8->BDTR; out[17]=TIM8->CCER; out[18]=TIM8->CR1; out[19]=TIM8->CNT;
}
static void cli_pwm_sysinfo(uint32_t out[4]) { uint32_t psc,tclk; PWM_GetSysInfo(&psc,&tclk); out[0]=SystemCoreClock; out[1]=psc; out[2]=tclk; out[3]=RCC->PLLCFGR; }
static uint32_t cli_pwm_deadtime_reg(void) { return TIM1->BDTR & 0xFFu; }
static void cli_foc_current(int32_t id, int32_t iq) { FOC_SetIdRef(id); FOC_SetIqRef(iq); }
static void cli_vf_status(CLI_VfStatus *out) { out->target=VFC_GetTarget(); out->measured=VFC_GetSpeed(); out->fe=vfc.f_e_hz; out->slip=vfc.f_slip_hz; out->vmag=vfc.voltage_mag; out->boost=vfc.v_boost_pct; out->rated=vfc.rated_freq_hz; }
static void cli_encoder_status(CLI_EncoderStatus *out) { out->angle=ENC_GetAngle14(); out->speed=ENC_GetSpeed_rpm(); out->period=ENC_GetPeriod_us(); out->pulse=ENC_GetPulseWidth_us(); out->error=ENC_GetError(); }
static int8_t cli_autotune_run(CLI_AutotuneKind kind) {
    switch(kind) {
        case CLI_AT_IROT: return Autotune_Irot(); case CLI_AT_INERTIA: return Autotune_Inertia(); case CLI_AT_CH: return Autotune_DetectChannel(); case CLI_AT_CHU: return Autotune_ProbePhase(0u); case CLI_AT_CHV: return Autotune_ProbePhase(1u); case CLI_AT_CHW: return Autotune_ProbePhase(2u); case CLI_AT_IV: return Autotune_MeasureRs_IV(); case CLI_AT_PAIRS: return Autotune_MeasureAllPairs(); case CLI_AT_OEW: return Autotune_MeasureLs_OEW(); case CLI_AT_RR: return Autotune_MeasureRr(); case CLI_AT_NOLOAD: return Autotune_MeasureNoLoad(); case CLI_AT_SCOPE: return Autotune_Scope(); case CLI_AT_LSPOS: return Autotune_MeasureLs_Position(); default: return Autotune_Idle();
    }
}
static void cli_autotune_abort(uint8_t value) { g_autotune_abort=value; }
static void cli_motor_get(CLI_MotorParams *out) { out->rs=g_motor_params.Rs_mOhm; out->ls=g_motor_params.Ls_uH; out->rr=g_motor_params.Rr_mOhm; out->lm=g_motor_params.Lm_uH; out->tr=g_motor_params.Tr_rotor_us; out->ke=g_motor_params.Ke_mV_per_rpm; out->pairs=g_motor_params.pole_pairs; out->inertia=g_motor_params.J_kg_m2_x1e6; out->measured_mask=g_motor_params.measured_mask; }
static void cli_motor_set(const CLI_MotorParams *in) { g_motor_params.Rs_mOhm=in->rs; g_motor_params.Ls_uH=in->ls; g_motor_params.Rr_mOhm=in->rr; g_motor_params.Lm_uH=in->lm; g_motor_params.Tr_rotor_us=in->tr; g_motor_params.Ke_mV_per_rpm=in->ke; g_motor_params.pole_pairs=(uint8_t)in->pairs; g_motor_params.J_kg_m2_x1e6=in->inertia; g_motor_params.measured_mask=in->measured_mask; }
static void cli_swo_test(uint32_t tick) { SWO_Printf("@SWO:test:tick=%lu\r\n", (unsigned long)tick); }
static int cli_mapcap_command(const char *line)
{
#if OEW_MAP_CAPTURE
    static uint32_t mapcap_next_id;
    if (strncmp(line,"mcarm=",6)==0) { unsigned int id; if(sscanf(line+6,"%u",&id)!=1) UART_SendStr("err: mcarm=<profile_id>\r\n> "); else { MapCaptureRequest r; if(!MapCaptureProfile_BuildRequest(id,++mapcap_next_id,&r)) UART_SendStr("@MC:ARM:BLOCKED:PROFILE\r\n> "); else UART_SendTelemetry("@MC:ARM:cap=%lu:rc=%d\r\n> ",(unsigned long)r.capture_id,(int)MapCapture_Arm(&r)); } return 1; }
    if (strcmp(line,"mapcap run")==0) { UART_SendTelemetry("@MC:RUN:rc=%d\r\n> ",(int)MapCapture_Run()); return 1; }
    if (strcmp(line,"mapcap drain")==0) { MapCaptureRecord r; unsigned int n=0; while(MapCapture_ConsumeRecord(&r)) { UART_SendTelemetry("@MC:REC:cap=%lu:seq=%lu:raw_i1=%u:raw_i2=%u:raw_ct=%u:raw_vbus=%u:i1=%ld:i2=%ld:vbus=%ld:ccr1=%u,%u,%u:ccr8=%u,%u,%u:arr=%u:trig=%lu:status=%d:fault=%d\r\n",(unsigned long)r.capture_id,(unsigned long)r.frame.sequence,(unsigned)r.frame.raw_idc1,(unsigned)r.frame.raw_idc2,(unsigned)r.frame.raw_ct,(unsigned)r.frame.raw_vbus,(long)r.frame.idc1_ma,(long)r.frame.idc2_ma,(long)r.frame.vbus_mv,(unsigned)r.pwm.tim1_ccr[0],(unsigned)r.pwm.tim1_ccr[1],(unsigned)r.pwm.tim1_ccr[2],(unsigned)r.pwm.tim8_ccr[0],(unsigned)r.pwm.tim8_ccr[1],(unsigned)r.pwm.tim8_ccr[2],(unsigned)r.pwm.tim1_arr,(unsigned long)r.pwm.trigger_revision,(int)r.frame.status,(int)r.fault_reason); ++n; } UART_SendTelemetry("@MC:DRAIN:records=%u\r\n> ",n); return 1; }
#if OEW_MAP_L3
    if (strncmp(line,"mapcap build=",13)==0) { unsigned int id; if(sscanf(line+13,"%u",&id)!=1) UART_SendStr("err: mapcap build=<profile>\r\n> "); else mapcap_build_and_load(id); return 1; }
#endif
    if (strcmp(line,"mapcap abort")==0) { UART_SendTelemetry("@MC:ABORT:rc=%d\r\n> ",(int)MapCapture_Abort()); return 1; }
    if (strcmp(line,"mapcap status")==0) { MapCaptureStats st; MapCapture_GetStats(&st); UART_SendTelemetry("@MC:STATUS:state=%d:term=%d:cap=%lu:frames=%u:dropped=%u:periods=%u:avail=%u\r\n> ",(int)st.state,(int)st.terminal_status,(unsigned long)st.capture_id,(unsigned)st.accepted_frames,(unsigned)st.dropped_records,(unsigned)st.periods_elapsed,(unsigned)st.records_available); return 1; }
#else
    (void)line;
#endif
    return 0;
}

int main(void) {
    SystemCoreClockUpdate();
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) | FLASH_ACR_LATENCY_4WS;
    /* HSI16 включаем и подтверждаем ДО конфигурации PLL. */
    RCC->CR |= RCC_CR_HSION;
    if(clock_wait_set(&RCC->CR, RCC_CR_HSIRDY) != 0) g_clock_fail = 1;
    RCC->CR &= ~RCC_CR_PLLON;
    (void)clock_wait_clear(&RCC->CR, RCC_CR_PLLRDY);   /* PLL выключен до смены параметров */
    RCC->PLLCFGR = (3U  << RCC_PLLCFGR_PLLM_Pos)   /* M=4 */
                 | (85U << RCC_PLLCFGR_PLLN_Pos)   /* N=85 */
                 | (0U  << RCC_PLLCFGR_PLLR_Pos)   /* R=div2 */
                 | RCC_PLLCFGR_PLLREN
                 | (2U  << RCC_PLLCFGR_PLLSRC_Pos); /* PLLSRC=10 = HSI16 (16 МГц):
                       16/4·85/2 = 170 МГц. RM0440 §7.4.4: 00/01 = no clock sent
                       to PLL, 10 = HSI16, 11 = HSE. */
    RCC->CR |= RCC_CR_PLLON;
    if(clock_wait_set(&RCC->CR, RCC_CR_PLLRDY) != 0) {
        g_clock_fail = 1;
        RCC->CR &= ~RCC_CR_PLLON;   /* остаёмся на HSI16 */
    } else {
        RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
        if(clock_wait_set(&RCC->CFGR, RCC_CFGR_SWS_PLL) != 0) {
            g_clock_fail = 1;
            RCC->CR &= ~RCC_CR_PLLON;
        }
    }
    SystemCoreClockUpdate();

    UART_Init();
    UART_SendTelemetry("OEW FOC v0.2 @%luMHz\r\n> ", (unsigned long)(SystemCoreClock / 1000000));
    if(g_clock_fail) {
        UART_SendStr("CLOCK FAIL: PLL not locked, running on HSI16 (16 MHz), power stage disabled\r\n> ");
    }
    SWO_Init();
    /* НЕ выводим в SWO при инициализации: ITM FIFO забивается ДО подключения
     * отладчика → ITM_TCR_BUSY навсегда (OpenOCD не может прочитать TCR).
     * SWO-вывод — только по команде 's', когда TPI уже настроен отладчиком. */
    /* Ревью P1: ошибки инициализации ADC — latched (g_clock_fail), PWM_Enable
     * впоследствии запрещён; не печатать success вслепую. */
    if(ADC_Init() != 0 || ADC_InjectedInit() != 0) {
        g_clock_fail = 1;
        UART_SendStr("ADC INIT FAIL: power stage locked\r\n");
    } else {
        UART_SendStr("ADC OK, injected OK\r\n");
    }
    PWM_Init(); UART_SendStr("PWM OK\r\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\r\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\r\n");
    FOC_Init(); UART_SendStr("FOC init OK\r\n");
    Autotune_Init();
    /* Autotune_Init() очищает MotorParams; FOC остаётся источником истины
     * для default pole_pairs и сразу восстанавливает зеркало. */
    (void)FOC_SetPolePairs(FOC_GetPolePairs());
    UART_SendStr("Autotune OK\r\n");
    ENC_Init();      UART_SendStr("Encoder OK\r\n");
    VFC_Init();      UART_SendStr("V/f Ctrl OK\r\n");
    if(MapCapturePort_Init()) UART_SendStr("MapCapture port OK\r\n");
    else UART_SendStr("MapCapture port FAIL\r\n");
    /* Direct-SD break IRQ terminal stop outranks TIM6/foreground. */
    NVIC_SetPriority(TIM1_BRK_TIM15_IRQn, 0);
    NVIC_SetPriority(TIM8_BRK_IRQn, 0);
    NVIC_EnableIRQ(TIM1_BRK_TIM15_IRQn);
    NVIC_EnableIRQ(TIM8_BRK_IRQn);
    NVIC_SetPriority(TIM1_UP_TIM16_IRQn, 2);

    NVIC_EnableIRQ(TIM1_UP_TIM16_IRQn);   /* UIF → MapCapture_OnPeriod */
    /* SysTick ДО TIM6 (ревью main.c, п.4): TIM6 ISR использует sys_tick_ms —
     * иначе первые миллисекунды после старта TIM6 читают sys_tick_ms=0. */
    SysTick_Config(SystemCoreClock / 1000U);
    NVIC_SetPriority(SysTick_IRQn, 3);  /* ниже ADC(0)/TIM2(1)/TIM6(2) — п.17 */
    TIM6_Init_1kHz();
    IWDG_Init();      /* дельта OEW: watchdog от зависания MCU */
    NVIC_SetPriority(ADC1_2_IRQn, 0);
    NVIC_EnableIRQ(ADC1_2_IRQn);
    print_help();
    UART_SendStr("> ");
    uint32_t last_telem_ms = 0;
    CLI_State cli_state = {0};
    const CLI_Ops cli_ops = {
        .send = UART_SendStr,
        .send_telem = UART_SendTelemetry,
        .send_dbg = cli_send_dbg,
        .send_dbg_fmt = cli_send_dbg_fmt,
        .print_help = print_help,
        .swo_test = cli_swo_test,
        .tick_ms = cli_tick_ms,
        .adc_start = ADC_StartConversion,
        .adc_raw = cli_adc_raw,
        .adc_offsets = cli_adc_offsets,
        .adc_calibrate_256 = ADC_CalibrateOffsets_256,
        .adc_calibrate = ADC_CalibrateOffsets,
        .adc_irq_disable = cli_adc_irq_disable,
        .adc_irq_enable = cli_adc_irq_enable,
        .adc_diag = cli_adc_diag,
        .adc_counts = cli_adc_counts,
        .pwm_is_enabled = PWM_IsEnabled,
        .pwm_status = cli_pwm_status,
        .pwm_set_debug = PWM_DebugSetModulation,
        .pwm_dump = cli_pwm_dump,
        .pwm_dump8 = cli_pwm_dump8,
        .pwm_full_dump = cli_pwm_full,
        .pwm_sysinfo = cli_pwm_sysinfo,
        .pwm_set_deadtime = PWM_SetDeadTime_ns,
        .pwm_deadtime_reg = cli_pwm_deadtime_reg,
        .foc_start = FOC_Start,
        .foc_stop = FOC_Stop,
        .foc_is_running = FOC_IsRunning,
        .foc_set_speed = FOC_SetSpeed,
        .foc_get_speed = FOC_GetSpeed,
        .foc_set_current = cli_foc_current,
        .foc_set_pole_pairs = FOC_SetPolePairs,
        .foc_set_vdc_mv = FOC_SetVdcMv,
        .foc_set_base_speed = FOC_SetBaseSpeed,
        .foc_set_params = FOC_SetMotorParams,
        .foc_get_params = FOC_GetMotorParams,
        .foc_sigma_l = FOC_GetSigmaL_uH,
        .foc_set_pi = FOC_SetPIGains,
        .foc_params_applied = FOC_IsParamsApplied,
        .foc_vbus_mv = ADC_GetVbus_mV,
        .fault_is_active = PROTECT_IsFault,
        .fault_reason = PROTECT_GetFaultReason,
        .fault_request_clear = (int (*)(void))PROTECT_RequestClear,
        .vf_start = VFC_Start,
        .vf_stop = VFC_Stop,
        .vf_is_running = VFC_IsRunning,
        .vf_status = cli_vf_status,
        .vf_set_params = VFC_SetVfParams,
        .trig_high = TRIG_High,
        .trig_low = TRIG_Low,
        .encoder_status = cli_encoder_status,
        .autotune_run = cli_autotune_run,
        .autotune_abort_set = cli_autotune_abort,
        .autotune_print_curve = Autotune_PrintCurve,
        .autotune_print_params = Autotune_PrintParams,
        .autotune_print_stats = Autotune_PrintStats,
        .autotune_calc_pi = Autotune_CalcPI,
        .autotune_last_pi = Autotune_GetLastPI,
        .motor_get = cli_motor_get,
        .motor_set = cli_motor_set,
        .mapcap_command = cli_mapcap_command
    };
    while(1) {
        char linebuf[64];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
                if(rc > 0) {
            cli_state.vflog_period_ms = vflog_period_ms;
            cli_state.vflog_last_ms = vflog_last_ms;
            int cli_rc = CLI_ProcessLine(linebuf, &cli_ops, &cli_state);

            vflog_period_ms = cli_state.vflog_period_ms;
            vflog_last_ms = cli_state.vflog_last_ms;
            if(cli_rc == CLI_EXIT_LOOP) break;
        } else if(rc < 0) UART_SendStr("line overflow\r\n> ");

        if(cli_state.adc_stream_period_ms > 0 && (sys_tick_ms - cli_state.adc_stream_last_ms) >= cli_state.adc_stream_period_ms) {
            cli_state.adc_stream_last_ms = sys_tick_ms; ADC_StartConversion();
            UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
        }
        if(cli_state.adc_stream_period_ms == 0 && (sys_tick_ms - last_telem_ms) >= 100) {
            last_telem_ms = sys_tick_ms;
            if(VFC_IsRunning()) {
                UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n",
                    (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
                    (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
            } else {
                UART_SendTelemetry("@FOC:I1=%ld:I2=%ld:Ires=%ld:VBUS=%ld:STATE=%u:SPD=%ld:TH=%ld:FAULT=%d:FAULT_R=%d:FAIL=%d:RUN=%d\r\n",
                    ADC_GetI1_mA(), ADC_GetI2_mA(), ADC_GetIres_mA(), ADC_GetVbus_mV(),
                    (unsigned)FOC_GetState(), (long)FOC_GetMeasSpeedRPM(),
                    (long)FOC_GetThetaMilliRad(), PROTECT_IsFault(), PROTECT_GetFaultReason(),
                    FOC_GetStartupFailReason(), FOC_IsRunning());
            }
        }
    }
}
