# AGENTS_STATUS.md — занятость агентов (координация)

Правило: **перед началом работы** агент добавляет строку, **по завершении**
обновляет статус и коммитит. Конфликт по файлам решать до начала работы.

| Агент | ПК | Ветка | Задача (ТЗ) | Файлы | Статус |
|---|---|---|---|---|---|
| ai4 (Manus) | sandbox | ai4/cli-golden-snapshots | CLI: golden snapshots полного реестра команд | tests/cli_test.c | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/gui-f821-lambda-fix | F821: безопасный захват exception в GUI callbacks | nucleo_debug_tool.py | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/vf-session-snapshot | V/f: снимки сессии и отсев устаревшего sync callback | vf_panel.py, tests/test_vf_panel_session.py, Makefile, .github/workflows/ci.yml | готово, ждёт CI-приёмки |
| ai4 (Manus) | sandbox | ai4/gui-p0-p1-fixes | TZ_GUI_P0_P1_FIXES: framing, state, queues, sigrok ownership | nucleo_debug_tool.py, vf_panel.py, foc_control_gui.py, measurement_gui.py, tests/test_gui_p0_p1.py | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/map-l3-numeric-canonical | TZ_MAP_L3_NUMERIC_CANONICAL: solver mA/wide arithmetic, canonical load validation | src/map_measurement_solver.c, src/map_candidate.c, Makefile, tests/map_solver_certifier_test.c | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/break-state-consistency | TZ_BREAK_STATE_CONSISTENCY: terminal FOC/capture state after hardware break | main.c | в работе |

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
