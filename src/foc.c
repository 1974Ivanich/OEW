#include "foc.h"
#include "stm32g474xx.h"
#include "cordic_math.h"
#include "observer.h"
#include "pll.h"
#include "flux_weakening.h"
#include "vf_start.h"
#include "adc.h"
#include "pwm.h"
#include "voltage_manager.h"
#include "autotune.h"   /* g_motor_params (Lm, Rr, Tr) для Lσ компенсации */
#include "encoder.h"    /* AS5048A — mechanical speed for encoder-based FOC */
#include "protect.h"    /* PROTECT_IsFault — interlock FOC_Start (ревью PR-02) */
#include "vf_control.h" /* VFC_IsRunning() — mutual exclusion */
#include "foc_handoff_gate.h" /* Ревью TEST-03: тестируемый I-f→RUN handoff gate */
#include "uart.h"       /* UART_TrySendStr — предупреждение Tr-fallback (UART-01: из ISR только Try) */

static inline int32_t foc_abs(int32_t x) {
    if(x == INT32_MIN) return INT32_MAX;
    return x < 0 ? -x : x;
}

/* Q15 математические константы, используемые преобразованиями.
 * Раньше были «магическими числами» в теле функций. */
#define FOC_INV_SQRT3_Q15       18919  /* 1/√3 ≈ 0.57735 → Q15 */
#define FOC_SQRT3_Q15           56756  /* √3  ≈ 1.73205 → Q15 */

AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw) {
    /* Трёхдатчиковое преобразование Кларке (амплитудно-инвариантное).
     * Iα = (2·Iu − Iv − Iw) / 3
     * Iβ = (Iv − Iw) / √3
     * √3 ≈ 1.73205; 1/√3 ≈ 0.57735 → 18919 / 32768.
     * При iw = −(iu + iv) сводится к классической двухдатчиковой форме.
     * Для OEW iw восстанавливается из трансформаторного датчика суммы
     * токов Ires (PA6 = ADC2_IN3), теперь подключенного к правильному каналу. */
    AlphaBeta ab;
    ab.alpha = (int32_t)(((int64_t)2*iu - iv - iw) / 3);
    ab.beta  = (int32_t)(((int64_t)(iv - iw) * FOC_INV_SQRT3_Q15) >> 15);
    return ab;
}

DQ Park_Transform(int32_t alpha, int32_t beta, int32_t theta_q31) {
    DQ dq;
    int32_t s, c;
    CORDIC_SinCos(theta_q31, &s, &c);
    dq.d = (int32_t)(((int64_t)alpha * c + (int64_t)beta * s) >> 15);
    dq.q = (int32_t)((-(int64_t)alpha * s + (int64_t)beta * c) >> 15);
    return dq;
}

AlphaBeta InvPark_Transform(int32_t vd, int32_t vq, int32_t theta_q31) {
    AlphaBeta vab;
    int32_t s, c;
    CORDIC_SinCos(theta_q31, &s, &c);
    vab.alpha = (int32_t)(((int64_t)vd * c - (int64_t)vq * s) >> 15);
    vab.beta  = (int32_t)(((int64_t)vd * s + (int64_t)vq * c) >> 15);
    return vab;
}

void InvClarke_Transform(int32_t valpha, int32_t vbeta, int32_t *vu, int32_t *vv, int32_t *vw) {
    /* Обратное преобразование Кларка (амплитудно-инвариантное).
     * Vu = Vα
     * Vv = (−Vα + √3·Vβ) / 2
     * Vw = (−Vα − √3·Vβ) / 2
     * √3 ≈ 1.73205 → 56756 / 32768. */
    *vu = valpha;
    /* int64: vbeta в транзиенте до ~46341 (32768·√2 из InvPark до клэмпа VM),
     * 46341·56756 ≈ 2.63e9 > INT32_MAX — знаковое переполнение (UB) инвертировало
     * бы фазы V/W. */
    int32_t sqrt3_vb = (int32_t)(((int64_t)vbeta * FOC_SQRT3_Q15) >> 15);
    *vv = (-valpha + sqrt3_vb) / 2;
    *vw = (-valpha - sqrt3_vb) / 2;
}

/* PI */
void PI_Init(PIController *pi, int32_t kp, int32_t ki, int32_t max, int32_t min) {
    pi->kp = kp; pi->ki = ki;
    pi->kw = 32768;  /* default Kw = 1.0 */
    pi->integral = 0;
    pi->out_max = max; pi->out_min = min;
}

int32_t PI_Update(PIController *pi, int32_t error) {
    /* P-term и накопление интегратора. int64 в умножении: kp/ki
     * из модульного оптимума для АД (Ls в мГн) дают kp·error до ~1e11 —
     * int32 переполнился бы. Anti-windup единый — внешний, от VM
     * через PI_BackCalculation. Внутренний back-calculation удалён,
     * чтобы не дублировать коррекцию. */
    int32_t p_term = (int32_t)(((int64_t)pi->kp * error) >> 15);
    pi->integral += (int32_t)(((int64_t)pi->ki * error) >> 15);
    /* Интегратор ограничиваем — он не должен «выползать» при
     * длительном насыщении; final clamp делает PI_BackCalculation. */
    pi->integral = CLAMP(pi->integral, pi->out_min, pi->out_max);
    /* Ревью Grok п.3 (ВЫСОКАЯ): int64-сумма корректна, но каст в int32
     * без saturating может дать переполнение (например p_term=3e9 →
     * отрицательное). VM рассчитан на Q15, но защита обязательна: выход
     * ВНЕШНЕ ограничивается VM (p_term проходит насквозь — проверено
     * тестом foc_math_test.c), а здесь только saturating-каст в int32. */
    int64_t out64 = (int64_t)p_term + pi->integral;
    if(out64 > INT32_MAX) return INT32_MAX;
    if(out64 < INT32_MIN) return INT32_MIN;
    return (int32_t)out64;
}

/* External anti-windup: коррекция интегратора от внешнего ограничителя (VM).
 * saturation_error = out_limited - out_commanded (отрицательное при ограничении).
 * integral += Kw * saturation_error, где Kw настраивается независимо.
 * После коррекции ограничиваем интегратор, чтобы он не уходил за рамки. */
void PI_BackCalculation(PIController *pi, int32_t saturation_error) {
    pi->integral += (int32_t)(((int64_t)pi->kw * saturation_error) >> 15);
    pi->integral = CLAMP(pi->integral, pi->out_min, pi->out_max);
}

/* FOC state */
static volatile uint8_t foc_running = 0;
static volatile int32_t speed_ref_rpm = 0;
static volatile int32_t id_ref_ma = 0;
static volatile int32_t iq_ref_ma = 0;               /* ручное задание Iq (мА); 0 = контур скорости */
static volatile int32_t meas_speed_erpm = 0; /* измеренная эл. скорость, обновляется в FOC_Run */
static volatile uint32_t meas_theta_q31 = 0; /* текущий эл. угол q31 */
static volatile int32_t pole_pairs = 4;   /* FOC_DEFAULT_POLE_PAIRS; задаётся из GUI (p=N) */
static volatile int32_t fw_base_speed_rpm = 1000;  /* FW speed gate (FW-01); из GUI (fwbase=N) */
static int32_t vbus_filtered_mv = 0;
static int32_t w_pll_filtered_q31 = 0;
static int32_t w_pll_raw_q31 = 0;       /* raw PLL speed — для диагностики */
static uint8_t speed_filter_init = 0;   /* флаг инициализации фильтра скорости */

/* ── Encoder-based phase accumulator для АД ─────────────────────────── */
static uint32_t enc_phase_accum = 0;    /* θe: uint32 wrap-around = 2π */
static int32_t  f_slip_hz = 0;          /* slip frequency, Hz (from Iq/Id model) */
static uint8_t   tr_fallback_warned = 0;  /* одноразовое предупреждение Tr-fallback */
static int32_t  f_e_hz = 0;             /* electrical stator frequency, Hz */
static int32_t  enc_speed_rpm_filtered = 0;  /* filtered mechanical speed */
static int32_t  enc_speed_rpm_prev = 0;      /* for stability check */
static uint8_t  enc_filter_init = 0;         /* filter init flag */
static int32_t  enc_delta_theta = 0;     /* Δθ per FOC cycle (full-turn units) — for decoupling */

