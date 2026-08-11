/* FOC-математика: тест для QEMU (Cortex-M4, semihosting) и hosted-запуска.
 *
 * Проверяет:
 *   1. Clarke: Iu+Iv+Iw=0 → alpha/beta корректны (амплитудно-инвариантно)
 *   2. Park: вращение d/q при 0° и 90°
 *   3. InvPark + Park: round-trip
 *   4. InvClarke: vu+vv+vw=0, восстановление alpha/beta
 *   5. PI: шаг, насыщение, anti-windup (BackCalculation)
 *
 * Сборка под QEMU:
 *   arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -mfloat-abi=soft \
 *     -I tests/mocks -ffunction-sections -fdata-sections \
 *     src/foc.c tests/mocks/mock_cordic.c tests/foc_math_test.c \
 *     -Wl,--gc-sections -Wl,-Ttext=0x08000000 -nostdlib -lc -lm \
 *     -o tests/foc_test.elf
 *   qemu-system-arm -M olimex-stm32-h405 -nographic -semihosting \
 *     -kernel tests/foc_test.elf
 */
#include "foc.h"
#include "cordic_math.h"

/* ── Semihosting вывод (работает в QEMU; на hosted компилируется в no-op) ── */
#if defined(__ARM_EABI__) || defined(__arm__)
/* QEMU-ветка: прямой semihosting без libc (bkpt 0xAB, r0=0x04 SYS_WRITE0, r1=строка) */
static void sh_init(void) { }
static void sh_puts(const char *s) {
    register int r0 __asm__("r0") = 0x04;    /* SYS_WRITE0 */
    register const char *r1 __asm__("r1") = s;
    __asm__ volatile("bkpt 0xAB" : : "r"(r0), "r"(r1) : "memory");
}
static void sh_putc(char c) {
    char buf[2] = {c, 0};
    sh_puts(buf);
}
static void sh_puthex(uint32_t v) {
    char buf[12]; int i;
    sh_puts("0x");
    for (i = 28; i >= 0; i -= 4) {
        int d = (v >> i) & 0xF;
        sh_putc(d < 10 ? '0' + d : 'A' + d - 10);
    }
}
static void sh_putdec(int32_t v) {
    char buf[16]; int i = 0; uint32_t u;
    if (v < 0) { sh_putc('-'); u = (uint32_t)(-v); } else u = (uint32_t)v;
    do { buf[i++] = '0' + (u % 10); u /= 10; } while (u);
    while (i > 0) sh_putc(buf[--i]);
}
#else
/* hosted (x86): обычный printf */
#include <stdio.h>
static void sh_puts(const char *s) { fputs(s, stdout); }
static void sh_putc(char c) { putchar(c); }
static void sh_puthex(uint32_t v) { printf("0x%08X", v); }
static void sh_putdec(int32_t v) { printf("%ld", (long)v); }
#endif

static int failures = 0;
static int checks = 0;

static void check(const char *name, int ok, int32_t got, int32_t expect, int tol) {
    checks++;
    sh_puts("  [");
    sh_puts(ok ? "PASS" : "FAIL");
    sh_puts("] ");
    sh_puts(name);
    if (!ok) {
        sh_puts(" got=");
        sh_putdec(got);
        sh_puts(" expect=");
        sh_putdec(expect);
        sh_puts(" tol=");
        sh_putdec(tol);
        failures++;
    }
    sh_puts("\n");
}

static int near(int32_t a, int32_t b, int tol) {
    int32_t d = a - b;
    if (d < 0) d = -d;
    return d <= tol;
}

/* Q15 константы: 0.5→16384, 1/√3≈0.577→18919, √3≈1.732→56756 */
#define Q15_HALF    16384
#define Q15_INVRT3  18919
#define Q15_RT3     56756

