/* USER CODE BEGIN Header */
/**
******************************************************************************
* @file           : main.c
* @brief          : Ultimate OEW Motor Control (FOC Only + ZSC PI + Field Weakening + OEW Phase Shift)
* @version        : 2.0 (Pure FOC)
******************************************************************************
*/
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "stm32g4xx_hal_flash.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum {
    ZSC_ATUNE_IDLE = 0, ZSC_ATUNE_RUNNING = 1, ZSC_ATUNE_DONE = 2, ZSC_ATUNE_FAILED = 3
} ZscAtuneState_t;

typedef struct {
    ZscAtuneState_t state;
    float relay_amp, izs_prev, peak_pos, peak_neg, half_period_sum;
    uint32_t half_periods, ticks, ticks_since_cross;
    float result_kp, result_ki;
} ZscAutotune_t;

typedef struct {
    float ia, ib, ic;
    float ia_raw, ib_raw, ic_raw;
    float off_a, off_b, off_c;
    float scale_a, scale_b, scale_c;
    uint8_t calibrated;
    uint32_t calib_cnt;
    float rms2_lpf;
} CurrentMeas_t;

typedef struct {
    float kp, ki, integ, out_min, out_max;
} PI_t;

/* Адаптивный компенсатор мёртвого времени.
   Kdt хранится в отсчётах ШИМ (0..HALF_PERIOD).
   corr_lpf — узкополосный синхронно-детектированный сигнал невязки.
   e_norm_lpf — LPF |e_alpha, e_beta| для диагностики и автоматического определения сходимости. */
typedef struct {
    float Kdt;                /* текущий коэффициент компенсации, отсчёты ШИМ */
    float mu;                 /* скорость адаптации, отсчётов/(А·с) */
    float sign_flip;          /* +1 / -1: эмпирический знак градиента */
    float corr_lpf;           /* LPF корреляции e*sign(i) */
    float e_norm_lpf;         /* LPF |e|, А */
    float i_alpha_last;       /* для телеметрии */
    float i_beta_last;        /* для телеметрии */
    uint8_t enabled;          /* 1 = адаптация активна */
    uint8_t compensate;       /* 1 = компенсация применяется к duty */
    uint8_t converged;        /* 1 = |corr_lpf| < порога длительное время */
    uint32_t hold_ticks;      /* счётчик hold-периода после входа в FOC */
    uint32_t converged_ticks; /* счётчик непрерывного нахождения в пороге */
    uint32_t adapt_ticks;     /* всего тактов с активной адаптацией */
    uint32_t sat_cooldown;    /* анти-спам событий Kdt min/max */
    uint32_t sat_reported;    /* флаг: уже сообщили о срабатывании лимита */
} DTC_t;
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define PWM_PERIOD          8500            /* 10 кГц ISR: 170e6/(2·8500) = 10000 Гц */
#define HALF_PERIOD         (PWM_PERIOD / 2)

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif
#define TWO_PI              6.28318530718f
#define SQRT3               1.7320508f
#define SQRT3_OVER_2        0.8660254f
#define ONE_OVER_SQRT3      0.57735026919f

#define SPEED_RATE          100.0f
#define POWER_RATE          2.0f
#define MAX_POWER           0.98f
#define MAX_SPEED_ELEC_HZ   400.0f

#define CORDIC_TIMEOUT_CYCLES   1000U
#define EXPECTED_ISR_HZ         10000.0f
#define ISR_HZ_TOLERANCE        0.05f
#define CURR_IIR_ALPHA          0.2f
#define CURR_CALIB_SAMPLES      2000U

#define I_DIDT_MAX_PER_TICK_DEFAULT  1.5f
#define I_PHASE_ABS_MAX_A_DEFAULT    8.0f
#define I_RMS_MAX_A_DEFAULT          5.0f
#define I_RMS_LPF_ALPHA              0.01f

#define PI_KP_DEFAULT   0.08f
#define PI_KI_DEFAULT   20.0f

float I_PHASE_ABS_MAX_A = I_PHASE_ABS_MAX_A_DEFAULT;
float I_RMS_MAX_A = I_RMS_MAX_A_DEFAULT;
float I_DIDT_MAX_PER_TICK = I_DIDT_MAX_PER_TICK_DEFAULT;

#define ZSC_KP          0.05f
#define ZSC_KI          0.5f
#define ZSC_OUT_MAX     ((float)HALF_PERIOD * 0.15f)

#define ZSC_ATUNE_RELAY_AMP    (ZSC_OUT_MAX * 0.8f)
#define ZSC_ATUNE_MIN_PERIODS  6U
#define ZSC_ATUNE_MAX_TICKS    50000U

#define AUTO_CALIB_TIMEOUT_MS  1000U
#define AUTO_SPIN_TIME_MS      2000U
#define AUTO_ZSC_TIMEOUT_MS    5000U

#define FOC_MIN_STARTUP_HZ     15.0f
#define FOC_LOST_HZ            5.0f

/* --- Опциональные расширенные функции --- */
#define IWDG_WATCHDOG_ENABLE     0   /* 1 = независимый watchdog */

/* --- Аппаратная защита силового каскада --- */
#define PWM_DEADTIME             255      /* ~2.5 мкс при 170 МГц */
#define PWM_BREAK_POLARITY_LOW   1        /* 1 = активный ноль (стандарт для STP/IGBT) */

/* =====================================================================
 *  Адаптивная компенсация мёртвого времени (Adaptive Dead-Time Compensator)
 *  ---------------------------------------------------------------------
 *  Kdt не задаётся константой, а медленно дрейфует так, чтобы минимизировать
 *  диагностическую невязку наблюдателя (e_alpha, e_beta). Алгоритм:
 *
 *    corr  = e_alpha * sign(i_alpha) + e_beta * sign(i_beta)     (синхронный детектор)
 *    corr_lpf += a_corr * (corr - corr_lpf)                       (узкополосный LPF)
 *    Kdt   += sign_flip * mu * corr_lpf * dt                      (градиентный шаг)
 *
 *  При недокомпенсации ток содержит 6-ю гармонику (в dq), коррелирующую с
 *  sign(i). Корреляция имеет детерминированный знак, поэтому Kdt сходится к
 *  значению, компенсирующему суммарную систематическую ошибку силового каскада
 *  (dead-time + падение на MOSFET + задержка драйвера + finite-slew).
 *
 *  sign_flip = +1 по умолчанию; если Kdt ползёт к 0 или к максимуму —
 *  инвертируйте знак командой "dtcsign -1".
 * ===================================================================== */
#define DTC_DEFAULT_KDT         20.0f    /* стартовое значение Kdt, отсчёты ШИМ */
#define DTC_KDT_MIN             0.0f
#define DTC_KDT_MAX             400.0f   /* ~9.4% от HALF_PERIOD=4250 */
#define DTC_ADAPT_MU            60.0f    /* скорость адаптации, отсчётов/(А·с) */
#define DTC_CORR_LPF_ALPHA      0.0004f  /* LPF корреляции, тау≈250 мс при 10 кГц */
#define DTC_ENORM_LPF_ALPHA     0.001f   /* LPF |e| для диагностики, тау≈250 мс при 10 кГц */
#define DTC_SIGN_FLIP_DEFAULT   1.0f     /* +1 или -1 — эмпирически */
#define DTC_MIN_SPEED_HZ        5.0f     /* блокировка адаптации на малой скорости */
#define DTC_MIN_CURRENT_A       0.3f     /* блокировка при холостом ходе */
#define DTC_STARTUP_HOLD_TICKS  6000U    /* 0.6 с после входа в FOC при 10 кГц */
#define DTC_CONVERGED_THRESH_A  0.015f   /* |corr_lpf| < порога = converged */
#define DTC_CONVERGED_TICKS     30000U   /* 3 с в пороге при 10 кГц */
#define DTC_SAT_TICKS_REPORT    10000U   /* анти-спам событий срабатывания лимитов */

/* =====================================================================
 *  Flash Black Box — непрерывная запись FOC-состояния во flash bank 2
 *  ---------------------------------------------------------------------
 *  32-байтная packed-запись пишется в SRAM ring buffer из ISR (10 Гц),
 *  затем дренируется в main loop во flash bank 2 (0x08040000, 256 КБ).
 *  Выгрузка по команде dumpflash. Переживает сброс питания.
 *  Ёмкость: 8192 записей × 100 мс = ~13 минут сеанса.
 * ===================================================================== */
#define FLASH_LOG_BASE    0x08040000U   /* bank 2 start */
#define FLASH_LOG_SIZE    0x00040000U   /* 256 KB */
#define FLASH_PAGE_SIZE   0x0800U       /* 2 KB */
#define FLASH_LOG_PAGES   (FLASH_LOG_SIZE / FLASH_PAGE_SIZE)  /* 128 */
#define FLASH_SNAP_SIZE   32U           /* packed record size */
#define FLASH_SNAPS_PER_PAGE  (FLASH_PAGE_SIZE / FLASH_SNAP_SIZE)  /* 64 */
#define SAT_FAULT_BIT    0x80U   /* fault flag encoded in sat_flags bit 7 */

/* SRAM staging buffer: ISR пишет, main loop дренирует */
#define SNAP_SRAM_SIZE    64U
typedef struct __attribute__((packed)) {
    uint32_t ts;            /* g_isr_ts */
    int16_t id, iq;         /* А x1000 */
    int16_t vd, vq;         /* о.е. x1000 */
    int16_t wr;             /* Гц x10 */
    int16_t slip;           /* Гц x100 */
    int16_t te;             /* Нм x1000 */
    int16_t psi;            /* Вб x10000 */
    uint8_t  sat_flags;     /* SAT_* bits + SAT_FAULT_BIT if fault */
    uint8_t  foc_state;     /* 0=IDLE,1=RAMPING,2=RUNNING */
    int16_t  p_elec, q_elec;/* Вт,ВАр x1000 */
    int16_t  e_alpha, e_beta;/* x1000 */
    uint16_t kdt;           /* x10 */
} FlashSnap_t;
static_assert(sizeof(FlashSnap_t) == FLASH_SNAP_SIZE, "FlashSnap_t must be 32 bytes");

typedef struct {
    volatile uint16_t head;     /* ISR write index */
    volatile uint16_t tail;     /* main loop read index */
    volatile uint8_t  overflow; /* флаг переполнения SRAM buffer */
} SnapCtrl_t;
static FlashSnap_t snap_buf[SNAP_SRAM_SIZE];
static SnapCtrl_t snap_ctrl = {0};

/* Flash log state */
typedef struct {
    uint32_t write_addr;        /* следующий адрес для записи */
    uint32_t record_count;      /* всего записано */
    uint8_t  enabled;           /* запись активна */
    uint8_t  full;              /* flash заполнен */
} FlashLog_t;
static FlashLog_t g_flash_log = { .write_addr = FLASH_LOG_BASE, .enabled = 1U };
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

__IO uint32_t BspButtonState = BUTTON_RELEASED;
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;

CORDIC_HandleTypeDef hcordic;

TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim8;

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
volatile float Target_Speed = 50.0f;
volatile float Target_Power = 0.5f;
float current_speed = 0.0f;
float current_power = 0.0f;
float ugol1 = 0.0f;
volatile uint32_t isr_tick_count = 0U;
volatile uint32_t g_isr_ts = 0U;   /* монотонный счетчик тактов ISR (не сбрасывается) */
volatile float measured_isr_hz = EXPECTED_ISR_HZ;
volatile float control_isr_dt = (1.0f / EXPECTED_ISR_HZ);
volatile float Target_Current_A = 2.0f;
volatile uint8_t Fault_OverCurrent = 0U;
volatile uint8_t Run_Enabled = 0U;

/* scale_a отрицательный: датчик тока фазы A инвертирован (подтверждено testL 12.07.2026) */
CurrentMeas_t g_curr = { .scale_a = -0.012788f, .scale_b = 0.012788f, .scale_c = 0.012788f };
CurrentMeas_t g_curr2 = { .scale_a = 0.012788f, .scale_b = 0.012788f, .scale_c = 0.012788f };

PI_t g_i_pi = { .kp = PI_KP_DEFAULT, .ki = PI_KI_DEFAULT, .integ = 0.0f, .out_min = 0.0f, .out_max = MAX_POWER };
PI_t g_zsc_pi = { .kp = ZSC_KP, .ki = ZSC_KI, .integ = 0.0f, .out_min = -ZSC_OUT_MAX, .out_max = ZSC_OUT_MAX };
ZscAutotune_t g_zsc_atune = { .state = ZSC_ATUNE_IDLE };

/* Адаптивный компенсатор мёртвого времени. Стартует с безопасного значения,
   сходится к оптимальному под конкретные MOSFET/драйвер/температуру/Vdc. */
DTC_t g_dtc = {
    .Kdt = DTC_DEFAULT_KDT,
    .mu = DTC_ADAPT_MU,
    .sign_flip = DTC_SIGN_FLIP_DEFAULT,
    .enabled = 1U,
    .compensate = 1U,
};

static float i_prev_a = 0.0f, i_prev_b = 0.0f, i_prev_c = 0.0f;

volatile uint8_t Stream_Enabled = 0;
volatile float izs_val = 0.0f;

static volatile uint16_t last_raw_a1 = 0U, last_raw_b1 = 0U, last_raw_c1 = 0U;
static volatile uint16_t last_raw_a2 = 0U, last_raw_b2 = 0U, last_raw_c2 = 0U;

/* Кольцевой burst-захват с пре-триггером: запись идет непрерывно,
   по триггеру (fault или команда capture) дописывается BURST_POST_TRIG
   отсчетов и буфер замораживается для выгрузки (половина окна — до события).
   BURST_DECIM=2: запись каждого 2-го такта ISR — окно ~200 мс при 10 кГц. */
#define BURST_CAP_SIZE   1024U
#define BURST_POST_TRIG  (BURST_CAP_SIZE / 2U)
#define BURST_DUMP_CHUNK 16
#define BURST_DECIM      2U
typedef struct {
    volatile uint8_t  triggered;
    volatile uint8_t  busy;
    volatile uint8_t  auto_triggered;  /* 1 = fault/FOC_LOST, 0 = manual capture */
    volatile uint8_t  decim_cnt;       /* счётчик децимации записи */
    volatile uint16_t head;
    volatile uint16_t filled;
    volatile uint16_t post_left;
    volatile uint16_t trig_idx;
    uint32_t isr_hz;
} BurstCtrl_t;
typedef struct {
    uint32_t ts;                 /* g_isr_ts на момент выборки */
    int16_t ia1, ib1, ic1;       /* А x1000 */
    int16_t ia2, ib2, ic2;
    int16_t izs;                 /* А x1000 */
    uint16_t ang;                /* 0..65535 = 0..2pi */
    int16_t id, iq, id_t, iq_t;  /* А x1000 (факт и задания) */
    int16_t vd, vq;              /* о.е. x1000 (после circle limiter) */
    int16_t vd_raw, vq_raw;      /* о.е. x1000 (до circle limiter) */
    int16_t wr;                  /* Гц x10 */
    int16_t slip;                /* Гц x100 */
    int16_t psi;                 /* Вб x10000 */
    int16_t te;                  /* Нм x1000 */
    int16_t pi_id_i, pi_iq_i, pi_spd_i; /* интеграторы PI x1000 */
    uint8_t  sat_flags;          /* битовая маска SAT_* */
    uint8_t  _pad;
    int16_t e_alpha, e_beta;     /* невязки наблюдателя x1000 */
    int16_t tgt_spd;             /* целевая скорость Гц x10 */
    int16_t theta_jit;           /* джиттер угла x10000 рад */
    int16_t diq_dt, dwr_dt;     /* производные Iq и wr x1000 */
    int16_t p_elec, q_elec;     /* активная/реактивная мощность x1000 */
    int16_t kdt;                 /* Kdt адаптивный x10 (отсчёты ШИМ) */
    int16_t dtc_corr;            /* corr_lpf x10000 (А) */
    uint16_t d1a, d1b, d1c;
    uint16_t d2a, d2b, d2c;
} BurstSample_t;
static BurstSample_t  burst_buf[BURST_CAP_SIZE];
static BurstCtrl_t    burst = {0};

