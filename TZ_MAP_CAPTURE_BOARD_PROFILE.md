# TZ_MAP_CAPTURE_BOARD_PROFILE — board-qualified capture profile для STEVAL (карта тока)

## Цель
Добавить в `src/map_capture_profiles.c` **board-профиль** (approved capture request +
MapBuilderQualification) под стенд STEVAL-IPM20B ×2 (STM32G474RE). Сейчас в production
`MapCaptureProfile_IsApproved()=false` (только SYNT под host-test) → `mcarm=<id>` →
`@MC:ARM:BLOCKED:PROFILE`, снятие карты тока невозможно. Профиль — единственный
недостающий элемент для кампании 12 регионов (6 секторов × 2 окна) → offline-оценка M
→ MAP_READY → разблокировка FOC.

## Условия
- База: `origin/main` (06a5cba).
- Железо: STM32G474RE + 2× STEVAL-IPM20B, DC-link 60 В, мотор в цепи (pp=3).
- Сборка стенда: `make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1
  -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1"` (release обязателен:
  иначе `MapCapture_Arm/Run` → `HW_INTERLOCK_MISSING`, проверено по коду).
- CLI: `mcarm=<id>`, `mapcap run/drain/status/build`, `@MC:REC` ↔ `tools/mapcap_uart_export.py`.

## Проблема (что блокирует)
`src/map_capture_profiles.c:151-176` (production-ветка): все три функции возвращают
false. Единственный approved-профиль — SYNT (0x53594E54) под
`OEW_MAP_SYNTHETIC_PROFILE+OEW_HOST_TEST` (host-тесты, на железе не активируется).
Проверены ветки ai-cascade/bench-map-profile-release, ai-bench/test3-*,
ai4/bench-test2-* — board-профиля нет нигде. MAP_L3_PIPELINE.md:21: «До появления
board-specific reviewed profile силовой мост не получает разрешение, карта не
переходит в MAP_READY».

## Требования к решению

