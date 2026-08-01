#ifndef VOLTAGE_MANAGER_H
#define VOLTAGE_MANAGER_H

#include <stdint.h>
#include <stdbool.h>

/*
 * Voltage Manager — тонкий математический модуль ограничения вектора Vdq.
 *
 * Архитектура: чистая функция (vd, vq, v_max_q15) → (vd_lim, vq_lim, sat).
 * Не знает про PI, FW, UART, скорость — только Q15-математика.
 * Ограничивает вектор до допустимой области напряжений (сейчас — круг,
 * при overmodulation может стать эллипс/шестиугольник, API не изменится).
 *
 * Поток данных:
 *   PI_d → vd ──┐
 *   PI_q → vq ──┤──► VM_Update() ──► vd_lim, vq_lim ──► InvPark ──► PWM
 *                │
 *                └──► vd_err, vq_err ──► PI anti-windup (внешне)
 *
 * Vmax_Q15 вычисляется один раз из Vbus:
 *   v_max_q15 = v_max_mv * 32768 / vdc_mv
 *   где v_max_mv = vdc_mv * margin / 100
 */

typedef enum {
    VM_PRIORITY_FLUX = 0,   /* Vd приоритет — сохранять поток, ограничивать Vq */
    VM_PRIORITY_TORQUE      /* Vq приоритет — сохранять момент, ограничивать Vd */
} VMPriority;

typedef struct {
    /* Параметры */
    int32_t v_max_q15;       /* Лимит модуля Vdq в Q15 (≈0.9*32768 для 90% margin) */
    VMPriority priority;     /* Стратегия приоритета */

    /* Выход (обновляется в VM_Update) */
    int32_t vd_out;          /* Ограниченный Vd */
    int32_t vq_out;          /* Ограниченный Vq */
    int32_t vd_err;          /* Ошибка: vd_out - vd_cmd (для PI anti-windup) */
    int32_t vq_err;          /* Ошибка: vq_out - vq_cmd (для PI anti-windup) */
    bool saturated;          /* true = вектор был ограничен */
    int32_t limit_scale_q15; /* 32767 — нет ограничения, 0..32766 — коэффициент ограничения.
                              * Полезен для телеметрии, FW gain, torque derating. */
} VoltageManager;

/* Инициализация. v_max_q15 — лимит в Q15 (например 29490 для 90% от 32767). */
void VM_Init(VoltageManager *vm, int32_t v_max_q15, VMPriority prio);

/* Обновить Vmax (при изменении Vbus). v_max_q15 = v_max_mv * 32768 / vdc_mv. */
void VM_SetVmax(VoltageManager *vm, int32_t v_max_q15);

/* Сменить стратегию приоритета. */
void VM_SetPriority(VoltageManager *vm, VMPriority prio);

/* Получить текущий Vmax (для FW — единый источник истины). */
int32_t VM_GetVmax(const VoltageManager *vm);

/* Получить коэффициент ограничения из последнего VM_Update.
 * 32767 = нет ограничения, <32767 = насыщение.
 * Используется FW для пропорционального ослабления поля. */
int32_t VM_GetLimitScale(const VoltageManager *vm);

/*
 * Основная функция: ограничивает (vd, vq) до допустимой области напряжений.
 * При насыщении применяет выбранную стратегию приоритета.
 * vd_err/vq_err — для внешнего anti-windup PI (передать в PI_BackCalc).
 * limit_scale_q15 — степень насыщения, для FW и телеметрии. */
void VM_Update(VoltageManager *vm, int32_t vd_cmd, int32_t vq_cmd);

#endif /* VOLTAGE_MANAGER_H */
