# TZ_MAP_GRID_PROFILE — grid-свейп на строку (BOAR v2) + калибровка solver-гейтов

Статус: ТЗ (подготовлено 02.09.2026, ПК-2 ai2). Основано на проверке реальных
данных кампании 01.09 (12/12 PASS, 192 записи, scope-слой ещё не собран).

## Цель

Разблокировать полный offline-пайплайн (Accumulator→Solver→Certifier→Writer)
для карты тока BOAR: получить коэффициенты recon M и сертифицированные
OewPwmRegion → скомпилировать в профиль v2 → `mapcap build` → MAP_READY → FOC.

## Проблема (подтверждено пересчётом, НЕ гипотеза)

Кампания 01.09 — **один вектор на строку** (12 сессий × 16 импульсов).
Прогон пайплайна на реальных данных (даже с идеальными scope-refs) падает на
двух стадиях:

1. **Solver: `MAP_SOLVER_SINGULAR`** (status 3, row 0/0).
   - `map_measurement_solver.c:111-113`: `det = S00·S11 − S01²`,
     `det_norm = det / 1e12` (жёсткая нормировка), гейт `det_norm ≥
     min_abs_determinant(=1)`.
   - Реальные токи строки 0/0 (мА): S00=322393, S01=733543, S11=4261050 →
     det=8.36e11 → det_norm=0 < 1 → SINGULAR (регионы 4 и 10 — то же).
   - Причина: при ОДНОМ векторе (idc1, idc2) коррелированы (оба ≈ k·I_dc-link)
     → возбуждение почти rank-1. **Гейт не зависит от scope-референсов.**
   - Дополнительно: нормировка det/1e12 калибрована под demo (токи 1–8 А,
     det/1e12=236); для стендовых 12..972 мА непроходима в принципе — даже
     идеальное 2-D возбуждение с ~300 мА даёт det≈8e11 → 0.
2. **Certifier: `MAP_CERT_DEGENERATE`** (`map_region_certifier.c:57-60`):
   все 16 ячеек строки в одной модуляционной точке → разброс 0 < 2·guard_q15.
   (Пайплайн до него не доходит — падает раньше на solver, но гейт стоит.)

Пороги квалификации кампании (min_abs_determinant, min_valid_cells=4)
унаследованы из demo (8 точек/строку, 1–8 А) без калибровки под стенд.

## Требования

### 1. Solver: нормировка детерминанта к масштабу данных (host-only)

`map_measurement_solver.c` не входит в прошивку (Makefile: только hosted-тесты
и tools .mk) — правка безопасна для safety.

- Заменить жёсткое `det / 1e12` на относительную меру возбуждения строки,
  например `det / (S00·S11)` (нормализованный детерминант, 0..1) или
  эквивалент, документированный в `map_measurement_solver.h`.
- `min_abs_determinant` в campaign manifest остаётся входным параметром —
  семантику задокументировать (значение для стенда выбрать в ТЗ реализации,
  напр. 1e-4 относительных).
- Boundary-тесты: одиночный вектор (rank-1) → SINGULAR; 2-D возбуждение с
  мА-масштабом → OK; масштаб 100 мА vs 5 А → одинаковый вердикт.

### 2. Профиль BOAR v2: grid-векторы на строку (firmware)

В `src/map_capture_profiles.c` расширен board-профиль: **4 approved-вектора
на (сектор, окно)**, id-пространство 48 вариантов:

```text
id = 0x424F4152 + sector*8 + window*4 + point,  sector 0..5, window 0..1, point 0..3
```

Вектора: центр (cu,cv,cw) из MAP_CAPTURE_BOARD_MOD + **симметричные**
смещения (центр кластера обязан лежать ВНУТРИ сертифицированного региона —
иначе guard_q15 сдвигает регион и стартовая точка не проходит
`CurrentMap_LoadMeasured`):

| point | смещение CCR | Q15 |
|---|---|---|
| 0 | (0,0,0) | (0,0,0) |
| 1 | (+4,−4,0) | (+262,−262,0) |
| 2 | (0,+4,−4) | (0,+262,−262) |
| 3 | (−4,0,+4) | (−262,0,+262) |

