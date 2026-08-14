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

/* Ревью VM-01/04: saturating разность int32 через int64 — residual для
 * anti-windup может выйти за Q15 при сверх-Q15 командах PI, а
 * -INT32_MIN в int32 был бы signed overflow (UB). */
static inline int32_t sat_sub_i32(int32_t a, int32_t b) {
    int64_t d = (int64_t)a - (int64_t)b;
    if (d > INT32_MAX) return INT32_MAX;
    if (d < INT32_MIN) return INT32_MIN;
    return (int32_t)d;
}

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
    /* Ревью Grok п.10: неизвестное значение prio (повреждённая структура)
     * не должно молча трактоваться как TORQUE. */
    vm->priority = (prio == VM_PRIORITY_FLUX) ? VM_PRIORITY_FLUX : VM_PRIORITY_TORQUE;
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
    /* Ревью Grok п.11: валидация, как в VM_Init. */
    vm->priority = (prio == VM_PRIORITY_FLUX) ? VM_PRIORITY_FLUX : VM_PRIORITY_TORQUE;
}

int32_t VM_GetVmax(const VoltageManager *vm) {
    return vm->v_max_q15;
}

int32_t VM_GetLimitScale(const VoltageManager *vm) {
    return vm->limit_scale_q15;
}

void VM_Update(VoltageManager *vm, int32_t vd_cmd, int32_t vq_cmd) {
    /* Ревью VM-01: исходные команды НЕЛЬЗЯ затирать до расчёта residual.
     * PI намеренно пропускает p_term насквозь (может выйти за Q15), а VM —
     * конечный ограничитель: vd_err/vq_err обязаны отражать РЕАЛЬНОЕ
     * превышение (vd_out − vd_raw), иначе PI_BackCalculation не разгрузит
     * интегратор. Крайний случай: vd_cmd=40000, vq=0, vmax=32767 — старый
     * код возвращал vd_err=0, saturated=false при превышении на 7233 кода. */
    int32_t vd_raw = vd_cmd;
    int32_t vq_raw = vq_cmd;
    /* Safety-вход для Q15_TO_Q31/CORDIC: кламп НЕ влияет на residual. */
    int32_t vd_safe = CLAMP(vd_raw, -32768, 32767);
    int32_t vq_safe = CLAMP(vq_raw, -32768, 32767);
    int32_t vmax = vm->v_max_q15;
    if (vmax <= 0) {
        vm->vd_out = 0;
        vm->vq_out = 0;
        /* Ревью VM-04: saturating residual — в int32 -INT32_MIN был бы UB. */
        vm->vd_err = sat_sub_i32(0, vd_raw);
        vm->vq_err = sat_sub_i32(0, vq_raw);
        vm->saturated = 1;
        vm->limit_scale_q15 = 0;
        return;
    }

    /* 1. Модуль вектора через CORDIC (аппаратный, q1.31 → Q15). */
    int32_t mod_q31, angle;
    CORDIC_Modulus(Q15_TO_Q31(vd_safe), Q15_TO_Q31(vq_safe), &mod_q31, &angle);
    (void)angle;
    int32_t v_mag = Q31_TO_Q15(mod_q31);  /* Q15 */

    /* 2. Нет геометрического насыщения — пропустить.
     * Ревью VM-01: saturated также при pre-clamp за Q15 (residual ≠ 0),
     * чтобы PI_BackCalculation сработал и по этому превышению. */
    if (v_mag <= vmax) {
        vm->vd_out = vd_safe;
        vm->vq_out = vq_safe;
        vm->vd_err = sat_sub_i32(vd_safe, vd_raw);
        vm->vq_err = sat_sub_i32(vq_safe, vq_raw);
        vm->saturated = (vd_safe != vd_raw) || (vq_safe != vq_raw);
        vm->limit_scale_q15 = 32767;  /* 32767 — нет ограничения */
        return;
    }

    /* 3. Насыщение — применить приоритет.
     * vmax² в Q15: (vmax * vmax) >> 15 */
    vm->saturated = 1;
    /* limit_scale_q15 = Vmax/|Vcmd| — ИНДИКАТОР степени превышения лимита
     * для FW, НЕ коэффициент применённого масштабирования: приоритетный
     * режим (FLUX/TORQUE) ограничивает компоненты раздельно (шаг 3),
     * а не масштабирует оба. 32767 = ограничения нет. */
    vm->limit_scale_q15 = (int32_t)(((int64_t)vmax << 15) / v_mag);
    int32_t vmax2 = (int32_t)(((int64_t)vmax * vmax) >> 15);

    if (vm->priority == VM_PRIORITY_FLUX) {
        /* FLUX priority: Vd получает весь доступный вектор, Vq ограничивается.
         * Если |Vd| > Vmax → Vd = ±Vmax, Vq = 0 (весь вектор — поток).
         * Иначе Vd сохраняется, Vq = clamp(vq, ±sqrt(Vmax² - Vd²)). */
        vm->vd_out = CLAMP(vd_safe, -vmax, vmax);
        int32_t vd2 = (int32_t)(((int64_t)vm->vd_out * vm->vd_out) >> 15);
        int32_t rem = vmax2 - vd2;   /* Ревью Grok п.3: явный clamp остатка —
                                        vd2 может слегка превысить vmax2 при
                                        округлениях (vd_out=±vmax) */
        if (rem < 0) rem = 0;
        int32_t vq_max = sqrt_q15(rem);
        vm->vq_out = CLAMP(vq_safe, -vq_max, vq_max);
    } else {
        /* TORQUE priority: Vq получает весь доступный вектор, Vd ограничивается.
         * Если |Vq| > Vmax → Vq = ±Vmax, Vd = 0 (весь вектор — момент).
         * Иначе Vq сохраняется, Vd = clamp(vd, ±sqrt(Vmax² - Vq²)). */
        vm->vq_out = CLAMP(vq_safe, -vmax, vmax);
        int32_t vq2 = (int32_t)(((int64_t)vm->vq_out * vm->vq_out) >> 15);
        int32_t rem = vmax2 - vq2;   /* Ревью Grok п.3: clamp остатка */
        if (rem < 0) rem = 0;
        int32_t vd_max = sqrt_q15(rem);
        vm->vd_out = CLAMP(vd_safe, -vd_max, vd_max);
    }

    /* 4. Ошибки насыщения для внешнего anti-windup — от ИСХОДНЫХ команд
     * (VM-01): даже при pre-clamp за Q15 PI получит реальный residual. */
    vm->vd_err = sat_sub_i32(vm->vd_out, vd_raw);
    vm->vq_err = sat_sub_i32(vm->vq_out, vq_raw);
}