typedef enum { FOC_STATE_STARTUP = 0, FOC_STATE_RUN } FOCState;

static BEMFObserver observer;
static PLL pll;
static PIController pi_d, pi_q, pi_spd;
static FluxWeakening fw;
static VFStart vf;
static VoltageManager vm;
static FOCState foc_state = FOC_STATE_STARTUP;
static int foc_initialized = 0;

/* ── Ревью VFS-02/04 + TEST-03: bounded I-f → RUN handoff policy ──────
 * Переход — через тестируемую state machine FocHandoffGate (host-тест
 * foc_handoff_gate_test): required_consecutive циклов устойчивой EMF,
 * общий watchdog от старта + post-complete timeout, latch READY/TIMEOUT
 * до следующего FOC_Start. FOC_Stop() из ISR безопасен: PWM_Disable
 * снимает CEN → TRGO исчезает → ISR больше не вызывается. */
static FocHandoffGate handoff_gate;

/* FOCStartupFail — enum в foc.h (общий с GUI/телеметрией). */
static uint8_t startup_fail_reason = FOC_STARTUP_OK;

/* Ревью VFS-04: причина последнего неудачного startup (телеметрия/GUI). */
int FOC_GetStartupFailReason(void) { return startup_fail_reason; }

/*
 * Параметры по умолчанию. Подобраны для типичного PMSM-мотора 24В/5А.
 * R, L нужно уточнять по datasheet мотора; kp/ki PI-регуляторов —
 * тюнить на реальной нагрузке. Здесь даны стартовые безопасные значения.
 */
#define FOC_DEFAULT_R_MOHM      50     /* 0.05 Ом */
#define FOC_DEFAULT_L_UH        100    /* 100 мкГн */
#define FOC_DEFAULT_TS_US       200    /* 5 кГц — период ШИМ */
#define FOC_DEFAULT_VDC_MV      24000  /* 24В шина */
#define FOC_DEFAULT_PI_KP       2000
#define FOC_DEFAULT_PI_KI       100
/* Ревью PLL-01: коэффициенты в единицах omega_q31 = Δθ/цикл (5 кГц):
 * 1 eRPM = FOC_OMEGA_PER_ERPM = 14317. Старые kp=1000/ki=50 не могли
 * захватить скорость (набор 1000 eRPM ~57 c). kp=500000 (полоса ~1 Гц,
 * коррекция фазы ~35 eRPM на единичную ошибку), ki=5000 (набор 1000 eRPM
 * ~1 c при err~0.5). Диагностика; при использовании PLL как feedback —
 * параметризовать в Hz. */
#define FOC_DEFAULT_PLL_KP      500000
#define FOC_DEFAULT_PLL_KI      5000
#define FOC_DEFAULT_FW_KP       200
#define FOC_DEFAULT_FW_KI       10
#define FOC_DEFAULT_ID_REF_MA   2000   /* Id_ref = 2A — намагничивание АД */
#define FOC_VM_VMAX_Q15         29490  /* 90% от 32767 — запас для линейности PWM */
#define FOC_VM_PRIORITY         VM_PRIORITY_FLUX  /* АД (не PMSM!): Vd/поток приоритет —
                                                     комментарий исправлен по ревью Grok */

/* Контур скорости и open-loop старт */
#define FOC_DEFAULT_POLE_PAIRS  4      /* пары полюсов по умолчанию; меняется командой p=N */
#define FOC_POLE_PAIRS_MIN      1
#define FOC_POLE_PAIRS_MAX      24
#define FOC_OMEGA_PER_ERPM      14317  /* Δθ(q31) за цикл Ts на 1 эл. об/мин.
 * theta_u32 — uint32, 2^32 = 2π (полный эл. оборот), НЕ 2^31.
 * Δθ = 2^32 / (60 × Fs) = 4294967296 / (60 × 5000) = 14316.56 ≈ 14317.
 * Проверка: 1 eRPM → 14317 × 5000 = 71585000/с → 2^32/71585000 = 60.0с = 1 об. ✓
 *
 * ВНИМАНИЕ (ревью VFS-03): 14317 — только для сравнений/диагностики.
 * Для НАКОПЛЕНИЯ фазы использовать foc_delta_theta() — константа 14317
 * даёт дрейф +0.4423 ед. на eRPM·цикл: ~22° за ramp 2с до 120000 eRPM,
 * в RUN 0.44°/с на 12000 eRPM (уезжает ориентация dq). */
#define FOC_DELTA_THETA_NUM     4294967296LL   /* 2^32 — полный эл. оборот */
#define FOC_DELTA_THETA_DEN     300000LL       /* 60 × 5000 (Ts = 200 мкс) */

/* Δθ(q31) за цикл для эл. скорости erpm·p (int64, округление): */
static inline int64_t foc_delta_theta(int64_t erpm_p) {
    return (erpm_p * FOC_DELTA_THETA_NUM + FOC_DELTA_THETA_DEN / 2) / FOC_DELTA_THETA_DEN;
}

/* Параметры OEW-распределения и компенсации ключей. */
#define FOC_MOD_MAX_Q15        32112  /* 98% от 32768 — запас на линейность PWM (было FOC_OEW_DUTY_MAX=49/50) */

/* Коэффициент знаменателя перекрёстных связей:
 * E_Q15 = Δθ·Lσ·I / (Vbus·KDEN), где Δθ в full-turn units (2^32=2π).
 * KDEN = 2^32·Vbus_mV / (2π·Fs·Lσ_uH·1e-6·I_int·0.1·32768)
 *       = 2^32 / (2π·5000·1e-6·0.1·32768) = 41722
 * Ошибка ×2 исправлена: было 20860 (считали для 2^31, а phase accumulator = 2^32). */
#define FOC_DECOUPLE_KDEN       41722

/* Dead-time и падение на силовых ключах (OEW: 2 инвертора, знак по току).
 * STGIB20M60TS-L IGBT: VCE(sat) typ 1.55 В @ 20 А, ~1.75 В @ 25 А (на 1 IGBT).
 * В OEW ток идёт через 2 IGBT: Vf суммарное ~1.5 В, R суммарное ~80 мОм. */
#define FOC_DTCOMP_I0           3      /* порог плавного sign, ед. мА/100 (~300 мА) */
#define FOC_INV_R_MOHM          80     /* суммарное сопротивление 2 IGBT, мОм */
#define FOC_INV_VF_MV           1500   /* суммарное Vf 2 IGBT, мВ */

#define FOC_VF_RAMP_MS          2000   /* разгон open-loop, мс */
#define FOC_STARTUP_IQ          30     /* ~3 А в внутр. единицах (мА/100) */
#define FOC_STARTUP_ID          20     /* ~2 А намагничивания на старте */
#define FOC_SPD_KP              2000   /* Ревью Grok п.2: НАСТРОЙКА ПОД МОТОР!
                                            При тек. масштабе (err>>8) 1 rpm →
                                            P≈13 ед., 11 rpm → уже IQ_MAX=150.
                                            Контур почти всегда насыщен. При
                                            первом железном прогоне проверить
                                            и подобрать (см. ROADMAP). */
#define FOC_SPD_KI              50
#define FOC_IQ_MAX              150    /* ±15 А — внутренний жёсткий лимит задания */
#define FOC_I_MAX_MA            10000  /* 10 А — лимит модуля IPM/двигателя (ревью FOC-03):
                                          круг тока sqrt(Id²+Iq²) ≤ FOC_I_MAX_MA;
                                          подстроить под реальный силовой модуль */

/* ── Encoder-based FOC для АД (slip frequency model) ──────────────────
 * f_slip = (1/(2π·Tr))·(Iq/Id) — steady-state rotor flux model.
 * f_e = p·n_mech/60 + f_slip → phase accumulator → θe.
 * PLL сохранён как диагностический (сравнение encoder vs observer). */
