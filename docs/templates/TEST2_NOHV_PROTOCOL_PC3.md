# Протокол испытаний — ПК‑3, тест № 2 MapCapture no-HV VBUS gate

> **Как использовать:** перед испытанием скопировать этот шаблон в локальную папку `campaign_raw/test2_nohv_YYYYMMDD_HHMM/`, переименовать в `protocol_filled.md` и заполнить по ходу работы. Полный UART-лог, build-log, DMM-фото/показания и trace ЛА/осциллографа являются неотъемлемыми приложениями. Заполненный протокол и артефакты стенда не коммитируются в Git.
>
> Рабочая процедура: [`docs/BENCH_PC3_TEST2_NOHV.md`](../BENCH_PC3_TEST2_NOHV.md).

| Поле | Значение |
|---|---|
| Дата и время начала | `<YYYY-MM-DD HH:MM local>` |
| Дата и время окончания | `<YYYY-MM-DD HH:MM local>` |
| Оператор | `<ФИО/позывной>` |
| Приёмщик / safety-owner | `<ФИО/позывной>` |
| ПК | **ПК‑3** |
| Плата | `<Nucleo серийный № / маркировка>` |
| Модули | `<STEVAL-IPM20B №1 / №2>` |
| Двигатель / стенд | `<идентификатор>` |
| COM-порт | `<COMx>` |
| Ветка | `<например: origin/main>` |
| SHA прошитого commit | `<40-символьный SHA>` |
| SHA проверенного CI artefact | `<SHA/URL run>` |
| Версия инструкции | `docs/BENCH_PC3_TEST2_NOHV.md @ <SHA>` |

## 1. Цель и границы испытания

**Цель:** доказать безопасное terminal shutdown `MapCapture` при PC4=0 и отключённом DC-link после успешных `mcarm`/`mapcap run` в временном synthetic diagnostic образе.

**Не входит в испытание:** Stage A, 60 В, FOC/V/f/autotune, `mapcap build`, запись карты, `MAP_READY`, изменение admission.

| Ограничение | Подтверждение оператора |
|---|---|
| DC-link физически отсоединён от Inv1 и Inv2 | `<Да/Нет; DMM значения ниже>` |
| На PC4 нет имитатора/внешнего источника VBUS | `<Да/Нет>` |
| Aux 3,3 В STEVAL — отдельный источник | `<Да/Нет>` |
| SD1/SD2 не подвергаются намеренному break-тесту | `<Да/Нет>` |
| Логи/trace включены до `mapcap run` | `<Да/Нет>` |

## 2. Артефакты испытания

| Артефакт | Локальный путь / имя файла | Контроль |
|---|---|---|
| Build log | `<campaign_raw/.../build.log>` | Полный вывод `make clean`, `make`, `make flash` |
| UART log | `<campaign_raw/.../uart.log>` | Непрерывный, без ручного удаления строк |
| Trace PWM / LA / scope | `<campaign_raw/.../pwm_shutdown_trace.*>` | Видно отсутствие непрерывного PWM после terminal state |
| Фото/значения DMM | `<путь/файл или значения>` | Обе DC-link шины <1 В |
| Заполненный протокол | `<campaign_raw/.../protocol_filled.md>` | Итоговый PASS/FAIL и подписи |

## 3. Физический no-HV preflight

| № | Проверка | Факт | PASS / FAIL | Комментарий / доказательство |
|---:|---|---|---|---|
| 1 | DC-link Inv1, DMM | `<… V>` | `<PASS/FAIL>` | `<…>` |
| 2 | DC-link Inv2, DMM | `<… V>` | `<PASS/FAIL>` | `<…>` |
| 3 | PC4 без внешнего VBUS source | `<Да/Нет>` | `<PASS/FAIL>` | `<…>` |
| 4 | Aux 3,3 В обоих STEVAL от отдельного источника | `<Да/Нет>` | `<PASS/FAIL>` | `<…>` |
| 5 | SD1 high до старта | `<Да/Нет/способ проверки>` | `<PASS/FAIL>` | `<…>` |
| 6 | SD2 high до старта | `<Да/Нет/способ проверки>` | `<PASS/FAIL>` | `<…>` |
| 7 | Вал/механика неподвижны; управление не запланировано | `<Да/Нет>` | `<PASS/FAIL>` | `<…>` |
| 8 | UART 115200 8N1 и запись включена | `<Да/Нет>` | `<PASS/FAIL>` | `<…>` |
| 9 | ЛА/осциллограф armed | `<Да/Нет>` | `<PASS/FAIL>` | `<каналы/частота>` |

**Решение preflight:** `<GO к прошивке / NO-GO>`
**Причина NO-GO (если применимо):** `<…>`

## 4. Сборка и прошивка временного diagnostic образа

Команды, которые должны быть зафиксированы в build-log:

```bash
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1"
make flash
```

