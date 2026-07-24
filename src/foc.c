#include "foc.h"
#include "cordic_math.h"
#include "observer.h"
#include "pll.h"
#include "flux_weakening.h"
#include "adc.h"
#include "pwm.h"

AlphaBeta Clarke_Transform(int32_t iu, int32_t iv, int32_t iw) {
    /* Амплитудно-инвариантное преобразование Кларка (без нулевого провода).
     * Iα = Iu
     * Iβ = (2·Iv − Iu − Iw) / √3
     * При Iu+Iv+Iw=0: Iβ = (Iu + 2·Iv) / √3.
     * √3 ≈ 1.73205; в q1.15: 1/√3 ≈ 0.57735 → 18919 / 32768.
     * Используем целочисленное приближение: умножение на 18919 и сдвиг на 15.
     * iw принят для совместимости с расширенной 3-ф схемой; для 2-ф формулы
     * используется только iu и iv. */
    AlphaBeta ab;
    (void)iw;
    ab.alpha = iu;
    ab.beta  = ((iu + 2*iv) * 18919) >> 15;
    return ab;
}

DQ Park_Transform(int32_t alpha, int32_t beta, int32_t theta_q31) {
    DQ dq;
    int32_t s = CORDIC_Sin(theta_q31);
    int32_t c = CORDIC_Cos(theta_q31);
    dq.d = (alpha * c + beta * s) >> 15;
    dq.q = (-alpha * s + beta * c) >> 15;
    return dq;
}

AlphaBeta InvPark_Transform(int32_t vd, int32_t vq, int32_t theta_q31) {
    AlphaBeta vab;
    int32_t s = CORDIC_Sin(theta_q31);
    int32_t c = CORDIC_Cos(theta_q31);
    vab.alpha = (vd * c - vq * s) >> 15;
    vab.beta  = (vd * s + vq * c) >> 15;
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
    pi->integral = 0;
    pi->out_max = max; pi->out_min = min;
}

int32_t PI_Update(PIController *pi, int32_t error) {
    pi->integral += (pi->ki * error) >> 15;
    if(pi->integral > pi->out_max) pi->integral = pi->out_max;
    if(pi->integral < pi->out_min) pi->integral = pi->out_min;
    int32_t out = ((pi->kp * error) >> 15) + pi->integral;
    if(out > pi->out_max) out = pi->out_max;
    if(out < pi->out_min) out = pi->out_min;
    return out;
}

/* FOC state */
static volatile uint8_t foc_running = 0;
static int32_t speed_ref_rpm = 0;
static int32_t id_ref_ma = 0;

static BEMFObserver observer;
static PLL pll;
static PIController pi_d, pi_q;
static FluxWeakening fw;
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

void FOC_Init(void) {
    if(foc_initialized) return;
    BEMF_Init(&observer, FOC_DEFAULT_R_MOHM, FOC_DEFAULT_L_UH, FOC_DEFAULT_TS_US);
    PLL_Init(&pll, FOC_DEFAULT_PLL_KP, FOC_DEFAULT_PLL_KI, FOC_DEFAULT_TS_US);
    PI_Init(&pi_d, FOC_DEFAULT_PI_KP, FOC_DEFAULT_PI_KI, 32767, -32768);
    PI_Init(&pi_q, FOC_DEFAULT_PI_KP, FOC_DEFAULT_PI_KI, 32767, -32768);
    FW_Init(&fw, FOC_DEFAULT_VDC_MV, FOC_DEFAULT_FW_KP, FOC_DEFAULT_FW_KI);
    speed_ref_rpm = 0;
    id_ref_ma = FOC_DEFAULT_ID_REF_MA;
    foc_initialized = 1;
}

void FOC_SetSpeed(int32_t rpm) { speed_ref_rpm = rpm; }
void FOC_SetIdRef(int32_t ma)  { id_ref_ma = ma; }

int FOC_IsRunning(void) { return foc_running != 0; }

void FOC_Start(void) {
    if(!foc_initialized) FOC_Init();
    foc_running = 1;
    PWM_Enable();
}

