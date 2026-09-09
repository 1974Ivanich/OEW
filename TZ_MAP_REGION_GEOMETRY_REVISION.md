# TZ_MAP_REGION_GEOMETRY_REVISION — ревизия региональных bounds на геометрические

**Дата:** 2026-09-09
**Статус:** ТЗ — требует safety-owner approval
**Причина:** TZ_FOC_REGION_TOO_NARROW — карта BOAR не совместима с FOC из-за узких статистических bounds

## Проблема

Текущий пайплайн (solver/certifier) использует **статистические bounds** из съёмки:

**Certifier (src/map_region_certifier.c:63-68):**
```c
out->mu_min = (int16_t)(mu_min + qualification->guard_q15);
out->mu_max = (int16_t)(mu_max - qualification->guard_q15);
```

где `mu_min`/`mu_max` — min/max modulation из grid cells съёмки.

**Результат для BOAR 60V:**
- Регионы: ±261 Q15 (~±0.8% шкалы) вокруг 12 сертифицированных точек
- Покрытие круга модуляции: ±1.8° из 60° (~3% сектора)
- FOC_RunFrame требует вектор в регионе на каждом цикле → тихий стоп при V/f-разгоне

**Корень:** карта сертифицирована только в 12 статических точках, а rotating FOC-вектор физически не может всё время находиться в «пятнах».

## Требуемое изменение

**Цель:** регионы карты = геометрические клинья секторов (6×60° × амплитудные зоны), полное покрытие круга модуляции.

**Контракт:**
- Регион определяет сектор SVPWM (6 клиньев по 60°) через фазовые упорядочения
- Сектор 0: mu > 0 > mv > mw
- Сектор 1: mu > mw > mv
- Сектор 2: mv > 0 > mu > mw
- Сектор 3: mv > mw > mu
- Сектор 4: mw > 0 > mv > mu
- Сектор 5: mw > mu > mv
- Амплитудные зоны: окно 0 (низкая модуляция, например 6000..10000 Q15), окно 1 (высокая, 10000..14000 Q15)
- Recon-строки сертифицированы в центре региона, но применяются во всём клине (линейная интерполяция)

## Детали реализации

### 1. MapRegionQualification extension

Добавить флаг в qualification:

```c
typedef struct {
    uint16_t min_valid_cells;
    int16_t guard_q15;
    uint16_t min_margin_ticks;
    bool use_geometry_bounds;  // NEW: если true — использовать геометрические bounds
    // Амплитудные зоны для геометрических bounds (опционально, defaults)
    int16_t geometry_window0_min_mod_q15;  // минимальная модуляция для окна 0
    int16_t geometry_window0_max_mod_q15;  // максимальная модуляция для окна 0
    int16_t geometry_window1_min_mod_q15;  // минимальная модуляция для окна 1
    int16_t geometry_window1_max_mod_q15;  // максимальная модуляция для окна 1
} MapRegionQualification;
```

### 2. MapRegionCertify revision

Если `use_geometry_bounds == true`:

```c
if (qualification->use_geometry_bounds) {
    // Геометрические bounds по сектору
    switch (sector) {
        case 0:  // mu > 0 > mv > mw
            out->mu_min = geometry_window_min_mod_q15(window);
            out->mu_max = geometry_window_max_mod_q15(window);
            out->mv_min = -out->mu_max;
            out->mv_max = -1;
            out->mw_min = -out->mu_max;
            out->mw_max = -out->mu_min;
            break;
        case 1:  // mu > mw > mv
            out->mu_min = geometry_window_min_mod_q15(window);
            out->mu_max = geometry_window_max_mod_q15(window);
            out->mw_min = -out->mu_max;
            out->mw_max = -1;
            out->mv_min = -out->mu_max;
            out->mv_max = -out->mu_min;
            break;
        // ... остальные сектора
    }
} else {
    // Текущий путь: statistical bounds
    out->mu_min = (int16_t)(mu_min + qualification->guard_q15);
    out->mu_max = (int16_t)(mu_max - qualification->guard_q15);
    // ...
}
```

**Важно:** при геометрических bounds нужно **проверить**, что все certified cells находятся внутри региона (уже есть в текущем коде: MAP_CERT_INVALID_INSIDE/MAP_CERT_UNTESTED_INSIDE). Если certified cells выходят за геометрические bounds — REJECT (съёмка не соответствует выбранной геометрии).

### 3. Geometry bounds helper

Добавить функцию для геометрических bounds:

```c
static void geometry_bounds_for_sector_window(
    uint8_t sector,
    uint8_t window,
    const MapRegionQualification *q,
    OewPwmRegion *out)
{
    int16_t mod_min, mod_max;
    if (window == 0u) {
        mod_min = q->geometry_window0_min_mod_q15;
        mod_max = q->geometry_window0_max_mod_q15;
    } else {
        mod_min = q->geometry_window1_min_mod_q15;
        mod_max = q->geometry_window1_max_mod_q15;
    }
    // Фазовые упорядочения по сектору
    switch (sector) {
        case 0:  // mu > 0 > mv > mw
            out->mu_min = mod_min;
            out->mu_max = mod_max;
            out->mv_min = -mod_max;
            out->mv_max = -1;
            out->mw_min = -mod_max;
            out->mw_max = -mod_min;
            break;
        // ... остальные сектора
    }
}
```

### 4. CLI dataset.txt extension

Добавить поля в `regionq`:

```
regionq min=4 guard=0 margin=110 use_geometry=1 w0_mod_min=6000 w0_mod_max=10000 w1_mod_min=10000 w1_mod_max=14000
```

Если `use_geometry=0` — текущий путь (statistical bounds).

### 5. Interpolation error analysis

**Safety concern:** recon-строки сертифицированы только в центре региона (точка съёмки). Линейная интерполяция на краях региона вносит error.

**Требуется анализ:**
- Оценить M-коэффициенты на краях региона используя certified M из центра
- Compare с theoretical bounds из qualification (residual_rms_limit_ma)
- Если error > limits — требуется:
  - Увеличить количество certified points внутри региона (grid более 4 точек)
  - Или сузить амплитудные зоны (уменьшить window bounds)

**Proposed analysis:**
- Для каждого сектора S и окна W:
  - Брать certified M из центра региона
  - Вычислять predicted currents на краях региона (min/max modulation)
  - Сравнивать с theoretical bounds (линейная аппроксимация)
  - Error = |predicted - theoretical|
  - Должен быть < qualification.residual_rms_limit_ma

### 6. Test coverage

**Hosted tests:**
- Test геометрических bounds для всех 6 секторов × 2 окна
- Test certified cells внутри региона (MAP_CERT_OK)
- Test certified cells вне региона (MAP_CERT_INVALID_INSIDE/UNTESTED_INSIDE)
- Test statistical fallback (use_geometry=0)
- Test interpolation error на краях региона

**CI:**
- PyCompile для новых qualification полей
- Hosted test suite PASS

## План реализации

1. **Phase 1: ТЗ approval**
   - Safety-owner approves contract revision
   - Утверждает amplitude zones (w0_mod_min/max, w1_mod_min/max)
   - Утверждает interpolation error analysis methodology

2. **Phase 2: Implementation**
   - Расширить MapRegionQualification
   - Реализовать geometry_bounds_for_sector_window
   - Ревизовать MapRegionCertify
   - Обновить CLI dataset.txt parser
   - Обновить map_bench_dataset.py converter

3. **Phase 3: Test coverage**
   - Hosted tests для геометрических bounds
   - Interpolation error analysis (hosted, с real campaign data)
   - CI integration

4. **Phase 4: Campaign rebuild**
   - Пересобрать BOAR 60V campaign с новыми bounds
   - Verify: все certified cells внутри геометрических регионов
   - Verify: interpolation error на краях < limits

5. **Phase 5: Stend verification**
   - mapload нового артефакта
   - FOC_START + разгон мотора
   - Verify: FOC не останавливается тихо

## Open questions

1. **Amplitude zones:** какие значения для w0_mod_min/max и w1_mod_min/max?
   - Proposal: w0 = 6000..10000 Q15 (~0.18..0.30 модуляции), w1 = 10000..14000 Q15 (~0.30..0.43)
   - Requires agreement with campaign data (BOAR: current CCR ~355..645, Q15 ~5920..10750)

2. **Interpolation error tolerance:** допустимый error на краях региона?
   - Proposal: residual_rms_limit_ma из solver qualification (например, 50 mA)
   - Requires analysis with real campaign data

3. **Backward compatibility:** использовать use_geometry=false по умолчанию (статистические bounds)?
   - Proposal: use_geometry=false по умолчанию для существующих кампаний
   - Новые кампании явно указывают use_geometry=1

## Ссылки

- TZ_FOC_REGION_TOO_NARROW.md — диагноз проблемы
- src/map_region_certifier.c — текущий certifier
- src/map_measurement_solver.c — solver (без изменений)
- tools/map_artifact_pipeline_cli.c — CLI parser
