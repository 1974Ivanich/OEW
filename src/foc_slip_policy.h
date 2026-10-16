#ifndef FOC_SLIP_POLICY_H
#define FOC_SLIP_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* These are FOC-cycle units: 2^32 angle codes per electrical turn, 5 kHz. */
#define FOC_SLIP_POLICY_MIN_TR_US      1000
#define FOC_SLIP_POLICY_MIN_ID_INTERNAL 10
#define FOC_SLIP_POLICY_2PI_INV        159155
#define FOC_SLIP_POLICY_PHASE_PER_HZ   858993
/* Потолок slip в модели угла поля.
 *
 * 5 Гц были выставлены, когда Tr считался порядка 100 мс: тогда потолок
 * соответствовал Iq/Id ~ 0,3 и физикой не ограничивался. С Tr этого класса
 * машин (десятки мс) он наступает сразу: при Tr = 55 мс потолок 5 Гц
 * достигается уже при Iq/Id = 1,7, то есть slip обрезается в штатном режиме, а
 * обрезанный slip = систематическая ошибка угла поля = ошибка момента.
 *
 * 20 Гц — не «запас на всякий случай», а верхняя граница диапазона, в котором
 * slip для Tr >= 10 мс перестаёт быть малой поправкой: при Tr = 10 мс 20 Гц
 * соответствуют Iq/Id = 1,26. Значение ниже общего ограничителя фазы
 * FOC_MAX_FE_DT (200 Гц, src/foc.c), который и остаётся жёсткой защитой от
 * неразумной частоты; здесь же ограничивается только модель момента.
 *
 * Для V/f open-loop свой потолок (VFC_MAX_SLIP_HZ = 5 Гц, src/vf_control.c):
 * там slip создаёт пусковой ток и не считается из момента, поэтому он не
 * поднимается. */
#define FOC_SLIP_POLICY_MAX_HZ         20
#define FOC_SLIP_POLICY_MAX_DT         ((int32_t)((int64_t)FOC_SLIP_POLICY_MAX_HZ * \
                                                   FOC_SLIP_POLICY_PHASE_PER_HZ))

/* Tr годится для slip: значение не ниже порога осмысленности И получено
 * замером, а не введено оператором через mp=. Введённое число — такой же
 * «неизвестный» Tr, как и ноль: оно не имеет права задавать угол поля, потому
 * что систематическая ошибка Tr = систематическая ошибка угла = ошибка момента
 * (см. docs/MOTOR_PARAM_BASELINE_20261014.md, шаг 4). */
bool FocSlipPolicy_TrUsable(int32_t tr_us, bool tr_from_measurement);

/* Return electrical phase increment due to induction-motor slip. An unknown
 * Tr is a declared encoder-only mode: it returns zero instead of guessing a
 * nominal rotor time constant. `tr_from_measurement` is the caller's statement
 * that Tr came from a measurement, not from an operator's mp= entry: a
 * hand-typed value must never drive the field angle. */
int32_t FocSlipPolicy_ComputeDelta(int32_t tr_us,
                                   bool tr_from_measurement,
                                   int32_t id_internal,
                                   int32_t iq_internal);

#endif