int main(void) {
#if defined(__ARM_EABI__) || defined(__arm__)
    sh_init();
#endif
    sh_puts("=== FOC math test ===\n");

    /* 1. Clarke: сбалансированные токи Iu=1000,Iv=-500,Iw=-500 (сумма 0) */
    {
        sh_puts("-- Clarke_Transform --\n");
        AlphaBeta ab = Clarke_Transform(1000, -500, -500);
        /* Iα = (2*1000 - (-500) - (-500))/3 = (2000+1000)/3 = 1000 */
        check("Ialpha=Iu (balanced)", near(ab.alpha, 1000, 1), ab.alpha, 1000, 1);
        /* Iβ = (Iv-Iw)/√3 = 0 */
        check("Ibeta=0 (balanced)", near(ab.beta, 0, 2), ab.beta, 0, 2);

        /* Несбалансированный: Iu=3000, Iv=0, Iw=0 → Iα=2000, Iβ=0 */
        ab = Clarke_Transform(3000, 0, 0);
        check("Ialpha=2/3*Iu", near(ab.alpha, 2000, 1), ab.alpha, 2000, 1);
        check("Ibeta=0 (single phase)", near(ab.beta, 0, 2), ab.beta, 0, 2);
    }

    /* 2. Park: при θ=0 d=alpha, q=beta; при θ=90° d=-beta, q=alpha */
    {
        sh_puts("-- Park_Transform --\n");
        /* θ=0 (q31=0): sin=0, cos=32768 → d=alpha, q=beta */
        DQ dq0 = Park_Transform(3000, 2000, 0);
        check("Park(0): d=alpha", near(dq0.d, 3000, 2), dq0.d, 3000, 2);
        check("Park(0): q=beta", near(dq0.q, 2000, 2), dq0.q, 2000, 2);
        /* θ=90° (q31=0x40000000): sin≈32767, cos≈0 → d=+beta, q=-alpha
         * (стандартный Park: d=α·cos+β·sin, q=-α·sin+β·cos) */
        DQ dq90 = Park_Transform(3000, 2000, 0x40000000);
        check("Park(90): d=+beta", near(dq90.d, 2000, 3), dq90.d, 2000, 3);
        check("Park(90): q=-alpha", near(dq90.q, -3000, 3), dq90.q, -3000, 3);
    }

    /* 3. InvPark + Park: round-trip */
    {
        sh_puts("-- InvPark round-trip --\n");
        int32_t theta = 0x12345678;
        AlphaBeta vab = InvPark_Transform(5000, -3000, theta);
        DQ dq = Park_Transform(vab.alpha, vab.beta, theta);
        check("round-trip d", near(dq.d, 5000, 3), dq.d, 5000, 3);
        check("round-trip q", near(dq.q, -3000, 3), dq.q, -3000, 3);
    }

    /* 4. InvClarke: vu+vv+vw=0, восстановление */
    {
        sh_puts("-- InvClarke_Transform --\n");
        int32_t vu, vv, vw;
        InvClarke_Transform(2000, 3000, &vu, &vv, &vw);
        check("sum=0", near(vu + vv + vw, 0, 2), vu + vv + vw, 0, 2);
        check("vu=valpha", near(vu, 2000, 1), vu, 2000, 1);
        /* vv = (-va + √3·vb)/2 = (-2000 + 5196)/2 = 1598 */
        check("vv=(-va+√3vb)/2", near(vv, 1598, 2), vv, 1598, 2);
    }

    /* 5. PI: шаг, насыщение, anti-windup */
    {
        sh_puts("-- PI controller --\n");
        PIController pi;
        PI_Init(&pi, 16384, 3277, 10000, -10000);   /* kp=0.5, ki=0.1 */
        int32_t out = PI_Update(&pi, 1000);
        /* p_term = 0.5*1000 = 500; integral = 0.1*1000 = 100 → out=600 */
        check("PI step out", near(out, 600, 1), out, 600, 1);

        /* Большой error: integral насыщается out_max, но выход НЕ ограничен
         * (дизайн: p_term проходит насквозь, финальный clamp — PI_BackCalculation).
         * p_term=0.5*100000=50000, integral=0.1*100000=10000 → out=60000 */
        pi.integral = 0;
        out = PI_Update(&pi, 100000);
        check("PI p-term passes through (no output clamp)", out == 60000, out, 60000, 1);
        check("PI integral clamped to max", pi.integral == 10000, pi.integral, 10000, 0);

        /* BackCalculation: интегратор корректируется вниз */
        PI_BackCalculation(&pi, -5000);   /* saturation_error */
        out = PI_Update(&pi, 0);
        check("PI anti-windup recovers", out < 10000, out, 10000, 0);

        /* Отрицательная сторона: integral насыщается вниз, выход = -60000 */
        pi.integral = 0;
        out = PI_Update(&pi, -100000);
        check("PI negative integral clamped", pi.integral == -10000, pi.integral, -10000, 0);
        check("PI negative out = p+integral", out == -60000, out, -60000, 1);
    }

    sh_puts("=== ");
    sh_puts(failures == 0 ? "ALL PASS" : "FAILURES");
    sh_puts(" (");
    sh_putdec(checks);
    sh_puts(" checks, ");
    sh_putdec(failures);
    sh_puts(" failed) ===\n");

#if defined(__ARM_EABI__) || defined(__arm__)
    /* QEMU: вывод через UART2 — просто зацикливаемся (timeout убьёт QEMU) */
    for (;;) { }
#endif
    return failures ? 1 : 0;
}
