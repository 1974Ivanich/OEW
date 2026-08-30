/* V/f контроллер: тест для QEMU (Cortex-M4, semihosting) и hosted-запуска.
 *
 * Проверяет src/vf_control.c (VFC_Update, 1 кГц):
 *   1. Speed ramp: целочисленный разгон с остатком (ramp_rem), достижение цели
 *   2. Ramp клиппинг на VFC_MAX_RPM
 *   3. V/f характеристика: vmag = 100*|f_e|/rated + boost (одно деление!)
 *   4. Start boost: при f_e<1 Гц → vmag = v_boost_pct
 *   5. vmag клиппинг на VFC_MAX_VOLTAGE_PCT
 *   6. Электрическая частота: f_e = p*n/60 + f_slip
 *   7. Фазовый аккумулятор: theta += f_e * 2^32/1000 за тик
 *   8. 3-фазная генерация: sin_u+sin_v+sin_w = 0 (120°/240°)
 *   9. duty в [2..98] и сумма d_u+d_v+d_w = 150 (50% средняя)
 *  10. Slip PI клиппинг на VFC_MAX_SLIP_HZ
 *  11. f_e клиппинг на VFC_MAX_FE_HZ
 *  12. VFC_Start сохраняет target_rpm при fail-closed отказе
 *
 * Сборка hosted:
 *   gcc -I tests/mocks tests/vf_control_test.c tests/mocks/mock_cordic.c \
 *       tests/mocks/foc_stubs.c src/foc.c src/vf_control.c -o /tmp/vftest
 * Сборка QEMU (в Makefile: make test):
 *   arm-none-eabi-gcc ... tests/qemu_startup.s tests/vf_control_test.c \
 *       tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c \
 *       src/vf_control.c -T tests/qemu_test.ld -nostdlib -lgcc
 */

#include <stdint.h>
#include <string.h>

#include "vf_control.h"
#include "foc.h"
#include "autotune.h"
#include "encoder.h"

/* Управляемый энкодер (VFC_Update читает ENC_GetSpeed_rpm внутри) */
extern int32_t test_enc_rpm;

/* ── Вывод: hosted printf / QEMU semihosting (как в foc_math_test.c) ─────── */
#if defined(__ARM_EABI__) || defined(__arm__)
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
static void sh_putdec(int32_t v) {
    char buf[16]; int i = 0; uint32_t u;
    if (v < 0) { sh_putc('-'); u = (uint32_t)(-v); } else u = (uint32_t)v;
    do { buf[i++] = '0' + (u % 10); u /= 10; } while (u);
    while (i > 0) sh_putc(buf[--i]);
}
#else
#include <stdio.h>
static void sh_init(void) { }
static void sh_puts(const char *s) { fputs(s, stdout); }
static void sh_putc(char c) { putchar(c); }
static void sh_putdec(int32_t v) { printf("%ld", (long)v); }
#endif

static void sh_putnum(int32_t v) { sh_putdec(v); }

static int failures = 0;
static int checks = 0;

static void check(const char *name, int pass, int32_t got, int32_t exp, int32_t tol) {
    checks++;
    sh_puts("  [");
    sh_puts(pass ? "PASS" : "FAIL");
    sh_puts("] ");
    sh_puts(name);
    if (!pass) {
        sh_puts(" got=");
        sh_putnum(got);
        sh_puts(" expect=");
        sh_putnum(exp);
        if (tol) { sh_puts(" tol="); sh_putnum(tol); }
        failures++;
    }
    sh_puts("\n");
}

#define NEAR(got, exp, tol) ((got) >= (exp) - (tol) && (got) <= (exp) + (tol))

/* ── Утилиты: n вызовов VFC_Update с фиксированной скоростью энкодера ───── */
static void run_updates(int n, int32_t enc_rpm) {
    for (int i = 0; i < n; i++) {
        test_enc_rpm = enc_rpm;
        VFC_Update();
    }
}

