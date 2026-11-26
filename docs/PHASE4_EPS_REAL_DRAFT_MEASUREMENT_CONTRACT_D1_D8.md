# Phase-4 — Draft Measurement Contract D1–D8 для `ε_real`

**Статус:** `DRAFT / CONTRACT REVIEW ONLY`.

Цепочка контракта:

```text
measurement unit → stratum → bootstrap block → uncertainty budget
→ ε_real / E_real → upper decision boundary
```

Этот документ не разрешает measurement execution, HV, запуск двигателя или
Maxwell/FEM. Он не изменяет Phase-4 V0.4, ROM, `ε_W` или текущий acceptance.

## 0. Неизменяемый baseline

| Объект | Состояние |
|---|---|
| Phase-4 | V0.4, без изменений; источник привязан: `9ea121c` (§18) |
| `h` | `0.5°` mechanical (`TORQUE_H_MECH_DEG`) = `1.5°` electrical при `p=3` |
| ROM | `33×12` |
| Current acceptance | `E_trunc + E_corner = 1.327% > 1%` — `FAIL` |
| `ε_W` | `7.200 µJ`, proxy/ceiling, не measurement |
| `ε_real` | `UNKNOWN` |
| Measurement / HV | `NOT AUTHORIZED` |
| Maxwell/FEM | `BLOCKED` |

## 1. Кандидатное математическое определение

Для одного valid measurement unit `r`:

\[
\varepsilon_r=\int_{I_r}
|T_{\mathrm{ROM},r}(\theta;h)-T_{\mathrm{ref},r}(\theta)|d\theta,
\qquad h=0.5^\circ.
\]

Для агрегированной группы `g`:

\[
\widehat{\varepsilon}_g=\frac{1}{n_g}\sum_{r\in g}\varepsilon_r.
\]

Кандидатная point estimate кампании:

\[
\boxed{\widehat{\varepsilon}_{\mathrm{real}}=
\max_{g\in\mathcal G_{\mathrm{valid}}}\widehat{\varepsilon}_g}
\]

`ε_real` — point estimate в J; uncertainty и decision boundary являются
отдельными объектами. Это `ACCEPTED FOR FURTHER CONTRACT REVIEW`, не frozen
measurement contract.

## 2. D1 — угловое окно

Кандидат:

\[
P_\theta=360^\circ=2\pi\ \mathrm{rad\ electrical},
\qquad I_r=[\theta_{0,r},\theta_{0,r}+P_\theta].
\]

Окно содержит один полный электрический период в канонической угловой системе
V0.4. Шаг сетки V0.4 задан в **механических** градусах: `TORQUE_H_MECH_DEG = 0.5°`;
электрический стенсил `h_el = p·h_mech = 1.5°` при `p = 3`
(`tools/bm1109_doe1.py:89–90, 410, 1265–1266`: сдвиг `p * TORQUE_H_MECH_DEG`,
в summary пишутся `h_mech_deg` и `h_e_deg`). Поэтому `h` объявляется отдельно для
каждой координаты; отождествление `h = 0.5°` с электрическим шагом снято (§18).
Начало периода, phase alignment и stationarity criteria объявляются до анализа
ошибки; post-hoc phase/window selection запрещён. Переходы включаются или
исключаются только по заранее объявленному правилу.

Кандидатный phase marker: `θ0 = 0° electrical`, canonical electrical zero V0.4.
Если trigger не совпадает с marker, применяется только заранее записанный
циклический offset, одинаковый для ROM и reference.

**Статус D1:** `PROPOSED / SOURCE PINNED (9ea121c) / h_mech≠h_el CORRECTED / NOT FROZEN`.

## 3. D5 — canonical measurement unit

\[
r=(c,q,o,s,k,n),
\]

где `c=(i,j)` — ROM cell (`0≤i<33`, `0≤j<12`), `q` — role,
`o` — operating condition, `s` — session/setup, `k` — repeat, `n` — period.

Roles:

```text
NOMINAL | BOUNDARY | CORNER | TRANSITION | HOLDOUT
```

Unit — один полный угловой период, не отдельный time sample. Unit входит в
`R_valid` только при полной angular alignment, независимом reference, отсутствии
saturation/dropout/fault/reset/trigger mismatch, записанных actual values,
наличии raw+metadata+checksums и прохождении gates. Невалидные units сохраняются
как `INVALID` с причиной.

**Статус D5:** `ACCEPTED FOR FURTHER CONTRACT REVIEW`.

## 4. D2 — stratum и coverage

Stratum key:

```text
(campaign, board, motor, firmware_image, setup,
 operating_condition, cell_i, cell_j, point_role)
```

`repeat`, `period` и bootstrap resample в identity stratum не входят.

Nominal coverage — все `33×12=396` cells. Auxiliary strata:

- `BOUNDARY` — внутренние/внешние границы, где меняется ROM entry;
- `CORNER` — 2D corners и внешние corners domain;
- `TRANSITION` — distinct смена соседних entries/формул;
- `HOLDOUT` — заранее выделенный validation subset во всех классах.

Одна точка может иметь несколько coverage tags, но одну canonical unit record.
`COVERAGE_COMPLETE` разрешён только при полном nominal и обязательном auxiliary
coverage. Missing mandatory point даёт `INCOMPLETE`, не заменяется ближайшей.

**Статус D2:** `ACCEPTED FOR FURTHER CONTRACT REVIEW`.

## 5. D3 — repeats и hierarchy

```text
campaign
└── session
    └── setup / calibration
        └── operating condition
            └── stratum: cell + role
                └── repeat block
                    └── full angular period
                        └── samples
```

Недостаток valid blocks даёт `INCOMPLETE`, а не меньший вес группы.

**Статус D3:** `PROPOSED — CANDIDATE DEFAULT`.

## 6. D7 — hierarchical block bootstrap

Минимальная resampling unit — целый repeat/session block. Time samples внутри
периода не ресэмплируются.

Для replicate `m` внутри каждого stratum выбрать `n_g` blocks с возвращением,
при session effect сначала выбрать sessions, затем blocks; вычислить
`ε*_{g,m}`, затем:

\[
M_m^*=\max_{g\in\mathcal G_{\mathrm{valid}}}\widehat{\varepsilon}_{g,m}^*.
\]

Кандидатная percentile upper bound:

\[
\varepsilon_{\mathrm{stat,upper}}=Q_{1-\alpha}(M^*).
\]

`B` и `alpha` пока `PROPOSED / NOT FROZEN`; BCa не применяется без отдельного
решения.

**Block definition:** `ACCEPTED FOR FURTHER CONTRACT REVIEW`.
**Percentile upper bound:** `CANDIDATE`.

## 7. D6 — uncertainty budget и anti-double-counting

Разделять:

```text
epsilon_hat_real   = point estimate
U_measurement      = uncertainty budget
epsilon_real_upper = decision boundary
```

Каждый component обязан иметь:

```text
component_id, value, unit, method, source, correlation_class,
included_in_bootstrap: true | false, combination_rule,
double_count_guard, coverage_scope, owner, status
```

Инвариант:

```text
included_in_bootstrap=true  → не добавлять компонент во внешний budget
included_in_bootstrap=false → нужен явный external combination rule
unknown/missing              → uncertainty_status=INCOMPLETE
```

RSS допускается только после correlation review; correlated/unknown components
складываются консервативно. Coverage uncertainty не превращается в произвольную
численную поправку: неполное coverage даёт `INCOMPLETE`.

## 8. D8 — normalized metric

\[
W_{\mathrm{ref},g}=\int_{I_g}|T_{\mathrm{ref},g}(\theta)|d\theta,
\qquad
E_g=100\%\frac{\widehat{\varepsilon}_g}{W_{\mathrm{ref},g}}.
\]

\[
E_{\mathrm{real}}=\max_g E_g.
\]

Для decision использовать индивидуальный denominator:

\[
E_{\mathrm{real,upper}}=\max_g\left[
100\%\frac{\varepsilon_{g,\mathrm{upper}}}{W_{\mathrm{ref},g}}
\right].
\]

`E_real` — отдельная physical-validation metric. Кандидатный будущий gate:

\[
E_{\mathrm{real,upper}}\le1\%.
\]

Он применяется только при complete coverage, independent reference PASS,
synchronization/signal PASS, holdout PASS и complete uncertainty budget. Это
`PROPOSED FUTURE GATE`, не текущая acceptance rule.

## 9. Decision boundary

