# ТЗ: Offline verdict повторного no-HV прогона

## 1. Назначение и граница

`tools/bench_test2_rerun_verdict.py` является **offline-only** валидатором согласованности `summary.json` и непрерывного `uart.log` из повторного controlled no-HV прогона. Скрипт не открывает COM-порт, не обращается к ST-Link, sigrok, USB, STEVAL или DC-link, не прошивает MCU и не запускает команд firmware.

> `TERMINAL_VERDICT=PASS` доказывает только то, что сохранённые summary/UART evidence согласованно содержат ожидаемый terminal no-HV результат. Он **не является** Test №2 PASS, Test №3 PASS, допуском к Stage A с 60 V либо заменой ручной scope-приёмки, G0, pre-flight и approval safety-owner.

Исторические machine-readable имена `bench_test2_*` и `MAPCAP_TEST2` сохраняются ради совместимости. По утверждённой нумерации ADC baseline — это Test №2, а controlled no-HV MapCapture/G0 относится к Test №3.

## 2. Входы и выход

CLI принимает каталог кампании вне Git working tree:

```text
<campaign>/
├── summary.json
└── uart.log
```

Он записывает только новый файл `<campaign>/rerun_terminal_verdict.json`. Пользовательские входные артефакты не изменяются.

Успешное выполнение требует физического (`execution.mode=PHYSICAL`) summary. Software-HIL/`SIMULATED`, malformed JSON, отсутствующий лог, пустой лог, неполное evidence или противоречие summary/UART дают fail-closed `TERMINAL_VERDICT=FAIL`, exit code `2`.

## 3. Строгий terminal no-HV contract

Все перечисленные условия должны быть одновременно истинны.

| ID | Проверка | Ожидаемое значение |
|---|---|---|
| RV-01 | Execution mode | `PHYSICAL` |
| RV-02 | Automation result | `verdict.automation=PASS` |
| RV-03 | Scope/final consistency | `scope/final` — допустимая физическая пара `PENDING/PENDING` либо `PASS/PASS`; инструмент не оценивает scope самостоятельно |
| RV-04 | Existing evaluator checks | Все требуемые no-HV checks в `verdict.checks` существуют и равны `true` |
| RV-05 | Summary terminal status | Расширенный `@MC:STATUS` содержит `state=5`, `term=-12`, `detail=7`, `adc_status=7`, `raw_vbus<=9`, `0<=vbus_mv<1000`, `frames=dropped=avail=0`, а токи не превышают 10 000 mA |
| RV-06 | Sigrok declaration | `sigrok_capture_returncode_zero=true` и `sigrok_csv_exists=true` в summary |
| RV-07 | UART ARM/RUN | В непрерывном `uart.log` есть принятые `@MC:ARM` (`cap>0`, `rc=0`) и `@MC:RUN:rc=0`, причём ARM расположен раньше RUN |
| RV-08 | UART terminal evidence | Последний расширенный `@MC:STATUS` в `uart.log` отвечает тем же terminal fields, что summary |
| RV-09 | UART drain/records | Последний `@MC:DRAIN:records=0`; в логе нет `@MC:REC:` |
| RV-10 | Physical provenance | В `uart.log` нет `mode=SIMULATED` или `@SIM:` marker |

Параметры `state=5`, `term=-12`, `detail=7`, `adc_status=7`, raw VBUS, VBUS voltage, zero-record и shunt limits соответствуют существующему immutable no-HV automation contract `tools/bench_test2_capture.py`.[1]

## 4. Машиночитаемый результат

`rerun_terminal_verdict.json` содержит `schema_version`, входные пути и SHA-256, полный список checks (`id`, `result`, `expected`, `actual`, `detail`), найденные terminal fields, итог `terminal_verdict` и жёсткую строку `stage_a_60v=BLOCKED`.

CLI печатает одну строку `TERMINAL_VERDICT=PASS|FAIL stage_a_60v=BLOCKED` и возвращает `0` только для PASS; иначе возвращает `2`.

## 5. Регрессия

Нужны deterministic tests как минимум для: complete physical PASS; legacy incorrect `term=-11`/`ADC_SATURATED`; simulated summary; missing required evaluator check; UART/summary terminal mismatch; missing accepted RUN evidence; malformed input.

## 6. References

[1]: `tools/bench_test2_capture.md` — strict no-HV UART and evidence contract.

[2]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md` — approved Test №2 ADC-baseline scope and explicit boundary from Test №3.

[3]: `tools/bench_test2_preflight.md` — pre-flight and physical-GO boundary.
