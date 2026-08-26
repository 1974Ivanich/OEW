# Краткий маршрут: Test №2 → Test №3 → измеренная карта → ограниченный запуск

## Назначение

Этот документ заменяет раздробленный набор промежуточных физических «мини-тестов» одним контролируемым маршрутом. Он не изменяет принятые software-регрессии: CI продолжает проверять сборку, hosted/QEMU, simulation, dataset validator, solver и certifier автоматически. На ПК-3 остаются только физические доказательства, которые невозможно получить из CI.

> **Строгая граница:** Test №3 no-HV готовит и квалифицирует commissioning campaign, но не создаёт действительную физическую карту токов. Его terminal contract требует `VBUS_LOW` и ноль MapCapture records. Карта появляется только из последующей physical characterization с внешним эталоном токов и canonical dataset.

## Маршрут

| Этап | Один итоговый результат | Физический объём | Автоматизация | Блокировка следующего этапа |
|---|---|---|---|---|
| **Test №2** | `TEST2_BASELINE_ACCEPTED` | Default-deny ADC baseline: firmware identity, обе DC-link шины `<1 V`, PWM OFF, calibration, десять ADC samples, UART integrity. | Checker сам пересчитывает SHA-256 UART/ADC/summary/build evidence и сравнивает retained source identity с approval. | Нет принятого reviewer evidence либо mismatch/absence любого retained файла. |
| **Test №3** | `TEST3_NOHV_COMMISSIONING_READY` | Одна no-HV commissioning campaign: G0/pre-flight, scope timing qualification и reviewed matrix всех 12 contexts. | `test2_test3_route_check.py` отделяет valid plan от G0-bound readiness и локально проверяет G0/source/firmware binding. | Отсутствующий, PENDING или противоречивый G0/pre-flight; route checker ничего сам не утверждает. |
| **Characterization** | `REAL_BOARD_CAPTURE_VALID` | Один physical dataset с external two-phase current reference и всеми 12 sector/window contexts. | `map_bench_dataset.py` → `map_artifact_pipeline_cli` → solver/certifier/writer. | Любой raw/sample/timing/scope/region/identity failure. |
| **Ограниченный запуск** | Отдельное решение safety owner | Вне этого маршрута. | Только после accepted artifact, `CurrentMap_LoadMeasured` и отдельного Stage A approval. | Всегда BLOCKED данным route checker. |

## Test №2: не повторять без причины

Если реальный Test №2 уже дал полный campaign bundle, его не нужно повторять из-за повторения software-проверок. Назначенный reviewer создаёт `test2_baseline_approval.json`, фиксируя source SHA, относительные пути и SHA-256 UART/ADC/summary/build файлов, имя кампании, дату и statement о физически отключённом DC-link. Checker самостоятельно открывает retained файлы внутри campaign root, запрещает traversal/symlink и пересчитывает SHA-256; только после точного совпадения approval даёт `TEST2_BASELINE_ACCEPTED=PASS`.

Если в evidence не хватает любого hard gate, недопустимо «дописать» approval задним числом: Test №2 остаётся `FAIL` или `BLOCKED`, а correction выполняется до перехода далее.

## Test №3: одна объединённая no-HV campaign

Test №3 не дробится на отдельные физические проверки profile, sector, window и timing. Перед одной кампанией reviewer создаёт `test3_commissioning_plan.json` c ровно 12 парами:

```text
sector = 0…5
window = 0…1
```

Одна scope/timing qualification покрывает plan. Действительное physical execution всё равно требует отдельные accepted Test №3 G0 и pre-flight, а `decision` в plan намеренно остаётся `PENDING`: route checker не может и не должен заменить safety-owner approval.

Условия остаются no-HV/default-deny: DC-link, Stage A, FOC, V/f и autotune запрещены. Никакая готовность plan не разрешает запуск `mcarm`, `mapcap run` или diagnostic flash без уже существующих G0/pre-flight gates.

## Characterization: единственный путь к карте

Когда и только когда отдельный будущий safety gate разрешит physical characterization, не создаются дополнительные «evidence tests». Одна campaign формирует raw-first `manifest.json` + `samples.jsonl` по каноническому контракту. Она должна иметь external two-phase current reference, ADC/scope qualification и хотя бы один valid sample в каждой из 12 contexts.

Дальше автоматически выполняются существующие инструменты:

```text
map_bench_dataset.py
    → dataset.txt
    → map_artifact_pipeline_cli
    → Accumulator → Solver → Certifier → Writer
    → oew_map_v2.bin + oew_map_v2.json
```

Validator и pipeline fail-closed проверяют identity, raw samples, KCL, duplicate sequence, timing margin, conditioning и region coverage. `REAL_BOARD_CAPTURE_VALID` может быть заявлен только результатом этой действительной characterization и принятого artifact workflow, а не Test №3 no-HV plan.

## Offline route checker

```powershell
py -3 tools\test2_test3_route_check.py `
  --test2-approval D:\campaign_raw\test2_adc_<UTC>\test2_baseline_approval.json `
  --test3-plan D:\campaign_raw\test3_nohv_<UTC>\test3_commissioning_plan.json
```

При корректных входах возможен только следующий результат:

```text
ROUTE_PLAN_VALID=PASS
TEST2_BASELINE_ACCEPTED=PASS
TEST3_NOHV_COMMISSIONING_READY=BLOCKED|PASS
PHYSICAL_TEST3_EXECUTED=BLOCKED
REAL_BOARD_CAPTURE_VALID=BLOCKED
STAGE_A_60V=BLOCKED
LIMITED_MOTOR_START=BLOCKED
```

`ROUTE_PLAN_VALID=PASS` означает только, что Test №2 evidence действительно hash-bound к retained files и Test №3 plan корректен. `TEST3_NOHV_COMMISSIONING_READY=PASS` дополнительно требует локально проверяемые `APPROVED` Test №3 G0, exact source SHA и exact retained firmware SHA-256. Это не физический PASS Test №3: `PHYSICAL_TEST3_EXECUTED` всегда `BLOCKED`. Любая неполнота, hash mismatch, unsafe path, duplicate context, запрещённый scope flag, противоречивый G0 или преждевременная декларация карты/Stage A возвращает `ROUTE_PLAN_VALID=FAIL` и exit code `2`.

## Неавтоматизируемые решения

Human review остаётся обязательным для Test №2 evidence, Test №3 G0/pre-flight, scope waveform и любого future Stage A. Автоматизация собирает consistency/evidence checks; она не заменяет безопасное физическое решение.

## Ссылки

[1]: `TEST2_ADC_CHAIN_PC3_PLAN.md` — обязательный scope Test №2.

[2]: `TEST2_TEST3_TRANSITION.md` — переход к Test №3 и legacy identifier boundary.

[3]: `tools/map_bench_dataset.md` — canonical physical characterization dataset и 12-context validation.

[4]: `TZ_TEST2_TEST3_MAP_ROUTE.md` — формальный contract нового route checker.
