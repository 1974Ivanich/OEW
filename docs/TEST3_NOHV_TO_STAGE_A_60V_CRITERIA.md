# Переход от controlled no-HV Test №3 к отдельному Stage A с DC-link 60 V

## Статус по умолчанию

> **Stage A с DC-link 60 V по умолчанию BLOCKED.** Ни один результат Test №2, Test №3, G0, pre-flight, UART parser, sigrok CSV или `summary.json` сам по себе не переводит этот статус в GO.

Test №2 — это физический ADC/measurement-chain baseline при физически отключённом DC-link. Controlled MapCapture no-HV campaign относится к Test №3; исторические названия скриптов `bench_test2_*` остаются только machine-readable legacy identifiers. [1]

Новый offline parser `bench_test2_rerun_verdict.py` может подтвердить согласованность terminal evidence повторного no-HV прогона, но всегда записывает `stage_a_60v=BLOCKED`. Он не выполняет и не разрешает аппаратных действий.

## Почему no-HV PASS недостаточен

No-HV acceptance намеренно доказывает fail-closed защитную реакцию: `state=5`, `term=-12`, `detail=VBUS_LOW`, `adc_status=WINDOW_INVALID`, нулевые records и низкое VBUS evidence. Это полезная проверка цепочки измерения, terminal handling и evidence collection, но она не проверяет поведение с энергией на силовой шине, допустимый режим управления, тепловой режим, protection response под нагрузкой или конкретную схему питания Stage A. [2]

Текущий шаблон `HIL_TEST3_G0` специально имеет `decision=PENDING` и содержит `forbids_stage_a=true`, `forbids_dc_link=true`, `forbids_foc=true`, `forbids_vf=true`, `forbids_autotune=true`. Даже будущий approved no-HV G0 ограничен точным diagnostic no-HV scope и **не может** быть переиспользован как разрешение на 60 V. [3]

## Модель перехода

| Уровень | Что проверяется | Разрешённый результат | Что остаётся запрещённым |
|---|---|---|---|
| Test №2 | ADC baseline, default-deny, калибровка, VBUS без внешнего стимула | Physical Test №2 PASS | MapCapture, FOC/V/f, DC-link, characterization |
| Test №3 no-HV | Approved diagnostic-only G0, pre-flight, controlled terminal `VBUS_LOW`, UART/sigrok evidence | Physical no-HV campaign verdict | Stage A, DC-link, production control |
| Stage A readiness review | Отдельные 60 V hardware/software/safety criteria ниже | Только письменное `HIL_STAGE_A_60V=APPROVED` для exact plan/image | Любой иной test, voltage, image или wiring |
| Stage A execution | Только утверждённый operator procedure | Факты по точкам остановки Stage A | Расширение scope без нового approval |

## Обязательные criteria для отдельного `HIL_STAGE_A_60V` approval

Все строки являются AND-gates: значение `PENDING`, `UNKNOWN`, `NOT_APPLICABLE`, отсутствие evidence или любое FAIL означает **BLOCKED**. Указанные действия выполняет назначенный safety-owner/оператор по локальным правилам безопасности; этот документ не является инструкцией по подключению или включению 60 V.

