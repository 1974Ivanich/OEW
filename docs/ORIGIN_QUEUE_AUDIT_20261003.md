# Аудит очереди веток `origin` — «объявлено, но не влито» (03.10.2026)

**Зачем.** Регламент требует объявлять пакеты в `docs/AGENTS_STATUS.md` и вливать их в
`main` только через приёмку. Практика показала обратное: агенты пишут строку в журнал
(часто в *своей* копии журнала), а ветка остаётся на `origin` и из `main` не видна —
работа «существует», но её никто не может проверить, а строки журнала создают иллюзию
закрытой задачи. Этот аудит фиксирует реальное состояние на срез `main` = `e0ae8c4`
(03.10.2026) и даёт материал для решений владельцам ветвей.

**Как воспроизвести** (git ≥ 2.36, нужен `%(ahead-behind:)`):

```bat
py -3 scripts/queue_audit.py --md
```

Скрипт сравнивает `main` с каждым `origin/*` (`git diff --name-only main...<ref>`),
классифицирует изменённые файлы и пишет `build/queue_audit.txt` + `build/queue_audit.md`.
Колонка «Самообъявление» = имя ветки встречается в *её собственной* копии
`docs/AGENTS_STATUS.md` (то есть агент объявил себя, но содержимое осталось в ветке).

## Сводка

| Показатель | Значение |
|---|---|
| Веток на `origin` (без `main`/`HEAD`) | 144 |
| Без уникальных коммитов (дубликаты/пустые) | 90 — кандидаты на гигиену `origin` |
| С коммитами вне истории `main` | 54 |
| — затрагивают код (`src/`, `main.c`, `Makefile`, `.ioc`) | 16 |
| — из них широкий «safety» (`main.c`/`Makefile`/`.ioc`) | **9** (см. тип `код (safety)` в таблице) |
| — из них строго модули защиты/привода (`src/foc.c`, `src/adc_dispatch.c`) | **2** (`ai2/vf-overshoot-stability`, `no8-mapvec-policy`) |
| — только строка журнала (содержимого нет) | 3 |
| — упомянуты в журнале `main` | 10 |
| — объявили себя в своей копии журнала | 18 |

**Типы в таблице:** `код (safety)` — правки модулей защиты/привода (`foc/protect/vf/
adc/adc_dispatch/pwm`) или `main.c`/`Makefile`/`.ioc`; `код` — прочие `src/*.c`;
`docs` — только `docs/`, `tools/`, `tests/`, `scripts/`; `только-журнал` — единственный
отличающийся файл `docs/AGENTS_STATUS.md` (полезного содержимого нет).

## Полная таблица (54 ветки)

Срез `main` = e0ae8c4 (2026-10-03). Веток на `origin`: **144**; без уникальных коммитов: **90**; с коммитами вне истории `main`: **54** (затрагивают код: **16**, из них safety — **9** в широком смысле (`main.c`/`Makefile`/`.ioc`), строго модули защиты/привода — **2**).

