# РЕВИЗИЯ mapcap: чем доказана связь `sample ↔ sector/window ↔ CCR ↔ ADC frame`

ПК-2, 20.09.2026 · docs-only, прошивка не изменялась · все утверждения — со ссылками `file:line`.

Вопрос ревизии: даёт ли существующая sector/window-синхронизация **доказуемую** дополнительную
информацию о фазной привязке, которой не было в статическом rev7? Сначала это надо установить
по коду, иначе критерии ТЗ будут проверять не тот объект.

## 1. Что представляет собой контур сегодня

```text
CLI (main.c:523-560)
  mcarm=<profile_id>     MapCaptureProfile_BuildRequest(id, ++capture_id, &r) → MapCapture_Arm(&r)
                         печать @MC:ARM:cap=…:rc=…:offsets_valid=…:inj_start_rc=…
  mapcap run             MapCapture_Run()  → @MC:RUN:rc=
  mapcap drain           MapCapture_ConsumeRecord() → @MC:REC:… + @MC:DRAIN:records=
  mapcap identity        @MAP:IDENTITY:board/pwm/arr/trig/off/dt/adc_clk/sample_x2/res/acs/ccs
  mapcap build=<profile> mapcap_build_and_load(id)
```

Запрос **не** приходит с UART: он собирается из прошивной allow-list
(`src/map_capture_profiles.c:124-149`, `:305-345`) — «Immutable allow-list: strict exact-match
admission, no UART-provided CCR» (`:180`). Профилей 12 = 6 секторов × 2 окна, id =
`0x424F4152 + sector*2 + window`, и внутри каждого ещё 4 точки сетки (`:187-266`).

```text
сектор  = строгий порядок модуляции (mu>mv>mw …), Q15 8192 → CCR 375/500/625   (:226-233)
окно    = СДВИГ кластера CCR (+16 на max-фазе, −16 на min-фазе), сумма инвариантна (:253-291)
TIM8    = циклический сдвиг CCR TIM1 — «drives the opposite ends of the same open-winding
          phases» (:293-303)
```

Путь арма и запуска:

```text
MapCapture_Arm      (src/map_capture.c:150-184)
   request_is_sane (sector<6, window<2) → interlock/control/fault/offsets → validate_service_pattern
   ADC_SetControlAdmission(false)
   ADC_SetExpectedWindow(request->sector_candidate, request->window_candidate, false)   (:177)
   ADC_InjectedStart()
MapCapture_Run      (src/map_capture.c:186-218) → hooks.start_service_pwm(&g_request)
PWM_ServiceCaptureStart (src/pwm.c:373-408)
   строгая проверка паттерна (sector<6, window<2, trigger_revision == PWM_OEW_ADC_TRIGGER_REVISION)
   → при нарушении PWM_InvalidateSampleContext() + PWM_ENABLE_SERVICE_PATTERN_INVALID
   TIM1->CCR1..3 = pattern->tim1_ccr[0..2];  TIM8->CCR1..3 = pattern->tim8_ccr[0..2]   (:393-398)
   diagnostic_context.valid = false                                                     (:401)
   pwm_publish_context(&diagnostic_context) → ADC_SetExpectedWindow(sector, window, false)
                                                                            (src/pwm.c:61-71, :405)
```

Путь кадра:

```text
JEOS → ADC_InjectedIrq → adc_publish: ++frame_sequence, timestamp_cycles   (src/adc.c:97-107)
     → callbacks (main.c:167-174): adc_dispatch_get_frame=ADC_GetLatestFrame (seqlock)
                                  adc_dispatch_capture_frame=MapCapture_OnAdcFrame
     → MapCapture_OnAdcFrame(frame)                                        (src/map_capture.c:226-269)
        принимает кадр ТОЛЬКО если frame->status == ADC_FRAME_WINDOW_INVALID (:115)
        затем лимиты I1/I2/VBUS и запись в ring (64 записи)
     → per-frame снапшот CCR: cap_snapshot()                               (src/map_capture_port.c:210-229)
```

## 2. Что ПРОВЕРЕНО, а что ЗАЯВЛЕНО

| связь | механизм в коде | вердикт |
|---|---|---|
| `sample ↔ CCR` | `cap_snapshot` читает **живые** `TIM1->CCR1..3`, `TIM8->CCR1..3`, `TIM1->ARR`, `TIM1->BDTR` (port:213-225) в колбэке кадра; `snapshot_matches_request` требует равенства запросу, иначе сессия терминально падает (cap:93-107, :233-238) | **ПРОВЕРЕНО** — это показание регистров, а не метка (при условии статического вектора, см. §4) |
| `sample ↔ sector/window` | метка = кандидат из запроса: `ADC_SetExpectedWindow(candidate, candidate, false)` (cap:177) → `frame.tim1_sector/sample_window` (adc.c:122-123, :391-392, :546-547); проверка «метка == кандидат» (cap:118-123) сравнивает утверждение с запросом | **ЗАЯВЛЕНО (label only)**: «`sector_candidate; /* diagnostic label only; no control admission */`» (map_capture.h:78) |
| `sample ↔ timing (TRGO → апертура)` | `trigger_offset_ticks = 0` **принудительно**: «not measurable from timer registers (**scope qualification required**). It is deliberately kept 0 until the timing-verification stage; a nonzero value would otherwise mislead the map builder about physical timing» (port:220-224); в профиле `MAP_CAPTURE_BOARD_OFFSET 0 /* scope-qualified stage */` (profiles:212) | **НЕ УСТАНОВЛЕНО** — прямо признано в коде |
| `sample ↔ trigger identity` | `trigger_revision` = константа `PWM_OEW_ADC_TRIGGER_REVISION` (pwm.c:30, port:227); несоответствие → `MAP_CAPTURE_TRIGGER_MISMATCH (-17)` (cap.h:53) | **ПРОВЕРЕНО** как идентичность конфигурации; это **не** per-sample счётчик |

