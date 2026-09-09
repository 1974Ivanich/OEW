# Спецификация рефакторинга геометрии регионов карты OEW

**Статус:** проект спецификации, не разрешение на включение geometry-режима в production

## 1. Цель и критерий готовности

Геометрический регион должен описывать не независимый axis-aligned прямоугольник, а пару семантик: принадлежность modulation-вектора одному SVPWM-сектору и принадлежность амплитуды одному modulation window. Одна точка должна принадлежать не более чем одному сектору и одному окну. Сертификация, runtime selector и firmware admission обязаны использовать одну и ту же реализацию предиката.

Рефакторинг считается готовым только если выполняются все условия:

| Gate | Требование |
|---|---|
| Сектор | Каждая точка внутри допустимого modulation domain получает ровно один sector ID либо отклоняется как boundary/out-of-domain |
| Амплитуда | `mod=10000` принадлежит только window 1; окна не пересекаются |
| Certifier | Certified cells проверяются семантическим predicate, а не только six-coordinate bounds |
| Selector | Runtime выбирает ровно один region или возвращает `false` |
| Admission | Artifact с overlap, hole policy violation, невалидной geometry metadata или несовместимым revision отклоняется |
| Campaign | В каждом из 12 рядов есть достаточная внутренняя и пограничная evidence |
| Interpolation | RMS и maximum edge error не превышают qualification limits на реальных samples |
| Safety | После hardware verification и safety-owner approval geometry mode разрешается отдельно от legacy mode |

## 2. Нормализованная modulation domain

Точка представляется тройкой signed Q15: `(mu, mv, mw)`. Для balanced three-phase command обязательно выполняется:

```text
mu + mv + mw = 0
```

Проверка суммы выполняется с 32-битной арифметикой и допускает только явно заданный `kcl_tolerance_q15`. Для геометрического selector рекомендуется использовать exact zero после canonical SVPWM conversion; если округление неизбежно, tolerance должен быть частью qualification и wire provenance.

Амплитудная метрика определяется как:

```text
mod_q15 = max(abs(mu), abs(mv), abs(mw))
```

`abs(INT16_MIN)` вычисляется в `int32_t`, поэтому переполнения signed `int16_t` не возникает. Значение `mod_q15` является unsigned `int32_t` и сравнивается с Q15-порогами после проверки диапазона.

Эта метрика сохраняет физический смысл ограничения максимального фазного modulation command и допускает центральные точки вида `(8192, 0, -8192)`.

## 3. Детерминированные окна

Для утверждённых предложенных порогов используется следующая полуоткрытая/закрытая схема:

| Window | Predicate |
|---|---|
| 0 | `6000 <= mod_q15 < 10000` |
| 1 | `10000 <= mod_q15 <= 14000` |

Таким образом, `mod_q15=10000` принадлежит только window 1. Значения меньше 6000 и больше 14000 не принадлежат ни одному geometry region. Нулевые или перевёрнутые thresholds являются `BAD_ARGUMENT`.

Пороговые значения должны храниться в qualification и попадать в provenance revision. Изменение threshold требует новой qualification revision и повторной campaign certification.

## 4. Sector predicate и boundary ownership

Сектор определяется по descending order трёх phase values и знаку zero-crossing. Внутренняя область использует строгие отношения из SVPWM-контракта:

| Sector | Interior predicate |
|---:|---|
| 0 | `mu > 0 > mv > mw` |
| 1 | `mu > mw > mv` при `mu > 0 > mv` |
| 2 | `mv > 0 > mu > mw` |
| 3 | `mv > mw > mu` при `mv > 0 > mu` |
| 4 | `mw > 0 > mv > mu` |
| 5 | `mw > mu > mv` при `mw > 0 > mv` |

Для runtime boundary ownership нельзя использовать шесть независимых rectangle checks. Рекомендуется канонический алгоритм:

1. Проверить KCL и amplitude window.
2. Построить упорядочивание фаз с tie-break priority `U > V > W`.
3. Выбрать первый сектор из канонического списка, совместимый с ordering и zero-crossing.
4. При равенстве фаз использовать только этот tie-break, а не допускать несколько секторов.
5. Если точка лежит на запрещённой нулевой или амплитудной границе, применить таблицу ownership, одинаковую в certifier и selector.

Вариант для первой production revision: boundary points на inter-sector ties не сертифицировать, а классифицировать как `BOUNDARY_UNTESTED`, если hardware campaign не содержит отдельной evidence для выбранного владельца границы. Это безопаснее, чем неявно расширять сектор.