| Контроль | Фактический результат | PASS / FAIL | Ссылка на evidence |
|---|---|---|---|
| `make clean` | `<…>` | `<PASS/FAIL>` | `<build.log: lines …>` |
| Diagnostic `make` | `<Build complete!/ошибка>` | `<PASS/FAIL>` | `<build.log: lines …>` |
| Размер/выход сборки | `<…>` | `<INFO>` | `<build.log: lines …>` |
| `make flash` | `<Download verified successfully; MCU Reset/ошибка>` | `<PASS/FAIL>` | `<build.log: lines …>` |
| SHA соответствует указанному выше | `<Да/Нет>` | `<PASS/FAIL>` | `<команда/вывод>` |

**Решение после прошивки:** `<GO к UART preflight / NO-GO>`

## 5. UART preflight

| Шаг | Команда | Полный/ключевой ответ | Ожидание | PASS / FAIL | Примечание |
|---:|---|---|---|---|---|
| 1 | `sysinfo` | `<…>` | Свежий ответ диагностического образа | `<PASS/FAIL>` | `<…>` |
| 2 | `p?` | `<…>` | PWM off, MOE=0 | `<PASS/FAIL>` | `<…>` |
| 3 | `pdump` | `<…>` | PWM off / registers captured | `<PASS/FAIL>` | `<…>` |
| 4 | `a` | `I1=<…>; I2=<…>; Ires=<…>; VBUSraw=<…>` | I1/I2 не saturated; VBUS у нижней границы | `<PASS/FAIL>` | `<…>` |
| 5 | `c` | `<…>` | SUCCESS; offsets valid | `<PASS/FAIL>` | `I1=<…>; I2=<…>; Ires=<…>` |
| 6 | `enc` | `<…>` | `err=0` | `<PASS/FAIL>` | `period=<…>` |
| 7 | `mapcap status` | `<…>` | `state=0`, `frames=0`, `avail=0` | `<PASS/FAIL>` | `<…>` |

**Preflight UART вердикт:** `<GO / NO-GO>`

## 6. Выполнение MapCapture test № 2

### 6.1 Arm

| Команда | Ответ | Ожидание | PASS / FAIL | Capture ID |
|---|---|---|---|---|
| `mcarm=1398361684` | `<…>` | `@MC:ARM:cap=…:rc=0` | `<PASS/FAIL>` | `<…>` |
| `mapcap status` | `<…>` | `state=1`, `term=0`, `frames=0`, `avail=0` | `<PASS/FAIL>` | `<…>` |

При `BLOCKED:PROFILE` или любом `rc<0` **не выполнять** `mapcap run`: отметить FAIL и перейти к разделу 9.

### 6.2 Run и terminal state

| Шаг | Команда / действие | Ответ / наблюдение | Ожидание | PASS / FAIL |
|---:|---|---|---|---|
| 1 | ЛА/осциллограф уже пишет | `<…>` | Trace активен | `<PASS/FAIL>` |
| 2 | `mapcap run` | `<…>` | `@MC:RUN:rc=0` | `<PASS/FAIL>` |
| 3 | Подождать ≥1 с, не посылая команд управления | `<…>` | Service path завершился | `<PASS/FAIL>` |
| 4 | `mapcap status` | `<полная строка>` | `state=5`; `frames=0`; `dropped=0`; `avail=0`; `term=-11` или `-12` | `<PASS/FAIL>` |
| 5 | `mapcap drain` | `<полная строка>` | Нет `@MC:REC`; `@MC:DRAIN:records=0` | `<PASS/FAIL>` |
| 6 | `p?` | `<…>` | PWM off / MOE=0 | `<PASS/FAIL>` |
| 7 | `pdump` | `<…>` | PWM off / registers captured | `<PASS/FAIL>` |
| 8 | Проверить trace | `<имя файла/наблюдение>` | Нет непрерывного PWM после terminal state | `<PASS/FAIL>` |

### 6.3 Классификация terminal status

| Raw VBUS | `term` | Допустимость | Фактический результат |
|---:|---:|---|---|
| 0–1 | `-11` (`MAP_CAPTURE_ADC_FAULT`) | PASS | `raw=<…>; term=<…>` |
| ≥2 и расчётный VBUS <1000 мВ | `-12` (`MAP_CAPTURE_LIMIT_EXCEEDED`) | PASS | `raw=<…>; term=<…>` |
| Любой | `0`, `records>0`, `state=COMPLETE` | **FAIL** | `<…>` |
| Любой | иной отрицательный `term` | **FAIL** | `<…>` |

## 7. Явная очистка fault и завершение

| Шаг | Действие | Факт | Ожидание | PASS / FAIL |
|---:|---|---|---|---|
| 1 | Сохранить UART/build/trace evidence | `<пути>` | Все артефакты существуют | `<PASS/FAIL>` |
| 2 | Ещё раз проверить SD1/SD2 high | `<…>` | Обе high | `<PASS/FAIL>` |
| 3 | `f` | `<…>` | Явная очистка fault при healthy SD | `<PASS/FAIL>` |
| 4 | `p?` после `f` | `<…>` | PWM остаётся off; `f` не re-arm/re-start outputs | `<PASS/FAIL>` |
| 5 | `pdump` после `f` | `<…>` | MOE=0 / safe baseline | `<PASS/FAIL>` |

