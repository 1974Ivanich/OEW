Да. Ниже — готовый документ ТЗ. Он намеренно **не вносит изменений в текущую ветку** и задаёт архитектурный контракт для следующего PR.

# TZ_ACS712_5A_CURRENT_REFERENCE_AND_MAP_CONTRACT

**Статус:** DRAFT FOR REVIEW (v0.2)
**Тип:** Architecture / Measurement / Map Contract
**Предыдущий baseline:** `ai2/acs712-scope-foc-map @ ace8898875b6125b1b885b8fa86aa715749265d0`
**Предыдущий PR:** `#22 — REJECTED / SUPERSEDED`
**Scope:** ACS712-5A, current reference 1–3 A, map ingestion, manifest provenance, V/F → FOC commissioning boundary
**Редакция:** v0.2 добавляет: три независимых домена (geometric/qualification/control); SNR считается только от измеренного noise сессии; ACS712 bandwidth/settling gate; zero-drift budget; DC-link droop gate; V/F↔FOC hysteresis как TBD; экстраполяция выше 3 A явно запрещена; acs712_nohv_checkout обязателен для нового датчика; ожидаемый verdict по уровням.

---

## 1. Scope и цель изменения

### 1.1 Цель

Определить формальный контракт для использования внешнего токового измерения на базе **ACS712-5A** при получении и квалификации OEW/FOC map data.

ТЗ должно установить:

* как измеряется ток;
* как выполняется calibration;
* как определяется фактическая чувствительность;
* как current reference попадает в dataset;
* какие current limits относятся к map;
* какие данные являются measurement provenance;
* какие изменения допустимы в `map_scope_ingest.py`;
* какие изменения требуют отдельного safety/FOC acceptance;
* где проходит граница между measurement qualification и runtime qualification.

### 1.2 Baseline

На момент начала данного ТЗ инструментальный слой уже подготовлен:

```text
branch:
    ai2/acs712-scope-foc-map

commit:
    ace8898875b6125b1b885b8fa86aa715749265d0
```

В baseline уже присутствуют:

* `--sens-mv-per-a` в `scope_acs712_capture.py`;
* default `185.0 mV/A`;
* calibration template;
* документация;
* contract/freeze tests;
* отсутствие изменений в `map_scope_ingest.py`.

Этот baseline **не должен изменяться в рамках подготовки данного ТЗ**.

### 1.3 Методологический принцип: SNR от измеренного noise

SNR **не рассчитывается по номинальному/предполагаемому noise**. Перед каждой qualification campaign обязателен zero-current control measurement (см. Phase-0 в `TZ_REF_01`). Все SNR claims рассчитываются только от измеренного noise **этого** сеанса.

Три уровня SNR:

```text
SNR < 3       → CHAIN-ONLY / PATH-A: chain и timing квалифицируются,
                но не amplitude response
SNR ≥ 3       → допустимо наблюдение amplitude response,
                но НЕ автоматическое quantitative-scale claim
SNR ≥ 10      → quantitative-reference claim может рассматриваться
                при выполнении остальных gates
```

`SNR ≥ 3` **не становится доказательством точности current scale**. Это только порог «можно ли вообще увидеть амплитуду сигнала выше шума».

---

# 2. ACS712-5A: nominal 185 mV/A

## 2.1 Nominal characteristic

Для ACS712-5A nominal sensitivity принимается как:

```text
185 mV/A
```

Это **номинальная характеристика sensor variant**, а не автоматически квалифицированный коэффициент конкретного измерительного канала.

Следовательно:

```text
nominal_sensitivity = 185 mV/A
```

не является доказательством:

```text
actual_system_sensitivity = 185 mV/A
```

## 2.2 Measurement model

Базовая модель:

```text
Vsignal = Vzero + I · S
```

где:

```text
Vsignal — измеренное напряжение;
Vzero   — zero-current output;
I       — ток;
S       — фактическая sensitivity.
```

Для calibrated measurement:

```text
I = (Vsignal - Vzero) / S
```

## 2.3 Требование

Все downstream consumers должны однозначно различать:

* nominal sensitivity;
* calibrated sensitivity;
* фактически использованный при capture коэффициент.

---

# 3. Calibration procedure

## 3.1 Общий принцип

Перед использованием ACS712 как quantitative current reference должна существовать calibration procedure.

Calibration должна определять минимум:

```text
zero offset
sensitivity
polarity
reference current
measurement uncertainty
```

