/* Host-тесты расчётного ядра BM1109 (bm1109_measure_core.c).
 * Собираются и запускаются на ПК, без STM32 и без железа:
 *     make -C bm1109 test
 * Проверяются только формулы: восстановление известных R и L из
 * синтетических точек, отказы на вырожденных наборах, пересчёт звезды. */

#include "../bm1109_measure_core.h"

#include <stdio.h>
#include <math.h>

static int failures;

static void check(int cond, const char *what)
{
    if (cond) {
        printf("  ok    %s\n", what);
    } else {
        printf("  FAIL  %s\n", what);
        failures++;
    }
}

static int near_rel(float got, float want, float rel_tol)
{
    float den = (want > 0.0f) ? want : 1.0f;
    return fabsf(got - want) / den <= rel_tol;
}

/* R: три точки на прямой U = R*I + U0, R = 0,150 Ом, U0 = 1,10 В. */
static void test_resistance_exact(void)
{
    const float r_true = 0.150f;
    const float u0_true = 1.10f;
    const float currents[3] = {0.40f, 0.80f, 1.20f};
    bm1109_point pts[3];
    bm1109_r_result res;
    int k;

    printf("R: точная прямая\n");
    for (k = 0; k < 3; k++) {
        pts[k].i_a = currents[k];
        pts[k].u_v = r_true * currents[k] + u0_true;
    }

    check(bm1109_fit_resistance(pts, 3, &res) == BM1109_OK, "МНК вернул OK");
    check(near_rel(res.r_ohm, r_true, 1e-5f), "наклон = 0,150 Ом");
    check(near_rel(res.u_offset_v, u0_true, 1e-5f), "смещение = 1,10 В");
    check(res.rms_v < 1e-6f, "невязка ~ 0");
}

/* R: шум по току 1 % не должен уводить наклон больше чем на 3 %. */
static void test_resistance_noisy(void)
{
    const float r_true = 0.075f;
    const float u0_true = 0.90f;
    const float currents[3] = {0.40f, 0.80f, 1.20f};
    const float jitter[3] = {0.01f, -0.01f, 0.008f};
    bm1109_point pts[3];
    bm1109_r_result res;
    int k;

    printf("R: шум по току 1 %%\n");
    for (k = 0; k < 3; k++) {
        pts[k].i_a = currents[k] * (1.0f + jitter[k]);
        pts[k].u_v = r_true * currents[k] + u0_true;
    }

    check(bm1109_fit_resistance(pts, 3, &res) == BM1109_OK, "МНК вернул OK");
    check(near_rel(res.r_ohm, r_true, 0.04f), "наклон 0,075 Ом +-4 %");
}

/* R: нулевой ток — измерение недействительно. */
static void test_resistance_rejects_zero_current(void)
{
    bm1109_point pts[2];
    bm1109_r_result res;

    printf("R: нулевой ток\n");
    pts[0].i_a = 0.0f;
    pts[0].u_v = 0.0f;
    pts[1].i_a = 0.0f;
    pts[1].u_v = 0.0f;

    check(bm1109_fit_resistance(pts, 2, &res) == BM1109_ERR_NO_CURRENT,
          "отказ «нет тока»");
}

/* R: точки на почти одинаковом токе — наклон не определить. */
static void test_resistance_rejects_flat(void)
{
    bm1109_point pts[3];
    bm1109_r_result res;

    printf("R: вырожденный размах по току\n");
    pts[0].i_a = 0.800f; pts[0].u_v = 1.02f;
    pts[1].i_a = 0.805f; pts[1].u_v = 1.03f;
    pts[2].i_a = 0.802f; pts[2].u_v = 1.02f;

    check(bm1109_fit_resistance(pts, 3, &res) == BM1109_ERR_NOT_LINEAR,
          "отказ «нелинейность/вырожденный набор»");
}

/* L: точная экспонента i(t) = (U/R)(1-exp(-R*t/L)), L = 120 мкГн. */
static void test_inductance_exponential(void)
{
    const float r_true = 0.150f;
    const float l_true = 120e-6f;
    const float u_step = 6.0f;
    const float i_ss = u_step / r_true;
    bm1109_time_point pts[4];
    bm1109_l_result res;
    int k;

    printf("L: экспоненциальная ступень\n");
    for (k = 0; k < 4; k++) {
        float t = (float)(k + 1) * 20e-6f;
        pts[k].t_s = t;
        pts[k].i_a = i_ss * (1.0f - expf(-r_true * t / l_true));
    }

    check(bm1109_fit_inductance(pts, 4, u_step, r_true, i_ss, 0.05f, &res) == BM1109_OK,
          "МНК вернул OK");
    check(near_rel(res.l_h, l_true, 0.05f), "L_LL = 120 мкГн +-5 %");
    check(res.di_dt_a_s > 0.0f, "di/dt > 0");
}

/* L: слишком малый прирост тока — результат не выдаётся. */
static void test_inductance_rejects_small_delta(void)
{
    const float r_true = 0.150f;
    const float u_step = 6.0f;
    bm1109_time_point pts[3];
    bm1109_l_result res;
    int k;

    printf("L: малый прирост тока\n");
    for (k = 0; k < 3; k++) {
        pts[k].t_s = (float)(k + 1) * 10e-6f;
        pts[k].i_a = 0.200f + 0.005f * (float)k;   /* 5 мА на точку */
    }

    check(bm1109_fit_inductance(pts, 3, u_step, r_true, 40.0f, 0.05f, &res)
          == BM1109_ERR_LOW_DELTA_I, "отказ «малый прирост тока»");
}

/* Звезда: пересчёт и разброс. */
static void test_star_conversion(void)
{
    const float r_ll[3] = {0.300f, 0.310f, 0.290f};
    const float l_ll[3] = {240e-6f, 250e-6f, 230e-6f};
    float r_ph[3];
    float l_ph[3];
    float spread;

    printf("Звезда: R_ph = R_LL/2\n");
    bm1109_star_from_line(r_ll, l_ll, r_ph, l_ph);

    check(near_rel(r_ph[0], 0.150f, 1e-6f) && near_rel(r_ph[1], 0.155f, 1e-6f),
          "R_ph = 150/155 мОм");
    check(near_rel(l_ph[2], 115e-6f, 1e-6f), "L_ph = 115 мкГн");
    check(fabsf(spread = bm1109_spread_percent(r_ll, 3)) > 6.0f && spread < 7.0f,
          "разброс R_LL = 6,67 %");
}

static void test_status_str(void)
{
    printf("Статусы\n");
    check(bm1109_status_str(BM1109_OK) != NULL, "BM1109_OK имеет текст");
    check(bm1109_status_str(BM1109_ERR_OVER_CURRENT) != NULL,
          "OVER_CURRENT имеет текст");
}

int main(void)
{
    printf("== BM1109 measure core: host-тесты ==\n");

    test_resistance_exact();
    test_resistance_noisy();
    test_resistance_rejects_zero_current();
    test_resistance_rejects_flat();
    test_inductance_exponential();
    test_inductance_rejects_small_delta();
    test_star_conversion();
    test_status_str();

    printf("\n%s: провалов %d\n", failures == 0 ? "РЕЗУЛЬТАТ OK" : "РЕЗУЛЬТАТ FAIL", failures);
    return failures == 0 ? 0 : 1;
}
