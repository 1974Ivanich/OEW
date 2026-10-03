# Триаж очереди веток `origin` — что войдёт в `main`, если ветку влить (03.10.2026)

**Зачем.** `docs/ORIGIN_QUEUE_AUDIT_20261003.md` показал, что 54 ветви на `origin`
содержат коммиты вне `main`. Аудит отвечает на вопрос «что ветка *заявляет*» — считает
её собственные файлы (`git diff --name-only main...<ref>`). Этот документ отвечает на
второй вопрос: **что изменится в `main`, если ветку влить**, и раскладывает все 54 ветви
по корзинам решений: закрыть / только по ТЗ / приёмка / doc-пакет.

**Метод (рабочее дерево не меняется).** Для каждой ветви выполняется *сухой merge*:
`git merge-tree --write-tree main origin/<ветка>` (git ≥ 2.38) — Git считает результат
влития в object DB (ссылки не создаются, индекс и рабочее дерево не трогаются). Затем
`git diff --name-status main <дерево>` даёт точную **дельту влития** (что реально
изменится в `main`), а stage-строки `1/2/3` в выводе — список **конфликтных** файлов.
Скрипт: `scripts/queue_triage.py` (git ≥ 2.36 для `%(ahead-behind:)`, git ≥ 2.38 для
`merge-tree --write-tree`; проверено на git 2.45.1).

**Как воспроизвести:**

```bat
py -3 scripts/queue_triage.py --md     :: build/queue_triage.txt + build/queue_triage.md
```

**Чего нет в аудите и есть здесь.** 20 ветвей несут *свою* копию
`docs/AGENTS_STATUS.md` (журнал в `main` отремонтирован 03.10.2026: убраны разрывы
таблиц, экранированы переполнения) — merge вернёт либо конфликт, либо чужой отпечаток
журнала; 73 файла приносят ≥2 ветви; у 22 ветвей влитие добавляет только новые файлы и
не конфликтует ни в одном. Именно эти три факта определяют порядок работы.

## Сводка

| Корзина | Ветвей | Что значит | Действие |
|---|---:|---|---|
| `только-журнал` | 4 | влитие меняет только строку `docs/AGENTS_STATUS.md` | закрыть; строку перенести вручную, только если её нет в `main` |
| `safety` | 7 | модули защиты/привода (`foc/protect/vf/adc*`), `main.c`, `Makefile`, `.ioc` | только по явному ТЗ владельца |
| `код` | 7 | прочие `src/*` | публикация + CI + приёмка |
| `docs` | 36 | `docs/`, `tools/`, `tests/`, `scripts/` | doc-пакет: вливать по сверке |
| без уникальных коммитов | 90 | содержимое уже в `main` (отличается только история) | гигиена `origin` — решение владельца |

Дополнительные срезы по 54 ветвям:

| Показатель | Значение |
|---|---:|
| Сухой merge **без конфликтов** | 28 |
| Сухой merge **с конфликтами** | 26 |
| Влитие добавляет только новые файлы и не конфликтует | 22 (кандидаты на пакетное влитие) |
| Влитие меняет `docs/AGENTS_STATUS.md` | 20 (20 разных версий журнала) |
| Имя ветви уже присутствует в журнале `main` | 10 |
| Файлов, которые приносят ≥2 ветви | 73 |

## Ключевые находки

1. **`ai4/mapcap-fault-detail`** (5 коммитов «по журналу», 25.08) — по содержимому уже в
   `main`: сухой merge трогает **только строку журнала**. Это закрытие без потерь, а не
   «непринятый пакет».
2. **`ai2/vf-overshoot-stability`** — самый тяжёлый случай: 29 файлов, **9 конфликтов**
   (`src/foc.c`, `src/foc.h`, `src/vf_control.c/.h`, `.gitignore`, `docs/AGENTS_STATUS.md`,
   3 теста) при 334 коммитах отставания. Вливать целиком нельзя — только переиздание
   от свежего `main` по явному ТЗ.
3. **`no8-mapvec-policy`** (28.09, 15 коммитов, `src/foc.c/.h`, 0 конфликтов) — самый
   чистый safety-кандидат: мержится без конфликтов, но затрагивает FOC → нужен ТЗ.
