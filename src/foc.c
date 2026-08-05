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

AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw) {
    /* Двухдатчиковая формула Кларка (амплитудно-инвариантная).
     * Предполагается iu + iv + iw = 0 (3-фазная звезда без нейтрали).
     * Iα = Iu
     * Iβ = (Iu + 2·Iv) / √3
     * √3 ≈ 1.73205; 1/√3 ≈ 0.57735 → 18919 / 32768.
     * iw не используется — принят для совместимости с 3-ф схемой. */
    AlphaBeta ab;
    (void)iw;
    ab.alpha = iu;
    ab.beta  = ((iu + 2*iv) * 18919) >> 15;
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
    int32_t sqrt3_vb = (vbeta * 56756) >> 15;
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
    /* Back-calculation anti-windup (kw=1): интегратор корректируется
     * на величину насыщения выхода — быстрый выход из windup при смене
     * знака ошибки, без «замирания» conditional integration.
     * int64 в умножении: kp/ki из модульного оптимума для АД (Ls в мГн)
     * дают kp·error до ~1e11 — int32 переполнился бы. */
    int32_t p_term = (int32_t)(((int64_t)pi->kp * error) >> 15);
    pi->integral += (int32_t)(((int64_t)pi->ki * error) >> 15);
    int32_t out = p_term + pi->integral;
    int32_t out_clamped = CLAMP(out, pi->out_min, pi->out_max);
    pi->integral += out_clamped - out;   /* kw=1: полная коррекция за цикл */
    return out_clamped;
}

/* External anti-windup: коррекция интегратора от внешнего ограничителя (VM).
 * saturation_error = out_limited - out_commanded (отрицательное при ограничении).
 * integral += Kw * saturation_error, где Kw настраивается независимо. */
void PI_BackCalculation(PIController *pi, int32_t saturation_error) {
    pi->integral += (int32_t)(((int64_t)pi->kw * saturation_error) >> 15);
}

/* FOC state */
static volatile uint8_t foc_running = 0;
static int32_t speed_ref_rpm = 0;
static int32_t id_ref_ma = 0;
static int32_t iq_ref_ma = 0;               /* ручное задание Iq (мА); 0 = контур скорости */
static volatile int32_t meas_speed_erpm = 0; /* измеренная эл. скорость, обновляется в FOC_Run */
static volatile uint32_t meas_theta_q31 = 0; /* текущий эл. угол q31 */
static int32_t pole_pairs = 4;   /* FOC_DEFAULT_POLE_PAIRS; задаётся из GUI (p=N) */

typedef enum { FOC_STATE_STARTUP = 0, FOC_STATE_RUN } FOCState;

static BEMFObserver observer;
static PLL pll;
static PIController pi_d, pi_q, pi_spd;
static FluxWeakening fw;
static VFStart vf;
static VoltageManager vm;
static FOCState foc_state = FOC_STATE_STARTUP;
static int foc_initialized = 0;

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
#define FOC_DEFAULT_PLL_KP      1000
#define FOC_DEFAULT_PLL_KI      50
#define FOC_DEFAULT_FW_KP       200
#define FOC_DEFAULT_FW_KI       10
#define FOC_DEFAULT_ID_REF_MA   0      /* Id_ref = 0 для surface-mount PMSM */
#define FOC_VM_VMAX_Q15         29490  /* 90% от 32767 — запас для линейности PWM */
#define FOC_VM_PRIORITY         VM_PRIORITY_FLUX  /* PMSM: поток приоритет */