/* --- Журнал событий (причины fault, переходы FOC, результаты автотюна) --- */
typedef enum {
    EVT_NONE = 0, EVT_FAULT_DIDT, EVT_FAULT_IABS, EVT_FAULT_IRMS,
    EVT_FOC_ENGAGED, EVT_FOC_LOST, EVT_ZSC_TUNED, EVT_ZSC_FAILED,
    EVT_DTC_CONVERGED, EVT_DTC_SAT_MIN, EVT_DTC_SAT_MAX
} EvtCode_t;
typedef struct { uint32_t ts; uint8_t code; float v0, v1, v2; } LogEvent_t;
#define EVT_LOG_SIZE 16U
static LogEvent_t evt_log[EVT_LOG_SIZE];
static volatile uint8_t evt_head = 0U, evt_count = 0U;

/* --- Кольцевой буфер UART TX (неблокирующая передача через IT) --- */
#define UART_TX_RING_SIZE 16384U
static uint8_t tx_ring[UART_TX_RING_SIZE];
static volatile uint16_t tx_ring_head = 0U, tx_ring_tail = 0U;
static volatile uint8_t  tx_ring_busy = 0U;
static volatile uint32_t tx_dropped = 0U;
static uint8_t tx_chunk[64];

/* --- Флаги насыщения (битовая маска) --- */
#define SAT_CIRCLE   0x01u  /* circle limiter урезал vq */
#define SAT_FW       0x02u  /* field weakening активен */
#define SAT_POWER    0x04u  /* Target_Power упёрся в MAX_POWER */
#define SAT_DUTY     0x08u  /* duty упёрся в 0 или PWM_PERIOD */
#define SAT_SPEED    0x10u  /* speed PI на пределе out_min/out_max */
#define SAT_CURRENT  0x20u  /* ток близок к I_RMS_MAX */

/* --- Замер времени выполнения ISR через DWT->CYCCNT --- */
static volatile uint32_t isr_cycles_max = 0U;
static volatile uint32_t isr_cycles_sum = 0U;
static volatile uint32_t isr_cycles_cnt = 0U;
static volatile uint32_t isr_us_avg = 0U;   /* мкс, обновляется каждую 1 с */
static volatile uint32_t isr_us_max = 0U;   /* мкс, обновляется каждую 1 с */

/* Диагностические переменные ISR для burst-записи (обновляются в FOC_RUNNING) */
static float s_theta_jit = 0.0f, s_diq_dt = 0.0f, s_dwr_dt = 0.0f;

#define UART_RX_BUF_SIZE 64U
static uint8_t uart_rx_byte = 0U;
static volatile char uart_rx_buf[UART_RX_BUF_SIZE];
static volatile uint16_t uart_rx_len = 0U;
static volatile uint8_t  uart_rx_ready = 0U;

/* Флаги для безопасной печати из главного цикла (вместо printf в ISR) */
volatile uint8_t print_foc_engaged = 0U;
volatile uint8_t print_foc_lost = 0U;
volatile uint8_t print_testl = 0U;
volatile uint8_t print_dtc_converged = 0U;
volatile float   print_dtc_kdt_val = 0.0f;
volatile float   print_dtc_corr_val = 0.0f;

/* --- FOC Math Definitions --- */
typedef struct {
    float Rs, Rr, Ls, Lr, Lm, p_pairs;
} MotorParams_t;

typedef struct {
    float psi_alpha, psi_beta, theta, omega_r;
    float id, iq, vd, vq;
    float omega_slip;            /* рад/с (эл.) */
    float psi_mag;               /* Вб */
    float te_est;                /* Нм, оценка момента */
    float id_target, iq_target;  /* задания токовых контуров */
    float vd_raw, vq_raw;        /* напряжения до circle limiter */
    uint8_t sat_flags;           /* битовая маска SAT_* */
    float e_alpha, e_beta;       /* невязки наблюдателя (измерённый - предсказанный ток) */
    float p_elec, q_elec;        /* оценка активной и реактивной мощности */
    float theta_prev;            /* угол на предыдущем такте для расчёта джиттера */
    float iq_prev, wr_prev;      /* предыдущие значения для производных */
} FOC_State_t;

MotorParams_t g_motor = { .Rs = 0.5f, .Rr = 0.3f, .Ls = 0.001f, .Lr = 0.001f, .Lm = 0.0009f, .p_pairs = 2.0f };
FOC_State_t g_foc = {0};

typedef enum {
    FOC_STARTUP_IDLE = 0,
    FOC_STARTUP_RAMPING = 1,
    FOC_STARTUP_RUNNING = 2
} FocStartupState_t;
volatile FocStartupState_t foc_startup_state = FOC_STARTUP_IDLE;

PI_t g_pi_id = { .kp = 0.5f, .ki = 500.0f, .out_min = -1.0f, .out_max = 1.0f };
PI_t g_pi_iq = { .kp = 0.5f, .ki = 500.0f, .out_min = -1.0f, .out_max = 1.0f };
PI_t g_pi_speed = { .kp = 0.5f, .ki = 10.0f, .out_min = -5.0f, .out_max = 5.0f };

/* --- Auto-Start State Machine --- */
typedef enum {
    AUTO_START_IDLE = 0,
    AUTO_START_CALIB = 1,
    AUTO_START_SPIN = 2,
    AUTO_START_ZSC = 3,
    AUTO_START_DONE = 4
} AutoStartState_t;
volatile AutoStartState_t auto_start_state = AUTO_START_IDLE;
static float autosave_speed = 0;
static float autosave_power = 0;
static float autosave_current = 0;
static uint32_t auto_start_t0 = 0U;

// Регулятор ослабления поля (Field Weakening PI)
PI_t g_pi_fw = { .kp = 0.1f, .ki = 50.0f, .out_min = -2.0f, .out_max = 0.0f, .integ = 0.0f };
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_TIM1_Init(void);
static void MX_TIM8_Init(void);
static void MX_CORDIC_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */
static void uart2_tx_enqueue(const uint8_t *p, uint16_t n);
static uint16_t uart2_tx_free(void);

static void evt_push(uint8_t code, float v0, float v1, float v2) {
    LogEvent_t *e = &evt_log[evt_head];
    e->ts = g_isr_ts; e->code = code; e->v0 = v0; e->v1 = v1; e->v2 = v2;
    evt_head = (uint8_t)((evt_head + 1U) % EVT_LOG_SIZE);
    if (evt_count < EVT_LOG_SIZE) evt_count++;
}
static const char* evt_name(uint8_t code) {
    switch (code) {
        case EVT_FAULT_DIDT:    return "FAULT_DIDT";
        case EVT_FAULT_IABS:    return "FAULT_IABS";
        case EVT_FAULT_IRMS:    return "FAULT_IRMS";
        case EVT_FOC_ENGAGED:   return "FOC_ENGAGED";
        case EVT_FOC_LOST:      return "FOC_LOST";
        case EVT_ZSC_TUNED:     return "ZSC_TUNED";
        case EVT_ZSC_FAILED:    return "ZSC_FAILED";
        case EVT_DTC_CONVERGED: return "DTC_CONVERGED";
        case EVT_DTC_SAT_MIN:   return "DTC_SAT_MIN";
        case EVT_DTC_SAT_MAX:   return "DTC_SAT_MAX";
        default:                return "NONE";
    }
}
static void burst_trigger(uint8_t auto_trig) {
    if (burst.busy || burst.triggered) return;
    burst.trig_idx = burst.head;
    burst.post_left = BURST_POST_TRIG;
    burst.isr_hz = (uint32_t)measured_isr_hz;
    burst.triggered = 1U;
    burst.auto_triggered = auto_trig;
}
static inline float clampf(float v, float min, float max) { return (v < min) ? min : ((v > max) ? max : v); }
static inline uint32_t clamp_u32_from_float(float v, uint32_t min, uint32_t max) {
    if (!isfinite(v)) return min;
    if (v < (float)min) return min;
    if (v > (float)max) return max;
    return (uint32_t)v;
}
static inline float pi_step(PI_t *pi, float err, float dt) {
    float p = pi->kp * err;
    pi->integ += pi->ki * err * dt;
    float out = p + pi->integ;
    if (out > pi->out_max) { out = pi->out_max; pi->integ = out - p; }
    else if (out < pi->out_min) { out = pi->out_min; pi->integ = out - p; }
    return out;
}
static void current_update(CurrentMeas_t *c, float a_adc, float b_adc, float c_adc) {
    if (!c->calibrated) {
        c->off_a += a_adc; c->off_b += b_adc; c->off_c += c_adc; c->calib_cnt++;
        if (c->calib_cnt >= CURR_CALIB_SAMPLES) {
            float k = 1.0f / (float)c->calib_cnt;
            c->off_a *= k; c->off_b *= k; c->off_c *= k; c->calibrated = 1U;
        }
        c->ia = 0.0f; c->ib = 0.0f; c->ic = 0.0f; return;
    }
    c->ia_raw = (a_adc - c->off_a) * c->scale_a;
    c->ib_raw = (b_adc - c->off_b) * c->scale_b;
    c->ic_raw = (c_adc - c->off_c) * c->scale_c;
    c->ia += CURR_IIR_ALPHA * (c->ia_raw - c->ia);
    c->ib += CURR_IIR_ALPHA * (c->ib_raw - c->ib);
    c->ic += CURR_IIR_ALPHA * (c->ic_raw - c->ic);
    c->rms2_lpf += I_RMS_LPF_ALPHA * ((c->ia * c->ia + c->ib * c->ib + c->ic * c->ic) * 0.333333f - c->rms2_lpf);
}
static void pwm_shutdown_fault(void) {
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    Fault_OverCurrent = 1U;
}
static float zsc_autotune_step(ZscAutotune_t *at, float izs, float dt) {
    at->ticks++; at->ticks_since_cross++;
    if (at->ticks > ZSC_ATUNE_MAX_TICKS) {
        at->state = ZSC_ATUNE_FAILED;
        evt_push(EVT_ZSC_FAILED, (float)at->ticks, 0.0f, 0.0f);
        return 0.0f;
    }
    float vzs_corr = (izs >= 0.0f) ? -at->relay_amp : at->relay_amp;
    int cross = ((at->izs_prev >= 0.0f) && (izs < 0.0f)) || ((at->izs_prev < 0.0f) && (izs >= 0.0f));
    if (cross && (at->ticks_since_cross > 5U)) {
        at->half_period_sum += (float)at->ticks_since_cross * dt;
        at->half_periods++; at->ticks_since_cross = 0U;
        float amp = fmaxf(at->peak_pos, -at->peak_neg);
        if (amp < 1e-6f) amp = 1e-6f;
        if (at->half_periods >= ZSC_ATUNE_MIN_PERIODS) {
            float Tu = 2.0f * (at->half_period_sum / (float)at->half_periods);
            float Ku = (4.0f * at->relay_amp) / (3.14159265f * amp);
            at->result_kp = 0.45f * Ku; at->result_ki = 0.54f * Ku / Tu;
            g_zsc_pi.kp = at->result_kp; g_zsc_pi.ki = at->result_ki; g_zsc_pi.integ = 0.0f;
            at->state = ZSC_ATUNE_DONE;
            evt_push(EVT_ZSC_TUNED, at->result_kp, at->result_ki, Tu);
            return 0.0f;
        }
        at->peak_pos = 0.0f; at->peak_neg = 0.0f;
    }
    if (izs > at->peak_pos) at->peak_pos = izs;
    if (izs < at->peak_neg) at->peak_neg = izs;
    at->izs_prev = izs;
    return vzs_corr;
}

/* ----- Адаптивный компенсатор мёртвого времени -----
   Алгоритм: sign-sign LMS, минимизирующий |e_alpha, e_beta|².
   Корреляция e*sign(i) выделяет DC-составляющую, пропорциональную
   недокомпенсации. Узкополосный LPF подавляет шум, затем Kdt
   медленно дрейфует к оптимальному значению.

   Гатинги:
     - hold_ticks после входа в FOC (переходные процессы)
     - скорость не ниже DTC_MIN_SPEED_HZ (наблюдатель валиден)
     - ток не ниже DTC_MIN_CURRENT_A (сигнал достаточен)

   Лимиты Kdt в [DTC_KDT_MIN, DTC_KDT_MAX] — при срабатывании пишется
   событие (с анти-спамом). Если Kdt упирается в лимит — скорее всего
   неверный знак sign_flip; инвертируйте его командой "dtcsign -1". */
