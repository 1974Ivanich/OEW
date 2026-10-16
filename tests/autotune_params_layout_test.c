/* Контракт layout MotorParams против тех смещений, которыми пользуется HWT.
 *
 * Сценарий `tests/target/hw_motor_params.py` читает поля структуры как
 * «символ + смещение»: DWARF в образ не входит, а RSP-транспорт выражений вида
 * `g_motor_params.Tr_rotor_us` не вычисляет (см. `Target.word_at_symbol`,
 * tools/hwt_target.py). Значит смещение из `tools/hwt_stm32g4.py`
 * (`AT_MOTOR_PARAM_OFFSETS`) обязано совпадать с реальным layout: ошибка здесь
 * не падает, а читает соседнее поле — и сценарий выносит вердикт по чужим
 * данным, то есть врёт правдоподобно. Это худший вид отказа для гейта.
 *
 * Поэтому проверка стоит компилятором, а не разбором чисел в тесте:
 * расхождение ломает сборку — тот же приём, что у инварианта дефолтной
 * индуктивности (`src/foc.c:196-197`). Смещения считает тот же заголовок, из
 * которого собирается прошивка, поэтому проверка не зависит от того, кто и
 * когда правил структуру.
 *
 * Сравнивать есть смысл только с тем, что пишет сценарий: поля, которые гейт не
 * читает, здесь не пинятся — иначе тест пришлось бы править при каждой правке
 * структуры, и он перестал бы что-либо доказывать.
 *
 * Числа слева — те же, что в `tools/hwt_stm32g4.AT_MOTOR_PARAM_OFFSETS`;
 * их равенство питоновской копии проверяет
 * `tests/test_hwt_motor_param_gate.py` (этот файл — источник правды для C,
 * модуль — для сценария, и оба обязаны совпасть).
 *
 * Границы проверки, чтобы не приписывать ей больше, чем она даёт:
 *
 * * assert'ы ставит хост-компилятор, а прошивку собирает arm-none-eabi. Для
 *   запиненных полей это безразлично: все они — ведущие int32 плюс один uint8,
 *   а int32/uint8 выравниваются одинаково и в AAPCS, и в хост-ABI (enum
 *   `AtCurrentChannel`, размер которого мог бы зависеть от флагов сборки,
 *   лежит ниже и на эти смещения не влияет). Если однажды запинены будут
 *   поля после enum — проверку придётся смотреть под целевой компилятор;
 * * проверка ловит сдвиг полей, но не ловит подмену самого гейта: что сценарий
 *   читает те поля, которые нужно, доказывают негативные тесты
 *   (`test_hwt_on_target.py`: p=6, Tr=999, Tr=60) — они меняют память цели и
 *   требуют, чтобы вердикт изменился.
 */
#include <stddef.h>
#include <stdio.h>

#include "autotune.h"

/* src/autotune.h:59-92 — поля идут в порядке: Rs(0), Ls(4), Rr(8), Lm(12),
 * Tr_rotor_us(16), Ke(20), Isat(24), pole_pairs(28, uint8 + 3 байта
 * выравнивания до J_kg_m2_x1e6(32)). */
/* Диагностики этих assert'ов — латиницей намеренно: их печатает компилятор, а
 * консоль оператора на стенде — cp1251, где кириллица из UTF-8 отчёта
 * превращается в мусор (та же причина, по которой hwt.py и hwt_backup.py
 * печатают с errors="replace"). Русское объяснение — в комментарии выше, где
 * кодировка ни при чём: комментарии не печатаются. */
_Static_assert(offsetof(MotorParams, Tr_rotor_us) == 16,
               "Tr_rotor_us moved: update AT_MOTOR_PARAM_OFFSETS in "
               "tools/hwt_stm32g4.py to the same number");
_Static_assert(offsetof(MotorParams, pole_pairs) == 28,
               "pole_pairs moved: update AT_MOTOR_PARAM_OFFSETS in "
               "tools/hwt_stm32g4.py to the same number");
_Static_assert(offsetof(MotorParams, measured_mask) == 36,
               "measured_mask moved: update AT_MOTOR_PARAM_OFFSETS in "
               "tools/hwt_stm32g4.py to the same number");

int main(void)
{
    /* Печатает проверенные числа: в журнале CI должно быть видно, какой layout
     * подтверждён, а не только «PASS». */
    printf("autotune_params_layout_test: PASS "
           "(Tr_rotor_us@%zu, pole_pairs@%zu)\n",
           offsetof(MotorParams, Tr_rotor_us),
           offsetof(MotorParams, pole_pairs));
    return 0;
}
