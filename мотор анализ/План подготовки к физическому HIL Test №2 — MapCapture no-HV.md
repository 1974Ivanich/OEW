# План подготовки к физическому HIL Test №2 — MapCapture no-HV

**Статус:** план подготовки; не является разрешением на подачу DC-link или запуск стенда.  
**Цель:** безопасно подготовить и провести на полном реальном стенде HIL-проверку ограниченного MapCapture service path при физически отключённом DC-link и штатном нуле на PC4.

> **Базовый принцип.** Test №2 проверяет только предсказуемый fail-closed отказ MapCapture при нулевом VBUS. Он не измеряет карту, не подтверждает admission, не разрешает FOC/V/f/autotune и не является допуском к Stage A с 60 В.

## 1. Управляющий контракт и граница применимости

Исполнение должно следовать текущему strict fault-aware контракту в `tools/bench_test2_capture.md`. Прежняя стендовая инструкция допускает `term=-11` как исторический вариант, однако после принятого пакета fault-detail этот результат **не принимается автоматизацией physical Test №2**. Для данного плана единственный положительный terminal outcome — явное доказательство `VBUS_LOW`; пересмотр правила возможен только отдельным принятым ТЗ, изменением контракта и повторной автоматической регрессией.

| Область | Разрешено в Test №2 | Запрещено в Test №2 |
|---|---|---|
| Питание | Изолированное логическое питание Nucleo и штатное aux 3,3 В STEVAL | Любое подключение или подача DC-link; «низковольтная замена» DC-link |
| VBUS / PC4 | PC4 остаётся без внешнего источника, штатный ноль | Имитатор VBUS, инъекция напряжения на PC4 |
| Прошивка | Временный diagnostic образ с утверждённым `SYNT` | Production/Stage-A образ как замена diagnostic; изменение acceptance contract «на месте» |
| Действия ПО | `sysinfo`, `p?`/`pdump`, ADC-калибровка, `enc`, `mcarm`, `mapcap run/status/drain` через утверждённый script | FOC, V/f, autotune, `mapcap build`, JSONL, ручное открытие admission |
| Измерения | UART, DMM, цифровой захват PWM/SD на утверждённых линиях | UART вместо цифрового захвата; неподтверждённое подключение probes |

## 2. Условия начала: все обязательны

Оператор стенда и принимающий должны оформить это как единый pre-flight. Если хотя бы один пункт не закрыт документированным PASS, решение автоматически **NO-GO**; нельзя заменять отсутствующее доказательство устным подтверждением или ослаблять скрипт.

| Категория | Обязательное условие | Доказательство до включения aux 3,3 В | Ответственный |
|---|---|---|---|
| Исходный код | Ветка `main` на принятом SHA; обязательный CI зелёный; рабочая копия без незапланированного diff | SHA, ссылка/номер зелёного CI, `git diff --check` | Принимающий |
| Прошивка | Известны аргументы diagnostic build; выбранный бинарник соответствует этому SHA | Build log и manifest с SHA/флагами | Оператор + принимающий |
| Nucleo / SWD | Nucleo-G474RE, ST-Link/SWD и **фактический** UART VCP идентифицированы | Серийный номер платы/пробника, `--list-ports`, `sysinfo` после прошивки | Оператор |
| Полный силовой тракт | Две STEVAL, штатные датчики I1/I2/VBUS, исправная проводка и двигатель подключены только по утверждённой схеме | Визуальный checklist и фото/схема соединений, если это разрешено локальными правилами | Оператор |
| No-HV boundary | Обе DC-link шины физически отсоединены; DMM показывает менее 1 В на каждой; PC4 без внешнего VBUS | Два DMM-показания с единицами, отметка «PC4=0, external source absent» | Оператор, свидетель |
| Логическое питание и safety | Aux 3,3 В STEVAL от отдельного источника; SD1/SD2 high подтверждены approved измерением; доступна локальная процедура снятия aux | Показания SD, описание точки безопасного отключения, назначенный оператор | Оператор |
| Захват | Логический анализатор `fx2lafw`/Saleae подключён только к утверждённым D0…D11, `sigrok-cli` доступен и scan успешен | Вывод `--scan-sigrok`, test capture, имя устройства | Оператор |
| Роли | Один оператор исполняет команды; принимающий готовит решение; наблюдатель может подтвердить отсутствие DC-link | Лист ролей и время сессии | Принимающий |

## 3. Этапы подготовки

### Этап A — формальная готовность и каталог evidence

Создать новую папку кампании `campaign_raw/test2_nohv_<UTC>/` вне Git. В `metadata` или журнале начала сеанса до любых команд зафиксировать SHA `main`, URL/идентификатор зелёного CI, плату, ST-Link, фактический COM-порт, идентификатор анализатора, оператора, принимающего, время, две DMM-проверки DC-link и подтверждение отсутствия внешнего VBUS на PC4.

**Гейт A:** все строки раздела 2 заполнены; иначе не переходить к прошивке.

