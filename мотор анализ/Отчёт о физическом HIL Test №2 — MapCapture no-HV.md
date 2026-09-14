# Отчёт о физическом HIL Test №2 — MapCapture no-HV

**Шаблон версии:** 1.0  
**Статус документа:** заполняется по одной физической кампании. Все поля вида `________________` должны быть заполнены либо помечены `N/A` с обоснованием.  
**Объект:** OEW Motor, STM32G474RE, Nucleo-G474RE, dual-inverter bench.  
**Режим испытания:** diagnostic MapCapture с физически отключённым DC-link и без внешнего VBUS на PC4.

> **Назначение.** Отчёт фиксирует выполнение физического no-HV Test №2 и его evidence. Он не разрешает подачу 60 В, не открывает `control_admitted` и не заменяет отдельный Stage-A safety gate.

> **Fail-closed правило.** При незаполненном обязательном поле, неполном evidence, неутверждённом diagnostic build или любом противоречии между UART/DMM/sigrok итог кампании — **NO-GO / FAIL**. Запрещено задним числом «достраивать» evidence или заменять CSV устным подтверждением.

---

## 0. Итоговый лист

| Поле | Значение |
|---|---|
| Идентификатор кампании | `test2_nohv_YYYYMMDD_HHMM` / ________________________ |
| Дата, начало — окончание, часовой пояс | ________________________ |
| Оператор стенда | ________________________ |
| Наблюдатель / свидетель no-HV boundary | ________________________ |
| Принимающий | ________________________ |
| Safety-owner diagnostic build gate | ________________________ |
| Git remote / `main` SHA | ________________________ |
| GitHub Actions CI run / статус | ________________________ |
| Номер Nucleo / ревизия стенда | ________________________ |
| ST-Link serial / фактический UART VCP | ________________________ |
| Analyzer / serial / driver / `sigrok-cli --version` | ________________________ |
| Каталог исходного evidence (не Git) | `campaign_raw/test2_nohv_<UTC>/` / ________________________ |
| `automation` | `PASS` / `FAIL` / `NOT_RUN` |
| `scope` | `PASS` / `FAIL` / `PENDING` / `NOT_APPLICABLE` |
| `final` | `PASS` / `FAIL` / `PENDING` / `NOT_RUN` |
| Решение по Stage A | **NO-GO** по умолчанию; отдельное решение: ________________________ |

---

## 1. Контролирующий контракт

Испытание использует **только** нижеприведённый strict fault-aware acceptance contract. Прежний legacy-вариант `term=-11` не является положительным результатом этой версии physical automation.

| Контроль | Требование physical Test №2 |
|---|---|
| Approved profile | `SYNT`, `profile_id=1398361684` (`0x53594E54`) |
| UART arm/run | `@MC:ARM rc=0`; `@MC:RUN rc=0` |
| Terminal state | `state=5` (`FAULTED`) и `term=-12` |
| Terminal cause | `detail=7` (`VBUS_LOW`) и `adc_status=7` (`WINDOW_INVALID`) |
| VBUS evidence | `raw_vbus <= 9`; `0 <= vbus_mV < 1000` |
| Current evidence | `abs(i1_ma) <= 10000`; `abs(i2_ma) <= 10000` |
| Records | `frames=0`; `dropped=0`; `avail=0`; drain `records=0`; нет `@MC:REC` |
| Capture | sigrok `rc=0`; создан валидный CSV |
| Physical scope | Ограниченный service burst; нет unexpected PWM до `run`; нет PWM после terminal state |
| Fault clear | Не выполняется автоматически; допустим только после полного evidence и отдельной записи ниже |

> Любой `term=-11`, другой `detail`, другой `adc_status`, timeout, `records>0`, `state=COMPLETE`, `term=0`, legacy/incomplete status или отсутствие CSV означает **automation=FAIL**.

---

## 2. Hard Gate G0 — разрешение diagnostic build

В текущем исходном коде synthetic profile включается только при одновременных `OEW_MAP_SYNTHETIC_PROFILE=1` и `OEW_HOST_TEST=1`; комментарий исходника описывает этот путь как deliberate host-test guard. Поэтому физический запуск с таким образом **запрещён**, пока safety-owner не выдаст явное документированное разрешение именно для no-HV Test №2 либо не утвердит иной diagnostic target build.

