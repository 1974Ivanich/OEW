# ТЗ: board-qualified профиль MapCapture (energise-кампания)

**Источник:** стендовые сессии 2026-08-23/24 (ПК-1/ПК-3), no-HV. Установлено:
клинический fail-closed блокер (все профильные функции заглушки) — energise-кампания
невозможна до одобренного профиля.
**Характер:** safety-модуль (`src/map_capture_profiles.c/h`) — **только по явному ТЗ** (AGENTS.md §7).
**Затрагиваемые файлы:** `src/map_capture_profiles.c`, `src/map_capture_profiles.h`,
`tests/map_capture_profiles_test.c` (новый), при необходимости `main.c` (только привязка
hook/validate, если требуется). Остальное — ЗАПРЕЩЕНО.
**Разработка значений профиля (этап A) — на стенде с допуском; кодирование (этап B) — после данных.**

---

## 1. Проблема

```
mcarm=0        → @MC:ARM:BLOCKED:PROFILE   (MapCaptureProfile_BuildRequest → false)
mapcap run     → @MC:RUN:rc=-14            (MAP_CAPTURE_NOT_ACTIVE — нет одобренного импульса)
mapcap drain   → @MC:DRAIN:records=0       (буфер пуст — нечему выгружать)
```

Причины (читано в коде): все три функции профиля возвращают `false` намеренно
(fail-closed). Комментарии в коде требуют: «заменить только reviewed immutable
allow-list», «exact equality checks против конечной одобренной таблицы»,
«никогда не принимать произвольные UART-предоставленные CCR».

Без профиля невозможно:
1. получить физический SERVICE-PWM импульс (`mcarm`/`mapcap run`);
2. собрать непустой `raw_records.jsonl` (PC-экспортёр готов и проверен);
3. перейти к L3-квалификации карты (MapBuilder → 12 регионов → recon → MAP_READY).

## 2. Цель

После приёмки ТЗ на стенде (с токоограничением и допуском) можно выполнить:
`mcarm=<profile>` → `mapcap run` → `mapcap drain` → лог → `mapcap_uart_export.py` → первый непустой `raw_records.jsonl`.

**Fail-closed не ослабляется**: без одобренного профиля поведение ровно как сейчас (все функции → false).

## 3. Объект квалификации (структуры)

### 3.1 `MapCaptureRequest` (`src/map_capture.h`)
Одобренный запрос импульса должен содержать валидированные значения:
- `pulse_count` (≤ `MAP_CAPTURE_MAX_PULSES`, 256);
- `timeout_periods` — бюджет watchdog;
- `max_abs_shunt_ma` — порог шунтов (применяется к idc1/idc2);
- `min_vbus_mv`, `max_vbus_mv` — окно VBUS;
- `sector_candidate`, `window_candidate` — физическое окно (scope-verified);
- `tim1_ccr[3]`, `tim8_ccr[3]` — CCR паттерн обоих инверторов;
- `trigger_revision` (должен совпадать с `OEW_ADC_TRIGGER_REVISION` в прошивке).

### 3.2 `MapBuilderQualification` (`src/map_builder.h`)
Иммутабельная геометрия карты после сбора данных:
- `identity` (OewMapIdentity), `manifest` (MapReferenceManifest);
- `min_records_per_row`, `startup_hold_cycles`, `startup_sector/window`,
  `startup_mu/mv/mw`, `min_margin_ticks`;
- `region[12]`, `recon[12]` — все 12 секторов/окон (требует full coverage).

### 3.3 `MapCaptureProfile_IsApproved()` — exact-equality allow-list
Даны поля `MapCaptureRequest`. Требование: сравнение **всех** полей с конечной,
скомпилированной таблицей одобренных профилей (побитово/полю-в-поле). НЕ range-check,
НЕ пользовательский паттерн.

## 4. Этапы

### Этап A — сбор данных на стенде (нужен HV-допуск, DC-link 60 В с токоограничением)
1. **Измерить рабочие диапазоны** (без PWM/по мануалу):
   - шунты I1/I2 при нуле (уже ≈2040/2068 raw) и при характерных токах;
   - VBUS окно (60 В → raw ≈ 595; зафиксировать min/max из спецификации);
   - определить `pulse_count`/`timeout_periods` для ограниченного охвата 12 строк.