### 1. Новый approved-профиль (id: выбрать, напр. 0x424F4152u "BOAR"; НЕ SYNT)
В `src/map_capture_profiles.c` добавить board-профиль, активный в production
(без host-define'ов; под `OEW_MAP_CAPTURE && OEW_MAP_L3`, как вызывается из main.c).

**MapCaptureRequest (для mcarm=<id>):**
- `pulse_count` = 16 (как SYNT), `timeout_periods` = 20
- `max_abs_shunt_ma` = 10000, `min_vbus_mv` = 1000, `max_vbus_mv` = 70000
- `sector_candidate`/`window_candidate` = 0..5 / 0..1 — **заполняются из profile_id
  или фиксируются по одному региону на сессию** (12 профилей-вариантов либо
  параметризация id: старшие биты = сектор, младшие = окно). Определить в ТЗ
  реализации: предпочтительно **12 approved-вариантов** (id = база + sector*2+window),
  т.к. `MapBuilder_AddRecord` сверяет sector/window записи с qualified row.
- `tim1_ccr[3]` = `tim8_ccr[3]` = модуляционные вектора из контракта
  VfcApertureContract (ARR 999, mid=500): mu/mv/mw в диапазоне modulation 135..999,
  **по центру окна сектора** (например: сектор 0: (250,500,750), … — вычислить
  строго по 6 перестановкам (mu,mv,mw), НЕ копировать SYNT CCR=500,500,500 —
  SYNT даёт равенство фаз, контракт отвергнет).
- `trigger_revision` = 0x4F455731 (`PWM_OEW_ADC_TRIGGER_REVISION`).

**MapBuilderQualification (для mapcap build=<id>):**
- `identity`: board_revision=7, pwm_frequency_hz=5000, timer_arr=999,
  adc_trigger_id=0x4F455731, trigger_offset_ticks=12,
  **deadtime_ticks = live (TIM1->BDTR & 0xFF) — сверить на стенде/в hosted-тесте,
  НЕ копировать SYNT=68 вслепую**, adc_clock_hz = live (CKMODE=11 → HCLK/4;
  SYNT=170000000 может быть неверен — проверить через cap_adc_clock_hz),
  adc_sample_cycles_x2 = 1281 (SMPR=111), adc_resolution = 0 (12-bit),
  adc_config_signature / current_calibration_signature — **в map_identity_equal не
  входят** (main.c:263-274 сверяет 9 полей), заполнить константами для CRC
  (как SYNT), пометить «требует offline-ревизии».
- `min_records_per_row` = ≥1 (рекомендация 3–5 для агрегации), `min_margin_ticks` = 110
  (из VfcApertureContract.switching_margin_cycles).
- `startup_*`: sector=0, window=0, hold_cycles=1, mu=mv=mw=0 (как SYNT; стартовая
  точка — отдельный этап).
- `region[6][2]`: mu/mv/mw min=-32768/max=32767 (или сузить до контракта 135..999
  по CCR — предпочтительно сузить до qualified range), valid=1.
- `recon[6][2]`: **valid=false** (коэффициенты M неизвестны до offline-оценки!
  НЕ копировать SYNT identity-матрицу m00=1000/m11=1000 — это сделает карту
  «готовой» с фальшивой реконструкцией, обход fail-closed). До компиляции
  реальных коэффициентов карта обязана оставаться not-ready.

### 2. Именование и изоляция
- Новый профиль под собственным id и #define (BOARD_*), не трогая SYNT-ветку
  (host-test остаётся для тестов).
- Проверка `IsApproved` — строгое сравнение ВСЕХ полей запроса (как
  synthetic_request_matches), чтобы UART не мог подсунуть произвольные CCR.

### 3. Тесты (hosted)
- `tests/map_capture_port_test.c` (или новый): board-профиль:
  - `MapCaptureProfile_IsApproved` = true для сгенерированного запроса, false для
    изменённого CCR/сектора/trigger;
  - `BuildQualification` заполняет 12 region, recon.valid=false;
  - `BuildRequest` для 12 вариантов id даёт корректные sector/window;
  - production-сборка (без OEW_MAP_SYNTHETIC_PROFILE): SYNT по-прежнему false,
    board — true.
- `make test` (hosted+QEMU+pytest) ALL PASS; commissioning-сборка PASS.

## Критерии успеха (стенд, после приёмки и пересборки пакета)
1. `mcarm=<board_id>` → `@MC:ARM:cap=N:rc=0` (НЕ BLOCKED:PROFILE, НЕ HW_INTERLOCK_MISSING).
2. `mapcap run` → `@MC:RUN:rc=0`; `mapcap drain` → 16 записей `@MC:REC`
   (sector/window соответствуют выбранному варианту); `mapcap status` → COMPLETE, detail=0.
3. 12 сессий (6 секторов × 2 окна) собирают полный набор → `mapcap build=<board_id>`
   накапливает строки; **карта НЕ становится MAP_READY с recon.valid=false**
   (fail-closed до offline-квалификации).
4. `p?` → PWM off после каждой сессии, FAULT=0.

## Deliverables
1. Патч: `src/map_capture_profiles.c` (+ `.h` при необходимости), тесты.
2. Ветка `ai2/map-board-profile` от свежего origin/main, CI зелёный, приёмка.
3. Пересборка пакета `map_capture_pkg_20260901` с профилем → реальная кампания 12
   регионов на ПК-3.

## История контекста
- Стенд подтвердил (01.09): V/f-трек закрыт (overshoot/start/pp), карта — следующий
  блокер FOC (`rc=-2 map_unverified`).
- Infra-пакет (06a5cba, SHA 59fec3be) собран; инфраструктурная сессия пропущена
  (решение пользователя) — сразу к профилю.
- SYNT-профиль: референс структуры, НЕ источник значений (CCR=500,500,500 —
  равенство фаз; adc_clock 170e6 — под вопросом; recon identity — опасен для
  fail-closed).
- Safety: профиль управляет разрешением силового моста — строгий approved-контракт,
  recon.valid=false до реальных измерений.

---
*ТЗ для реализации (ai2 или web AI). База: origin/main @ 06a5cba. Стенд: ПК-3, 60 В, 12 регионов.*