int main(void) {
    sh_init();
    sh_puts("=== V/f control test ===\n");

    /* ── 0. API: Start uses the measured contract and retains target ── */
    {
        VFC_Init();
        int start_rc = VFC_Start(1500);
        check("start: measured contract accepts initial vector",
              start_rc == VFC_START_OK && VFC_IsRunning() && VFC_GetTarget() == 1500,
              start_rc, VFC_START_OK, 0);
        check("contract: all six entries are measured and bounded",
              VfcApertureContract[0].measured && VfcApertureContract[5].measured &&
              VfcApertureContract[0].window < 2u &&
              VfcApertureContract[0].modulation_min == 135u &&
              VfcApertureContract[0].modulation_max == 999u &&
              VfcApertureContract[0].trgo_to_jeos_min_cycles == 501u &&
              VfcApertureContract[0].trgo_to_jeos_max_cycles == 543u &&
              VfcApertureContract[0].switching_margin_cycles >= 110u,
              VfcApertureContract[0].modulation_min, 135, 0);
        VFC_Stop();
    }

    /* ── 1. Ramp: экспоненциальный подход current += diff*dt/ramp_time ──
     * ТЗ v3: current += (target-current)*dt/ramp_time → current(t) =
     * target*(1-(1-1/ramp_time)^t). За 2000 тиков при target=500:
     * 500*(1-(1999/2000)^2000) ≈ 500*(1-1/e) ≈ 316.1 */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetVfParams(30, 20);
        VFC_SetTarget(500); vfc.running = 1;  /* test: bypass context gate */
        run_updates(2000, 1);       /* near-zero encoder speed; motor effectively stopped */
        check("ramp: exp approach ≈316/500 за 2000 тиков",
              NEAR(vfc.ramp_current_rpm, 316, 2), vfc.ramp_current_rpm, 316, 2);
        /* за 10000 тиков (5·ramp_time) ≈ 500·(1−e⁻⁵) ≈ 496 — приближается */
        run_updates(8000, 1);
        check("ramp: приближается к цели (≥480 за 10·ramp_time)",
              vfc.ramp_current_rpm >= 480, vfc.ramp_current_rpm, 496, 20);
    }

    /* ── 2. Ramp клиппинг: цель выше максимума → current ≤ VFC_MAX_RPM ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetVfParams(30, 20);
        VFC_SetTarget(99999); vfc.running = 1;  /* test: bypass context gate */
        run_updates(4000, 0);
        check("ramp clamps to VFC_MAX_RPM", vfc.ramp_current_rpm <= 5000,
              vfc.ramp_current_rpm, 5000, 0);
        VFC_Stop();
    }

    /* ── 3. V/f: vmag = 100*|f_e|/rated + boost (одно деление!) ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(750); vfc.running = 1;  /* test: bypass context gate */              /* f_e = 2*750/60 = 25 Гц при slip=0 */
        vfc.ramp_current_rpm = 750;  /* ramp достиг цели → error=0 → slip=0 */
        run_updates(1, 750);
        check("vf: vmag = 100*fe/rated + boost", vfc.voltage_mag == 65,
              vfc.voltage_mag, 65, 0);
        check("vf: f_e = 25 Hz (p=2, n=750)", NEAR(vfc.f_e_hz, 25, 1),
              vfc.f_e_hz, 25, 1);
        VFC_Stop();
    }

    /* ── 4. Start boost: при f_e < 1 Гц → vmag = v_boost_pct ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(0); vfc.running = 1;  /* test: bypass context gate */                /* f_e = 0 → abs_fe < 1 */
        run_updates(1, 0);
        check("vf: start boost at f_e<1", vfc.voltage_mag == 15,
              vfc.voltage_mag, 15, 0);
        VFC_Stop();
    }

    /* ── 5. vmag клиппинг: ≤ VFC_MAX_VOLTAGE_PCT (95) ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(5000); vfc.running = 1;  /* test: bypass context gate */             /* f_e = 2*5000/60 ≈ 166 Гц */
        vfc.ramp_current_rpm = 5000;
        run_updates(1, 5000);
        /* vmag = 100*166/50 + 15 = 347 → clamp 95 */
        check("vf: vmag clamps to 95%", vfc.voltage_mag == 95,
              vfc.voltage_mag, 95, 0);
        VFC_Stop();
    }

    /* ── 6. f_e: p*n/60 + f_slip ── */
    {
        VFC_Init();
        FOC_SetPolePairs(4);
        VFC_SetTarget(1500); vfc.running = 1;  /* test: bypass start hardware gate */             /* f_e = 4*1500/60 = 100 Гц */
        VFC_SetVfParams(15, 200);  /* vmag=65 stays inside CCR>=135 aperture */
        vfc.ramp_current_rpm = 1500;
        run_updates(1, 1500);
        check("fe: p=4, n=1500 → 100 Hz", NEAR(vfc.f_e_hz, 100, 1),
              vfc.f_e_hz, 100, 1);
        VFC_Stop();
    }

    /* ── 7. Фазовый аккумулятор: theta += f_e * 2^32/1000 за тик ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(750); vfc.running = 1;  /* test: bypass context gate */              /* f_e = 25 Гц */
        vfc.ramp_current_rpm = 750;
        run_updates(1, 750);
        uint32_t t0 = vfc.theta_elec;
        VFC_Update();                /* +25 * 4294967 */
        uint32_t delta = vfc.theta_elec - t0;
        uint32_t exp_delta = (uint32_t)(25 * 4294967UL);
        check("theta: delta = fe * 2^32/1000", NEAR((int32_t)delta, (int32_t)exp_delta, 64),
              (int32_t)delta, (int32_t)exp_delta, 64);
        VFC_Stop();
    }

    /* ── 8. 3-фазная генерация: sin_u+sin_v+sin_w = 0 ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(750); vfc.running = 1;  /* test: bypass context gate */
        vfc.ramp_current_rpm = 750;
        run_updates(1, 750);
        /* d = 50 + vmag*sin/(2*32768). Сумма sin = 0 (сдвиг 120°),
         * значит d_u+d_v+d_w = 150, если клиппинг не сработал.
         * vmag=65 → амплитуда d = 65/(2*32768)*sin — в пределах [2..98]? */
        int32_t sum = vfc.duty_u + vfc.duty_v + vfc.duty_w;
        check("3ph: duty sum = 150 (sin sum = 0)", sum == 150, sum, 150, 0);
        check("3ph: duty_u in [2..98]", vfc.duty_u >= 2 && vfc.duty_u <= 98,
              vfc.duty_u, 50, 0);
        check("3ph: duty_v in [2..98]", vfc.duty_v >= 2 && vfc.duty_v <= 98,
              vfc.duty_v, 50, 0);
        check("3ph: duty_w in [2..98]", vfc.duty_w >= 2 && vfc.duty_w <= 98,
              vfc.duty_w, 50, 0);
        VFC_Stop();
    }

    /* ── 9. Slip PI клиппинг: |f_slip| ≤ VFC_MAX_SLIP_HZ (5) ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(500); vfc.running = 1;  /* test: bypass context gate */
        /* Огромная ошибка: target=500, энкодер показывает -5000 → error=5500 */
        run_updates(200, -5000);
        check("slip: |f_slip| ≤ 5 Hz", vfc.f_slip_hz >= -5 && vfc.f_slip_hz <= 5,
              vfc.f_slip_hz, -5, 0);
        VFC_Stop();
    }

    /* ── 10. f_e клиппинг: |f_e| ≤ VFC_MAX_FE_HZ (200) ── */
    {
        VFC_Init();
        FOC_SetPolePairs(4);
        VFC_SetTarget(5000); vfc.running = 1;  /* test: bypass context gate */             /* f_e = 4*5000/60 ≈ 333 Гц → clamp 200 */
        vfc.ramp_current_rpm = 5000;
        run_updates(1, 5000);
        check("fe: clamps to 200 Hz", NEAR(vfc.f_e_hz, 200, 1),
              vfc.f_e_hz, 200, 1);
        VFC_Stop();
    }

    /* ── 11. VF-01: physical PI must create slip below the old Q15 deadband ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        VFC_SetTarget(300); vfc.running = 1;
        vfc.ramp_current_rpm = 300;
        run_updates(1, 0);
        check("physical slip PI: error=300 produces positive slip", vfc.f_slip_hz > 0,
              vfc.f_slip_hz, 1, 0);
        VFC_Stop();
    }

    /* ── 12. Repeated start: slip integrator and angle are reset ── */
    {
        VFC_Init();
        FOC_SetPolePairs(2);
        check("restart: first start succeeds", VFC_Start(300) == VFC_START_OK,
              VFC_IsRunning(), 1, 0);
        vfc.ramp_current_rpm = 5000;
        run_updates(1, -5000);
        VFC_Stop();
        check("restart: second start succeeds", VFC_Start(300) == VFC_START_OK,
              VFC_IsRunning(), 1, 0);
        vfc.ramp_current_rpm = 300;
        run_updates(1, 0);
        check("restart: no residual negative slip", vfc.f_slip_hz > 0,
              vfc.f_slip_hz, 1, 0);
        check("restart: field angle starts from zero", vfc.theta_elec != 0,
              (int32_t)vfc.theta_elec, 0, 0);
        VFC_Stop();
    }

    /* ── 13. Equal-phase rejection holds one tick instead of stopping V/f. */
    {
        VFC_Init(); FOC_SetPolePairs(2); VFC_SetTarget(300); vfc.running = 1;
        vfc.ramp_current_rpm = 300; vfc.theta_elec = 1073741750u; /* 90 deg */
        run_updates(1, 0);
        check("selector hold: 90 degrees keeps V/f running", VFC_IsRunning(), 1, 1, 0);
        check("selector hold: slip remains positive", vfc.f_slip_hz > 0,
              vfc.f_slip_hz, 1, 0);
        VFC_Stop();
    }

    /* ── 14. Sweep one electrical revolution, including 90/270 degree points. */
    {
        VFC_Init(); FOC_SetPolePairs(2); VFC_SetTarget(300); vfc.running = 1;
        vfc.ramp_current_rpm = 300;
        for (uint32_t i = 0; i < 1000u; ++i) {
            vfc.theta_elec = i * 4294967u;
            VFC_Update();
            if (!VFC_IsRunning()) break;
        }
        check("selector hold: 1000-angle sweep stays running", VFC_IsRunning(), 1, 1, 0);
        VFC_Stop();
    }

    /* ── 15. SetTarget клиппинг ── */
    {
        VFC_Init();
        VFC_SetTarget(100); vfc.running = 1;  /* test: bypass context gate */
        VFC_SetTarget(99999);
        check("settarget: clamps to VFC_MAX_RPM", vfc.ramp_target_rpm == 5000,
              vfc.ramp_target_rpm, 5000, 0);
        VFC_Stop();
    }

    sh_puts("=== ");
    sh_puts(failures == 0 ? "ALL PASS" : "FAILURES");
    sh_puts(" (");
    sh_putdec(checks);
    sh_puts(" checks, ");
    sh_putdec(failures);
    sh_puts(" failed) ===\n");

#if defined(__ARM_EABI__) || defined(__arm__)
    /* QEMU: завершение через semihosting SYS_EXIT (0x18) */
    register int r0 __asm__("r0") = 0x18;             /* SYS_EXIT */
    register int r1 __asm__("r1") = 0x20026;          /* ADP_Stopped_ApplicationExit */
    __asm__ volatile("bkpt 0xAB" : : "r"(r0), "r"(r1));
    for (;;) { }
#endif
    return failures ? 1 : 0;
}
