# Проверка готовности маршрута Test №2 → Test №3 → карта

`test2_test3_route_check.py` — автономный fail-closed инструмент. Он читает reviewer-approved Test №2 evidence и reviewed Test №3 plan. Он **никогда** не открывает COM/ST-Link/sigrok, не прошивает контроллер и не отправляет UART-команды.

## Запуск

```powershell
py -3 tools\test2_test3_route_check.py `
  --test2-approval D:\campaign_raw\test2_adc_<UTC>\test2_baseline_approval.json `
  --test3-plan D:\campaign_raw\test3_nohv_<UTC>\test3_commissioning_plan.json
```

По умолчанию отчёт записывается рядом с Test №3 plan как `test2_test3_route_verdict.json`. Опция `--output <путь>` задаёт другое расположение.

| Exit code | Значение |
|---:|---|
| `0` | Все evidence/planning checks прошли; маршрут готов к следующему **человеческому** G0/pre-flight gate. |
| `2` | Любой input, review, context или scope check не прошёл. |

## `test2_baseline_approval.json`

```json
{
  "schema": "oew-test2-baseline-approval-v1",
  "test_id": "TEST2",
  "decision": "PASS",
  "source_sha": "<40–64 lowercase hexadecimal SHA>",
  "campaign": {
    "id": "test2_adc_<UTC>",
    "path": "D:/campaign_raw/test2_adc_<UTC>"
  },
  "evidence_sha256": {
    "uart_log": "<64 lowercase hexadecimal SHA-256>",
    "adc_samples": "<64 lowercase hexadecimal SHA-256>",
    "summary": "<64 lowercase hexadecimal SHA-256>"
  },
  "review": {
    "reviewer": "<назначенный reviewer>",
    "reviewed_at": "<UTC timestamp>",
    "statement": "DC-link disconnected; default-deny ADC baseline evidence reviewed."
  }
}
```

Файл разрешён только после review фактически собранного Test №2 campaign bundle. Он не заменяет campaign artifacts и не должен создаваться для неполного/непринятого evidence.

## `test3_commissioning_plan.json`

```json
{
  "schema": "oew-test3-commissioning-plan-v1",
  "test_id": "TEST3",
  "decision": "PENDING",
  "source_sha": "<40–64 lowercase hexadecimal SHA>",
  "g0_approval_path": "D:/campaign_raw/test3_nohv_<UTC>/g0_approval.json",
  "scope": {
    "target": "physical-nohv-diagnostic-test3",
    "diagnostic_only": true,
    "forbids_dc_link": true,
    "forbids_stage_a": true,
    "forbids_foc": true,
    "forbids_vf": true,
    "forbids_autotune": true
  },
  "scope_timing_review": {
    "reviewer": "<назначенный reviewer>",
    "reviewed_at": "<UTC timestamp>",
    "statement": "One no-HV timing review covers the planned 12 contexts."
  },
  "contexts": [
    {"sector": 0, "window": 0},
    {"sector": 0, "window": 1}
  ],
  "claims": {}
}
```

`contexts` обязан содержать ровно все 12 unique pairs `sector=0..5` × `window=0..1`. Значение `decision` намеренно `PENDING`: approval physical Test №3 остаётся отдельным G0/pre-flight human decision.

## Результат и безопасность

Даже при `ROUTE_VERDICT=PASS` output всегда содержит:

```text
REAL_BOARD_CAPTURE_VALID=BLOCKED
STAGE_A_60V=BLOCKED
LIMITED_MOTOR_START=BLOCKED
```

`TEST3_NOHV_COMMISSIONING_READY=PASS` означает только, что документированный маршрут Test №2/Test №3 готов к следующему reviewer gate. Физическая карта требует subsequent characterization с external current reference, canonical `map_bench_dataset.py` и pipeline certification; подача DC-link и запуск двигателя этим инструментом не разрешаются.