\[
\varepsilon_{\mathrm{real,upper}}
=Q_{1-\alpha}(M^*)+U_{\mathrm{outside\ bootstrap}},
\]

где внешний budget содержит только components с
`included_in_bootstrap=false`. Нельзя выбирать `B`, `alpha`, filters или budget
после просмотра результата.

## 10. Invalid/incomplete rules

`PASS` запрещён при missing mandatory stratum, insufficient blocks, unknown
reference independence, trigger/angular mismatch, saturation/dropout/fault/reset,
missing raw/metadata/checksum, post-hoc selection, missing bootstrap flag,
double-count risk или incomplete uncertainty budget.

Вердикты:

```text
PASS_CANDIDATE | PASS_MEASUREMENT | INCOMPLETE | INVALID | ERROR | BLOCKED
```

## 11. Separation from current Phase-4

Даже `E_real,upper≤1%` не отменяет автоматически:

```text
E_trunc + E_corner = 1.327% > 1% → FAIL
```

Physical measurement PASS и proxy FAIL могут сосуществовать до отдельного
решения владельца Phase-4. `ε_W=7.200 µJ` не входит в `ε_real`.

## 12. Evidence-backed D1

Сверка доступных canonical sources:

- `docs/MOTOR_PARAM_BASELINE_20261014.md` задаёт `6 poles / p=3`;
- `src/foc.c:128` содержит `pole_pairs=3`;
- `src/foc.c:136` описывает `enc_phase_accum` как `θe` с wrap-around `2π`;
- `docs/tz/TZ_AS5048A_VF_CONTROL.md:219` задаёт `theta_elec` как `0..2π`,
  а `:440` инициализирует accumulator с нуля.

Поэтому для текущего baseline:

\[
\boxed{\theta_e=p\theta_m\pmod{2\pi}},\qquad p=3,
\]

или:

\[
\boxed{\theta_e=3\theta_m\pmod{360^\circ}}.
\]

Это evidence-backed candidate; источник V0.4 найден и привязан по hash (§18).
Metadata содержит:

```text
v04_source_id      = 9ea121cc04bd64c308a4aea78e6869ffafe5e708:docs/tz/TZ_PHASE4_TORQUE_ROM_CONTRACT_V0_1.md:351-465
v04_source_hash    = sha256:f1a4f1fd8cf9eb0f80ea975dff545a9a59f4591159ec2292e2659b21758f602c
v04_section_hash   = sha256:643c5d740b9c2d6a2e66b584f5cc7518dbe8385aa0c3a8d29957c020bb2ba572
v04_normative_hash = sha256:faa94f7cfa3557fb1a19074ba0e760d913129ae00598ed49d7293216752b2205
pole_pairs_source  = docs/MOTOR_PARAM_BASELINE_20261014.md + src/foc.c:128
phase_accumulator_source = src/foc.c:136 + docs/tz/TZ_AS5048A_VF_CONTROL.md:219
h_coordinate       = h_mech 0.5° (V0.4) -> h_el 1.5° @ p=3
```

## 13. Setup contract: stationarity и session cuts

**Статус:** `PROPOSED PLANNING RULES / NOT FROZEN`.

Для параметра `x` между соседними control periods:

\[
\delta_x=\frac{|x_2-x_1|}{\max(|x_1|,X_{\mathrm{floor}})}.
\]

Candidate limits:

| Metric | Limit |
|---|---:|
| electrical period duration / speed | `≤0.5%` |
| VBUS mean | `≤1.0%` |
| reference torque mean | `≤1.0%` |
| reference torque RMS | `≤2.0%` |
| phase-marker residual | `≤0.5° electrical` |
| temperature drift | `≤1°C/min` |
| missing mandatory samples | `0` |
| saturation/clipping | `0` |
| trigger loss/mismatch | `0` |

Допуск к unit — после двух последовательных passing control periods; после серии
снять post-series control period. Для near-zero channels задать до setup freeze:

```text
speed_abs_floor = <TBD>
torque_abs_floor = <TBD>
vbus_abs_floor = <TBD>
```

Session cut происходит при power cycle/reset/watchdog/fault/break/interlock loss,
изменении firmware/ROM/config/setup/timing/wiring/ground/trigger/sample rate,
повторной calibration или замене probe, изменении operating condition/VBUS/load,
stationarity failure, continuous gap `>30 min` или thermal shift `>2°C` от начала
session. После cut обязательны новый IDs, zero/reference calibration, два новых
control periods, trigger check и причина cut. Эти `30 min` и `2°C` — planning
limits, не frozen параметры.

## 14. Planning uncertainty budget

**Статус:** `PROPOSED PLANNING BUDGET / NOT MEASURED / NOT FROZEN`.

Contribution ceilings ниже выражены как проценты соответствующего `W_ref,g`.

| Component | Ceiling | `included_in_bootstrap` | Источник до freeze | Combination |
|---|---:|---|---|---|
| `U_repeat` | `0.20%` | `true` | dispersion whole repeat blocks | bootstrap |
| `U_noise` | `0.10%` | `true` | validated within-block noise | bootstrap |
| `U_bootstrap` | `Q_(1-alpha)(M*)` | `true` | percentile operation | not additive |
| `U_ref` | `0.15%` | `false` | reference certificate | additive |
| `U_scale` | `0.10%` | `false` | gain/offset/probe calibration | additive |
| `U_sync` | `0.15%` | `false` | trigger/phase study | additive |
| `U_angle` | `0.10%` | `false` | encoder/interpolation | additive |
| `U_filter` | `0.05%` | `false` | predeclared response | additive |
| `U_drift` | `0.15%` | `false` | session/day drift | additive |
| `U_coverage` | gate only | `false` | coverage audit | incomplete=>INCOMPLETE |

Внешняя planning sum:

\[
U_{\mathrm{outside\ bootstrap,plan}}
=0.15+0.10+0.15+0.10+0.05+0.15=0.70\%.
\]

`U_repeat`, `U_noise` и percentile operation не добавляются второй раз к `0.70%`.
До freeze каждый component получает `source_id/hash`, `value` (measured/certified/
TBD), `correlation_class` и explicit `double_count_guard`. Unknown source или
correlation даёт `UNCERTAINTY_INCOMPLETE`.

## 15. R_valid и minimum repeats

Для каждого обязательного stratum candidate minimum:

| Stratum | Valid repeat blocks | Holdout |
|---|---:|---|
| `NOMINAL` | `5` | минимум 1 validation-only |
| `BOUNDARY` | `5` | минимум 1 validation-only |
| `CORNER` | `5` | минимум 1 validation-only |
| `TRANSITION` | `5` | минимум 1 validation-only |
| `HOLDOUT` | `5` | весь stratum validation-only |

Пять blocks должны покрывать минимум две независимые sessions. Менее пяти valid
blocks или недостаточная session diversity дают `INCOMPLETE`/
`SESSION_DIVERSITY_INSUFFICIENT`; invalid blocks не считаются repeats.

\[
R_{\mathrm{valid}}=\{r\in R_{\mathrm{planned}}:\text{all gates PASS}\}.
\]

`R_valid` не включает неполные периоды, post-hoc selected units, units без
independent reference, missing mandatory channel или неизвестный bootstrap flag.

## 16. Open items перед freeze

| Item | Status |
|---|---|
| `Pθ=360° electrical`, `p=3` | evidence-backed candidate; V0.4 source pinned `9ea121c` (§18) |
| `h` coordinate | `h_mech=0.5°`, `h_el=1.5°`; corrected per V0.4 source; declare per window |
| quadrature / interpolation rule | `FREEZE CANDIDATE` (§22.2) |
| reference sample density `N_θ ≥ 2400` | `GAP — блокирует freeze`; развязано в §23.1 |
| reference angle source | `FREEZE CANDIDATE`: `enc_angle`/`theta`; целлоградусный API запрещён (§22.4) |
| phase marker `0°` | proposed; source/alignment record required |
| stationarity limits and absolute floors | правило закрыто (§22.9); значения `TBD` |
| session cuts | planning candidate; not frozen |
| exact `R_valid` physical composition | `coverage ≠ depth`, два слоя (§23.3) — `PENDING OWNER` |
| minimum `5` blocks / `2` sessions | `OPEN — OWNER DECISION`: `n=5` не поддерживает percentile; estimator в §23.2 |
| external budget `0.70%` | planning ceiling; gain-сокращение не зачитывается (§22.5) |
| correlation assumptions | reviewed (§19, §22.5); values still planning |
| `B`, `alpha` | `B=10000`, `alpha=0.05` — `FREEZE CANDIDATE` (§22.7) |
| `E_real,upper≤1%` | proposed future gate |
| acceptance impact | separate future decision |