void FOC_Stop(void) {
    foc_running = 0;
    PWM_Disable();
}

/* Напряжения, выданные в предыдущем FOC-цикле — нужны observer'у,
 * потому что observer на текущем шаге получает applied voltage прошлого шага
 * (стандартный predictive FOC). */
static int32_t prev_valpha = 0;
static int32_t prev_vbeta  = 0;

void FOC_Run(void) {
    if(!foc_running) return;

    /* 1. Чтение токов АЦП (уже выполнено через ADC_StartConversion) */
    int32_t i1_ma = ADC_GetI1_mA();
    int32_t i2_ma = ADC_GetI2_mA();
    int32_t in_ma = ADC_GetIN_mA();
    /* Приведение к внутреннему масштабу (Q15) — делим на 100.
     * Полный диапазон ±26А → ±26000 мА → ±260 в Q15.
     * Принято, что фактический рабочий диапазон мотора меньше. */
    int32_t iu = i1_ma / 100;
    int32_t iv = i2_ma / 100;
    int32_t iw = -(iu + iv + in_ma/100);

    /* 2. Clarke: Iα, Iβ */
    AlphaBeta ab = Clarke_Transform(iu, iv, iw);

    /* 3. BEMF Observer — получает Vα, Vβ ПРОШЛОГО цикла (predictive) */
    BEMF_Update(&observer, prev_valpha, prev_vbeta, ab.alpha, ab.beta);

    /* 4. PLL: Eα, Eβ → θ */
    PLL_Update(&pll, observer.emf_alpha, observer.emf_beta);
    int32_t theta = PLL_GetTheta(&pll);

    /* 5. Park: Iα, Iβ → Id, Iq */
    DQ dq = Park_Transform(ab.alpha, ab.beta, theta);

    /* 6. Flux Weakening: подгоняем Id_ref, чтобы напряжение не выходило
     * за 95% V_max. На первом шаге используем нулевые Vd, Vq (warm-up). */
    FW_Update(&fw, prev_valpha, prev_vbeta);   /* грубая аппроксимация через αβ */
    int32_t id_add = FW_GetIdAdd(&fw);

    /* 7. PI регуляторы по току */
    int32_t vd = PI_Update(&pi_d, (id_ref_ma + id_add) - dq.d);
    int32_t vq = PI_Update(&pi_q, speed_ref_rpm - dq.q);

    /* 8. Inverse Park + Clarke: Vd, Vq → Vα, Vβ → Vu, Vv, Vw */
    AlphaBeta vab = InvPark_Transform(vd, vq, theta);
    int32_t vu, vv, vw;
    InvClarke_Transform(vab.alpha, vab.beta, &vu, &vv, &vw);

    /* 9. Запоминаем Vα, Vβ для observer'а на следующем шаге */
    prev_valpha = vab.alpha;
    prev_vbeta  = vab.beta;

    /* 10. OEW распределение: V_inv1 = Vdc/2 + V/2, V_inv2 = Vdc/2 - V/2.
     * В Q15: 1.0 = 32768. Нормируем V/2 → ±49 (полупериод). */
    int32_t dc_bias = 50;
    int32_t half_vu = (vu * 49) / 65536;   /* Q15 → duty */
    int32_t half_vv = (vv * 49) / 65536;
    int32_t half_vw = (vw * 49) / 65536;
    PWM_SetDuty1((uint16_t)CLAMP(dc_bias + half_vu, 1, 98),
                 (uint16_t)CLAMP(dc_bias + half_vv, 1, 98),
                 (uint16_t)CLAMP(dc_bias + half_vw, 1, 98));
    PWM_SetDuty2((uint16_t)CLAMP(dc_bias - half_vu, 1, 98),
                 (uint16_t)CLAMP(dc_bias - half_vv, 1, 98),
                 (uint16_t)CLAMP(dc_bias - half_vw, 1, 98));

    /* 11. Обновляем Vdc для FW */
    fw.vdc_mv = ADC_GetVbus_mV();
}