#define FOC_MAX_SLIP_HZ         5      /* |f_slip| ≤ 5 Hz */
#define FOC_MAX_FE_HZ           200    /* |f_e| ≤ 200 Hz */
#define FOC_MAX_SLIP_DT         ((int32_t)((int64_t)FOC_MAX_SLIP_HZ * FOC_PHASE_PER_HZ))  /* ~4294965 */
#define FOC_MAX_FE_DT           ((int32_t)((int64_t)FOC_MAX_FE_HZ * FOC_PHASE_PER_HZ))   /* ~171798600 */
#define FOC_ENC_FILTER_SHIFT    3      /* IIR 1/8 для encoder speed */
#define FOC_SLIP_2PI_INV        159155 /* 1e6/(2π) — для f_slip = Iq·K/(Id·Tr_us) */
#define FOC_PHASE_PER_HZ        858993 /* 2^32/5000 — Δθ(q31) per Hz per FOC cycle */
#define FOC_ENC_MIN_RPM         30     /* мин. скорость для перехода V/f→FOC */
#define FOC_MIN_ID_SLIP         10     /* мин. |Id| (мА/100) для вычисления slip = 1 A */
#define FOC_SPD_ERR_SHIFT       8      /* speed PI error scaling (q31>>8) */

/* Ревью TEST-03: конфигурация handoff-gate — значения прежнего инлайн-кода:
 * 50 циклов устойчивости (10 мс), watchdog 5 с от старта и 5 с после
 * завершения рампы (25000 циклов @5кГц). */
static const FocHandoffConfig handoff_cfg = {
    .emf_min = FOC_EMF_MIN_THRESHOLD,
    .enc_min_rpm = FOC_ENC_MIN_RPM,
    .max_jerk_rpm_per_cycle = 200,
    .min_id_internal = FOC_MIN_ID_SLIP,
    .required_consecutive = 50,
    .max_startup_cycles = 25000,
    .max_handoff_cycles = 25000
};

/* ── Сохранённые параметры автотюнинга (tz_foc_params) ─────────────── */
static int32_t motor_R_mOhm  = FOC_DEFAULT_R_MOHM;
static int32_t motor_L_uH    = FOC_DEFAULT_L_UH;
static int32_t motor_Kp      = FOC_DEFAULT_PI_KP;
static int32_t motor_Ki      = FOC_DEFAULT_PI_KI;
static int32_t foc_lsigma_uH = 0;   /* Lσ статора для компенсации перекрёстных связей (мкГн) */
static int     params_applied = 0;   /* 0 = дефолты, 1 = применены из автотюнинга */


void FOC_Init(void) {
    if(foc_initialized) return;
    /* Lσ: при дефолтах Lm/Rr/Tr неизвестны → Lσ = Ls (observer консистентен) */
    if(foc_lsigma_uH == 0) foc_lsigma_uH = motor_L_uH;
    BEMF_Init(&observer, motor_R_mOhm, foc_lsigma_uH, FOC_DEFAULT_TS_US, ADC_GetVbus_mV());
    PLL_Init(&pll, FOC_DEFAULT_PLL_KP, FOC_DEFAULT_PLL_KI);
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_spd, FOC_SPD_KP, FOC_SPD_KI, FOC_IQ_MAX, -FOC_IQ_MAX);
    FW_Init(&fw, FOC_DEFAULT_FW_KP, FOC_DEFAULT_FW_KI);
    VM_Init(&vm, FOC_VM_VMAX_Q15, FOC_VM_PRIORITY);
    FW_SetVmaxQ15(&fw, VM_GetVmax(&vm));  /* VM — единый источник Vmax */
    FW_SetBaseSpeedRpm(&fw, fw_base_speed_rpm);  /* FW-01: speed gate из GUI */
    speed_ref_rpm = 0;
    id_ref_ma = FOC_DEFAULT_ID_REF_MA;
    pole_pairs = FOC_DEFAULT_POLE_PAIRS;
    foc_initialized = 1;
}

#define FOC_MAX_RPM  5000
/* Ревью FOC-04: макс. механическая скорость из лимита f_e (200 Гц) и пар
 * полюсов: 200·60/p (при p=4 → 3000 rpm; 5000 rpm было бы 333 Гц > 200 Гц). */
int32_t FOC_GetMaxSpeedRPM(void) {
    int32_t pp = pole_pairs; if (pp < 1) pp = 1;
    int32_t m = (int32_t)((int64_t)FOC_MAX_FE_HZ * 60 / pp);
    return (m > FOC_MAX_RPM) ? FOC_MAX_RPM : m;
}
void FOC_SetSpeed(int32_t rpm) {
    int32_t max_rpm = FOC_GetMaxSpeedRPM();
    if(rpm > max_rpm) rpm = max_rpm;
    if(rpm < -max_rpm) rpm = -max_rpm;
    speed_ref_rpm = rpm;
    /* Если FOC в V/f разгоне — обновляем цель рампы на лету.
     * Иначе цель, зафиксированная в FOC_Start (часто 0), останется
     * навсегда — мотор не раскрутится. */
    if(foc_running && foc_state == FOC_STATE_STARTUP) {
        /* Критическая секция: VF_Update работает в ADC ISR */
        __disable_irq();
        VF_SetTarget(&vf, speed_ref_rpm * pole_pairs);
        __enable_irq();
        /* Новая рампа → сброс handoff-gate (watchdog от старта) и причины. */
        FocHandoffGate_Init(&handoff_gate);
        startup_fail_reason = FOC_STARTUP_OK;
    }
}
int32_t FOC_GetSpeed(void) { return speed_ref_rpm; }
/* Ревью FOC-03: команды тока клэмпятся к лимиту модуля — пользовательская
 * команда не может запросить ток выше FOC_I_MAX_MA. */
void FOC_SetIdRef(int32_t ma) {
    if(ma > FOC_I_MAX_MA) ma = FOC_I_MAX_MA;
    if(ma < -FOC_I_MAX_MA) ma = -FOC_I_MAX_MA;
    id_ref_ma = ma;
}
void FOC_SetIqRef(int32_t ma) {
    if(ma > FOC_I_MAX_MA) ma = FOC_I_MAX_MA;
    if(ma < -FOC_I_MAX_MA) ma = -FOC_I_MAX_MA;
    iq_ref_ma = ma;
}

/* Измеренная механическая скорость, об/мин (эл. скорость / пары полюсов) */
int32_t FOC_GetMeasSpeedRPM(void) { return meas_speed_erpm / pole_pairs; }

/* Текущий электрический угол в миллирадианах (0..6283) */
int32_t FOC_GetThetaMilliRad(void) {
    return (int32_t)(((uint64_t)meas_theta_q31 * 6283u) >> 32);
}

/* Пары полюсов: менять только при остановленном FOC — влияет на пересчёт
 * rpm → электрическая скорость в V/f и контуре скорости. */
int FOC_SetPolePairs(int32_t pp) {
    if(foc_running) return -1;
    if(pp < FOC_POLE_PAIRS_MIN || pp > FOC_POLE_PAIRS_MAX) return -1;
    pole_pairs = pp;
    return 0;
}

int32_t FOC_GetPolePairs(void) { return pole_pairs; }

/* Базовая скорость для ослабления поля (FW-01, speed gate). */
int FOC_SetBaseSpeed(int32_t rpm) {
    if (rpm < 100 || rpm > 5000) return -1;
    fw_base_speed_rpm = rpm;
    return 0;
}
int32_t FOC_GetBaseSpeed(void) { return fw_base_speed_rpm; }

/* ── Модульный оптимум (Антиучебник, §3.4, Табл.3.1, стр.42) ──────────
 * Контур тока АД, объект R+sL, компенсация большой постоянной Ti = L/R:
 *   Kp_phys[В/А] = L / (2·a·Tμ),   Ki_phys = Kp/Ti = Kp·R/L
 * где a — коэффициент Табл.3.1 (a=2 → перерегулирование 4.3%, время 4.7·Tμ),
 * Tμ — малые постоянные: задержка ШИМ + фильтр тока ≈ Ts.
 * Пересчёт в единицы кода: p_term = (kp·error)>>15,
 * error в CURRENT_INT (мА/100 = 0.1 А), output в Q15 (V/Vbus·32768).
 * Kp_code = Kp_phys · 0.1 · 32768 / Vdc_В
 *         = L_uH · 3276800000 / (2·a·Ts_us·Vdc_mV)
 *   ki = kp·Ts_us·R_mOhm / (L_uH·1000)          (дискретный интегратор) */
