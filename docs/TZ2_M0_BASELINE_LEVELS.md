# TZ-02: два уровня M0 baseline (reference vs repeatability)

Статус: governance-запись. Никакие ранее принятые артефакты задним числом не переоформляются.

## Уровни

```text
M0_REFERENCE_FROZEN        один прогон (M0-R1), проходит F1…F8
      │                    → неизменяемая точка отсчёта для инструментов и следующего шага
      └── НЕ repeatability qualification

M0_BASELINE_FROZEN         M0-R1…M0-R5, сведённые tools/m0_repeatability.py
                           → основание сравнивать M1 именно с M0, а не с одним запуском
```

Почему различие принципиально: пять прогонов нужны не для пятого «PASS», а чтобы измерить
**разброс** — `idc1/idc2`, повторяемость условий запуска, telemetry-derived параметры,
устойчивость capture pipeline и наличие единичных аномальных прогонов. Иначе изменение
коэффициентов карты сравнивалось бы с особенностями одного конкретного запуска.

## Текущее состояние

```text
M0-R1                       ACCEPTED (аудит A1…A9, гейт G0…G9 → PASS, заморозка F1…F8)
M0 reference artifact       FROZEN   (0x00E666F3 / sha256 cacf7b83…)
M0 repeatability            OPEN     (R2…R5 не выполнены)
M0 full baseline            NOT YET FROZEN
M1 design                   BLOCKED pending M0-R1…R5
G3 physical qualification   BLOCKED
```

Записи о заморозке не переименовывались (сохранена непрерывность хешей):

| Файл | Где | sha256 | Семантика по этой записи |
|---|---|---|---|
| `M0_BASELINE_FROZEN.json` (M0-R1) | каталог возврата сессии | `9786af3a3fc4d86f39692f3463d17ca12c4955c93fb45d63afe154bfb50c1f02` | **reference** уровень |
| `M0_BASELINE_FROZEN.json` (R1…R5) | выпускает `m0_repeatability.py --out-dir` | появляется после R5 | **baseline** уровень |

Имя файла у обоих уровней одинаковое — поэтому уровень определяется **каталогом и записью
`level`** внутри JSON: у baseline-записи есть поле `"level": "M0_BASELINE_FROZEN"` и список
прогонов; у reference-записи — `"schema_version": "tz2-m0-freeze-1"` и один `run_id`.

## Правила R2…R5 (проверяются инструментом, не дисциплиной)

```text
R1  одинаковый firmware_sha256 у всех прогонов (== 6d3ba902…)
R2  одинаковый artifact_sha256 и R2b — map_crc32 (== 0x00E666F3)
R3  одинаковая живая identity (все 11 полей; в частности ccs и arr)
R4  одинаковый конверт (vbus_target, current_limit, burst_duration, pause)
R5  каждый прогон внутренне чист: нет FAULT!=0, нет @BRK:valid=1, uart_drp/trunc=0,
    drain == число @MC:REC, dropped=0, t монотонен, stop_gate не срабатывал
R6  ≥ 5 прогонов с уникальными run_id
R7  аномалии (выброс разброса idc, числа записей, VBUS) либо отсутствуют,
    либо явно приняты: --acknowledge-anomaly M0-R3="<причина>"
```

Без R1…R7 запись уровня baseline **не выпускается** (fail-closed, код выхода 1).

**Никакой подстройки карты между R1 и R5**: меняются только `run_id`/время/оператор сессии.
Каждый прогон — отдельная сессия со своим `session_manifest.json` (одна baseline-запись на
манифест удовлетворяет правилам `B1`/`B6`, а `B14`/`B16` к baseline-прогонам не применяются).

## Команда свёртки

```bash
python tools/m0_repeatability.py \
    --run M0-R1=<каталог возврата R1> --run M0-R2=<…> --run M0-R3=<…> \
    --run M0-R4=<…> --run M0-R5=<…> \
    --expect-firmware 6d3ba90235e7b81681f88ea305957dd7ba5b2a69f810f2d73dfe088cfe3e0201 \
    --expect-crc 0x00E666F3 --min-runs 5 \
    --json M0_REPEATABILITY.json --markdown M0_REPEATABILITY.md --out-dir <каталог baseline>
```