/* Контур скорости и open-loop старт */
#define FOC_DEFAULT_POLE_PAIRS  4      /* пары полюсов по умолчанию; меняется командой p=N */
#define FOC_POLE_PAIRS_MIN      1
#define FOC_POLE_PAIRS_MAX      24
#define FOC_OMEGA_PER_ERPM      14317  /* Δθ(q31) за цикл 200 мкс на 1 эл. об/мин */
#define FOC_VF_RAMP_MS          2000   /* разгон open-loop, мс */
#define FOC_STARTUP_IQ          30     /* ~3 А в внутр. единицах (мА/100) */
#define FOC_STARTUP_ID          20     /* ~2 А намагничивания на старте */
#define FOC_SPD_KP              2000
#define FOC_SPD_KI              50
#define FOC_IQ_MAX              150    /* ±15 А — лимит задания тока */

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
    PLL_Init(&pll, FOC_DEFAULT_PLL_KP, FOC_DEFAULT_PLL_KI, FOC_DEFAULT_TS_US);
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_spd, FOC_SPD_KP, FOC_SPD_KI, FOC_IQ_MAX, -FOC_IQ_MAX);
    FW_Init(&fw, FOC_DEFAULT_VDC_MV, FOC_DEFAULT_FW_KP, FOC_DEFAULT_FW_KI);
    VM_Init(&vm, FOC_VM_VMAX_Q15, FOC_VM_PRIORITY);
    FW_SetVmaxQ15(&fw, VM_GetVmax(&vm));  /* VM — единый источник Vmax */
    speed_ref_rpm = 0;
    id_ref_ma = FOC_DEFAULT_ID_REF_MA;
    pole_pairs = FOC_DEFAULT_POLE_PAIRS;
    foc_initialized = 1;
}

#define FOC_MAX_RPM  5000
void FOC_SetSpeed(int32_t rpm) {
    if(rpm > FOC_MAX_RPM) rpm = FOC_MAX_RPM;
    if(rpm < -FOC_MAX_RPM) rpm = -FOC_MAX_RPM;
    speed_ref_rpm = rpm;
    /* Если FOC в V/f разгоне — обновляем цель рампы на лету.
     * Иначе цель, зафиксированная в FOC_Start (часто 0), останется
     * навсегда — мотор не раскрутится. */
    if(foc_running && foc_state == FOC_STATE_STARTUP) {
        /* Критическая секция: VF_Update работает в ADC ISR */
        __disable_irq();
        VF_SetTarget(&vf, speed_ref_rpm * pole_pairs);
        __enable_irq();
    }
}
int32_t FOC_GetSpeed(void) { return speed_ref_rpm; }
void FOC_SetIdRef(int32_t ma)  { id_ref_ma = ma; }
void FOC_SetIqRef(int32_t ma)  { iq_ref_ma = ma; }

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

/* ── Модульный оптимум (Антиучебник, §3.4, Табл.3.1, стр.42) ──────────
 * Контур тока АД, объект R+sL, компенсация большой постоянной Ti = L/R:
 *   Kp_phys[В/А] = L / (2·a·Tμ),   Ki_phys = Kp/Ti = Kp·R/L
 * где a — коэффициент Табл.3.1 (a=2 → перерегулирование 4.3%, время 4.7·Tμ),
 * Tμ — малые постоянные: задержка ШИМ + фильтр тока ≈ Ts.
 * Пересчёт в единицы кода: p_term = (kp·error)>>15, error в мА/100,
 * u_В = p_term·Vdc/32768² → kp = Kp_phys·0.1·2^30/Vdc_В:
 *   kp = L_uH·1.07374e11 / (2·a·Tμ_us·Vdc_mV)
 *   ki = kp·Ts_us·R_mOhm / (L_uH·1000)          (дискретный интегратор)
 * int64 — Ls асинхронника до 100 мГн даёт kp·error ~1e11. */