| Ветка | Коммитов вне main | В журнале main | Самообъявление | Файлов | Тип | Ключевые файлы |
|---|---:|---|---|---:|---|---|
| `ai2/vf-telemetry-rate-fix` | 2 | да | — | 13 | код (safety) | Makefile, main.c |
| `ai4/bench-test2-fault-aware` | 5 | да | да | 11 | код (safety) | main.c |
| `ai4/mapcap-fault-detail` | 1 | да | да | 7 | код (safety) | main.c |
| `ai2/oi-preflight-makefile` | 2 | — | да | 2 | код (safety) | Makefile |
| `ai2/tz2-ls-standstill-step` | 4 | — | — | 6 | код (safety) | main.c |
| `ai2/tz3-mapcap-rec-fields` | 1 | — | — | 4 | код (safety) | main.c |
| `ai2/vf-contract-telemetry` | 1 | — | — | 12 | код (safety) | Makefile, main.c |
| `ai2/vf-overshoot-stability` | 14 | — | да | 30 | код (safety) | main.c, src/adc_dispatch.c, src/adc_dispatch.h, src/foc.c, src/foc.h, src/protect.c, src/protect.h |
| `no8-mapvec-policy` | 15 | — | — | 8 | код (safety) | main.c, src/foc.c, src/foc.h |
| `ai2/tz2-physical-qualification` | 6 | да | — | 5 | код | src/current_map_selector.c |
| `ai-cascade/bench-map-profile-release` | 1 | — | да | 2 | код | src/map_capture_profiles.c |
| `ai/real-board-auto-characterization` | 8 | — | — | 5 | код | src/map_real_board_profile.c, src/map_real_board_profile.h |
| `ai2/foc-region-diagnosis` | 6 | — | — | 9 | код | src/map_region_certifier.c, src/map_region_certifier.h |
| `ai2/tz2-ls-probe-service-path` | 6 | — | — | 3 | код | src/autotune.c |
| `ai4/foc-first-start-pc3` | 14 | — | — | 3 | код | src/cli.c |
| `ai4/map-region-geometry-revision` | 25 | — | — | 13 | код | src/current_map_selector.c, src/current_map_selector.h, src/map_artifact_decoder.c, src/map_artifact_decoder.h, src/map_candidate.c, src/map_region_certifier.c |
| `ai2/60v-ingest-waiver` | 1 | да | да | 1 | только-журнал | — |
| `ai2/acs712-noise-source-hunt` | 16 | да | да | 15 | docs | — |
| `ai2/acs712-scope-foc-map` | 20 | да | да | 25 | docs | — |
| `ai2/map-cal-sig-stable` | 1 | да | да | 1 | только-журнал | — |
| `ai2/tzref01-phase0-fix` | 14 | да | да | 13 | docs | — |
| `ai4/bench-pc3-test2-automation` | 1 | да | да | 5 | docs | — |
| `ai-cascade/schematic-inv1-fix` | 1 | — | да | 2 | docs | — |
| `ai2/boar-amplitude-zones` | 5 | — | да | 10 | docs | — |
| `ai2/cubemx-hygiene` | 2 | — | — | 5 | docs | — |
| `ai2/ls-offline-analysis` | 2 | — | — | 4 | docs | — |
| `ai2/map-ref-1a-3a-acs712-5a` | 21 | — | — | 15 | docs | — |
| `ai2/map-scope-refw` | 2 | — | да | 4 | docs | — |
| `ai2/mapcap-identity-cli` | 1 | — | да | 1 | только-журнал | — |
| `ai2/tz-ls-analyzer-fix1` | 1 | — | — | 1 | docs | — |
| `ai2/tz-ls-observability` | 1 | — | — | 1 | docs | — |
| `ai2/tz-ls-observability-revB` | 3 | — | — | 2 | docs | — |
| `ai2/tz-ls-step-analyzer` | 23 | — | — | 3 | docs | — |
| `ai2/tz-phase-reference-impl` | 2 | — | — | 2 | docs | — |
| `ai2/tz-phase-reference-ingest` | 4 | — | — | 5 | docs | — |
| `ai2/tz2-audit-fresh-start` | 5 | — | — | 5 | docs | — |
| `ai2/tz2-audit-required` | 6 | — | — | 5 | docs | — |
| `ai2/tz2-baseline-pin` | 1 | — | — | 1 | docs | — |
| `ai2/tz2-gate-pdump-fix` | 10 | — | — | 21 | docs | — |
| `ai2/tz2-identity-intake` | 2 | — | — | 2 | docs | — |
| `ai2/tz2-m0-repeatability` | 1 | — | — | 3 | docs | — |
| `ai2/tz2-manifest-lf` | 1 | — | — | 3 | docs | — |
| `ai2/tz2-probe-single-modulating` | 2 | — | — | 1 | docs | — |
| `ai2/tz2-protocol-calibration` | 3 | — | — | 3 | docs | — |
| `ai2/tz2-repeatability-audit-link` | 4 | — | — | 5 | docs | — |
| `ai2/tz2-repeatability-fix` | 2 | — | — | 5 | docs | — |
| `ai2/tz2-repeatability-invalid` | 3 | — | — | 5 | docs | — |
| `ai2/tz2-variant-writer` | 8 | — | — | 21 | docs | — |
| `ai2/tz3-map-phase-reference-contract` | 1 | — | — | 1 | docs | — |
| `ai2/tz3-mapcap-association-review` | 1 | — | — | 1 | docs | — |
| `ai2/tz3-mapcap-evidence-contract` | 2 | — | — | 2 | docs | — |
| `ai2/tzref01-phase0-floor-f4` | 15 | — | да | 14 | docs | — |
| `ai2/vbus-r5-closure` | 1 | — | да | 2 | docs | — |
| `ai4/bench-pc3-test2-docs` | 1 | — | да | 5 | docs | — |

