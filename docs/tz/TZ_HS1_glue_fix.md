# ТЗ: OEW-HS-1 glue audit — правки P0/P1 (web AI)

**Проект:** OEW Motor Drive — STM32G474RE, CMSIS-only (без HAL), OEW dual-inverter FOC.
**Репозиторий:** github.com/1974Ivanich/OEW (текущий HEAD: 4966486, 14 тестовых целей).
**Роль:** напиши готовые патчи (unified diff или полные фрагменты замены) для пунктов ниже. Код не коммитить — интеграцию и сборку делает интегратор на ПК2.

**Аудит «OEW-HS-1 final glue audit» (по b97868a) проверен по текущему дереву. Статус:**
- **P0-B (FOC_Start (0,0,true) bypass) — УЖЕ ИСПРАВЛЕН** (foc.c:519 `CurrentMap_SelectInitialStartupContext`; admission после публикации контекста; `PWM_Enable() != PWM_ENABLE_OK` с откатом). **НЕ трогать.**
- Остальные пункты актуальны — см. ниже.

---

## P0-A: break-IRQ определены, но НЕ включены (актуально)

Обработчики полные (main.c:66-84 — читают flags, HS1Diag, `PROTECT_LatchFault(PROTECT_FAULT_HARDWARE_BREAK)`, `PWM_Disable`), НО нигде нет `TIM_DIER_BIE` и `NVIC_EnableIRQ(TIM1_BRK_TIM15_IRQn)` / `(TIM8_BRK_IRQn)` — break снимает MOE аппаратно, а software-latch/terminal stop не выполняются (T8 не пройдёт).

**Требуется:**
1. `src/pwm.c` `PWM_Init()` — после конфигурации обоих таймеров (TIM1: BDTR/AF1 на ~243-247, TIM8: ~259-261, перед `TIM8->EGR = TIM_EGR_UG;` на 269) добавить:
   ```c
   TIM1->DIER |= TIM_DIER_BIE;
   TIM8->DIER |= TIM_DIER_BIE;
   ```
2. `main.c` — в блок инициализации (после `HS1Diag_Init();` на 282, рядом с NVIC-настройками на 283-292) добавить:
   ```c
   /* OEW-HS-1 P0-A: break IRQ — terminal stop обязан обогнать TIM6(2)/foreground.
    * Обработчики короткие (latch + PWM_Disable), приоритет 0 как у ADC. */
   NVIC_SetPriority(TIM1_BRK_TIM15_IRQn, 0);
   NVIC_SetPriority(TIM8_BRK_IRQn, 0);
   NVIC_EnableIRQ(TIM1_BRK_TIM15_IRQn);
   NVIC_EnableIRQ(TIM8_BRK_IRQn);
   ```

## P0-C: `at_injected_sync` публикует валидное окно выборки (актуально)

`src/autotune.c:265`: `ADC_SetExpectedWindow(0u, 0u, true);` — raw-диагностика не требует control-window: статус-чек ниже (275-277) отвергает только OVERRUN/JQOVF/DESYNC/NOT_ARMED/JEOS_TIMEOUT, а `WINDOW_INVALID` допускается.

**Требуется:** убрать строку 265 и переписать комментарий (255-264): raw-фреймы со статусом WINDOW_INVALID допустимы для характеризации (control admission/окно — только для FOC по карте секторов). НЕ добавлять SetExpectedWindow никуда в autotune.

## P1-A: стиль очистки UIF (актуально)

`main.c:59`: `TIM1->SR = ~TIM_SR_UIF;` → заменить на `TIM1->SR &= ~TIM_SR_UIF;` (стиль write-zero-to-clear как в TIM6 ISR; все биты SR на G4 — rc_w0, семантика та же).

## P1-B: latch последних BIF-флагов в HS1Diag (актуально)

Обработчики main.c:67-80 читают `flags = TIMx->SR & (BIF|B2IF)`, но `HS1Diag_OnTim*BreakIrq(void)` (hs1_diag.c:54-59) их не принимают; после очистки флагов команда `hs1?` показывает `bif=0` — нет post-injection evidence.

**Требуется:**
1. `src/hs1_diag.h`: прототипы `void HS1Diag_OnTim1BreakIrq(uint32_t flags);` / `HS1Diag_OnTim8BreakIrq(uint32_t flags);`; в `Hs1DiagSnapshot` добавить `uint32_t last_tim1_flags; uint32_t last_tim8_flags;`
2. `src/hs1_diag.c`:
   - `Hs1DiagCounters`: + `last_tim1_flags`, `last_tim8_flags`
   - `hs1_counter_update(uint32_t source, uint32_t flags)`: сохранять flags в соответствующее поле (внутри seqlock)
   - `HS1Diag_Init()`: обнулить
   - `HS1Diag_Read()`: скопировать в snapshot
   - `HS1Diag_FormatLine()`: в строку `@HS1` добавить `l_bif_t1=%u:l_b2if_t1=%u:l_bif_t8=%u:l_b2if_t8=%u:` (биты из `last_tim1_flags`/`last_tim8_flags`)
3. `main.c` (66-84): передать `flags` в обработчики HS1Diag
4. `tests/hs1_diag_test.c` (51-53): обновить вызовы + добавить проверку latched-флагов

## Ограничения
- Только перечисленные правки; ничего вне скоупа не менять (не рефакторить, не переименовывать).
- Не трогать legacy-маскирование ADC IRQ в autotune-командах main.c (временный hard-block, задокументировано).
- Не менять поведение PWM-конфигурации вне добавления BIE.
- Формат ответа: по каждому пункту — файл, точное место (строка), готовый фрагмент/диф.

## Проверки интегратора (не твоя забота)
`make` (0 предупреждений), `make test` (14 прогонов), статические гейты (0 вхождений `ADC_SetExpectedWindow(...,true)` вне map-публикации).