Дополнительно: `window` в комментарии шапки описан как «physical, scope-verified timing window»
(map_capture.h:79), тогда как в board-профиле это **сдвиг CCR-кластера** (profiles:253-291).
Семантика метки и её реализация расходятся — при формулировке критериев ТЗ это надо развести.

## 3. Что реально несёт стендовый лог сегодня

```text
@MC:REC:cap=…:seq=…:raw_i1=…:raw_i2=…:raw_ct=…:raw_vbus=…:i1=…:i2=…:vbus=…
        :ccr1=%u,%u,%u:ccr8=%u,%u,%u:arr=%u:trig=%lu:status=%d:fault=%d   (main.c:530)
```

```text
ЕСТЬ:  capture_id, sequence, raw_i1/i2/ct/vbus, i1/i2/vbus, живые CCR обоих таймеров, ARR,
       trigger_revision, status, fault_reason
НЕТ:   sector, window, timestamp_cycles, счётчик триггеров/фреймов на запись
```

`AdcFrame` содержит `tim1_sector`, `sample_window`, `timestamp_cycles` (adc.h:54-56) — но **в UART
не выводятся**. Следствие: **сегодняшний лог не может быть использован для проверки
sector/window-зависимой сигнатуры вообще** — даже если она физически есть. Это gap транспорта
доказательства, а не измерительного тракта.

## 4. Ответ на вопрос о «sec/win от команды против sample от следующего состояния»

* Дрейф метки **внутри сессии исключён**: `ADC_SetExpectedWindow` вызывается один раз на сессию
  (арм/старт) и переписывается только при инвалидировании; вектор на весь burst статичен
  (`PWM_ServiceCaptureStart` пишет CCR один раз, `:393-398`).
* Реальный риск иной и он материален:
  1. **Положение апертуры внутри периода не доказано** — `trigger_offset_ticks` принудительно 0,
     в коде прямо написано «scope qualification required» (port:220-224).
  2. Апертура широкая: `SMPR=111 → 640.5 такта` при ADC-клоке 42.5 МГц (profiles:201-202) ≈ **15.1 мкс
     на канал** при периоде 200 мкс → «момент» выборки занимает ≈7.5 % периода.
  3. Плюс: I1 и I2 берутся **одновременно** (ADC1/ADC2 rank 1 на одном инжектированном триггере,
     src/adc.c:275) — парность (i1,i2) внутри одной апертуры обеспечена.
* Итог: формулировать риск надо не как «sample попал в следующий вектор», а как
  **«неизвестно, где именно внутри периода лежит апертура и нет ли наложения на переключения»**.

## 5. Что нужно до эксперимента на конвенцию (предложение, не ТЗ)

```text
1. В лог: sector, window, timestamp_cycles, признак номера фрейма в сессии.
   Правка печати @MC:REC — изменение прошивки, поэтому отдельным ТЗ (не «заодно»).
2. Установить TRGO → апертура. Осциллограф в проекте не авторитетен (G0 v4 waiver),
   поэтому метод — sweep: менять SMPR/источник триггера/значение offset в утверждённом
   профиле и искать самосогласованность сигнатуры; результат фиксировать как измеренный
   offset в identity.
3. Заранее зарегистрировать критерий идентифицируемости: если два разных физических фазных
   состояния дают одну и ту же наблюдаемую пару (I1,I2) → PHASE CONVENTION = NOT IDENTIFIABLE
   (никакого выбора «наиболее похожей» фазы).
4. Развести семантику «окна»: сдвиг CCR-кластера (профиль) vs временное окно выборки (шапка).
```

## 6. Вывод ревизии

```text
МЕХАНИЗМ sector/window существует и протянут от профиля до кадра.
ДОКАЗАНО:   идентичность конфигурации (trigger_revision), соответствие ЖИВЫХ CCR запросу.
НЕ ДОКАЗАНО: (a) положение апертуры внутри периода (offset=0, «scope qualification required»);
             (b) физическая корректность метки sector/window (label only, тавтологическая проверка);
             (c) доступность этих полей в стендовом логе (в @MC:REC их нет).
⇒ Существующая sector/window-синхронизация сегодня НЕ даёт доказуемой дополнительной информации
  о фазной привязке. Сначала ТЗ на §5.1-5.4, затем GO на bench.
```

Никаких изменений прошивки, ветки-исполнителя или эксперимента этой ревизией не производилось.