4. **Семейство `ai2/tz2-*` (12 ветвей)** делит одни и те же файлы:
   `tools/m0_repeatability.py` и `tests/test_m0_repeatability.py` — по 6 ветвей (5 разных
   версий содержимого), `docs/TZ2_M0_BASELINE_LEVELS.md` — 6 ветвей (3 версии),
   `tools/audit_m0_run.py` — 4 ветви. Вливать надо **по одной ветви семейства**, остальные
   закрыть после сверки, иначе второй merge откатит первый.
5. **Кластер TZ-REF-01 / ACS712 (5 ветвей)** — `tools/scope_acs712_capture.py` и
   `docs/TZ_REF_01_DRAFT_INDEPENDENT_CURRENT_REFERENCE.md` в 4 версиях, плюс конфликт в
   `.github/workflows/ci.yml`. Выбрать одну (самая полная — `ai2/acs712-scope-foc-map`,
   25.09, 25 файлов, включает `docs/pc3_package/`), остальные — история.
6. **`ai4/bench-test2-*` (3 ветви, 25.08)** — конфликтуют в `README.md` и
   `tools/bench_test2_capture.*` (по 5 конфликтов каждая): влитие = ручной разбор против
   сегодняшнего `main`, выгоднее переиздание.
7. **Стендовая зависимость.** У 10 ветвей имя уже в журнале `main`, но коммитов и
   содержимого в `main` нет (напр. `ai2/vf-telemetry-rate-fix`, `ai2/acs712-scope-foc-map`,
   `ai2/tzref01-phase0-fix`, `ai4/bench-pc3-test2-automation`). Часть из них описана в
   журнале как база стендовых прогонов ПК-3 — то есть прогоны опирались на код/скрипты,
   которых в `main` не было. Это отдельный риск воспроизводимости, а не только гигиена
   очереди.
## ① Закрыть без потерь — 4 ветви (`только-журнал`)

Влитие каждой из них меняет в `main` ровно один файл — `docs/AGENTS_STATUS.md`.
Содержимого (кода, скриптов, документов пакета) в ветви нет: коммиты 08.09.2026 — это
записи «merged to main», сделанные после влития самих пакетов.

| Ветка | Коммитов | Дата | Влитие меняет | Имя уже в журнале `main` | Действие |
|---|---:|---|---|---|---|
| `ai4/mapcap-fault-detail` | 1 | 25.08 | журнал (1 M) | да | закрыть: содержимое (`main.c`) уже в `main` |
| `ai2/60v-ingest-waiver` | 1 | 08.09 | журнал (1 M) | да | закрыть: строка в `main` уже есть |
| `ai2/map-cal-sig-stable` | 1 | 08.09 | журнал (1 M) | да | закрыть: строка в `main` уже есть |
| `ai2/mapcap-identity-cli` | 1 | 08.09 | журнал (1 M) | **нет** | перед закрытием перенести одну строку в `main` (или закрыть, если запись не нужна) |

## ② Safety: только по явному ТЗ — 7 ветвей

Затрагивают регулятор/защиту (`src/foc.*`, `src/vf_control.*`, `src/adc_dispatch.*`,
`src/protect.*`), `main.c`/`Makefile`. Регламент: такие пакеты приёмщик сам не вливает и
не переписывает — нужно ТЗ владельца. Колонка «Дельта влития» — `M/A/D` по сухому merge.