#define FOC_PI_OPTIMUM_A        2      /* Табл.3.1: 4.3% перерегулирование */
void FOC_ComputePIGains(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv,
                        int32_t *kp_out, int32_t *ki_out) {
    int32_t kp = 0, ki = 0;
    if(l_uh >= 1 && r_mohm >= 1 && vdc_mv >= 1000) {
        /* kp = L_uH·32768·0.1·1e6 / (2·a·Ts_us·Vdc_mV) = L_uH·3276800000 / den */
        int64_t num = (int64_t)l_uh * 3276800000LL;
        int64_t den = (int64_t)2 * FOC_PI_OPTIMUM_A * FOC_DEFAULT_TS_US * vdc_mv;
        kp = (int32_t)(num / den);
        /* ki = kp·Ts·R/L = kp·Ts_us·R_mOhm/(L_uH·1000) */
        ki = (int32_t)(((int64_t)kp * FOC_DEFAULT_TS_US * r_mohm) / ((int64_t)l_uh * 1000));
        if(kp < 0) kp = 0;
        if(ki < 0) ki = 0;
    }
    if(kp_out) *kp_out = kp;
    if(ki_out) *ki_out = ki;
}

void FOC_ComputePIGainsBW(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv,
                          int32_t bw_hz, int32_t *kp_out, int32_t *ki_out) {
    int32_t kp = 0, ki = 0;
    if(l_uh >= 1 && r_mohm >= 1 && vdc_mv >= 1000 && bw_hz >= 1) {
        /* a = 1/(2·bw·Ts). Ts в секундах = Ts_us/1e6.
         * a = 1e6 / (2·bw_hz·Ts_us). Clamp [1,100] для стабильности. */
        int32_t a = (int32_t)(1000000LL / ((int64_t)2 * bw_hz * FOC_DEFAULT_TS_US));
        if(a < 1) a = 1;
        if(a > 100) a = 100;
        int64_t num = (int64_t)l_uh * 3276800000LL;
        int64_t den = (int64_t)2 * a * FOC_DEFAULT_TS_US * vdc_mv;
        kp = (int32_t)(num / den);
        ki = (int32_t)(((int64_t)kp * FOC_DEFAULT_TS_US * r_mohm) / ((int64_t)l_uh * 1000));
        if(kp < 0) kp = 0;
        if(ki < 0) ki = 0;
    }
    if(kp_out) *kp_out = kp;
    if(ki_out) *ki_out = ki;
}

uint8_t FOC_GetState(void) { return (uint8_t)foc_state; }

/* ── tz_foc_params: применение параметров автотюнинга ──────────────── */
int FOC_SetMotorParams(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv) {
    if(foc_running) return -1;
    if(r_mohm < 1 || l_uh < 1) return -2;
    motor_R_mOhm = r_mohm;
    motor_L_uH   = l_uh;
    /* Модульный оптимум: Kp/Ki автоматически из Rs/Ls (Антиучебник §3.4).
     * a=2 → 4.3% перерегулирования, Tμ=Ts=200 мкс. */
    int32_t vdc = (vdc_mv > 0) ? vdc_mv : FOC_DEFAULT_VDC_MV;
    FOC_ComputePIGains(r_mohm, l_uh, vdc, &motor_Kp, &motor_Ki);
    /* Lσ для компенсации перекрёстных связей: Lσs = Ls − Lm²/Lr
     * (из схемы замещения АД); если Lm/Rr/Tr неизвестны — Lσ ≈ Ls. */
    foc_lsigma_uH = l_uh;
    if(g_motor_params.Lm_uH > 0 && g_motor_params.Tr_rotor_us > 0 &&
       g_motor_params.Rr_mOhm > 0) {
        /* Lr = Tr·Rr (нГн/мкГн): Lr_uH = Tr_us·Rr_mOhm/1000 */
        int64_t lr_uH = (int64_t)g_motor_params.Tr_rotor_us * g_motor_params.Rr_mOhm / 1000;
        if(lr_uH > 0) {
            int64_t lm_uH = g_motor_params.Lm_uH;
            int64_t lsigma = (int64_t)l_uh - (lm_uH * lm_uH) / lr_uH;
            if(lsigma >= l_uh / 10) foc_lsigma_uH = (int32_t)lsigma;  /* не менее 10% Ls */
        }
    }
    /* Антиучебник стр.171 (модель насыщаемого АД): observer должен
     * использовать Lσ (рассеяние), а НЕ полную Ls = Lσs + Lm.
     * V = R·Is + Lσ·dIs/dt + dψr/dt → EMF = dψr/dt оценивается верно
     * при любом насыщении Lm — без дифференциальной индуктивности. */
    BEMF_Init(&observer, motor_R_mOhm, foc_lsigma_uH, FOC_DEFAULT_TS_US, vdc);
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    params_applied = 1;
    return 0;
}

int FOC_SetPIGains(int32_t kp, int32_t ki) {
    if(foc_running) return -1;
    if(kp < 0 || ki < 0) return -2;
    motor_Kp = kp;
    motor_Ki = ki;
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    params_applied = 1;
    return 0;
}

int FOC_IsParamsApplied(void) { return params_applied; }

int32_t FOC_GetSigmaL_uH(void) { return foc_lsigma_uH; }

void FOC_GetMotorParams(int32_t *r_mohm, int32_t *l_uh, int32_t *kp, int32_t *ki) {
    if(r_mohm) *r_mohm = motor_R_mOhm;
    if(l_uh)   *l_uh   = motor_L_uH;
    if(kp)     *kp     = motor_Kp;
    if(ki)     *ki     = motor_Ki;
}

int FOC_IsRunning(void) { return foc_running != 0; }

/* Напряжения, выданные в предыдущем FOC-цикле — нужны observer'у и FW */
static int32_t prev_valpha = 0;
static int32_t prev_vbeta  = 0;
static int32_t prev_vd = 0;
static int32_t prev_vq = 0;
static int32_t prev_dq_d = 0;  /* Id предыдущего цикла — для вычисления slip */
static int32_t prev_dq_q = 0;  /* Iq предыдущего цикла — для вычисления slip */

void FOC_Start(void) {
    if(foc_running) return;
    if(VFC_IsRunning()) return;  /* не запускать поверх V/f-режима */
    /* Ревью PR-02: latched fault — interlock: PWM не включается поверх
     * аварии; сброс только через PROTECT_RequestClear() (команда 'f'). */
    if(PROTECT_IsFault()) {
        UART_SendStr("FOC start blocked: fault latched, send 'f' to clear\r\n");
        return;
    }
    if(!foc_initialized) FOC_Init();
    /* Калибровка нуля токов — непосредственно перед запуском,
     * пока инвертор выключен (токи истинно нулевые). */
    ADC_CalibrateOffsets();
    /* Сброс состояний перед каждым запуском.
     * Observer должен использовать Lσ, а не полную Ls. */
    BEMF_Init(&observer, motor_R_mOhm, foc_lsigma_uH, FOC_DEFAULT_TS_US, ADC_GetVbus_mV());
    PLL_Init(&pll, FOC_DEFAULT_PLL_KP, FOC_DEFAULT_PLL_KI);
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    pi_d.integral = 0;
    pi_q.integral = 0;
    pi_spd.integral = 0;
    prev_valpha = prev_vbeta = 0;
    prev_vd = prev_vq = 0;
    w_pll_filtered_q31 = 0;
    w_pll_raw_q31 = 0;
    speed_filter_init = 0;
    /* Encoder-based FOC state resets */
    enc_phase_accum = 0;
    f_slip_hz = 0;
    f_e_hz = 0;
    enc_speed_rpm_filtered = 0;
    enc_speed_rpm_prev = 0;
    enc_filter_init = 0;
    enc_delta_theta = 0;
    prev_dq_d = 0;
    prev_dq_q = 0;
    /* Сброс состояния FW (интегратор, флаг) при каждом запуске */
    FW_Init(&fw, FOC_DEFAULT_FW_KP, FOC_DEFAULT_FW_KI);
    VM_Init(&vm, FOC_VM_VMAX_Q15, FOC_VM_PRIORITY);
    FW_SetVmaxQ15(&fw, VM_GetVmax(&vm));  /* VM — единый источник Vmax */
    FW_SetBaseSpeedRpm(&fw, fw_base_speed_rpm);  /* FW-01: speed gate из GUI */
    /* Open-loop I-f разгон до заданной скорости (электрические об/мин) */
    VF_Init(&vf, speed_ref_rpm * pole_pairs, FOC_VF_RAMP_MS);
    foc_state = FOC_STATE_STARTUP;
    foc_running = 1;
    /* Ревью VFS-02/TEST-03: сброс handoff-gate и причины при каждом запуске. */
    FocHandoffGate_Init(&handoff_gate);
    startup_fail_reason = FOC_STARTUP_OK;
    /* Ревью foc.c п.17: инициализировать фильтр Vbus при КАЖДОМ старте,
     * а не только при первом FOC_Run (иначе при повторном запуске фильтр
     * стартует со старого значения после остановки). */
    vbus_filtered_mv = ADC_GetVbus_mV();
    if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    ADC_InjectedStart();           /* ADC ждёт TIM1_TRGO */
    PWM_Enable();                  /* CEN → TRGO → ADC → ISR → FOC_Run */
}

