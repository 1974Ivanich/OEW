# AGENTS_STATUS.md — занятость агентов (координация)

Правило: **перед началом работы** агент добавляет строку, **по завершении**
обновляет статус и коммитит. Конфликт по файлам решать до начала работы.

| Агент | ПК | Ветка | Задача (ТЗ) | Файлы | Статус |
|---|---|---|---|---|---|
| ai4 (Manus) | sandbox | ai4/cli-golden-snapshots | CLI: golden snapshots полного реестра команд | tests/cli_test.c, src/cli.c (только при обнаружении тестируемого дефекта) | в работе |

<!-- Пример:
| ai1 (Hermes) | ПК-1 | ai1/cli-main-loop | F7: вынос command loop (TZ_CLI_MAIN_LOOP_EXTRACTION.md) | main.c, src/cli.*, Makefile | в работе |
| ai2 | ПК-2 | ai2/encoder-fix | F2: encoder тесты | src/encoder.c, tests/encoder_test.c | готово, ждёт приёмки |
-->

## Зоны ответственности (закреплено в AGENTS_WORKFLOW.md §6)

- `main.c`, `src/cli.*`, интеграция, приёмка — **один агент** (приёмщик).
- `src/autotune*`, `src/observer*`, `src/pll*`, `src/vf_*` — по ТЗ, отдельные ветки.
- Safety (`src/foc*`, `src/pwm*`, `src/protect*`, `src/adc*`, `src/adc_dispatch.*`,
  `.ioc`) — **только по явному ТЗ**, один агент за раз.
- `Makefile`, `scripts/` — только с согласованием (общие файлы).
