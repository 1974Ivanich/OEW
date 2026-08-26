# ТЗ: упрощённый маршрут Test №2 → Test №3 → измеренная карта → ограниченный запуск

## 1. Цель

Зафиксировать короткий, проверяемый и fail-closed маршрут от уже собранного baseline evidence Test №2 до физически квалифицированной `OewCurrentMap`. Маршрут не создаёт повторных физических тестов только ради дублирующих software-проверок и не меняет firmware, safety-модули, MapCapture UART-контракт или `.ioc`.

## 2. Единственные физические ступени

| Ступень | Единственный результат | Разрешённый scope | Что не доказывает |
|---|---|---|---|
| **Test №2** | `TEST2_BASELINE_ACCEPTED` | Default-deny ADC baseline: identity, обе шины DC-link `<1 V`, PWM OFF/MOE=0, calibration, I1/I2/Ires/VBUS, UART integrity. | Test №3, MapCapture, карту, Stage A или запуск двигателя. |
| **Test №3** | `TEST3_NOHV_COMMISSIONING_READY` | Одна controlled no-HV кампания: approved G0, pre-flight, одна scope timing qualification, reviewed plan всех 12 contexts и MapCapture readiness. | Измеренную карту: no-HV terminal contract требует VBUS_LOW и ноль records. |
| **Characterization** | `REAL_BOARD_CAPTURE_VALID` | Отдельная physical campaign с внешним двухфазным current reference, raw-first dataset, 12 sector/window contexts, solver/certifier. | Загрузку карты в firmware и запуск двигателя без отдельного Stage A допуска. |
| **Ограниченный запуск** | Вне данного пакета | Только после отдельного safety-owner Stage A approval, accepted map artifact и map load admission. | Любой следующий режим/мощность. |

> Нельзя заменять physical reference dataset одним no-HV MapCapture: действующий no-HV contract требует `term=-12`, `detail=VBUS_LOW`, `adc_status=WINDOW_INVALID` и zero records. Поэтому `REAL_BOARD_CAPTURE_VALID` формируется только после characterization, а не Test №3.

## 3. Упрощение критического пути

Маршрут не вводит отдельные физические «profile test», «sector test», «window test», повторный software-HIL или повторные host-side map regressions. Эти проверки остаются частью одной Test №3 commissioning campaign, существующего CI или автоматической dataset/map certification.

Повтор Test №2 не требуется, если уже существующий campaign bundle полностью проверен назначенным reviewer и записан в signed/evidence-bound `test2_baseline_approval.json`.

## 4. Offline route checker

Добавляется `tools/test2_test3_route_check.py`. Он не открывает COM/ST-Link/sigrok и не запускает firmware. Он проверяет два machine-readable входа:

1. `test2_baseline_approval.json` — explicit human approval уже собранного Test №2 evidence.
2. `test3_commissioning_plan.json` — reviewed plan одной no-HV Test №3 campaign с полной матрицей 6 sectors × 2 windows.

При корректных входах tool может выдать только:

```text
TEST2_BASELINE_ACCEPTED=PASS
TEST3_NOHV_COMMISSIONING_READY=PASS
REAL_BOARD_CAPTURE_VALID=BLOCKED
STAGE_A_60V=BLOCKED
LIMITED_MOTOR_START=BLOCKED
```

Parser никогда не выдаёт `REAL_BOARD_CAPTURE_VALID=PASS`, `STAGE_A_60V=PASS` или разрешение на запуск. Для этих состояний нужны фактическая characterization campaign, canonical dataset validator, pipeline certification, map-load admission и отдельный safety-owner approval, которые намеренно находятся за границей данного инструмента.

## 5. Required Test №2 approval fields

`test2_baseline_approval.json` содержит schema, `test_id=TEST2`, `decision=PASS`, campaign id/path, source SHA, evidence SHA-256, reviewer, reviewed UTC и явный statement, что DC-link remained disconnected and no energising command occurred. Ложная или неполная декларация — FAIL.

## 6. Required Test №3 plan fields

`test3_commissioning_plan.json` содержит schema, `test_id=TEST3`, `decision=PENDING`, source SHA, approved G0 path, scope mode `physical-nohv-diagnostic-test3`, обязательный scope timing review и ровно 12 unique contexts `(sector 0..5, window 0..1)`. План обязан содержать `forbids_dc_link=true`, `forbids_stage_a=true`, `forbids_foc=true`, `forbids_vf=true` и `forbids_autotune=true`.

## 7. Regression matrix

Нужны deterministic tests как минимум для: valid route readiness; missing Test №2 reviewer; Test №2 non-PASS; Test №3 non-PENDING; missing/duplicate/incomplete contexts; forbidden scope flag false; unsupported schema; malformed JSON; попытки claim `REAL_BOARD_CAPTURE_VALID` или Stage A PASS.

## 8. Acceptance

Пакет принимается только после `py_compile`, полного `pytest`, `make`, `make test`, `git diff --check`, публикации отдельной ветки и зелёного CI. Hardware commands, flash, COM, ST-Link, sigrok, STEVAL и DC-link не используются.

## 9. References

[1]: `docs/TEST2_ADC_CHAIN_PC3_PLAN.md`.

[2]: `docs/TEST2_TEST3_TRANSITION.md`.

[3]: `tools/map_bench_dataset.md`.

[4]: `docs/templates/test3_nohv_campaign/g0_approval.json`.
