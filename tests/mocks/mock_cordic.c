/* Mock: аппаратный CORDIC → программная реализация БЕЗ libm (для bare-metal QEMU).
 * arm-none-eabi libm при -nostdlib вызывает semihosting → Data Abort в QEMU.
 * Поэтому sin/cos/atan2/sqrt реализованы собственным полиномом (точности
 * достаточно для проверки трансформаций: ошибка < 1 LSB Q15).
 * Сигнатуры идентичны реальному CORDIC (Q1.31 угол, Q15 sin/cos). */
#include "cordic_math.h"

#define Q31_2PI 2147483648.0f   /* q31 → радианы (2^31 = π) */
#define PI_F 3.14159265358979f

/* ── Собственные математические функции (без libm) ─────────────────────── */
static float f_abs(float x) { return x < 0.0f ? -x : x; }

/* sin/cos: ряд Тейлора до x^15 (схема Хорнера). Ошибка при x=π ~7e-7
 * → < 1 LSB Q15. Аргумент сводится к [−π, π]. */
static float poly_sin(float x) {
    float x2 = x * x;
    /* sin(x) = x·(1 − x²/3! + x⁴/5! − x⁶/7! + x⁸/9! − x¹⁰/11! + x¹²/13! − x¹⁴/15!) */
    return x * (1.0f + x2 * (-0.16666667f
                + x2 * (0.0083333333f
                + x2 * (-0.00019841270f
                + x2 * (0.0000027557319f
                + x2 * (-0.000000025052108f
                + x2 * (0.00000000016059044f
                + x2 * (-0.00000000000076471637f))))))));
}

static float f_sin(float x) {
    /* сводим к [-2π, 2π] → [-π, π] */
    while (x >  PI_F) x -= 2.0f * PI_F;
    while (x < -PI_F) x += 2.0f * PI_F;
    return poly_sin(x);
}

static float f_cos(float x) { return f_sin(x + PI_F * 0.5f); }

static float f_sqrt(float x) {
    if (x <= 0.0f) return 0.0f;
    /* метод Ньютона, 8 итераций */
    float r = x;
    for (int i = 0; i < 8; i++) r = 0.5f * (r + x / r);
    return r;
}

static float f_atan_small(float t) {
    /* atan(t), |t| <= 1: полином 7-й степени */
    float t2 = t * t;
    return t * (1.0f - t2 * (0.33333333f - t2 * (0.2f - t2 * 0.14285714f)));
}

static float f_atan2(float y, float x) {
    /* atan2 через atan(y/x) с квадрантной коррекцией (без деления на 0) */
    float ax = f_abs(x), ay = f_abs(y);
    float a;
    if (ax > ay) a = f_atan_small(y / x);            /* |y/x| < 1 */
    else if (ay > 0.0f) a = PI_F * 0.5f - f_atan_small(x / y); /* |x/y| < 1 */
    else a = 0.0f;
    if (x < 0.0f) a = (y >= 0.0f) ? PI_F - f_abs(a) : -PI_F + f_abs(a);
    else if (y < 0.0f) a = -f_abs(a);
    return a;
}

static int32_t q15_round(float v) {
    if (v >= 1.0f) return 32767;
    if (v <= -1.0f) return -32768;
    return (int32_t)(v * 32768.0f + (v >= 0.0f ? 0.5f : -0.5f));
}

void CORDIC_Init(void) { }

int32_t CORDIC_Sin(int32_t angle_q31) {
    float a = (float)angle_q31 / Q31_2PI * PI_F;
    return q15_round(f_sin(a));
}

int32_t CORDIC_Cos(int32_t angle_q31) {
    float a = (float)angle_q31 / Q31_2PI * PI_F;
    return q15_round(f_cos(a));
}

void CORDIC_SinCos(int32_t angle_q31, int32_t *sin_q15, int32_t *cos_q15) {
    float a = (float)angle_q31 / Q31_2PI * PI_F;
    *sin_q15 = q15_round(f_sin(a));
    *cos_q15 = q15_round(f_cos(a));
}

int32_t CORDIC_Atan2(int32_t y, int32_t x) {
    float a = f_atan2((float)y / Q31_2PI * PI_F, (float)x / Q31_2PI * PI_F) / PI_F;
    return (int32_t)(a * Q31_2PI + (a >= 0.0f ? 0.5f : -0.5f));
}

void CORDIC_Modulus(int32_t x, int32_t y, int32_t *mod, int32_t *angle) {
    float fx = (float)x / Q31_2PI * PI_F, fy = (float)y / Q31_2PI * PI_F;
    *mod   = (int32_t)(f_sqrt(fx*fx + fy*fy) / PI_F * Q31_2PI + 0.5f);
    *angle = (int32_t)(f_atan2(fy, fx) / PI_F * Q31_2PI + (fy >= 0.0f ? 0.5f : -0.5f));
}