## 17. Authorization boundary

Этот draft не разрешает measurement execution, HV, запуск двигателя, MCU injections
или Maxwell/FEM; не меняет firmware, ToR, ROM, `ε_W` или acceptance; не превращает
`ε_real=UNKNOWN` в измеренное значение.

**Итоговый статус:** `D1–D8 DRAFT MEASUREMENT CONTRACT — FOR REVIEW ONLY`.

## 18. Результат проверки `v04_source_id/hash`

**Статус:** `RESOLVED — SOURCE PINNED` (проверка 2026-11-26). Блокер снят; D1 при
этом остаётся `PROPOSED / NOT FROZEN`.

Первичная проверка шла по устаревшему клону: локальный `HEAD = 73cc02a`, и в этом
дереве `docs/tz/TZ_PHASE4_TORQUE_ROM_CONTRACT_V0_1.md` ещё не содержал редакции
V0.4 (`grep -c "Редакция V0.4"` = `0`). Коммит `9ea121c` действительно отсутствовал
в локальной object database, но был на remote как `HEAD` и `refs/heads/main`.
После `git fetch origin` (`73cc02a..9ea121c  main -> origin/main`) объект доступен
локально. На момент сверки merge/rebase/checkout не выполнялись, рабочее дерево
оставалось на `73cc02a` (отставание — 96 коммитов). Синхронизация выполнена
2026-11-26 по решению владельца: `git merge --ff-only origin/main`
(`73cc02a..9ea121c`, Fast-forward, 86 файлов), `HEAD = 9ea121c`, tracked-дерево
чисто; untracked остались только локальные артефакты (`BM1109/`,
`Optimus Drive Studio V1.02/`, `docs/shema/` и оба draft-документа).

```text
v04_source_id    = 9ea121cc04bd64c308a4aea78e6869ffafe5e708
                   (Wed Nov 25 22:28:14 2026 +0400,
                    "docs(tz): V0.4 — два разных h* разделены, T_min/h_max
                     привязаны к applicability()")
v04_source_path  = docs/tz/TZ_PHASE4_TORQUE_ROM_CONTRACT_V0_1.md
v04_blob         = 9e02f029a21ec94843049f83f1366b174db4a998  (465 строк, 36853 байт)
v04_source_hash  = f1a4f1fd8cf9eb0f80ea975dff545a9a59f4591159ec2292e2659b21758f602c
v04_section      = строки 351–465 (115 строк, 9773 байта)
v04_section_hash = 643c5d740b9c2d6a2e66b584f5cc7518dbe8385aa0c3a8d29957c020bb2ba572
v04_normative    = строки 431–465 («§14 Финальное решение владельца», 2996 байт)
v04_norm_hash    = faa94f7cfa3557fb1a19074ba0e760d913129ae00598ed49d7293216752b2205
```

Hash считается по сырым байтам блоба, без CRLF-нормализации:
`git cat-file blob 9e02f02… | sha256sum` → `f1a4f1f…` (проверено 2026-11-26).
Практическое уточнение для воспроизведения: в этом клоне `core.autocrlf=true`,
а `.gitattributes` отсутствует, поэтому checkout даёт `w/crlf` при `i/lf`
(`git ls-files --eol`). Файл на диске — 37318 байт с 465 `CR`, и
`sha256sum <file>` даёт `9b0922c8…`, то есть **не** равен pinned. Нормализация
`CRLF → LF` возвращает ровно 36853 байта и `f1a4f1f…`. Поэтому hash всегда
берётся из объекта git (или после нормализации), а расхождение байтов рабочей
копии — артефакт checkout, не изменение источника. Норма `*.md → LF` (AGENTS.md)
в индексе соблюдена (`i/lf`); настройку `core.autocrlf` в рамках сверки не меняли.

Конвенция section-hash: диапазон строк хешируется **вместе с завершающим `\n`
последней строки** (как `git show <commit>:<path> | sed -n 'A,Bp'`), поэтому байт
на 1 больше, чем при склейке строк через `join`. Для контроля: без завершающего
`\n` те же диапазоны дают `c4b52168…` (351–465) и `ff85e4f8…` (431–465).
Full-file hash от конвенции не зависит.

Уточнения, следующие из найденного исходника:

1. Отдельного файла «V0.4» не существует: V0.4 — раздел внутри файла с именем
   `..._V0_1.md`. Поэтому `v04_source_id` содержит commit + путь + диапазон строк;
   одного commit hash недостаточно (в файле лежат и V0.3, и V0.4).
2. В файле два заголовка V0.4: строка 351 «Редакция V0.4 (проект на согласование
   владельца…)» и строка 431 «Редакция V0.4 — УТВЕРЖДЕНА владельцем…». Нормативен
   §14 (строки 431–465); заголовок 351 сохраняет слово «проект». Дефект оформления
   исходника: `OPEN`, на hash не влияет, но при цитировании нужно указывать диапазон.
3. Commit message содержит «(НЕ запушено)», тогда как remote `refs/heads/main` равен
   `9ea121c`. Привязка делается к hash, а не к тексту сообщения; расхождение записано.
4. `1.327% FAIL` подтверждён исходником (§14 п.3): `E_trunc = 0.411%`,
   `E_corner = 0.917%`, `E_total = 1.309%` — контрольная метрика, не основание PASS;
   `eps_W = 7.200e-6 Дж`, `T_min(0.5°) = 140.013 мН·м`, `h_max(C1) = 0.780871°`.

**Координата `h` — новый дефект, найденный при сверке.** V0.4 объявляет
`TORQUE_H_MECH_DEG = 0.5°`, то есть механические градусы. В `tools/bm1109_doe1.py`
электрический сдвиг считается как `p * TORQUE_H_MECH_DEG` (строки 89–90, 410),
в summary пишутся оба поля `h_mech_deg` / `h_e_deg` (1265–1266). Следовательно при
`p = 3`:

\[
h_{el} = p\,h_{mech} = 1.5^\circ\ \text{electrical}.
\]

Прежнее утверждение draft «`h=0.5°` относится к той же [электрической] координате»
исходником **не подтверждается** и снято: `0.5°` — механические, электрический шаг
`1.5°`. Расхождение было в сторону более мелкой сетки в `p` раз; на нём не строилось
ни одного расчёта или вывода, но строки §0/§2 исправлены.
Статус: `CORRECTED — h_mech / h_el разделены`.

**Проверка координатной семантики в расчётном пути (2026-11-26).** Отдельно
проверено, что ни один ранее посчитанный результат не трактовал `0.5°` как
электрический шаг (проверка чтением формул, прогон не запускался):

- `tools/bm1109_doe1.py:90` — `TORQUE_H_MECH_DEG = 0.5` (механические);
  `:410` — состояния `theta_e ± p·TORQUE_H_MECH_DEG`, то есть электрический
  сдвиг; `:950, :1100` — производная делится на
  `h_mech_rad = radians(TORQUE_H_MECH_DEG)`, что корректно: `Δθ_el = p·h_mech`,
  а момент есть `∂W'/∂θ_mech`;
- `:1265–1266` — в summary публикуются оба поля, `h_mech_deg` и
  `h_e_deg = p·h_mech_deg`;
- `tools/bm1109_torque_metrics.py:84–99` — `h_max_c1()` возвращает механические
  градусы (docstring: `0.780871°` при `n=6, p=3, tol=1%`);
  `:102–116` — `T_min` делит на `radians(h_m_deg)`; `:182–209` —
  `h_m = radians(h_m_deg)`, `h_e = h_m_deg·P`, `E_corner = eps_W/h_m/T_peak`;
- `tests/test_bm1109_torque_metrics.py:48` — `h_max_deg ≈ 0.780871` (механические).

Вывод: `E_trunc`, `E_corner`, `ε_W`, `T_min`, `h_max` используют механический
`h`; пересчёт не требуется, `1.327% FAIL` остаётся в силе.

`v04_source_id/hash` больше не `TBD` и не `BLOCKED`. Привязка источника не делает D1
`FROZEN`: незакрытыми остаются setup validation, absolute floors и correlation review.
`OPEN` по рабочему дереву снят: `git merge --ff-only origin/main` выполнен
2026-11-26, `HEAD = 9ea121c`, текст V0.4 присутствует (`grep -n "Редакция V0.4"`
→ строки 351 и 431). Правки `9ea121c` не откатывались, `make`/стенд не запускались.

