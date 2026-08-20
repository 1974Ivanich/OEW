#include "stm32g474xx.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "uart.h"
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
            FOC_IsRunning,
            VFC_IsRunning,
            Autotune_IsActive,
            PWM_IsEnabled,
            ADC_InjectedIsArmed,
            PROTECT_IsFault,
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
    uint32_t last_telem_ms = 0, last_adc_stream_ms = 0, adc_stream_period_ms = 0;
    while(1) {
        char linebuf[64];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
        if(rc > 0) {
            unsigned int u1, u2, u3, u4;
            int a1=0, a2=0, a3=0, a4=0, a5=0, a6=0, a7=0, a8=0;
#if OEW_MAP_CAPTURE
            static uint32_t mapcap_next_id = 0;

#endif
            if(strcmp(linebuf, "a") == 0) {
                ADC_StartConversion();
                UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n> ", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
            }
            else if(sscanf(linebuf, "a=%u", &u1) == 1) {
                if(u1 == 0) { adc_stream_period_ms = 0; DBG_STR("ADC stream stopped\r\n> "); }
                else if(u1 >= 50 && u1 <= 1000) { adc_stream_period_ms = u1; last_adc_stream_ms = sys_tick_ms; DBG_FMT("ADC stream started: %u ms\r\n> ", u1); }
                else { DBG_STR("err: N must be 0 or 50..1000\r\n> "); }
            }
            else if(strcmp(linebuf, "a?") == 0) { UART_SendTelemetry("@ADC:STATUS:offset_i1=%u:stream=%lu\r\n> ", ADC_GetOffsetI1(), (unsigned long)adc_stream_period_ms); }
            else if(strcmp(linebuf, "c") == 0) {
                /* Калибровка single-shot'ами НЕСОВМЕСТИМА с вооружённой
                 * injected-группой (JADSTART ждёт TIM1_TRGO) — жёсткий запрет
                 * при работающем PWM (ревью pwm.c+adc.c, P1). */
                if(PWM_IsEnabled())
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else {
                    /* Ревью п.8: JEOSIE остаётся включённым, а калибровка сама
                     * гоняет injected программно → ISR мог бы читать JDR
                     * параллельно. Отключаем ADC IRQ на время калибровки
                     * (как в autotune-командах). */
                    NVIC_DisableIRQ(ADC1_2_IRQn);
                    ADC_CalibrateOffsets_256();
                    NVIC_EnableIRQ(ADC1_2_IRQn);
                    UART_SendTelemetry("@ADC:CAL:offset_i1=%u:offset_i2=%u:offset_ires=%u\r\n> ", ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIres());
                }
            }
            else if(strcmp(linebuf, "p?") == 0) {
                uint32_t cr1,ccer,bdtr,cnt; PWM_GetStatus(&cr1,&ccer,&bdtr,&cnt);
                UART_SendTelemetry("@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ", (unsigned long)cr1,(unsigned long)ccer,(unsigned long)bdtr,(unsigned long)cnt);
            }
            else if(sscanf(linebuf, "p=%u,%u,%u,%u", &u1, &u2, &u3, &u4) >= 3) {
                /* P0 (ревью pwm.c, п.8/10): debug-модуляция переконфигурирует
                 * TIM1/8 и делает UG — при вооружённой injected-группе
                 * (JADSTART=1) UG дал бы ложный TRGO и битое состояние ADC.
                 * Запрет при работающем PWM (как в команде 'c'). */
                if(PWM_IsEnabled())
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else {
                    /* GUI-01: mask 0 НЕ является stop — превращается во все
                 * 6 каналов (debug-дефолт). Стоп PWM — командой '0'
                 * (FOC_Stop → PWM_Disable → CEN/MOE off, EN LOW). */
                    PWM_DebugSetModulation((uint16_t)u1, (uint16_t)u2, u3, (uint8_t)u4);
                    UART_SendTelemetry("@PWM:OK:arr=%u:duty=%u:dt=%u\r\n> ", u1, u2, u3);
                }
            }
            else if(linebuf[0] == '1' && linebuf[1] == '\0') {
                if(PROTECT_IsFault()) DBG_STR("FAULT! send 'f' to clear\r\n> ");
                else {
                    VFC_Stop();
                    int rc = FOC_Start();
                    if(rc == FOC_START_OK) DBG_STR("FOC started\r\n> ");
                    else UART_SendTelemetry("@FOC:START:FAIL:rc=%d (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm)\r\n> ", rc);
                }
            }
            else if(linebuf[0] == '0' && linebuf[1] == '\0') { FOC_Stop(); DBG_STR("FOC stopped\r\n> "); }
#if OEW_MAP_CAPTURE
            /* Service-only map capture CLI (пакет OEW Service-Only Map Capture):
             * mapcap arm=<id>,<count>,<to>,<imax>,<vmin>,<vmax>,<sec>,<win>,
             *                  <t1a>,<t1b>,<t1c>,<t8a>,<t8b>,<t8c>,<trig>
             * mapcap run / mapcap drain / mapcap abort / mapcap status.
             * Только commissioning build; IRQ не маскируется; UART — только
             * из main loop (drain). */
            else if(strncmp(linebuf, "mcarm=", 6) == 0) {
                unsigned int profile_id;
                if(sscanf(linebuf + 6, "%u", &profile_id) != 1) {
                    UART_SendStr("err: mcarm=<profile_id>\r\n> ");
                } else {
                    MapCaptureRequest mcreq;
                    if(!MapCaptureProfile_BuildRequest(profile_id, ++mapcap_next_id, &mcreq)) {
                        UART_SendStr("@MC:ARM:BLOCKED:PROFILE\r\n> ");
                    } else {
                        MapCaptureStatus st = MapCapture_Arm(&mcreq);
                        UART_SendTelemetry("@MC:ARM:cap=%lu:rc=%d\r\n> ",
                                           (unsigned long)mcreq.capture_id, (int)st);
                    }
                }
            }            else if(strcmp(linebuf, "mapcap run") == 0) {
                MapCaptureStatus st = MapCapture_Run();
                UART_SendTelemetry("@MC:RUN:rc=%d\r\n> ", (int)st);
            }
            else if(strcmp(linebuf, "mapcap drain") == 0) {
                MapCaptureRecord rec;
                unsigned int n = 0u;
                while(MapCapture_ConsumeRecord(&rec)) {
                    UART_SendTelemetry("@MC:REC:cap=%lu:seq=%lu:raw_i1=%u:raw_i2=%u:raw_ct=%u:raw_vbus=%u:i1=%ld:i2=%ld:vbus=%ld:ccr1=%u,%u,%u:ccr8=%u,%u,%u:arr=%u:trig=%lu:status=%d:fault=%d\r\n",
                                       (unsigned long)rec.capture_id,
                                       (unsigned long)rec.frame.sequence,
                                       (unsigned)rec.frame.raw_idc1, (unsigned)rec.frame.raw_idc2,
                                       (unsigned)rec.frame.raw_ct, (unsigned)rec.frame.raw_vbus,
                                       (long)rec.frame.idc1_ma, (long)rec.frame.idc2_ma,
                                       (long)rec.frame.vbus_mv,
                                       (unsigned)rec.pwm.tim1_ccr[0], (unsigned)rec.pwm.tim1_ccr[1], (unsigned)rec.pwm.tim1_ccr[2],
                                       (unsigned)rec.pwm.tim8_ccr[0], (unsigned)rec.pwm.tim8_ccr[1], (unsigned)rec.pwm.tim8_ccr[2],
                                       (unsigned)rec.pwm.tim1_arr,
                                       (unsigned long)rec.pwm.trigger_revision,
                                       (int)rec.frame.status, (int)rec.fault_reason);
                    n++;
                }
                UART_SendTelemetry("@MC:DRAIN:records=%u\r\n> ", n);
            }
#if OEW_MAP_L3
            else if(strncmp(linebuf, "mapcap build=", 13) == 0) {
                unsigned int profile_id;
                if (sscanf(linebuf + 13, "%u", &profile_id) != 1) {
                    UART_SendStr("err: mapcap build=<profile>\r\n> ");
                } else {
                    mapcap_build_and_load((uint32_t)profile_id);
                }
            }
#endif
            else if(strcmp(linebuf, "mapcap abort") == 0) {
                UART_SendTelemetry("@MC:ABORT:rc=%d\r\n> ", (int)MapCapture_Abort());
            }
            else if(strcmp(linebuf, "mapcap status") == 0) {
                MapCaptureStats st;
                MapCapture_GetStats(&st);
                UART_SendTelemetry("@MC:STATUS:state=%d:term=%d:cap=%lu:frames=%u:dropped=%u:periods=%u:avail=%u\r\n> ",
                                   (int)st.state, (int)st.terminal_status,
                                   (unsigned long)st.capture_id,
                                   (unsigned)st.accepted_frames, (unsigned)st.dropped_records,
                                   (unsigned)st.periods_elapsed, (unsigned)st.records_available);
            }
#endif
            else if(linebuf[0] == 'm' && linebuf[1] == '\0') { print_help(); }
            else if(linebuf[0] == 's' && linebuf[1] == '\0') {
                SWO_Printf("@SWO:test:tick=%lu\r\n", (unsigned long)sys_tick_ms);
                UART_SendStr("SWO test sent\r\n> ");
            }
            else if(linebuf[0] == 'f' && linebuf[1] == '\0') {
                /* Ревью «План блокеров»: request-clear с детальным статусом.
                 * При работающем контроле (FOC/V-f) latch не снимаем —
                 * CONTROL_ACTIVE. Калибровка — только при полном стопе. */
                if(FOC_IsRunning() || VFC_IsRunning()) {
                    DBG_FMT("@FAULT:CLEAR:STATUS=%d\r\n> ", (int)PROTECT_CLEAR_CONTROL_ACTIVE);
                    DBG_STR("err: stop FOC/Vf first\r\n> ");
                } else {
                    ProtectClearStatus st = PROTECT_RequestClear();
                    if(st == PROTECT_CLEAR_OK) {
                        ADC_CalibrateOffsets();
                        DBG_STR("fault cleared\r\n> ");
                    } else {
                        DBG_FMT("@FAULT:CLEAR:STATUS=%d\r\n> ", (int)st);
                        DBG_STR("fault NOT cleared: Vbus/current still out of range\r\n> ");
                    }
                }
            }
            else if(linebuf[0] == 's' && linebuf[1] == '=') {
                long rpm_tmp = 0; char trail = '\0';  /* п.19 ревью: %ld → long, без (long*)&int32_t */
                int f = sscanf(linebuf + 2, "%ld%c", &rpm_tmp, &trail);
                int32_t rpm = (int32_t)rpm_tmp;
                if(f < 1) UART_SendStr("err: no digits\r\n> ");
                else if(f > 1 && trail != '\0') UART_SendStr("err: trailing chars\r\n> ");
                else if(rpm > 50000 || rpm < -50000) UART_SendStr("err: out of range\r\n> ");
                else { FOC_SetSpeed(rpm); DBG_FMT("speed=%ld rpm\r\n> ", (long)FOC_GetSpeed()); }
            } else if(strcmp(linebuf, "dump") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "dumpa") == 0) {
                /* Diagnostic: all ADC2 config registers */
                UART_SendTelemetry("@ADUMP:SQR1=0x%08lX:CFGR=0x%08lX:SMPR1=0x%08lX:JSQR=0x%08lX:DIFSEL=0x%08lX:CR=0x%08lX:ISR=0x%08lX:DR=0x%04lX:JDR1=0x%04lX:JDR2=0x%04lX:JDR3=0x%04lX:JDR4=0x%04lX\r\n> ",
                    (unsigned long)ADC2->SQR1, (unsigned long)ADC2->CFGR,
                    (unsigned long)ADC2->SMPR1, (unsigned long)ADC2->JSQR,
                    (unsigned long)ADC2->DIFSEL, (unsigned long)ADC2->CR,
                    (unsigned long)ADC2->ISR, (unsigned long)ADC2->DR,
                    (unsigned long)ADC2->JDR1, (unsigned long)ADC2->JDR2,
                    (unsigned long)ADC2->JDR3, (unsigned long)ADC2->JDR4);
            } else if(strcmp(linebuf, "dump8") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs8(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM8:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "pdump") == 0) {
                UART_SendTelemetry("@PWM:FULL:SYS=%lu:CFGR=0x%08lX:T1:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu:T8:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)RCC->CFGR,
                    (unsigned)TIM1->PSC, (unsigned)TIM1->ARR,
                    (unsigned)TIM1->CCR1, (unsigned)TIM1->CCR2, (unsigned)TIM1->CCR3,
                    (unsigned long)TIM1->BDTR, (unsigned long)TIM1->CCER, (unsigned long)TIM1->CR1,
                    (unsigned long)TIM1->CNT,
                    (unsigned)TIM8->PSC, (unsigned)TIM8->ARR,
                    (unsigned)TIM8->CCR1, (unsigned)TIM8->CCR2, (unsigned)TIM8->CCR3,
                    (unsigned long)TIM8->BDTR, (unsigned long)TIM8->CCER, (unsigned long)TIM8->CR1,
                    (unsigned long)TIM8->CNT);
            } else if(strcmp(linebuf, "sysinfo") == 0) {
                uint32_t psc, tclk;
                PWM_GetSysInfo(&psc, &tclk);
                UART_SendTelemetry("@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:PLLCFGR=0x%08lx:OVR=%lu:JEOS=%lu:TO=%lu:JQOVF=%lu\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)psc, (unsigned long)tclk,
                    (unsigned long)RCC->PLLCFGR, (unsigned long)ADC_GetOvrCount(),
                    (unsigned long)ADC_GetJeosCount(), (unsigned long)ADC_GetTimeoutCount(),
                    (unsigned long)ADC_GetJqovfCount());
            } else if(sscanf(linebuf, "pp=%u", &u1) == 1) {
                if(u1 < 1 || u1 > 24) UART_SendStr("err: pole pairs must be 1..24\r\n> ");
                else if(FOC_IsRunning() || VFC_IsRunning())
                    UART_SendStr("err: stop FOC/Vf first\r\n> ");
                else if(FOC_SetPolePairs((uint8_t)u1) == 0) {
                    /* Ревью MAIN-08: оба представления меняются ТОЛЬКО при
                     * успешном FOC_SetPolePairs (иначе рассинхрон с
                     * сохранёнными параметрами при работающем FOC). */
                    g_motor_params.pole_pairs = (uint8_t)u1;
                    UART_SendTelemetry("pole_pairs=%u\r\n> ", u1);
                } else UART_SendStr("err: pole pairs not applied\r\n> ");
            } else if(sscanf(linebuf, "vdc=%u", &u1) == 1) {
                /* Номинал шины для PI-расчётов (модульный оптимум kp~1/Vdc).
                 * Фактический Vbus измеряется независимо (VBUS= телеметрия). */
                if(u1 < 10 || u1 > 400) UART_SendStr("err: VDC must be 10..400 V\r\n> ");
                else if(FOC_SetVdcMv((int32_t)u1 * 1000) == 0)
                    UART_SendTelemetry("@VDC:OK:%lu mV (VBUS measured=%ld mV)\r\n> ",
                        (unsigned long)u1 * 1000, (long)ADC_GetVbus_mV());
                else UART_SendStr("err: VDC not set\r\n> ");
            } else if(sscanf(linebuf, "fwbase=%u", &u1) == 1) {
                /* FW-01: базовая скорость ослабления поля (speed gate). */
                if(u1 < 100 || u1 > 5000) UART_SendStr("err: FW base speed must be 100..5000 rpm\r\n> ");
                else if(FOC_SetBaseSpeed((int32_t)u1) == 0)
                    UART_SendTelemetry("fw_base_speed=%u rpm\r\n> ", u1);
                else UART_SendStr("err: can't set base speed\r\n> ");
            } else if(sscanf(linebuf, "dt=%u", &u1) == 1) {
                if(u1 > 12700) UART_SendStr("err: max 12700 ns\r\n> ");
                else if(PWM_SetDeadTime_ns(u1) != 0)
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else
                    UART_SendTelemetry("@PWM:DT=%u ns (DTG=%lu)\r\n> ", u1, (unsigned long)(TIM1->BDTR & 0xFF));
            } else if(strcmp(linebuf, "curve") == 0) {
                Autotune_PrintCurve();
                UART_SendStr("\r\n> ");
            } else if(strcmp(linebuf, "params") == 0) {
                Autotune_PrintParams();
                UART_SendTelemetry("@AP:%d\r\n> ", FOC_IsParamsApplied());
            } else if(strcmp(linebuf, "irot") == 0) {
                int8_t r = Autotune_Irot();
                if(r == 0) UART_SendStr("@IROT:OK\r\n> ");
                else       UART_SendStr("@IROT:FAIL\r\n> ");
            } else if(strcmp(linebuf, "inertia") == 0) {
                int8_t r = Autotune_Inertia();
                if(r == 0) UART_SendStr("@INERTIA:OK\r\n> ");
                else       UART_SendStr("@INERTIA:FAIL\r\n> ");
            } else if(strcmp(linebuf, "ch") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_DetectChannel();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:CH:OK\r\n> ");
                else       UART_SendStr("@AT:CH:FAIL\r\n> ");
            } else if(strcmp(linebuf, "chu") == 0 || strcmp(linebuf, "chv") == 0 || strcmp(linebuf, "chw") == 0) {
                /* Debug AT-03/06: возбуждение фазы U/V/W + отклик всех каналов
                 * со знаками (@DBG:CHx:DELTA:d_i1=...:d_i2=...:d_ires=...). */
                uint8_t _ph = (linebuf[2] == 'u') ? 0 : (linebuf[2] == 'v') ? 1 : 2;
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _rp = Autotune_ProbePhase(_ph);
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rp == 0) UART_SendTelemetry("@AT:CH%c:OK\r\n> ", linebuf[2]);
                else         UART_SendStr("@AT:CHP:FAIL\r\n> ");
            } else if(strcmp(linebuf, "iv") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_MeasureRs_IV();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:IV:OK\r\n> ");
                else       UART_SendStr("@AT:IV:FAIL\r\n> ");
            } else if(strcmp(linebuf, "pairs") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_MeasureAllPairs();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:PAIRS:RESULT_OK\r\n> ");
                else       UART_SendStr("@AT:PAIRS:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "abort") == 0) {
                g_autotune_abort = 1;
                UART_SendStr("abort requested\r\n> ");
            } else if(strcmp(linebuf, "oew") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _ro = Autotune_MeasureLs_OEW(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_ro == 0) UART_SendStr("@AT:OEW:RESULT_OK\r\n> "); else if(_ro == -5) UART_SendStr("@AT:OEW:ABORTED\r\n> "); else UART_SendStr("@AT:OEW:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "rr") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rr = Autotune_MeasureRr(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rr == 0) UART_SendStr("@AT:RR:RESULT_OK\r\n> "); else if(_rr == -6) UART_SendStr("@AT:RR:ABORTED\r\n> "); else UART_SendStr("@AT:RR:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "noload") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rn = Autotune_MeasureNoLoad(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rn == 0) UART_SendStr("@AT:NOLOAD:RESULT_OK\r\n> "); else if(_rn == -6) UART_SendStr("@AT:NOLOAD:ABORTED\r\n> "); else UART_SendStr("@AT:NOLOAD:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "scope") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rs = Autotune_Scope(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rs == 0) UART_SendStr("@SCOPE:RESULT_OK\r\n> "); else UART_SendStr("@SCOPE:RESULT_FAIL\r\n> ");
            } else if(sscanf(linebuf, "pi=%u", &u1) == 1) {
                Autotune_CalcPI((int32_t)u1);
                UART_SendStr("> ");
            }
            else if(strcmp(linebuf, "lspos") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rl = Autotune_MeasureLs_Position(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rl == 0) UART_SendStr("@AT:LSPOS:RESULT_OK\r\n> "); else if(_rl == -5) UART_SendStr("@AT:LSPOS:ABORTED\r\n> "); else UART_SendStr("@AT:LSPOS:RESULT_FAIL\r\n> ");
            } else if(sscanf(linebuf, "mp=%d,%d,%d,%d,%d,%d,%d,%d", &a1,&a2,&a3,&a4,&a5,&a6,&a7,&a8) >= 2) {
                /* Сначала формируем согласованный снимок всех параметров,
                 * затем ровно один раз применяем его к FOC. Так первый mp=
                 * использует свежие Rr/Lm/Tr для расчёта Lsigma. */
                g_motor_params.Rs_mOhm = a1;
                g_motor_params.Ls_uH   = a2;
                if(a3 > 0) g_motor_params.Rr_mOhm = a3;
                if(a4 > 0) g_motor_params.Lm_uH = a4;
                if(a5 > 0) g_motor_params.Tr_rotor_us = a5;
                if(a6 > 0) g_motor_params.Ke_mV_per_rpm = a6;
                if(a7 > 0) g_motor_params.pole_pairs = (uint8_t)a7;
                if(a8 > 0) g_motor_params.J_kg_m2_x1e6 = a8;
                g_motor_params.measured_mask |= AT_VALID_RS | AT_VALID_LS;
                if(a3 > 0) g_motor_params.measured_mask |= AT_VALID_RR;
                if(a4 > 0) g_motor_params.measured_mask |= AT_VALID_LM;
                if(a5 > 0) g_motor_params.measured_mask |= AT_VALID_TR;
                if(a6 > 0) g_motor_params.measured_mask |= AT_VALID_KE;
                if(a7 > 0) g_motor_params.measured_mask |= AT_VALID_PAIRS;
                if(a8 > 0) g_motor_params.measured_mask |= AT_VALID_J;
                int _rc = FOC_SetMotorParams(a1, a2, (int32_t)ADC_GetVbus_mV());
                if(_rc == 0) {
                    if(a7 >= 1 && a7 <= 24) (void)FOC_SetPolePairs(a7);
                    int32_t _kp, _ki, _lsig;
                    FOC_GetMotorParams(NULL, NULL, &_kp, &_ki);
                    _lsig = FOC_GetSigmaL_uH();
                    UART_SendTelemetry("@MP:OK:Rs=%d:Ls=%d:Rr=%d:Lm=%d:Tr=%d:Ke=%d:p=%d:J=%d:Kp=%d:Ki=%d:Lsig=%d:AP=1\r\n> ", a1,a2,a3,a4,a5,a6,a7,a8,_kp,_ki,_lsig);
                } else {
                    UART_SendTelemetry("@MP:ERROR:%d\r\n> ", _rc);
                }
            } else if(strcmp(linebuf, "mpapply") == 0) {
                int _rc = FOC_SetMotorParams(g_motor_params.Rs_mOhm,
                                             g_motor_params.Ls_uH,
                                             ADC_GetVbus_mV());
                if(_rc == 0) {
                    if(g_motor_params.pole_pairs >= 1 && g_motor_params.pole_pairs <= 24)
                        FOC_SetPolePairs(g_motor_params.pole_pairs);
                    int32_t _kp, _ki;
                    FOC_GetMotorParams(NULL, NULL, &_kp, &_ki);
                    int32_t _lsig = FOC_GetSigmaL_uH();
                    UART_SendTelemetry("@MPAPPLY:OK:Rs=%ld:Ls=%ld:Rr=%ld:Lm=%ld:Tr=%ld:p=%d:Kp=%ld:Ki=%ld:Lsig=%ld:AP=1\r\n> ",
                        (long)g_motor_params.Rs_mOhm, (long)g_motor_params.Ls_uH,
                        (long)g_motor_params.Rr_mOhm, (long)g_motor_params.Lm_uH,
                        (long)g_motor_params.Tr_rotor_us, (int)g_motor_params.pole_pairs,
                        (long)_kp, (long)_ki, (long)_lsig);
                } else {
                    UART_SendTelemetry("@MPAPPLY:ERROR:%d\r\n> ", _rc);
                }
            } else if(strcmp(linebuf, "piapply") == 0) {
                int32_t _kp, _ki, _bw;
                if(Autotune_GetLastPI(&_kp, &_ki, &_bw) == 0) {
                    int _rc = FOC_SetPIGains(_kp, _ki);
                    if(_rc == 0) UART_SendTelemetry("@PI:APPLIED:Kp=%ld:Ki=%ld:AP=1\r\n> ", (long)_kp, (long)_ki);
                    else UART_SendTelemetry("@PI:ERROR:%d\r\n> ", _rc);
                } else {
                    UART_SendStr("@PI:ERROR:NOT_CALCULATED\r\n> ");
                }
            } else if(strcmp(linebuf, "stats") == 0) {
                Autotune_PrintStats();
                UART_SendStr("> ");
            } else if(strcmp(linebuf, "idle") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_Idle();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0)      UART_SendStr("@IDLE:OK\r\n> ");
                else if(_r == -5) UART_SendStr("@IDLE:ABORTED\r\n> ");
                else             UART_SendStr("@IDLE:FAIL\r\n> ");
            }
            /* ── V/f control + encoder commands ── */
            else if(sscanf(linebuf, "i=%d,%d", &a1, &a2) == 2) {
                /* Ревью GUI-02: команда задания токов (мА); 0,0 = контур
                 * скорости. Clamp к FOC_I_MAX_MA внутри FOC_SetIdRef/SetIqRef. */
                FOC_SetIdRef(a1);
                FOC_SetIqRef(a2);
                UART_SendTelemetry("@I:OK:Id=%ld:Iq=%ld\r\n> ", (long)a1, (long)a2);
            } else if(sscanf(linebuf, "vf=%d", &a1) == 1) {
                if(a1 == 0) {
                    VFC_Stop();
                    vflog_period_ms = 0;   /* авто-стоп лога вместе с V/f */
                    TRIG_Low();
                    UART_SendStr("V/f stopped\r\n> ");
                } else if(a1 >= -5000 && a1 <= 5000) {
                    if(PROTECT_IsFault()) {
                        UART_SendStr("FAULT! send 'f' to clear\r\n> ");
                    } else {
                        FOC_Stop();
                        /* Аппаратный триггер СРАЗУ перед VFC_Start(): фронт PB6 виден
                         * на sigrok, а trig_tick — тот же sys_tick_ms, что публикуется
                         * в @VFLOG:t=... — точная привязка UART-лога к захвату лог.
                         * анализатора без программной оценки задержки USB/UART. */
                        TRIG_High();
                        uint32_t trig_tick = sys_tick_ms;
                        int vfc_rc = VFC_Start(a1);
                        if(vfc_rc != VFC_START_OK) {
                            UART_SendTelemetry("V/f blocked: rc=%d (sample context unverified)\r\n> ",
                                               vfc_rc);
                            vflog_period_ms = 0;
                            TRIG_Low();
                            break;
                        }
                        /* авто-старт лога вместе с V/f, если не включен вручную заранее */
                        if(vflog_period_ms == 0) vflog_period_ms = VFLOG_DEFAULT_PERIOD_MS;
                        vflog_last_ms = sys_tick_ms;
                        UART_SendTelemetry("V/f started: %d rpm\r\n@TRIG:tick=%lu\r\n> ",
                            a1, (unsigned long)trig_tick);
                    }
                } else {
                    UART_SendStr("err: rpm range -5000..+5000\r\n> ");
                }
            } else if(sscanf(linebuf, "vflog=%u", &u1) == 1) {
                if(u1 == 0) { vflog_period_ms = 0; UART_SendStr("vflog stopped\r\n> "); }
                else if(u1 >= 10 && u1 <= 1000) {
                    vflog_period_ms = u1; vflog_last_ms = sys_tick_ms;
                    UART_SendTelemetry("vflog started: %u ms\r\n> ", u1);
                } else { UART_SendStr("err: N must be 0 or 10..1000\r\n> "); }
            } else if(strcmp(linebuf, "vf?") == 0) {
                UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n> ",
                    (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
                    (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
            } else if(strcmp(linebuf, "enc") == 0) {
                UART_SendTelemetry("@ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u\r\n> ",
                    (unsigned)ENC_GetAngle14(), (long)ENC_GetSpeed_rpm(),
                    (unsigned long)ENC_GetPeriod_us(), (unsigned long)ENC_GetPulseWidth_us(),
                    (unsigned)ENC_GetError());
            } else if(sscanf(linebuf, "vfk=%d,%d", &a1, &a2) == 2) {
                VFC_SetVfParams(a1, a2);
                /* Ревью MAIN-11: печатаем ФАКТИЧЕСКИ применённые значения
                 * (SetVfParams молча отклоняет вне диапазона). */
                UART_SendTelemetry("V/f params: boost=%ld%% rated=%ldHz\r\n> ",
                    (long)vfc.v_boost_pct, (long)vfc.rated_freq_hz);
            } else {
                UART_SendStr("unknown\r\n> ");
            }
        } else if(rc < 0) UART_SendStr("line overflow\r\n> ");

        if(adc_stream_period_ms > 0 && (sys_tick_ms - last_adc_stream_ms) >= adc_stream_period_ms) {
            last_adc_stream_ms = sys_tick_ms; ADC_StartConversion();
            UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
        }
        if(adc_stream_period_ms == 0 && (sys_tick_ms - last_telem_ms) >= 100) {
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
