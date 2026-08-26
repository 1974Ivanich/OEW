# Шаблоны упрощённого маршрута Test №2 → Test №3 → карта

Эти файлы — **шаблоны reviewer evidence**, а не разрешение физического запуска. Скопируйте их в отдельный campaign directory вне Git и замените все значения в угловых скобках фактическими данными. До этого route checker должен вернуть `FAIL`.

| Файл | Кто заполняет | Когда |
|---|---|---|
| `test2_baseline_approval.template.json` | Назначенный reviewer Test №2 | Только после полного review существующего Test №2 campaign bundle. |
| `test3_commissioning_plan.template.json` | Reviewer/Test №3 safety owner | До Test №3 G0/pre-flight; `decision` остаётся `PENDING`. |

Проверка:

```powershell
py -3 tools\test2_test3_route_check.py `
  --test2-approval <путь>\test2_baseline_approval.json `
  --test3-plan <путь>\test3_commissioning_plan.json
```

Даже корректные заполненные файлы дают только готовность маршрута. Они не разрешают diagnostic flash, `mcarm`, `mapcap run`, DC-link, Stage A, карту или запуск двигателя.