| ID | Независимый gate | PASS evidence, проверяемое до DC-link 60 V | Блокирует переход |
|---|---|---|---|
| A60-01 | Закрытый Test №2 | Принятый physical Test №2 report: verified default-deny identity, calibration, complete ADC observations, no-HV boundary и command audit. | Частичный/Nucleo-only/simulated test, неразрешённая аномалия ADC/VBUS или отсутствующий архив. |
| A60-02 | Закрытый Test №3 no-HV | G0/pre-flight/controlled no-HV campaign evidence сохранены; terminal evidence соответствует contract; scope CSV принят человеком; archive hash manifest проверен. | `PENDING` G0, `FAIL`, отсутствующий sigrok scope review, ненулевые records, `term=-11`/`ADC_SATURATED`, malformed evidence. |
| A60-03 | Новый scope approval | Письменный `HIL_STAGE_A_60V=APPROVED` от назначенного safety-owner с ID, временем, оператором, локальной процедурой, конкретным maximum voltage/current limit и stop criteria. | Использование approval Test №3, устное согласование, `PENDING`, неидентифицированный approver или расширение scope. |
| A60-04 | Точный firmware/image | Новый reviewed Stage A plan указывает source SHA, green CI run, CI-produced binary SHA-256, target board identity и configuration. | Local-only binary как единственный provenance, diagnostic `OEW_HOST_TEST`/synthetic no-HV image, SHA mismatch или неподтверждённая configuration. |
| A60-05 | Review safety/control configuration | Отдельный review required control/protection paths для exact Stage A configuration; default-deny/commissioning admission, break/interlock and fault policy определены и проверяемы. | Неявное включение FOC/V/f, bypass/suppression safety paths, изменение `.ioc`/safety modules без отдельного review. |
| A60-06 | Независимая bench readiness | Два человека или эквивалентная независимая проверка закрепляют wiring, polarity, protective earth/insulation, emergency-stop/removal procedure, supply limit and disabled initial state по локальному EHS procedure. | Неполный harness, неясная топология, неработоспособная emergency action, активный источник до письменного GO. |
| A60-07 | Измерение и capture plan | Утверждены DMM/осциллограф/изоляция probes, сигналы для наблюдения, sampling limits, data destination, expected safe terminal behavior и stop criteria. | Нет измерительных средств/диапазонов/синхронизации, или evidence plan заменён UART-only наблюдением. |
| A60-08 | Stepwise experimental plan | Отдельный versioned procedure задаёт pre-energization checks, low-energy stop points, кто имеет право продолжить, и автоматический STOP при отклонении. | Попытка начать сразу с «full test», отсутствие конкретного stop point или изменение условий на ходу. |
| A60-09 | Campaign provenance | Новый каталог вне Git, readiness checklist, approval, hashes, operator/time log и blank observation forms созданы до energization. | Артефакты пишутся задним числом, смешение campaign folders, отсутствуют source/image links. |
| A60-10 | Final human GO | Safety-owner и operator подписали все предыдущие PASS evidence непосредственно перед началом exact Stage A session. | Любой change source/image/wiring/limits/operator/date после approval без повторного review. |

## Критерии терминальной остановки во время Stage A

Stage A plan обязан иметь собственный набор порогов и terminal handling. До его отдельного утверждения следующие условия трактуются как безусловный STOP: неверная board/image identity, отсутствие telemetry/capture, неясный or unexpected PWM admission, unexpected fault/reset, evidence loss, supply limit event, abnormal sound/smell/temperature, нарушенная изоляция или любое сомнение оператора.

После STOP питание и дальнейшие действия регулируются локальной процедурой стенда; evidence сохраняется неизменным. Нельзя очищать faults, повышать limits или повторять запуск, пока safety-owner не классифицирует событие и не зафиксирует решение.

## Decision record

Перед 60 V должен существовать отдельный неизменяемый decision record со следующими полями.

| Поле | Требование |
|---|---|
| Gate | `HIL_STAGE_A_60V` |
| Decision | Только `APPROVED` или `REJECTED`; `PENDING` = BLOCKED |
| Scope | Exact source SHA, CI binary SHA-256, board, wiring revision, supply maximum/current limit и one approved procedure revision |
| Authority | Имя/роль safety-owner, operator, UTC time и approval ID |
| Preconditions | Результаты A60-01…A60-10 с ссылками/хешами на evidence |
| Boundaries | Явный запрет выходить за approved voltage/current/command scope |
| Stop criteria | Версия procedure и правило: first unexpected event → STOP/preserve evidence |

## References

[1]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md` — классификация Test №2 и его граница.

[2]: `tools/bench_test2_capture.md` — strict terminal no-HV UART/evidence contract.

[3]: `docs/templates/test3_nohv_campaign/g0_approval.json` — template Test №3 G0 с явными no-HV запретами.

[4]: `tools/bench_test2_preflight.md` — physical pre-flight boundary.