### Этап B — подготовка диагностического образа

На выбранном SHA собрать временный образ только после завершения Этапа A. Утверждённая конфигурация:

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1"
make flash
```

Build log и результат прошивки сохранить в папку evidence. Передать в протокол точную команду, SHA и дату. Этот образ предназначен исключительно для no-HV Test №2: `SYNT` и `OEW_HOST_TEST` нельзя переносить в Stage A или считать production-разрешением.

**Гейт B:** прошивка верифицирована, UART после reset отвечает, нет неожиданного непрерывного fault-latch. Если build, flash или UART не подтверждены, выполнить STOP и разбирать проблему вне текущей HIL-кампании.

### Этап C — de-energized и logical-power pre-flight

Порядок выполняется по одной операции с непрерывным UART-логом. До `mcarm` оператор повторяет DMM-проверку обеих шин DC-link, убеждается в отсутствии подключённого VBUS на PC4, включает только aux 3,3 В и подтверждает SD1/SD2 high. Затем выполняет разрешённые диагностические команды.

| Шаг | Действие / команда | Обязательное наблюдение | STOP при любом из условий |
|---:|---|---|---|
| C1 | Повторный DMM обеих DC-link шин | Каждая менее 1 В, значения сохранены | Любая шина ≥1 В или измерение сомнительно |
| C2 | Проверка PC4 и внешних соединений | Нет имитатора/внешнего источника VBUS | Любое внешнее воздействие на PC4 |
| C3 | Включить только aux 3,3 В; проверить SD1/SD2 | Обе линии high, нет active fault | SD low, неизвестная защита, нет процедуры снятия aux |
| C4 | `sysinfo`, затем `p?` и/или `pdump` | Опознана свежая прошивка; PWM исходно выключен, MOE=0 | Неверный образ, PWM/MOE активен, неполный UART |
| C5 | `a`, затем `c` | I1/I2 не насыщены; калибровка успешна; VBUS соответствует PC4=0 | Saturation, invalid offsets или калибровка FAIL |
| C6 | `enc` | `err=0`; результат сохранён | Encoder error |
| C7 | `mapcap status` | IDLE: неактивный session, нулевые records/avail | Session не IDLE, записи или неясный status |
| C8 | Тестовое обнаружение capture | `--scan-sigrok` и краткий допустимый test capture успешны | Analyzer/driver/path/CSV не работают |

**Гейт C:** все C1–C8 PASS. Ни `mcarm`, ни `mapcap run` не выполняются при любом FAIL.

### Этап D — запуск автоматизированной HIL-кампании

Только после Gate C оператор запускает physical backend. В аргументе `--port` указывается UART Virtual COM Port MCU, а не предположительный номер. Ниже приведён шаблон; конкретный номер порта фиксируется в протоколе только после C4.

```powershell
py -3 tools\bench_test2_capture.py `
  --port <MCU_UART_VCP> `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

Скрипт обязан начать sigrok capture **до** `mapcap run`, затем выполнить `mcarm=1398361684`, `mapcap run`, terminal polling с absolute monotonic deadline и `mapcap drain`. По умолчанию fault не очищается. Оператор не добавляет параллельных команд управления и не редактирует evidence во время кампании.

### Этап E — автоматический verdict и ручная scope-приёмка

Автоматизация допускает только следующий строгий outcome. Любое отклонение, включая legacy/неполный `@MC:STATUS`, является `automation=FAIL`.

| Проверка | Единственное допустимое значение для `automation=PASS` |
|---|---|
| Profile и старт | `profile_id=1398361684`; `@MC:ARM rc=0`; `@MC:RUN rc=0` |
| Terminal state | `state=5` (`FAULTED`), `term=-12` |
| Причина | `detail=7` (`VBUS_LOW`) и `adc_status=7` (`WINDOW_INVALID`) |
| VBUS evidence | `raw_vbus <= 9`; `0 <= vbus_mV < 1000` |
| Токи | `abs(i1_ma) <= 10000`, `abs(i2_ma) <= 10000` |
| Records | `frames=dropped=avail=0`; `@MC:DRAIN:records=0`; нет `@MC:REC` |
| Захват | sigrok завершается с `rc=0` и создаёт CSV |
| Safety output | После terminal state нет непрерывного PWM; manual trace подтверждает bounded service burst и выключение PWM |

После automation PASS итог остаётся `scope=PENDING`, `final=PENDING`. Принимающий вручную просматривает `sigrok_digital.csv`: подтверждает ограниченный начальный service burst, отсутствие опасного overlap на наблюдаемых PWM-линиях и отсутствие PWM после terminal state. Только затем устанавливается `scope=PASS`, `final=PASS` в протоколе. Скрипт не заменяет эту проверку.

## 4. Условия немедленного STOP и действия после STOP

