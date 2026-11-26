# Phase-4 — структура будущей run card для определения `ε_real`

**Статус:** draft structure / не является разрешением на эксперимент.

**Назначение:** зафиксировать будущую структуру измерительного прогона, который должен
экспериментально определить `ε_real` — физическую энергию ошибки аппроксимации,
используемую в Phase-4 acceptance. Этот файл не содержит результатов, не открывает
Maxwell/FEM и не изменяет действующий ToR.

**Связь с замороженным контрактом:**

- действующая редакция: **V0.4**;
- шаг: `h_mech = 0.5°` (механические); `h_el = p·h_mech = 1.5°` при `p = 3`;
- acceptance threshold: `1%`;
- ROM: `33×12`;
- `ε_W = 7.200 µJ` остаётся full-ROM proxy/ceiling, а **не Maxwell measurement**;
- текущий conservative result `E_trunc + E_corner = 1.327% > 1%` не пересматривается
  этой run card;
- `ε_real` до выполнения и разбора эксперимента считается **UNKNOWN**.

> Любое заполнение чисел, изменение acceptance logic или замена proxy на физическое
> измерение требует отдельного решения владельца Phase-4. Сам факт наличия этой
> структуры не является разрешением на подачу HV или запуск двигателя.

---

## 1. Цель и измеряемая величина

### 1.1 Исследовательский вопрос

> Какое значение `ε_real` следует использовать для физически обоснованной оценки
> ошибки ROM при зафиксированных `V0.4`, `h = 0.5°` и ROM `33×12`?

### 1.2 Операционное определение `ε_real`

Определение задано канонически и **в run card не выбирается**:

- `docs/PHASE4_EPS_REAL_DRAFT_MEASUREMENT_CONTRACT_D1_D8.md` §1 —
  `ε_r = ∫_{I_r} |T_ROM,r(θ;h) − T_ref,r(θ)| dθ`, `h = 0.5°` mech,
  `I_r` — один полный электрический период;
- §1 — агрегация `ε̂_g = (1/n_g)·Σ_{r∈g} ε_r`, `ε_real = max_{g∈G_valid} ε̂_g`;
- §8 — нормировка `E_g = 100%·ε̂_g/W_ref,g`, `E_real = max_g E_g`;
- §23.2 — верхняя граница `ε_g,upper = mean_g + t(1−α_s, n_g−1)·s_g/√n_g`;
- §25.1 — единицы feasibility (`E_r`, `E_g`, `s_g` — п.п. от `W_ref`);
- §25.3/§25.5 — `m_max`, `N_θ,ref`, escalation, `G_gate`, `α_gate` объявляются
  до данных, затем — `FEASIBLE` / `CONTRACT NOT FEASIBLE`.

Альтернативные определения (средняя / RMS / 95-й перцентиль / worst-case) здесь
**не рассматриваются**: выбор между ними снят при фиксации контракта. Замена
определения — новая редакция контракта, а не заполнение run card.

В run card заполняются только **фактические параметры кампании**, которые контракт
оставляет объявлению:

- reference-канал и его паспортные характеристики (§25.2);
- `N_θ,ref`, `f_s`, `f_3dB`, anti-alias — по §23.1 и §25.2;
- источник угла (`enc_angle` / `theta`) и флаг когерентности клока;
- `m_max`, `G_gate`, `n_g`, `α_gate`, `α_s` — по §25.3;
- единицы и преобразование в процент acceptance metric (по §25.1);
- фактические значения: VBUS, температура, скорость/`f_e`, setpoints.

До заполнения этих полей численная оценка не считается `ε_real`.

### 1.3 Что эксперимент не должен утверждать

Эксперимент не доказывает автоматически:

- корректность всей FOC/safety-цепочки;
- безопасность работы при HV;
- адекватность Maxwell/FEM;
- переносимость результата на другой мотор, плату, VBUS, режим или firmware image;
- прохождение acceptance, если не выполнены все заранее зафиксированные гейты.

---

## 2. Baseline и неизменяемые входы

Заполнить до подготовки стенда:

| Поле | Значение | Источник / hash |
|---|---|---|
| Phase-4 revision | `V0.4` | ToR |
| firmware commit | `[TBD]` | `git rev-parse HEAD` |
| firmware image SHA-256 | `[TBD]` | artifact |
| board revision | `[TBD]` | паспорт |
| motor / serial | `[TBD]` | паспорт |
| ROM dimensions | `33×12` | ToR |
| angular step | `0.5°` mech = `1.5°` el @ `p=3` | ToR + `tools/bm1109_doe1.py:89–90` |
| PWM / control configuration | `[TBD]` | firmware + capture |
| VBUS / operating point | `[TBD]` | run matrix |
| current / torque range | `[TBD]` | run matrix |
| reference provider revision | `[TBD]` | instrument/config hash |
| analysis tool revision | `[TBD]` | commit / package hash |

Запрещено менять после первого принятого прогона без создания новой campaign
revision. Любая замена firmware, карты, калибровки, датчика или анализа должна
получить новый `campaign_id` либо быть явно отмечена как invalidating change.

---

## 3. Аппаратная конфигурация и независимость reference

### 3.1 Измерительная цепь

Заполнить таблицу фактическим подключением:

| Канал | Сигнал | Прибор / probe | Точка подключения | Масштаб | Роль |
|---|---|---|---|---|---|
| 1 | `[phase / torque / position]` | `[TBD]` | `[TBD]` | `[TBD]` | reference / observable |
| 2 | `[TBD]` | `[TBD]` | `[TBD]` | `[TBD]` | reference / observable |
| 3 | `[TBD]` | `[TBD]` | `[TBD]` | `[TBD]` | timing / trigger |
| 4 | `[TBD]` | `[TBD]` | `[TBD]` | `[TBD]` | auxiliary |

### 3.2 Доказательство независимости

Reference должен быть физически независим от двух каналов/значений, по которым
оценивается ROM. Записать:

- почему reference не вычисляется из `idc1/idc2`, ADC-кодов или той же прошивочной
  модели;
- отдельную цепь датчика, усилителя, АЦП/осциллографа и земли;
- способ проверки полярности, gain и phase alignment;
- какие признаки выявляют общую ошибку, насыщение или потерю синхронизации.

Если независимость не доказана, результат классифицируется как **непригодный для
определения `ε_real`**, даже если численная подгонка выглядит хорошей.

### 3.3 Калибровка до прогона

- [ ] нулевые уровни и drift записаны;
- [ ] gain/scale reference проверен независимым способом;
- [ ] bandwidth и anti-aliasing зафиксированы;
- [ ] trigger path и задержка измерены;
- [ ] polarity каждой цепи подтверждена;
- [ ] saturation range и noise floor измерены;
- [ ] interlock и emergency stop проверены;
- [ ] осциллограф/DAQ timebase и probe compensation записаны.

---

## 4. Safety gates и условия остановки

### 4.1 До подачи энергии

- [ ] backup выполнен и его hash сохранён;
- [ ] образ и board identity проверены;
- [ ] safety/FOC default-deny не изменён;
- [ ] no-HV preflight завершён;
- [ ] fixture, экраны, заземление и interlock проверены;
- [ ] оператор и наблюдатель назначены;
- [ ] путь сохранения raw-данных создан вне репозитория;
- [ ] stop criteria прочитаны вслух и согласованы.

### 4.2 Немедленная остановка и invalidation run

Остановить текущий run и пометить его `INVALID`, если есть хотя бы одно:

- потеря trigger или неизвестная временная привязка;
- saturation, clipping, dropout или overflow любого обязательного канала;
- fault, break, watchdog, reset или потеря связи с целью;
- изменение VBUS, нагрузки, firmware, карты или instrument setup вне протокола;
- reference невалиден, не независим или не синхронизирован;
- превышение заранее утверждённых тока, момента, температуры или времени;
- raw-файл неполный, перезаписан или не имеет checksum;
- оператор не может восстановить фактическую последовательность действий.

`INVALID` не превращается в `PASS` повторным выбором удобного фрагмента данных.

---

## 5. Run matrix

Матрица должна быть утверждена до первого измерения. Минимальная структура:

| Dimension | Levels / range | Planned repeats | Reason |
|---|---|---:|---|
| ROM row / cell | `[TBD]` | `[TBD]` | coverage |
| angle / phase | `[TBD]` | `[TBD]` | angular error |
| VBUS | `[TBD]` | `[TBD]` | operating envelope |
| load / torque | `[TBD]` | `[TBD]` | physical regime |
| speed / frequency | `[TBD]` | `[TBD]` | dynamic regime |
| direction | `[TBD]` | `[TBD]` | asymmetry |
| warm-up state | `[cold / warm / TBD]` | `[TBD]` | drift |
| repeat / day | `[TBD]` | `[TBD]` | reproducibility |

Зафиксировать отдельно:

- какие точки являются calibration-only;
- какие точки являются fit set;
- какие точки являются hold-out/validation set;
- какие boundary/corner points обязательны;
- минимальное число валидных повторов на точку;
- правило покрытия, при котором отсутствие точки делает кампанию неполной.

---

## 6. Процедура одного run

### 6.1 Идентификация

1. Записать `campaign_id`, `run_id`, UTC timestamp и оператора.
2. Проверить firmware/image/config hashes.
3. Снять нулевые и reference baselines.
4. Проверить свободный канал/триггер на контрольном событии.
5. Записать фактические setpoints до старта.

### 6.2 Снятие данных

1. Подготовить одновременно прошивочный capture и независимый reference.
2. Вооружить trigger и убедиться, что post/pre-trigger окна достаточны.
3. Выполнить только утверждённый шаг run matrix.
4. Сохранить raw waveform/stream, UART/telemetry и machine metadata.
5. Повторить заданное число раз без изменения setup.
6. После серии снять zero/drift контроль.
7. Выключить энергию по утверждённой последовательности.

### 6.3 Контроль приёмки run

Run получает `CANDIDATE`, только если:

- все обязательные каналы присутствуют и покрывают одно событие;
- временная привязка reference ↔ firmware capture однозначна;
- нет stop/invalidation condition;
- setpoints и фактические значения записаны;
- raw-файлы и checksums сохранены.

Иначе результат — `INVALID` или `ERROR`, но не `CANDIDATE`.

---

## 7. Обязательные артефакты

Для каждого run сохранить рядом, но не добавлять raw-данные в git:

```text
<campaign_id>/<run_id>/
  metadata.json
  setup.txt
  operator_log.txt
  firmware_image.sha256
  instrument_config/
  raw/
    firmware_capture.*
    reference_capture.*
    trigger_capture.*
    uart.log
  checksums.sha256
  derived/
    aligned_samples.csv
    quality_metrics.json
  verdict.json
```

`metadata.json` должен содержать минимум:

- IDs и hashes всех входов;
- board/motor/instrument identity;
- setpoints и фактические измерения;
- sample rate, trigger, scale, offsets, filters;
- temperature, VBUS, current/torque/speed;
- zero/drift values;
- operator actions и deviations;
- status каждого канала;
- stop/invalidation flags;
- software versions и command line.

---

## 8. Offline reduction и выравнивание

Этот раздел заполняется до эксперимента и затем не меняется постфактум.

1. Прочитать raw без потери исходной точности.
2. Проверить checksums и структурный контракт файлов.
3. Свести временные шкалы по утверждённому trigger marker.
4. Применить только заранее объявленные offset/gain/filter.
5. Отметить saturation, missing samples, trigger mismatch и rejected windows.
6. Разделить fit, repeat и hold-out данные до расчёта результата.
7. Рассчитать reference quantity и ROM quantity на одинаковом окне.
8. Получить ошибку и интегральную energy metric по каноническому определению
   (§1.2 → contract §1/§8), а не по выбору исполнителя.
9. Сохранить промежуточные таблицы и параметры анализа.

Нельзя выбирать окно, фильтр или subset по знанию итогового значения.

---

## 9. Расчёт `ε_real`

Заполнить канонические формулы и численные параметры:

```text
reference quantity:       [TBD] — паспорт канала (§25.2)
ROM quantity:             [TBD] — источник ROM/config hash
integration window:       [TBD] — фактический θ0 и alignment (D1: 360° эл)
time/sample weighting:    [TBD] — трапеции на 240 узлах (contract §22.2)
outlier rule:             [TBD]

error definition:         |T_ROM(θ) − T_ref(θ)|, h = 0.5° mech (contract §1)
per-run estimator:        ε_r = ∫|ΔT|dθ по одному эл. периоду (contract §1)
campaign estimator:       ε_real = max_g mean_{r∈g} ε_r (contract §1)
uncertainty estimator:    mean_g + t(1−α_s, n_g−1)·s_g/√n_g (contract §23.2)
```

Результат обязан включать минимум:

| Метрика | Значение | Единицы | Набор данных |
|---|---:|---|---|
| `ε_real` point estimate | `[TBD]` | `[TBD]` | `[fit/validation]` |
| repeatability | `[TBD]` | `[TBD]` | `[repeats]` |
| between-run variation | `[TBD]` | `[TBD]` | `[runs]` |
| uncertainty / interval | `[TBD]` | `[TBD]` | `[method]` |
| worst accepted case | `[TBD]` | `[TBD]` | `[coverage]` |

Отдельно сравнить с `ε_W`, но не подменять ими друг друга:

```text
ε_real estimate: [TBD]
ε_real uncertainty upper bound: [TBD]
ε_W proxy/ceiling: 7.200 µJ
relationship: [TBD; descriptive only until acceptance rule is approved]
```

---

## 10. Quality gates и decision tree

### 10.1 Гейты данных

- **G0 — setup identity:** все hashes и конфигурации совпадают;
- **G1 — safety/coverage:** run выполнен в разрешённом envelope;
- **G2 — reference independence:** независимость доказана;
- **G3 — synchronisation:** alignment и timing quality проходят пороги;
- **G4 — signal quality:** noise/saturation/dropout проходят пороги;
- **G5 — repeatability:** повторы согласованы;
- **G6 — hold-out:** validation set проходит заранее заданное правило;
- **G7 — coverage:** покрыты утверждённые ROM rows/corners/regimes;
- **G8 — estimator:** uncertainty и worst-case обработаны по плану.

Порог каждого гейта должен быть заполнен до первого принятого измерения.

### 10.2 Вердикты

| Вердикт | Значение |
|---|---|
| `PASS_CANDIDATE` | данные пригодны для расчёта, но acceptance ещё не пересмотрен |
| `PASS_MEASUREMENT` | `ε_real` определён в рамках утверждённой measurement definition |
| `INCOMPLETE` | coverage или repeats недостаточны |
| `INVALID` | нарушен контракт данных/синхронизации/независимости |
| `ERROR` | инфраструктурный отказ или потеря достоверности |
| `BLOCKED` | safety или аппаратный гейт не открыт |

Ни один из этих вердиктов сам по себе не меняет Phase-4 acceptance result.

---

## 11. Итоговый пакет и review

Итоговый пакет должен содержать:

- signed/hashed manifest raw-данных;
- таблицу покрытия run matrix;
- список invalid/incomplete runs с причинами;
- независимый анализатор или воспроизводимую команду запуска;
- derived tables и графики reference vs ROM;
- per-run и aggregate metrics;
- uncertainty budget;
- deviation log;
- итоговый `ε_real` с единицами и definition ID;
- явное сравнение с `ε_W`;
- предложение, **но не автоматическое изменение**, acceptance decision;
- подписи/идентификаторы reviewer и operator.

### Финальный шаблон решения

```text
Campaign:                 [TBD]
Definition ID:            [TBD]
Firmware / setup hash:    [TBD]
Coverage status:          [TBD]
Reference independent:    [PASS/FAIL/BLOCKED]
Data quality:              [PASS/FAIL/BLOCKED]
ε_real estimate:           [TBD]
ε_real uncertainty upper:  [TBD]
ε_W proxy:                 7.200 µJ
Measurement verdict:       [TBD]
Phase-4 acceptance impact: [NO CHANGE / REVIEW REQUIRED]
Maxwell/FEM status:        BLOCKED
Reviewer:                  [TBD]
Date:                      [TBD]
```

> До отдельного разрешения этот документ остаётся только структурой будущего
> measurement/run card. Он не разрешает HV, не требует правок прошивки и не
> заменяет утверждённый ToR.