## 8. Возврат платы в generic default-deny

Команды после фиксации результата:

```bash
make clean
make
make flash
```

| Контроль | Факт | PASS / FAIL | Evidence |
|---|---|---|---|
| Generic build | `<…>` | `<PASS/FAIL>` | `<build.log / отдельный restore log>` |
| Generic flash | `<…>` | `<PASS/FAIL>` | `<…>` |
| `p?` после reset | `<…>` | `<PASS/FAIL>` | PWM off |
| DC-link после завершения | `Inv1=<… V>; Inv2=<… V>` | `<PASS/FAIL>` | `<…>` |

## 9. Отклонения и блокеры

| Наблюдение | Произошло? | Действие оператора | Артефакты для разбора |
|---|---|---|---|
| `BLOCKED:PROFILE` / `mcarm rc<0` | `<Да/Нет>` | Не выполнять `run`; NO-GO | UART, build log, SHA |
| Калибровка неуспешна | `<Да/Нет>` | STOP; не выполнять arm | `a`, `c`, `dumpa` |
| Timeout / trigger mismatch / interlock / иной term | `<Да/Нет>` | STOP; Stage A blocked | UART, trace, `pdump` |
| `records>0` или `state=COMPLETE` при PC4=0 | `<Да/Нет>` | STOP; критический VBUS-gate FAIL | Полный UART, trace, DMM |
| PWM активен после terminal state | `<Да/Нет>` | STOP; применить локальную безопасную процедуру снятия aux при необходимости | Непрерывный trace, UART, регистры |
| `f` не очищает fault при SD high | `<Да/Нет>` | STOP; Stage A blocked | UART, SD evidence |

## 10. Итог test № 2

| Критерий | Результат |
|---|---|
| No-HV / PC4=0 соблюдены | `<PASS/FAIL>` |
| Arm `rc=0` | `<PASS/FAIL>` |
| Run `rc=0` | `<PASS/FAIL>` |
| Terminal `-11` или `-12` | `<PASS/FAIL>` |
| `records=0` и отсутствие `@MC:REC` | `<PASS/FAIL>` |
| PWM выключен после terminal state | `<PASS/FAIL>` |
| Explicit clear не включает PWM | `<PASS/FAIL>` |
| Generic default-deny восстановлен | `<PASS/FAIL>` |
| **Общий вердикт test № 2** | **`<PASS / FAIL>`** |

**Обоснование вердикта:**
`<…>`

| Подпись / роль | Имя | Дата | Подпись/подтверждение |
|---|---|---|---|
| Оператор ПК‑3 | `<…>` | `<…>` | `<…>` |
| Приёмщик / safety-owner | `<…>` | `<…>` | `<…>` |

## 11. Stage A 60 В — решение только после test № 2

> PASS test № 2 сам по себе **не** разрешает 60 В. До первого Stage-A capture burst должны быть документированы все пункты ниже. Отсутствие хотя бы одного пункта означает **NO-GO**.

| Гейт Stage A | PASS / FAIL / N/A | Ссылка на evidence / решение |
|---|---|---|
| Test № 2 PASS по разделу 10 | `<…>` | `<…>` |
| Актуальные SD T1–T4 PASS | `<…>` | `<…>` |
| Согласованный SHA, `git diff --check`, CI: `make test` + commissioning build green | `<…>` | `<…>` |
| Назначен оператор, приёмщик и аварийный порядок отключения питания | `<…>` | `<…>` |
| Источник 60 В и токовый лимит утверждены ответственным за данный стенд | `<…>` | `<значение лимита утверждает ответственный>` |
| Утверждён путь к реальному Stage-A request: acquisition profile **или** отдельная scope-процедура | `<…>` | `<номер ТЗ / SHA / решение>` |
| Synthetic `SYNT` и `OEW_HOST_TEST` не используются на 60 В | `<…>` | `<build evidence>` |
| Final Stage-A preflight: offsets valid, SD high, fault cleared, ADC/VBUS живы, PWM off | `<…>` | `<…>` |
| Нет ручного обхода admission | `<…>` | `<…>` |
| **Решение Stage A** | **`<GO / NO-GO>`** | `<приёмщик + дата>` |

**Комментарии/ограничения Stage A:**
`<…>`

---

**Ссылки:** [рабочая инструкция](../BENCH_PC3_TEST2_NOHV.md), [SD checklist](../OEW_SD_CHECKLIST.md), [fail-closed test № 1](../BENCH_MAPCAP_FAILCLOSED_CHECKLIST.md), [план Stage A/L3](../../TZ_L3_MAP_QUALIFICATION_DRAFT.md).
