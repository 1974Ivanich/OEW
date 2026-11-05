#include "autotune_math.h"
#include <stdio.h>
#include <stdint.h>

static int failures;
static int checks;

static void check(const char *name, int condition)
{
    ++checks;
    if (!condition) {
        ++failures;
        printf("FAIL: %s\n", name);
    } else {
        printf("ok:   %s\n", name);
    }
}

static void check_near(const char *name, int32_t value, int32_t expected, int32_t tolerance)
{
    int64_t delta = (int64_t)value - expected;
    if (delta < 0) delta = -delta;
    check(name, delta <= tolerance);
}

int main(void)
{
    check_near("sin 0", AT_MathSinQ15(0), 0, 2);
    check_near("sin pi/2", AT_MathSinQ15(1571), 32767, 16);
    check_near("sin pi", AT_MathSinQ15(3142), 0, 32);
    check_near("sin 3pi/2", AT_MathSinQ15(4712), -32767, 32);
    check_near("sin 2pi", AT_MathSinQ15(6283), 0, 2);
    check_near("sin negative", AT_MathSinQ15(-1571), -32767, 16);
    check_near("sin wrapped", AT_MathSinQ15(6283 + 1571), 32767, 16);
    check_near("cos via shifted sine", AT_MathSinQ15(1571 + 1571), 0, 32);

    {
        int32_t values[] = {9, 1, 7, 3, 5};
        check("median odd", AT_MathMedianSmall(values, 5) == 5);
    }
    {
        int32_t values[] = {8, 2, 6, 4};
        check("median even project upper", AT_MathMedianSmall(values, 4) == 6);
    }
    {
        int32_t values[] = {4};
        check("median one", AT_MathMedianSmall(values, 1) == 4);
    }
    {
        int32_t values[] = {9, 2};
        check("median two project upper", AT_MathMedianSmall(values, 2) == 9);
    }

    check("Ls lower accepted", AT_SaneLs(500) == 500);
    check("Ls upper accepted", AT_SaneLs(500000) == 500000);
    check("Ls zero rejected", AT_SaneLs(0) == 0);
    check("Ls negative rejected", AT_SaneLs(-1) == 0);
    check("Rs positive accepted", AT_SaneRs(10) == 10);
    check("Rs zero rejected", AT_SaneRs(0) == 0);
    check("Rs negative rejected", AT_SaneRs(-10) == 0);

    {
        AtCurvePoint curve[8] = {
            {100, 1000}, {200, 990}, {300, 980}, {400, 970},
            {500, 960}, {600, 950}, {700, 940}, {800, 930}
        };
        uint8_t kept = AT_MathCurveFilterOutliers(curve, 8);
        check("normal curve preserved", kept == 8);
    }
    {
        AtCurvePoint curve[8] = {
            {100, 1000}, {200, 990}, {300, 10}, {400, 970},
            {500, 960}, {600, 950}, {700, 940}, {800, 930}
        };
        uint8_t kept = AT_MathCurveFilterOutliers(curve, 8);
        check("single outlier removed", kept == 7);
    }
    {
        AtCurvePoint curve[8] = {
            {100, 0}, {200, -1}, {300, 0}, {400, -10},
            {500, 0}, {600, -2}, {700, 0}, {800, -3}
        };
        check("all invalid curve safely empty", AT_MathCurveFilterOutliers(curve, 8) == 0);
    }

    {
        AtCurvePoint curve[8] = {
            {100, 1000}, {200, 990}, {300, 980}, {400, 970},
            {500, 650}, {600, 640}, {700, 630}, {800, 620}
        };
        check("Isat interpolates above/below threshold",
              AT_MathCalcIsat(curve, 8, 1000, 70) == 484);
    }
    {
        AtCurvePoint curve[8] = {
            {100, 600}, {200, 590}, {300, 580}, {400, 570},
            {500, 560}, {600, 550}, {700, 540}, {800, 530}
        };
        check("Isat first point below is conservative",
              AT_MathCalcIsat(curve, 8, 1000, 70) == 100);
    }
    {
        AtCurvePoint curve[4] = {{100, 1000}, {200, 900}, {300, 800}, {400, 700}};
        check("Isat insufficient curve rejected", AT_MathCalcIsat(curve, 4, 1000, 70) == 0);
    }

    /* 11.2026: МНК-оценка Rs (AT_LinearRegressionR) — та же математика, что
     * в Autotune_MeasureRs_IV, но проверяемая без железа. */
    {
        const int32_t U[3] = {5000, 10000, 15000};
        const int32_t I[3] = {1000, 2000, 3000};
        int32_t r = 0;
        check("regr exact slope 5000 mOhm",
              AT_LinearRegressionR(U, I, 3u, &r) == 0 && r == 5000);
    }
    {
        /* Постоянное смещение напряжения не должно попадать в наклон. */
        const int32_t U[4] = {8000, 13000, 18000, 23000};
        const int32_t I[4] = {1000, 2000, 3000, 4000};
        int32_t r = 0;
        check("regr offset cancels",
              AT_LinearRegressionR(U, I, 4u, &r) == 0 && r == 5000);
    }
    {
        /* Отрицательный наклон проходит как есть: диапазон проверяет вызывающий. */
        const int32_t U[3] = {5000, 3000, 1000};
        const int32_t I[3] = {1000, 2000, 3000};
        int32_t r = 0;
        check("regr negative slope returned",
              AT_LinearRegressionR(U, I, 3u, &r) == 0 && r == -2000);
    }
    {
        const int32_t U[3] = {100, 200, 300};
        const int32_t I[3] = {700, 700, 700};
        int32_t r = 0;
        check("regr no current spread rejected",
              AT_LinearRegressionR(U, I, 3u, &r) == -2);
    }
    {
        const int32_t U[2] = {100, 200};
        const int32_t I[2] = {100, 200};
        int32_t r = 0;
        check("regr too few points rejected",
              AT_LinearRegressionR(U, I, 2u, &r) == -1);
        check("regr null pointer rejected",
              AT_LinearRegressionR(0, I, 3u, &r) == -1);
    }

    printf("AutoTune math: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