| Ветка | Коммитов вне main | Дельта влития | Конфликты | Что приносит | Вердикт |
|---|---:|---|---:|---|---|
| `no8-mapvec-policy` | 15 | 8 (8/0/0) | 0 | `src/foc.c/.h` (mapvec frame policy: miss карты ≠ stop FOC), `main.c`, `src/cli.c`, 3 теста | 28.09 — самый свежий и единственный **без конфликтов**; кандидат №1 на ТЗ и приёмку |
| `ai2/tz2-ls-standstill-step` | 4 | 6 (6/0/0) | 0 | `src/autotune.c/.h`, `src/cli.c/.h`, `main.c`, `tests/cli_test.c` (VBUS в предпроверке LsStep) | 19.09, мержится чисто; ТЗ (затрагивает `main.c`) |
| `ai2/vf-contract-telemetry` | 1 | 12 (11/1/0) | 1: `Makefile` | `src/vf_control.c/.h`, `main.c`, `src/cli.c/.h`, `telem_parser.py`, `tests/pwm_oew_differential_test.c` (новый) | 24.09; перед вливанием решить конфликт Makefile вручную |
| `ai2/vf-telemetry-rate-fix` | 2 | 13 (12/1/0) | 1: `Makefile` | `@VFLOG` 25 Гц (40 мс), единый формат строки, `src/telemetry_format.h` | 24.09; в журнале `main` описан как база стендового комплекта ПК-3 — уточнить применимость после сегодняшнего `main` |
| `ai2/tz3-mapcap-rec-fields` | 1 | 4 (3/1/0) | 1: `scripts/cubemx_check_script.txt` | `@MC:REC` несёт `sec/win/ts`, `tools/mapcap_uart_export.py` | 20.09; конфликтный файл — артефакт генератора CubeMX, решается удалением/регенерацией |
| `ai2/oi-preflight-makefile` | 2 | 2 (2/0/0) | 2: `Makefile`, журнал | преflight-цель в `Makefile` | 30.08, 336 коммитов отставания: выгоднее переиздать цель на сегодняшнем `Makefile` |
| `ai2/vf-overshoot-stability` | 14 | 29 (18/11/0) | **9** | `src/foc.c/.h`, `src/protect.c/.h`, `src/adc_dispatch.c/.h`, `src/vf_control.c/.h`, `main.c`, `src/uart.c`, 11 докладов/ТЗ в `docs/` | **вливать целиком нельзя**: 9 конфликтов, среди них регулятор и защита; только переиздание от свежего `main` по ТЗ |

## ③ Код вне safety-модулей: публикация + приёмка — 7 ветвей

`src/*` (кроме `foc/protect/vf/adc/pwm`) и `tools/*.c`. Меняют поведение, но не модули
защиты/привода → нужен пакет + CI + решение владельца.

| Ветка | Коммитов | Дельта влития | Конфликты | Что приносит | Вердикт |
|---|---:|---|---:|---|---|
| `ai/real-board-auto-characterization` | 8 | 5 (0/5/0) | 0 | новые `src/map_real_board_profile.c/.h`, `tests/test_map_auto_characterize.py`, `tools/map_auto_characterize.{py,md}` | 25.08, 452 отставания: переиздание тестов/скрипта на сегодняшних интерфейсах |
| `ai2/tz2-ls-probe-service-path` | 6 | 3 (2/1/0) | 0 | `TZ_CHANNEL_PROBE_SERVICE_PATH.md` (новый), `src/autotune.c`, `tests/cli_test.c` (probe rev7: один модулирующий инвертор) | мержится чисто; пакет + прогон |
| `ai4/foc-first-start-pc3` | 14 | 3 (3/0/0) | 0 | `docs/TZ_FOC_FIRST_START_PC3.md`, `src/cli.c`, `tests/cli_test.c` (загрузка карты через `tools/map_upload.py`) | мержится чисто; пакет + прогон |
| `ai2/foc-region-diagnosis` | 6 | 4 (2/2/0) | 2: `docs/AGENTS_STATUS.md`, `src/map_region_certifier.c` | `TZ_FOC_REGION_TOO_NARROW.md`, `docs/MAP_GEOMETRY_REVISION_REVIEW.md` (новые) + правка `map_region_certifier.c` | перед вливанием решить 2 конфликта вручную |
| `ai4/map-region-geometry-revision` | 25 | 13 (8/5/0) | 1: `src/current_map_selector.c` | `src/current_map_selector.*`, `src/map_artifact_decoder.*`, `src/map_candidate.c`, `src/map_region_certifier.c` + 5 новых тестов/скриптов и workflow `map-geometry-revision.yml` | конфликт в основном файле пакета: переиздание от свежего `main` |
| `ai2/tz2-physical-qualification` | 6 | 5 (5/0/0) | **5**: `docs/MAP_ADMISSION_PIPELINE_AUDIT_20260914.md`, `docs/TZ2_P0_P1_BENCH_PROTOCOL.md`, `docs/TZ2_PHYSICAL_CURRENT_MAP_QUALIFICATION.md`, `src/current_map_selector.c`, `tests/current_map_selector_test.c` | протокол P0/P1 + правка селектора карты | целиком не вливать (5 конфликтов): переиздание |
| `ai-cascade/bench-map-profile-release` | 1 | 2 (2/0/0) | 1: `docs/AGENTS_STATUS.md` | `src/map_capture_profiles.c` (синтетический профиль стенда через `OEW_MAP_BENCH_PROFILE`) | 30.08, 336 отставания: сверить с текущим `map_capture_profiles.c`, иначе переиздать |

