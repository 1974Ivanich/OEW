# ТЗ: software-HIL simulation для MapCapture test №2

## Цель

Пакет `ai4/bench-test2-simulation` должен проверить software execution pipeline test №2 без физического оборудования. Его результат доказывает только корректность orchestration, UART/capture evidence pipeline и fail-closed verdict; он **не является** физическим испытанием двигателя или STEVAL.

## Обязательные требования

| Область | Требование |
|---|---|
| Acceptance contract | Не изменять утверждённый контракт `SYNT` из fault-aware automation. |
| UART | Общий интерфейс транспорта с physical `SerialTransport` и deterministic `SimulatedTransport`. |
| Capture | Общий интерфейс capture с physical `RealSigrokCapture` и deterministic `SimulatedSigrokCapture`. |
| Общий путь | Physical и simulation используют одну `_run_pipeline()`, terminal polling, `evaluate_test()` и запись `summary.json`. Дублировать verdict-логику запрещено. |
| Simulation marker | `metadata.json` и `summary.json` содержат `execution.mode=SIMULATED`. |
| Verdict | В simulation `scope=NOT_APPLICABLE`, `final=SIMULATED` вне зависимости от automation verdict; `final=PASS` запрещён. |
| Hardware boundary | В simulation запрещены COM15, `serial.Serial`, ST-Link, STEVAL, DC-link, USB sigrok и `sigrok-cli`. |
| Evidence | Создавать `campaign_raw/test2_sim_<UTC>/uart.log`, sigrok evidence, `metadata.json`, `summary.json`. Исключение: scenario `missing-csv` намеренно подтверждает отсутствие CSV. |
| Fault clear | Simulation не отправляет `f`; любой FAIL не может включать `f` или `final=PASS`. |

## Детерминированные сценарии

Поддержать: `vbus-low-valid`, `i1-limit`, `i2-limit`, `vbus-high`, `adc-invalid`, `timeout`, `records`, `arm-fail`, `run-fail`, `sigrok-fail`, `missing-csv`.

Только `vbus-low-valid` может иметь `automation=PASS`, но его итог всё равно `final=SIMULATED`.

## E2E-регрессии

Тесты должны проверять полный command sequence и artifact pipeline. В частности:

1. `capture:start` происходит до `mapcap run`.
2. `mcarm` precedes `mapcap run`.
3. ARM FAIL запрещает `mapcap run`.
4. RUN FAIL запрещает повторный run и terminal polling.
5. Timeout запрещает drain и fault clear.
6. Отрицательные UART/sigrok scenarios создают `automation=FAIL`.
7. Ни один simulation scenario не получает `final=PASS`.

## Приёмка

Выполнить `py_compile`, parser/verdict tests, E2E simulation tests, `make` и `make test`. `make flash` намеренно не выполнять: пользователь явно запретил доступ к ST-Link и физическому оборудованию для данного пакета. Перед публикацией выполнить `git diff --check`, rebase на свежий `origin/main`, push и `git ls-remote`.