| Событие | Классификация | Немедленное безопасное действие | Обязательные evidence |
|---|---|---|---|
| DC-link ≥1 В, PC4 с внешним VBUS, неясная проводка | Pre-flight NO-GO | Не включать aux или run; привести стенд к de-energized состоянию по локальной процедуре | DMM, фото/описание, время |
| SD low, PWM/MOE неожиданно active до run | Safety anomaly | Не отправлять `mcarm/run`; безопасно снять aux по утверждённой процедуре, если необходимо | UART, `p?`/`pdump`, trace |
| ARM/RUN rc≠0, неправильный profile, timeout | Software/commissioning FAIL | Не повторять run «для проверки»; сохранить evidence | UART, summary, build manifest |
| `term=-11`, любой detail кроме VBUS_LOW, `adc_status` не WINDOW_INVALID | Contract FAIL | Не ослаблять verdict и не трактовать как PASS | Полный UART/status/summary |
| Любые records, `state=COMPLETE`, `term=0`, VBUS/high-current limit fault | Safety blocker | Остановить кампанию; не выполнять build/FOC | UART, sigrok CSV, DMM, summary |
| PWM продолжается после terminal state | Critical anomaly | Не посылать стартовые команды; при необходимости снять aux **только** по локальной approved процедуре | Непрерывный trace, UART, регистры, время |
| sigrok/CSV отсутствует или повреждён | Evidence FAIL | Не подменять его UART-логом; устранить setup до повторной отдельной кампании | stdout/stderr, scan output |

Fault clear допустим только после сохранения complete evidence, повторной проверки SD1/SD2 high и решения оператора. После `f` надо зафиксировать `p?`/`pdump`; clear не должен включать MOE или таймеры. При неудаче clear или изменении PWM итог кампании — FAIL.

## 5. Закрытие кампании и возврат к default-deny

После ручного verdict сохраните неизменёнными UART, `summary.json`, `metadata.json`, CSV, stdout/stderr sigrok, DMM-показания, build/flash logs и сведения о trace. Артефакты стенда не коммитируются в Git. Затем вернуть плату к generic default-deny образу:

```powershell
make clean
make
make flash
```

После reset подтвердить `p?`: PWM выключен. Завершить протокол одной строкой с SHA, фактическим VBUS/raw VBUS, terminal reason/detail, records, результатом scope review, поведением после `f`, именами evidence и финальным `PASS`/`FAIL`.

## 6. Решение по результату и Stage-A boundary

| Вердикт Test №2 | Следующее действие | Что это **не** разрешает |
|---|---|---|
| `automation=FAIL` | NO-GO; расследование в новом ТЗ/ветке, сохранение evidence | Повторный run без разбора, Stage A, admission |
| `automation=PASS`, `scope=PENDING` | Просмотр CSV и физическая подпись протокола | `final=PASS`, Stage A |
| `automation=PASS`, `scope=FAIL` | NO-GO; остановить кампанию и разбирать trace | Stage A, FOC, V/f, build |
| `automation=PASS`, `scope=PASS`, `final=PASS` | Закрыть Test №2 и вернуть default-deny | Автоматический GO к Stage A |

Даже `final=PASS` является лишь необходимым входом в будущую Stage A. Отдельно должны быть закрыты доказательства T1–T4 SD/interlock, зелёный CI согласованного исходного кода, назначенные оператор и приёмщик, процедура emergency-off, утверждённый токовый лимит, а также отдельный safety-пакет для пути A (узкий board-specific acquisition profile) **или** пути B (независимая предварительная scope-характеризация). `SYNT` никогда не заменяет Stage-A profile.

## 7. Минимальный лист подписей

| Поле | Заполняет |
|---|---|
| Дата/время, оператор, наблюдатель, принимающий | Все назначенные роли |
| SHA main, CI run, diagnostic build flags и COM VCP | Оператор + принимающий |
| DMM: DC-link Inverter 1 / Inverter 2; PC4 external source absent | Оператор + свидетель |
| SD1/SD2, offsets, calibration, encoder, baseline MOE | Оператор |
| ARM/RUN/status/drain и strict terminal evidence | Автоматический summary + оператор |
| Имена UART/CSV/trace/build evidence | Оператор |
| Scope review и `final` | Принимающий |
| Возврат к default-deny, PWM off после reset | Оператор + принимающий |
| Решение Stage A | Принимающий; по умолчанию NO-GO до отдельного gate |

## References

[1]: `tools/bench_test2_capture.md` — действующий strict automation contract и physical CLI.

[2]: `TZ_MAPCAP_FAULT_DETAIL_TEST2.md` — terminal fault detail и VBUS evidence contract.

[3]: `docs/BENCH_MAPCAP_FAILCLOSED_CHECKLIST.md` — ранее подтверждённый baseline fail-closed step №1.

[4]: `мотор анализ/Инструкция стенда_ тест № 2 MapCapture без DC-link и критерии перехода к Stage A (60 В).md` — стендовая последовательность и Stage-A gates; его legacy allowance `term=-11` заменён strict contract [1].

[5]: `docs/AGENTS_WORKFLOW.md` — CI и приёмка исходного кода.
