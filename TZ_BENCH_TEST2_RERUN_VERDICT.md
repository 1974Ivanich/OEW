# ТЗ: Offline verdict повторного no-HV прогона

## 1. Назначение и граница

`tools/bench_test2_rerun_verdict.py` — **offline-only** валидатор evidence bundle повторного controlled no-HV прогона. Скрипт не открывает COM-порт, не обращается к ST-Link, sigrok, USB, STEVAL или DC-link, не прошивает MCU и не запускает firmware-команд.

> `TERMINAL_VERDICT=PASS` доказывает только, что сохранённый, human-attested physical evidence bundle внутренне согласован с ожидаемым no-HV terminal contract. Он **не является** Test №2 PASS, Test №3 PASS, допуском к Stage A/DC-link 60 V или заменой ручной sigrok scope-приёмки, G0, pre-flight и approval safety-owner.

Исторические machine-readable имена `bench_test2_*` и `MAPCAP_TEST2` сохраняются ради совместимости. По утверждённой нумерации ADC baseline — Test №2, controlled no-HV MapCapture/G0 — Test №3.

## 2. Входы и выход

CLI принимает один каталог кампании вне Git working tree. Все четыре input files являются обязательными.

```text
<campaign>/
├── summary.json
├── uart.log
├── metadata.json
├── sigrok_digital.csv
└── physical_run_attestation.json
```

`summary.json`, `uart.log`, `metadata.json` и CSV создаются existing automation/evidence workflow. `physical_run_attestation.json` оформляется **после** run именованным bench-operator: он фиксирует physical observation statement и SHA-256 exact summary/UART/metadata/CSV artifacts. Скрипт проверяет schema, required fields, paths и hashes этого attestation, но не выполняет криптографическую проверку личности оператора или устройства.

Валидатор записывает только `<campaign>/rerun_terminal_verdict.json`. Исходные evidence files не изменяются. Любая пустота, повреждённый JSON, hash mismatch, path outside campaign, synthetic marker, incomplete evidence или противоречие даёт `TERMINAL_VERDICT=FAIL`, exit code `2`.

## 3. Строгий terminal no-HV contract

Все условия ниже являются независимыми AND-gates.

| ID | Проверка | Требование |
|---|---|---|
| RV-00 | Input completeness | Parseable summary/metadata/attestation и непустой UART log. |
| RV-01 | Physical identity | `summary.execution.mode=PHYSICAL`, `metadata.execution.mode=PHYSICAL`, `metadata.profile_id=1398361684`. |
| RV-02/03 | Existing output consistency | `automation=PASS`; physical `scope/final` — только `PENDING/PENDING` или `PASS/PASS`. |
| RV-04 | Existing evaluator claims | Все required existing no-HV checks присутствуют и `true`; это сохранённая декларация production evaluator, не единственное доказательство. |
| RV-04b | Independent ADC recomputation | Все raw `@ADC` preflight lines из UART: count равен `metadata.arguments.vbus_samples`; lower median VBUS `<=9`, maximum `<=200`, I1/I2 не saturated. |
| RV-04c | Summary/UART preflight match | Полный ordered I1/I2/Ires/VBUS sample sequence из summary идентичен raw UART lines. |
| RV-05 | Summary terminal | Полный extended STATUS удовлетворяет expected no-HV values. |
| RV-06 | Sigrok artifact | Successful capture declaration, non-empty CSV внутри campaign и exact size binding с summary. Scope waveform review остаётся ручным. |
| RV-06b | Physical attestation | Required operator/name/time/statement present; all four evidence hashes и CSV path match current files exactly. |
| RV-07 | ARM → RUN | Accepted `@MC:ARM` (`cap>0`, `rc=0`) расположен до accepted `@MC:RUN:rc=0`. |
| RV-08 | Terminal status history | В непрерывном UART log присутствует **ровно один** terminal STATUS (`state` in 3/4/5); он соответствует no-HV contract. Ранний terminal/fault не может быть скрыт поздней строкой. |
| RV-08b | Complete identity match | Summary и UART terminal STATUS совпадают по всем parsed fields: `state`, `term`, `cap`, `frames`, `dropped`, `periods`, `avail`, `detail`, `raw_vbus`, `vbus_mv`, `i1_ma`, `i2_ma`, `adc_status`, `sector`, `window`. |
| RV-09 | Drain | Последний drain имеет `records=0`; `@MC:REC:` отсутствует. |
| RV-10 | Negative simulation indicator | В UART нет `mode=SIMULATED` или `@SIM:`. Это лишь supplementary check; positive physical provenance требует RV-01/RV-06/RV-06b. |

Expected terminal values: `state=5`, `term=-12`, `detail=7 (VBUS_LOW)`, `adc_status=7 (WINDOW_INVALID)`, `raw_vbus<=9`, `0<=vbus_mv<1000`, `|i1_ma|,|i2_ma|<=10000`, `frames=dropped=avail=0`. Они соответствуют immutable no-HV automation contract.[1]

Исторический `term=-11`/`ADC_SATURATED`, `I1_LIMIT`, `I2_LIMIT`, `VBUS_HIGH`, timeout, records, incomplete fields или последовательность `bad terminal STATUS → good terminal STATUS` дают FAIL.

## 4. Машиночитаемый результат

`rerun_terminal_verdict.json` содержит schema version, SHA-256 всех input artifacts, полный список checks (`id`, `result`, `expected`, `actual`, `detail`), полный terminal STATUS, terminal count и фиксированное поле `stage_a_60v=BLOCKED`.

CLI печатает одну строку `TERMINAL_VERDICT=PASS|FAIL stage_a_60v=BLOCKED` и возвращает `0` только при PASS; иначе `2`.

## 5. Граница доказательства

Проверка hashes/attestation повышает chain of custody и запрещает text-only synthetic pair без заявленного physical observation. Она не может, и не утверждает, что автоматически доказывает подлинность человека, hardware или waveform. Для этого необходимы G0/pre-flight, original campaign procedure, manual sigrok scope acceptance, archive/receipt workflow и designated human review.

## 6. Регрессия

Deterministic coverage включает: полный attested evidence PASS; unattested synthetic pair FAIL; historical `term=-11` FAIL; earlier bad terminal then good terminal FAIL; full STATUS identity mismatch FAIL; simulation FAIL; missing evaluator check FAIL; summary/UART mismatch FAIL; missing RUN FAIL; independently recomputed bad preflight FAIL; malformed summary FAIL.

## 7. References

[1]: `tools/bench_test2_capture.md` — strict no-HV UART and evidence contract.

[2]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md` — approved Test №2 ADC-baseline boundary.

[3]: `tools/bench_test2_preflight.md` — pre-flight and physical-GO boundary.