**Разделение окон:** окно 1 сдвигает кластер на **+16 CCR по max-фазе,
−16 по min-фазе** (инвариант суммы mu+mv+mw=1500 и порядок фаз сохранены).
Без этого окна сектора давали одинаковые регионы → перекрытие → отказ
`CurrentMap_LoadMeasured`. Проверено: 66/66 пар кластеров попарно
непересекаются, центры всех 12 кластеров внутри своих регионов.

- `pulse_count` = **8** (4 точки × 8 = 32 сэмпла на строку — ровно лимит
  `MAP_ACCUM_MAX_SAMPLES_PER_ROW=32`; 16×4=64 не влезало в CLI/accumulator).
- Требования профиля: exact-match на полный запрос (как сейчас), sector/window
  из id, точка из id; `min_records_per_row` ≥ 3; region/recon как в BOAR v1
  (recon.valid=false, fail-closed до offline-оценки).
- Стартовая точка (`startup`) — центр кластера sector 0 / window 0
  (mu=8192, mv=0, mw=−8192): (0,0,0) не покрыт ни одним регионом.
  Физический выбор стартового вектора — отдельный этап FOC.
- Identity НЕ меняется (board 7, pwm 294, arr 999, trigger 0x4F455731,
  clk 42.5e6, sample 1281, deadtime 192).

### 3. Ingest: таблица векторов по (row, point)

`tools/map_scope_ingest.py` — расширить `expected_ccr` на 4 точки на строку
(лог региона теперь = 4 сессии × 16 записей; файлы логов именовать
`region_<r>_<p>.log`, scope CSV — `scope_region_<r>_<p>.csv`). Fail-closed
гейты и provenance не меняются.

### 4. Кампания со scope (ПК-3)

48 сессий (12 строк × 4 точки), на каждую: mcarm → run (захват осциллографом)
→ drain. Scope: ref_u/v/w_ma на точку выборки, scope_qualified, margin,
trigger_offset (TRGO→апертура — измерить! в identity сейчас 0). Возврат —
как в `map_scope_pkg_boar_20260902\SESSION_20260902_SCOPE_PROTOCOL.md`
(шаблон расширить на 4 точки на строку).

## Критерии успеха

1. Hosted: `make test` ALL PASS (включая новые solver-boundary и profile-тесты);
   CI зелёный.
2. Пайплайн на синтетическом grid-датасете (реальные токи стенда, refs от
   известной M): Accumulator→Solver→Certifier→Writer OK, артефакт проходит
   `CurrentMap_LoadMeasured`.
3. Стенд: 48/48 mcarm rc=0, drain 16/16; scope-слой 48×16; ingest → validator
   PASS; полный пайплайн на реальных данных → recon коэффициенты →
   профиль v2 (recon.valid=true) → `mapcap build` → MAP_READY → FOC.

## Deliverables

1. Патч solver (нормировка det) + тесты.
2. Патч профиля (grid v2) + тесты.
3. Расширение `tools/map_scope_ingest.py` + тест.
4. Пакет для стенда `map_grid_pkg_<дата>` (образ из свежего origin/main
   после приёмки, `-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1
   -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1`).
5. Ветки `ai2/map-solver-detscale`, `ai2/map-grid-profile` от свежего
   origin/main, CI зелёный, приёмка.

## История контекста

- 01.09: кампания BOAR 12/12 PASS (firmware bd48ce0, SHA 3fc1b987…).
- 02.09: проверка evidence — 192 записи валидны, ccr↔сектор сходится,
  fail-closed `ERROR:QUALIFICATION` подтверждён кодом (recon.valid=false).
- 02.09: прогон пайплайна с синтетическим scope-слоем (identity-M) на
  реальных записях → status 3 (SOLVER SINGULAR) row 0/0; по коду подтверждён
  также MAP_CERT_DEGENERATE для 1-векторных строк.
- Scope-сессия на 12 центрах (пакет map_scope_pkg_boar_20260902) остаётся
  как методический этап (refs центров + timing + trigger_offset), но сама по
  себе M не даёт.