## 3.2 Reference

Calibration должна использовать внешний reference current measurement.

Допустимые варианты должны быть явно указаны в calibration record.

Нельзя считать:

```text
ACS712 nominal = 185 mV/A
```

самостоятельной calibration procedure.

## 3.3 Calibration record

Calibration record должен содержать минимум:

```text
sensor_model
sensor_variant
nominal_sensitivity_mv_per_a
calibrated_sensitivity_mv_per_a
zero_offset
polarity
reference_instrument
reference_current
calibration_method
calibration_timestamp
operator_or_run_id
uncertainty
qualification_status
```

## 3.4 Qualification status

До завершения физической calibration:

```text
quantitative_current_reference = NOT_QUALIFIED
```

После успешного выполнения всех acceptance criteria:

```text
quantitative_current_reference = QUALIFIED
```

Переход между состояниями должен быть явным.

---

# 4. Zero offset и polarity

## 4.1 Zero offset

Zero-current output должен измеряться отдельно.

Модель:

```text
I = (Vsignal - Vzero) / S
```

Использование:

```text
I = Vsignal / S
```

допускается только если архитектурно доказано, что:

```text
Vzero = 0
```

с требуемой точностью.

## 4.2 Polarity

Calibration должна определить знак:

```text
positive reference current
        ↓
positive calibrated current
```

или соответствующий инвертированный convention.

Polarity должна быть записана в calibration record.

## 4.3 Acceptance

Нельзя квалифицировать current reference только по абсолютной величине sensitivity без проверки:

* zero;
* polarity;
* linearity в используемом диапазоне.

---

# 5. Current conversion и единицы

## 5.1 Canonical unit

В map contract canonical current unit:

```text
mA
```

Для human-readable calibration:

```text
A
```

допускается как дополнительная единица.

## 5.2 Conversion

Если sensitivity хранится в:

```text
mV/A
```

то:

```text
I[A] = (Vsignal[mV] - Vzero[mV]) / sensitivity[mV/A]
```

и:

```text
I[mA] = 1000 · I[A]
```

## 5.3 Precision

Округление не должно происходить до завершения всех необходимых вычислений.

Dataset должен сохранять достаточную precision для последующей map qualification.

## 5.4 Provenance

Каждое quantitative measurement должно иметь возможность быть связано с:

```text
sensor calibration
capture configuration
sensitivity
zero
polarity
reference
```

---

# 6. Uncertainty и acceptance tolerance

## 6.1 Требование

Для current reference необходимо определить total measurement uncertainty.

Она должна учитывать, где применимо:

```text
ACS712 sensitivity tolerance
calibration uncertainty
reference instrument uncertainty
ADC/scope resolution
zero-offset uncertainty
noise
temperature effects
capture processing
rounding
```

## 6.2 Нельзя

Нельзя использовать:

```text
185 mV/A
```

как uncertainty estimate.

Номинальная sensitivity — это calibration input/nominal characteristic, а не measurement error budget.

## 6.3 Acceptance

ТЗ реализации должно определить:

```text
U_current_max
```

или эквивалентный acceptance criterion.

До определения этого значения current reference может использоваться для exploratory measurement, но не должен автоматически считаться production-qualified quantitative reference.

## 6.4 Temperature

Если рабочий диапазон температуры влияет на sensitivity/offset, calibration contract должен явно определить:

* temperature range;
* compensation или отсутствие compensation;
* acceptance limits.

---

# 7. Допустимый диапазон 1–3 A

## 7.1 Map control range

Для данного архитектурного изменения целевой qualification range:

```text
qual_current_min_ma = 1000
qual_current_max_ma = 3000
```

или:

```text
1000 mA
3000 mA
```

соответственно.

## 7.2 Семантика

Эти значения являются **map-data control range**.

Они означают:

> Диапазон токов, в котором map measurements предназначены для quantitative qualification.

Они не являются автоматически:

* FOC current limits;
* inverter protection thresholds;
* motor thermal limits;
* ACS712 electrical limits;
* production safety limits.

## 7.3 Boundary

Должно быть явно различено:

```text
measurement range
map qualification range
runtime current limit
hardware protection limit
```

Изменение одного диапазона не должно автоматически изменять остальные.

---

## 7.4 Три независимых домена (v0.2)

Map qualification и control envelope — это **разные** вещи. Их нельзя смешивать:

