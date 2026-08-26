# ТЗ: упрощённый маршрут Test №2 → Test №3 → измеренная карта → ограниченный запуск

## 1. Цель

Зафиксировать короткий, проверяемый и fail-closed маршрут от уже собранного evidence Test №2 до физически квалифицированной `OewCurrentMap`. Маршрут не создаёт повторных физических тестов ради дублирующих программных проверок и не изменяет firmware, safety-модули, MapCapture UART-контракт или `.ioc`.

## 2. Физические ступени и их границы

| Ступень | Результат | Разрешённый объём | Что результат не доказывает |
|---|---|---|---|
| **Test №2** | `TEST2_BASELINE_ACCEPTED` | Default-deny ADC baseline: identity, обе шины DC-link `<1 V`, PWM OFF/MOE=0, calibration, I1/I2/Ires/VBUS, UART integrity. | Test №3, MapCapture, карту, Stage A или запуск двигателя. |
| **Test №3** | `TEST3_NOHV_COMMISSIONING_READY` | Одна controlled no-HV кампания: approved G0, pre-flight, одна scope/timing qualification, reviewed plan всех 12 contexts и MapCapture readiness. | Измеренную карту: no-HV terminal contract требует `VBUS_LOW` и zero records. |
| **Characterization** | `REAL_BOARD_CAPTURE_VALID` | Отдельная physical campaign с внешним двухфазным current reference, raw-first dataset, 12 sector/window contexts, solver/certifier. | Загрузку карты и запуск двигателя без отдельного Stage A допуска. |
| **Ограниченный запуск** | Отдельное решение safety owner | Только после принятой карты, map-load admission и отдельного Stage A approval. | Любой следующий режим/мощность. |

> Нельзя заменять physical reference dataset одним no-HV MapCapture: действующий no-HV contract требует `term=-12`, `detail=VBUS_LOW`, `adc_status=WINDOW_INVALID` и zero records. Поэтому `REAL_BOARD_CAPTURE_VALID` формируется только после characterization, а не Test №3.

## 3. Упрощение критического пути

Маршрут не вводит отдельные физические profile/sector/window tests, повторный software-HIL или повторные host-side map regressions. Они остаются частью одной Test №3 commissioning campaign, существующего CI или автоматической dataset/map certification.

Повтор Test №2 не требуется, если уже существующий campaign bundle прошёл review и его retained files криптографически связаны с `test2_baseline_approval.json`.

## 4. Offline route checker

`tools/test2_test3_route_check.py` не открывает COM/ST-Link/sigrok и не запускает firmware. Он читает только локальные файлы Test №2/Test №3 campaigns. Результаты намеренно разделены:

```text
ROUTE_PLAN_VALID
TEST2_BASELINE_ACCEPTED
TEST3_G0_EVIDENCE
TEST3_NOHV_COMMISSIONING_READY
PHYSICAL_TEST3_EXECUTED
```

| Статус | Условие `PASS` | Безусловная граница |
|---|---|---|
| `ROUTE_PLAN_VALID` | Test №2 evidence-bound approval и Test №3 12-context no-HV plan корректны. | Не означает G0 или физическое выполнение. |
| `TEST2_BASELINE_ACCEPTED` | Все retained Test №2 files существуют, безопасно расположены и их фактические SHA-256 совпадают с approval. | Не разрешает Test №3. |
| `TEST3_G0_EVIDENCE` | Локальный G0 `APPROVED`, scope, source SHA и retained firmware SHA-256 точны. | Не доказывает состояние физической платы. |
| `TEST3_NOHV_COMMISSIONING_READY` | План valid и `TEST3_G0_EVIDENCE=PASS`. | Не является physical Test №3 PASS. |
| `PHYSICAL_TEST3_EXECUTED` | Никогда не выдаётся этим инструментом. | Всегда `BLOCKED`. |

Parser никогда не выдаёт `PHYSICAL_TEST3_EXECUTED=PASS`, `REAL_BOARD_CAPTURE_VALID=PASS`, `STAGE_A_60V=PASS` или разрешение на запуск.

## 5. Test №2 evidence binding

`test2_baseline_approval.json` обязан содержать: schema, `test_id=TEST2`, `decision=PASS`, campaign id и существующий root path, `source_sha`, относительный `source_identity_path`, reviewer/time/statement и по каждому required evidence file:

```text
uart_log
adc_samples
summary
build_log
```

Для каждого файла approval содержит **относительный** путь и declared SHA-256. Checker обязан:

1. запретить отсутствующий root, absolute path, path traversal и symbolic link;
2. открыть только regular file внутри campaign root;
3. самостоятельно пересчитать SHA-256;
4. сравнить computed hash с declared hash;
5. сравнить bytes `source_identity_path` с `source_sha` approval.

Правильный формат хэша без существующего совпадающего файла — `FAIL`.

## 6. Test №3 plan и G0 binding

`test3_commissioning_plan.json` обязан содержать schema, `test_id=TEST3`, `decision=PENDING`, source SHA, **относительный** `g0_approval_path`, exact no-HV scope, scope/timing review и ровно 12 unique contexts `(sector 0..5, window 0..1)`.

Для `TEST3_NOHV_COMMISSIONING_READY=PASS` retained `g0_approval.json` дополнительно обязан иметь:

- schema `h1-g0-approval-v2-test3-transition`, gate `HIL_TEST3_G0`, `test_id=TEST3` и `decision=APPROVED`;
- safety-owner metadata: role, approval id, approver и timestamp;
- exact equality G0 `source_sha` и plan `source_sha`;
- exact diagnostic/no-HV scope с запретами DC-link/Stage A/FOC/V/f/autotune;
- относительный path retained firmware и exact equality computed/approved firmware SHA-256.

Отсутствующий или `PENDING` G0 даёт `BLOCKED`; существующий, но malformed, source-mismatched или firmware-mismatched G0 даёт `FAIL`.

## 7. Regression matrix

Нужны deterministic tests как минимум для: valid evidence-bound route; tampered Test №2 UART; path traversal/symlink; Test №2 source identity mismatch; missing/duplicate contexts; forbidden scope flag; missing/PENDING G0; G0 source/firmware mismatch; malformed JSON; преждевременные claims physical Test №3/карты/Stage A.

## 8. Acceptance

Пакет принимается только после `py_compile`, полного `pytest`, `make`, `make test`, `git diff --check`, публикации отдельной ветки и зелёного CI. Flash, COM, ST-Link, sigrok, STEVAL и DC-link не используются.

## 9. References

[1]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md`.

[2]: `docs/TEST2_TEST3_TRANSITION.md`.

[3]: `tools/map_bench_dataset.md`.

[4]: `docs/templates/test3_nohv_campaign/g0_approval.json`.
