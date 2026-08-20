# Спецификация `MapMeasurementAccumulator`

**Статус:** проект спецификации для board-qualified commissioning  
**Назначение:** вычислять измеренную матрицу реконструкции `M` и консервативные границы PWM-региона для каждой строки `sector × window`, не ослабляя fail-closed дизайн FOC.

## 1. Резюме и принципиальное ограничение

Текущий `MapCaptureRecord` содержит два измерения DC-link shunt-токов `idc1/idc2`, Vbus, ADC-статус, sector/window и связанный PWM snapshot. Этого **недостаточно для оценки матрицы реконструкции** без независимого эталона фазных токов.

Для строки карты матрица имеет вид:

\[
 y = Mx,
 \qquad
 x = \begin{bmatrix} i_{dc1} \\ i_{dc2} \end{bmatrix},
 \qquad
 y = \begin{bmatrix} i_{phase_a} \\ i_{phase_b} \end{bmatrix},
\]

где `phase_a` и `phase_b` — две различные фазы из `{U,V,W}`. Поэтому pipeline обязан получать для каждого принятого измерения независимый эталон `y`, например от двухканального токового зонда/осциллографа, либо из заранее квалифицированного стендового reference provider. Нельзя вычислять `M` только из `idc1/idc2`: это было бы самоподтверждением модели и не даст доказательства правильности полярности, gain и фазовой привязки.

> **Обязательное правило:** без независимых `phase_a/phase_b` reference samples accumulator не имеет права вернуть `ROW_READY`, а карта не может перейти в `MAP_READY`.

## 2. Область ответственности

`MapMeasurementAccumulator` отвечает за сбор и валидацию измерений, оценку `M`, оценку качества и сертификацию aperture-региона. Он **не** включает PWM, **не** меняет `ADC_SetControlAdmission`, **не** вызывает `PWM_Enable`, **не** вызывает обычный FOC и **не** устанавливает `CurrentRecon` напрямую.

После успешного вычисления он создаёт только кандидат `OewCurrentMap`. Финальная установка выполняется отдельным commissioning wrapper, который проверяет остановленное состояние силовой части и передаёт карту в `CurrentMap_LoadMeasured()`.

| Слой | Ответственность |
|---|---|
| `MapCapture` | Безопасно получить bounded `MapCaptureRecord` через service-only PWM path. |
| `MapMeasurementAccumulator` | Собрать записи по строкам, оценить `M`, timing quality и PWM bounds. |
| `CurrentMap_LoadMeasured` | Проверить magic/revision/identity/CRC/regions/reconstruction/startup и атомарно установить карту. |
| `FOC_Start` | Требовать готовую реконструкцию и реальный startup context до ADC/PWM admission. |

## 3. Расширение входного формата

Существующий `MapCaptureRecord` следует сохранить совместимым, добавив отдельный reference/evidence слой. Reference-данные могут приходить из offline import после стендового измерения; они не должны приниматься произвольной UART-командой во время включённого моста.

```c
typedef struct {
    int32_t phase_u_ma;
    int32_t phase_v_ma;
    int32_t phase_w_ma;
    uint8_t valid;
    uint8_t source;          /* reviewed external probe/reference provider */
    uint16_t reserved;
    uint32_t sample_id;
    uint32_t timestamp_cycles;
} MapPhaseReference;

typedef struct {
    uint16_t margin_ticks;       /* measured trigger-to-valid-aperture margin */
    uint16_t blanking_ticks;
    uint8_t adc_settled;
    uint8_t scope_qualified;
    uint16_t reserved;
} MapTimingEvidence;

typedef struct {
    MapCaptureRecord capture;
    MapPhaseReference reference;
    MapTimingEvidence timing;
} MapMeasurementSample;
```

Для каждой записи должны совпадать `capture.frame.sequence`, `reference.sample_id` и внешний timestamp в допустимом окне. Если reference измерен осциллографом, соответствие должно быть установлено offline по trigger marker; простое совпадение номера строки недостаточно.

Минимальные входные условия для sample:

| Условие | Требование |
|---|---|
| ADC status | Только `ADC_FRAME_WINDOW_INVALID` для service capture; эта запись не является control-valid. |
| Sector/window | `0 ≤ sector < 6`, `0 ≤ window < 2`, и request candidate должен совпадать с frame context. |
| Capture status | `MAP_CAPTURE_OK`, без timeout, overflow, trigger mismatch, ADC fault или protection fault. |
| Shunt data | Нет ADC saturation/overrun/desynchronization; offsets предварительно валидны. |
| Reference | `valid=1`, approved source, обе выбранные фазы измерены одновременно или синхронно. |
| Timing | `scope_qualified=1`, aperture margin и blanking измерены для именно этого PWM vector. |
| Identity | Board revision, PWM frequency, ARR, trigger revision и polarity profile совпадают. |

## 4. Организация измерительной сессии

Карта состоит из `6 × 2 = 12` независимых rows. Для каждой row выполняется sweep из нескольких PWM vectors, а не одна точка.

Рекомендуемая последовательность для одной row:

1. Проверить независимый hardware interlock, offsets и отсутствие control activity.
2. Включить только bounded service-capture burst с заранее скомпилированным профилем.
3. Пройти grid вокруг предполагаемой aperture области: центральные точки, границы по каждой фазе и углы/диагонали.
4. Для каждой точки получить одновременно shunt sample, reference phase sample и timing evidence.
5. Повторить центральные и граничные точки минимум в двух независимых burst-повторах.
6. Завершить row только если покрыты положительные и отрицательные тесты границы.

Accumulator должен поддерживать состояния:

```text
EMPTY
  → COLLECTING
  → ROW_CANDIDATE
  → ROW_READY
  → MAP_CANDIDATE
  → MAP_READY_CANDIDATE
  → REJECTED
```

Переход в `ROW_READY` разрешён только для одной конкретной row. Переход в `MAP_READY_CANDIDATE` разрешён только после `ROW_READY` для всех двенадцати rows. Это ещё не означает, что глобальный FOC `MAP_READY` установлен.

## 5. Преобразование измерений

### 5.1 Shunt vector

После offset correction:

\[
 x_k = \begin{bmatrix} i_{dc1,k} \\ i_{dc2,k} \end{bmatrix}
\]

Значения должны оставаться в инженерных единицах mA до вычисления статистики. Нельзя округлять каждый sample до Q15 перед оценкой: это ухудшит condition number и residual.

### 5.2 Reference phase vector

Для каждой row profile задаёт пару фаз, например `(U,V)`, `(V,W)` или `(W,U)`. Из полного reference:

\[
 y_k = \begin{bmatrix} i_{phase_a,k} \\ i_{phase_b,k} \end{bmatrix}
\]

Третья фаза используется для независимой проверки KCL:

\[
 e_{KCL,k} = i_{U,k} + i_{V,k} + i_{W,k}.
\]

Большой `e_KCL` означает плохую синхронизацию reference, насыщение или неверную полярность и должен отклонять row.

## 6. Оценка матрицы `M`

Для каждой row собираются `N` валидных samples, где `N` должен быть не меньше `N_MIN_ROW`; рекомендуемый минимум — 24, включая повторения и границы.

Определим:

\[
 X = [x_1\ x_2\ ...\ x_N],
 \qquad
 Y = [y_1\ y_2\ ...\ y_N].
\]

Оценка ordinary least squares:

\[
 M = YX^T(XX^T)^{-1}.
\]

Для матрицы 2×2:

\[
 S = XX^T =
 \begin{bmatrix}
 \sum x_{1,k}^2 & \sum x_{1,k}x_{2,k} \\
 \sum x_{1,k}x_{2,k} & \sum x_{2,k}^2
 \end{bmatrix},
\]

\[
 \det(S) = S_{00}S_{11} - S_{01}^2.
\]

Если `det(S)` меньше минимального порога или condition estimate превышает максимум, row отклоняется как недостаточно возбуждённая. Нельзя подставлять псевдообратную матрицу или нулевой коэффициент: это маскирует отсутствие независимых уравнений.

