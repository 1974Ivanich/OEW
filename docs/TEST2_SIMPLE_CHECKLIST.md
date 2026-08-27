# Упрощённый checklist — Test №2 ADC/no-HV

**Назначение:** минимальная физическая проверка измерительной цепи и ADC на полном стенде без DC-link с возможностью перейти к отдельному controlled no-HV Test №3 без дополнительного G0-файла и письменного approval. Этот checklist не разрешает DC-link, FOC, V/f, autotune или Stage A.

> Если любой обязательный пункт имеет статус `FAIL`, `UNKNOWN` или не может быть подтверждён, испытание не начинать или немедленно остановить.
>
> **Статус упрощения:** этот документ предлагает сокращённый операторский маршрут и не переписывает автоматически authoritative Test №3 validator/transition contract. Если проектная приёмка требует отдельного G0 или approval, их отсутствие блокирует официальный Test №3 независимо от этого checklist.

## 1. До включения питания

| Проверка | Требование | Отметка |
|---|---|---|
| DC-link | Обе силовые шины физически отключены; каждая измерена DMM как `<1 V` | [ ] PASS |
| VBUS/PC4 | На PC4 нет внешнего источника или имитатора VBUS | [ ] PASS |
| Aux supply | Подключено только отдельное разрешённое низковольтное aux-питание; DC-link не используется | [ ] PASS |
| Harness | Подключён полный согласованный harness датчиков и STEVAL paths | [ ] PASS |
| Emergency action | Оператор может снять aux-питание по локальной процедуре | [ ] PASS |
| Scope | Не будут выполняться `mcarm`, `mapcap run`, `mapcap build`, `f`, FOC, V/f или autotune | [ ] PASS |

**Если все шесть пунктов не `PASS`, прошивку и тест не начинать.**

## 2. Прошивка и UART

Использовать только default-deny сборку `main`, без Test №3 diagnostic flags. После flash выполнить verify для фактической платы и открыть фактический MCU VCP.

```text
sysinfo
p?
pdump
c
a
a
a
a
a
a
a
a
a
a
```

Сохранить **полный UART-лог**, включая boot/reset output и все отправленные команды. COM-порт, board identity, source SHA и результат flash verify записать в минимальный отчёт.

## 3. Критерии PASS

| Область | PASS-критерий | Отметка |
|---|---|---|
| Firmware identity | `sysinfo` подтверждает ожидаемую default-deny прошивку на нужном MCU | [ ] PASS |
| PWM | `p?` и `pdump` показывают disabled/default-deny; активность MOE отсутствует или однозначно запрещена | [ ] PASS |
| Calibration | `c` возвращает `offset_i1`, `offset_i2`, `offset_ires` без ошибки | [ ] PASS |
| ADC completeness | Все 10 ответов `a` содержат I1, I2, Ires и VBUS | [ ] PASS |
| No-HV VBUS | Обе шины `<1 V`, внешнего VBUS нет, raw VBUS соответствует baseline `≤9` | [ ] PASS |
| Current baseline | Нет ADC rail/saturation, timeout, пропусков или необъяснимых скачков | [ ] PASS |
| Command boundary | Выполнены только `sysinfo`, `p?`, `pdump`, `c` и 10 команд `a` | [ ] PASS |

## 4. Минимальные записи

Новая папка кампании создаётся вне Git working tree. Достаточно сохранить:

```text
<campaign>/uart.log
<campaign>/report.md
```

В `report.md` укажите дату/время UTC, оператора, board/MCU identity, firmware/build label, результат flash verify, два DMM-показания DC-link, aux voltage, 10 строк ADC-наблюдений и итог `PASS`/`FAIL`/`BLOCKED`. Полный UART-лог не редактировать и не сокращать.

## 5. Стоп-критерии

Немедленно остановиться и сохранить evidence при активном или неоднозначном PWM, неожиданном reset/fault, неверной board/image identity, VBUS выше no-HV baseline, неполном UART, rail/saturation-коде, потере связи или любом сомнении оператора. Не очищать fault и не повторять запуск до разбора события.

## 6. Упрощённый переход к Test №3

После `Test №2 PASS` можно перейти к отдельному controlled no-HV Test №3 без отдельного G0-файла и письменного approval safety-owner, если оператор перед началом ещё раз подтверждает три условия: DC-link физически отключён и обе шины `<1 V`; PWM остаётся disabled/default-deny; используется проверенная на Test №2 плата и прошивка. Достаточно отметить эти три пункта в отчёте Test №2 и использовать отдельную папку логов Test №3.

Переход относится только к no-HV MapCapture. Нельзя подавать DC-link, включать FOC/V/f/autotune, запускать Stage A или использовать результат Test №2 как разрешение на работу с силовой шиной. При любом неоднозначном PWM, unexpected reset/fault, VBUS выше no-HV baseline, потере UART или сомнении оператора — немедленный STOP.

## 7. Граница результата

`Test №2 PASS` подтверждает физический ADC/measurement-chain baseline и является практическим prerequisite для controlled no-HV Test №3. Он не разрешает DC-link, Stage A, FOC, V/f или autotune.
