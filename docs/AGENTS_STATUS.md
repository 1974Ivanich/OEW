# AGENTS_STATUS.md — доска занятости

Одна строка = одна активная работа. Завершённое здесь не хранится: история — в
`git log`, отчёты — в `docs/`. Строка нужна, только если ты держишь файл,
который может тронуть другой агент; обновлять её по завершении не обязательно.
Ссылки на «Ветка» без публикации на GitHub приёмке не подлежат.

## Держат файл (открытые PR)

| Ветка (PR) | Файлы (держит) | Кто | Что нужно |
|---|---|---|---|
| ai2/map-ref-1a-3a-acs712-5a (#22) | tools/map_scope_ingest.py, tests/test_acs712_5a_map_reference.py, tools/acs712_ingest_acceptance.py | ai2 (ПК-2) | WIP, 21 коммит вне main, 15 файлов |
| ai4/map-region-geometry-revision (#12) | src/current_map_selector.*, src/map_artifact_decoder.*, src/map_candidate.c, src/map_region_certifier.c | ai4 | 25 коммитов вне main, 13 файлов; CI только у этой ветки, красный |
| ai2/tz-ls-step-analyzer (#20) | tools/ls_step_analyze.py, tests/test_ls_step_analyze.py | ai2 (ПК-2) | 23 коммита вне main, 3 файла |
| ai2/vf-overshoot-stability (#11) | main.c, src/adc_dispatch.*, docs/WEB AI TZ/TZ_WEB_AI_vf_* | ai2 (ПК-2) | 14 коммитов вне main, 30 файлов |
| ai4/foc-first-start-pc3 (#21) | src/cli.c, tests/cli_test.c, docs/TZ_FOC_FIRST_START_PC3.md | ai4 | 14 коммитов вне main, 3 файла |
| ai/real-board-auto-characterization (#7) | src/map_real_board_profile.*, tools/map_auto_characterize.py | ai | 8 коммитов вне main, 5 файлов |

## Держат файл (PR не открыт)

| Ветка / где | Файлы (держит) | Кто | Что нужно |
|---|---|---|---|
| pc3/enc-direction-and-startup-fix (локально, PR не открыт) | src/encoder.c/.h, src/foc.c, src/cli.c/.h, main.c, Makefile, tests/cli_test.c | агент ПК-3 (стенд) | 1 коммит `fc955db` поверх origin/main (8 файлов, +71 −11), гейт `make test` зелёный. ВНИМАНИЕ: правит `src/cli.c` и `tests/cli_test.c`, которые держит ai4 (`ai4/foc-first-start-pc3`, #21) — при вливании обоих ожидать конфликт в этих файлах и в golden-снапшотах |
| ai2/map-cal-sig-stable (origin) | src/map_capture_port.c, src/map_capture_profiles.c | ai2 (ПК-2) | ждёт приёмки, PR не открыт |
| ai2/60v-ingest-waiver (origin) | tools/map_scope_ingest.py, tools/boar_campaign_ingest.py | ai2 (ПК-2) | ждёт приёмки, PR не открыт |
| pc3/mock-bkf-header | tests/hs1_mock/stm32g474xx.h | ПК-3 | правка mock после 7b84771: снимает красный CI на main (шаг «Hosted + QEMU + pytest tests»); ждёт приёмки |
| ai-hermes/gitflic-ci-smoke (только локально) | gitflic-ci.yaml | ai-hermes | приёмке не подлежит до публикации ветки |
| ai2/oi-preflight-makefile (#10, PR закрыт 06.10.2026 без слияния) | Makefile | ai2 (ПК-2) | 2 коммита вне main, ветка не двигалась с 30.08; с main конфликтует (merge-tree), переоткрытие требует rebase |
| ai2/acs712-scope-foc-map (#23, PR закрыт 06.10.2026 без слияния) | docs/TZ_ACS712_SCOPE_FOC_CURRENT_MAP.md, docs/pc3_package/**, .github/workflows/ci.yml | ai2 (ПК-2) | 26 коммитов вне main, последний 05.10; с main конфликтует (ci.yml, merge-tree), переоткрытие требует rebase |
|| main (рабочая копия ПК-3) | src/foc.c, src/current_map_selector.* | devin (ПК-3) | ТЗ: политика старта — сжатие mod-вектора к активному региону при промахе SelectNextContext |
| main (golden-тест артефакта + метаданные калибровки) | tests/map_artifact_golden_test.c, Makefile, .github/workflows/ci.yml, tools/map_scope_ingest.py, tools/map_bench_dataset.md | ПК-1 | правки поверх main; пересечения с #10/#23 сняты — PR закрыты 06.10 без слияния |

## Сводка по origin

`origin` = 56 ветвей: `main` + 55 с уникальными коммитами вне main (от 1 до 26),
открытых PR нет (проверено 15.10.2026: #10 и #23 закрыты 06.10 без слияния).
Все — незавершённые задачи; ничего из них не удалено. Восстановить сводку:

```bash
git for-each-ref --format='%(refname:short)' refs/remotes/origin | sed 's|^origin/||' \
  | grep -v -x -e HEAD -e main | while read b; do
      printf '%3s  %s\n' "$(git rev-list --count main..origin/"$b")" "$b"; done | sort -rn
```

`git log --oneline main..origin/<ветка>` — что ветка приносит;
`git merge-tree --write-tree main origin/<ветка>` — конфликтует ли.

Что вливать и что закрывать — решает владелец.
