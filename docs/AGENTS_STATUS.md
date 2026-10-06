# AGENTS_STATUS.md — доска занятости

Одна строка = одна активная работа. Завершённое здесь не хранится: история — в
`git log`, отчёты — в `docs/`. Строка нужна, только если ты держишь файл,
который может тронуть другой агент; обновлять её по завершении не обязательно.
Ссылки на «Ветка» без публикации на GitHub приёмке не подлежат.

| Ветка / где | Файлы (держит) | Кто | Что нужно |
|---|---|---|---|
| main (ТЗ) | docs/TZ_ADC_REGULAR_READ_WHILE_INJECTED.md | ai1 (ПК-1) | ждёт исполнителя: RED T1–T5 → R1–R9 → измерение |
| стенд, без ветки | E:\b1_phase_obs_return_20260920\ | ai3 (ПК-3) | прогон B1 сделан; закрыть F7/F8 (пиннинг хэшей инструментов) |
| ai3/phase-ref-e1e6-preflight-review | E:\phase_ref_e1e6_bench_20260921\ | ai3 (ПК-3) | пауза: шаг 2 ждёт декларации оператора |
| ai2/map-cal-sig-stable | src/map_capture_port.c, src/map_capture_profiles.c | ai2 (ПК-2) | CI запущен, ждёт приёмки |
| ai2/60v-ingest-waiver | tools/map_scope_ingest.py, tools/boar_campaign_ingest.py | ai2 (ПК-2) | ждёт приёмки |
| ai2/hwt-rsp-v1 | tools/hwt.py, tools/hwt_rsp.py, tests/target/* | ai2 (ПК-1) | ждёт стенда (ST-Link): `make hwt-doctor && make hwt-run` |
| ai-koda/minimize-agent-rules | AGENTS.md, README.md, docs/AGENTS_STATUS.md | ai-koda | сжатие регламента, ждёт проверки владельцем |
| sandbox, не опубликованы | ai4/* (cli-golden-snapshots, gui-f821-lambda-fix, vf-session-snapshot, gui-p0-p1-fixes, map-l3-numeric-canonical, break-state-consistency, telemetry-fw-budget, adc-calibration-ct-boundary) | ai4 (Manus) | приёмке не подлежат до публикации ветки |
| локально, не опубликованы | ai1/cli-main-loop, ai2/encoder-fix, ai-bench/map-artifact-writer, ai-koda/telemetry-gui-logging | ai1, ai2, ai-bench, ai-koda | приёмке не подлежат до публикации ветки |

Открытые вопросы по ветвям `origin` вне `main` (что вливать, что закрывать) — за
владельцем. Сводка восстанавливается командами git:
`git log --oneline main..origin/<ветка>` — что ветка приносит;
`git merge-tree --write-tree main origin/<ветка>` — конфликтует ли.