2. **Определить паттерн CCR** для одного сектора/окна (scope-verified):
   - `tim1_ccr[3]`, `tim8_ccr[3]`, `trigger_revision`, ARR (999), window/сector.
3. **Разработать и согласовать с приёмщиком** одобренную таблицу профилей
   (значения CCR/лимитов/окон) — это единственный источник для кода.

### Этап B — реализация (после данных, safety-пакет)
1. `src/map_capture_profiles.c`:
   - `MapCaptureProfile_BuildRequest(profile_id, capture_id, out)` — заполняет
     одобренный запрос из таблицы; `false` для неизвестного profile_id;
   - `MapCaptureProfile_IsApproved(request)` — exact equality против той же таблицы;
   - `MapCaptureProfile_BuildQualification(profile_id, out)` — заполняет 12 регионов
     и recon (данные этапа A); `false` для неизвестного id.
2. Убедиться, что `MapCaptureHooks.validate_service_pattern` (если заполняется в main.c)
   согласуется с таблицей и отклоняет любой неодобренный паттерн.
3. Тесты `tests/map_capture_profiles_test.c` (hosted):
   - известный profile_id → BuildRequest/IsApproved true, поля каноничны;
   - неизвестный/подменённый profile_id → false;
   - подмена одного поля в Request → IsApproved false (exact equality);
   - BuildQualification заполняет все 12 строк (проверка структуры).
4. Логика **не загружается в generic-сборку** никак (только commissioning config
   остаётся включённой, но без профиля → false — безопасно).

## 5. Критерии приёмки

- [ ] `make` (generic, default-deny) — PASS; поведение без профиля НЕ изменилось
      (все три функции → false; `mcarm` → BLOCKED:PROFILE).
- [ ] `make test` — PASS (hosted+QEMU+pytest), включая новый
      `map_capture_profiles_test`.
- [ ] commissioning: `make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1
      -DPWM_OEW_BOARD_REVISION=7"` — PASS.
- [ ] На стенде (с допуском): `mcarm=<approved>` → `@MC:ARM:rc=0`;
      `mapcap run` → `rc=0`; `mapcap drain` → `records>0`;
      PC-экспорт → непустой `raw_records.jsonl`.
- [ ] Diff ограничен `map_capture_profiles.c/h`, `tests/map_capture_profiles_test.c`,
      `main.c` (только если нужна привязка validate) — всё в safety-границах.
- [ ] `git diff origin/main...HEAD --check` — чисто; CI зелёный.

## 6. Запреты

- **Не** ослаблять fail-closed: профильные функции должны применять exact-equality
  против скомпилированной таблицы; никаких `>=`/`<=`/масок/диапазонов.
- **Не** принимать CCR/лимиты от UART/пользователя.
- **Не** трогать `protect.c`, `pwm.c`, `.ioc`, ADC/калибровку.
- **Не** выполнять energise без явной процедуры BENCH_FIRST_SESSION §5 и допуска.
- Временные диагностические принты — только `#ifdef`, удалить.

## 7. Процесс

- Ветка `ai-bench/map-capture-profile` от свежего `origin/main`.
- Запись в `docs/AGENTS_STATUS.md` (агент=ПК-3, задача, файлы, статус).
- Этап A требует физического HV-допуска — делает человек/приёмщик.
- Публикация + `git ls-remote` подтверждение SHA, ожидание приёмки (main не трогать).

## 8. Задел (готово и не требует повтора)

- Калибровка токов (Путь B) — в main: `c` → SUCCESS.
- Комиссионинг-сборка и команды `mapcap` — проверены на стенде.
- PC-экспортёр `mapcap_uart_export.py` (ветка PR #6, draft) — сквозная проверка
  формата `@MC:REC`/`@MC:DRAIN` и fail-closed.
- План кампании — `docs/BENCH_NEXT_STEP_PLAN_20260824.md`.