| G0-проверка | Требование | Факт / ссылка на evidence | PASS / FAIL |
|---|---|---|---|
| Разрешение safety-owner | Явно разрешает либо отклоняет `OEW_HOST_TEST=1` в physical **no-HV diagnostic-only** build | Документ/issue/подпись: ________________________ | ☐ PASS ☐ FAIL |
| Область разрешения | Разрешение ограничено только Test №2; не является production или Stage-A допуском | ________________________ | ☐ PASS ☐ FAIL |
| Точный набор defines | Сохранён build manifest с полным списком preprocessor defines и командой сборки | ________________________ | ☐ PASS ☐ FAIL |
| Исключение Stage A | В manifest/решении прямо запрещены `SYNT` и `OEW_HOST_TEST` для Stage A | ________________________ | ☐ PASS ☐ FAIL |
| Итог G0 | Все строки выше PASS | ________________________ | ☐ PASS ☐ FAIL |

**Правило:** если итог G0 не PASS, физические `mcarm` и `mapcap run` не выполняются. Заполнить итог как `NOT_RUN / NO-GO`, приложить evidence G0 и закрыть отчёт.

---

## 3. Идентификация образа и build evidence

| Поле | Заполненное значение |
|---|---|
| SHA исходного кода | ________________________ |
| Branch / tag | ________________________ |
| CI workflow, run ID/URL, зелёный статус | ________________________ |
| `git diff --check` перед сборкой | ☐ PASS, вывод: ________________________ |
| Компилятор / версия | ________________________ |
| Полная команда diagnostic build | `make EXTRA_CFLAGS="..."` / ________________________ |
| Полный список defines из build manifest | ________________________ |
| Путь к build log | ________________________ |
| Хэш `firmware.bin` / размер | ________________________ |
| Результат `make flash` / verification / reset | ________________________ |
| Время reset и начало UART log | ________________________ |

### 3.1 Diagnostic-image contract

| Define | Ожидаемое значение | Фактическое значение из manifest | Вердикт |
|---|---:|---:|---|
| `OEW_MAP_CAPTURE` | `1` | ________________________ | ☐ PASS ☐ FAIL |
| `OEW_MAP_L3` | `1` | ________________________ | ☐ PASS ☐ FAIL |
| `PWM_OEW_BOARD_REVISION` | `7` | ________________________ | ☐ PASS ☐ FAIL |
| `OEW_MAP_SYNTHETIC_PROFILE` | `1` | ________________________ | ☐ PASS ☐ FAIL |
| `OEW_HOST_TEST` | `1` **только при G0=PASS** | ________________________ | ☐ PASS ☐ FAIL |
| Другие defines | Зафиксированы; нет незаявленных safety overrides | ________________________ | ☐ PASS ☐ FAIL |

---

## 4. No-HV physical boundary и роли

Заполняется **до включения aux 3,3 В** и повторно непосредственно перед execution. Значения DMM должны содержать единицу измерения, прибор и время.

| Проверка | До aux 3,3 В | Перед execution | Evidence / прибор | PASS / FAIL |
|---|---|---|---|---|
| DC-link Inverter 1 физически отсоединён | ________________________ | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| DC-link Inverter 1 DMM | `<1 V`: ______ V | `<1 V`: ______ V | DMM / точка / время: ________________________ | ☐ PASS ☐ FAIL |
| DC-link Inverter 2 физически отсоединён | ________________________ | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| DC-link Inverter 2 DMM | `<1 V`: ______ V | `<1 V`: ______ V | DMM / точка / время: ________________________ | ☐ PASS ☐ FAIL |
| PC4: внешний источник VBUS отсутствует | ________________________ | ________________________ | Визуальная проверка / witness: ________________________ | ☐ PASS ☐ FAIL |
| PC4: штатная wiring/divider configuration | ________________________ | ________________________ | Схема/проверка: ________________________ | ☐ PASS ☐ FAIL |
| PC4: DMM на **утверждённой** безопасной точке, если она существует | ______ V / N/A (обоснование) | ______ V / N/A | Точка: ________________________ | ☐ PASS ☐ FAIL |
| Aux 3,3 В отдельный от DC-link источник | ________________________ | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| Процедура emergency-off и оператор назначены | ________________________ | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| Вал/механика: команды вращения запрещены | ________________________ | ________________________ | ________________________ | ☐ PASS ☐ FAIL |

**Итог no-HV boundary:** ☐ PASS — перейти к preflight; ☐ FAIL — `NOT_RUN / NO-GO`.

---

## 5. Инфраструктурная готовность (не является acceptance Test №2)

Эти проверки повышают уверенность в целостности стенда, но encoder result сам по себе не определяет Verdict MapCapture.