В embedded implementation рекомендуется фиксированная точка с расширенными аккумуляторами:

- `x` и `y`: signed 32-bit mA;
- sums of products: signed 64-bit минимум;
- intermediate determinant/product: signed 128-bit в offline builder либо масштабированная 64-bit арифметика с доказанными bounds;
- итоговые коэффициенты: `int32_t` в масштабе `CURRENT_RECON_COEFF_SCALE = 1000`.

Коэффициенты округляются только после прохождения всех quality checks:

```c
m00 = round(M00 * CURRENT_RECON_COEFF_SCALE);
m01 = round(M01 * CURRENT_RECON_COEFF_SCALE);
m10 = round(M10 * CURRENT_RECON_COEFF_SCALE);
m11 = round(M11 * CURRENT_RECON_COEFF_SCALE);
```

### 6.1 Residual validation

Для каждого sample вычисляется:

\[
 \hat y_k = Mx_k,
 \qquad
 r_k = y_k - \hat y_k.
\]

Row принимается только при одновременном выполнении всех условий:

| Метрика | Критерий |
|---|---|
| RMS residual каждой фазы | Ниже board-qualified `RMS_MAX_MA`. |
| Maximum absolute residual | Ниже `ABS_RESIDUAL_MAX_MA`. |
| Bias residual | Среднее residual около нуля, ниже `BIAS_MAX_MA`. |
| Leave-one-out или hold-out residual | Не хуже основного fit; минимум один repeat должен быть validation-only. |
| KCL residual | `RMS(e_KCL)` и max `|e_KCL|` ниже порогов. |
| Gain/polarity | Знак и диапазон каждого коэффициента соответствуют board profile. |
| Determinant | `det(M)` ненулевой и в квалифицированном диапазоне. |

Нельзя проверять только RMS: несколько outlier samples могут скрыть ошибочную полярность или плохо установленный trigger.

### 6.2 Робастность

Перед OLS применяются только детерминированные правила качества: saturation rejection, hard range rejection и median/MAD outlier rejection по заранее заданным board thresholds. Нельзя произвольно удалять samples до тех пор, пока residual не станет удобным.

Рекомендуемый порядок:

1. Отбросить hardware-invalid samples.
2. Отбросить samples вне Vbus/current/timing limits.
3. Вычислить median и MAD отдельно для `idc1`, `idc2`, reference phases.
4. Отбросить только samples за фиксированным `K_MAD` и сохранить число отброшенных.
5. Если после фильтрации меньше `N_MIN_ROW`, вернуть reject.
6. Выполнить OLS и validation.

## 7. Построение границ `OewPwmRegion`

`OewPwmRegion` — axis-aligned box в Q15:

```c
typedef struct {
    int16_t mu_min, mu_max;
    int16_t mv_min, mv_max;
    int16_t mw_min, mw_max;
    uint16_t min_margin_ticks;
    uint8_t valid;
    uint8_t reserved;
} OewPwmRegion;
```

Граница не должна вычисляться как простой min/max всех положительных samples. Такой min/max не доказывает безопасность внутренних точек и может включить неиспытанную область.

### 7.1 Разметка grid

Для каждой row sweep должен иметь дискретную grid в modulation-space. Каждая tested cell получает label:

```text
VALID      — ADC aperture полностью устойчива и timing evidence положительна
INVALID    — окно невалидно, trigger/settling нарушен или есть saturation
UNTESTED   — измерение отсутствует или evidence неполно
```

Кандидатный box допускается только если:

1. все grid cells внутри box имеют статус `VALID`;
2. ни один `INVALID` cell не находится внутри box;
3. вокруг каждой грани есть отрицательная boundary evidence либо scope-qualified guard;
4. расстояние от каждой грани до ближайшего invalid/untested cell не меньше `GUARD_Q15`;
5. box не пересекается с box другой row после применения `margin` и quantization guard.

Если grid слишком редкая для доказательства box, row должна быть отклонена, а не расширена интерполяцией.

