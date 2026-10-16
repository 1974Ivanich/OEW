"""HWT: параметры двигателя на цели — шаги 1 и 4 паспорта, машинно.

Паспорт (`docs/MOTOR_PARAM_BASELINE_20261014.md`) оставляет два факта, которые
обычно сверяют глазами в сеансе отладки:

* **шаг 1** — число пар полюсов на цели равно шильдиковым 3 (шильдик: «6 полюсов»),
  и оба места, где прошивка его хранит, согласованы;
* **шаг 4** — обе половины критерия прямого старта: `Tr` **измерен** (бит
  `AT_VALID_TR` без `AT_VALID_TR_MANUAL`: число, введённое оператором через
  `mp=`, угол поля не задаёт) и достигает порога, а канал энкодера без ошибки
  (`ENC_GetError() == 0`); иначе `Foc_Start()` уходит в open-loop V/f с
  handoff-гейтом.

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
* `enc_error` (`src/encoder.c:50`) — байт состояния канала, который возвращает
  `ENC_GetError()` (`src/encoder.c:226-228`) и читает `FOC_Start` вместе с `Tr`
  (`src/foc.c:643`). Читается именно байтом (`byte_at_symbol`), а не словом: за
  ним в памяти идут счётчики захватов TIM2, и 32-битное чтение вернуло бы их
  вместе с состоянием. Размер символа (1 байт) сверяется с ELF — иначе
  проверка читала бы соседние объекты и выносила вердикт по чужим данным.

Сценарий только читает: ни записей, ни инъекций, HV не требуется. Он НЕ
заменяет измерений: `Tr` на этой плате должен прийти от `rr`/`noload`
(паспорт, шаги 3-4), и вердикт ниже — это проверка того, чем цель уже
располагает, а не новая измерительная процедура.

Режим кампании. `HW_START_PATH_READY` — единственная проверка проекта,
объявленная снимаемой (`waivable=True`): пока кампания идёт через V/f, отказ
`VF_CAMPAIGN` из `tests/target/waivers.json` оставляет прогон в коде 0, но
вердикт сценария становится **WAIVED**, а не PASS, и попадает в result.json,
junit.xml и консоль вместе с причиной и компенсацией. Записи без
причины/компенсации, с маской вместо имени, с опечаткой в поле или дублем —
ERROR до обращения к плате. Как только `Tr` измерена (шаг 4), проверка
проходит сама, и запись печатается как «не понадобилась» — иначе говоря,
требование снять её из реестра. `HW_MOTOR_PAIRS` снять нельзя: `p` —
шильдиковый факт, а не невыполненный шаг. Половина энкодера тоже не
снимается: ошибка канала (`enc_error != 0`) — это неисправность, а не
невыполненный шаг, и кампания через V/f её не компенсирует. Запись,
адресованная этой проверке, не применяется и попадает в `waivers.not_applied`
(«не объявлена waivable»), а прогон остаётся FAIL.
"""

from __future__ import annotations

from hwt_stm32g4 import (AT_MOTOR_PARAM_OFFSETS, AT_VALID_TR, AT_VALID_TR_MANUAL,
                         ENC_ERROR_NONE, FOC_POLE_PAIRS_NAMEPLATE,
                         FOC_SLIP_POLICY_MIN_TR_US)
from hwt_target import case

# Контрольная точка — символ, который в этой сборке РЕАЛЬНО исполняется.
# CLI_ProcessLine при -Os инлайнится в вызывающего: символ в ELF есть, а точка
# по нему не срабатывает никогда (проверено на цели: UART_ReadLine → T05 сразу,
# CLI_ProcessLine → тишина до предела времени). См. docs/HWT_DDTT.md.
CHECKPOINT = "UART_ReadLine"
MOTOR_PARAMS = "g_motor_params"
# Символ состояния энкодера (src/encoder.c:50): байт, который возвращает
# ENC_GetError() и читает Foc_Start. Литерал, а не импорт: метаданные
# контракта обязаны читаться без исполнения кода (DDTT-6.1-2), а равенство
# этого имени прошивке проверяет tests/test_hwt_motor_param_gate.py.
ENC_ERROR = "enc_error"


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
      contracts=(CHECKPOINT, MOTOR_PARAMS, ENC_ERROR),
      description="Обе половины критерия шага 4: Tr достигает порога прямого "
                  "encoder-старта и канал энкодера без ошибки "
                  "(ENC_GetError() == 0); иначе старт идёт через V/f с handoff")
