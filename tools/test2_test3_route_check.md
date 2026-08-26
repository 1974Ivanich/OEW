# Проверка маршрута Test №2 → Test №3 → карта

`test2_test3_route_check.py` — автономный fail-closed инструмент. Он читает локальные JSON/evidence files, но **никогда** не открывает COM/ST-Link/sigrok, не прошивает контроллер и не отправляет UART-команды.

## Запуск

```powershell
py -3 tools\test2_test3_route_check.py `
  --test2-approval D:\campaign_raw\test2_adc_<UTC>\test2_baseline_approval.json `
  --test3-plan D:\campaign_raw\test3_nohv_<UTC>\test3_commissioning_plan.json
```

По умолчанию отчёт записывается рядом с Test №3 plan как `test2_test3_route_verdict.json`. Опция `--output <путь>` задаёт другое расположение.

| Exit code | Значение |
|---:|---|
| `0` | `ROUTE_PLAN_VALID=PASS`: Test №2 evidence и Test №3 plan корректны. Это **не** означает готовность физического запуска. |
| `2` | Test №2 evidence или Test №3 plan не проходят fail-closed checks. |

## Evidence-bound Test №2 approval

`test2_baseline_approval.json` находится рядом с физическим campaign bundle и содержит его **существующий абсолютный root path**, но все individual evidence paths только относительны к этому root.

```json
{
  "schema": "oew-test2-baseline-approval-v2-evidence-bound",
  "test_id": "TEST2",
  "decision": "PASS",
  "source_sha": "<40–64 lowercase hexadecimal SHA>",
  "campaign": {
    "id": "test2_adc_<UTC>",
    "path": "D:/campaign_raw/test2_adc_<UTC>"
  },
  "source_identity_path": "identity/source_sha.txt",
  "evidence": {
    "uart_log": {"path": "uart/test2_adc_uart.log", "sha256": "<64 hex>"},
    "adc_samples": {"path": "uart/adc_samples.csv", "sha256": "<64 hex>"},
    "summary": {"path": "summary/test2_adc_summary.md", "sha256": "<64 hex>"},
    "build_log": {"path": "identity/build.log", "sha256": "<64 hex>"}
  },
  "review": {
    "reviewer": "<назначенный reviewer>",
    "reviewed_at": "<UTC timestamp>",
    "statement": "DC-link disconnected; default-deny ADC baseline evidence reviewed."
  }
}
```

Checker запрещает отсутствующие files, absolute/traversal paths и symbolic links. Для каждого файла он сам вычисляет SHA-256 и сравнивает его с approval. Он также требует, чтобы bytes retained `identity/source_sha.txt` совпадали с `source_sha` approval. Одного правильного формата hash более недостаточно.

## Test №3 plan и G0

`test3_commissioning_plan.json` остаётся планом, поэтому его `decision` всегда `PENDING`. Его `g0_approval_path` должен быть **относительным** путём к `g0_approval.json` внутри directory Test №3 campaign.

Для `TEST3_NOHV_COMMISSIONING_READY=PASS` checker требует, чтобы этот G0 file существовал и содержал:

| Гейт | Условие |
|---|---|
| Schema/gate | `h1-g0-approval-v2-test3-transition`, `HIL_TEST3_G0`, `TEST3` |
| Решение | `decision=APPROVED`, роль `safety-owner`, идентификатор/approver/time |
| Source identity | SHA в G0 точно совпадает с SHA Test №3 plan |
| Scope | Сохраняются diagnostic/no-HV и все `forbids_*` flags |
| Firmware identity | Retained non-symlink firmware file внутри Test №3 campaign существует и его SHA-256 совпадает с G0 approval |

`PENDING`, отсутствующий или противоречивый G0 не даёт physical readiness. При этом корректный Test №2/Test №3 **plan** остаётся отдельным состоянием `ROUTE_PLAN_VALID=PASS`.

## Четыре разных состояния

```text
ROUTE_PLAN_VALID
    └─ документы, Test №2 evidence hashes и 12-context plan проверены.

TEST2_BASELINE_ACCEPTED
    └─ reviewer-approved Test №2 evidence реально hash-bound к retained files.

TEST3_NOHV_COMMISSIONING_READY
    └─ plan valid + локально проверенный Test №3 G0/firmware binding.

PHYSICAL_TEST3_EXECUTED
    └─ всегда BLOCKED в этом инструменте; только фактическая campaign может его доказать.
```

Независимо от всех положительных offline checks output всегда содержит:

```text
PHYSICAL_TEST3_EXECUTED=BLOCKED
REAL_BOARD_CAPTURE_VALID=BLOCKED
STAGE_A_60V=BLOCKED
LIMITED_MOTOR_START=BLOCKED
```

Physical characterization требует external current reference, canonical `map_bench_dataset.py`, pipeline certification и отдельное решение safety owner. Подача DC-link и запуск двигателя этим инструментом не разрешаются.
