# Software-HIL: MapCapture test №2

## Назначение

Этот пакет проверяет **software execution pipeline** no-HV test №2 без Nucleo, COM-порта, ST-Link, STEVAL, DC-link, датчиков или реального логического анализатора. Он использует тот же порядок команд, terminal polling, `evaluate_test()` и генерацию `summary.json`, что и physical backend.

> **Software-HIL PASS не является результатом физического test №2.** Любой run с `--simulate-uart` получает `execution.mode=SIMULATED`, `scope=NOT_APPLICABLE` и `final=SIMULATED`; значение `final=PASS` в simulation невозможно.

## Быстрый запуск

На ПК-1 из корня репозитория:

```powershell
py -3 tools\bench_test2_capture.py `
  --simulate-uart vbus-low-valid `
  --simulate-sigrok
```

Команда не открывает COM, не вызывает `sigrok-cli` и не обращается к USB-устройствам. При успешной симуляции она завершится кодом 0 и создаст каталог вида:

```text
campaign_raw/test2_sim_<UTC>/
  uart.log
  sigrok.csv
  sigrok.stdout
  sigrok.stderr
  metadata.json
  summary.json
```

Проверка всех E2E-сценариев:

```powershell
py -3 -m pytest tests\test_bench_test2_simulation.py -vv -rA
```

Полный software-набор вместе с parser/verdict регрессиями:

```powershell
py -3 -m pytest `
  tests\test_bench_test2_capture.py `
  tests\test_bench_test2_simulation.py -vv -rA
```

## Архитектурная граница

| Уровень | Physical mode | Simulation mode |
|---|---|---|
| UART backend | `SerialTransport` через `--port COMx` | `SimulatedTransport` через `--simulate-uart <scenario>` |
| Capture backend | `RealSigrokCapture` через `sigrok-cli` | `SimulatedSigrokCapture` через `--simulate-sigrok` |
| Алгоритм | Общий `_run_pipeline()` | Тот же общий `_run_pipeline()` |
| Evidence | `test2_nohv_<UTC>` | `test2_sim_<UTC>` |
| Итог | `automation/scope/final` по physical процедуре | `automation=PASS|FAIL`, `scope=NOT_APPLICABLE`, `final=SIMULATED` |

В обоих режимах capture start выполняется до `mapcap run`. Команда `f` не отправляется в simulation; отрицательные сценарии также не могут вызвать очистку fault.

## Сценарии

| Scenario | Имитация | Ожидаемый automation verdict |
|---|---|---|
| `vbus-low-valid` | Строгий SYNT no-HV terminal: `term=-12`, `detail=VBUS_LOW`, raw VBUS 2, 201 mV, нулевые records | `PASS` |
| `i1-limit` | terminal `I1_LIMIT`, 10001 мА | `FAIL` |
| `i2-limit` | terminal `I2_LIMIT`, −10001 мА | `FAIL` |
| `vbus-high` | terminal `VBUS_HIGH`, 70000 мВ | `FAIL` |
| `adc-invalid` | terminal ADC fault с несовместимым status | `FAIL` |
| `timeout` | `RUNNING` остаётся до absolute terminal deadline | `FAIL` |
| `records` | terminal соответствует VBUS-low, но drain содержит `@MC:REC` и 1 record | `FAIL` |
| `arm-fail` | `mcarm` возвращает non-zero rc | `FAIL`; `mapcap run` не посылается |
| `run-fail` | `mapcap run` возвращает non-zero rc | `FAIL`; run не повторяется, polling не начинается |
| `sigrok-fail` | UART path valid, capture возвращает non-zero rc | `FAIL` |
| `missing-csv` | UART path valid, capture не создаёт CSV | `FAIL` |

`missing-csv` намеренно является исключением из полного artifact set: отсутствие `sigrok.csv` — проверяемая причина fail-closed verdict.

## Проверяемые state-machine инварианты

Полный E2E-набор проверяет порядок preflight-команд, `mcarm → capture start → run`, terminal polling, response/evidence в `uart.log` и структуру `summary.json`. Он также закрепляет следующие запреты:

| Условие | Запрещённое действие |
|---|---|
| ARM FAIL | `mapcap run` |
| RUN FAIL | Повторный `mapcap run` и terminal polling |
| Terminal timeout | `mapcap drain` и `f` |
| Любой automation FAIL | `f` и `final=PASS` |
| Любая simulation | `scope=PASS` и `final=PASS` |

## Переход к физической проверке

Software-HIL закрывает transport orchestration и evidence pipeline. Он **не подтверждает** реальный UART timing MCU, PWM, ADC, VBUS, токи или trace анализатора. После его приёмки всё равно требуется штатный no-HV preflight с STEVAL, датчиками и sigrok по `tools/bench_test2_capture.md`.