```text
GEOMETRIC DOMAIN
    определяется PWM-векторами / секторами / окнами.
    Может быть шире, чем physical qualification domain.
    Не зависит от current reference.

PHYSICAL QUALIFICATION DOMAIN
    1 A ... 3 A
    В нём собраны реальные измерения с квалифицированной calibration.
    Только в нём `map` считается physically qualified.

CONTROL DOMAIN
    V/F  при I < 1 A
    FOC  при I ≥ 1 A
    Граница контроля. Не совпадает с physical qualification domain.
    Operating/extrapolation выше 3 A — отдельная operating-envelope
    гипотеза, а не расширение физической квалификации.
```

Запрещено:

```text
map qualification domain   = map geometric domain
map qualification domain   = FOC operating envelope
```

Изменение одного домена не должно автоматически изменять остальные.

## 7.5 Экстраполяция выше 3 A (v0.2)

> **Physical qualification of current scale/map is limited to 1–3 A. Operation above 3 A is not retroactively considered physically qualified.**

Любое использование карты выше 3 A опирается на **отдельно обоснованную operating-envelope гипотезу** и не должно представляться как дополнительная physical qualification.

Для каждого уровня тока выше 3 A обязательно явное обоснование относительной ошибки:

```text
- dead-time error       (растёт с током через di/dt)
- switching-edge error  (зависит от CCM/DCM и формы тока)
- sensor error          (bandwidth, settling, temperature)
- ADC quantization      (младший код = 1/SNR × I)
```

Эти компоненты **не считаются автоматически** доминирующими над ACS712 metrology — для каждого нужен отдельный gate.

## 8. `qual_current_min_ma/qual_current_max_ma` и их семантика

## 8.1 Введение

Если реализация требует machine-readable qualification limits, должны быть определены:

```text
qual_current_min_ma
qual_current_max_ma
```

как **physical qualification metadata** карты. Имя зафиксировано в §10.3 (manifest contract) и §11.3.1 (control policy).

## 8.2 Предлагаемая семантика

```text
qual_current_min_ma = 1000
qual_current_max_ma = 3000
```

означают:

```text
1000 mA <= qualified map current <= 3000 mA
```

Они не означают:

```text
FOC current limit = 3 A
```

и не означают:

```text
safety trip = 1 A
```

## 8.3 Out-of-range samples

Dataset sample вне указанного map control range должен получать явно определённый статус.

Минимально необходимо различать:

```text
IN_RANGE
BELOW_MAP_RANGE
ABOVE_MAP_RANGE
```

Такие samples не должны молча смешиваться с qualified samples.

---

# 9. Где должны находиться current limits

## 9.1 Separation of concerns

Current limits должны находиться в том слое, которому они принадлежат.

### Measurement layer

Содержит:

```text
sensor sensitivity
offset
calibration
uncertainty
```

### Map layer

Содержит:

```text
map current range
map qualification state
sample validity
measurement provenance
```

### Safety layer

Содержит:

```text
overcurrent limits
hardware protection
software protection
fault thresholds
```

### FOC runtime layer

Содержит:

```text
FOC current command
current controllers
commissioning limits
operating limits
```

## 9.2 Запрет

Нельзя использовать:

```text
qual_current_min_ma
qual_current_max_ma
```

как неявный источник runtime safety limits.

---

# 10. Изменение `map_scope_ingest.py` и manifest contract

## 10.1 Изменения допускаются только после утверждения этого ТЗ

Текущий baseline:

```text
map_scope_ingest.py
```

остаётся неизменённым до завершения architecture review.

## 10.2 После approval

Если architecture review подтвердит необходимость, `map_scope_ingest.py` может получить:

* parsing calibration provenance;
* validation current units;
* validation map current range;
* validation manifest fields;
* rejection of incompatible calibration records.

## 10.3 Manifest

Если current calibration становится частью formal map provenance, manifest должен явно описывать:

```text
sensor_model
nominal_sensitivity_mv_per_a
calibrated_sensitivity_mv_per_a
zero_offset
polarity
calibration_status
qual_current_min_ma        ← rename from map_current_min_ma
qual_current_max_ma        ← rename from map_current_max_ma
uncertainty
```

Имена `qual_current_min_ma` / `qual_current_max_ma` фиксируются как **physical qualification domain identity fields**. Эти поля описывают диапазон, **в котором** карта физически квалифицирована (1–3 A по v0.3), а **не** FOC operating envelope и не geometric domain.

Naming rule (v0.3):