## ④ Doc-пакеты: вливать по сверке — 36 ветвей

### ④a. Чистые добавления: 0 конфликтов, только новые файлы — 21 ветвь

Самые безопасные к вливанию: сухой merge не трогает ни одного существующего файла в
`main`. Ограничение — те же файлы приносят соседние ветви семейства (см. ④c и ④e).

| Ветка | Коммитов | Новых файлов | Что приносит |
|---|---:|---:|---|
| `ai2/tz2-gate-pdump-fix` | 10 | 21 | `docs/TZ2_M_TUNING_SESSION.md`, `tools/map_variant_writer.*`, `tools/map_variant_compare.*`, `tools/freeze_m0_baseline.py`, `tools/preflight_m0_burst.py`, `tools/session_manifest_*`, `tools/validate_session_manifest.py`, `tools/live_identity_from_log.py`, `tools/map_variant_cli.c`, 6 тестов |
| `ai2/tz2-variant-writer` | 8 | 21 | тот же набор (заморозка M0 baseline, «M1 не проектируется до freeze») — **дубль набора** `gate-pdump-fix` в 5 общих файлах |
| `ai2/tz2-audit-fresh-start` | 5 | 5 | `docs/TZ2_M0_BASELINE_LEVELS.md`, `tools/audit_m0_run.py`, `tools/m0_repeatability.py`, 2 теста (B10 — независимый старт прогона) |
| `ai2/tz2-audit-required` | 6 | 5 | тот же набор (R8 — процедурный вердикт обязателен; B10 FAIL = INVALID) |
| `ai2/tz2-repeatability-audit-link` | 4 | 5 | тот же набор (связка «процедурный аудит → статистика») |
| `ai2/tz2-repeatability-fix` | 2 | 5 | тот же набор (repeatability fail-closed) |
| `ai2/tz2-repeatability-invalid` | 3 | 5 | тот же набор (INVALID-прогон исключается из статистики, а не превращается в ноль) |
| `ai2/tz2-m0-repeatability` | 1 | 3 | `docs/TZ2_M0_BASELINE_LEVELS.md`, `tools/m0_repeatability.py`, тест |
| `ai2/tz2-manifest-lf` | 1 | 3 | `docs/TZ2_RETURN_MANIFEST_LF.md`, `tools/verify_manifest.py`, тест |
| `ai2/tz2-protocol-calibration` | 3 | 3 | `docs/TZ2_M_TUNING_PROTOCOL_ADDENDUM_C.md` (правило I12), `tools/identity_intake_check.py`, тест |
| `ai2/tz2-identity-intake` | 2 | 2 | `tools/identity_intake_check.py`, тест |
| `ai2/tz2-probe-single-modulating` | 2 | 1 | `TZ_PROBE_SINGLE_MODULATING_INVERTER.md` (rev7, docs-only) |
| `ai2/tz3-mapcap-evidence-contract` | 2 | 2 | `TZ_MAPCAP_EVIDENCE_CONTRACT.md`, `TZ_MAPCAP_MIN_CONTRACT.md` |
| `ai2/tz3-map-phase-reference-contract` | 1 | 1 | `TZ_MAP_PHASE_REFERENCE_CONTRACT.md` (контракт независимой фазовой референции y) |
| `ai2/tz3-mapcap-association-review` | 1 | 1 | `REVIEW_MAPCAP_ASSOCIATION.md` (ревизия доказательства sample↔sector↔CCR) |
| `ai2/tz-phase-reference-impl` | 2 | 2 | `TZ_PHASE_REFERENCE_IMPLEMENTATION.md` (E1…E6), `TZ_MAP_PHASE_REFERENCE_CONTRACT.md` |
| `ai2/ls-offline-analysis` | 2 | 4 | `docs/LS_OFFLINE_ANALYSIS.md`, `tools/autotune_idle_analyzer.py`, `tools/ls_observability_budget.py`, тест |
| `ai2/tz-ls-step-analyzer` | 23 | 3 | `tools/ls_step_analyze.py`, `TZ_for_web_AI_ls_step_analyzer.md`, тест |
| `ai2/tz-ls-analyzer-fix1` | 1 | 1 | `TZ_for_web_AI_ls_step_analyzer_FIX1.md` (фикс падения анализатора на `fit=None`) |
| `ai2/tz-ls-observability` | 1 | 1 | `TZ_LS_OBSERVABILITY.md` (наблюдаемость токового отклика внутри PWM-периода) |
| `ai2/tz-ls-observability-revB` | 3 | 2 | `TZ_LS_OBSERVABILITY.md` + `STEP0_SAMPLING_FEASIBILITY.md` (редакция B) |