### 7.2 Вычисление границ

Для каждой координаты сначала вычисляется envelope положительно проверенных cells, затем применяется консервативное сужение:

\[
 min' = min_{valid} - G_{q15},
 \qquad
 max' = max_{valid} + G_{q15}
\]

Это расширение допустимо только если все дополнительные cells внутри результата имеют положительную evidence. Если evidence отсутствует, используется сужение, а не расширение:

\[
 min' = min_{certified} + G_{q15},
 \qquad
 max' = max_{certified} - G_{q15}.
\]

Конкретная политика (`expand` или `shrink`) должна быть зафиксирована profile; generic accumulator не выбирает её эвристически.

`min_margin_ticks` равен минимуму измеренного timing margin среди всех samples, использованных для сертификации row, за вычетом fixed `TIMING_GUARD_TICKS`. При результате меньше требуемого board threshold row отклоняется.

### 7.3 CCR → Q15

Преобразование должно использовать тот же exact timer model, что и PWM service path. Для center-aligned PWM при `mid = (ARR+1)/2`:

\[
 m = round\left(\frac{(CCR-mid)\cdot32768}{mid}\right).
\]

Нельзя использовать независимое приближённое преобразование в host tool и firmware builder. Формула, rounding mode, clipping policy и допустимый CCR range должны быть общими и покрыты boundary tests.

## 8. Проверка всей карты

После получения 12 row candidates accumulator формирует `OewCurrentMap`, но передаёт его loader только после следующих проверок:

| Проверка | Действие при отказе |
|---|---|
| Все 12 rows `valid` | `REJECT_INCOMPLETE_MAP`. |
| Все `CurrentReconEntry` имеют разные phase IDs | Reject. |
| Все `det(M)` non-zero и в диапазоне | Reject. |
| Все regions sane и имеют margin | Reject. |
| Нет overlap regions | Reject. |
| Нет coverage holes для разрешённого scheduler domain | Reject. |
| Startup vector покрыт startup region | Reject. |
| Board/timer/trigger identity совпадает | Reject. |
| CRC совпадает с canonical serialization | Reject. |
| Reference/evidence provenance полна | Reject. |

Поля `reserved` и padding должны быть нулевыми до CRC. Canonical serialization обязана быть стабильной между host builder и firmware loader.

## 9. Fail-closed интеграция с FOC

Accumulator не имеет права вызывать `CurrentRecon_LoadMap()` во время сбора. Рекомендуемый API:

```c
bool MapMeasurementAccumulator_Begin(
    const MapMeasurementQualification *qualification);

MapAccumStatus MapMeasurementAccumulator_Add(
    const MapMeasurementSample *sample);

MapAccumStatus MapMeasurementAccumulator_FinalizeRow(
    uint8_t sector, uint8_t window,
    MapRowReport *report);

MapAccumStatus MapMeasurementAccumulator_FinalizeMap(
    OewCurrentMap *candidate,
    MapMapReport *report);
```

Отдельный wrapper выполняет загрузку:

```c
bool Commissioning_LoadMeasuredMap(const OewCurrentMap *candidate)
{
    if (MapCapture_IsActive() || FOC_IsRunning() || VFC_IsRunning() ||
        Autotune_IsActive() || PWM_IsEnabled() || ADC_InjectedIsArmed() ||
        PROTECT_IsFault()) {
        return false;
    }
    if (!MapCandidate_IdentityMatchesLive(candidate)) return false;
    return CurrentMap_LoadMeasured(candidate, &live_identity) &&
           CurrentMap_IsReady();
}
```

В штатной production build должны сохраняться:

```c
#define OEW_MAP_CAPTURE 0
#define OEW_MAP_L3 0
```

Даже commissioning build не должен разрешать произвольный profile из UART. Profile ID выбирает только immutable compiled record, а reference coefficients/evidence должны быть приняты board review.

FOC admission остаётся строго после map load:

