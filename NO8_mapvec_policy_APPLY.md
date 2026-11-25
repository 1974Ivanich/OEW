# Патч №8 `NO8_mapvec_policy.patch` — применение и приёмка

База: ветка **`ai4/foc-first-start-pc3`** (коммит `e04b55f216b9f6f8e98f9679e3f018b088308899`,
base ТЗ `1fa2109a`). 5 файлов, +81/−8. Смоук-сборка и полный `test-hosted` (42 теста)
пройдены на этой машине (GCC ARM 14.3.1, CubeCLT 1.22.0, 28.09.2026).

## Что делает

| файл | изменение |
|---|---|
| `src/foc.h` | тип `FOC_MapVecOut` + `FOC_GetLastMapVecOut()` |
| `src/foc.c` | `FOC_MAPVEC_MODE` (0 ENFORCE / 1 SKIP_FRAME / 2 ZERO_VECTOR, дефолт **1**), счётчик и rate-limited телеметрия `FOC: VEC:OUT:mu=…:mv=…:mw=…:cnt=…` вместо молчаливого `FOC_Stop()`, сброс счётчиков в `FOC_Start` |
| `main.c` | №6: после `@MAP:LOAD:OK` печатается `@MAP:READY:rows=6x2` (prompt перенесён в неё) |
| `tests/mocks/foc_stubs.c`, `tests/foc_start_gate_mocks.c` | стаб `UART_TrySendTelemetry` для хостед-тестов |

Селектор (`current_map_selector.c`) и защита (`protect.c`, break, SD interlock) — **не тронуты**:
инвариант «ровно одна измеренная апертура» сохранён, карта остаётся арбитром, меняется только
реакция FOC-кадра (compile-time, без новых CLI-команд).

## Применение (на машине с клоном OEW)

```bash
git fetch origin ai4/foc-first-start-pc3
git worktree add ../no8-apply ai4/foc-first-start-pc3   # или ветка от base
cd ../no8-apply
git am NO8_mapvec_policy.patch        # если предпочтителен commit; либо: git apply NO8_mapvec_policy.patch
```

## Сборка (идентично CI `.github/workflows/ci.yml:63`)

```bash
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HS1_COMMISSIONING_RELEASE=1"
```

Хостед-тесты: `make test-hosted` — у меня: все 42 цели собрались и прошли (0 failures).

Локальный артефакт смоука (НЕ для прошивки по протоколу, только контроль компилируемости):
`build/firmware.bin`, 80936 Б, sha256 `1e063da44d725bda70acdc7b2629fe49c2fd6494e02eca0a50bb2f20526f90fd`.
Для стенда: пропатченную ветку → MR/PR → зелёный CI → артефакт `firmware-commissioning`
нового рана (по ТЗ локальный hash критерием byte-identity не служит).

## CI-приёмка (выполнено 28.09.2026)

| пункт | значение |
|---|---|
| ветка | `no8-mapvec-policy` (base `e04b55f` = origin/ai4/foc-first-start-pc3) |
| коммит | `a623842ba32c4e54a2bd577acf3ee6c8c45864e8` |
| CI run | `build-test` #709, run id 36386926312 — **SUCCESS**, 1m31s |
| артефакт | `firmware-commissioning/firmware.bin`, **88020 Б**, sha256 `d2bc0439311a835dd025005515e843e292a07920e1f239f94a6715dc4211cfc7` (из `diagnostic_build_manifest.json`, `source_sha=a623842…`, defines complete) |
| копии | `Desktop\NO8_pkg\` (патч + образ + манифест + лог сборки) |
| URL | https://github.com/1974Ivanich/OEW/actions/runs/36386926312 |

Дальше — по `NO8_BENCH_RUN_PROTOCOL.md`: прошить именно `firmware_commissioning_a623842.bin`
(не `firmware_mapcap_rec.bin`, не старый `36f0e970…` — это образ ДО патча),
проверить sha на дампе до подачи силового звена.

## Что ожидать на стенде после прошивки патченным CI-образом

1. `c` → калибровка; `mapload <994 hex>` → теперь **пара** строк:
   `@MAP:LOAD:OK:…` + `@MAP:READY:rows=6x2` (закрытие №6).
2. `mp=15120,19410,0,0,0,0,3,0` → `mpapply` → `i=2000,100` → `rpm=100` → `f` → `1`.
3. Вместо тишины: `FOC: VEC:OUT:mu=…:mv=…:mw=…:cnt=1` (далее каждые 128 кадров),
   FOC остаётся в STARTUP: SKIP_FRAME держит последний CCR, PI recovery возвращает
   модуляцию в апертуру → кадры с вектором возобновляются.
4. Это и есть эксперимент из плана: карта-как-qualification-сигнал vs карта-как-hard gate.
   Ответ читается по соотношению VEC:OUT-кадров к кадрам с вектором и по токам.
5. Если SKIP_FRAME даст дёрганье тока — пересобрать с `FOC_MAPVEC_MODE 2` (ZERO_VECTOR).

## Откат

`git apply -R NO8_mapvec_policy.patch` — поведение байт-в-байт как у `e04b55f`.
