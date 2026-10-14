"""HWT: параметры двигателя на цели — шаги 1 и 4 паспорта, машинно.

Паспорт (`docs/MOTOR_PARAM_BASELINE_20261014.md`) оставляет два факта, которые
обычно сверяют глазами в сеансе отладки:

* **шаг 1** — число пар полюсов на цели равно шильдиковым 3 (шильдик: «6 полюсов»),
  и оба места, где прошивка его хранит, согласованы;
* **шаг 4** — `Tr` достигает порога прямого encoder-старта, иначе `Foc_Start()`
  уходит в open-loop V/f с handoff-гейтом.

Что читается и почему именно так:

* `pole_pairs` (`src/foc.c:128`) — то самое число, которым умножается
  электрический угол в slip-модели; `FOC_GetPolePairs()` возвращает его же.
  Это не измеряемая величина, а факт с шильдика: 0 или «не прочитано» здесь
  означало бы, что угол считается другой машиной;
* `g_motor_params.pole_pairs` — зеркало для телеметрии и сохранения
  (`main.c:705`). Обязано совпадать с FOC: единый источник истины — FOC
  (`src/foc.c:415-417`). Расхождение — это ровно тот случай, когда `mp=` при
  `a7 = 0` поле не обновил (`src/cli.c:405`), но вернул ноль в `@MP:OK`
  (`src/cli.c:415`);
* `g_motor_params.Tr_rotor_us` — то, что подаётся в
  `FocSlipPolicy_IsMeasuredTr()` (`src/foc.c:644`). DWARF в образ не входит,
  а RSP-транспорт выражений вида `g_motor_params.Tr_rotor_us` не вычисляет,
  поэтому поле адресуется как символ + смещение. Смещение (16) — константа
  `tools/hwt_stm32g4.py`, запиненная контрактом сборки
  `tests/autotune_params_layout_test.c` (`_Static_assert` на `offsetof`): без
  этого сценарий читал бы соседнее поле и выносил вердикт по чужим данным.

Сценарий только читает: ни записей, ни инъекций, HV не требуется. Он НЕ
заменяет измерений: `Tr` на этой плате должен прийти от `rr`/`noload`
(паспорт, шаги 3-4), и вердикт ниже — это проверка того, чем цель уже
располагает, а не новая измерительная процедура.
"""

from __future__ import annotations

from hwt_stm32g4 import (AT_MOTOR_PARAM_OFFSETS, FOC_POLE_PAIRS_NAMEPLATE,
                         FOC_SLIP_POLICY_MIN_TR_US)
from hwt_target import case

# Контрольная точка — символ, который в этой сборке РЕАЛЬНО исполняется.
# CLI_ProcessLine при -Os инлайнится в вызывающего: символ в ELF есть, а точка
# по нему не срабатывает никогда (проверено на цели: UART_ReadLine → T05 сразу,
# CLI_ProcessLine → тишина до предела времени). См. docs/HWT_DDTT.md.
CHECKPOINT = "UART_ReadLine"
MOTOR_PARAMS = "g_motor_params"


@case("HW_MOTOR_PAIRS", timeout_s=20, labels=("params", "no-hv"),
      contracts=(CHECKPOINT, "pole_pairs", MOTOR_PARAMS),
      description="Пары полюсов на цели равны шильдику, и зеркало MotorParams "
                  "не разошлось с FOC")
def hw_motor_pairs(t):
    t.reach(CHECKPOINT)

    foc_pairs = t.word_at_symbol("pole_pairs")
    t.check("FOC: пар полюсов ровно столько, сколько на шильдике", foc_pairs,
            FOC_POLE_PAIRS_NAMEPLATE,
            detail="src/foc.c:128 — FOC_DEFAULT_POLE_PAIRS, src/foc.c:336 — "
                   "FOC_Init; p умножает электрический угол, поэтому 6 вместо 3 "
                   "удваивает θe и выглядит как потеря синхронизма")

    mirror = t.word_at_symbol(MOTOR_PARAMS,
                              AT_MOTOR_PARAM_OFFSETS["pole_pairs"])
    t.check("зеркало g_motor_params.pole_pairs совпадает с FOC", mirror,
            foc_pairs,
            detail="main.c:705 — зеркало восстанавливается после "
                   "Autotune_Init; расхождение = дыра от mp= с a7=0 "
                   "(src/cli.c:405) и в телеметрии не то число, по которому "
                   "считается угол")

    t.note(f"пары полюсов: FOC={foc_pairs}, зеркало={mirror}; p — факт с "
           f"шильдика, а не измерение: бит AT_VALID_PAIRS тут не требуется")


@case("HW_START_PATH_READY", timeout_s=20,
      labels=("params", "start-path", "no-hv"),
      contracts=(CHECKPOINT, MOTOR_PARAMS),
      description="Tr достигает порога прямого encoder-старта; при меньшем "
                  "значении старт идёт через V/f с handoff")
def hw_start_path_ready(t):
    t.reach(CHECKPOINT)

    tr_us = t.word_at_symbol(MOTOR_PARAMS, AT_MOTOR_PARAM_OFFSETS["Tr_rotor_us"])
    direct = tr_us >= FOC_SLIP_POLICY_MIN_TR_US
    t.check("Tr достигает порога прямого encoder-старта", direct, True,
            detail=f"Tr={tr_us} мкс, порог {FOC_SLIP_POLICY_MIN_TR_US} мкс — "
                   "FOC_SLIP_POLICY_MIN_TR_US (src/foc_slip_policy.h:8), "
                   "сравнение включительное (src/foc_slip_policy.c:10); "
                   "ветка старта — src/foc.c:640-647")

    t.note("путь старта: " + ("прямой encoder-старт (FOC_STATE_RUN)"
                              if direct else
                              "open-loop V/f → handoff (FOC_STATE_STARTUP)"))
    if not direct:
        t.note("это валидное состояние стенда (кампания через V/f), но прямой "
               "старт закрыт: нужны Rr и Lm от rr/noload — паспорт, шаг 4")
    t.note("вторая половина критерия шага 4 (ENC_GetError() == 0) здесь не "
           "читается: канал энкодера проверяют отдельно от параметров")