Проверено на реальном возврате M0-R1: `R1…R5, R7 PASS`, `R6 FAIL (прогонов 1, минимум 5)` →
`M0_BASELINE_FROZEN.json не выпускается`. То есть один прогон **не** превращается в baseline
по невнимательности.

## Границы

Оба уровня — про **воспроизводимость экспериментальной точки отсчёта**, а не про физическую
корректность карты: `M0_BASELINE_FROZEN` не доказывает правильность коэффициентов,
реконструкции фазных токов и линейности ADC. Физическая квалификация (G3 / TZ-02 P0/P1)
остаётся отдельным, по-прежнему BLOCKED, этапом.

## Семантика уровня уточнена (2026-09-19)

Основание: на возврате R2…R5 запись была выпущена с `--min-runs 4` на четыре прогона, но с
шапкой `schema_version: tz2-m0-baseline-5run-1` и уровнем `M0_BASELINE_FROZEN` — то есть
under-count мог выглядеть как полный baseline. Инструмент исправлен, ранее выпущенная запись
не переписывается (остаётся в возврате как исторический артефакт).

```text
R6b          полный baseline достижим только при runs_count ≥ 5
             при меньшем числе — FAIL, если не задан --allow-provisional
             с --allow-provisional выпускается M0_BASELINE_PROVISIONAL.json (provisional: true)
schema       нейтральное tz2-m0-repeatability-1; runs_count и min_runs_required — в самой записи
лог          только telemetry.raw_log из манифеста; fallback на первый *.log — с явной пометкой
             в problems (в logs/ бывает две семьи логов: сессионная и пер-прогонная)
@SYS         отсутствие строк @SYS = FAIL: целостность UART не подтверждена (было fail-open:
             пустой список потерь читался как «потерь нет»)
BREAK        --breakdiag-archive фиксирует архив отдельным каналом provenance
             (break_diagnostic, sha256 файлов, affects_runs: false) и не влияет на R5
аудит        tools/audit_m0_run.py --mode session (A1…A9) | --mode repeat (B1…B9)
```

Контрольный прогон на фактическом возврате R2…R5 (пять прогонов, R1 из возврата сессии):

```text
R6 PASS · R6b PASS · R1 PASS · R2 PASS · R2b PASS · R3 PASS · R4 PASS
R5 FAIL   M0-R2…M0-R5: нет @SYS → целостность UART не подтверждена
R7 FAIL   аномалии: M0-R1 raw_i1_spread 25.0 (+178 %), M0-R2 14.0 (+56 %), M0-R5 raw_i2_spread 48.0 (+71 %)
→ ПОВТОРЯЕМОСТЬ: FAIL, уровень NONE, запись не выпускается
```

## INVALID-прогон исключается из статистики (2026-09-19, `ai2/tz2-repeatability-invalid`)

Требование приёмки: невалидный прогон **не превращается** в нулевой или «чистый» результат — он
исключается из статистики целиком и называется по имени с причинами.

```text
validity        любой дефект (FAULT, @BRK:valid=1, нет @SYS, потери UART, drain≠@MC:REC,
                dropped≠0, немонотонный t, stop_gate, несовпадение firmware/artifact/CRC/ccs,
                нет лога/манифеста) → INVALID
R6 / R6b        считают ВАЛИДНЫЕ прогоны: уровень решает валидное число, а не поданное
метрики         медианы, min/max и детектор аномалий — только по валидным; у каждой метрики
                в записи есть список runs, к которым относятся значения
R5 FAIL         перечисляет INVALID-прогоны с причинами («исключены из статистики целиком,
                не ноль, не чистый результат»)
запись/вывод    runs_count, runs_valid_count, runs_valid, runs_invalid, statistics_basis
аномалии        принимает приёмка (--acknowledge-anomaly), не оператор; R7 — «приняты приёмкой»
```

Контроль на фактическом возврате R2…R5: прогоны R2…R5 INVALID (нет `@SYS`) → статистика строится
на одном валидном прогоне (R1) → `R6 FAIL` (валидных 1 < 5) → запись не выпускается. Четыре
невалидных прогона не дают ни «нулей», ни усреднения с валидным.