## 19. Correlation assumptions и anti-double-counting matrix

**Статус:** `PROPOSED FOR SETUP REVIEW / NOT FROZEN`.

Единицей correlation review является физический источник вариации, а не имя
budget component. Для каждого источника фиксируются `source_id`, affected
channels, block/session scope, `included_in_bootstrap` и rule включения.

| Source of variation | Components affected | Bootstrap inclusion | Correlation assumption | Combination rule |
|---|---|---|---|---|
| repeat-to-repeat operating variation | `U_repeat` | `true` | correlated within block/session, resampled by whole block | percentile bootstrap |
| within-period reference noise | `U_noise` | `true` | correlated over adjacent samples; independent blocks only approximately | included in block distribution |
| reference calibration bias | `U_ref` | `false` | common-mode across all units in a calibration state | additive |
| gain/offset/probe scale | `U_scale` | `false` | common-mode; correlated with reference amplitude, not independent of `U_ref` by default | additive joint bound |
| trigger/phase delay | `U_sync` | `false` | common-mode within setup/session; correlated with angular error | additive with `U_angle` unless separated by calibration |
| angle encoder/interpolation | `U_angle` | `false` | correlated across periods in setup; may be coupled to `U_sync` | additive joint bound |
| declared digital/analog filter response | `U_filter` | `false` | deterministic bias common to all units using filter revision | additive |
| session/day thermal drift | `U_drift` | `false` | correlated within session; session-level variation not assumed independent of operating drift | additive unless session effect is explicitly bootstrapped |
| missing ROM/coverage class | `U_coverage` | `false` | not an error random variable | gate: `INCOMPLETE`, no numeric patch |

The following invariants are mandatory:

1. `U_repeat` and `U_noise` are not added to the external `0.70%` sum when they
   are represented by the block bootstrap.
2. `U_ref` and `U_scale` are not treated as independent by name alone; until a
   calibration decomposition proves otherwise, their upper contributions are
   combined additively.
3. `U_sync` and `U_angle` are one joint timing/angle risk unless an independent
   calibration separates them; the planning table therefore uses additive
   combination, not RSS.
4. `U_drift` is external only when session drift is not included in the chosen
   hierarchical bootstrap. If sessions are resampled as the primary block,
   `U_drift.included_in_bootstrap` must be changed to `true` and removed from the
   external sum.
5. Every component must have exactly one owner of the variation source:
   bootstrap or external budget, never both.

The resulting planning budget remains:

```text
external = U_ref + U_scale + U_sync + U_angle + U_filter + U_drift = 0.70%
bootstrap = U_repeat + U_noise + percentile(M*)
coverage  = gate, not a numerical uncertainty term
```

## 20. Setup-validation specification for stationarity thresholds

**Статус:** `PLANNING SPECIFICATION / NOT FROZEN`.

For adjacent full control periods `a` and `b`, define:

\[
\delta(x)=\frac{|x_b-x_a|}{\max(|x_a|,X_{floor})}.
\]

The setup record must contain the absolute floors before acquisition. Candidate
relative thresholds are:

| Quantity | Relative threshold | Absolute floor |
|---|---:|---:|
| electrical period / speed | `0.5%` | `speed_abs_floor = TBD` |
| VBUS mean | `1.0%` | `vbus_abs_floor = TBD` |
| reference torque mean | `1.0%` | `torque_abs_floor = TBD` |
| reference torque RMS | `2.0%` | `torque_rms_abs_floor = TBD` |
| phase marker residual | `0.5° electrical` | direct angular limit |
| temperature drift | `1 °C/min` | direct rate limit |
| mandatory missing samples | `0` | exact count |
| clipping/saturation | `0` | exact count |
| trigger mismatch/loss | `0` | exact count |

For a near-zero quantity, PASS requires the absolute difference to be no greater
than its predeclared floor; the relative test is not used with a near-zero
denominator. Floors must come from instrument resolution/noise and be recorded
with source and hash. They may not be tuned using `ε_r`.

Admission rule:

1. acquire two consecutive full control periods;
2. require all stationarity and integrity limits to PASS;
3. acquire the next full period as the measurement unit;
4. acquire one post-series control period;
5. on any failure, close the session, preserve all raw data, and classify the
   affected unit/series as `INVALID` or `INCOMPLETE`.

A session cut is mandatory after reset, watchdog, fault/break, interlock loss,
calibration or probe change, firmware/config/setup/timing/wiring/ground/trigger
change, operating-point change, a stationarity failure, a continuous gap over
`30 min`, or a thermal shift over `2 °C` from session start. The `30 min` and
`2 °C` values are planning limits, not measured system properties.

After a session cut, require a new session/setup ID, fresh zero/reference
calibration, two new control periods, a trigger/alignment check, and a recorded
cut reason. No unit from a cut session is silently promoted to a repeat of the
next session.

## 21. Updated freeze status

| Item | Status |
|---|---|
| `v04_source_id/hash` | `RESOLVED — pinned: 9ea121c / blob 9e02f02 / sha256 f1a4f1f…` |
| `h` coordinate (mech vs el) | `CORRECTED — 0.5° mech = 1.5° el @ p=3` |
| V0.4 text in working tree | `YES — ff-only 73cc02a..9ea121c, HEAD = 9ea121c` |
| `p=3`, `Pθ=360° electrical` | evidence-backed candidate |
| `θ0=0° electrical` | proposed candidate; alignment source required |
| correlation matrix | reviewed (§19, §22.5); values still planning |
| quadrature / angle-source rules | `FREEZE CANDIDATE` (§22.2, §22.4) |
| reference sample density | `GAP`; требование `N ≥ 74·m_max`, кандидат `2400` (§23.1) |
| external planning budget | `0.70%`, not measured, conservative (§22.5) |
| absolute stationarity floors | rule closed (§22.9); values `TBD` |
| minimum blocks per stratum | estimator — §23.2; числовые `n`, `α_s`, `G_gate` — по feasibility gate |
| coverage depth `396×5` | снят архитектурой `coverage ≠ depth` (§23.3), `PENDING OWNER` |
| session-cut rules | planning, not frozen |
| `ε_real` | `UNKNOWN` |
| current Phase-4 | `1.327% FAIL` |
| measurement/HV | `NOT AUTHORIZED` |
| Maxwell/FEM | `BLOCKED` |

Привязка источника по hash не превращает D1–D8 в `FROZEN` и не меняет
`ε_real = UNKNOWN`: незакрытыми остаются setup validation, absolute floors и
correlation review. Draft остаётся `FOR REVIEW ONLY`. Полный результат
методологического review D1–D8 — §22. Согласованный итоговый статус §22–§23 и
запрет численного закрытия — §24.

## 22. Методологический review D1–D8 (2026-11-26)

**Статус:** `REVIEW COMPLETE / PARTIAL FREEZE CANDIDATE / NOTHING PROMOTED TO FROZEN`.
Раздел не меняет ни одного числа Phase-4 (`1.327% FAIL`, `ε_W`, `T_min`, `h_max`)
и ничего не разрешает: измерение, HV, стенд, Maxwell/FEM остаются `BLOCKED`.

### 22.1 Что закрылось, что осталось

| Item | Результат review | Статус после review |
|---|---|---|
| D1 окно `Pθ=360° el` | подтверждено, зависит от `p=3` (evidence-backed) | `FREEZE CANDIDATE` |
| D1 квадратура/интерполяция | правила не было; сформулировано (§22.2) | `FREEZE CANDIDATE` |
| D1 плотность отсчётов reference | требования не было; **новый GAP** (§22.3) | `GAP — блокирует freeze` |
| D5 источник угла в логе | `enc_angle` 14-bit + `theta` u32; целлоградусный API запрещён (§22.4) | `FREEZE CANDIDATE` |
| D2 coverage (все `396` cells) | конфликт с D15: `396×5 = 1980` blocks (§22.8) | `OPEN — OWNER DECISION` |
| D3/D7 минимум `5` blocks | `5` блоков не поддерживают percentile-95 (§22.7) | `OPEN — OWNER DECISION` |
| D7 `B`, `alpha` | `B=10000`, `alpha=0.05` + MC-контроль (§22.7) | `FREEZE CANDIDATE` |
| D6 budget `0.70%` | остаётся консервативным; gain-компоненты в `E` сокращаются (§22.5) | `FREEZE CANDIDATE (as ceiling)` |
| D7 assumption-компоненты | переводятся в измеряемые sensitivity (§22.6) | `FREEZE CANDIDATE` |
| absolute floors | правило закрыто, значения — нет (§22.9) | значения `TBD / BLOCKED` |
| `R_valid` | определение закрыто как множество (§22.8) | `FREEZE CANDIDATE` |
| `E_real,upper ≤ 1%` | остаётся `PROPOSED FUTURE GATE` | без изменений |