## 5. Изменение модели данных

`OewPwmRegion` остаётся совместимым контейнером legacy rectangle bounds, но не должен быть единственным носителем geometry semantics. Для новой map revision необходимо формально выбрать один из вариантов:

| Вариант | Решение |
|---|---|
| A | Расширить wire-format новой revision и добавить `geometry_mode`, sector predicate revision, window thresholds и boundary policy |
| B | Добавить отдельный `OewMapGeometry` в artifact, включить его в CRC и admission |
| C | Для revision 2 оставить `use_geometry=false`, а geometry artifacts блокировать до появления нового wire revision |

Не разрешается тайно переиспользовать `reserved` в revision 2. До принятия новой wire semantics `use_geometry=1` должен быть host-only experimental режимом и не должен выпускать firmware-loadable artifact.

## 6. План реализации

### Phase 1 — contract freeze

Нужно утвердить amplitude metric, thresholds, KCL tolerance, sector boundary policy, geometry revision и safety ownership. Изменения thresholds или tie-break после approval считаются новой contract revision.

### Phase 2 — shared geometry library

Создать единую host/firmware-compatible библиотеку с функциями `Geometry_ModulationQ15`, `Geometry_ClassifySector`, `Geometry_ClassifyWindow` и `Geometry_ClassifyPoint`. Certifier и selector не должны дублировать эти predicates.

### Phase 3 — certifier

Certifier должен проверять semantic predicate каждой certified cell, отклонять valid cell вне sector/window, отклонять forbidden boundary, требовать достаточное число cells около каждой внутренней границы и не генерировать только rectangle bounds для geometry artifact.

### Phase 4 — selector и admission

Selector должен классифицировать vector через shared geometry library и возвращать ровно один `(sector, window)`. Admission должен проверять geometry revision, thresholds, boundary policy, KCL policy и canonical region metadata. Rectangle overlap между разными geometry regions не должен быть самостоятельным reject, если semantic regions доказано disjoint; до такой реализации geometry artifact следует отклонять.

### Phase 5 — campaign rebuild

BOAR campaign необходимо пересобрать с центрами, совместимыми с amplitude metric и sector predicate. Все 12 строк должны содержать certified center, interior points, lower-edge points, upper-edge points и boundary evidence согласно policy.

### Phase 6 — interpolation and hardware gates

Сначала запускается реальный campaign edge-analysis harness. Затем выполняются map decode/admission/selector tests, bench mapload, FOC start и controlled acceleration. Production enablement возможно только после safety-owner approval.

## 7. Acceptance tests

Минимальный набор включает exhaustive deterministic tests для шести секторов, обеих окон, `mod=5999`, `6000`, `9999`, `10000`, `14000`, `14001`, zero-crossing, equal-phase ties, KCL violation, holes и overlaps. Отдельно проверяются equivalence host certifier versus firmware selector на одном наборе vectors.

## 8. Interpolation evidence

Для каждой пары `(sector, window)` harness должен обучить reconstruction matrix на certified center samples и оценить held-out samples, расположенные ближе всего к lower и upper modulation edges. Отчёт обязан содержать число training/edge samples, modulation span, RMS error, maximum absolute error и qualification limits. Недостаток edge spread, отсутствие holdout samples или отсутствие theoretical limit являются `BLOCKED`, а не `PASS`.

В репозитории добавлен воспроизводимый harness `tools/map_geometry_interpolation_analysis.py`. Он читает `manifest.json` и `samples.jsonl`, вычисляет `mod_q15`, проверяет принадлежность samples заявленному окну, строит center OLS-модель и сравнивает предсказанные phase currents с фактическими reference currents у наблюдаемых нижней и верхней границ. Отчёт сохраняется в JSON и имеет статусы `PASS`, `FAIL` или `BLOCKED`.

Пример запуска:

```text
python tools/map_geometry_interpolation_analysis.py <campaign_dir> report.json
```

Доступная в текущем репозитории `tools/campaign_demo` не является утверждённой BOAR campaign: её samples имеют modulation примерно `2465..3029 Q15`, тогда как proposed windows начинаются с `6000 Q15`. Harness корректно выдаёт `FAIL` для всех 12 строк по причине выхода samples за deterministic amplitude windows. Это safety verdict и не является доказательством interpolation PASS для BOAR.

## References

[1]: https://github.com/1974Ivanich/OEW "OEW repository"
[2]: https://en.wikipedia.org/wiki/Space_vector_modulation "Space vector modulation overview"