def hw_start_path_ready(t):
    t.reach(CHECKPOINT)

    tr_us = t.word_at_symbol(MOTOR_PARAMS, AT_MOTOR_PARAM_OFFSETS["Tr_rotor_us"])
    mask = t.word_at_symbol(MOTOR_PARAMS, AT_MOTOR_PARAM_OFFSETS["measured_mask"])
    tr_manual = bool(mask & AT_VALID_TR_MANUAL)
    tr_measured = bool(mask & AT_VALID_TR) and not tr_manual
    origin = ("замер" if tr_measured else
              "введён оператором (mp=)" if tr_manual else "не отмечено")
    # Прямой encoder-старт открывает FOC_Start() только при ИЗМЕРЕННОМ Tr
    # (src/foc.c: ENC_GetError() == 0 и FocSlipPolicy_TrUsable(Tr, mask)), а
    # TrUsable — это порог И происхождение: число, введённое оператором через
    # mp=, угол поля не задаёт, потому что RUN — это slip-режим, а slip из
    # введённого числа давал ошибку угла (стенд 15.10: ±1 А по моменту).
    direct = (tr_us >= FOC_SLIP_POLICY_MIN_TR_US) and tr_measured
    # Единственная снимаемая проверка: кампанию можно вести через V/f, пока
    # Rr/Lm не измерены, — тогда гейт не валит прогон, а показывается как
    # WAIVED с записью VF_CAMPAIGN (tests/target/waivers.json). Пары полюсов
    # снять нельзя ничем: неверная p — это не «шаг не выполнен», а другая
    # машина в модели, при любом способе старта.
    t.check("Tr достигает порога прямого encoder-старта", direct, True,
            waivable=True,
            detail=f"Tr={tr_us} мкс, порог {FOC_SLIP_POLICY_MIN_TR_US} мкс, "
                   f"происхождение Tr: {origin} (mask=0x{mask:08X}) — "
                   "FOC_SLIP_POLICY_MIN_TR_US (src/foc_slip_policy.h:8), "
                   "сравнение включительное (src/foc_slip_policy.c:10); "
                   "FocSlipPolicy_TrUsable() = порог И замер (AT_VALID_TR без "
                   "AT_VALID_TR_MANUAL, src/autotune.h); ветка старта — "
                   "src/foc.c:640-680")

    # Вторая половина критерия шага 4 — и она НЕ снимается отказом: ошибка
    # канала — это неисправность, а не невыполненный шаг, и кампания через
    # V/f её не компенсирует. Читается байтом: за enc_error в памяти идут
    # счётчики захватов TIM2, и слово вернуло бы их вместе с состоянием.
    enc_error = t.byte_at_symbol(ENC_ERROR)
    enc_ok = enc_error == ENC_ERROR_NONE
    t.check("ENC_GetError() == 0: канал энкодера исправен", enc_error,
            ENC_ERROR_NONE,
            detail="src/encoder.c:50 — enc_error, возвращается ENC_GetError() "
                   "(src/encoder.c:226-228); 0 = ошибок нет, 0x01 TIMEOUT "
                   "(нет импульсов дольше 5 мс, src/encoder.c:46), 0x02 "
                   "BAD_PERIOD (src/encoder.h:31-32). Foc_Start входит в RUN "
                   "только при ENC_GetError() == 0 И Tr >= порога "
                   "(src/foc.c:640-647); эта половина не снимается записью")

    if direct and enc_ok:
        t.note("путь старта: прямой encoder-старт (FOC_STATE_RUN)")
    else:
        why = ("канал энкодера в ошибке" if not enc_ok else
               "Tr введён оператором" if tr_manual else
               "Tr не отмечен как измеренный" if not tr_measured else
               "Tr ниже порога")
        t.note("путь старта: open-loop V/f → handoff (FOC_STATE_STARTUP) — "
               + why)
    if not direct:
        t.note("это валидное состояние стенда (кампания через V/f), но прямой "
               "старт закрыт: нужны Rr и Lm от rr/noload — паспорт, шаг 4")
    if tr_manual:
        t.note("Tr введён оператором (mp=): угол поля такое число не задаёт "
               "(FocSlipPolicy_TrUsable), поэтому прямой старт закрыт даже при "
               "Tr выше порога — нужен замер: rr → noload → irot")
    if not enc_ok:
        t.note("прямой старт закрыт не «невыполненным шагом», а ошибкой "
               "канала энкодера; запись отказа на эту проверку не действует — "
               "сначала исправить энкодер (docs/encoder_test_STATUS.md)")
    t.note(f"критерий шага 4: Tr={'годен' if direct else 'не годен'} "
           f"({origin}), enc_error={0 if enc_ok else enc_error} — записью "
           "снимается только половина Tr, половина энкодера не снимается")