### ④b. Остальные doc-пакеты, меняющие существующие файлы — 10 ветвей

| Ветка | Коммитов | Дельта влития | Конфликты | Что приносит | Вердикт |
|---|---:|---|---:|---|---|
| `ai2/tz2-baseline-pin` | 1 | 1 (1/0/0) | 0 | правка `docs/TZ2_P0_P1_BENCH_PROTOCOL.md` (пин базы до принятого `main` + sha256 образа) | мержится чисто; вливать вместе с пакетом tz2 |
| `ai2/vbus-r5-closure` | 1 | 2 (2/0/0) | 1: журнал | `docs/MOPT0_BASELINE_20260918.md` (закрытие пункта VBUS-транзиента) | вливать без журнала (строку — вручную) |
| `ai-cascade/schematic-inv1-fix` | 1 | 2 (2/0/0) | 0 | `docs/schematic.html` (STEVAL-IPM20B No1: порты TP23 SD1/EN1, полигон) | 30.08, 336 отставания: сверить с текущим `schematic.html` |
| `ai2/boar-amplitude-zones` | 5 | 10 (1/9/0) | 1: журнал | `TZ_MAP_IDENTITY_QUALIFICATION_CONTRACT.md`, `boar_geometry_dataset.txt`, `docs/BOAR_*`, `docs/FIRMWARE_TRANSITION_SAMPLING_SPEC.md`, `tools/geometry_partition_audit.py`, `tools/verify_boar_geometry_dataset.py`, 2 теста | пакет «amplitude zones»; вливать без журнала |
| `ai2/cubemx-hygiene` | 2 | 4 (3/1/0) | 2: `.gitignore`, `scripts/cubemx_check.py` | `tests/test_cubemx_hygiene.py` (новый), правки `scripts/cubemx_check.py`, `docs/templates/.../README_ACS712_NOHV_PC3.md`, `.gitignore` | конфликты в инструменте самоконтроля — сверить с текущим `cubemx_check.py` |
| `ai2/tz-phase-reference-ingest` | 4 | 5 (3/2/0) | 1: `tools/map_scope_ingest.py` | `TZ_PHASE_REFERENCE_IMPLEMENTATION.md`, `TZ_MAP_PHASE_REFERENCE_CONTRACT.md`, правки `tools/map_scope_ingest.{py,md}` + тест | конфликт в ingestion-скрипте (E5 не пропускается молча) — вручную |
| `ai2/map-scope-refw` | 2 | 4 (4/0/0) | 3: журнал, `tools/map_scope_ingest.md`, `tools/map_scope_ingest.py` | фикс `check_evidence` (`abs(None)`) в `tools/map_scope_ingest.py` + тест | 01.09, 307 отставания: фикс, вероятно, уже перекрыт `main`; проверить `git log main -- tools/map_scope_ingest.py`, иначе переиздать |
| `ai4/bench-pc3-test2-automation` | 1 | 5 (5/0/0) | **5**: `README.md`, журнал, `tests/test_bench_test2_capture.py`, `tools/bench_test2_capture.md`, `tools/bench_test2_capture.py` | автоматизация UART+sigrok для test2 | 25.08, 463 отставания: целиком не вливать — переиздание |
| `ai4/bench-pc3-test2-docs` | 1 | 5 (3/2/0) | 2: `README.md`, журнал | `docs/BENCH_PC3_TEST2_NOHV.md`, `docs/templates/TEST2_NOHV_PROTOCOL_PC3.md` (новые) + правки `README.md`, `docs/BENCH_NEXT_STEP_PLAN_20260824.md` | 25.08: новые файлы можно взять точечно, `README.md` — вручную |
| `ai4/bench-test2-fault-aware` | 5 | 5 (5/0/0) | **5**: `README.md`, журнал, `tests/test_bench_test2_capture.py`, `tools/bench_test2_capture.md`, `tools/bench_test2_capture.py` | «fault-aware» артефакты test2; в журнале `main` описан как завершённый | целиком не вливать: в `main` этих коммитов нет, содержимое пересекается с `bench-pc3-test2-automation` |