### 22.2 Сетка, квадратура, интерполяция

Электрический период при `p=3` — `120°` механических; при `h_mech = 0.5°`
(`tools/bm1109_doe1.py:90`) это ровно `240` интервалов и `240` различных узлов
(замыкающая точка не дублируется):

```text
240 = 360° / 3 / 0.5°
```

Правила, предлагаемые к freeze:

1. оба ряда (`T_ROM`, `T_ref`) приводятся к одному набору из этих `240` узлов;
2. интеграл — трапеции по замкнутому периоду, `N` интервалов, не `N+1`;
3. `T_ROM` не интерполируется: значения берутся в узлах V0.4, иначе метрика
   измеряет интерполятор ROM, а не ROM;
4. `T_ref` ресемплится к узлам объявленным до анализа методом (по умолчанию
   линейно), метод пишется в metadata unit'а;
5. никакой необъявленной фильтрации при ресемплинге; всё объявленное идёт в
   `U_filter`.

Квадратура даёт **систематическое смещение, пропорциональное `ε`**, а не
аддитивное: для гармоники электрического порядка `m` при `N` отсчётах на
период относительная ошибка трапеций ≈ `(2πm/N)²/12`. При `N = 240`:

| `m` | `(2πm/240)²/12` |
|---:|---:|
| 6 | `0.21%` |
| 12 | `0.82%` |
| 24 | `3.3%` |

Это относительно `ε`, поэтому при `E ≈ 1%` худший случай `3.3%` даёт `0.033%`
по `W_ref`. Порядок меньше `0.70%`, но для порога `1%` смещение `3%` от `ε`
нельзя оставлять без оценки: оно либо измеряется refinement-тестом (§22.6),
либо объявляется систематическим с верхней границей. `m_max` не фиксирован —
он зависит от зубцовой гармоники, а `N_s` в контракте отсутствует (`T_cog`
блокирован); поэтому `3.3%` — верхняя оценка при `m_max = 24`, не измерение.

Линейная интерполяция `T_ref` к узлам даёт ошибку ≈ `(1/8)(2πm/N_ref)²`:

| `N_ref` (отсчётов на эл. период) | `m=6` | `m=24` |
|---:|---:|---:|
| `240` (плотность узлов) | `0.31%` | `4.9%` |
| `2400` | `0.003%` | `0.05%` |
| `5461` (нативная 14-bit шкала) | `0.0006%` | `0.0095%` |

Это свойство **определения** метрики, а не дополнительная uncertainty: `T_ROM` и
reference считаются на одной сетке, а дискретизация ROM уже входит в `E_trunc`
(§23.1). Отсюда требование §22.3 — не «чем чаще, тем лучше», а конкретный порог.

### 22.3 Новый GAP: плотность отсчётов reference

В D1–D8 нет требования к числу отсчётов reference на электрический период.
Без него `∫|ΔT|dθ` может измерять частоту логирования, а не ошибку ROM.
Кандидат требования (уточняется в §23.1):

```text
N_theta,ref >= 10 * 240 = 2400 отсчётов на электрический период
f_s,ref     >= 2400 * f_e          (f_e — электрическая частота, Гц)
f_3dB,ref   >= 3 * m_max * f_e     (антиалиас до дискретизации)
f_s,ref     >= 5 * f_3dB,ref
```

Следствия, которые нужно принять явно:

- при `f_s = 1 кГц` это `f_e ≤ 0.417 Гц`, то есть очень медленное вращение;
- при `f_e = 10 Гц` нужно `f_s ≥ 24 кГц` на канале момента;
- если условие не выполнено, unit не `INVALID`, а метрика `NOT_EVALUABLE`:
  `ε_r` в этом случае характеризует канал, а не ROM.

Пока требование не выполнено, `E_real,upper` **не определено**, а не «примерно
известно». Это единственный новый блокер freeze, признанный review; Phase-4 от
него не зависит.

### 22.4 Источник угла

Проверено по коду: в лог попадают два разных представления угла, и они не
равноценны:

- `docs/tz/TZ_VF_DATA_LOGGING.md:61,63,131` — поля `theta=<u32>`
  (электрический phase accumulator) и `enc_angle=<0..16383>` (14-bit,
  механический);
- `src/foc.c:136` — `enc_phase_accum` как `θe` с wrap-around `2π`;
- `src/encoder.c:19` — `ENC_COUNTS_PER_REV 16384`.

Квант: `360°/16384 = 0.0219727°` мех = `0.0659180°` эл при `p=3`; на один узел
V0.4 (`0.5°` мех) приходится `22.7555` кванта. То есть узел сетки **не лежит**
на кванте энкодера, максимальное рассогласование — `0.5` кванта =
`0.010986°` мех = `0.03296°` эл = `0.0092%` периода.

Отдельно: `src/encoder.c:257` (`ENC_GetAngle_deg`) возвращает **целые градусы**
(`enc_angle14 * 360 / 16384`). Использование этого API или лейбла
`enc_angle_label` как источника угла даёт квант `1°` мех — **вдвое грубее**
шага V0.4, что несовместимо с `ε_r`. Требование к freeze:

```text
reference_angle_source = enc_angle (0..16383) | theta (u32)
forbidden              = ENC_GetAngle_deg(), *_label, любое округление до градуса
```

### 22.5 Нормализация `E` и судьба gain-компонентов

`E_g = 100%·ε_g/W_ref,g` — отношение, поэтому общий масштаб reference
сокращается в первом порядке: если `T_ref → (1+δ)·T_ref`, то `ε` и `W_ref`
умножаются на `(1+δ)` и `E` не меняется. Следствия:

- `U_ref` (gain/offset сертификата) и `U_scale` (`0.15% + 0.10%`) в `E`
  **не аддитивны в полном объёме**: аддитивной остаётся форма/полоса
  reference, а не масштаб;
- `U_sync`, `U_angle`, `U_filter` и новый `U_rate` (§22.3) не сокращаются;
- `0.70%` остаётся **консервативным planning ceiling**: сокращение не
  зачитывается вниз, пока нет сертификата reference и scale-теста.

Сокращение — только первого порядка. Оно ломается при нелинейности тракта
(насыщение, нелинейная постоянная момента, клиппирование), поэтому к freeze
добавляется дешёвая проверка на уже собранных данных:

```text
scale perturbation: T_ref * (1 ± 0.1%)  ->  |ΔE| <= 0.01% (abs)
если не выполнено -> сокращение не зачитывается, U_ref/U_scale аддитивны
```

Тем самым `E_real` объявляется **масштабно-инвариантной и формо-чувствительной**
метрикой — это свойство определения D8, а не побочный эффект.

### 22.6 Assumption → measured sensitivity

Сейчас `U_sync`, `U_angle`, `U_filter` и квадратура — предположения. Их можно
превратить в измеряемые sensitivity **без дополнительного оборудования**,
предобъявив анализ до просмотра `ε`:

| Component | Операция на тех же данных | Что получаем |
|---|---|---|
| `U_sync` + `U_angle` | сдвиг reference на `±0.5°` эл (`±1/3` узла), пересчёт `ε_r` | эмпирический `Δε/ε` |
| квадратура / `U_rate` | ресемплинг reference к `h/2` (120 узлов) и обратно | `|Δε|/ε` — прямая оценка bias |
| `U_filter` | пересчёт с объявленным и отключённым фильтром (если raw сохранён) | `|Δε|/ε` |
| `U_ref`/`U_scale` | scale-perturbation §22.5 | сокращение подтверждено/нет |

Правило: эти числа вычисляются **до** вердикта и записываются в budget как
`measured`, заменяя ceilings. Если операция невозможна (raw не сохранён),
компонент остаётся `NOT_VERIFIED`, а budget — `INCOMPLETE`. Это снимает
претензию «`U_sync = 0.15%` взято из воздуха»: значение становится следствием
данных, а не допущением.

