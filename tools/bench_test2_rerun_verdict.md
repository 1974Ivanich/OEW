# `bench_test2_rerun_verdict.py` — offline verdict повторного no-HV прогона

## Назначение

Скрипт сверяет **два уже сохранённых** артефакта одного controlled no-HV прогона:

```text
<campaign>/summary.json
<campaign>/uart.log
```

Он читает их offline, создаёт `<campaign>/rerun_terminal_verdict.json` и не открывает COM, ST-Link, sigrok, USB или DC-link. Скрипт не прошивает MCU, не посылает UART-команд и не изменяет `summary.json`/`uart.log`.

> `TERMINAL_VERDICT=PASS` означает только, что physical summary и непрерывный UART лог согласованно соответствуют expected terminal no-HV contract. Он **не** является Test №2 PASS, Test №3 PASS, ручной sigrok scope acceptance или разрешением на Stage A/DC-link 60 V.

## Запуск

Запускайте после завершения кампании, сохраняя исходные artifacts неизменными:

```powershell
py -3 tools\bench_test2_rerun_verdict.py `
  --campaign D:\campaign_raw\test3_nohv_<UTC>
```

| Exit code | Console result | Meaning |
|---:|---|---|
| `0` | `TERMINAL_VERDICT=PASS stage_a_60v=BLOCKED` | Все machine-checkable terminal evidence checks согласованы. |
| `2` | `TERMINAL_VERDICT=FAIL stage_a_60v=BLOCKED` | Отсутствует/повреждён evidence, найден simulation marker или хотя бы один no-HV check не подтверждён. |

## Что проверяется

| Evidence layer | Required condition |
|---|---|
| Summary provenance | `execution.mode=PHYSICAL`, `automation=PASS`, допустимая физическая пара `scope/final`. |
| Existing evaluator | Все original no-HV checks сохранены и равны `true`; исключений/частичных PASS нет. |
| Summary terminal | `state=5`, `term=-12`, `detail=7`, `adc_status=7`, no-HV VBUS, допустимые currents и нулевые frames/dropped/avail. |
| Sigrok declaration | Summary содержит successful capture return code и CSV existence. Это не заменяет manual CSV review. |
| UART sequence | Accepted `@MC:ARM` (`cap>0`, `rc=0`) предшествует accepted `@MC:RUN:rc=0`. |
| UART terminal | Последняя extended `@MC:STATUS` равна terminal status в summary; последняя drain строка имеет `records=0`, `@MC:REC:` отсутствует. |
| Physical provenance | В `uart.log` отсутствуют `mode=SIMULATED` и `@SIM:` markers. |

Все проверки fail-closed. Например, исторический `term=-11` / `ADC_SATURATED`, `term=-12` с иной detail, timeout, `records>0`, incomplete status, UART/summary mismatch, missing RUN или synthetic evidence дают FAIL.

## Результат

`rerun_terminal_verdict.json` содержит SHA-256 обоих входов, полный список checks, status projections, UTC time проверки и фиксированное поле:

```json
{
  "terminal_verdict": "PASS",
  "stage_a_60v": "BLOCKED"
}
```

Перед любой дальнейшей физической работой сохраните evidence, проведите ручной review sigrok CSV и используйте подходящий campaign archive workflow. Требования отдельного перехода к 60 V описаны в `docs/TEST3_NOHV_TO_STAGE_A_60V_CRITERIA.md`.

## References

[1]: `TZ_BENCH_TEST2_RERUN_VERDICT.md` — formal parser contract.

[2]: `tools/bench_test2_capture.md` — existing strict no-HV UART/evidence contract.

[3]: `docs/TEST2_PC3_FRESH_MAIN_CHECKLIST.md` — ПК-3 procedure and path separation.
