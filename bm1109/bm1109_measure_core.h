#ifndef BM1109_MEASURE_CORE_H
#define BM1109_MEASURE_CORE_H

/* ============================================================================
 *  BM1109 — расчётное ядро измерения R и L.
 *
 *  Здесь нет ни одного обращения к регистрам: только входные точки и
 *  результаты. Поэтому этот модуль собирается и проверяется на host-машине
 *  (bm1109/test/test_core.c) отдельно от прошивки.
 *
 *  Соглашения:
 *    - R_LL — сопротивление «линия-линия» (два вывода мотора);
 *    - для звезды с недоступной нейтралью R_ph = R_LL / 2, L_ph = L_LL / 2;
 *    - L_LL включает взаимную индуктивность двух фаз, поэтому для точного
 *      L_ph нужна либо паспортная поправка, либо измерение при разомкнутой
 *      третьей фазе (в текущей схеме третья фаза висит в воздухе).
 * ========================================================================== */

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BM1109_OK = 0,
    BM1109_ERR_ARG,
    BM1109_ERR_ADC,
    BM1109_ERR_NO_CURRENT,
    BM1109_ERR_OVER_CURRENT,
    BM1109_ERR_TIMEOUT,
    BM1109_ERR_VBUS,
    BM1109_ERR_NOT_LINEAR,
    BM1109_ERR_LOW_DELTA_I,
    BM1109_ERR_VBUS_L_HIGH
} bm1109_status;

/* Точка для МНК по сопротивлению: приложенное напряжение и ток. */
typedef struct {
    float u_v;
    float i_a;
} bm1109_point;

/* Точка переходного процесса: время от начала импульса и ток. */
typedef struct {
    float t_s;
    float i_a;
} bm1109_time_point;

typedef struct {
    float   r_ohm;        /* наклон U(I) — R_LL без смещения U0 */
    float   u_offset_v;   /* U0: ключи + dead time + сдвиг канала */
    float   r_single_ohm; /* U/I последней точки, без компенсации (диагностика) */
    float   rms_v;        /* СКО невязки МНК, В */
    float   i_span_a;     /* размах точек по току, А */
    uint8_t points;
    bm1109_status status;
} bm1109_r_result;

typedef struct {
    float   l_h;        /* L_LL, Гн */
    float   di_dt_a_s;  /* наклон di/dt, А/с */
    float   i_mean_a;   /* средний ток выбранных точек */
    float   u_eff_v;    /* напряжение на обмотке за вычетом R*i и падения ключей */
    uint8_t points;
    bm1109_status status;
} bm1109_l_result;

/* МНК по U = R*I + U0. Требует >= 2 точек с положительным током и
 * ненулевым размахом. Возвращает BM1109_OK и заполняет out. */
bm1109_status bm1109_fit_resistance(const bm1109_point *pts, uint8_t n,
                                    bm1109_r_result *out);

/* Оценка L по начальному участку ступенчатого отклика:
 *     L = (u_step - R*i_mean) / (di/dt).
 * Используются точки с i <= 0,5*i_ss (линейная зона). Требует >= 2 таких
 * точек и прирост тока не меньше min_delta_a. */
bm1109_status bm1109_fit_inductance(const bm1109_time_point *pts, uint8_t n,
                                    float u_step_v, float r_ohm, float i_ss_a,
                                    float min_delta_a, bm1109_l_result *out);

/* Звезда: R_ph = R_LL/2, L_ph = L_LL/2. Массивы по 3 элемента (UV, VW, WU). */
void bm1109_star_from_line(const float r_ll[3], const float l_ll[3],
                           float r_ph[3], float l_ph[3]);

/* (max - min) / mean * 100 %. При mean == 0 возвращает 0. */
float bm1109_spread_percent(const float *values, uint8_t n);

/* Текст статуса для отчёта (никогда не NULL). */
const char *bm1109_status_str(bm1109_status st);

#ifdef __cplusplus
}
#endif

#endif /* BM1109_MEASURE_CORE_H */