static void dtc_adapt_step(DTC_t *dtc, float e_alpha, float e_beta,
                           float i_alpha, float i_beta,
                           float i_rms, float speed_hz, float dt,
                           uint8_t zsc_active) {
    /* Сохраняем последние измерения для телеметрии */
    dtc->i_alpha_last = i_alpha;
    dtc->i_beta_last  = i_beta;

    /* Диагностика: LPF нормы невязки — всегда обновляем, даже когда
       адаптация заглушена. Это позволяет наблюдать качество компенсации. */
    float e_norm = sqrtf(e_alpha * e_alpha + e_beta * e_beta);
    dtc->e_norm_lpf += DTC_ENORM_LPF_ALPHA * (e_norm - dtc->e_norm_lpf);

    /* Hold-период после входа в FOC — ждём успокоения переходных процессов */
    if (dtc->hold_ticks < DTC_STARTUP_HOLD_TICKS) {
        dtc->hold_ticks++;
        return;
    }
    if (!dtc->enabled) return;

    /* Блокировка во время ZSC autotune: relay-feedback в izs создаёт
       большие колебания e_alpha/e_beta через наблюдатель — ложная
       корреляция уводит Kdt от оптимального значения. */
    if (zsc_active) return;

    /* Гатинги по скорости и току: наблюдатель валиден только при
       достаточной ЭДС, а на холостом ходе корреляция мала и шумит */
    if (fabsf(speed_hz) < DTC_MIN_SPEED_HZ) return;
    if (i_rms < DTC_MIN_CURRENT_A) return;

    /* Синхронный детектор: корреляция e*sign(i) выделяет DC-компоненту,
       пропорциональную недокомпенсации. Знак i_alpha, i_beta берём
       мгновенно (внутри такта ШИМ) — этого достаточно для LPF. */
    float i_mag = sqrtf(i_alpha * i_alpha + i_beta * i_beta);
    float i_weight = clampf(i_mag / 0.5f, 0.0f, 1.0f);
    float sa = (i_alpha >= 0.0f) ? 1.0f : -1.0f;
    float sb = (i_beta  >= 0.0f) ? 1.0f : -1.0f;
    float corr = (e_alpha * sa + e_beta * sb) * i_weight;

    /* Узкополосный LPF: тау ~ 250 мс при 10 кГц. Это эквивалентно
       интегрированию по многим периодам электрической частоты. */
    dtc->corr_lpf += DTC_CORR_LPF_ALPHA * (corr - dtc->corr_lpf);

    /* Градиентный шаг: Kdt += sign_flip * mu * corr_lpf * dt.
       sign_flip компенсирует неопределённость знака градиента
       (зависит от того, какая модель стоит в наблюдателе — токовая
       или напряженная). По умолчанию +1 для токовой модели. */
    float mu_eff;
    if (dtc->converged) {
        mu_eff = dtc->mu * 0.3f;
    } else if (dtc->adapt_ticks < 5000U) {
        mu_eff = dtc->mu * 2.0f;
    } else {
        mu_eff = dtc->mu;
    }
    float dKdt = dtc->sign_flip * mu_eff * dtc->corr_lpf * dt;
    float Kdt_new = dtc->Kdt + dKdt;

    /* Защита от NaN/Inf (при сбое наблюдателя) */
    if (!isfinite(Kdt_new)) Kdt_new = DTC_DEFAULT_KDT;

    /* Лимиты с анти-спамом событий */
    if (Kdt_new <= DTC_KDT_MIN) {
        Kdt_new = DTC_KDT_MIN;
        if (dtc->sat_cooldown == 0U && !dtc->sat_reported) {
            evt_push(EVT_DTC_SAT_MIN, dtc->Kdt, dtc->corr_lpf, dtc->sign_flip);
            dtc->sat_reported = 1U;
            dtc->sat_cooldown = DTC_SAT_TICKS_REPORT;
        }
    } else if (Kdt_new >= DTC_KDT_MAX) {
        Kdt_new = DTC_KDT_MAX;
        if (dtc->sat_cooldown == 0U && !dtc->sat_reported) {
            evt_push(EVT_DTC_SAT_MAX, dtc->Kdt, dtc->corr_lpf, dtc->sign_flip);
            dtc->sat_reported = 1U;
            dtc->sat_cooldown = DTC_SAT_TICKS_REPORT;
        }
    } else {
        /* Ушли от лимита — сбрасываем флаг сообщения */
        dtc->sat_reported = 0U;
    }
    if (dtc->sat_cooldown > 0U) dtc->sat_cooldown--;

    dtc->Kdt = Kdt_new;
    dtc->adapt_ticks++;

    /* Детектор сходимости: |corr_lpf| < порога непрерывно в течение
       DTC_CONVERGED_TICKS тактов → пишем событие один раз. */
    if (fabsf(dtc->corr_lpf) < DTC_CONVERGED_THRESH_A) {
        if (dtc->converged_ticks < DTC_CONVERGED_TICKS) {
            dtc->converged_ticks++;
            if (dtc->converged_ticks == DTC_CONVERGED_TICKS && !dtc->converged) {
                dtc->converged = 1U;
                evt_push(EVT_DTC_CONVERGED, dtc->Kdt, dtc->corr_lpf, dtc->e_norm_lpf);
                /* Сообщаем в основной цикл о необходимости печати */
                print_dtc_converged = 1U;
                print_dtc_kdt_val = dtc->Kdt;
                print_dtc_corr_val = dtc->corr_lpf;
            }
        }
    } else {
        dtc->converged_ticks = 0U;
        dtc->converged = 0U;
    }
}

/* Применение компенсации мёртвого времени к duty-циклу.
   Для каждой фазы: inv1 += Kdt*sign(i), inv2 -= Kdt*sign(i).
   Жёсткая противофаза OEW даёт двойной эффект на обмотке. */
static inline int32_t dtc_phase_correction(const DTC_t *dtc, float i_phase) {
    if (!dtc->compensate) return 0;
    float k = dtc->Kdt;
    return (i_phase >= 0.0f) ? (int32_t)k : -(int32_t)k;
}

// Исправлена ошибка 1 (CORDIC underflow) и ошибка 7 (static)
static float CORDIC_Calc(float angle_rad, float *last_result_ptr) {
    angle_rad = fmodf(angle_rad + M_PI, TWO_PI);
    if (angle_rad < 0) angle_rad += TWO_PI;
    angle_rad -= M_PI;
    int32_t angle_q31 = (int32_t)(angle_rad * (2147483648.0f / M_PI));
    CORDIC->WDATA = angle_q31;
    uint32_t timeout = CORDIC_TIMEOUT_CYCLES;
    while (((CORDIC->CSR & CORDIC_CSR_RRDY) == 0U) && (timeout-- > 0U)) { }
    if ((CORDIC->CSR & CORDIC_CSR_RRDY) == 0U) return *last_result_ptr;
    int32_t result_q31 = CORDIC->RDATA;
    *last_result_ptr = (float)result_q31 / 2147483648.0f;
    return *last_result_ptr;
}

static float CORDIC_Cos(float angle_rad) {
    static float last_cos = 1.0f;
    return CORDIC_Calc(angle_rad, &last_cos);
}

static float CORDIC_Sin(float angle_rad) {
    static float last_sin = 0.0f;
    return CORDIC_Calc(angle_rad - 1.5707963f, &last_sin);
}

static void Clarke_Transform(float ia, float ib, float *ialpha, float *ibeta) {
    *ialpha = ia;
    *ibeta = (1.0f / 1.7320508f) * ia + (2.0f / 1.7320508f) * ib;
}

// Обратный Кларк (амплитудно-инвариантная форма): va=valpha, vb=-0.5*valpha+(sqrt3/2)*vbeta
// Исправлена критическая ошибка: ранее здесь была формула прямого Кларка (copy-paste).
static void Inv_Clarke_Transform(float valpha, float vbeta, float *va, float *vb, float *vc) {
    *va =  valpha;
    *vb = -0.5f * valpha + 0.8660254f * vbeta;   // sqrt(3)/2
    *vc = -0.5f * valpha - 0.8660254f * vbeta;
}

/* SVPWM zero-sequence injection: common-mode offset = -(max+min)/2.
   Расширяет линейный диапазон модуляции с 0.866 до 1.0 (≈+15% использования шины).
   Добавляется одинаково к обеим тройкам инверторов (common-mode),
   не конфликтует с vzs_corr (differential mode ZSC). */
static void svpwm_inject(float *va, float *vb, float *vc) {
    float vmax = fmaxf(*va, fmaxf(*vb, *vc));
    float vmin = fminf(*va, fminf(*vb, *vc));
    float v0 = -0.5f * (vmax + vmin);
    *va += v0; *vb += v0; *vc += v0;
}
static void Park_Transform(float ialpha, float ibeta, float sin_t, float cos_t, float *id, float *iq) {
    *id =  ialpha * cos_t + ibeta * sin_t;
    *iq = -ialpha * sin_t + ibeta * cos_t;
}
static void Inv_Park_Transform(float vd, float vq, float sin_t, float cos_t, float *valpha, float *vbeta) {
    *valpha = vd * cos_t - vq * sin_t;
    *vbeta  = vd * sin_t + vq * cos_t;
}

// Исправлена ошибка 4 (согласованность psi при расчете omega_e)
static void RotorFluxObserver(FOC_State_t *foc, MotorParams_t *m, float ialpha, float ibeta, float dt) {
    float Tr = m->Lr / m->Rr;
    float Lm_Tr = m->Lm / Tr;
    float inv_Tr = 1.0f / Tr;

    float psi_alpha_old = foc->psi_alpha;
    float psi_beta_old = foc->psi_beta;

    float d_psi_alpha = (Lm_Tr * ialpha) - (inv_Tr * psi_alpha_old) + (foc->omega_r * psi_beta_old);
    float d_psi_beta  = (Lm_Tr * ibeta)  - (inv_Tr * psi_beta_old)  - (foc->omega_r * psi_alpha_old);

    float psi_mag_sq = psi_alpha_old * psi_alpha_old + psi_beta_old * psi_beta_old;
    if (psi_mag_sq < 1e-6f) psi_mag_sq = 1e-6f;

    // omega_e считается по старым psi и их производным (согласованный расчет)
    float omega_e = (psi_alpha_old * d_psi_beta - psi_beta_old * d_psi_alpha) / psi_mag_sq;

    // Обновляем psi только после использования старых значений
    foc->psi_alpha = psi_alpha_old + d_psi_alpha * dt;
    foc->psi_beta  = psi_beta_old  + d_psi_beta  * dt;

    float omega_slip = (Lm_Tr * (psi_alpha_old * ibeta - psi_beta_old * ialpha)) / psi_mag_sq;
    foc->omega_r = omega_e - omega_slip;

    /* Состояния для телеметрии: скольжение, модуль потока, оценка момента */
    foc->omega_slip = omega_slip;
    foc->psi_mag = sqrtf(psi_mag_sq);
    foc->te_est = 1.5f * m->p_pairs * (m->Lm / m->Lr)
                  * (psi_alpha_old * ibeta - psi_beta_old * ialpha);

    foc->theta = atan2f(foc->psi_beta, foc->psi_alpha);
    if (foc->theta < 0.0f) foc->theta += TWO_PI;

    /* Невязки наблюдателя: предсказанный ток по модели потока
       i_pred = psi / Lm,  ошибка = измеренный - предсказанный */
    float ia_pred = foc->psi_alpha / m->Lm;
    float ib_pred = foc->psi_beta  / m->Lm;
    foc->e_alpha = ialpha - ia_pred;
    foc->e_beta  = ibeta  - ib_pred;
}
void UART_Send_Stream(void) {
    static char buf[896];
    /* Снапшот под запретом прерываний — все поля кадра относятся к одному такту ISR */
    uint32_t primask = __get_PRIMASK(); __disable_irq();
    CurrentMeas_t c1 = g_curr;
    CurrentMeas_t c2 = g_curr2;
    FOC_State_t foc = g_foc;
    float s_izs = izs_val, s_spd = current_speed, s_tgs = Target_Speed;
    float s_pwr = current_power, s_tgi = Target_Current_A, s_zint = g_zsc_pi.integ;
    float s_hz = measured_isr_hz;
    uint32_t s_ts = g_isr_ts, s_txd = tx_dropped;
    uint32_t s_isu = isr_us_avg, s_isum = isr_us_max;
    uint8_t s_flt = Fault_OverCurrent, s_run = Run_Enabled;
    uint8_t s_foc = (uint8_t)foc_startup_state;
    uint8_t s_sat = foc.sat_flags;
    float s_pid_i = g_pi_id.integ, s_piq_i = g_pi_iq.integ, s_pspd_i = g_pi_speed.integ;
    float s_pe = foc.p_elec, s_qe = foc.q_elec;
    float s_ea = foc.e_alpha, s_eb = foc.e_beta;
    /* Снимаем состояние DTC под запретом прерываний — консистентно с остальным кадром */
    float s_kdt = g_dtc.Kdt, s_corr = g_dtc.corr_lpf, s_enorm = g_dtc.e_norm_lpf;
    uint8_t s_dtc_en = g_dtc.enabled, s_dtc_cmp = g_dtc.compensate, s_dtc_conv = g_dtc.converged;
    uint8_t s_dtc_hold = (g_dtc.hold_ticks < DTC_STARTUP_HOLD_TICKS) ? 1U : 0U;
    __set_PRIMASK(primask);

    /* Float printf доступен (-u _printf_float в линкере).
       Используем %.Nf напрямую — это исправляет баг fxp, который терял
       знак для значений в диапазоне (-1, 0) (например ib = -0.758 → "0.758"). */
    int len = snprintf(buf, sizeof(buf),
        "{\"ts\":%lu,"
        "\"Ia1\":%.3f,\"Ib1\":%.3f,\"Ic1\":%.3f,\"Ia2\":%.3f,\"Ib2\":%.3f,\"Ic2\":%.3f,"
        "\"Izs\":%.3f,\"Irms\":%.3f,\"f\":%.2f,\"tgF\":%.2f,\"P\":%.3f,\"tgI\":%.3f,"
        "\"zint\":%.3f,\"flt\":%d,\"run\":%d,\"foc\":%d,\"cal\":%d,\"sat\":%u,"
        "\"Id\":%.3f,\"Iq\":%.3f,\"IdT\":%.3f,\"IqT\":%.3f,"
        "\"Vd\":%.3f,\"Vq\":%.3f,\"VdR\":%.3f,\"VqR\":%.3f,"
        "\"Wr\":%.2f,\"Th\":%.3f,"
        "\"slip\":%.2f,\"psi\":%.4f,\"Te\":%.3f,"
        "\"Iid\":%.3f,\"Iiq\":%.3f,\"Isp\":%.3f,"
        "\"Pe\":%.3f,\"Qe\":%.3f,\"Ea\":%.3f,\"Eb\":%.3f,"
        "\"Kdt\":%.1f,\"Dcorr\":%.4f,\"Den\":%.3f,\"Dflags\":%d%d%d%d,"
        "\"rA1\":%u,\"rB1\":%u,\"rC1\":%u,\"rA2\":%u,\"rB2\":%u,\"rC2\":%u,"
        "\"ISR_Hz\":%.1f,\"isu\":%lu,\"isum\":%lu,\"txd\":%lu,"
        "\"flg\":\"%lu/%lu/%d/%d\"}\r\n",
        (unsigned long)s_ts,
        c1.ia, c1.ib, c1.ic, c2.ia, c2.ib, c2.ic,
        s_izs, sqrtf(fmaxf(c1.rms2_lpf, 0.0f)), s_spd, s_tgs, s_pwr, s_tgi,
        s_zint, (int)s_flt, (int)s_run, (int)s_foc, (int)c1.calibrated, (unsigned)s_sat,
        foc.id, foc.iq, foc.id_target, foc.iq_target,
        foc.vd, foc.vq, foc.vd_raw, foc.vq_raw,
        foc.omega_r, foc.theta,
        foc.omega_slip / TWO_PI, foc.psi_mag, foc.te_est,
        s_pid_i, s_piq_i, s_pspd_i,
        s_pe, s_qe, s_ea, s_eb,
        s_kdt, s_corr, s_enorm,
        (int)s_dtc_en, (int)s_dtc_cmp, (int)s_dtc_conv, (int)s_dtc_hold,
        (unsigned)last_raw_a1, (unsigned)last_raw_b1, (unsigned)last_raw_c1,
        (unsigned)last_raw_a2, (unsigned)last_raw_b2, (unsigned)last_raw_c2,
        s_hz, (unsigned long)s_isu, (unsigned long)s_isum, (unsigned long)s_txd,
        (unsigned long)g_flash_log.record_count, (unsigned long)(g_flash_log.record_count / FLASH_SNAPS_PER_PAGE),
        (int)g_flash_log.enabled, (int)g_flash_log.full);

    if (len <= 0 || len >= (int)sizeof(buf)) {
        len = snprintf(buf, sizeof(buf), "{\"error\":\"buf_len\"}\r\n");
    }
    uart2_tx_enqueue((uint8_t*)buf, (uint16_t)len);
}

