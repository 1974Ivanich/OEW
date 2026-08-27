# Минимальный отчёт — Test №2 ADC/no-HV

> Заполняется по фактическим наблюдениям. Не подставляйте synthetic или предположительные значения. Полный UART-лог хранится рядом с этим отчётом и не редактируется.

## Идентификация

| Поле | Значение |
|---|---|
| Campaign directory | |
| Дата и время начала (UTC) | |
| Оператор | |
| Source SHA | |
| Board / MCU identity | |
| UART VCP / COM | |
| Firmware build | `default-deny main` |
| Flash verify result | |
| Полный UART-лог | `uart.log` |

## Безопасная граница

| Проверка | Фактическое значение | Результат |
|---|---|---|
| DC-link rail 1, DMM | V | PASS / FAIL |
| DC-link rail 2, DMM | V | PASS / FAIL |
| Внешний VBUS/PC4 source | отсутствует / обнаружен | PASS / FAIL |
| Aux supply | V, источник | PASS / FAIL |
| Harness | полный / неполный | PASS / FAIL |
| Emergency action | подтверждена / не подтверждена | PASS / FAIL |
| Forbidden commands absent | да / нет | PASS / FAIL |

## UART identity and control state

| Команда | Краткий фактический результат | Результат |
|---|---|---|
| `sysinfo` | | PASS / FAIL |
| `p?` | PWM disabled/default-deny, MOE state: | PASS / FAIL |
| `pdump` | | PASS / FAIL |
| `c` | offset_i1: ; offset_i2: ; offset_ires: | PASS / FAIL |

## Десять ADC-наблюдений

| № | Время UTC | I1 raw | I2 raw | Ires raw | VBUS raw | Ответ полный | Примечание |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | | | | | | да / нет | |
| 2 | | | | | | да / нет | |
| 3 | | | | | | да / нет | |
| 4 | | | | | | да / нет | |
| 5 | | | | | | да / нет | |
| 6 | | | | | | да / нет | |
| 7 | | | | | | да / нет | |
| 8 | | | | | | да / нет | |
| 9 | | | | | | да / нет | |
| 10 | | | | | | да / нет | |

## Итог

| Область | Итог |
|---|---|
| Firmware identity | PASS / FAIL / BLOCKED |
| PWM default-deny | PASS / FAIL / BLOCKED |
| Calibration | PASS / FAIL / BLOCKED |
| ADC completeness | PASS / FAIL / BLOCKED |
| No-HV VBUS | PASS / FAIL / BLOCKED |
| Current baseline | PASS / FAIL / BLOCKED |
| Command boundary | PASS / FAIL / BLOCKED |

**Итог Test №2:** `PASS` / `FAIL` / `BLOCKED`

**Причина итогового решения:**


**Подпись/инициалы оператора:**  
**Дата/время закрытия (UTC):**  

## Ограничение результата

Этот отчёт подтверждает только ADC/measurement-chain baseline при физически отключённом DC-link. Он не является разрешением на Test №3, MapCapture, RealBoardProfile, automatic characterization, DC-link, Stage A, FOC, V/f или autotune.