void FOC_Stop(void) {
    foc_running = 0;
    meas_speed_erpm = 0;
    /* Ревью VFS-07: I-f стартер не остаётся логически активным после
     * fault/stop (иначе состояние vf рассинхронизировано с foc_running,
     * а повторный FOC_Start выглядит как «продолжение» старой рампы). */
    vf.complete = 0;
    vf.current_speed = 0;
    PWM_Disable();
    /* Ревью MAIN-06: ADC_InjectedStop уже вызван внутри PWM_Disable()
     * (JADSTP + сброс флагов) — повторный вызов здесь не нужен. */
}

void FOC_Run(void) {
    if(!foc_running) return;

    /* 1. Чтение токов АЦП (данные из injected group JDR1-4, обновлены в ADC ISR).
     * Ires — трансформаторный датчик суммы токов A+B+C (PA6 = ADC2_IN3),
     * теперь с правильным масштабом 100 мВ/А.
     * В OEW сумма фазных токов не равна нулю, поэтому восстанавливаем
     * третий ток: iw = Ires − iu − iv, и применяем полное 3-датчиковое
     * преобразование Кларка. */
    int32_t i1_ma = ADC_GetI1_mA();
    int32_t i2_ma = ADC_GetI2_mA();
    int32_t ires_ma = ADC_GetIres_mA();
    int32_t iw_ma = ires_ma - i1_ma - i2_ma;

    /* Приведение к внутреннему масштабу (Q15) — делим на 100.
     * Полный диапазон ±26А → ±26000 мА → ±260 в Q15. */
    int32_t iu = i1_ma / 100;
    int32_t iv = i2_ma / 100;
    /* iw = Ires − Iu − Iv (реальный третий фазный ток). Используется
     * одновременно в 3-датчиковом Clarke и в dead-time компенсации. */
    int32_t iw = iw_ma / 100;

    /* 1b. Фильтр Vbus: IIR 1-го порядка, 1/16 нового значения.
     * Все алгоритмы ниже получают отфильтрованное Vbus. */
    {
        int32_t vbus_raw = ADC_GetVbus_mV();
        if(vbus_raw < 1000) vbus_raw = 1000;
        if(vbus_filtered_mv == 0) vbus_filtered_mv = vbus_raw;
        vbus_filtered_mv = (vbus_filtered_mv * 15 + vbus_raw) / 16;
        if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    }

    /* 1c. Инвертерные потери в фазе (Q15: ±32768 ↔ ±Vdc).
     * Dead-time: V_err = 2·(t_dt/Tsw)·Vdc, для OEW оба инвертора.
     * Падение на ключах: V_drop = R·I + Vf, знак по току.
     * Плавный sign: i/(|i|+I0) — без скачка в нуле. */
    int32_t vdt_mag = 0;
    uint32_t dt_ns = PWM_GetDeadTime_ns();
    if(dt_ns > 0) {
        /* 2·dt_ns·32768 / (Ts_us·1000) — Q15.
         * Ревью Grok п.5: Ts зашит константой. Это ОСОЗНАННО — FOC-цикл
         * и PWM жёстко связаны (5 кГц, TRGO от TIM1). При смене частоты
         * PWM обновить FOC_DEFAULT_TS_US (и BEMF/PLL/PI-гейны тоже). */
        vdt_mag = (int32_t)((2LL * (int64_t)dt_ns * 32768LL) /
                            ((int64_t)FOC_DEFAULT_TS_US * 1000LL));
    }
    int32_t vbus_i = vbus_filtered_mv;
    int32_t vcomp_u = 0, vcomp_v = 0, vcomp_w = 0;
    if(vdt_mag > 0 || FOC_INV_R_MOHM > 0 || FOC_INV_VF_MV > 0) {
        int32_t iu_ma = foc_abs(iu) * 100;
        int32_t iv_ma = foc_abs(iv) * 100;
        int32_t iw_abs_ma = foc_abs(iw) * 100;
        /* int64 — страховка: iu в mA/100, переполнение требует |I| > 6.5 кА
         * (нереально), но каст бесплатный и защищает от мусора АЦП. */
        int32_t sgn_u = (int32_t)(((int64_t)iu * 32768) / (foc_abs(iu) + FOC_DTCOMP_I0));
        int32_t sgn_v = (int32_t)(((int64_t)iv * 32768) / (foc_abs(iv) + FOC_DTCOMP_I0));
        int32_t sgn_w = (int32_t)(((int64_t)iw * 32768) / (foc_abs(iw) + FOC_DTCOMP_I0));
        /* Ревью Grok п.1 (КРИТИЧНО): R[mΩ]×I[mA] = μV, а Vf[mV] — уже мВ.
         * Раньше слагаемые складывались в разных единицах → Vf-часть
         * занижалась в 1000 раз (при I=0, Vbus=24В: давало ~2 Q15 вместо
         * 2048). Теперь: vdrop_mv = R·I/1000 + Vf, всё в мВ. */
        int32_t vdrop_u = (int32_t)((((int64_t)FOC_INV_R_MOHM * iu_ma / 1000LL + FOC_INV_VF_MV) * 32768LL) /
                                    ((int64_t)vbus_i));
        int32_t vdrop_v = (int32_t)((((int64_t)FOC_INV_R_MOHM * iv_ma / 1000LL + FOC_INV_VF_MV) * 32768LL) /
                                    ((int64_t)vbus_i));
        int32_t vdrop_w = (int32_t)((((int64_t)FOC_INV_R_MOHM * iw_abs_ma / 1000LL + FOC_INV_VF_MV) * 32768LL) /
                                    ((int64_t)vbus_i));
        vcomp_u = ((int64_t)vdt_mag * iu) / (foc_abs(iu) + FOC_DTCOMP_I0) +
                  ((int64_t)vdrop_u * sgn_u) / 32768;
        vcomp_v = ((int64_t)vdt_mag * iv) / (foc_abs(iv) + FOC_DTCOMP_I0) +
                  ((int64_t)vdrop_v * sgn_v) / 32768;
        vcomp_w = ((int64_t)vdt_mag * iw) / (foc_abs(iw) + FOC_DTCOMP_I0) +
                  ((int64_t)vdrop_w * sgn_w) / 32768;
    }
    AlphaBeta vcomp_ab;
    vcomp_ab.alpha = (int32_t)(((int64_t)2*vcomp_u - vcomp_v - vcomp_w) / 3);
    vcomp_ab.beta  = (int32_t)(((int64_t)(vcomp_v - vcomp_w) * FOC_INV_SQRT3_Q15) >> 15);

    /* 2. Clarke: Iα, Iβ (3-датчиковая формула) — в единицах mA/100 (Q15-like).
     * Для observer нужна отдельная Clarke в mA — 100× выше разрешение производной. */
    AlphaBeta ab = Clarke_Transform(iu, iv, iw);
    /* Clarke в mA для observer: Iα_ma, Iβ_ma */
    int32_t ia_ma = i1_ma;
    int32_t ib_ma = i2_ma;
    int32_t iw_ma_clarke = ires_ma - i1_ma - i2_ma;
    AlphaBeta ab_ma = Clarke_Transform(ia_ma, ib_ma, iw_ma_clarke);

    /* 3. BEMF Observer — получает Vα, Vβ ПРОШЛОГО цикла (predictive),
     * и токи Iα, Iβ в мА (не mA/100) для высокоразрешающей производной. */
    BEMF_Update(&observer, prev_valpha, prev_vbeta, ab_ma.alpha, ab_ma.beta);

    /* 4. PLL — диагностический: сравнение encoder vs observer.
     * PLL больше НЕ источник угла для Park. Угол = phase accumulator. */
    PLL_Update(&pll, observer.emf_alpha, observer.emf_beta);
    w_pll_raw_q31 = PLL_GetSpeed(&pll);
    if(!speed_filter_init) {
        w_pll_filtered_q31 = w_pll_raw_q31;
        speed_filter_init = 1;
    }
    w_pll_filtered_q31 = (w_pll_filtered_q31 * 15 + w_pll_raw_q31) / 16;
    if(foc_abs(w_pll_raw_q31) < (FOC_OMEGA_PER_ERPM / 2) &&
       foc_abs(w_pll_filtered_q31) < (FOC_OMEGA_PER_ERPM / 2)) {
        w_pll_filtered_q31 = 0;
    }

    /* 4b. Encoder speed filtering (для перехода и RUN). */
    int32_t enc_rpm_raw = ENC_GetSpeed_rpm();
    if(!enc_filter_init) {
        enc_speed_rpm_filtered = enc_rpm_raw;
        enc_filter_init = 1;
    }
    enc_speed_rpm_filtered += (enc_rpm_raw - enc_speed_rpm_filtered) >> FOC_ENC_FILTER_SHIFT;

    /* 5. Выбор угла и задания тока:
     * STARTUP — open-loop V/f (как раньше).
     * RUN — encoder speed + slip model → phase accumulator → θe. */
    int32_t theta;
    int32_t iq_ref;
    int32_t id_target;
    if(foc_state == FOC_STATE_STARTUP) {
        VF_Update(&vf);
        theta = VF_GetTheta(&vf);
        /* Ревью Grok п.4 (ВЫСОКАЯ): meas_speed_erpm ДОЛЖНА быть ИЗМЕРЕННОЙ
         * всегда. Раньше в STARTUP сюда попадала командная V/f скорость
         * (VF_GetSpeed) — GUI/логи показывали «измеренную» 1000 rpm при
         * реально стоящем моторе. Теперь: измеренная encoder-скорость. */
        meas_speed_erpm = enc_speed_rpm_filtered * (int32_t)pole_pairs;
        iq_ref = (speed_ref_rpm >= 0) ? FOC_STARTUP_IQ : -FOC_STARTUP_IQ;
        id_target = FOC_STARTUP_ID;

        /* Переход V/f → encoder FOC (для АД):
         * 1. V/f рампа завершена
         * 2. |EMF| > threshold (observer видит реальную ЭДС)
         * 3. |encoder speed| > минимум (двигатель вращается)
         * 4. Направление encoder совпадает с V/f
         * 5. Скачок скорости encoder < 200 rpm (стабильность) */
        int32_t vf_erpm = VF_GetSpeed(&vf);
        int32_t enc_erpm = enc_speed_rpm_filtered * pole_pairs;
        int32_t speed_mismatch = foc_abs(vf_erpm - enc_erpm);
        int32_t enc_jerk = foc_abs(enc_rpm_raw - enc_speed_rpm_prev);
        int8_t dir_ok = ((vf_erpm > 0 && enc_rpm_raw > 0) ||
                         (vf_erpm < 0 && enc_rpm_raw < 0));
        /* Ревью OBS-02/06 + TEST-03: переход — через тестируемую state
         * machine FocHandoffGate (foc_handoff_gate.c): required_consecutive
         * циклов устойчивой EMF (одиночный транзиент/шум не запускает FOC),
         * общий watchdog от старта + post-complete timeout. Валидность
         * observer (glitch/saturation) входит как emf=0 → gate провалит
         * emf_min. */
        FocHandoffInput handoff_in;
        handoff_in.vf_complete = VF_IsComplete(&vf);
        handoff_in.vf_erpm = vf_erpm;
        handoff_in.encoder_raw_rpm = enc_rpm_raw;
        handoff_in.encoder_filtered_rpm = enc_speed_rpm_filtered;
        handoff_in.pole_pairs = pole_pairs;
        handoff_in.emf_magnitude = BEMF_IsValid(&observer) ? BEMF_GetMagnitude(&observer) : 0;
        handoff_in.encoder_jerk_rpm = enc_jerk;
        handoff_in.previous_id_internal = prev_dq_d;

        switch (FocHandoffGate_Update(&handoff_gate, &handoff_cfg, &handoff_in)) {
        case FOC_HANDOFF_READY: {
            /* Бесшовный переход: phase accumulator = V/f theta.
             * Δθ сразу из модели АД: rotor_dt + slip_dt (не integer Hz).
             * Угол сохраняем от V/f, скорость фазы — из encoder + slip. */
            int32_t p = pole_pairs; if(p < 1) p = 1;
            /* Ревью FOC-05: encoder-скорость клэмпнута к механическому
             * пределу (скачок PWM AS5048A на пол-оборота формально даёт
             * ~27 000 rpm — умножение на p·FOC_OMEGA_PER_ERPM переполнило
             * бы int32_t ДО clamp). Считаем в int64_t. */
            int32_t enc_spd0 = CLAMP(enc_speed_rpm_filtered, -FOC_GetMaxSpeedRPM(), FOC_GetMaxSpeedRPM());
            /* VFS-03: точный Δθ (2^32/300000) — без дрейфа константы 14317. */
            int64_t rotor_dt0 = foc_delta_theta((int64_t)enc_spd0 * p);
            int32_t tr0 = (int32_t)g_motor_params.Tr_rotor_us;
            if(tr0 < 1000) tr0 = 100000;
            int32_t slip_dt0 = 0;
            if(foc_abs(prev_dq_d) >= FOC_MIN_ID_SLIP) {
                slip_dt0 = (int32_t)(((int64_t)FOC_SLIP_2PI_INV * FOC_PHASE_PER_HZ * prev_dq_q) /
                                      ((int64_t)tr0 * prev_dq_d));
                slip_dt0 = CLAMP(slip_dt0, -FOC_MAX_SLIP_DT, FOC_MAX_SLIP_DT);
            }
            int32_t total_dt0 = (int32_t)CLAMP(rotor_dt0 + slip_dt0, -FOC_MAX_FE_DT, FOC_MAX_FE_DT);
            f_slip_hz = slip_dt0 / FOC_PHASE_PER_HZ;   /* telemetry */
            f_e_hz = total_dt0 / FOC_PHASE_PER_HZ;      /* telemetry */
            enc_phase_accum = (uint32_t)theta;
            enc_delta_theta = total_dt0;
            foc_state = FOC_STATE_RUN;
            break;
        }
        case FOC_HANDOFF_TIMEOUT:
            /* Ревью VFS-02: bounded handoff — watchdog сработал → стоп.
             * FOC_Stop() из ISR безопасен: PWM_Disable снимает CEN → TRGO
             * исчезает → ISR больше не вызывается. Повторный старт —
             * командой FOC_Start (после PROTECT_Clear при fault). */
            startup_fail_reason = FOC_STARTUP_FAIL_TIMEOUT;
            (void)UART_TrySendStr("FOC: STARTUP FAIL: handoff timeout, PWM off\r\n");
            FOC_Stop();
            return;
        default:
            /* PENDING. Ревью VFS-04: диагностика причины — только после
             * завершения рампы (во время рампы невыполнение условий
             * нормально). Причина — в телеметрии @FOC FAIL=. */
            if(handoff_in.vf_complete) {
                if(foc_abs(enc_speed_rpm_filtered) <= FOC_ENC_MIN_RPM)
                    startup_fail_reason = FOC_STARTUP_FAIL_NO_ROTATION;
                else if(!dir_ok)
                    startup_fail_reason = FOC_STARTUP_FAIL_ENC_DIRECTION;
                else if(!BEMF_IsValid(&observer) ||
                        BEMF_GetMagnitude(&observer) <= FOC_EMF_MIN_THRESHOLD)
                    startup_fail_reason = FOC_STARTUP_FAIL_EMF_INVALID;
                else if(speed_mismatch >= (foc_abs(vf_erpm) / 3))
                    startup_fail_reason = FOC_STARTUP_FAIL_SPEED_MISMATCH;
                else
                    startup_fail_reason = FOC_STARTUP_FAIL_UNSTABLE;  /* jerk / Id */
            }
            break;
        }
        enc_speed_rpm_prev = enc_rpm_raw;
    } else {
        /* FOC_STATE_RUN: encoder + slip model → phase accumulator → θe.
         *
         * Архитектура для АД:
         *   encoder → n_mech → rotor_dt = rpm·p·FOC_OMEGA_PER_ERPM
         *   Iq/Id → slip_dt = (Iq/Id)·FOC_SLIP_2PI_INV·FOC_PHASE_PER_HZ/Tr
         *   delta_theta = rotor_dt + slip_dt (без integer Hz квантования)
         *   θe(k+1) = θe(k) + delta_theta
         *
         * Slip из Id/Iq предыдущего цикла (Park ещё не выполнен).
         * Задержка 1 цикл (200 мкс) пренебрежимо мала для slip. */
        int32_t p = pole_pairs;
        if(p < 1) p = 1;

        /* Ревью FOC-05: encoder-скорость клэмпнута к механическому пределу
         * (см. переходный блок), приращение — в int64_t до clamp. */
        int32_t enc_spd = CLAMP(enc_speed_rpm_filtered, -FOC_GetMaxSpeedRPM(), FOC_GetMaxSpeedRPM());

        /* Rotor electrical delta_theta: rpm·p·Δθ_per_erpm (VFS-03: точная
         * формула 2^32/300000, не константа 14317 — дрейф фазы в RUN). */
        int64_t rotor_dt = foc_delta_theta((int64_t)enc_spd * p);

        /* Slip delta_theta: (1/(2π·Tr))·(Iq/Id)·FOC_PHASE_PER_HZ
         * Tr из автотюнинга; fallback 100 мс если неизвестен.
         * Не вычисляем slip при малом |Id| — поток недостаточен. */
        int32_t tr_us = (int32_t)g_motor_params.Tr_rotor_us;
        /* Ревью foc.c п.7/п.7: silent fallback 100 мс опасен для закрытого
         * FOC (slip-ошибка в разы при реальном Tr≠100мс). Не запрещаем RUN
         * (иначе мотор вообще не поедет до автотюна), но явно предупреждаем
         * ОДИН раз через DBG_STR — диагностика видит, что Tr не измерен. */
        if(tr_us < 1000) {
            if(!tr_fallback_warned) {
                tr_fallback_warned = 1;
                /* Ревью UART-01: НЕ блокирующий UART_SendStr из FOC ISR
                 * (priority 0) — при полном TX-ring это deadlock: продвигать
                 * tx_tail может только USART2_IRQHandler (priority 2), который
                 * НЕ вытесняет ADC. TrySend: дроп пакета при полном буфере. */
                (void)UART_TrySendStr("WARN: Tr not measured, using 100ms fallback\r\n");
            }
            tr_us = 100000;
        }
        int32_t slip_dt = 0;
        if(foc_abs(prev_dq_d) >= FOC_MIN_ID_SLIP) {
            slip_dt = (int32_t)(((int64_t)FOC_SLIP_2PI_INV * FOC_PHASE_PER_HZ * prev_dq_q) /
                                 ((int64_t)tr_us * prev_dq_d));
            slip_dt = CLAMP(slip_dt, -FOC_MAX_SLIP_DT, FOC_MAX_SLIP_DT);
        }
        f_slip_hz = slip_dt / FOC_PHASE_PER_HZ;   /* telemetry (integer Hz) */

        /* Total electrical delta_theta with clamp (int64 до clamp — FOC-05) */
        int32_t delta_theta = (int32_t)CLAMP(rotor_dt + slip_dt, -FOC_MAX_FE_DT, FOC_MAX_FE_DT);
        f_e_hz = delta_theta / FOC_PHASE_PER_HZ;   /* telemetry (integer Hz) */

        /* Phase accumulator: θe += delta_theta (full-turn uint32 wrap-around) */
        enc_phase_accum += (uint32_t)delta_theta;
        enc_delta_theta = delta_theta;

        theta = (int32_t)enc_phase_accum;
        meas_speed_erpm = enc_spd * pole_pairs;

        /* Speed PI: error в q31/256 (совместимость с существующими gains) */
        if(iq_ref_ma != 0) {
            iq_ref = CLAMP(iq_ref_ma / 100, -FOC_IQ_MAX, FOC_IQ_MAX);
        } else {
            int64_t omega_ref = (int64_t)speed_ref_rpm * p * FOC_OMEGA_PER_ERPM;
            int64_t omega_enc = (int64_t)enc_spd * p * FOC_OMEGA_PER_ERPM;
            int32_t spd_err = (omega_ref - omega_enc) >> FOC_SPD_ERR_SHIFT;
            iq_ref = PI_Update(&pi_spd, spd_err);
            iq_ref = CLAMP(iq_ref, -FOC_IQ_MAX, FOC_IQ_MAX);
            /* Ревью FW-03: НЕ ограничиваем iq_ref через FW_GetIqLimit —
             * это остаток НАПРЯЖЕНИЯ q (sqrt(Vmax²−Vd²)) в Q15, а не ток;
             * единицы (Q15 vs 0.1А) несовместимы, и VM УЖЕ ограничивает
             * Vq = ±sqrt(Vmax²−Vd²) (flux priority, anti-windup). */
        }
        id_target = id_ref_ma / 100;
        enc_speed_rpm_prev = enc_rpm_raw;
    }
    meas_theta_q31 = (uint32_t)theta;

    /* 6. Park: Iα, Iβ → Id, Iq + потери инвертера → dq */
    DQ dq = Park_Transform(ab.alpha, ab.beta, theta);
    prev_dq_d = dq.d;
    prev_dq_q = dq.q;
    DQ vcomp_dq = Park_Transform(vcomp_ab.alpha, vcomp_ab.beta, theta);

    /* 7. Flux Weakening: по limit_scale прошлого цикла VM.
     * FW получает степень насыщения от VM — пропорциональное ослабление поля.
     * Задержка в один цикл несущественна при частоте ШИМ. */
    /* id_base (0.1А) → мА: единицы id_fw (1 код = 1 мА) — ревью FW-02. */
    FW_Update(&fw, prev_vd, prev_vq, VM_GetLimitScale(&vm), id_target * 100,
              enc_speed_rpm_filtered);   /* FW-01: speed gate по измеренной скорости */
    int32_t id_add = FW_GetIdAdd(&fw);

    /* Ревью FOC-03: круг тока — суммарный вектор (Id + FW-добавка, Iq) не
     * выше FOC_I_MAX_MA (10 А — лимит модуля): Iq_limit = sqrt(Imax² − Id²).
     * VM ограничивает НАПРЯЖЕНИЕ, этот контур ограничивает ТОК. */
    int32_t id_total = id_target + id_add / 100;
    if (id_total < 0) id_total = 0;
    {
        int32_t imax = FOC_I_MAX_MA / 100;   /* 0.1А-единицы */
        if (id_total > imax) id_total = imax;
        int32_t iq_lim = 0;
        int32_t iq_lim_sq = imax * imax - id_total * id_total;
        if (iq_lim_sq <= 0) {
            iq_lim = 0;
        } else if (iq_lim_sq >= imax * imax) {
            iq_lim = imax;
        } else {
            int32_t x_q31 = (int32_t)(((int64_t)iq_lim_sq << 31) / ((int64_t)imax * imax));
            if (x_q31 > 0x7FFFFFFF) x_q31 = 0x7FFFFFFF;
            iq_lim = (int32_t)(((int64_t)CORDIC_Sqrt(x_q31) * imax) >> 31);
        }
        iq_ref = CLAMP(iq_ref, -iq_lim, iq_lim);
    }

    /* 8. PI регуляторы по току */
    /* id_add в мА → 0.1А (id_add/100): контур токов работает в 0.1А,
     * раньше мА-добавка смешивалась с 0.1А-ошибкой напрямую (в 100 раз). */
    int32_t vd = PI_Update(&pi_d, (id_target + id_add / 100) - dq.d);
    int32_t vq = PI_Update(&pi_q, iq_ref - dq.q);

    /* 8b. Компенсация перекрёстных связей dq (Антиучебник, 7.7.1, стр.186-187).
     * Уравнения статора АД в dq (ориентация по ψr):
     *   Vd = Rs·Id + Lσ·dId/dt − ω·Lσ·Iq
     *   Vq = Rs·Iq + Lσ·dIq/dt + ω·Lσ·Id + ω·(Lm/Lr)·ψr
     * ПИ отрабатывает первые члены; перекрёстные −ω·Lσ·Iq / +ω·Lσ·Id
     * компенсируем напрямую (член ЭДС ротора остаётся на ПИ/observer):
     *   vd += +ω·Lσ·Iq
     *   vq += −ω·Lσ·Id
     * Масштабы: ω = PLL omega_q31 (Δθ q31 за цикл); I = dq в мА/100;
     * Lσ в мкГн; результат в Q15 (32768 ↔ Vbus).
     * E_Q15 = ω_q31·Lσ_uH·I_int·2π·1e-6·0.1·32768 / (2^31·Ts·Vbus_В)
     *        = ω_q31·Lσ_uH·I_int / (Vbus_В·2.086e7)  — int64, защита от переполнения.
     * Используем отфильтрованные Vbus и ω; при |ω| < 3 эл. об/мин
     * компенсацию отключаем — на нулевой скорости она только добавляет шум. */
    {
        /* Decoupling: используем encoder delta_theta (from phase accumulator).
         * PLL speed — только для диагностики. */
        int32_t w_q31 = enc_delta_theta;
        int32_t lsigma = foc_lsigma_uH;            /* Lσ статора (Lσs), мкГн */
        int32_t vbus_mv = vbus_filtered_mv;
        if(vbus_mv < 1000) vbus_mv = 1000;         /* защита от деления на 0 */
        int32_t kden = (int32_t)((int64_t)vbus_mv * FOC_DECOUPLE_KDEN);
        if(lsigma > 0 && kden > 0 &&
           foc_abs(w_q31) >= (3 * FOC_OMEGA_PER_ERPM)) {
            int32_t e_d = (int32_t)(((int64_t)w_q31 * lsigma * dq.q) / kden);
            int32_t e_q = (int32_t)(((int64_t)w_q31 * lsigma * dq.d) / kden);
            vd += e_d;    /* +ω·Lσ·Iq  → компенсирует −ω·Lσ·Iq в Vd */
            vq -= e_q;    /* −ω·Lσ·Id  → компенсирует +ω·Lσ·Id в Vq */
        }
    }

    /* Добавляем компенсацию инвертерных потерь перед VM: VM должен
     * ограничивать тот вектор, который реально будет выдан в ШИМ. */
    vd += vcomp_dq.d;
    vq += vcomp_dq.q;

    /* 9. Voltage Manager: ограничение модуля Vdq + anti-windup.
     * VM работает в Q15, не знает про PI/FW — чистая математика.
     * Vmax обновляется ежециклово; пока это фиксированная доля Vdc. */
    /* Vmax_Q15 = 90% × 32768 = 29490 — это ДОЛЯ от Vdc, не абсолютное
     * напряжение, поэтому не зависит от значения Vbus. Если Vbus меняется,
 * observer/FW используют отфильтрованное Vbus (обновляется ниже).
 * FW_SetVmaxQ15 вызывается каждый цикл для синхронизации с VM —
 * даже если значение не меняется, это защищает от рассинхрона
 * при будущих изменениях Vmax (overmodulation и т.д.). */
    VM_SetVmax(&vm, FOC_VM_VMAX_Q15);
    FW_SetVmaxQ15(&fw, VM_GetVmax(&vm));
    VM_Update(&vm, vd, vq);
    if (vm.saturated) {
        /* Anti-windup через PI_BackCalculation с настраиваемым Kw.
         * PI сам решает как применять коррекцию — VM не знает о внутренностях PI. */
        PI_BackCalculation(&pi_d, vm.vd_err);
        PI_BackCalculation(&pi_q, vm.vq_err);
    }
    vd = vm.vd_out;
    vq = vm.vq_out;

    /* 10. Inverse Park + Clarke: Vd, Vq → Vα, Vβ → Vu, Vv, Vw */
    AlphaBeta vab = InvPark_Transform(vd, vq, theta);
    int32_t vu, vv, vw;
    InvClarke_Transform(vab.alpha, vab.beta, &vu, &vv, &vw);

    /* 11. OEW распределение: единая Q15 модуляция для TIM1 и TIM8 (CCR равны).
     * TIM8 в PWM mode 2 (активен при CNT>CCR): HIN_U2=1 при CNT>CCR, LIN_U2=1 при
     * CNT<CCR — ровно как HIN_U1 (TIM1 mode 1). → HIN_U1=1 ⇔ LIN_U2=1 (верх Inv1 +
     * низ Inv2 вместе) → ток по обмотке. V_phase ≈ mod·Vbus, CCR = mid + mod·mid.
     * mod = vu·98/100: запас 2% (FOC_MOD_MAX_Q15) для линейности PWM.
     * Эквивалент старой %-модели: d = 50 + vu·49/32768 ⇔ mod = 2d−1 = vu·98/100. */
    int32_t mod_u = CLAMP((vu * 98) / 100, -FOC_MOD_MAX_Q15, FOC_MOD_MAX_Q15);
    int32_t mod_v = CLAMP((vv * 98) / 100, -FOC_MOD_MAX_Q15, FOC_MOD_MAX_Q15);
    int32_t mod_w = CLAMP((vw * 98) / 100, -FOC_MOD_MAX_Q15, FOC_MOD_MAX_Q15);

    PWM_SetMod1((int16_t)mod_u, (int16_t)mod_v, (int16_t)mod_w);
    PWM_SetMod2((int16_t)mod_u, (int16_t)mod_v, (int16_t)mod_w);

    /* 12. Фактическое напряжение после CLAMP → observer и FW.
     * В Q15-модели реконструированное напряжение = mod (V_phase ≈ mod·Vbus;
     * старая %-модель давала rvu = 2·d·32768/100 − 32768 ≡ mod при d = 50+vu·49/32768).
     * rvu — «идеальное» напряжение по модуляции; физическое напряжение двигателя
     * на ε меньше из-за dead-time и падения на ключах (vcomp_u/v/w).
     * Forward Clarke (амплитудно-инвариантная):
     *   Vα = (2·Vu − Vv − Vw) / 3
     *   Vβ = (Vv − Vw) / √3 */
    int32_t rvu = mod_u;
    int32_t rvv = mod_v;
    int32_t rvw = mod_w;
    /* int64 в β: разность клэмпнутых mod ± vcomp ≤ ~70000, ×18919 ≈ 1.3e9 —
     * в пределах int32, но запас <60%; каст убирает риск при будущем
     * изменении масштабов vcomp/mod. */
    prev_valpha = (int32_t)(((int64_t)2*(rvu - vcomp_u) - (rvv - vcomp_v) - (rvw - vcomp_w)) / 3);
    prev_vbeta  = (int32_t)(((int64_t)((rvv - vcomp_v) - (rvw - vcomp_w)) * FOC_INV_SQRT3_Q15) >> 15);
    prev_vd = vd;
    prev_vq = vq;

    /* 13. Обновляем Vdc для observer — отфильтрованное Vbus.
     * (fw.vdc_mv удалён — ревью FW-04: mV-поля в FW мёртвые.) */
    observer.Vdc_mV = vbus_filtered_mv;
}
