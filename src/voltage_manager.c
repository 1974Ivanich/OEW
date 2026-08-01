#include "voltage_manager.h"
#include "cordic_math.h"

/*
 * Voltage Manager — реализация.
 *
 * Все вычисления в Q15. Никаких mV, никаких ссылок на PI/FW/UART.
 *
 * Форматы данных:
 *   vd_cmd, vq_cmd, v_max_q15  — Q15 ([-32768, 32767])
 *   CORDIC_Modulus: вход q1.31 (Q15_TO_Q31), выход q1.31 (Q31_TO_Q15)
 *   CORDIC_Sqrt:    вход q1.31 (Q15_TO_Q31), выход q1.31 (Q31_TO_Q15)
 *
 * Ограничение вектора до допустимой области напряжений в плоскости (Vd, Vq).
 * Текущая реализация: круг радиуса v_max_q15 (линейный режим PWM).
 * При переходе к overmodulation / шестиступенчатому режиму / OEW-специфичным
 * ограничениям форма области может измениться (круг → эллипс → шестиугольник),
 * но API останется прежним.
 * При насыщении применяется стратегия приоритета:
 *   FLUX   — Vd не трогаем, Vq = clamp(vq_cmd, ±sqrt(Vmax² - Vd²))
 *   TORQUE — Vq не трогаем, Vd = clamp(vd_cmd, ±sqrt(Vmax² - Vq²))
 *
 * Anti-windup: vd_err/vq_err выдаются наружу — PI сам решает как их использовать.
 */

#ifndef CLAMP
#define CLAMP(x, lo, hi) (((x) < (lo)) ? (lo) : (((x) > (hi)) ? (hi) : (x)))
#endif

/* Q15 ↔ Q1.31 преобразования форматов.
 * int64_t cast — гарантия отсутствия UB при отрицательных значениях. */
#define Q15_TO_Q31(x) ((int32_t)((int64_t)(x) * 65536))
#define Q31_TO_Q15(x) ((int32_t)((x) >> 16))

/* sqrt(x_q15) → результат в Q15, через аппаратный CORDIC.
 * x_q15 ∈ [0, 32767] → q1.31 через Q15_TO_Q31.
 * CORDIC_Sqrt возвращает q1.31 → Q31_TO_Q15 даёт Q15.
 * Нижняя граница: отрицательные входы (округление) → 0.
 * Верхняя граница: clamp до 32767 — защита от переполнения q1.31. */
static int32_t sqrt_q15(int32_t x_q15) {
    if (x_q15 <= 0) return 0;
    if (x_q15 > 32767) x_q15 = 32767;
    int32_t x_q31 = Q15_TO_Q31(x_q15);
    return Q31_TO_Q15(CORDIC_Sqrt(x_q31));
}

void VM_Init(VoltageManager *vm, int32_t v_max_q15, VMPriority prio) {
    vm->v_max_q15 = CLAMP(v_max_q15, 0, 32767);
    vm->priority = prio;
    vm->vd_out = 0;
    vm->vq_out = 0;
    vm->vd_err = 0;
    vm->vq_err = 0;
    vm->saturated = 0;
    vm->limit_scale_q15 = 32767;  /* 32767 — нет ограничения */
}

void VM_SetVmax(VoltageManager *vm, int32_t v_max_q15) {
    vm->v_max_q15 = CLAMP(v_max_q15, 0, 32767);
}

void VM_SetPriority(VoltageManager *vm, VMPriority prio) {
    vm->priority = prio;
}

int32_t VM_GetVmax(const VoltageManager *vm) {
    return vm->v_max_q15;
}

int32_t VM_GetLimitScale(const VoltageManager *vm) {
    return vm->limit_scale_q15;
}

void VM_Update(VoltageManager *vm, int32_t vd_cmd, int32_t vq_cmd) {
    int32_t vmax = vm->v_max_q15;
    if (vmax <= 0) {
        vm->vd_out = 0;
        vm->vq_out = 0;
        vm->vd_err = -vd_cmd;
        vm->vq_err = -vq_cmd;
        vm->saturated = 1;
        vm->limit_scale_q15 = 0;
        return;
    }

    /* 1. Модуль вектора через CORDIC (аппаратный, q1.31 → Q15). */
    int32_t mod_q31, angle;
    CORDIC_Modulus(Q15_TO_Q31(vd_cmd), Q15_TO_Q31(vq_cmd), &mod_q31, &angle);
    (void)angle;
    int32_t v_mag = Q31_TO_Q15(mod_q31);  /* Q15 */

    /* 2. Нет насыщения — пропустить */
    if (v_mag <= vmax) {
        vm->vd_out = vd_cmd;
        vm->vq_out = vq_cmd;
        vm->vd_err = 0;
        vm->vq_err = 0;
        vm->saturated = 0;
        vm->limit_scale_q15 = 32767;  /* 32767 — нет ограничения */
        return;
    }

    /* 3. Насыщение — применить приоритет.
     * vmax² в Q15: (vmax * vmax) >> 15 */
    vm->saturated = 1;
    vm->limit_scale_q15 = (int32_t)(((int64_t)vmax << 15) / v_mag);
    int32_t vmax2 = (int32_t)(((int64_t)vmax * vmax) >> 15);

    if (vm->priority == VM_PRIORITY_FLUX) {
        /* FLUX priority: Vd получает весь доступный вектор, Vq ограничивается.
         * Если |Vd| > Vmax → Vd = ±Vmax, Vq = 0 (весь вектор — поток).
         * Иначе Vd сохраняется, Vq = clamp(vq, ±sqrt(Vmax² - Vd²)). */
        vm->vd_out = CLAMP(vd_cmd, -vmax, vmax);
        int32_t vd2 = (int32_t)(((int64_t)vm->vd_out * vm->vd_out) >> 15);
        int32_t vq_max = sqrt_q15(vmax2 - vd2);
        vm->vq_out = CLAMP(vq_cmd, -vq_max, vq_max);
    } else {
        /* TORQUE priority: Vq получает весь доступный вектор, Vd ограничивается.
         * Если |Vq| > Vmax → Vq = ±Vmax, Vd = 0 (весь вектор — момент).
         * Иначе Vq сохраняется, Vd = clamp(vd, ±sqrt(Vmax² - Vq²)). */
        vm->vq_out = CLAMP(vq_cmd, -vmax, vmax);
        int32_t vq2 = (int32_t)(((int64_t)vm->vq_out * vm->vq_out) >> 15);
        int32_t vd_max = sqrt_q15(vmax2 - vq2);
        vm->vd_out = CLAMP(vd_cmd, -vd_max, vd_max);
    }

    /* 4. Ошибки насыщения для внешнего anti-windup */
    vm->vd_err = vm->vd_out - vd_cmd;
    vm->vq_err = vm->vq_out - vq_cmd;
}