```text
CurrentRecon_IsReady()
  → CurrentMap_SelectInitialStartupContext()
  → PWM_SetControlVector(valid context)
  → ADC_SetControlAdmission(true)
  → ADC_InjectedStart()
  → PWM_Enable()
  → foc_running = 1
```

Любой отказ обязан оставить `CurrentMap_IsReady()==false` или очистить карту перед возвратом ошибки. Нельзя использовать номинальный `(0,0,true)` startup context и нельзя считать service-capture frame control-valid.

## 10. Критерии отказа accumulator

Accumulator возвращает reject при любом из условий:

- отсутствует независимый phase reference;
- reference sample не синхронизирован с capture sequence;
- ADC saturation, overrun, desynchronization или invalid offset;
- trigger revision, ARR, PWM frequency или board identity mismatch;
- несовпадение TIM1/TIM8 snapshot с approved vector;
- недостаточная rank/condition matrix `XXᵀ`;
- слишком большой residual, bias, KCL error или hold-out error;
- неверная полярность, gain или determinant;
- недостаточно samples после deterministic outlier filtering;
- есть `UNTESTED`/`INVALID` cells внутри candidate region;
- aperture margin ниже threshold;
- overlap или coverage hole в итоговой карте;
- CRC/provenance/serialization mismatch;
- активны PWM, ADC, FOC, V/f, Auto-Tune или protection fault.

Ошибку нельзя преобразовывать в warning с продолжением. Diagnostics должны включать row, reason code, accepted/rejected counts и identity, но не должны раскрывать путь к энергизации.

## 11. Тестирование

### 11.1 Unit tests

Нужны детерминированные тесты для:

| Группа | Сценарии |
|---|---|
| Matrix fit | Identity/gain matrix, sign inversion, swapped shunts, near-singular excitation, zero excitation. |
| Residuals | Low noise pass, bias fail, one large outlier, KCL fail, hold-out fail. |
| Quantization | CCR endpoints, ARR odd/even, rounding boundaries, overflow guards. |
| Regions | Fully covered box, untested hole, invalid cell inside box, adjacent boxes touching, overlap after guard. |
| Identity | Wrong board revision, ARR, PWM frequency, trigger revision, polarity profile. |
| Session | Duplicate sequence, non-monotonic sequence, mixed capture IDs, incomplete row, repeated row. |
| Safety | Active PWM/ADC/FOC/V/f/Auto-Tune/fault must block final load. |
| Serialization | Reserved/padding zeroing, stable CRC, corrupted candidate rejection. |

### 11.2 Hardware-in-the-loop

Для каждой row необходимо подтвердить:

1. trigger marker совпадает с aperture snapshot;
2. внешний phase reference синхронен с ADC frame;
3. valid/invalid boundary совпадает с осциллографом;
4. measured matrix предсказывает фазные токи на независимых hold-out vectors;
5. после успешной загрузки FOC получает только разрешённые rows;
6. при fault/break/load failure bridge остаётся выключенным.

### 11.3 Acceptance gate

L3 можно считать board-qualified только если все 12 rows имеют независимую reference evidence, все matrix/region thresholds пройдены на hold-out данных, карта проходит `CurrentMap_LoadMeasured()`, а негативные тесты доказывают отсутствие `ADC/PWM/FOC` admission при любой ошибке.

## 12. План реализации

Реализация должна идти в следующем порядке:

1. Добавить offline/reference format и provenance manifest.
2. Реализовать общий exact CCR↔Q15 conversion module.
3. Добавить per-row accumulator и deterministic filtering.
4. Добавить fixed-point/host solver с одинаковым canonical output.
5. Добавить residual, KCL, condition и hold-out validators.
6. Добавить grid-based region certifier с conservative guard.
7. Добавить candidate serializer/CRC.
8. Подключить commissioning wrapper к `CurrentMap_LoadMeasured()`.
9. Добавить negative tests до включения любого board-specific profile.
10. Только после board review заменить generic `return false` в compiled profile allow-list.

До завершения этих шагов текущие `OEW_MAP_CAPTURE=0`, `OEW_MAP_L3=0` и fail-closed FOC gates должны оставаться без изменений.