static void burst_dump_step(void) {
    static uint16_t idx = 0U;
    static uint8_t hdr_sent = 0U;
    if (!burst.busy) { idx = 0U; hdr_sent = 0U; return; }
    /* TX-буфер > 75% занят — откладываем burst, даём приоритет JSON-стриму */
    if (uart2_tx_free() < (UART_TX_RING_SIZE / 4U)) return;
    char line[384];
    uint16_t n = burst.filled;
    uint16_t oldest = (uint16_t)((burst.head + BURST_CAP_SIZE - n) % BURST_CAP_SIZE);
    if (!hdr_sent) {
        uint16_t trig_rel = (uint16_t)((burst.trig_idx + BURST_CAP_SIZE - oldest) % BURST_CAP_SIZE);
        int len = snprintf(line, sizeof(line), "$BURST,%u,%lu,%u,%u,%u\r\n",
            (unsigned)n, (unsigned long)burst.isr_hz, (unsigned)trig_rel,
            (unsigned)burst.auto_triggered, (unsigned)BURST_DECIM);
        if (len <= 0 || len >= (int)sizeof(line)) return; /* усечение — не отправляем */
        if (uart2_tx_free() < (uint16_t)len) return; /* TX занят — повтор на следующей итерации */
        uart2_tx_enqueue((uint8_t*)line, (uint16_t)len);
        hdr_sent = 1U;
        /* Строка параметров для идентификации лога */
        int plen = snprintf(line, sizeof(line),
            "$PARAMS,Rs=%.4f,Rr=%.4f,Ls=%.5f,Lr=%.5f,Lm=%.5f,pp=%.1f,"
            "KP_id=%.3f,KI_id=%.1f,KP_iq=%.3f,KI_iq=%.1f,KP_spd=%.3f,KI_spd=%.1f,"
            "Kdt=%.2f,mu=%.2f,sign_flip=%.0f,en=%u,cmp=%u\r\n",
            g_motor.Rs, g_motor.Rr, g_motor.Ls, g_motor.Lr, g_motor.Lm, g_motor.p_pairs,
            g_pi_id.kp, g_pi_id.ki, g_pi_iq.kp, g_pi_iq.ki, g_pi_speed.kp, g_pi_speed.ki,
            g_dtc.Kdt, g_dtc.mu, g_dtc.sign_flip, (unsigned)g_dtc.enabled, (unsigned)g_dtc.compensate);
        if (plen <= 0 || plen >= (int)sizeof(line)) return;
        if (uart2_tx_free() < (uint16_t)plen) return;
        uart2_tx_enqueue((uint8_t*)line, (uint16_t)plen);
    }
    uint16_t end = idx + BURST_DUMP_CHUNK;
    for (; (idx < end) && (idx < n); idx++) {
        const BurstSample_t *s = &burst_buf[(oldest + idx) % BURST_CAP_SIZE];
        int len = snprintf(line, sizeof(line),
            "%u,%lu,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%u,%u,%u,%u,%u\r\n",
            (unsigned)idx, (unsigned long)s->ts,
            s->ia1, s->ib1, s->ic1, s->ia2, s->ib2, s->ic2, s->izs, s->ang,
            s->id, s->iq, s->id_t, s->iq_t,
            s->vd, s->vq, s->vd_raw, s->vq_raw,
            s->wr, s->slip, s->psi, s->te,
            s->pi_id_i, s->pi_iq_i, s->pi_spd_i, (unsigned)s->sat_flags,
            s->e_alpha, s->e_beta, s->tgt_spd, s->theta_jit,
            s->diq_dt, s->dwr_dt, s->p_elec, s->q_elec,
            s->kdt, s->dtc_corr,
            s->d1a, s->d1b, s->d1c, s->d2a, s->d2b, s->d2c);
        if (len <= 0 || len >= (int)sizeof(line)) continue; /* усечение — пропускаем сэмпл */
        if (uart2_tx_free() < (uint16_t)len) return;
        uart2_tx_enqueue((uint8_t*)line, (uint16_t)len);
    }
    if (idx >= n) {
        if (uart2_tx_free() < 6U) return;
        uart2_tx_enqueue((uint8_t*)"$END\r\n", 6U);
        uint32_t primask = __get_PRIMASK(); __disable_irq();
        burst.busy = 0U; burst.filled = 0U; burst.head = 0U; burst.trig_idx = 0U;
        burst.auto_triggered = 0U;
        __set_PRIMASK(primask);
        idx = 0U; hdr_sent = 0U;
    }
}

#define TEST_L_SAMPLES 300U
static volatile uint8_t test_l_active = 0U;
static volatile uint16_t test_l_idx = 0U;
static volatile uint16_t test_r_ticks = 0U;  /* testR: ДЛИТЕЛЬНЫЙ DC для сверки с мультиметром */
static volatile int16_t test_l_ia[TEST_L_SAMPLES];
static volatile int16_t test_l_ib[TEST_L_SAMPLES];
static volatile int16_t test_l_ic[TEST_L_SAMPLES];
static volatile int16_t test_l_ia2[TEST_L_SAMPLES];  /* ADC2 cross-check */
static volatile int16_t test_l_ib2[TEST_L_SAMPLES];  /* ADC2 cross-check */

// Исправлена ошибка 3: Добавлены критические секции для Race condition

/* =====================================================================
 *  Flash Black Box — helpers (вызываются только из main loop, не из ISR)
 * ===================================================================== */

/* Сканирует flash, находит первую пустую (0xFF) 32-байтную ячейку.
   Пустая запись = ts == 0xFFFFFFFF. Используется для возобновления
   после сброса питания. */
static void flash_log_scan_resume(void) {
    uint32_t addr = FLASH_LOG_BASE;
    uint32_t end = FLASH_LOG_BASE + FLASH_LOG_SIZE;
    while (addr + FLASH_SNAP_SIZE <= end) {
        uint32_t *p = (uint32_t*)addr;
        if (p[0] == 0xFFFFFFFFU) break;
        addr += FLASH_SNAP_SIZE;
    }
    g_flash_log.write_addr = addr;
    g_flash_log.record_count = (addr - FLASH_LOG_BASE) / FLASH_SNAP_SIZE;
    if (addr + FLASH_SNAP_SIZE > end) g_flash_log.full = 1U;
}

/* Стирает все страницы bank 2. ~5 секунд на 128 страниц. */
static void flash_log_erase_all(void) {
    HAL_FLASH_Unlock();
    FLASH_EraseInitTypeDef ei;
    ei.TypeErase = FLASH_TYPEERASE_PAGES;
    ei.Banks = FLASH_BANK_2;
    ei.Page = 0;
    ei.NbPages = FLASH_LOG_PAGES;
    uint32_t page_err = 0U;
    HAL_StatusTypeDef st = HAL_FLASHEx_Erase(&ei, &page_err);
    HAL_FLASH_Lock();
    if (st != HAL_OK) {
        printf("FLASH ERASE ERROR: page_err=%lu\r\n", (unsigned long)page_err);
        return;
    }
    g_flash_log.write_addr = FLASH_LOG_BASE;
    g_flash_log.record_count = 0U;
    g_flash_log.full = 0U;
    printf("FLASH CLEARED: %u pages erased\r\n", (unsigned)FLASH_LOG_PAGES);
}

/* Записывает одну 32-байтную запись (4 × 64-bit word) во flash.
   Возвращает 1 = успех, 0 = flash заполнен или ошибка. */
static uint8_t flash_log_write_snap(const FlashSnap_t *snap) {
    if (g_flash_log.full) return 0U;
    if (g_flash_log.write_addr + FLASH_SNAP_SIZE > FLASH_LOG_BASE + FLASH_LOG_SIZE) {
        g_flash_log.full = 1U;
        return 0U;
    }
    /* Стираем страницу, если пишем в её начало */
    if ((g_flash_log.write_addr & (FLASH_PAGE_SIZE - 1U)) == 0U) {
        HAL_FLASH_Unlock();
        FLASH_EraseInitTypeDef ei;
        ei.TypeErase = FLASH_TYPEERASE_PAGES;
        ei.Banks = FLASH_BANK_2;
        ei.Page = (g_flash_log.write_addr - FLASH_LOG_BASE) / FLASH_PAGE_SIZE;
        ei.NbPages = 1;
        uint32_t page_err = 0U;
        HAL_StatusTypeDef st = HAL_FLASHEx_Erase(&ei, &page_err);
        HAL_FLASH_Lock();
        if (st != HAL_OK) return 0U;
    }
    /* Пишем 4 × 64-bit word (32 байта) */
    const uint64_t *src = (const uint64_t*)snap;
    HAL_FLASH_Unlock();
    for (int i = 0; i < 4; i++) {
        HAL_StatusTypeDef st = HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD,
            g_flash_log.write_addr + i * 8U, src[i]);
        if (st != HAL_OK) {
            HAL_FLASH_Lock();
            return 0U;
        }
    }
    HAL_FLASH_Lock();
    g_flash_log.write_addr += FLASH_SNAP_SIZE;
    g_flash_log.record_count++;
    return 1U;
}

/* Дреин SRAM snapshot buffer → flash. Вызывается из main loop. */
static void flash_log_drain(void) {
    if (!g_flash_log.enabled || g_flash_log.full) return;
    while (snap_ctrl.tail != snap_ctrl.head) {
        FlashSnap_t snap;
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        snap = snap_buf[snap_ctrl.tail];
        snap_ctrl.tail = (uint16_t)((snap_ctrl.tail + 1U) % SNAP_SRAM_SIZE);
        __set_PRIMASK(primask);
        if (!flash_log_write_snap(&snap)) {
            if (g_flash_log.full) printf("FLASH LOG FULL: %lu records\r\n",
                (unsigned long)g_flash_log.record_count);
            break;
        }
    }
}

/* Выгрузка всех записей из flash по UART в CSV-формате. */
static void flash_log_dump(void) {
    uint32_t count = g_flash_log.record_count;
    char line[128];
    int len;

    /* Заголовок: ждём места в TX-буфере перед отправкой */
    len = snprintf(line, sizeof(line), "$FLASHLOG,%lu\r\n", (unsigned long)count);
    while (uart2_tx_free() < (uint16_t)len) { /* spin */ }
    uart2_tx_enqueue((uint8_t*)line, (uint16_t)len);

    len = snprintf(line, sizeof(line), "ts,id,iq,vd,vq,wr,slip,te,psi,sat,foc,p,q,ea,eb,kdt,fault\r\n");
    while (uart2_tx_free() < (uint16_t)len) { /* spin */ }
    uart2_tx_enqueue((uint8_t*)line, (uint16_t)len);

    for (uint32_t i = 0U; i < count; i++) {
        const FlashSnap_t *s = (const FlashSnap_t*)(FLASH_LOG_BASE + i * FLASH_SNAP_SIZE);
        if (s->ts == 0xFFFFFFFFU) break;  /* пустая запись */
        len = snprintf(line, sizeof(line),
            "%lu,%d,%d,%d,%d,%d,%d,%d,%d,%u,%u,%d,%d,%d,%d,%u,%u\r\n",
            (unsigned long)s->ts,
            (int)s->id, (int)s->iq, (int)s->vd, (int)s->vq,
            (int)s->wr, (int)s->slip, (int)s->te, (int)s->psi,
            (unsigned)(s->sat_flags & 0x7FU), (unsigned)s->foc_state,
            (int)s->p_elec, (int)s->q_elec,
            (int)s->e_alpha, (int)s->e_beta,
            (unsigned)s->kdt, (unsigned)((s->sat_flags & SAT_FAULT_BIT) ? 1U : 0U));
        if (len <= 0 || len >= (int)sizeof(line)) continue;
        while (uart2_tx_free() < (uint16_t)len) { /* spin */ }
        uart2_tx_enqueue((uint8_t*)line, (uint16_t)len);
    }
    while (uart2_tx_free() < 6U) { /* spin */ }
    uart2_tx_enqueue((uint8_t*)"$END\r\n", 6U);
    printf("FLASH DUMP: %lu records\r\n", (unsigned long)count);
}

/* Информация о состоянии flash-лога. */
static void flash_log_info(void) {
    uint32_t used_pages = g_flash_log.record_count / FLASH_SNAPS_PER_PAGE;
    printf("FLASH LOG: %lu records, %lu/%u pages, addr=0x%08lX, %s%s\r\n",
        (unsigned long)g_flash_log.record_count,
        (unsigned long)used_pages, (unsigned)FLASH_LOG_PAGES,
        (unsigned long)g_flash_log.write_addr,
        g_flash_log.enabled ? "ENABLED" : "DISABLED",
        g_flash_log.full ? " FULL" : "");
}