### ④c. Кластер TZ-REF-01 / ACS712 — 5 ветвей одного семейства

Все пять приносят одни и те же файлы в разных версиях:
`tools/scope_acs712_capture.py`, `tools/scope_acs712_selftest.py`,
`tools/acs712_ingest_acceptance.py`, `docs/TZ_ACS712_SCOPE_FOC_CURRENT_MAP.md`,
`docs/TZ_REF_01_DRAFT_INDEPENDENT_CURRENT_REFERENCE.md`, `docs/TZ-REF-01-2026-09-24-TAO3104A/*`
и правку `.github/workflows/ci.yml` (конфликтует с `main` у четырёх из пяти).

| Ветка | Коммитов | Дельта влития | Конфликты | Особенность |
|---|---:|---|---:|---|
| `ai2/acs712-scope-foc-map` | 20 | 25 (3/22/0) | 1: `ci.yml` | **самая полная** (25.09): плюс `docs/pc3_package/` (`PC3_BENCH_PROTOCOL.md`, `PC3_PHASE0_INSTRUCTION.md`, `tao3104a_cap.py`) — брать как основу |
| `ai2/map-ref-1a-3a-acs712-5a` | 21 | 15 (4/11/0) | 1: `ci.yml` | 25.09: карта каналов ACS712, `tests/test_acs712_5a_map_reference.py` |
| `ai2/acs712-noise-source-hunt` | 16 | 15 (3/12/0) | 2: `ci.yml`, журнал | 24.09: HOLD количественной ветки + ТЗ поиска источника шума, `tests/test_tzref01_phase0_floor.py` |
| `ai2/tzref01-phase0-floor-f4` | 15 | 14 (3/11/0) | 2: `ci.yml`, журнал | 24.09: приёмочный noise floor только по reference-каналам (F4) |
| `ai2/tzref01-phase0-fix` | 14 | 13 (3/10/0) | 2: `ci.yml`, журнал | 24.09: ERRATA к примеру PC-1, факты проверки отчёта ПК-3 |

**Решение кластера:** вливать **одну** ветвь (по объёму и дате — `ai2/acs712-scope-foc-map`),
остальные четыре закрыть как историю итераций. Иначе: сначала влитая ветвь попадёт под
конфликт/перезапись при влитии следующей (4–5 версий одного и того же скрипта).

### ④d. Правило «один файл — один раз»

73 файла приносят ≥2 ветви. Крупнейшие пересечения (полная таблица — в
`build/queue_triage.md`, раздел «Пересечения»):