### 22.7 `B`, `alpha` и почему `5` блоков мало

- `B`: для percentile `1-alpha` нужно достаточно точек в хвосте. При
  `alpha = 0.05`, `B = 10000` в хвосте ~`500` реализаций; при `B = 1000` —
  ~`50`, чего для `max` по сотням страт мало. Предлагается `B = 10000`,
  `alpha = 0.05` и обязательный отчёт MC-ошибки квантиля (jackknife по
  репликам) с требованием `≤ 0.02%` в единицах `E`.
- `alpha` фиксируется **до** анализа; `0.90` и `0.99` считаются только для
  информации и не могут выбирать вердикт.
- Минимум `5` valid blocks (D15) **не поддерживает** percentile-95: при `n=5`
  эмпирический 95-й процентиль практически равен максимуму из пяти значений,
  то есть upper bound вырождается в «худший блок + шум». Для percentile нужно
  `n ≥ 10` блоков на страту либо другой estimator (`max + k·s` с объявленным
  распределением). `5` можно оставить как порог допустимости (`INCOMPLETE`
  ниже пяти), но не как основание percentile.

**Смещение выбора.** `ε̂_real = max_g ε̂_g` по ~`400` стратам — это максимум по
`400` шумным оценкам, систематически выше истинного максимума средних
(winner's curse). Для одностороннего gate это консервативно (завышает `E`),
поэтому численная поправка не вводится, но формулировка обязательна:
величина есть «максимум по плановым стратам оценок средних», а не «типичная
ошибка ROM» и не «истинный worst case по моменту».

### 22.8 Конфликт coverage и глубины

D2 требует nominal coverage по всем `33×12 = 396` cells как mandatory, а D15
требует `≥5` valid blocks на каждый mandatory stratum, причём stratum key
включает `(cell_i, cell_j)`. Вместе это `396 × 5 = 1980` блоков только по
nominal, плюс auxiliary strata, плюс `≥2` sessions. Это арифметическое
следствие двух принятых пунктов, а не оценка.

Варианты (решение владельца, не review):

```text
(a) coverage complete: >=5 blocks на subset ROBUST (corners + boundary +
    holdout), 1 block на остальные cells, отдельный флаг глубины;
(b) nominal coverage сокращается до объявленного подмножества cells,
    остальные cells вне claim;
(c) 1980+ blocks принимаются как реальный объём кампании.
```

До решения `COVERAGE_COMPLETE` не выставляется, `E_real` определён только на
покрытом подмножестве. Молчаливое ослабление D2 или D15 запрещено.

### 22.9 Absolute floors: правило закрыто, значения нет

Правило к freeze:

```text
floor_x = max(k_sigma * sigma_x,noise , n_lsb * LSB_x)   , k_sigma = 3, n_lsb = 10
relative test only if |x| >= 20 * floor_x
floor_x declared with source_id + hash before acquisition
```

Гистерезис `20×` убирает разрыв между абсолютным и относительным тестом на
границе near-zero. Значения `speed_abs_floor`, `torque_abs_floor`,
`vbus_abs_floor`, `torque_rms_abs_floor` остаются `TBD`: в репозитории нет
сертифицированных данных о разрешении и шуме канала момента и напряжения
(для угла разрешение есть: `16384` кванта, `src/encoder.c:19`). Это `BLOCKED`
на приборных данных, а не на методологии.

### 22.10 Что осталось до `FROZEN`

```text
1. GAP §22.3 (N_theta,ref >= 2400/период) — закрыть требованием и проверкой
2. OPEN §22.7 (n_blocks >= 10 либо смена estimator)
3. OPEN §22.8 (coverage depth 396x5)
4. TBD §22.9 (absolute floors -> приборные данные + hash)
5. reference certificate (U_ref shape) и calibration decomposition (U_scale)
6. подтверждение сокращения масштаба scale-тестом §22.5
```

Всё остальное в D1–D8 после этого review — `FREEZE CANDIDATE`. До закрытия
пунктов 1–6 документ остаётся `DRAFT / FOR REVIEW ONLY`; `ε_real = UNKNOWN`;
`1.327% FAIL` не меняется; measurement/HV/FEM не разрешены.

## 23. Развязка трёх design decisions (2026-11-26)

**Статус:** `DESIGN DECISIONS PROPOSED / NOT FROZEN / NOTHING MEASURED`.
Три решения развязаны: каждое фиксируется отдельно, и ни одно из них не требует
стенда, HV или FEM. Ни один пункт не поднимается до `FROZEN`.

### 23.1 D-A: плотность reference и антиалиасинг

Сначала уточнение определения, снимающее лишний член: метрика определена **на**
240-узловой сетке с трапециями (§22.2); `T_ROM` в узлах точен по определению;
значит единственное ресемплирование — reference. Квадратура — не «ошибка
измерения», а часть определения; дискретизация ROM уже сидит в `E_trunc = 0.411%`
(V0.4). Поэтому требование к плотности относится только к reference.

Ошибка линейной интерполяции гармоники порядка `m` при `N` отсчётах на период —
`(1/8)(2πm/N)²` от амплитуды. Перенос в метрику (Коши–Шварц, синусоидальный
момент `W_ref ≈ 0.6366·A·P`) — **аналитическая оценка, candidate**:

```text
δε/ε <= 13.9 · (2πm/N)² / (E/1%)        E — в процентах
```

| Допуск на interpolation bias | Требование | при `m_max = 24` |
|---|---|---:|
| `δε ≤ 10%·ε` | `N ≥ 74·m_max` | `≥ 1776` |
| `δε ≤ 2%·ε` | `N ≥ 165·m_max` | `≥ 3960` |

Эта оценка — **аналитический candidate, не frozen acceptance equation**: она
предполагает одну доминирующую гармонику амплитуды `A` и `W_ref ≈ 0.6366·A·P`,
а отношение амплитуды гармоники к полному `ε` для реального reference не
проверено. Численные проценты bias — порядок величины, а не свойство системы.

Корректная формулировка:

```text
N_theta,ref >= 2400   — planning candidate при выбранном m_max
окончательный минимум — после фиксации m_max и sensitivity criterion
```

Утверждение «`2400` гарантирует `10%`» **не поддерживается и снято**: `2400`
покрывает `74·m_max` только при `m_max ≤ 32` и только внутри предпосылок оценки;
`2%`-допуск потребовал бы почти вдвое больше.

**Антиалиасинг здесь не связывает.** При `N = 2400` частота дискретизации в 50 раз
выше Найквиста для `m_max = 24`; ограничение — точность интерполяции, а не
алиасинг. Фильтру достаточно `f_3dB ≥ 2.5·m_max·f_e`; требование `f_s ≥ 5·f_3dB`
из §22.3 этим поглощается и отдельным числом не является.

Связка с кампанией (`f_s = N·f_e`, `rpm = 60·f_e/p`):

| `f_s,ref` | `f_e,max` | `rpm` при `p=3` |
|---:|---:|---:|
| `1 кГц` | `0.42 Гц` | `8.3` |
| `5 кГц` | `2.1 Гц` | `42` |
| `10 кГц` | `4.2 Гц` | `83` |
| `20 кГц` | `8.3 Гц` | `167` |
| `50 кГц` | `20.8 Гц` | `417` |

Следствие: медленное вращение — не побочный эффект, а условие измеримости;
при типичном телеметрическом темпе `1 кГц` кампания возможна только на
`f_e ≤ 0.42 Гц`. Это проектное ограничение кампании, а не свойство ROM.

Два условия к freeze:

```text
N_theta,ref >= 74 * m_max  (кандидат 2400 при m_max <= 32)
f_s,ref     = N_theta,ref * f_e ; f_3dB,ref >= 2.5 * m_max * f_e
angle and torque на одном клоке (или theta с частотой >= f_s,ref)
```

`m_max` — `TBD`: он зависит от зубцовой гармоники, а `N_s` в контракте нет
(`T_cog` блокирован). Поэтому численное `2400` остаётся **кандидатом** до
объявления `m_max`; нарушение условия даёт `NOT_EVALUABLE`, не «примерно принято».

### 23.2 D-B: estimator при конечном `n`

Факт: при `n = 5` эмпирический `Q_0.95` практически совпадает с максимумом из
пяти значений, дискретен и не поддерживает порог `1%`. Значит `n = 5` и
`percentile-95` нельзя совмещать.

Решение: **решающая статистика — односторонняя верхняя доверительная граница
среднего по блокам**, а percentile bootstrap понижается до диагностики:

```text
ε_g,upper = mean_g + t(1-alpha_s, n_g-1) * s_g / sqrt(n_g)
percentile bootstrap: только diagnostic, допустим при n_g >= 10
alpha_s = alpha_gate / G_gate        (gate = «все gate-страты проходят»)
```

Множитель `t/√n` при `s` = межблочное СКО страты:

| `n` | `G=1`, `α=0.05` | `G=12`, `α_s=0.00417` | `G=90`, `α_s=0.00056` |
|---:|---:|---:|---:|
| 5 | `0.95` | `2.30` | `3.85` |
| 10 | `0.58` | `1.13` | `1.80` |
| 15 | `0.45` | `0.83` | `1.26` |
| 20 | `0.39` | `0.69` | `1.03` |

Читается так: при `G_gate = 90` и `n = 10` граница уходит на `1.8·s`, то есть при
`0.70%` внешнего ceiling и пороге `1%` (запас `0.30%`) достижимо только `s ≲ 0.17%`.
При `G_gate = 12`, `n = 10` — `s ≲ 0.27%`. Вывод: **feasibility решает `s`, а не `n`
само по себе**, и именно поэтому `n`, `α_s` и `G_gate` нельзя назначать без
измеренного `s`.

Отсюда разделение, ради которого делается развязка:

- **сейчас** фиксируется *форма* estimator'а (UCL по межблочному разбросу, `α_s =
  α_gate/G_gate`, `max` по стратам, percentile только как диагностика) — для этого
  не нужен ни стенд, ни `ε_real`;
- **позже** фиксируются числа `n_gate`, `α_s`, `G_gate` — через feasibility gate:
  измерить `s` на одной представительной страте 10 блоками (только setup, без HV;
  сейчас `NOT AUTHORIZED`) и проверить `E_point + (t/√n)·s + 0.70% ≤ 1%`;
- если не проходит, владелец ослабляет **явно** один из четырёх параметров
  (`n`, `G_gate`, `α`, порог/бюджет), а не молча меняет `n`.

Множественность учитывается явно: `max` по `G_gate` односторонним UCL даёт
вероятность ложного `PASS` не более `G_gate·α_s`, поэтому `α_s = α_gate/G_gate`
обязателен, а большое `G_gate` (>~20) делает gate статистически недостижимым
и должен уменьшаться через §23.3, а не через ослабление `α`.

Условие применимости: `α_s = α_gate/G_gate` корректен только если эти `G_gate`
страт **объявлены заранее** и являются единицами множественного решения.
Поэтому `G_gate = 16` — проектный параметр, объявляемый до данных; после setup
измерения допустима только проверка feasibility, но не перевыбор `G_gate` по
результатам. Набор, выбранный после просмотра `ε`, делает контроль FWER
недействительным.

### 23.3 D-C: модель покрытия — `coverage ≠ depth`

**Статус:** `PROPOSED ARCHITECTURE / NOT FREEZE CANDIDATE` — до формализации
deterministic escalation rule и обоснования репрезентативности gate-набора.

Конфликт `396 × 5 = 1980` (D2 × D15) снимается не ослаблением D2 или D15, а
разделением двух разных свойств: **покрытие** и **глубина**.

```text
coverage layer: все 396 cells, >= 1 valid block -> COVERAGE_COMPLETE
gate layer:     G_gate объявленных страт, n_g >= n_gate (>=10) -> E_real
escalation:     если point ε покрывающей cell превышает текущий gate-max,
                cell повышается в gate layer (правило объявляется заранее,
                только добавляет данные, никогда не убирает)
```

Арифметика:

| Модель | Блоков (nominal) | Покрытие | Gate-страт |
|---|---:|---|---:|
| full depth `396×5` | `1980` (+holdout) | complete | `396` |
| два слоя, `n_gate = 10`, `G = 16` | `396 + 16·9 = 540` | complete | `16` |
| два слоя, `n_gate = 5`, `G = 16` | `396 + 16·4 = 460` | complete | `16` |

`540` против `1980` — в `3.7` раза меньше при **полном** покрытии и без
ослабления gate-набора: одиночные cells никогда не входят в `max`, они дают
покрытие и право на escalation. Это не «5 блоков на всю кампанию»: внутри
gate-страты остаётся `n_g >= 10`, никакого пулинга между cells нет.

Состав `540` надо читать буквально: `396` — это **по одному valid block на
каждую nominal cell**, а `16·9 = 144` — **новые** блоки только для уже
выбранных gate-страт (их coverage-блок входит в `n_g` как один из десяти).
Получить `540` перераспределением тех же `396` измерений нельзя.

Требования к gate layer (кандидат, не frozen):

```text
4 corner cells + 8 boundary/transition cells + 2 holdout + 2 interior
G_gate = 16   (совместимо с α_s = 0.05/16 = 0.0031)
>= 2 независимые sessions на gate-страту; coverage cells могут быть 1 session,
но помечаются SESSION_SINGLE
```

`G_gate = 16` выбран именно из §23.2: при `n = 10` и `s = 0.2%` граница даёт
`~0.23%`, что оставляет запас при `0.70%` и `E ≈ 0.1–0.3%`. При `G_gate = 90` тот же расчёт даёт `~0.36%` и упирается в порог.

**Escalation rule: форма фиксируется до данных, значения — `UNSET`.**

```text
Триггер   : сравнение с порогом, объявленным до просмотра ε
Величина  : point ε̂_cell ЛИБО его upper bound — выбирается одна, не обе
Запас     : абсолютный (п.п. E) ЛИБО относительный (доля от gate-max) — одна
Порог     : THRESH, объявляется до данных (кандидат 0.8·gate-max, NOT FROZEN)
Тай-брейк : все cells с превышением повышаются; выбор «лучших» запрещён
Эффект    : повышение только добавляет блоки, ни одна cell не удаляется
```

Пока эти пять полей не объявлены, escalation — не правило, а post-hoc selection,
и `E_real` на gate-наборе не определён.

**Репрезентативность `G_gate = 16` для `396` cells** утверждением не доказывается.
Структурная часть аргумента готова: gate-страты берутся там, где ROM меняет
entry/формулу (corner, boundary, transition — §4/D2). Статистическая часть
существует только через escalation, который переносит покрывающее свидетельство
в gate. Поэтому репрезентативность — **эмпирическое утверждение**, и без
(i) заранее объявленного правила отбора gate-страт и (ii) escalation rule она
остаётся `UNSET`, а `E_real` определён только на объявленном подмножестве.

### 23.4 Что это меняет в статусах

| Решение | Статус | Что осталось |
|---|---|---|
| D-A плотность/антиалиасинг | `FREEZE CANDIDATE` только для структуры; формула — `analytical candidate` | численное `N` после объявления `m_max` (`N_s`) |
| D-B форма estimator'а | `FREEZE CANDIDATE` | `n`, `α_s`, `G_gate` — после setup-измерения `s` |
| D-C два слоя покрытия | `PROPOSED ARCHITECTURE / NOT FREEZE CANDIDATE` | escalation rule, репрезентативность `G_gate`, согласие владельца |

Уточнение к §22.5, которое надо держать в силе: масштабная инвариантность `E`
касается только **общего линейного** gain. Форма, фазовый сдвиг, полоса,
rate и нелинейности остаются, поэтому `0.70%` остаётся planning ceiling и не
пересчитывается вниз ни при каком сокращении gain-членов.

### 23.5 Порядок закрытия

```text
D-A  N_theta,ref / m_max
       |
D-B  форма estimator'а
       |
D-C  заранее объявленный gate coverage (escalation + отбор страт)
       |
setup measurement (s_g, floors, signal/rate limits)
       |
выбор n и G_gate
       |
freeze measurement contract
```

Жёсткое условие: setup measurement **не может** менять правило estimator/coverage
задним числом. Он отвечает только на вопрос «выполним ли заранее выбранный
контракт»; если нет — статус `INCOMPLETE / CONTRACT NOT FEASIBLE`, а не
адаптация критерия под полученные данные.

D-A, D-B и D-C — проектные решения и арифметика: `make`, стенд и FEM не нужны.
Setup measurement — единственный шаг, которому нужен стенд, и он остаётся
`NOT AUTHORIZED`.

## 24. Зафиксированный статус design-review layer

**Статус:** `AGREED STATUS / NO NUMERICAL CLOSURE WITHOUT SETUP DATA`.

| Layer | Состояние |
|---|---|
| Source / V0.4 | `PINNED + SYNCED` (`9ea121c`) |
| Coordinate semantics | `h_mech = 0.5°`, `h_el = 1.5° @ p=3` — `RESOLVED` |
| Phase-4 proxy | `E_trunc + E_corner = 1.327%` — `FAIL`, unchanged |
| D-A | structure `FREEZE CANDIDATE`; `N_θ,ref = 2400` planning candidate; interpolation `analytical candidate`; `m_max` `OPEN` |
| D-B | estimator form `FREEZE CANDIDATE`; `n` `OPEN`; `α_s` `OPEN`; `G_gate` `OPEN` |
| D-C | architecture `PROPOSED`; escalation `OPEN`; representativeness `OPEN`; `NOT FREEZE CANDIDATE` |
| `ε_real` | `UNKNOWN` |
| Measurement / HV | `NOT AUTHORIZED` |
| Maxwell / FEM | `BLOCKED` |

Обязательное правило: `n`, `G_gate`, `α_s` и `0.70%` — параметры реальной
измерительной системы. Их нельзя «закрыть» расчётом из этого документа или из
данных репозитория. Назначение их численно до получения `s_g`, `m_max`,
instrument floors и reference characteristics не даёт `FROZEN`, а даёт
`INCOMPLETE`; архитектурная экономия `3.7×` (§23.3) не является основанием
считать D-C почти замороженным.

Следующий переход — §23 → setup-design, а не уточнение Phase-4 proxy.
Setup-design обязан произвести:

```text
m_max             <- N_s (T_cog блокирован)
s_g               <- dispersion на одной представительной страте, 10 блоков
instrument floors <- разрешение/шум каналов момента и напряжения + source hash
reference         <- сертификат, полоса, шум, антиалиас, когерентность с углом
escalation        <- пять полей §23.3, объявленных до данных
```

Ни один из этих пунктов не выводится из текущего текста. До их появления
measurement contract остаётся `DRAFT / NOT FROZEN`, а
`E_trunc + E_corner = 1.327% > 1%` — единственное действующее количественное
утверждение Phase-4. Формализация перехода и pre-data acceptance contract — §25.

## 25. Setup-design / pre-data acceptance contract

**Статус:** `CANONICAL TRANSITION / PRE-DATA / NOTHING MEASURED / NOT FROZEN`.
Раздел объявляет правила **до** первого отсчёта. После объявления они неизменяемы
по результатам setup; меняется только вердикт: `FEASIBLE` либо
`CONTRACT NOT FEASIBLE`.

| Item | Статус |
|---|---|
| `m_max` | `OPEN` |
| `s_g` | `OPEN` |
| instrument floors | `OPEN` |
| reference characteristics | `OPEN` |
| `N_θ,ref` | planning candidate, не frozen |
| `G_gate`, `n`, `α_s` | объявляются до данных; численные значения `OPEN` |
| escalation | объявляется до данных, после — неизменяемо |
| estimator / coverage architecture / `α_gate` | `IMMUTABLE BY SETUP RESULTS` |
| feasibility inequality | проверяется после setup characterization |
| при невыполнении | `CONTRACT NOT FEASIBLE`, критерий не пересматривается |
| measurement / HV | `NOT AUTHORIZED` до отдельного разрешения |

### 25.1 Единицы — обязательное условие осмысленности feasibility equation

Шаблон `E_point + (t/√n)·s_g + 0.70% ≤ 1%` имеет смысл только если все три члена
выражены в одной единице. Принятая конвенция:

```text
E_r      = 100% · ε_r / W_ref,r        (проценты от W_ref того же блока)
W_ref,r  = ∫|T_ref,r(θ)| dθ по периоду блока r
E_g      = mean_{r in g} E_r           [п.п.]
s_g      = sd_{r in g} E_r             [п.п.]
0.70%    = относительный ceiling (§14), поэтому совпадает в любой страте [п.п.]
```

Требования к единицам:

1. `s_g` — СКО **нормализованных** блочных значений `E_r`, не СКО в джоулях;
2. каждый блок нормализуется своим `W_ref,r`, поэтому дрейф усиления внутри блока
   не превращается в разброс;
3. неравенство проверяется **по каждой gate-страте отдельно** со своим `W_ref,g`,
   вердикт — по худшей страте (согласовано с §8:
   `E_real,upper = max_g [100%·ε_{g,upper}/W_ref,g]`);
4. джоули, проценты от другой базы и проценты от общего `W_ref` кампании —
   `NOT_COMPARABLE`: неравенство не вычислимо, вердикт не выставляется.

Без выполнения 1–4 формула — **шаблон**, а не критерий. Это уточнение не меняет
§24 и не меняет `0.70%`.

### 25.2 Setup record schema

Обязательные поля записи (без них unit не входит в `R_valid`):

```text
session_id, setup_id, unit_id, campaign_id
firmware_image_hash, rom_hash, config_hash, calibration_chain_id
daq_id, channel_map, f_s [Гц], f_3dB [Гц], anti_alias_revision
angle_source = enc_angle | theta + clock_coherence_flags
torque_reference = transducer_id + certificate_id + range + gain/shunt
temperature_start, temperature_drift, vbus_mean, speed / f_e
stationarity_control_periods = 2 + post_series_control_period
```

### 25.3 Pre-data declarations

Объявляются до первого отсчёта и в том же commit/версии документа:

```text
m_max              <- путь получения из N_s (источник N_s указывается)
N_theta,ref        <- по §23.1 при объявленном m_max
escalation         <- пять полей §23.3 (триггер, величина, запас, THRESH, tie-break)
G_gate             <- полный список страт, не выборка
alpha_gate, alpha_s<- alpha_s = alpha_gate / G_gate
aggregation        <- mean по блокам внутри страты; max по gate-стратам
```

Добавление или удаление страты после просмотра `ε` запрещено. Форма правила
escalation фиксируется здесь же; значения его полей — тоже здесь, а не после
просмотра результатов.

### 25.4 Sensitivity-операции, объявленные заранее

Каждая операция даёт `measured` компонент вместо ceiling (§22.6):

| Операция | Вход | Выход |
|---|---|---|
| phase shift `±0.5°` эл | reference, те же блоки | `U_sync` + `U_angle` [п.п.] |
| resample `h/2 ↔ h` | reference | `U_rate` / квадратурный bias [п.п.] |
| scale `±0.1%` | reference | подтверждение сокращения (§22.5) |
| filter on/off | raw, если сохранён | `U_filter` [п.п.] |

Все четыре считаются до вердикта и не могут быть пересчитаны после его получения.

### 25.5 Feasibility check

```text
для каждой gate-страты g:
    E_g + t(1-alpha_s, n_g-1) * s_g / sqrt(n_g) + 0.70 <= 1.0     [п.п., одна база]
вердикт:
    все страты проходят         -> FEASIBLE (для объявленных n, G_gate, alpha_s)
    хотя бы одна не проходит    -> CONTRACT NOT FEASIBLE
```

`CONTRACT NOT FEASIBLE` — окончательный вердикт для **объявленного** контракта.
Он не даёт права менять estimator, `G_gate`, `α_gate`, coverage architecture или
`0.70%`. Возврат к проектированию возможен только новой версией документа
с новым объявлением и явным решением владельца — не правкой критерия «по месту».

### 25.6 Что setup не может изменить

```text
estimator form (§23.2)          IMMUTABLE
coverage architecture (§23.3)   IMMUTABLE
G_gate set и escalation rule    IMMUTABLE после объявления
alpha_gate                      IMMUTABLE
0.70% planning ceiling          IMMUTABLE (вниз — только при доказанном сокращении)
D2/D15 semantics                IMMUTABLE
```

Граница односторонняя: setup характеризует систему и выносит `FEASIBLE` /
`CONTRACT NOT FEASIBLE`; он не переопределяет правила, по которым его же судят.

### 25.7 Authorization

§25 не разрешает measurement, HV, запуск двигателя, инъекции в МК или Maxwell/FEM.
До отдельного разрешения владельца `measurement/HV = NOT AUTHORIZED`,
`ε_real = UNKNOWN`, Phase-4 proxy `1.327% FAIL` — без изменений.

`m_max`, `s_g`, instrument floors и reference characteristics из артефактов
репозитория не выводятся: для них §24 установил экспериментальный источник
истины, и подстановка расчётных значений вместо них запрещена.