void process_uart_command(const char *cmd) {
    if ((cmd == NULL) || (cmd[0] == '\0')) return;
    uint32_t primask;
    if (strcmp(cmd, "start") == 0) {
        autosave_speed = Target_Speed;
        autosave_power = Target_Power;
        autosave_current = Target_Current_A;
        primask = __get_PRIMASK();
        __disable_irq();
        Target_Power = 0.0f; current_speed = 0.0f;
        Fault_OverCurrent = 0U; TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
        g_curr.calibrated = 0U; g_curr.calib_cnt = 0U; g_curr.off_a = 0.0f; g_curr.off_b = 0.0f; g_curr.off_c = 0.0f;
        g_curr2.calibrated = 0U; g_curr2.calib_cnt = 0U; g_curr2.off_a = 0.0f; g_curr2.off_b = 0.0f; g_curr2.off_c = 0.0f;
        foc_startup_state = FOC_STARTUP_IDLE;
        Target_Speed = 20.0f;
        Target_Power = 0.5f;
        Target_Current_A = 2.0f;
        /* DTC: полный сброс при новом пуске, чтобы адаптация начиналась с безопасного Kdt */
        g_dtc.Kdt = DTC_DEFAULT_KDT;
        g_dtc.corr_lpf = 0.0f;
        g_dtc.e_norm_lpf = 0.0f;
        g_dtc.hold_ticks = 0U;
        g_dtc.converged = 0U;
        g_dtc.converged_ticks = 0U;
        g_dtc.adapt_ticks = 0U;
        g_dtc.sat_reported = 0U;
        g_dtc.sat_cooldown = 0U;
        __set_PRIMASK(primask);
        auto_start_state = AUTO_START_CALIB;
        auto_start_t0 = HAL_GetTick();
        Run_Enabled = 1U;
        printf("AUTO START: Calibrating...\r\n");
    }
    else if (strcmp(cmd, "stop") == 0) {
        primask = __get_PRIMASK();
        __disable_irq();
        Run_Enabled = 0U;
        Target_Power = 0.0f; current_speed = 0.0f; current_power = 0.0f;
        g_i_pi.integ = 0.0f; g_zsc_pi.integ = 0.0f;
        g_pi_id.integ = 0.0f; g_pi_iq.integ = 0.0f; g_pi_speed.integ = 0.0f;
        g_pi_fw.integ = 0.0f; // Сброс FW
        /* DTC: сохраняем Kdt (значение осталось в силовом каскаде), но
           сбрасываем диагностические счётчики и hold — при следующем пуске
           адаптация стартует заново. */
        g_dtc.corr_lpf = 0.0f;
        g_dtc.e_norm_lpf = 0.0f;
        g_dtc.hold_ticks = 0U;
        g_dtc.converged = 0U;
        g_dtc.converged_ticks = 0U;
        g_dtc.sat_reported = 0U;
        g_dtc.sat_cooldown = 0U;
        foc_startup_state = FOC_STARTUP_IDLE;
        auto_start_state = AUTO_START_IDLE;
        __set_PRIMASK(primask);
        printf("STOPPED (DTC Kdt=%.2f held)\r\n", g_dtc.Kdt);
    }
    else if (strcmp(cmd, "stream on") == 0) { Stream_Enabled = 1; printf("STREAM ON\r\n"); }
    else if (strcmp(cmd, "stream off") == 0) { Stream_Enabled = 0; printf("STREAM OFF\r\n"); }
    else if (strcmp(cmd, "zscatune") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_zsc_atune = (ZscAutotune_t){.state = ZSC_ATUNE_RUNNING, .relay_amp = ZSC_ATUNE_RELAY_AMP};
        g_zsc_pi.integ = 0.0f;
        __set_PRIMASK(primask);
        printf("ZSC AUTOTUNE STARTED\r\n");
    } else if (cmd[0] == 's') { Target_Speed = clampf(strtof(&cmd[1], NULL), -MAX_SPEED_ELEC_HZ, MAX_SPEED_ELEC_HZ); }
    else if (cmd[0] == 'p') {
        primask = __get_PRIMASK(); __disable_irq();
        Target_Power = clampf(strtof(&cmd[1], NULL), 0.0f, MAX_POWER);
        __set_PRIMASK(primask);
    }
    else if (cmd[0] == 'i') { Target_Current_A = clampf(strtof(&cmd[1], NULL), 0.0f, I_RMS_MAX_A * 0.95f); }
    else if (cmd[0] == 'K' && cmd[1] == 'P') { g_i_pi.kp = strtof(&cmd[2], NULL); printf("KP OK\r\n"); }
    else if (cmd[0] == 'K' && cmd[1] == 'I') { g_i_pi.ki = strtof(&cmd[2], NULL); printf("KI OK\r\n"); }
    else if (cmd[0] == 'I' && cmd[1] == 'M') { I_PHASE_ABS_MAX_A = strtof(&cmd[2], NULL); printf("IMAX OK\r\n"); }
    else if (cmd[0] == 'I' && cmd[1] == 'R') { I_RMS_MAX_A = strtof(&cmd[2], NULL); printf("IRMS OK\r\n"); }
    else if (cmd[0] == 'D' && cmd[1] == 'T') { I_DIDT_MAX_PER_TICK = strtof(&cmd[2], NULL); printf("DT OK\r\n"); }
    else if (cmd[0] == 'M' && cmd[1] == 'P') {
        float rs, rr, ls, lr, lm;
        if (sscanf(&cmd[2], "%f,%f,%f,%f,%f", &rs, &rr, &ls, &lr, &lm) == 5) {
            g_motor.Rs = rs; g_motor.Rr = rr; g_motor.Ls = ls; g_motor.Lr = lr; g_motor.Lm = lm;
            printf("MOTOR PARAMS UPDATED: Rs=%.3f Rr=%.3f Ls=%.4f Lr=%.4f Lm=%.4f\r\n", rs, rr, ls, lr, lm);
        } else { printf("MP FORMAT ERROR\r\n"); }
    }
    else if (strcmp(cmd, "faultclr") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        Fault_OverCurrent = 0U; TIM1->BDTR |= TIM_BDTR_MOE; TIM8->BDTR |= TIM_BDTR_MOE;
        __set_PRIMASK(primask);
        printf("FAULT CLEARED\r\n");
    } else if (strcmp(cmd, "capture") == 0) {
        if (burst.busy) { printf("BUSY\r\n"); }
        else {
            primask = __get_PRIMASK(); __disable_irq();
            burst_trigger(0U);  /* manual capture */
            __set_PRIMASK(primask);
            printf("CAPTURE TRIGGERED\r\n");
        }
    } else if (strcmp(cmd, "events") == 0) {
        LogEvent_t snap[EVT_LOG_SIZE];
        primask = __get_PRIMASK(); __disable_irq();
        uint8_t n = evt_count, h = evt_head;
        memcpy(snap, evt_log, sizeof(snap));
        __set_PRIMASK(primask);
        printf("$EVENTS,%u\r\n", (unsigned)n);
        for (uint8_t k = 0U; k < n; k++) {
            uint8_t j = (uint8_t)((h + EVT_LOG_SIZE - n + k) % EVT_LOG_SIZE);
            printf("%lu,%s,%.4f,%.4f,%.4f\r\n", (unsigned long)snap[j].ts,
                   evt_name(snap[j].code), snap[j].v0, snap[j].v1, snap[j].v2);
        }
        printf("$END\r\n");
    } else if (strcmp(cmd, "testL") == 0) {
        if (Fault_OverCurrent == 1U) {
            printf("Clear fault first\r\n");
        } else if (!g_curr.calibrated) {
            printf("Calibrate first (calib or start)\r\n");
        } else if (test_l_active == 0U) {
            test_l_active = 1U;
            test_l_idx = 0U;
            printf("L_TEST_START\r\n");
        }
    } else if (strcmp(cmd, "testR") == 0) {
        if (Fault_OverCurrent == 1U) {
            printf("Clear fault first\r\n");
        } else if (!g_curr.calibrated) {
            printf("Calibrate first (calib or start)\r\n");
        } else if (Run_Enabled) {
            printf("Stop motor first\r\n");
        } else {
            test_r_ticks = 30000U;  /* 3 с @ 10 кГц */
            printf("R_TEST_START: 3s DC on phase A/B, watch ia in stream + multimeter\r\n");
        }
    } else if (strcmp(cmd, "calib") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_curr.calibrated = 0U; g_curr.calib_cnt = 0U;
        g_curr.off_a = 0.0f; g_curr.off_b = 0.0f; g_curr.off_c = 0.0f;
        g_curr2.calibrated = 0U; g_curr2.calib_cnt = 0U;
        g_curr2.off_a = 0.0f; g_curr2.off_b = 0.0f; g_curr2.off_c = 0.0f;
        __set_PRIMASK(primask);
        printf("CALIB RESET\r\n");

    /* ===== Управление адаптивной компенсацией мёртвого времени ===== */
    } else if (strcmp(cmd, "dtcinfo") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        float k = g_dtc.Kdt, c = g_dtc.corr_lpf, e = g_dtc.e_norm_lpf;
        uint32_t at = g_dtc.adapt_ticks, ct = g_dtc.converged_ticks;
        uint8_t en = g_dtc.enabled, cmp = g_dtc.compensate, conv = g_dtc.converged;
        uint8_t hold = (g_dtc.hold_ticks < DTC_STARTUP_HOLD_TICKS) ? 1U : 0U;
        __set_PRIMASK(primask);
        printf("$DTC,Kdt=%.2f,mu=%.2f,sign=%.0f,corr=%.5f,enorm=%.4f,"
               "en=%u,cmp=%u,conv=%u,hold=%u,adapt_ticks=%lu,conv_ticks=%lu\r\n",
               k, g_dtc.mu, g_dtc.sign_flip, c, e,
               (unsigned)en, (unsigned)cmp, (unsigned)conv, (unsigned)hold,
               (unsigned long)at, (unsigned long)ct);
    } else if (strcmp(cmd, "dtc on") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.enabled = 1U;
        __set_PRIMASK(primask);
        printf("DTC ADAPT ON\r\n");
    } else if (strcmp(cmd, "dtc off") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.enabled = 0U;
        __set_PRIMASK(primask);
        printf("DTC ADAPT OFF (Kdt=%.2f frozen)\r\n", g_dtc.Kdt);
    } else if (strcmp(cmd, "dtccomp on") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.compensate = 1U;
        __set_PRIMASK(primask);
        printf("DTC COMPENSATE ON (Kdt=%.2f)\r\n", g_dtc.Kdt);
    } else if (strcmp(cmd, "dtccomp off") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.compensate = 0U;
        __set_PRIMASK(primask);
        printf("DTC COMPENSATE OFF (Kdt=%.2f idle)\r\n", g_dtc.Kdt);
    } else if (cmd[0] == 'D' && cmd[1] == 'K') {
        /* DK<val> — ручная установка Kdt */
        float v = strtof(&cmd[2], NULL);
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.Kdt = clampf(v, DTC_KDT_MIN, DTC_KDT_MAX);
        g_dtc.corr_lpf = 0.0f;
        g_dtc.converged = 0U;
        g_dtc.converged_ticks = 0U;
        __set_PRIMASK(primask);
        printf("DTC Kdt=%.2f\r\n", g_dtc.Kdt);
    } else if (cmd[0] == 'D' && cmd[1] == 'M') {
        /* DM<val> — установка скорости адаптации mu */
        float v = strtof(&cmd[2], NULL);
        if (v >= 0.0f && v <= 1000.0f) {
            primask = __get_PRIMASK(); __disable_irq();
            g_dtc.mu = v;
            __set_PRIMASK(primask);
            printf("DTC mu=%.2f\r\n", g_dtc.mu);
        } else {
            printf("DM RANGE 0..1000\r\n");
        }
    } else if (cmd[0] == 'D' && cmd[1] == 'S') {
        /* DS<+1|-1> — инверсия знака градиента */
        float v = strtof(&cmd[2], NULL);
        if (v >= 0.0f) g_dtc.sign_flip = 1.0f;
        else           g_dtc.sign_flip = -1.0f;
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.corr_lpf = 0.0f;
        g_dtc.converged = 0U;
        g_dtc.converged_ticks = 0U;
        __set_PRIMASK(primask);
        printf("DTC sign_flip=%.0f\r\n", g_dtc.sign_flip);
    } else if (strcmp(cmd, "dtcreset") == 0) {
        primask = __get_PRIMASK(); __disable_irq();
        g_dtc.Kdt = DTC_DEFAULT_KDT;
        g_dtc.corr_lpf = 0.0f;
        g_dtc.e_norm_lpf = 0.0f;
        g_dtc.hold_ticks = 0U;
        g_dtc.converged = 0U;
        g_dtc.converged_ticks = 0U;
        g_dtc.adapt_ticks = 0U;
        g_dtc.sat_reported = 0U;
        g_dtc.sat_cooldown = 0U;
        __set_PRIMASK(primask);
        printf("DTC RESET (Kdt=%.2f)\r\n", g_dtc.Kdt);

    /* ===== Flash Black Box ===== */
    } else if (strcmp(cmd, "dumpflash") == 0) {
        flash_log_dump();
    } else if (strcmp(cmd, "clearflash") == 0) {
        flash_log_erase_all();
    } else if (strcmp(cmd, "flashinfo") == 0) {
        flash_log_info();
    } else if (strcmp(cmd, "flashlog on") == 0) {
        g_flash_log.enabled = 1U; printf("FLASH LOG ON\r\n");
    } else if (strcmp(cmd, "flashlog off") == 0) {
        g_flash_log.enabled = 0U; printf("FLASH LOG OFF\r\n");

    } else { printf("Unknown cmd. Use: start, stop, s<val>, p<val>, stream on/off, zscatune, capture, events, testL, calib,"
                    " dtcinfo, dtc on/off, dtccomp on/off, DK<val>, DM<val>, DS<+1|-1>, dtcreset,"
                    " dumpflash, clearflash, flashinfo, flashlog on/off\r\n"); }
}
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* Неблокирующая передача: кольцевой буфер + HAL_UART_Transmit_IT.
   При переполнении байты отбрасываются (счетчик tx_dropped в стриме \"txd\"). */
static uint16_t uart2_tx_pending(void) {
    return (uint16_t)((tx_ring_head + UART_TX_RING_SIZE - tx_ring_tail) % UART_TX_RING_SIZE);
}
static uint16_t uart2_tx_free(void) {
    return (uint16_t)(UART_TX_RING_SIZE - 1U - uart2_tx_pending());
}
static void uart2_tx_kick(void) {
    if (tx_ring_busy) return;
    uint16_t n = 0U;
    while ((n < sizeof(tx_chunk)) && (tx_ring_tail != tx_ring_head)) {
        tx_chunk[n++] = tx_ring[tx_ring_tail];
        tx_ring_tail = (uint16_t)((tx_ring_tail + 1U) % UART_TX_RING_SIZE);
    }
    if (n == 0U) return;
    tx_ring_busy = 1U;
    (void)HAL_UART_Transmit_IT(&huart2, tx_chunk, n);
}
static void uart2_tx_enqueue(const uint8_t *p, uint16_t n) {
    uint32_t primask = __get_PRIMASK(); __disable_irq();
    if (uart2_tx_free() < n) {
        tx_dropped += n;
    } else {
        for (uint16_t i = 0U; i < n; i++) {
            tx_ring[tx_ring_head] = p[i];
            tx_ring_head = (uint16_t)((tx_ring_head + 1U) % UART_TX_RING_SIZE);
        }
        uart2_tx_kick();
    }
    __set_PRIMASK(primask);
}
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance != USART2) return;
    tx_ring_busy = 0U;
    uart2_tx_kick();
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance != USART2) { return; }
    char c = (char)uart_rx_byte;

    if (!uart_rx_ready) {
        if ((c == '\r') || (c == '\n')) {
            if (uart_rx_len > 0U) {
                uart_rx_buf[uart_rx_len] = '\0';
                uart_rx_ready = 1U;
            }
        } else if (uart_rx_len < (UART_RX_BUF_SIZE - 1U)) {
            uart_rx_buf[uart_rx_len] = c;
            uart_rx_len++;
        }
    }
    HAL_UART_Receive_IT(&huart2, &uart_rx_byte, 1U);
}
// Обработчик ошибок UART (предотвращает зависание приема при Overrun Error)
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart) {
    if (huart->Instance == USART2) {
        __HAL_UART_CLEAR_OREFLAG(huart); // Сбрасываем флаг переполнения
        HAL_UART_Receive_IT(&huart2, &uart_rx_byte, 1U); // Перезапускаем прием
    }
}