| Проверка | Команда / действие | Результат и точный лог | Классификация |
|---|---|---|---|
| UART VCP | `sysinfo` | ________________________ | Infrastructure readiness: ☐ PASS ☐ FAIL |
| Analyzer discovery | `py -3 tools\bench_test2_capture.py --scan-sigrok` | ________________________ | Infrastructure readiness: ☐ PASS ☐ FAIL |
| C8: standalone analyzer connectivity | Короткий capture **без PWM command и не как evidence Test №2** | CSV path/validity: ________________________ | Infrastructure readiness: ☐ PASS ☐ FAIL |
| SD safety | Approved check of SD1/SD2 high | ________________________ | Preflight gate: ☐ PASS ☐ FAIL |
| Encoder | `enc` | `err=___`; period=___; log: ________________________ | Infrastructure readiness: ☐ PASS ☐ FAIL / ☐ N/A |

> C8 проверяет только acquisition chain. Он не запускает PWM, не входит в actual Test №2 capture и не может использоваться для `scope=PASS`.

---

## 6. Preflight physical Test №2

Все команды отправляются по одной, с непрерывной записью `uart.log`. При любом FAIL `mcarm`/`run` запрещены.

| ID | Команда / действие | Ожидаемый результат | Фактический response / измерение | Evidence timestamp | PASS / FAIL |
|---|---|---|---|---|---|
| P1 | Включить только aux 3,3 В; проверить SD1/SD2 | SD1/SD2 high, нет active fault | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P2 | `sysinfo` | Ответ от прошитого SHA | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P3 | `p?` и/или `pdump` | Software: PWM off, `MOE=0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P4 | Hardware observation выходов PWM | Выходы неактивны до `run` | Probe/channel/trace: ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P5 | Запустить actual sigrok capture, ещё без `run` | Capture начат и включает pre-run window | Start time/CSV target: ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P6 | Проверить pre-run portion capture | Нет unexpected transition/burst до `run` | Scope note: ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P7 | `a` | I1/I2 не насыщены; raw VBUS согласуется с PC4=0 | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P8 | `c` | Calibration successful | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P9 | Offset evidence | `offset_i1`, `offset_i2`, `offset_valid`, `calibration_status` сохранены | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| P10 | `mapcap status` | IDLE; `frames=0`; `avail=0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |

**Preflight gate:** ☐ PASS — разрешён единственный `mcarm → run` execution; ☐ FAIL — `NOT_RUN / NO-GO`.

---

## 7. Actual Test №2 execution

`actual capture` — единственный захват, используемый для verdict. Он должен быть стартован до `mapcap run`; оператор не добавляет параллельных команд и не повторяет `run` после FAIL.

| Шаг | Команда / действие | Фактический TX/RX / timestamp | PASS / FAIL |
|---:|---|---|---|
| X1 | Confirm actual sigrok capture active | ________________________ | ☐ PASS ☐ FAIL |
| X2 | `mcarm=1398361684` | ________________________ | ☐ PASS ☐ FAIL |
| X3 | `mapcap status` после arm | ________________________ | ☐ PASS ☐ FAIL |
| X4 | `mapcap run` | ________________________ | ☐ PASS ☐ FAIL |
| X5 | Terminal polling скриптом до absolute deadline | Количество poll: ____; terminal time: ________________________ | ☐ PASS ☐ FAIL |
| X6 | `mapcap drain` | ________________________ | ☐ PASS ☐ FAIL |
| X7 | Завершение actual sigrok capture | Stop time: ________________________ | ☐ PASS ☐ FAIL |
| X8 | Сохранение `summary.json` | ________________________ | ☐ PASS ☐ FAIL |

### 7.1 Strict terminal-evidence table