```text
- qual_* : physical qualification (1–3 A, измерено)
- map_*  : geometric map domain (PWM-вектора, регионы)  ← НЕ путать с qual
- ctrl_* : control/operating envelope (V/F, FOC, hysteresis)
```

Совпадение имён в campaign / map manifest / admission строго обязательно:

```text
campaign.qual_current_min_ma   ==  map.qual_current_min_ma
campaign.qual_current_max_ma   ==  map.qual_current_max_ma
campaign.calibration_id        ==  map.calibration_id
```

При `missing / malformed / different` → **fail-closed**, аналогично существующему PWM frequency identity в `map_scope_ingest.py`.

Названия полей должны быть стабильными и versioned.

## 10.4 Backward compatibility

Изменение manifest schema должно иметь:

```text
schema version
compatibility rule
migration/rejection rule
```

Старый manifest нельзя молча интерпретировать как новый qualified calibration record.

## 10.5 ACS712-5A = новый checkout (v0.2)

```text
ACS712-20A qualification
        ≠
ACS712-5A qualification
```

Каждый новый sensor variant (ACS712-5A вместо ACS712-20A) обязан проходить `acs712_nohv_checkout` по существующему шаблону (`docs/templates/acs712_nohv_checkout/`). Параметры чувствительности и zero offset переопределяются, поэтому **нельзя переносить qualification status с -20A на -5A** без новой процедуры.

Все результаты checkout'а попадают в session manifest как `acs712_nohv_<timestamp>/`.

---

# 11. Safety / FOC implications

## 11.1 Safety isolation

ACS712 calibration не должна автоматически изменять:

```text
PROTECT
overcurrent thresholds
hardware trip thresholds
FOC current limits
PWM protection
```

## 11.2 FOC runtime

Переход к FOC на основе новой current reference требует отдельного runtime acceptance.

Особенно:

```text
V/F → FOC
```

не должен происходить автоматически только потому, что:

```text
ACS712 sensitivity = 185 mV/A
```

## 11.3 1 A commissioning

1 A является целевой нижней границой physical qualification domain (см. §7.4).

Это **не** означает автоматически:

```text
FOC minimum current = 1 A
FOC may safely start at 1 A
```

Такие утверждения требуют отдельной runtime qualification.

## 11.3.1 V/F ↔ FOC control boundary — TBD (v0.2)

`qual_current_min_ma = 1000` не превращается в control policy. Будущий control contract должен определить:

```text
FOC_ENTRY_CURRENT  >= 1.0 A   (TBD, не зашито в это ТЗ)
FOC_EXIT_CURRENT   <  0.8 A   (TBD, hysteresis)
```

Эти числа **остаются TBD** до отдельного runtime ТЗ. До тех пор:
- любая попытка использовать `qual_current_min_ma` для решения V/F↔FOC — отвергается;
- фактический момент перехода фиксируется в `operator_notes.md` сессии.

Дополнительно (отдельно) должны быть определены:
- hysteresis (FOC_ENTRY > FOC_EXIT);
- bumpless transfer (continuous angle/state);
- no mode chatter;
- timeout / fault behavior при зависании в пограничной зоне.

## 11.4 Protection hierarchy

Должна сохраняться иерархия:

```text
hardware protection
        >
software protection
        >
FOC operating limit
        >
map/measurement range
```

Точное поведение должно быть определено существующим safety contract и не должно ухудшаться из-за данного изменения.

## 11.5 ACS712 bandwidth и settling gate (v0.2)

Для количественного amplitude claim обязательна проверка:

```text
- полоса ACS712 (kHz) соответствует PWM fundamental + harmonics
- время нарастания датчика — порядка нескольких µs,
  должно быть меньше PWM dead-time
- длительность сервисного импульса достаточна для settling
- положение sampling window: за пределами settling
```

Acceptance:

```text
sampling_window_start
    >
sensor_settling_time + required_margin
```

Если не выполняется, amplitude measurement нельзя считать количественно достоверным без correction / uncertainty model.

## 11.6 Zero-drift budget (v0.2)

Обязательные поля measurement record:

```text
zero_uncertainty_ma
zero_drift_in_session_ma
noise_pp_ma
smallest_expected_current_ma
```

Нижняя граница physical qualification domain определяется не как фиксированное `I_min = 1 A`, а как:

```text
I_min >= function(noise, zero_uncertainty, zero_drift, acceptance criterion)
```

То есть **1 A сейчас является целевой архитектурной границей, но не заранее доказанной metrological boundary**. Metrological boundary доказывается измерением в каждой сессии.