| Файл | Ветвей | Разных версий | Кластер |
|---|---:|---:|---|
| `docs/AGENTS_STATUS.md` | 20 | 20 | все документные ветви (никогда не merge'ить — только перенос строки) |
| `tests/cli_test.c` | 7 | 5 | safety/код: `tz2-ls-standstill-step`, `vf-contract-telemetry`, `vf-overshoot-stability`, `vf-telemetry-rate-fix`, `no8-mapvec-policy`, `tz2-ls-probe-service-path`, `foc-first-start-pc3` |
| `main.c` | 6 | 6 | safety-ветви (5) + `tz2-ls-standstill-step` |
| `tests/test_m0_repeatability.py`, `tools/m0_repeatability.py` | 6 | 5 | семейство tz2 |
| `docs/TZ2_M0_BASELINE_LEVELS.md` | 6 | 3 | семейство tz2 |
## ⑤ Ветви без уникальных коммитов — 90 (гигиена `origin`)

Содержимое этих ветвей уже в `main` (ветвь — предок `main` либо её правки влиты другим
коммитом), уникальных коммитов у них нет — вливать нечего. Полный список: раздел «B» в
`build/queue_audit.txt` / `build/queue_triage.txt` и §F документа аудита. Удаление
безопасно с точки зрения содержимого `main`, но лишает истории прогонов — решение
владельца. 4 ветви из ① закрываются по той же причине, итого «закрытие» касается
94 из 144 ветвей на `origin`.

## Порядок исполнения (для приёмщика)

1. **Никогда не merge'ить ветвь целиком, если в дельте есть `docs/AGENTS_STATUS.md`**
   (20 ветвей; 20 разных версий журнала). Журнал в `main` отремонтирован 03.10.2026 —
   merge вернёт конфликт или чужой отпечаток. Содержимое брать точечно
   (`git checkout origin/<ветка> -- <файлы>` **без** журнала), строку журнала добавлять
   вручную в отремонтированную таблицу.
2. **Критерий проверки — дельта влития, а не список файлов ветви.** До и после влития
   полезно сверить `build/queue_triage.md`: если после merge дельта изменилась не так,
   как ожидалось, — остановиться и разобрать.
3. **Один файл — один раз** (④d). Для семейств (`tz2-*`, TZ-REF-01/ACS712): сначала
   выбрать ветвь-основу, остальные закрыть как историю итераций.
4. **Safety (②) — только по явному ТЗ владельца**; приёмщик такие пакеты не переписывает.
5. **Код (③) и doc-пакеты (④a)** — пачками по 3–5 ветвей с одним прогоном
   `make && make test` и одним CI-раном (по регламенту правка кода требует `make`);
   после каждого пакета — перезапуск `py -3 scripts/queue_triage.py --md`, иначе числа
   устареют (дельта пересчитывается от текущего `main`).
6. **Закрытие — только при пустой дельте влития** (`пусто-по-содержимому`) или
   `только-журнал`; в остальных случаях сначала вливается содержимое.

## Что нужно от владельца (решения)

| № | Решение | Объём | Приоритет |
|---|---|---|---|
| 1 | Закрыть ветви «только-журнал» (①) | 4 ветви | низкий риск; `ai2/mapcap-identity-cli` — перенести строку вручную |
| 2 | ТЗ по safety (②) | 7 ветвей | начать с `no8-mapvec-policy` (0 конфликтов, 28.09) и `ai2/tz2-ls-standstill-step` |
| 3 | Код вне safety (③) | 7 ветвей | 3 ветви мержатся без конфликтов (`ai/real-board-auto-characterization`, `ai2/tz2-ls-probe-service-path`, `ai4/foc-first-start-pc3`) |
| 4 | Doc-пакеты (④) | 36 ветвей | ④a — 21 ветвь готова к пакетному вливанию; по ④c выбрать одну ветвь |
| 5 | Гигиена `origin` (⑤) | 90 ветвей | после решений 1–4 |

## Ограничения

1. Сравнивается **результат влития в текущий `main`**, а не история: ветвь может быть
   фактически закрыта (правка уже в `main` другим коммитом) — тогда дельта пуста и ветвь
   попадёт в «пусто-по-содержимому» (среди 54 таких нет).
2. У конфликтных ветвей дельта содержит версии файлов с маркерами конфликта — это сигнал
   «нужен ручной разбор», а не готовый список содержимого.
3. `git merge-tree --write-tree` пишет в object DB только деревья результата (ссылок не
   создаёт, рабочее дерево и индекс не трогает); мусор убирается `git gc`.
4. Числа привязаны к срезу `main` = `f2c084a` (03.10.2026) и меняются при каждом merge:
   перед использованием — перезапустить `py -3 scripts/queue_triage.py --md`.
5. Документ не заменяет приёмку: решения по safety-модулям и срокам — за владельцем.

## См. также

- `docs/ORIGIN_QUEUE_AUDIT_20261003.md` — аудит «объявлено, но не влито» (что заявляет
  ветвь: свои файлы, самообъявление в журнале).
- `scripts/queue_audit.py` — генератор аудита; `scripts/queue_triage.py` — генератор
  этого триажа (сухой merge + дельта влития + конфликты + пересечения).