## Выводы и рамки решения

**A. Safety-модули (2 ветки) — вливать только по явному ТЗ владельца.**
`ai2/vf-overshoot-stability` (14 коммитов, `src/foc.c/.h`, `src/protect.c/.h`,
`src/adc_dispatch.c/.h`, `main.c` — 30 файлов) и `no8-mapvec-policy` (15 коммитов,
`src/foc.c/.h`). Затрагивают регулятор и защиту: приёмщик такие пакеты сам не вливает
и не переписывает (регламент, §«safety-модули — только по явному ТЗ»).

**B. `main.c` / `Makefile` / `.ioc` без safety-модулей (7 ветвей)** —
`ai2/vf-telemetry-rate-fix` (база образов стендового комплекта ПК-3 v2/v4!),
`ai4/bench-test2-fault-aware`, `ai4/mapcap-fault-detail`, `ai2/oi-preflight-makefile`,
`ai2/tz2-ls-standstill-step`, `ai2/tz3-mapcap-rec-fields`, `ai2/vf-contract-telemetry`.
Важно: `ai4/mapcap-fault-detail` и `ai4/bench-test2-fault-aware` описаны в журнале
`main` как завершённые (раздел ③), но их коммитов в `main` нет — при проверке наличия
пакета в `main` на это нельзя ссылаться.

**C. Прочий код (7 ветвей)** — `map_*`-инструменты и `src/cli.c`/`src/autotune.c`:
`ai2/tz2-physical-qualification`, `ai-cascade/bench-map-profile-release`,
`ai/real-board-auto-characterization`, `ai2/foc-region-diagnosis`,
`ai2/tz2-ls-probe-service-path`, `ai4/foc-first-start-pc3`,
`ai4/map-region-geometry-revision`. Код вне safety-модулей, но меняет поведение →
нужна приёмка (тест/прогон), решение за владельцем.

**D. Документ-пакеты (35 ветвей)** — `docs/tools/tests/scripts` только; могут вливаться
как docs-пакеты. `ai2/tzref01-phase0-fix`, `ai2/acs712-scope-foc-map`,
`ai2/acs712-noise-source-hunt` уже описаны в журнале как источник стендовых прогонов
ПК-3 — их содержимое (скрипты анализа) полезно иметь в `main`.

**E. `только-журнал` (3 ветки)** — `ai2/60v-ingest-waiver`, `ai2/map-cal-sig-stable`,
`ai2/mapcap-identity-cli`: единственное отличие от `main` — строка
`docs/AGENTS_STATUS.md`. Содержимого нет → вливать нечего, строки уже присутствуют в
журнале `main` (раздел ②). Это чистые кандидаты на закрытие (удаление с `origin`).

**F. 90 ветвей без уникальных коммитов** — дубликаты (их содержимое уже в `main` или
ветка — предок `main`): `ai2/pc3-bench-kit-v3/v4`, `ai3/pc3-bench-kit-v2`,
`ai-koda/adc-vbus-mismatch-report` (влита 03.10.2026), `ai2/vf-*`/`ai4/bench-test2-*`
серии и др. Полный список — в `build/queue_audit.txt` (этот файл не в git: `build/`
в `.gitignore`; воспроизводится одной командой `py -3 scripts/queue_audit.py --md`).

