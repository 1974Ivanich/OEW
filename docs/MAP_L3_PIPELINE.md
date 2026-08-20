# L3 mapcap pipeline

## Назначение

L3 связывает service-only map capture с измеренной картой тока. Путь намеренно разделён на четыре стадии:

```text
mapcap arm/run
      ↓
MapCaptureRecord ring
      ↓  mapcap build=<profile>
MapBuilder_AddRecord()
      ↓  все 6×2 rows квалифицированы
MapBuilder_Finalize()
      ↓
CurrentMap_LoadMeasured()
      ↓
MAP_READY / CurrentMap_IsReady()
```

Производственная сборка остаётся **default-deny**. Макросы `OEW_MAP_CAPTURE` и `OEW_MAP_L3` по умолчанию равны нулю, а generic `MapCaptureProfile_BuildRequest()` и `MapCaptureProfile_BuildQualification()` возвращают `false`. До появления board-specific reviewed profile силовой мост не получает разрешение, а карта не может перейти в `MAP_READY`.

## Контракт board-qualified profile

Профиль должен быть скомпилированным и неизменяемым. Он обязан поставлять полный набор из двенадцати запросов для шести секторов и двух окон, а также единую `MapBuilderQualification` с:

- `OewMapIdentity`, совпадающей с активными board revision, PWM frequency, TIM1 ARR и ADC trigger revision;
- `OewPwmRegion` для каждой строки, включая scope-approved modulation bounds и `min_margin_ticks`;
- `CurrentReconEntry` для каждой строки с двумя независимыми фазами и измеренными коэффициентами;
- измеренной стартовой точкой и ненулевым `startup_hold_cycles`;
- минимальным числом записей на каждую строку.

Профиль не должен принимать CCR, sector, window или коэффициенты из UART. UART выбирает только идентификатор заранее проверенного профиля.

## Сборка нескольких capture-сессий

Одна capture-сессия соответствует одному profile request и обычно одной строке sector/window. Команда `mapcap build=<profile>` после завершения сессии переносит все записи из ring в `MapBuilder`. Если карта ещё неполна, builder сохраняет накопленные строки; следующая завершённая сессия с тем же profile продолжает сборку. Смена profile, изменение live identity, повтор sequence, mismatch CCR/TIM8, несовпадение trigger или выход измеренного modulation vector за qualified region сбрасывают builder и не загружают карту.

Запись принимается только при `MAP_CAPTURE_OK` и `ADC_FRAME_WINDOW_INVALID`. Это ожидаемый статус service capture: незаверенная карта не может притвориться control-valid frame.

## Условия загрузки

Перед `CurrentMap_LoadMeasured()` проверяются отсутствие active capture, FOC, V/f и Auto-Tune, выключенный PWM и ADC injected master, а также отсутствие защёлкнутой защиты. Сам loader дополнительно проверяет CRC, identity, все reconstruction rows, регионы, startup context и overlap/coverage. Только успешное прохождение loader делает `CurrentMap_IsReady()` истинным.

Пример commissioning-сборки:

```bash
make EXTRA_CFLAGS='-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7'
```

На штатной generic ветке ожидается блокировка:

```text
@MC:ARM:BLOCKED:PROFILE
@MAP:BUILD:BLOCKED:PROFILE
```

Это не ошибка пайплайна, а требуемая защита до board-qualified commissioning.
