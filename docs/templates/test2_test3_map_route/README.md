# Шаблоны маршрута Test №2 → Test №3 → карта

Эти файлы — **шаблоны reviewer evidence**, а не разрешение физического запуска. Скопируйте их в отдельный campaign directory вне Git и замените все значения в угловых скобках фактическими данными. До этого route checker обязан вернуть `FAIL` или `BLOCKED`.

| Файл | Кто заполняет | Когда |
|---|---|---|
| `test2_baseline_approval.template.json` | Назначенный reviewer Test №2 | Только после полного review существующего Test №2 campaign bundle и вычисления хэшей фактических файлов. |
| `test3_commissioning_plan.template.json` | Reviewer/Test №3 safety owner | До Test №3 G0/pre-flight; `decision` всегда остаётся `PENDING`. |

## Test №2 evidence binding

`test2_baseline_approval.json` указывает реальный существующий `campaign.path`. Все paths в `source_identity_path` и `evidence.*.path` должны быть относительны к этому root. Checker отвергает отсутствующие файлы, path traversal и symbolic links, а затем сам пересчитывает SHA-256 каждого retained файла.

Не подставляйте хэши без соответствующих UART log, ADC CSV, summary, build log и `identity/source_sha.txt`: правильный формат строки без файла не является evidence.

## Test №3 plan и G0

`g0_approval_path` в plan — относительный путь к будущему `g0_approval.json` внутри directory Test №3 campaign. Route checker различает состояния:

```text
ROUTE_PLAN_VALID
TEST2_BASELINE_ACCEPTED
TEST3_NOHV_COMMISSIONING_READY
PHYSICAL_TEST3_EXECUTED
```

`TEST3_NOHV_COMMISSIONING_READY` может стать `PASS` только после существующего, `APPROVED` Test №3 G0 с совпадающими source SHA и retained firmware SHA-256. Пока transition G0 template остаётся `PENDING`, это состояние корректно остаётся `BLOCKED`; текущий checker ничего сам не утверждает.

Проверка:

```powershell
py -3 tools\test2_test3_route_check.py `
  --test2-approval <путь>\test2_baseline_approval.json `
  --test3-plan <путь>\test3_commissioning_plan.json
```

Даже при корректных заполненных files route checker не разрешает diagnostic flash, `mcarm`, `mapcap run`, DC-link, Stage A, карту или запуск двигателя.