**G. Дрейф регламента (обнаружено при аудите; к очереди ветвей не относится).**
`AGENTS.md` (раздел «Совместная работа») утверждает: «push в main — только через приёмку
(hook main-guard блокирует)». Фактически hook `main-guard` снят: установленный
`.git/hooks/pre-push` содержит комментарий «main-guard УБРАН — прямой push в main
разрешён (AGENTS_WORKFLOW §1, упрощено 28.08.2026)»; `docs/AGENTS_WORKFLOW.md` §1
предписывает «правь, коммить, пуши в `main` напрямую», §3 — приёмка только для крупных
пакетов, §4 — `make && make test` только для правок кода. Риск: агент, читающий только
`AGENTS.md`, ищет «процесс приёмки» там, где достаточно прямого push, и задерживает
публикацию. Правка регламента (одна строка в `AGENTS.md`) — за владельцем.

## Сопутствующий ремонт журнала (найден при аудите)

Работая по очереди ветвей, обнаружены три дефекта рендера `docs/AGENTS_STATUS.md`
(данные пропадали из рендера, файл при этом оставался «валидным» в git):

1. **Пустые строки внутри таблиц** — в GFM пустая строка закрывает таблицу, строки
   после разрыва рендерились текстом, а не таблицей. Удалено 47 строк → все 4 таблицы
   (①–④) непрерывны (`a853ce3`).
2. **Переполнение колонок** — неэкранированные `\|` внутри текста дают «лишние» ячейки
   (строки с 8 и 26 ячейками), GFM отбрасывает лишние ячейки вместе с данными. Палки
   экранированы, текст строк не изменён (`a853ce3`).
3. **Строка-сирота** — строка `ai2 (Hermes) v7-return` стояла выше заголовка ①, вне
   блока строк с разделителем (рендерилась абзацем). Перенесена в ① под разделитель,
   текст байт-в-байт не изменён (`15498c6`).

Проверки после ремонта: 4 таблицы непрерывны (разделители 22/80/98/108), 183 строки
таблиц по 6 ячеек, «сиротских» блоков 0, CRLF без BOM, маркеров конфликта 0
(`build/verify_status.py`, `build/move_orphan_row.py` — выходят с кодом 0).
Остаточное ограничение: 10 строк раздела ④ в источнике имеют 4 ячейки вместо 6
(GFM дополняет их пустыми) — это pre-existing дефект данных, содержимое не терялось.

## Ограничения аудита

1. Сравнивается **история коммитов**, а не содержимое: ветка может быть фактически
   закрыта (её изменения уже в `main` другим коммитом/чери-пиком), оставаясь «впереди»
   по истории. Перед закрытием такой ветки нужно сверить содержимое
   (`git diff main...<ref>` по существенным файлам).
2. «Упоминание в журнале `main`» не означает влитие: строка может быть из разделов
   ②/③ («в работе» / «завершено») и описывать *результат прогона*, а не пакет.
3. Аудит не отменяет решения владельца: он лишь показывает, где заявленное расходится
   с историей `main`. Приёмщик вливает кодовые пакеты только по ТЗ и после проверки
   (pytest/CI/прогон), safety-модули — только по явному ТЗ.
4. Числа привязаны к срезу `e0ae8c4` (03.10.2026) и меняются при каждом merge —
   перезапускать `py -3 scripts/queue_audit.py --md` перед использованием.

## См. также

- `docs/ORIGIN_QUEUE_TRIAGE_20261003.md` — **триаж той же очереди**: что изменится в
  `main`, если ветку влить (сухой merge `git merge-tree --write-tree`, дельта влития,
  конфликтные файлы, пересечения ветвей по файлам) и раскладка 54 ветвей по решениям
  «закрыть / только по ТЗ / приёмка / doc-пакет». Инструмент — `scripts/queue_triage.py`.
- Метрики двух документов разные и это не расхождение: аудит считает **свои файлы ветви**
  (`git diff main...<ref>`), триаж — **дельту влития в текущий `main`** (сухой merge) и
  число конфликтов. Поэтому «код (safety)» в аудите — 9 ветвей (широкий признак
  `main.c`/`Makefile`/`.ioc`), а корзина `safety` в триаже — 7 (у двух ветвей merge
  фактически не меняет code-файлы `main`: напр. `ai4/mapcap-fault-detail` по содержимому
  уже влито, поэтому его дельта — только строка журнала); «только-журнал» — 3 в аудите
  (срез `e0ae8c4`) и 4 в триаже (срез `f2c084a`). Перед решением сверяться с более
  свежим документом.
