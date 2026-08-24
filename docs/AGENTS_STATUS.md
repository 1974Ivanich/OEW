# AGENTS_STATUS.md — занятость агентов (координация)

Правило: **перед началом работы** агент добавляет строку, **по завершении**
обновляет статус и коммитит. Конфликт по файлам решать до начала работы.

| Агент | ПК | Ветка | Задача (ТЗ) | Файлы | Статус |
|---|---|---|---|---|---|
| ai-bench (стенд) | ПК-1 | ai-bench/map-artifact-writer | PR-2 artifact writer: CI-подключение standalone-регрессии + фиксы пакета (wire size 490, adc_frame_stub в .mk, ADC-мок регистров, provenance-фикстуры) | tools/map_artifact_writer.*, tests/map_capture_port_test.c, tests/foc_start_gate_test.c, tests/map_builder_test.c, tests/hs1_mock/stm32g474xx.h, .github/workflows/ci.yml | готово к CI-приёмке |
| ai-hermes (приёмщик) | ПК-1 | ai-hermes/map-artifact-pipeline | Интеграция L3: Accumulator→Solver→Certifier→Writer (host), CLI oew_map_v2.bin/.json, регрессия + e2e в CI | tools/map_artifact_pipeline.*, tools/map_artifact_pipeline_cli.c, tools/map_artifact_pipeline_demo.txt, tests/map_artifact_pipeline_test.c, tools/map_artifact_writer_test.mk, .github/workflows/ci.yml | влито в main |
| ai-bench (стенд) | ПК-1 | ai-bench/map-characterization-e2e | Characterization adapter: стенд capture+scope+timing → MapMeasurementSample (fail-closed, без синтеза evidence), регрессия, standalone .mk | tools/map_characterization_adapter.*, tests/map_characterization_e2e_test.c, tools/map_characterization_e2e_test.mk | влито в main |
| ai-bench (стенд) | ПК-1 | ai-bench/map-bench-dataset | Канонический dataset кампании: manifest.json+samples.jsonl (raw-first), validator/конвертер, REJECT-матрица e2e; CLI: самопроверка LoadMeasured | tools/map_bench_dataset.*, tools/campaign_demo/, tests/test_map_bench_dataset.py, tools/map_artifact_pipeline_cli.c | в работе → CI |
| ai2 (Hermes) | ПК-2 | ai2/emstop-telemetry | Телеметрия EM_STOP1/2 (PB12/PD2): периодика @FOC/@VF + @FAULT:CLEAR:STATUS | main.c, src/cli.*, src/pwm_board_pins.*, tests/cli_test.c | влито в main |
| ai4 (Manus) | sandbox | ai4/cli-golden-snapshots | CLI: golden snapshots полного реестра команд | tests/cli_test.c | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/gui-f821-lambda-fix | F821: безопасный захват exception в GUI callbacks | nucleo_debug_tool.py | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/vf-session-snapshot | V/f: снимки сессии и отсев устаревшего sync callback | vf_panel.py, tests/test_vf_panel_session.py, Makefile, .github/workflows/ci.yml | готово, ждёт CI-приёмки |
| ai4 (Manus) | sandbox | ai4/gui-p0-p1-fixes | TZ_GUI_P0_P1_FIXES: framing, state, queues, sigrok ownership | nucleo_debug_tool.py, vf_panel.py, foc_control_gui.py, measurement_gui.py, tests/test_gui_p0_p1.py | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/map-l3-numeric-canonical | TZ_MAP_L3_NUMERIC_CANONICAL: solver mA/wide arithmetic, canonical load validation | src/map_measurement_solver.c, src/map_candidate.c, Makefile, tests/map_solver_certifier_test.c | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/break-state-consistency | TZ_BREAK_STATE_CONSISTENCY: terminal FOC/capture state after hardware break | main.c | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/telemetry-fw-budget | TZ_TELEMETRY_FW_BUDGET: compact V/f telemetry profile and UART truncation accounting | main.c, src/cli.h, src/uart.c, src/uart.h, tests/telemetry_budget_test.c, Makefile | готово, ждёт приёмки |
| ai4 (Manus) | sandbox | ai4/adc-calibration-ct-boundary | ADC: fail-closed offset calibration when CT/Ires input is at the ADC boundary; preserve dual-injected runtime path | src/adc.c, src/adc.h, tests/adc_frame_host_test.c, Makefile | готово, ждёт приёмки |
| ai-koda | local | ai-koda/telemetry-gui-logging | TZ_TELEMETRY_GUI_LOGGING: GUI CSV batching, bounded log retention, buffered file writer, session robustness | vf_panel.py, nucleo_debug_tool.py, measurement_gui.py, foc_control_gui.py, tests/test_telemetry_gui_logging.py | готово, ждёт приёмки |
| ai-bench (стенд, ПК-3) | ПК-3 | main | ТЗ CT-канал/калибровка: принято в main (фиксы CT: retain shunt offsets + честный FAIL + Путь B) | src/adc.c, src/cli.c, tests/adc_frame_host_test.c | влито в main |
| ai-bench (стенд, ПК-3) | ПК-3 | ai-bench/map-capture-profile | ТЗ board-qualified профиль MapCapture: этап B (synthetic exact-match) реализован + тесты (exact-match, REJECT unknown/0, 12 rows, default-deny) + CI-шаг; protected #if (OEW_HOST_TEST+OEW_MAP_SYNTHETIC_PROFILE) | src/map_capture_profiles.c, tests/map_capture_profile_test.c/.default.c/.mk, .github/workflows/ci.yml | готово → ждёт CI PASS + приёмки (real values — после Stage A) |
| (первый запуск) | ПК-1 | main | Первая стендовая сессия: ADC PASS, ENC PASS (err=0, period=897 мкс, angle=3019) | — | завершено |
| (SD-приёмка) | ПК-1 | main | SD-приёмка T1–T4: default-deny, SD1 break, SD2 break, reset — все PASS | — | завершено |
| (L3 commissioning-проверка) | ПК-1 | main | Проверка commissioning-сборки: mcarm/build заблокированы без board-qualified профиля (fail-closed подтверждён на железе) | main.c, src/map_capture_profiles.c | завершено — карта невозможна до offline-профиля |

<!-- Пример:
| ai1 (Hermes) | ПК-1 | ai1/cli-main-loop | F7: вынос command loop (TZ_CLI_MAIN_LOOP_EXTRACTION.md) | main.c, src/cli.*, Makefile | готово, ждёт приёмки |
| ai2 | ПК-2 | ai2/encoder-fix | F2: encoder тесты | src/encoder.c, tests/encoder_test.c | готово, ждёт приёмки |
-->

## Зоны ответственности (закреплено в AGENTS_WORKFLOW.md §6)

- `main.c`, `src/cli.*`, интеграция, приёмка — **один агент** (приёмщик).
- `src/autotune*`, `src/observer*`, `src/pll*`, `src/vf_*` — по ТЗ, отдельные ветки.
- Safety (`src/foc*`, `src/pwm*`, `src/protect*`, `src/adc*`, `src/adc_dispatch.*`,
  `.ioc`) — **только по явному ТЗ**, один агент за раз.
- `Makefile`, `scripts/` — только с согласованием (общие файлы).