| Поле | Ожидание | Фактическое значение | Источник (`uart.log` / `summary.json`) | PASS / FAIL |
|---|---|---|---|---|
| `profile_id` | `1398361684` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| ARM | `rc=0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| RUN | `rc=0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `state` | `5` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `term` | `-12` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `detail` | `7` / `VBUS_LOW` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `adc_status` | `7` / `WINDOW_INVALID` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `raw_vbus` | `<=9` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `vbus_mV` | `0 <= x < 1000` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `i1_ma` | `abs(x) <= 10000` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `i2_ma` | `abs(x) <= 10000` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `frames` | `0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `dropped` | `0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `avail` | `0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| Drain | `records=0` | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| `@MC:REC` | Absent | ________________________ | ________________________ | ☐ PASS ☐ FAIL |
| Terminal polling | До deadline, без retry после terminal/FAIL | ________________________ | ________________________ | ☐ PASS ☐ FAIL |

**Automation verdict:** ☐ PASS ☐ FAIL  
**`failure_stage` / `failure_reason`, если FAIL:** ________________________

---

## 8. Sigrok/CSV evidence и manual scope verdict

| Проверка | Требование | Факт / ссылка | PASS / FAIL |
|---|---|---|---|
| Analyzer identity | Соответствует разделу 0 | ________________________ | ☐ PASS ☐ FAIL |
| Actual capture start | До `mapcap run` | Capture start: ____; run TX: ____ | ☐ PASS ☐ FAIL |
| Actual capture end | После terminal state | Terminal: ____; capture end: ____ | ☐ PASS ☐ FAIL |
| Correlation 1 | `run_timestamp < first expected PWM activity` | ________________________ | ☐ PASS ☐ FAIL |
| Correlation 2 | `terminal_timestamp < PWM stop` | ________________________ | ☐ PASS ☐ FAIL |
| Correlation 3 | `terminal_timestamp < capture_end` | ________________________ | ☐ PASS ☐ FAIL |
| CSV | Файл существует, парсится, относится к actual capture | ________________________ | ☐ PASS ☐ FAIL |
| Pre-run observation | Нет unexpected PWM transition/burst до `run` | ________________________ | ☐ PASS ☐ FAIL |
| Bounded service burst | Наблюдается только ограниченный начальный service burst | ________________________ | ☐ PASS ☐ FAIL |
| PWM after terminal | Нет активности PWM после terminal state | ________________________ | ☐ PASS ☐ FAIL |
| Overlap check | На наблюдаемых HIN/LIN нет недопустимого overlap | ________________________ | ☐ PASS ☐ FAIL |
| sigrok process | `rc=0`; stdout/stderr сохранены | ________________________ | ☐ PASS ☐ FAIL |

**Scope verdict:** ☐ PASS ☐ FAIL ☐ PENDING  
**Причина / замечания reviewer:** ________________________

---

## 9. Полный перечень артефактов

Все пути должны указывать на неизменённые оригиналы. Артефакты стенда не коммитируются в Git.

| Артефакт | Required | Фактический путь / имя | SHA-256 или размер | Проверил |
|---|---|---|---|---|
| Campaign folder | Да | `campaign_raw/test2_nohv_<UTC>/` / ________________________ | ________________________ | ________________________ |
| `metadata.json` | Да | ________________________ | ________________________ | ________________________ |
| `summary.json` | Да | ________________________ | ________________________ | ________________________ |
| `uart.log` (непрерывный: boot, TX, RX) | Да | ________________________ | ________________________ | ________________________ |
| `sigrok_digital.csv` actual capture | Да | ________________________ | ________________________ | ________________________ |
| `sigrok_stdout.log` | Да | ________________________ | ________________________ | ________________________ |
| `sigrok_stderr.log` | Да | ________________________ | ________________________ | ________________________ |
| Diagnostic build log | Да | ________________________ | ________________________ | ________________________ |
| Diagnostic build manifest / defines | Да | ________________________ | ________________________ | ________________________ |
| Flash verification log | Да | ________________________ | ________________________ | ________________________ |
| G0 safety-owner approval | Да | ________________________ | ________________________ | ________________________ |
| DMM readings / worksheet | Да | ________________________ | ________________________ | ________________________ |
| Pre-run and actual scope trace/screenshot | Да | ________________________ | ________________________ | ________________________ |
| Default-deny build log / manifest | Да после campaign | ________________________ | ________________________ | ________________________ |
| Default-deny flash verification | Да после campaign | ________________________ | ________________________ | ________________________ |
| Final `p?` / `pdump` after reset | Да после campaign | ________________________ | ________________________ | ________________________ |

---

## 10. Fault latch и manual clear (заполняется только после evidence freeze)

| Условие | Факт | PASS / FAIL |
|---|---|---|
| Evidence из раздела 9 сохранён до `f` | ________________________ | ☐ PASS ☐ FAIL |
| SD1/SD2 повторно проверены high | ________________________ | ☐ PASS ☐ FAIL |
| Решение/разрешение на clear | ________________________ | ☐ PASS ☐ FAIL |
| Команда `f` и response | ________________________ | ☐ PASS ☐ FAIL |
| `p?`/`pdump` после clear | PWM/MOE остаются off: ________________________ | ☐ PASS ☐ FAIL |
| Неожиданная активность после clear | ☐ Нет ☐ Да; details: ________________________ | ☐ PASS ☐ FAIL |

> Если PWM продолжается после terminal state или меняется неожиданно после clear: не запускать повторно, сохранить непрерывный trace и действовать только по утверждённой локальной процедуре emergency-off. Итог кампании — FAIL.

---

## 11. Closure — возврат к generic default-deny

| Проверка | Требование | Факт / evidence | PASS / FAIL |
|---|---|---|---|
| Evidence freeze | Полный набор раздела 9 сохранён | ________________________ | ☐ PASS ☐ FAIL |
| Build command | `make clean`; `make` без diagnostic defines | ________________________ | ☐ PASS ☐ FAIL |
| Default-deny manifest | `OEW_MAP_SYNTHETIC_PROFILE` отсутствует | ________________________ | ☐ PASS ☐ FAIL |
| Default-deny manifest | `OEW_HOST_TEST` отсутствует | ________________________ | ☐ PASS ☐ FAIL |
| Flash verification | Generic image прошит и reset выполнен | ________________________ | ☐ PASS ☐ FAIL |
| Runtime baseline | `p?`/`pdump`: PWM off, `MOE=0` | ________________________ | ☐ PASS ☐ FAIL |
| Runtime profile denial | `mcarm=1398361684` не даёт synthetic profile | ________________________ | ☐ PASS ☐ FAIL |
| No-HV boundary on closure | Обе DC-link шины остаются `<1 V` | ________________________ | ☐ PASS ☐ FAIL |

**Closure verdict:** ☐ PASS ☐ FAIL

---

## 12. Итоговая матрица и подписи

| Слой | Условие PASS | Факт | Итог |
|---|---|---|---|
| G0 diagnostic authorisation | Явное physical no-HV разрешение или утверждённый alternative build | ________________________ | ☐ PASS ☐ FAIL |
| Readiness / preflight | Все обязательные hard gates PASS | ________________________ | ☐ PASS ☐ FAIL |
| Automation | Все строки strict terminal-evidence table PASS | ________________________ | ☐ PASS ☐ FAIL |
| Scope | CSV/timing/outputs review PASS | ________________________ | ☐ PASS ☐ FAIL ☐ PENDING |
| Closure | Default-deny доказан compile-time и runtime | ________________________ | ☐ PASS ☐ FAIL |
| Final Test №2 | `automation=PASS` и `scope=PASS`, без unresolved safety issue | ________________________ | ☐ PASS ☐ FAIL ☐ PENDING |
| Stage A | Отдельный gate с Stage-A acquisition profile/path A/B и 60-V safety plan | ________________________ | **NO-GO** / отдельное решение |

| Роль | ФИО | Дата/время | Подпись / ссылка на approval |
|---|---|---|---|
| Оператор стенда | ________________________ | ________________________ | ________________________ |
| Наблюдатель no-HV boundary | ________________________ | ________________________ | ________________________ |
| Reviewer scope / evidence | ________________________ | ________________________ | ________________________ |
| Safety-owner G0 | ________________________ | ________________________ | ________________________ |
| Принимающий Test №2 | ________________________ | ________________________ | ________________________ |

## 13. Отклонения и CAPA

| ID | Наблюдение | Классификация | Немедленное действие | Evidence | Владелец и следующее отдельное ТЗ |
|---|---|---|---|---|---|
| D-01 | ________________________ | ☐ Preflight NO-GO ☐ Automation FAIL ☐ Scope FAIL ☐ Critical anomaly | ________________________ | ________________________ | ________________________ |
| D-02 | ________________________ | ☐ Preflight NO-GO ☐ Automation FAIL ☐ Scope FAIL ☐ Critical anomaly | ________________________ | ________________________ | ________________________ |
| D-03 | ________________________ | ☐ Preflight NO-GO ☐ Automation FAIL ☐ Scope FAIL ☐ Critical anomaly | ________________________ | ________________________ | ________________________ |

---

## References

[1]: `tools/bench_test2_capture.md` — действующий physical automation contract, CLI, evidence и `automation/scope/final` semantics.

[2]: `src/map_capture_profiles.c` — compile-time guard synthetic profile: `OEW_MAP_SYNTHETIC_PROFILE` вместе с `OEW_HOST_TEST`.

[3]: `TZ_MAPCAP_FAULT_DETAIL_TEST2.md` — extended UART terminal evidence and fault detail.

[4]: `docs/AGENTS_WORKFLOW.md` — CI and accepted-source requirements.

[5]: `План подготовки к физическому HIL Test №2 — MapCapture no-HV.md` — readiness/execution/closure plan and Stage-A boundary.
