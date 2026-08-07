#ifndef AUTOTUNE_H
#define AUTOTUNE_H

#include <stdint.h>
#include <stdbool.h>

#define AUTOTUNE_MAX_CURRENT_MA      8000
#define AUTOTUNE_MAX_CURVE_POINTS    64
#define AUTOTUNE_MAX_REPEATS         5
#define AUTOTUNE_NUM_PAIRS           3

#define AT_DI_TARGET_MIN_MA          100
#define AT_DI_TARGET_MAX_MA          500
#define AT_ASYMMETRY_WARN_PCT        10
#define AT_SPREAD_WARN_PCT           15

typedef enum {
    AT_CH_UNKNOWN = 0,
    AT_CH_I1,
    AT_CH_I2,
    AT_CH_IN
} AtCurrentChannel;

typedef struct {
    int32_t current_ma;
    int32_t inductance_uH;
} AtCurvePoint;

typedef struct {
    int32_t median;
    int32_t min;
    int32_t max;
    int32_t spread_pct;
    int32_t values[AUTOTUNE_MAX_REPEATS];
    uint8_t count;
} AtStat32;

typedef struct {
    int32_t Rs_mOhm;
    int32_t Ls_uH;
    int32_t Isat_ma;
    bool valid;
} AtPairResult;

/* Bitmask: какие параметры реально измерены, а не остались нулевыми. */
#define AT_VALID_RS    (1U << 0)
#define AT_VALID_LS    (1U << 1)
#define AT_VALID_RR    (1U << 2)
#define AT_VALID_LM    (1U << 3)
#define AT_VALID_TR    (1U << 4)
#define AT_VALID_ISAT  (1U << 5)
#define AT_VALID_KE    (1U << 6)
#define AT_VALID_J     (1U << 7)
#define AT_VALID_PAIRS (1U << 8)
#define AT_VALID_CH    (1U << 9)

typedef struct {
    /* ── Измеренные параметры ── */
    int32_t  Rs_mOhm;          /* Сопротивление статора, мОм */
    int32_t  Ls_uH;            /* Индуктивность статора, мкГн */
    int32_t  Rr_mOhm;          /* Сопротивление ротора, мОм */
    int32_t  Lm_uH;            /* Индуктивность намагничивания, мкГн */
    int32_t  Tr_rotor_us;      /* Постоянная времени ротора Lr/Rr, мкс */
    int32_t  Ke_mV_per_rpm;    /* ЭДС холостого хода, мВ/(об/мин) */
    int32_t  Isat_ma;          /* Ток насыщения (Ls падает на 30%), мА */
    uint8_t  pole_pairs;       /* Количество пар полюсов */
    int32_t  J_kg_m2_x1e6;     /* Момент инерции, кг·м²×10⁻⁶ */

    /* ── Validity mask ── */
    uint32_t measured_mask;    /* Биты AT_VALID_* — какие параметры измерены */

    /* ── Статистика измерений ── */
    AtStat32 Rs_stat;
    AtStat32 Ls_stat;

    /* ── Кривая насыщения Ls(I) ── */
    AtCurvePoint curve[AUTOTUNE_MAX_CURVE_POINTS];
    uint8_t      curve_count;

    /* ── Результаты по фазам ── */
    AtPairResult pairs[AUTOTUNE_NUM_PAIRS];

    /* ── Конфигурация ── */
    AtCurrentChannel current_channel;  /* Канал АЦП для измерения тока */
    int8_t           current_sign;     /* +1 или -1: полярность канала */

    /* ── Flash storage (зарезервировано) ── */
    uint32_t magic;           /* MAGIC для проверки валидности во Flash */
    uint32_t crc32;           /* CRC32 структуры для Flash storage */
} MotorParams;

extern MotorParams g_motor_params;
extern volatile uint8_t g_autotune_abort;

void    Autotune_Init(void);
int8_t  Autotune_Idle(void);
int8_t  Autotune_MeasureRs_IV(void);
int8_t  Autotune_DetectChannel(void);
int8_t  Autotune_MeasureAllPairs(void);
int8_t  Autotune_Irot(void);
int8_t  Autotune_Inertia(void);
void    Autotune_PrintParams(void);
void    Autotune_PrintCurve(void);
void    Autotune_PrintPairs(void);
void    Autotune_PrintStats(void);
int8_t  Autotune_MeasureLs_OEW(void);
int8_t  Autotune_MeasureRr(void);
int8_t  Autotune_MeasureNoLoad(void);
int8_t  Autotune_Scope(void);
void    Autotune_CalcPI(int32_t bandwidth_hz);
int     Autotune_GetLastPI(int32_t *kp, int32_t *ki, int32_t *bw_hz);
int8_t  Autotune_MeasureLs_Position(void);


#endif
