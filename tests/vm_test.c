/* Тесты Voltage Manager (ревью VM-01/03/04): anti-windup residual от
 * ИСХОДНОЙ команды (не от Q15-клампа), границы круга, saturating vmax<=0. */
#include "voltage_manager.h"
#include <stdio.h>
#include <stdlib.h>

static int failures = 0;
static int checks = 0;

static void check(const char *name, int cond) {
    checks++;
    if(!cond) { failures++; printf("FAIL: %s\n", name); }
    else printf("ok:   %s\n", name);
}

static void check_i32(const char *name, int32_t got, int32_t exp, int32_t tol) {
    checks++;
    int32_t d = got - exp; if(d < 0) d = -d;
    if(d > tol) { failures++; printf("FAIL: %s: got %ld exp %ld\n", name, (long)got, (long)exp); }
    else printf("ok:   %s (%ld)\n", name, (long)got);
}

int main(void) {
    VoltageManager vm;

    /* 1. VM-01: pre-clamp за Q15 при vmax=32767 — residual от ИСХОДНОЙ команды
     * (старый код: vd_err=0, saturated=false при превышении на 7233 кода). */
    VM_Init(&vm, 32767, VM_PRIORITY_FLUX);
    VM_Update(&vm, 40000, 0);
    check("VM-01a saturated (pre-clamp)", vm.saturated == true);
    check_i32("VM-01a vd_out", vm.vd_out, 32767, 1);
    check_i32("VM-01a vd_err (raw-7233)", vm.vd_err, 32767 - 40000, 1);
    check_i32("VM-01a vq_err", vm.vq_err, 0, 1);

    /* 2. VM-01: pre-clamp + геометрия (vmax=29490) — полный residual. */
    VM_Init(&vm, 29490, VM_PRIORITY_FLUX);
    VM_Update(&vm, 40000, 0);
    check("VM-01b saturated", vm.saturated == true);
    check_i32("VM-01b vd_out", vm.vd_out, 29490, 1);
    check_i32("VM-01b vd_err (raw-10510)", vm.vd_err, 29490 - 40000, 1);

    /* 3. Норма: вектор в круге — нулевые ошибки, limit_scale = 32767. */
    VM_Update(&vm, 10000, 5000);
    check("VM-03a not saturated", vm.saturated == false);
    check_i32("VM-03a vd_err", vm.vd_err, 0, 1);
    check_i32("VM-03a vq_err", vm.vq_err, 0, 1);
    check_i32("VM-03a limit_scale", vm.limit_scale_q15, 32767, 1);

    /* 4. FLUX sat: Vd приоритет — при |Vd|=Vmax весь бюджет у потока, Vq=0. */
    VM_Init(&vm, 29490, VM_PRIORITY_FLUX);
    VM_Update(&vm, 29490, 10000);
    check("VM-04a saturated", vm.saturated == true);
    check_i32("VM-04a vd_out (flux)", vm.vd_out, 29490, 1);
    check_i32("VM-04a vq_out (rem=0)", vm.vq_out, 0, 1);
    check_i32("VM-04a vq_err", vm.vq_err, 0 - 10000, 1);

    /* 5. FLUX sat partial: Vq ограничен до sqrt(Vmax^2 - Vd^2) ≈ 21672. */
    VM_Update(&vm, 20000, 25000);
    check("VM-05a saturated", vm.saturated == true);
    check_i32("VM-05a vd_out", vm.vd_out, 20000, 1);
    check_i32("VM-05a vq_out (circle)", vm.vq_out, 21672, 3);
    check("VM-05a vq_err < 0", vm.vq_err < 0);
    check("VM-05a limit_scale indicator", vm.limit_scale_q15 > 0 && vm.limit_scale_q15 < 32767);

    /* 6. TORQUE sat: Vq приоритет — при |Vq|=Vmax Vd=0. */
    VM_Init(&vm, 29490, VM_PRIORITY_TORQUE);
    VM_Update(&vm, 25000, 29490);
    check("VM-06a saturated", vm.saturated == true);
    check_i32("VM-06a vq_out (torque)", vm.vq_out, 29490, 1);
    check_i32("VM-06a vd_out (rem=0)", vm.vd_out, 0, 1);
    check_i32("VM-06a vd_err", vm.vd_err, 0 - 25000, 1);

    /* 7. VM-04: vmax<=0 + INT32_MIN — saturating residual, не UB. */
    VM_Init(&vm, 0, VM_PRIORITY_FLUX);
    VM_Update(&vm, INT32_MIN, INT32_MIN);
    check("VM-07 saturated", vm.saturated == true);
    check_i32("VM-07 vd_err sat", vm.vd_err, INT32_MAX, 0);
    check_i32("VM-07 vq_err sat", vm.vq_err, INT32_MAX, 0);

    printf("VM: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