#define FOC_PI_OPTIMUM_A        2      /* Табл.3.1: 4.3% перерегулирование */
void FOC_ComputePIGains(int32_t r_mohm, int32_t l_uh, int32_t vdc_mv,
                        int32_t *kp_out, int32_t *ki_out) {
    int32_t kp = 0, ki = 0;
    if(l_uh >= 1 && r_mohm >= 1 && vdc_mv >= 1000) {
        int64_t num = (int64_t)l_uh * 107374182400LL;   /* L_uH·2^30·0.1·1e6 */
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

void FOC_Start(void) {
    if(foc_running) return;
    if(!foc_initialized) FOC_Init();
    /* Калибровка нуля токов — непосредственно перед запуском,
     * пока инвертор выключен (токи истинно нулевые). */
    ADC_CalibrateOffsets();
    /* Сброс состояний перед каждым запуском */
    BEMF_Init(&observer, motor_R_mOhm, motor_L_uH, FOC_DEFAULT_TS_US, ADC_GetVbus_mV());
    PLL_Init(&pll, FOC_DEFAULT_PLL_KP, FOC_DEFAULT_PLL_KI, FOC_DEFAULT_TS_US);
    PI_Init(&pi_d, motor_Kp, motor_Ki, 32767, -32768);
    PI_Init(&pi_q, motor_Kp, motor_Ki, 32767, -32768);
    pi_d.integral = 0;
    pi_q.integral = 0;
    pi_spd.integral = 0;
    prev_valpha = prev_vbeta = 0;
    prev_vd = prev_vq = 0;
    /* Сброс состояния FW (интегратор, флаг) при каждом запуске */
    FW_Init(&fw, ADC_GetVbus_mV(), FOC_DEFAULT_FW_KP, FOC_DEFAULT_FW_KI);
    VM_Init(&vm, FOC_VM_VMAX_Q15, FOC_VM_PRIORITY);
    FW_SetVmaxQ15(&fw, VM_GetVmax(&vm));  /* VM — единый источник Vmax */
    /* Open-loop I-f разгон до заданной скорости (электрические об/мин) */
    VF_Init(&vf, speed_ref_rpm * pole_pairs, FOC_VF_RAMP_MS);
    foc_state = FOC_STATE_STARTUP;
    foc_running = 1;
    ADC_InjectedStart();           /* ADC ждёт TIM1_TRGO */
    PWM_Enable();                  /* CEN → TRGO → ADC → ISR → FOC_Run */
}

void FOC_Stop(void) {
    foc_running = 0;
    meas_speed_erpm = 0;
    PWM_Disable();
    ADC_InjectedStop();
}

void FOC_Run(void) {
    if(!foc_running) return;

    /* 1. Чтение токов АЦП (данные из injected group JDR1-4, обновлены в ADC ISR) */
    int32_t i1_ma = ADC_GetI1_mA();
    int32_t i2_ma = ADC_GetI2_mA();
    /* Ires — трансформаторный датчик суммы токов A+B+C (не фазный ток).
     * ВНИМАНИЕ (OEW): Clarke_Transform() использует 2-датчиковую формулу
     * (iu, iv), которая математически подразумевает iu+iv+iw=0. Это
     * условие гарантировано только при общей звезде/треугольнике (КЗТ в
     * узле нейтрали). В OEW (Open-End Winding, раздельное питание каждой
     * обмотки от двух инверторов, нет общей нейтрали) сумма фазных токов
     * НЕ обязана быть нулевой — Ires может быть ненулевым, и текущая
     * формула Clarke является приближением, а не точным преобразованием.
     * Точный 3-датчиковый Clarke потребовал бы iw = Ires_sum - iu - iv.
     * Пока не пересмотрено и не проверено на реальном моторе — оставлено
     * как есть; Ires читается только для телеметрии/защиты. */
    (void)ADC_GetIres_mA();  /* читаем для телеметрии/защиты, но не для Clarke */
    /* Приведение к внутреннему масштабу (Q15) — делим на 100.
     * Полный диапазон ±26А → ±26000 мА → ±260 в Q15. */
    int32_t iu = i1_ma / 100;
    int32_t iv = i2_ma / 100;

    /* 2. Clarke: Iα, Iβ (2-ф формула, iw не нужен) */
    AlphaBeta ab = Clarke_Transform(iu, iv, 0);

    /* 3. BEMF Observer — получает Vα, Vβ ПРОШЛОГО цикла (predictive) */
    BEMF_Update(&observer, prev_valpha, prev_vbeta, ab.alpha, ab.beta);

    /* 4. PLL: Eα, Eβ → θ (работает и во время старта — сходится в фоне) */
    PLL_Update(&pll, observer.emf_alpha, observer.emf_beta);

    /* 5. Выбор угла и задания тока: open-loop V/f на старте, затем PLL +
     * контур скорости (PI по ошибке эл. скорости → Iq_ref) */
    int32_t theta;
    int32_t iq_ref;
    int32_t id_target;
    if(foc_state == FOC_STATE_STARTUP) {
        VF_Update(&vf);
        theta = VF_GetTheta(&vf);
        meas_speed_erpm = VF_GetSpeed(&vf);
        iq_ref = (speed_ref_rpm >= 0) ? FOC_STARTUP_IQ : -FOC_STARTUP_IQ;
        id_target = FOC_STARTUP_ID;
        if(VF_IsComplete(&vf) && BEMF_GetMagnitude(&observer) > FOC_EMF_MIN_THRESHOLD) {
            /* Бесшовный переход: предзагружаем PLL углом и скоростью V/f.
             * Проверяем, что observer уже даёт значимый EMF — иначе скачок угла. */
            PLL_Preset(&pll, theta, VF_GetSpeed(&vf) * FOC_OMEGA_PER_ERPM);
            foc_state = FOC_STATE_RUN;
        }
    } else {
        theta = PLL_GetTheta(&pll);
        meas_speed_erpm = PLL_GetSpeed(&pll) / FOC_OMEGA_PER_ERPM;
        if(iq_ref_ma != 0) {
            /* Ручное задание Iq (torque mode): мА → внутр. единицы мА/100 */
            iq_ref = CLAMP(iq_ref_ma / 100, -FOC_IQ_MAX, FOC_IQ_MAX);
        } else {
            int32_t omega_ref = speed_ref_rpm * pole_pairs * FOC_OMEGA_PER_ERPM;
            int32_t spd_err = (omega_ref - PLL_GetSpeed(&pll)) >> 8;
            iq_ref = PI_Update(&pi_spd, spd_err);
            /* Ослабление поля: ограничение Iq при активном FW */
            if(FW_IsActive(&fw)) {
                int32_t iq_lim = FW_GetIqLimit(&fw);
                if(iq_ref > iq_lim) iq_ref = iq_lim;
                if(iq_ref < -iq_lim) iq_ref = -iq_lim;
            }
        }
        id_target = id_ref_ma / 100;   /* мА → внутр. единицы мА/100 */
    }
    meas_theta_q31 = (uint32_t)theta;

    /* 6. Park: Iα, Iβ → Id, Iq */
    DQ dq = Park_Transform(ab.alpha, ab.beta, theta);

    /* 7. Flux Weakening: по limit_scale прошлого цикла VM.
     * FW получает степень насыщения от VM — пропорциональное ослабление поля.
     * Задержка в один цикл несущественна при частоте ШИМ. */
    FW_Update(&fw, prev_vd, prev_vq, VM_GetLimitScale(&vm));
    int32_t id_add = FW_GetIdAdd(&fw);

    /* 8. PI регуляторы по току */
    int32_t vd = PI_Update(&pi_d, (id_target + id_add) - dq.d);
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
     *        = ω_q31·Lσ_uH·I_int / (Vbus_В·2.086e7)  — int64, защита от переполнения. */
    {
        int32_t w_q31 = PLL_GetSpeed(&pll);
        int32_t lsigma = foc_lsigma_uH;            /* Lσ статора (Lσs), мкГн */
        int32_t vbus_mv = ADC_GetVbus_mV();
        if(vbus_mv < 1000) vbus_mv = 1000;         /* защита от деления на 0 */
        /* Vbus_В·2.086e7 = vbus_mv·20860 */
        int32_t kden = (int32_t)((int64_t)vbus_mv * 20860LL);
        if(lsigma > 0 && kden > 0) {
            int32_t e_d = (int32_t)(((int64_t)w_q31 * lsigma * dq.q) / kden);
            int32_t e_q = (int32_t)(((int64_t)w_q31 * lsigma * dq.d) / kden);
            vd += e_d;    /* +ω·Lσ·Iq  → компенсирует −ω·Lσ·Iq в Vd */
            vq -= e_q;    /* −ω·Lσ·Id  → компенсирует +ω·Lσ·Id в Vq */
        }
    }

    /* 9. Voltage Manager: ограничение модуля Vdq + anti-windup.
     * VM работает в Q15, не знает про PI/FW — чистая математика.
     * Flux priority: Vd сохраняется, Vq ограничивается по кругу.
     * Anti-windup: vd_err/vq_err передаются в PI через integral correction. */
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

    /* 11. OEW распределение: V_inv1 = Vdc/2 + V/2, V_inv2 = Vdc/2 + V/2 — ОДИНАКОВО!
     * TIM8 в PWM mode 2 (активен при CNT>CCR): HIN_U2=1 при CNT>CCR, LIN_U2=1 при
     * CNT<CCR — ровно как HIN_U1 (TIM1 mode 1). → HIN_U1=1 ⇔ LIN_U2=1 (верх Inv1 +
     * низ Inv2 вместе) → ток по обмотке. Среднее V_U = (2·CCR−ARR)·VBUS/ARR.
     * 49 вместо 50 — запас 1% для линейности PWM (не упираться в 0/100%).
     * Коэффициент 98/100 автоматически учитывается в шаге 12. */
    int32_t dc_bias = 50;
    int32_t half_vu = (vu * 49) / 32768;
    int32_t half_vv = (vv * 49) / 32768;
    int32_t half_vw = (vw * 49) / 32768;
    int32_t d1u = CLAMP(dc_bias + half_vu, 1, 98);
    int32_t d2u = CLAMP(dc_bias + half_vu, 1, 98);
    int32_t d1v = CLAMP(dc_bias + half_vv, 1, 98);
    int32_t d2v = CLAMP(dc_bias + half_vv, 1, 98);
    int32_t d1w = CLAMP(dc_bias + half_vw, 1, 98);
    int32_t d2w = CLAMP(dc_bias + half_vw, 1, 98);
    PWM_SetDuty1((uint16_t)d1u, (uint16_t)d1v, (uint16_t)d1w);
    PWM_SetDuty2((uint16_t)d2u, (uint16_t)d2v, (uint16_t)d2w);

    /* 12. Фактическое напряжение после CLAMP → observer и FW.
     * OEW + TIM8 mode 2 (d1=d2=d): V_U = V_U1 − V_U2, где
     *   V_U1 = d/100·Vdc (HIN_U1 активен CNT<CCR, mode 1)
     *   V_U2 = (100−d)/100·Vdc (HIN_U2 активен CNT>CCR, mode 2 — противофаза)
     * → V_U = (2·d/100 − 1)·Vdc, в Q15: (2·d1u·32768/100 − 32768).
     * НЕ (d1u−d2u)·32768/100 — при d1=d2 тот дал бы 0, а реально
     * обмотка получает (2d−100)% шины! Это автоматически учитывает:
     *   - коэффициент 49/50 (≈2% масштаб) — без отдельной коррекции;
     *   - насыщение PWM (CLAMP 1..98) — observer видит реальное V;
     *   - dead-time и падение на ключах не учитываются (нужен compensation).
     * Forward Clarke (амплитудно-инвариантная):
     *   Vα = (2·Vu − Vv − Vw) / 3
     *   Vβ = (Vv − Vw) / √3  →  (Vv − Vw) · 18919 >> 15 */
    int32_t rvu = (int32_t)(((int64_t)d1u * 2 * 32768) / 100) - 32768;
    int32_t rvv = (int32_t)(((int64_t)d1v * 2 * 32768) / 100) - 32768;
    int32_t rvw = (int32_t)(((int64_t)d1w * 2 * 32768) / 100) - 32768;
    prev_valpha = (2*rvu - rvv - rvw) / 3;
    prev_vbeta  = ((rvv - rvw) * 18919) >> 15;
    prev_vd = vd;
    prev_vq = vq;

    /* 13. Обновляем Vdc для FW и observer */
    fw.vdc_mv = ADC_GetVbus_mV();
    observer.Vdc_mV = fw.vdc_mv;
}