## 11.7 DC-link droop gate (v0.2)

Для 1–3 A campaign обязательна регистрация:

```text
Vbus_before
Vbus_during
Vbus_after
ΔVbus
recharge_interval
```

Особенно для 3 A. Если DC-link не успевает восстановиться между pulse groups:

```text
profile должен вводить recovery delay
ИЛИ campaign должна отдельно учитывать droop
```

Без этого droop может исказить observed current scale, и `current response OBSERVED` не будет достоверным даже при SNR ≥ 10.

---

# 12. Commissioning и physical qualification gates

## 12.1 Gate 0 — tooling

Проверить:

```text
scope_acs712_capture.py
--sens-mv-per-a
calibration template
manifest generation
```

Статус:

```text PASS / FAIL
```

## 12.2 Gate 1 — zero

Проверить:

```text zero-current output
repeatability
drift
noise
```

Статус:

```text PASS / FAIL
```

## 12.3 Gate 2 — polarity

Подать известный reference current в обеих полярностях или выполнить эквивалентную процедуру.

Проверить знак.

Статус:

```text PASS / FAIL
```

## 12.4 Gate 3 — sensitivity

Получить calibrated:

```text S_cal [mV/A]
```

и сравнить с acceptance tolerance.

Статус:

```text PASS / FAIL
```

## 12.5 Gate 4 — linearity

Проверить несколько точек внутри:

```text 1 A ... 3 A
```

Минимум должны быть представлены:

```text near 1 A
mid-range
near 3 A
```

## 12.6 Gate 5 — uncertainty

Рассчитать measurement uncertainty и проверить:

```text U_current <= U_current_max
```

Статус:

```text PASS / FAIL
```

## 12.7 Gate 6 — map data

Для каждого sample проверить:

```text current provenance
current range
calibration identity
validity
```

Нельзя квалифицировать dataset только по наличию файлов.

## 12.8 Gate 7 — map semantic qualification

После measurement qualification отдельно проверить:

* sector identity;
* window identity;
* PWM geometry;
* current response;
* consistency;
* отсутствие synthetic substitution;
* отсутствие tautological qualification;
* физическую воспроизводимость.

## 12.9 Gate 8 — OEW physical qualification

Только после прохождения предыдущих gates можно утверждать физическую пригодность данных для OEW/FOC.

Это отдельный verdict:

```text PHYSICALLY_QUALIFIED
```

и он не должен следовать автоматически из:

```text CALIBRATION_QUALIFIED
```

---

## 13. Explicit NON-GOALS

Данное ТЗ **не разрешает автоматически**:

1. менять FOC runtime;
2. менять `PROTECT`;
3. менять hardware overcurrent protection;
4. менять PWM protection;
5. менять существующие FOC current limits;
6. переводить V/F → FOC только на основании ACS712 calibration;
7. считать 185 mV/A физически подтверждённым коэффициентом;
8. считать ACS712 quantitative reference qualified без physical calibration;
9. считать map physically qualified только после успешного ingestion;
10. изменять `map_scope_ingest.py` до architecture approval;
11. менять manifest contract без schema/version decision;
12. использовать map current range как safety limit;
13. **считать карту квалифицированной выше 3 A** (см. §7.5);
14. **использовать `qual_current_min/max_ma` для V/F↔FOC policy** (см. §11.3.1);
15. **переносить qualification status с ACS712-20A на ACS712-5A** (см. §10.5).

---

# 14. Required implementation boundaries

После утверждения ТЗ изменения должны быть разделены минимум на следующие logical commits/PR scopes.

### PR-A — Measurement contract

Допускается:

```text
calibration schema
calibration validation
measurement provenance
```

### PR-B — Map ingestion

Допускается:

```text
map current range
manifest integration
ingest validation
```

### PR-C — Physical qualification

Содержит:

```text
real measurements
calibration evidence
map qualification
OEW physical evidence
```

### PR-D — FOC/runtime

Только отдельным ТЗ:

```text
FOC current reference
commissioning
V/F → FOC transition
runtime limits
safety integration
```

Не объединять PR-B и PR-D без отдельного architecture approval.

---

# 15. Required tests

## Unit tests

Должны проверять:

* parsing sensitivity;
* default behavior;
* explicit `185.0 mV/A`;
* zero-offset conversion;
* polarity;
* unit conversion;
* current-range boundaries;
* out-of-range handling;
* manifest schema;
* calibration qualification state.