void HAL_ADCEx_InjectedConvCpltCallback(ADC_HandleTypeDef* hadc) {
    if (hadc->Instance != ADC1) return;
    uint32_t _cyc0 = DWT->CYCCNT;
    isr_tick_count++;
    g_isr_ts++;

    float a1 = (float)hadc1.Instance->JDR1;
    float b1 = (float)hadc1.Instance->JDR2;
    float c1_raw = (float)hadc1.Instance->JDR3; // ADC1 IN6 (PC0) для отладки

    float a2 = (float)hadc2.Instance->JDR1;
    float b2 = (float)hadc2.Instance->JDR2;
    float c2_raw = (float)hadc2.Instance->JDR3; // ADC2 IN4 для отладки

    /* OEW: сумма фазных токов НЕ равна нулю (ток нулевой последовательности),
       поэтому расчёт C по Кирхгофу неприменим — читаем реальный датчик C (JDR3) */
    last_raw_a1 = (uint16_t)a1; last_raw_b1 = (uint16_t)b1; last_raw_c1 = (uint16_t)c1_raw;
    last_raw_a2 = (uint16_t)a2; last_raw_b2 = (uint16_t)b2; last_raw_c2 = (uint16_t)c2_raw;

    current_update(&g_curr, a1, b1, c1_raw);
    current_update(&g_curr2, a2, b2, c2_raw);

    /* Непрерывная кольцевая запись burst-буфера (пре-триггер).
       Пишется до защит и расчетов, чтобы захватывать и такты после fault.
       izs/ang/FOC-состояния и duty (из CCR) — с предыдущего такта.
       Децимация BURST_DECIM: запись каждого N-го такта для увеличения окна. */
    if (!burst.busy) {
        if (burst.decim_cnt > 0U) { burst.decim_cnt--; }
        if (burst.decim_cnt == 0U) {
        burst.decim_cnt = BURST_DECIM;
        BurstSample_t *s = &burst_buf[burst.head];
        s->ts  = g_isr_ts;
        s->ia1 = (int16_t)(g_curr.ia  * 1000.0f);
        s->ib1 = (int16_t)(g_curr.ib  * 1000.0f);
        s->ic1 = (int16_t)(g_curr.ic  * 1000.0f);
        s->ia2 = (int16_t)(g_curr2.ia * 1000.0f);
        s->ib2 = (int16_t)(g_curr2.ib * 1000.0f);
        s->ic2 = (int16_t)(g_curr2.ic * 1000.0f);
        s->izs = (int16_t)(izs_val * 1000.0f);
        s->ang = (uint16_t)(ugol1 * (65536.0f / TWO_PI));
        s->id   = (int16_t)(g_foc.id * 1000.0f);
        s->iq   = (int16_t)(g_foc.iq * 1000.0f);
        s->id_t = (int16_t)(g_foc.id_target * 1000.0f);
        s->iq_t = (int16_t)(g_foc.iq_target * 1000.0f);
        s->vd   = (int16_t)(g_foc.vd * 1000.0f);
        s->vq   = (int16_t)(g_foc.vq * 1000.0f);
        s->vd_raw = (int16_t)(g_foc.vd_raw * 1000.0f);
        s->vq_raw = (int16_t)(g_foc.vq_raw * 1000.0f);
        s->wr   = (int16_t)((g_foc.omega_r / TWO_PI) * 10.0f);
        s->slip = (int16_t)((g_foc.omega_slip / TWO_PI) * 100.0f);
        s->psi  = (int16_t)(g_foc.psi_mag * 10000.0f);
        s->te   = (int16_t)(g_foc.te_est * 1000.0f);
        s->pi_id_i  = (int16_t)(g_pi_id.integ * 1000.0f);
        s->pi_iq_i  = (int16_t)(g_pi_iq.integ * 1000.0f);
        s->pi_spd_i = (int16_t)(g_pi_speed.integ * 1000.0f);
        s->sat_flags = g_foc.sat_flags;
        s->e_alpha = (int16_t)(g_foc.e_alpha * 1000.0f);
        s->e_beta  = (int16_t)(g_foc.e_beta * 1000.0f);
        s->tgt_spd = (int16_t)(Target_Speed * 10.0f);
        s->theta_jit = (int16_t)(s_theta_jit * 10000.0f);
        s->diq_dt = (int16_t)(s_diq_dt * 1000.0f);
        s->dwr_dt = (int16_t)(s_dwr_dt * 1000.0f);
        s->p_elec = (int16_t)(g_foc.p_elec * 1000.0f);
        s->q_elec = (int16_t)(g_foc.q_elec * 1000.0f);
        s->kdt     = (int16_t)(g_dtc.Kdt * 10.0f);
        s->dtc_corr = (int16_t)(g_dtc.corr_lpf * 10000.0f);
        s->d1a = (uint16_t)TIM1->CCR1; s->d1b = (uint16_t)TIM1->CCR2; s->d1c = (uint16_t)TIM1->CCR3;
        s->d2a = (uint16_t)TIM8->CCR1; s->d2b = (uint16_t)TIM8->CCR2; s->d2c = (uint16_t)TIM8->CCR3;
        burst.head = (uint16_t)((burst.head + 1U) % BURST_CAP_SIZE);
        if (burst.filled < BURST_CAP_SIZE) burst.filled++;
        if (burst.triggered) {
            if (burst.post_left > 0U) burst.post_left--;
            if (burst.post_left == 0U) { burst.triggered = 0U; burst.busy = 1U; }
        }
        } /* end decim block */
    }

    float target_speed = isfinite(Target_Speed) ? clampf(Target_Speed, -MAX_SPEED_ELEC_HZ, MAX_SPEED_ELEC_HZ) : 0.0f;
    float target_power = isfinite(Target_Power) ? clampf(Target_Power, 0.0f, MAX_POWER) : 0.0f;
    float dt = control_isr_dt;

    if (g_curr.calibrated) {
        float di_a = fabsf(g_curr.ia - i_prev_a);
        float di_b = fabsf(g_curr.ib - i_prev_b);
        float di_c = fabsf(g_curr.ic - i_prev_c);
        i_prev_a = g_curr.ia; i_prev_b = g_curr.ib; i_prev_c = g_curr.ic;
        if (!test_l_active && (test_r_ticks == 0U) &&
            ((di_a > I_DIDT_MAX_PER_TICK) || (di_b > I_DIDT_MAX_PER_TICK) || (di_c > I_DIDT_MAX_PER_TICK))) {
            pwm_shutdown_fault();
            evt_push(EVT_FAULT_DIDT, di_a, di_b, di_c);
            burst_trigger(1U);  /* auto: fault */
            return;
        }

        float i_abs = fmaxf(fmaxf(fabsf(g_curr.ia), fabsf(g_curr.ib)), fabsf(g_curr.ic));
        float i_rms = sqrtf(fmaxf(g_curr.rms2_lpf, 0.0f));
        if (i_abs > I_PHASE_ABS_MAX_A) {
            pwm_shutdown_fault();
            evt_push(EVT_FAULT_IABS, g_curr.ia, g_curr.ib, g_curr.ic);
            burst_trigger(1U);  /* auto: fault */
            return;
        }
        if (i_rms > I_RMS_MAX_A) {
            pwm_shutdown_fault();
            evt_push(EVT_FAULT_IRMS, i_rms, i_abs, 0.0f);
            burst_trigger(1U);  /* auto: fault */
            return;
        }

        if (Fault_OverCurrent == 0U && Run_Enabled) {
            float err = clampf(Target_Current_A, 0.0f, I_RMS_MAX_A * 0.95f) - i_rms;
            target_power = pi_step(&g_i_pi, err, dt);
            Target_Power = clampf(target_power, 0.0f, MAX_POWER);
            if (Target_Power >= MAX_POWER * 0.99f) g_foc.sat_flags |= SAT_POWER;
            if (i_rms > I_RMS_MAX_A * 0.9f)      g_foc.sat_flags |= SAT_CURRENT;
        } else {
            target_power = 0.0f;
            Target_Power = 0.0f;
            g_i_pi.integ = 0.0f;   // анти-виндап на останове
        }
    }

    // testL выполняется ПОСЛЕ current_update и токовых защит:
    // в буфер пишется актуальный ток, защиты активны во время теста
    if (test_l_active) {
        if (test_l_idx < TEST_L_SAMPLES) {
            /* Этап 1: импульс A+ / B-, замер ia, ib */
            uint32_t test_duty = HALF_PERIOD + 850;
            TIM1->CCR1 = test_duty; TIM1->CCR2 = PWM_PERIOD - test_duty; TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = PWM_PERIOD - test_duty; TIM8->CCR2 = test_duty; TIM8->CCR3 = HALF_PERIOD;
            test_l_ia[test_l_idx] = (int16_t)(g_curr.ia * 1000.0f);
            test_l_ib[test_l_idx] = (int16_t)(g_curr.ib * 1000.0f);
            test_l_ia2[test_l_idx] = (int16_t)(g_curr2.ia * 1000.0f);
            test_l_ib2[test_l_idx] = (int16_t)(g_curr2.ib * 1000.0f);
            test_l_idx++;
        } else if (test_l_idx < (2U * TEST_L_SAMPLES)) {
            /* Этап 2: импульс C+, замер ic (проверка полярности датчика C) */
            uint32_t test_duty = HALF_PERIOD + 850;
            TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = test_duty;
            TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = PWM_PERIOD - test_duty;
            test_l_ic[test_l_idx - TEST_L_SAMPLES] = (int16_t)(g_curr.ic * 1000.0f);
            test_l_idx++;
        } else {
            TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = HALF_PERIOD;
            test_l_active = 0U;
            print_testl = 1U; // Ошибка 2: Вывод перенесен в main loop
        }
        return;
    }

    /* testR: постоянный DC (A+ / B-) для сверки показаний ia с мультиметром */
    if (test_r_ticks) {
        uint32_t test_duty = HALF_PERIOD + 850;
        TIM1->CCR1 = test_duty; TIM1->CCR2 = PWM_PERIOD - test_duty; TIM1->CCR3 = HALF_PERIOD;
        TIM8->CCR1 = PWM_PERIOD - test_duty; TIM8->CCR2 = test_duty; TIM8->CCR3 = HALF_PERIOD;
        test_r_ticks--;
        if (test_r_ticks == 0U) {
            TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = HALF_PERIOD;
        }
        return;
    }

    uint32_t d1a = HALF_PERIOD, d1b = HALF_PERIOD, d1c = HALF_PERIOD;
    uint32_t d2a = HALF_PERIOD, d2b = HALF_PERIOD, d2c = HALF_PERIOD;

    g_foc.sat_flags = 0U;  /* сброс в начале каждого такта ISR, до ветвления по режиму */

    if (foc_startup_state == FOC_STARTUP_RAMPING) {
        float ramp_target_speed = fmaxf(fabsf(target_speed), FOC_MIN_STARTUP_HZ);
        if (target_speed < 0.0f) ramp_target_speed = -ramp_target_speed;

        if (current_speed < ramp_target_speed) { current_speed += SPEED_RATE * dt; if (current_speed > ramp_target_speed) current_speed = ramp_target_speed; }
        else if (current_speed > ramp_target_speed) { current_speed -= SPEED_RATE * dt; if (current_speed < ramp_target_speed) current_speed = ramp_target_speed; }

        current_power = Target_Power;
        ugol1 += TWO_PI * current_speed * dt;
        ugol1 = fmodf(ugol1, TWO_PI); if (ugol1 < 0.0f) ugol1 += TWO_PI;

        float amp = current_power * (float)PWM_PERIOD * 0.5f;
        float va = amp * CORDIC_Cos(ugol1);
        float vb = amp * CORDIC_Cos(ugol1 - 2.0943951f);
        float vc = amp * CORDIC_Cos(ugol1 + 2.0943951f);

        /* SVPWM: common-mode инжекция расширяет линейный диапазон модуляции */
        svpwm_inject(&va, &vb, &vc);

        // Во время разгона всегда жесткая противофаза (максимальное напряжение на катушке)
        float v1a = 0.5f * va, v1b = 0.5f * vb, v1c = 0.5f * vc;
        float v2a = -0.5f * va, v2b = -0.5f * vb, v2c = -0.5f * vc;

        float izs = 0.0f;
        if (g_curr.calibrated && g_curr2.calibrated) {
            izs = ((g_curr.ia + g_curr.ib + g_curr.ic) * 0.333333f) - ((g_curr2.ia + g_curr2.ib + g_curr2.ic) * 0.333333f);
        }
        izs_val = izs;
        float vzs_corr = (g_zsc_atune.state == ZSC_ATUNE_RUNNING) ? zsc_autotune_step(&g_zsc_atune, izs, dt) : pi_step(&g_zsc_pi, -izs, dt);

        d1a = clamp_u32_from_float((float)HALF_PERIOD + v1a - vzs_corr, 0U, PWM_PERIOD);
        d1b = clamp_u32_from_float((float)HALF_PERIOD + v1b - vzs_corr, 0U, PWM_PERIOD);
        d1c = clamp_u32_from_float((float)HALF_PERIOD + v1c - vzs_corr, 0U, PWM_PERIOD);
        d2a = clamp_u32_from_float((float)HALF_PERIOD + v2a + vzs_corr, 0U, PWM_PERIOD);
        d2b = clamp_u32_from_float((float)HALF_PERIOD + v2b + vzs_corr, 0U, PWM_PERIOD);
        d2c = clamp_u32_from_float((float)HALF_PERIOD + v2c + vzs_corr, 0U, PWM_PERIOD);

        /* Адаптивная компенсация мёртвого времени. Применяется и в режиме
           разгона — это убирает 6-ю гармонику из preliminary V/f и делает
           переход на FOC более гладким. Ток фазы берём из первого инвертора
           (g_curr), поскольку izs уже близок к нулю благодаря ZSC PI. */
        if (g_curr.calibrated) {
            int32_t sa = dtc_phase_correction(&g_dtc, g_curr.ia);
            int32_t sb = dtc_phase_correction(&g_dtc, g_curr.ib);
            int32_t sc = dtc_phase_correction(&g_dtc, g_curr.ic);
            d1a = clamp_u32_from_float((float)d1a + (float)sa, 0U, PWM_PERIOD);
            d1b = clamp_u32_from_float((float)d1b + (float)sb, 0U, PWM_PERIOD);
            d1c = clamp_u32_from_float((float)d1c + (float)sc, 0U, PWM_PERIOD);
            d2a = clamp_u32_from_float((float)d2a - (float)sa, 0U, PWM_PERIOD);
            d2b = clamp_u32_from_float((float)d2b - (float)sb, 0U, PWM_PERIOD);
            d2c = clamp_u32_from_float((float)d2c - (float)sc, 0U, PWM_PERIOD);
        }

        // Как только достигли минимальной скорости — переключаемся на FOC
        if (fabsf(current_speed) >= FOC_MIN_STARTUP_HZ * 0.95f) {
            float ialpha0, ibeta0;
            Clarke_Transform(g_curr.ia, g_curr.ib, &ialpha0, &ibeta0);

            g_foc.theta = ugol1;
            g_foc.omega_r = TWO_PI * current_speed;
            g_foc.psi_alpha = g_motor.Lm * ialpha0;
            g_foc.psi_beta  = g_motor.Lm * ibeta0;
            g_foc.theta_prev = g_foc.theta;
            g_foc.iq_prev = 0.0f;
            g_foc.wr_prev = g_foc.omega_r;
            s_theta_jit = 0.0f; s_diq_dt = 0.0f; s_dwr_dt = 0.0f;
            /* Сброс hold-таймера DTC: после входа в FOC даём наблюдателю
               успокоиться перед началом адаптации Kdt. */
            g_dtc.hold_ticks = 0U;
            g_dtc.converged_ticks = 0U;
            g_dtc.converged = 0U;
            g_dtc.corr_lpf = 0.0f;
            foc_startup_state = FOC_STARTUP_RUNNING;
            print_foc_engaged = 1U;
            evt_push(EVT_FOC_ENGAGED, current_speed, 0.0f, 0.0f);
        }
    }
    else if (foc_startup_state == FOC_STARTUP_RUNNING) {

        // 1. Вычисляем целевые токи
        float Id_target_base = Target_Power * 2.0f;
        float Iq_target = pi_step(&g_pi_speed, target_speed - (g_foc.omega_r / TWO_PI), dt);
        g_foc.iq_target = Iq_target;
        if (Iq_target >= g_pi_speed.out_max || Iq_target <= g_pi_speed.out_min)
            g_foc.sat_flags |= SAT_SPEED;

        // 2. Преобразование токов (Кларк-Парк)
        float ialpha, ibeta;
        Clarke_Transform(g_curr.ia, g_curr.ib, &ialpha, &ibeta);
        RotorFluxObserver(&g_foc, &g_motor, ialpha, ibeta, dt);

        /* Адаптивная компенсация мёртвого времени: шаг адаптации Kdt.
           Запускается сразу после наблюдателя, когда e_alpha/e_beta свежие.
           i_rms берём из g_curr.rms2_lpf — это LPF от защитного контура. */
        float dtc_i_rms = sqrtf(fmaxf(g_curr.rms2_lpf, 0.0f));
        float dtc_speed_hz = g_foc.omega_r / TWO_PI;
        dtc_adapt_step(&g_dtc, g_foc.e_alpha, g_foc.e_beta,
                       ialpha, ibeta, dtc_i_rms, dtc_speed_hz, dt,
                       (g_zsc_atune.state == ZSC_ATUNE_RUNNING) ? 1U : 0U);

        float sin_t = CORDIC_Sin(g_foc.theta);
        float cos_t = CORDIC_Cos(g_foc.theta);
        Park_Transform(ialpha, ibeta, sin_t, cos_t, &g_foc.id, &g_foc.iq);

        // --- 3. ОСЛАБЛЕНИЕ ПОЛЯ (FIELD WEAKENING) ---
        // FW-коррекция Id-задания по vd/vq предыдущего такта — ДО единственного вызова PI
        float vmag = sqrtf(g_foc.vd * g_foc.vd + g_foc.vq * g_foc.vq);
        float v_max_limit = 0.95f;  /* SVPWM расширяет линейный предел до 1.0 */
        float Id_target = Id_target_base;
        if (vmag > v_max_limit) {
            Id_target += pi_step(&g_pi_fw, vmag - v_max_limit, dt);
            g_foc.sat_flags |= SAT_FW;
        } else {
            g_pi_fw.integ = 0.0f; 
        }
        g_foc.id_target = Id_target;

        // 4. Расчет напряжений Vd, Vq (ровно один вызов pi_step на регулятор за такт)
        g_foc.vd = pi_step(&g_pi_id, Id_target - g_foc.id, dt);
        g_foc.vq = pi_step(&g_pi_iq, Iq_target - g_foc.iq, dt);
        g_foc.vd_raw = g_foc.vd;  /* до circle limiter */
        g_foc.vq_raw = g_foc.vq;

        // Circle limiter: приоритет Vd, Vq урезается по остатку окружности |V|<=1
        float v2 = g_foc.vd * g_foc.vd + g_foc.vq * g_foc.vq;
        if (v2 > 1.0f) {
            float vq_lim = sqrtf(fmaxf(1.0f - g_foc.vd * g_foc.vd, 0.0f));
            g_foc.vq = (g_foc.vq >= 0.0f) ? vq_lim : -vq_lim;
            g_pi_iq.integ = g_foc.vq - g_pi_iq.kp * (Iq_target - g_foc.iq); // анти-виндап
            g_foc.sat_flags |= SAT_CIRCLE;
        }
        // 5. Обратное преобразование Парка
        float valpha, vbeta;
        Inv_Park_Transform(g_foc.vd, g_foc.vq, sin_t, cos_t, &valpha, &vbeta);
        /* Энергетический баланс: P = 3/2*(Vα*Iα + Vβ*Iβ), Q = 3/2*(Vβ*Iα - Vα*Iβ) */
        g_foc.p_elec = 1.5f * (valpha * ialpha + vbeta * ibeta);
        g_foc.q_elec = 1.5f * (vbeta * ialpha - valpha * ibeta);
        // 6. Формирование напряжений фаз
        float va_norm, vb_norm, vc_norm;
        Inv_Clarke_Transform(valpha, vbeta, &va_norm, &vb_norm, &vc_norm);
        /* SVPWM: common-mode инжекция расширяет линейный диапазон модуляции */
        svpwm_inject(&va_norm, &vb_norm, &vc_norm);
        float izs = ((g_curr.ia + g_curr.ib + g_curr.ic) * 0.333333f) - ((g_curr2.ia + g_curr2.ib + g_curr2.ic) * 0.333333f);
        izs_val = izs;
        float vzs_corr = (g_zsc_atune.state == ZSC_ATUNE_RUNNING) ? zsc_autotune_step(&g_zsc_atune, izs, dt) : pi_step(&g_zsc_pi, -izs, dt);
        float va_duty = va_norm * (float)HALF_PERIOD;
        float vb_duty = vb_norm * (float)HALF_PERIOD;
        float vc_duty = vc_norm * (float)HALF_PERIOD;
        // 7. Жесткая противофаза инверторов во всем диапазоне скоростей
        float k1 = 0.5f;
        float k2 = -0.5f;
        /* Компенсация мёртвого времени: Kdt*sign(i_phase) добавляется к inv1
           и вычитается из inv2. В OEW-конфигурации с жёсткой противофазой это
           даёт двойной эффект на обмотке (ΔV_winding = 2*Kdt*sign(i)). */
        int32_t sa = dtc_phase_correction(&g_dtc, g_curr.ia);
        int32_t sb = dtc_phase_correction(&g_dtc, g_curr.ib);
        int32_t sc = dtc_phase_correction(&g_dtc, g_curr.ic);
        d1a = clamp_u32_from_float((float)HALF_PERIOD + (k1 * va_duty) - vzs_corr + (float)sa, 0U, PWM_PERIOD);
        d1b = clamp_u32_from_float((float)HALF_PERIOD + (k1 * vb_duty) - vzs_corr + (float)sb, 0U, PWM_PERIOD);
        d1c = clamp_u32_from_float((float)HALF_PERIOD + (k1 * vc_duty) - vzs_corr + (float)sc, 0U, PWM_PERIOD);
        d2a = clamp_u32_from_float((float)HALF_PERIOD + (k2 * va_duty) + vzs_corr - (float)sa, 0U, PWM_PERIOD);
        d2b = clamp_u32_from_float((float)HALF_PERIOD + (k2 * vb_duty) + vzs_corr - (float)sb, 0U, PWM_PERIOD);
        d2c = clamp_u32_from_float((float)HALF_PERIOD + (k2 * vc_duty) + vzs_corr - (float)sc, 0U, PWM_PERIOD);
        if (d1a == 0U || d1a == PWM_PERIOD || d1b == 0U || d1b == PWM_PERIOD ||
            d1c == 0U || d1c == PWM_PERIOD || d2a == 0U || d2a == PWM_PERIOD ||
            d2b == 0U || d2b == PWM_PERIOD || d2c == 0U || d2c == PWM_PERIOD)
            g_foc.sat_flags |= SAT_DUTY;
        ugol1 = g_foc.theta;
        current_speed = g_foc.omega_r / TWO_PI; // синхронизация с оценкой наблюдателя
        /* Джиттер угла: Δθ - ω*dt, где Δθ = θ(k)-θ(k-1) с unwrap в [-π, π] */
        float dtheta = g_foc.theta - g_foc.theta_prev;
        while (dtheta > M_PI)  dtheta -= TWO_PI;
        while (dtheta < -M_PI) dtheta += TWO_PI;
        s_theta_jit = dtheta - g_foc.omega_r * dt;
        g_foc.theta_prev = g_foc.theta;
        /* Производные для диагностики колебаний */
        s_diq_dt = (g_foc.iq - g_foc.iq_prev) / dt;
        s_dwr_dt = (g_foc.omega_r - g_foc.wr_prev) / dt;
        g_foc.iq_prev = g_foc.iq;
        g_foc.wr_prev = g_foc.omega_r;
        // Если скорость упала — возвращаемся в режим разгона
        if (fabsf(g_foc.omega_r / TWO_PI) < FOC_LOST_HZ) {
            foc_startup_state = FOC_STARTUP_RAMPING;
            print_foc_lost = 1U;
            evt_push(EVT_FOC_LOST, g_foc.omega_r / TWO_PI, 0.0f, 0.0f);
            burst_trigger(1U);  /* auto: FOC lost sync */
        }
    }
    // FOC_STARTUP_IDLE оставляет d1a..d2c равными HALF_PERIOD (безопасный нейтральный вектор)
    TIM1->CCR1 = d1a; TIM1->CCR2 = d1b; TIM1->CCR3 = d1c;
    TIM8->CCR1 = d2a; TIM8->CCR2 = d2b; TIM8->CCR3 = d2c;

    /* Flash Black Box: 10 Гц snapshot в SRAM ring buffer (main loop дренирует во flash).
       1000 тактов ISR при 10 кГц = 100 мс. */
    static uint16_t snap_decim = 0U;
    if (snap_decim > 0U) snap_decim--;
    if (snap_decim == 0U) {
        snap_decim = 1000U;
        uint16_t next = (uint16_t)((snap_ctrl.head + 1U) % SNAP_SRAM_SIZE);
        if (next != snap_ctrl.tail) {
            FlashSnap_t *s = &snap_buf[snap_ctrl.head];
            s->ts = g_isr_ts;
            s->id = (int16_t)(g_foc.id * 1000.0f);
            s->iq = (int16_t)(g_foc.iq * 1000.0f);
            s->vd = (int16_t)(g_foc.vd * 1000.0f);
            s->vq = (int16_t)(g_foc.vq * 1000.0f);
            s->wr = (int16_t)((g_foc.omega_r / TWO_PI) * 10.0f);
            s->slip = (int16_t)((g_foc.omega_slip / TWO_PI) * 100.0f);
            s->te = (int16_t)(g_foc.te_est * 1000.0f);
            s->psi = (int16_t)(g_foc.psi_mag * 10000.0f);
            s->sat_flags = g_foc.sat_flags | (Fault_OverCurrent ? SAT_FAULT_BIT : 0U);
            s->foc_state = (uint8_t)foc_startup_state;
            s->p_elec = (int16_t)(g_foc.p_elec * 1000.0f);
            s->q_elec = (int16_t)(g_foc.q_elec * 1000.0f);
            s->e_alpha = (int16_t)(g_foc.e_alpha * 1000.0f);
            s->e_beta = (int16_t)(g_foc.e_beta * 1000.0f);
            s->kdt = (uint16_t)(g_dtc.Kdt * 10.0f);
            snap_ctrl.head = next;
        } else {
            snap_ctrl.overflow = 1U;
        }
    }

    /* Замер времени выполнения ISR */
    uint32_t _cyc1 = DWT->CYCCNT;
    uint32_t _cyc = _cyc1 - _cyc0;
    isr_cycles_sum += _cyc;
    isr_cycles_cnt++;
    if (_cyc > isr_cycles_max) isr_cycles_max = _cyc;
}
int __io_putchar(int ch) {
    uint8_t b = (uint8_t)ch;
    uart2_tx_enqueue(&b, 1U);
    return ch;
}