## Regression tests

Должны гарантировать:

```text
existing map_scope_ingest behavior
```

не изменяется до момента явного contract migration.

## Negative tests

Обязательны проверки:

```text
missing sensitivity
invalid sensitivity
zero sensitivity
negative sensitivity
missing calibration status
invalid current range
reversed current range
missing manifest provenance
incompatible schema version
```

---

# 16. Acceptance criteria

ТЗ считается реализованным только если одновременно выполнено:

### Measurement

```text
ACS712 calibration procedure documented
zero validated
polarity validated
sensitivity validated
uncertainty calculated
```

### Map

```text
1–3 A range explicitly represented
out-of-range samples classified
calibration provenance preserved
manifest schema versioned
```

### Safety

```text
no unintended safety-limit changes
no implicit FOC-limit changes
no protection regression
```

### FOC

```text
no implicit V/F → FOC transition
runtime behavior unchanged unless separately approved
```

### Physical

```text
real reference measurement exists
real ACS712 measurement exists
repeatability demonstrated
map data traceable to calibration
OEW physical qualification performed separately
```

---

## 17. Required verdict vocabulary

Чтобы не смешивать разные уровни доказательства, использовать отдельные verdicts:

```text
TOOLING_PASS
CALIBRATION_NOT_QUALIFIED
CALIBRATION_QUALIFIED
MEASUREMENT_QUALIFIED
MAP_DATA_VALID
MAP_PHYSICALLY_QUALIFIED
OEW_PHYSICALLY_QUALIFIED
RUNTIME_NOT_QUALIFIED
```

Не использовать один общий `PASS` для всех уровней.

## 17.1 Expected honest verdict (v0.2)

Типичный honest verdict для текущего baseline (после Phase-0 без независимого reference):

```text
ADC chain                 QUALIFIED
phase mapping             QUALIFIED
timing                    QUALIFIED
capture integrity         QUALIFIED

current response 1–3 A   OBSERVED

current scale accuracy   NOT DEMONSTRATED
                          until independent reference exists
```

Эта формулировка **не смешивает** «правильную физическую реакцию канала» с «доказанной абсолютной точностью амперной шкалы». Она остаётся честной при любом SNR.

---

# 18. Baseline protection

До формального approval данного ТЗ сохраняется:

```text
branch:
    ai2/acs712-scope-foc-map

commit:
    ace8898875b6125b1b885b8fa86aa715749265d0
```

Следующие компоненты считаются frozen:

```text
tools/map_scope_ingest.py
safety contract
FOC runtime
existing map manifest contract
```

Любое изменение этих компонентов должно ссылаться на конкретный раздел данного ТЗ и иметь отдельное review.

---

# 19. Decision gate

Перед началом архитектурного PR необходимо отдельно ответить на следующие вопросы:

1. Является ли ACS712-5A единственным current reference или только одним из источников?
2. Какой reference instrument является authoritative?
3. Какая допустимая uncertainty?
4. Какой calibrated sensitivity acceptance range?
5. Какой zero-offset acceptance?
6. Какой polarity convention?
7. Является ли диапазон 1–3 A обязательным для всех map datasets?
8. Что происходит с samples <1 A?
9. Что происходит с samples >3 A?
10. Должна ли calibration provenance входить в signed/versioned manifest?
11. Какие именно поля должен валидировать `map_scope_ingest.py`?
12. Какие изменения, если таковые нужны, допускаются в safety/FOC runtime?

Только после получения ответов и architecture approval разрешается переходить к implementation PR.

---

# 20. Итоговый архитектурный принцип

Основной принцип данного ТЗ:

```text
ACS712 nominal characteristic
        ≠
ACS712 calibrated measurement
        ≠
qualified current reference
        ≠
qualified map dataset
        ≠
OEW physically qualified map
        ≠
FOC runtime qualification
```

Каждый переход между уровнями должен иметь собственные измерения, acceptance criteria и verdict.

**185 mV/A — это nominal sensor characteristic.**

**1–3 A — это proposed map qualification range.**

**Ни одно из этих утверждений само по себе не является safety limit и не является доказательством физической пригодности OEW/FOC map.**

Это ТЗ можно использовать как **review-документ до начала следующего архитектурного PR**. Я бы пока не коммитил его в `main`: сначала пройти по 12 decision-gate вопросам, особенно по **reference instrument, uncertainty, acceptance tolerance и точной семантике 1–3 A**.