/* __io_getchar намеренно отключён:
   syscalls.c::_read дёргает его через stdin-буфер Newlib,
   а HAL_UART_Receive здесь блокирующий — он съедает байты,
   которые предназначены для IT-приёма команд (start, stop, s50, ...).
   Оставляем заглушку, чтобы линкер не падал. */
int __io_getchar(void) {
    return (int)0;
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_ADC1_Init();
  MX_ADC2_Init();
  MX_TIM1_Init();
  MX_TIM8_Init();
  MX_CORDIC_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */

  // 1. Запускаем ШИМ на обоих таймерах
  HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
  HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_2);
  HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_3);
  HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_1);
  HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_2);
  HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_3);

  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_1);
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_2);
  HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_3);
  HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_1);
  HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_2);
  HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_3);

  // 2. Включаем главный выход (Main Output Enable) для TIM1 и TIM8
  __HAL_TIM_MOE_ENABLE(&htim1);
  __HAL_TIM_MOE_ENABLE(&htim8);

  // 3. Запускаем инжектированные измерения АЦП с прерываниями
  HAL_ADCEx_InjectedStart_IT(&hadc1);
  HAL_ADCEx_InjectedStart(&hadc2);

  // 4. Запускаем прием данных по UART
  HAL_UART_Receive_IT(&huart2, &uart_rx_byte, 1U);
  SET_BIT(USART2->CR1, USART_CR1_RXNEIE);  // страховка: явно включаем RXNE IT в регистре

  printf("System Started. Pure FOC Mode.\r\n");

  /* Включение DWT (Data Watchpoint and Trace) для замера времени ISR */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  /* Flash Black Box: возобновление записи после сброса питания.
     Сканирует bank 2, находит первую пустую запись. */
  flash_log_scan_resume();
  if (g_flash_log.record_count > 0U) {
    printf("FLASH LOG: resumed at %lu records (addr=0x%08lX)\r\n",
        (unsigned long)g_flash_log.record_count,
        (unsigned long)g_flash_log.write_addr);
  }

  /* USER CODE END 2 */

  /* Initialize led */
  BSP_LED_Init(LED_GREEN);

  /* Initialize USER push-button, will be used to trigger an interrupt each time it's pressed.*/
  BSP_PB_Init(BUTTON_USER, BUTTON_MODE_EXTI);

  /* USER CODE BEGIN BSP */

  /* -- Sample board code to switch on led ---- */
  BSP_LED_On(LED_GREEN);

  /* USER CODE END BSP */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */

  static uint32_t last_tick = 0;

  while (1)
  {

    /* -- Sample board code for User push-button in interrupt mode ---- */
    if (BspButtonState == BUTTON_PRESSED)
    {
      BspButtonState = BUTTON_RELEASED;
      BSP_LED_Toggle(LED_GREEN);
    }
/*    if (uart_rx_ready) {
            // простой тест: шлём обратно то, что пришло
            uart2_tx_blocking((uint8_t*)"RX:", 3);
            uart2_tx_blocking((uint8_t*)uart_rx_buf, uart_rx_len);
            uart2_tx_blocking((uint8_t*)"\r\n", 2);
            uart_rx_ready = 0;
            uart_rx_len = 0;
        }*/

    // Обработка входящих команд с терминала
    if (uart_rx_ready) {
        char cmd_copy[UART_RX_BUF_SIZE];
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        memcpy(cmd_copy, (const void*)uart_rx_buf, uart_rx_len + 1U); // +1 включая '\0'
        uart_rx_len = 0U;
        uart_rx_ready = 0U;
        __set_PRIMASK(primask);

        process_uart_command(cmd_copy);
    }

    // Обработка автозапуска
    switch (auto_start_state) {
        case AUTO_START_IDLE:
            break;

        case AUTO_START_CALIB:
            if (g_curr.calibrated && g_curr2.calibrated) {
                auto_start_state = AUTO_START_SPIN;
                auto_start_t0 = HAL_GetTick();
                Run_Enabled = 1U;
                uint32_t primask = __get_PRIMASK(); __disable_irq();
                foc_startup_state = FOC_STARTUP_RAMPING;
                __set_PRIMASK(primask);
                printf("AUTO START: Spinning up...\r\n");
            } else if (HAL_GetTick() - auto_start_t0 > AUTO_CALIB_TIMEOUT_MS) {
                printf("AUTO START: calib timeout\r\n");
                auto_start_state = AUTO_START_IDLE;
            }
            break;

        case AUTO_START_SPIN:
            if (HAL_GetTick() - auto_start_t0 > AUTO_SPIN_TIME_MS) {
                uint32_t primask = __get_PRIMASK(); __disable_irq();
                g_zsc_atune = (ZscAutotune_t){ .state = ZSC_ATUNE_RUNNING, .relay_amp = ZSC_ATUNE_RELAY_AMP };
                g_zsc_pi.integ = 0.0f;
                __set_PRIMASK(primask);
                auto_start_state = AUTO_START_ZSC;
                auto_start_t0 = HAL_GetTick();
                printf("AUTO START: ZSC autotune...\r\n");
            }
            break;

        case AUTO_START_ZSC:
            if (g_zsc_atune.state == ZSC_ATUNE_DONE) {
                auto_start_state = AUTO_START_DONE;
                printf("AUTO START: ZSC tuned, restoring targets\r\n");
            } else if (g_zsc_atune.state == ZSC_ATUNE_FAILED ||
                       (HAL_GetTick() - auto_start_t0 > AUTO_ZSC_TIMEOUT_MS)) {
                printf("AUTO START: ZSC autotune failed/timeout\r\n");
                auto_start_state = AUTO_START_IDLE;
                Run_Enabled = 0U;
            }
            break;

        case AUTO_START_DONE: {
            uint32_t primask = __get_PRIMASK(); __disable_irq();
            Target_Speed = autosave_speed;
            Target_Power = autosave_power;
            Target_Current_A = autosave_current;
            foc_startup_state = FOC_STARTUP_RAMPING; 
            g_pi_id.integ = 0.0f; g_pi_iq.integ = 0.0f; g_pi_speed.integ = 0.0f;
            g_pi_fw.integ = 0.0f;
            g_foc.psi_alpha = 0.0f; g_foc.psi_beta = 0.0f; g_foc.theta = 0.0f; g_foc.omega_r = 0.0f;
            /* DTC: подготовка к работе в FOC — hold-timer обнулится при
               переходе RAMPING→RUNNING, corr_lpf сбрасываем для чистой адаптации. */
            g_dtc.corr_lpf = 0.0f;
            g_dtc.e_norm_lpf = 0.0f;
            g_dtc.converged = 0U;
            g_dtc.converged_ticks = 0U;
            g_dtc.sat_reported = 0U;
            g_dtc.sat_cooldown = 0U;
            __set_PRIMASK(primask);
            auto_start_state = AUTO_START_IDLE;
            printf("AUTO START: DONE\r\n");
            break;
        }
    }

    // Ошибка 2: Безопасный вывод сообщений из ISR
    if (print_testl) {
        print_testl = 0U;
        printf("$TESTL_BEGIN\r\n");
        for (int i = 0; i < TEST_L_SAMPLES; i++) { printf("%d,%d,%d,%d,%d,%d\r\n", i, test_l_ia[i], test_l_ib[i], test_l_ic[i], test_l_ia2[i], test_l_ib2[i]); }
        printf("$TESTL_END\r\n");
    }
    if (print_foc_engaged) {
        print_foc_engaged = 0U;
        printf("FOC ENGAGED\r\n");
    }
    if (print_foc_lost) {
        print_foc_lost = 0U;
        printf("FOC LOST (Low speed) -> Ramping\r\n");
    }
    if (print_dtc_converged) {
        print_dtc_converged = 0U;
        printf("DTC CONVERGED: Kdt=%.2f corr=%.5f (dead-time compensated)\r\n",
               print_dtc_kdt_val, print_dtc_corr_val);
    }

    // Отправка потока данных (JSON) в терминал, если включен стрим (пейсинг без блокировки)
    // Пауза во время burst dump — даёт выгрузке захвата 100% полосы UART.
    static uint32_t last_stream_tick = 0;
    if (Stream_Enabled && !burst.busy && (HAL_GetTick() - last_stream_tick >= 20U)) {
        last_stream_tick = HAL_GetTick();
        UART_Send_Stream();
    }

    /* Сигнализация переполнения flash-лога — каждые 10 сек */
    static uint32_t last_flash_warn = 0;
    if (g_flash_log.full && (HAL_GetTick() - last_flash_warn >= 10000U)) {
        last_flash_warn = HAL_GetTick();
        printf("WARNING: FLASH LOG FULL (%lu records). Use 'clearflash' to erase.\r\n",
               (unsigned long)g_flash_log.record_count);
    }

    // Выгрузка захваченных данных (Capture), если был запрос
    if (burst.busy) {
        burst_dump_step();
    }

    /* Flash Black Box: drain SRAM snapshot buffer → flash bank 2.
       Не выполняется во время burst dump (приоритет UART — burst). */
    if (!burst.busy) {
        flash_log_drain();
    }

    // Ошибка 8: Атомарный сброс isr_tick_count для измерения частоты
    uint32_t current_tick = HAL_GetTick();
    if (current_tick - last_tick >= 1000) { // Каждую 1 секунду
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        uint32_t ticks = isr_tick_count;
        isr_tick_count = 0U;
        __set_PRIMASK(primask);

        float hz = (float)ticks * 1000.0f / (float)(current_tick - last_tick);
        measured_isr_hz = hz;
        if (fabsf(hz - EXPECTED_ISR_HZ) <= (EXPECTED_ISR_HZ * ISR_HZ_TOLERANCE)) {
            control_isr_dt = 1.0f / hz;
        }
        /* Обновление статистики времени ISR */
        if (isr_cycles_cnt > 0U) {
            uint32_t cyc_per_us = SystemCoreClock / 1000000U;
            isr_us_avg = (isr_cycles_sum / isr_cycles_cnt) / cyc_per_us;
            isr_us_max = isr_cycles_max / cyc_per_us;
            isr_cycles_sum = 0U; isr_cycles_cnt = 0U; isr_cycles_max = 0U;
        }
        last_tick = current_tick;
    }
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1_BOOST);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV6;
  RCC_OscInitStruct.PLL.PLLN = 85;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_MultiModeTypeDef multimode = {0};
  ADC_InjectionConfTypeDef sConfigInjected = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
  hadc1.Init.Resolution = ADC_RESOLUTION_12B;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.GainCompensation = 0;
  hadc1.Init.ScanConvMode = ADC_SCAN_ENABLE;
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc1.Init.LowPowerAutoWait = DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.NbrOfConversion = 1;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.DMAContinuousRequests = DISABLE;
  hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc1.Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure the ADC multi-mode
  */
  multimode.Mode = ADC_DUALMODE_INJECSIMULT;
  multimode.DMAAccessMode = ADC_DMAACCESSMODE_DISABLED;
  multimode.TwoSamplingDelay = ADC_TWOSAMPLINGDELAY_1CYCLE;
  if (HAL_ADCEx_MultiModeConfigChannel(&hadc1, &multimode) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_1;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_1;
  sConfigInjected.InjectedSamplingTime = ADC_SAMPLETIME_12CYCLES_5;
  sConfigInjected.InjectedSingleDiff = ADC_SINGLE_ENDED;
  sConfigInjected.InjectedOffsetNumber = ADC_OFFSET_NONE;
  sConfigInjected.InjectedOffset = 0;
  sConfigInjected.InjectedNbrOfConversion = 3;
  sConfigInjected.InjectedDiscontinuousConvMode = DISABLE;
  sConfigInjected.AutoInjectedConv = DISABLE;
  sConfigInjected.QueueInjectedContext = DISABLE;
  sConfigInjected.ExternalTrigInjecConv = ADC_EXTERNALTRIGINJEC_T1_TRGO;
  sConfigInjected.ExternalTrigInjecConvEdge = ADC_EXTERNALTRIGINJECCONV_EDGE_RISING;
  sConfigInjected.InjecOversamplingMode = DISABLE;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc1, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_5;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_2;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc1, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_6;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_3;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc1, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief ADC2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC2_Init(void)
{

  /* USER CODE BEGIN ADC2_Init 0 */

  /* USER CODE END ADC2_Init 0 */

  ADC_InjectionConfTypeDef sConfigInjected = {0};
  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC2_Init 1 */

  /* USER CODE END ADC2_Init 1 */

  /** Common config
  */
  hadc2.Instance = ADC2;
  hadc2.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
  hadc2.Init.Resolution = ADC_RESOLUTION_12B;
  hadc2.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc2.Init.GainCompensation = 0;
  hadc2.Init.ScanConvMode = ADC_SCAN_ENABLE;
  hadc2.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc2.Init.LowPowerAutoWait = DISABLE;
  hadc2.Init.ContinuousConvMode = DISABLE;
  hadc2.Init.NbrOfConversion = 1;
  hadc2.Init.DiscontinuousConvMode = DISABLE;
  hadc2.Init.DMAContinuousRequests = DISABLE;
  hadc2.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc2.Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(&hadc2) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_2;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_1;
  sConfigInjected.InjectedSamplingTime = ADC_SAMPLETIME_12CYCLES_5;
  sConfigInjected.InjectedSingleDiff = ADC_SINGLE_ENDED;
  sConfigInjected.InjectedOffsetNumber = ADC_OFFSET_NONE;
  sConfigInjected.InjectedOffset = 0;
  sConfigInjected.InjectedNbrOfConversion = 3;
  sConfigInjected.InjectedDiscontinuousConvMode = DISABLE;
  sConfigInjected.AutoInjectedConv = DISABLE;
  sConfigInjected.QueueInjectedContext = DISABLE;
  sConfigInjected.InjecOversamplingMode = DISABLE;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc2, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_3;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_2;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc2, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_4;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_3;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc2, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_2;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_12CYCLES_5;
  sConfig.SingleDiff = ADC_SINGLE_ENDED;
  sConfig.OffsetNumber = ADC_OFFSET_NONE;
  sConfig.Offset = 0;
  if (HAL_ADC_ConfigChannel(&hadc2, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC2_Init 2 */

  /* USER CODE END ADC2_Init 2 */

}

/**
  * @brief CORDIC Initialization Function
  * @param None
  * @retval None
  */
static void MX_CORDIC_Init(void)
{

  /* USER CODE BEGIN CORDIC_Init 0 */

  /* USER CODE END CORDIC_Init 0 */

  /* USER CODE BEGIN CORDIC_Init 1 */

  /* USER CODE END CORDIC_Init 1 */
  hcordic.Instance = CORDIC;
  if (HAL_CORDIC_Init(&hcordic) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN CORDIC_Init 2 */

  // Явная конфигурация CORDIC для FOC:
  //   FUNC=0     -> Cosine (CORDIC_Cos напрямую, CORDIC_Sin через сдвиг на pi/2)
  //   PRECISION=24 -> высокая точность (по умолчанию 4 итераций недостаточно для токового контура)
  //   SCALE=0    -> корректно для cosine (выход в q31 [-1, 1])
  //   ARGSIZE=3  -> q31 angle в радианах [-pi, pi) (по умолчанию)
  //   RESSIZE=3  -> q31 output (по умолчанию)
  // Используем MODIFY_REG, чтобы не сбросить прочие поля CSR.
  MODIFY_REG(CORDIC->CSR,
             CORDIC_CSR_FUNC_Msk | CORDIC_CSR_PRECISION_Msk,
             (0U  << CORDIC_CSR_FUNC_Pos)      |  // FUNC = 0: Cosine
             (24U << CORDIC_CSR_PRECISION_Pos));  // PRECISION = 24 итераций

  /* USER CODE END CORDIC_Init 2 */

}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */

  /* USER CODE END TIM1_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */

  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 0;
  htim1.Init.CounterMode = TIM_COUNTERMODE_CENTERALIGNED1;
  htim1.Init.Period = 8499;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 1;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_ENABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_TIM_DISABLE_OCxPRELOAD(&htim1, TIM_CHANNEL_1);
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_TIM_DISABLE_OCxPRELOAD(&htim1, TIM_CHANNEL_2);
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_TIM_DISABLE_OCxPRELOAD(&htim1, TIM_CHANNEL_3);
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 120;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.BreakAFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.Break2AFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */

  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

}

/**
  * @brief TIM8 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM8_Init(void)
{

  /* USER CODE BEGIN TIM8_Init 0 */

  /* USER CODE END TIM8_Init 0 */

  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM8_Init 1 */

  /* USER CODE END TIM8_Init 1 */
  htim8.Instance = TIM8;
  htim8.Init.Prescaler = 0;
  htim8.Init.CounterMode = TIM_COUNTERMODE_CENTERALIGNED1;
  htim8.Init.Period = 8499;
  htim8.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim8.Init.RepetitionCounter = 0;
  htim8.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_TRIGGER;
  sSlaveConfig.InputTrigger = TIM_TS_ITR0;
  if (HAL_TIM_SlaveConfigSynchro(&htim8, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim8, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 120;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.BreakAFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.Break2AFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim8, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM8_Init 2 */

  /* USER CODE END TIM8_Init 2 */
  HAL_TIM_MspPostInit(&htim8);

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 921600;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart2.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_4) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_4) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief BSP Push Button callback
  * @param Button Specifies the pressed button
  * @retval None
  */
void BSP_PB_Callback(Button_TypeDef Button)
{
  if (Button == BUTTON_USER)
  {
    BspButtonState = BUTTON_PRESSED;
  }
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
