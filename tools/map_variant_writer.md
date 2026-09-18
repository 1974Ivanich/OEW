# Генератор вариантов карты (TZ-02 experimental M-tuning)

Host/offline инструмент: из канонического артефакта `M0` строит варианты `M1/M2/M3…`
с проверкой инвариантов и admission-условий прошивки. **Firmware и control algorithm не
изменяются** — вариант загружается в рантайме командой `mapload <994 hex>`
(`tools/map_upload.py`), пересборка прошивки не требуется.

## Что здесь есть

| Файл | Назначение |
|---|---|
| `tools/map_variant_writer.h/.c` | библиотека: decode → transform → admission-проверки → canonical encode |
| `tools/map_variant_cli.c` | CLI: `--emit-fixture`, преобразования, манифест, `--expect-base` |
| `tools/map_variant_writer.mk` | hosted-сборка (автономный mk, корневой `Makefile` не меняется) |
| `tools/run_map_variant_writer_test.sh` | e2e: фикстура → M1/M2/M3 → негативные кейсы → sha256 |
| `tests/map_variant_writer_test.c` | 60 проверок (round-trip, identity, canonical, CRC, negatives) |

## Инварианты (проверяются, а не предполагаются)

1. **Identity preservation** — `board_revision`, `pwm_frequency_hz`, `timer_arr`,
   `adc_trigger_id`, `trigger_offset_ticks`, `deadtime_ticks`, `adc_clock_hz`,
   `adc_sample_cycles_x2`, `adc_resolution`, `adc_config_signature`,
   `current_calibration_signature` после decode → transform → encode совпадают с M0
   (`MapVariant_IdentityMatches`, при расхождении — `ERR_IDENTITY`).
2. **Provenance / регионы / startup** — не изменяются вовсе (`memcmp`-сравнение).
3. **Топология карты** — трансформация касается только коэффициентов `m00/m01/m10/m11`
   (4 на запись, 12 записей sector×window = 48 коэффициентов). Число записей,
   phase-пары, регионы и startup не трогаются.
4. **CRC** — считается каноническим путём ПОСЛЕ трансформации: `CurrentMap_CalculateCrc32()`
   над структурой, затем `MapArtifactWriter_EncodeBinary()` в 497 байт. CRC по JSON или по
   промежуточной структуре не используется.
5. **Admission-зеркало** — выход проходит те же проверки, что прошивка:
   `entry_is_sane` (валидность, фазы 0..2 и не равны, `|coeff| ≤ 10000`, `det ≠ 0`),
   `region_is_sane` (`valid`, `min_margin_ticks ≠ 0`, `min ≤ max` по трём осям),
   непересечение всех регионов попарно. Иначе — `ERR_ADMISSION`
   (напр. сильный downscale обнуляет матрицу → `det = 0` → отказ).
6. **Детерминизм** — целочисленная арифметика, округление к нулю, сатурация явная;
   два прогона на тех же входах дают байт-идентичный артефакт
   (проверено тестом `M1/M2 детерминизм`).

## Определение преобразований

```text
M1 = M0 × num / den     (GLOBAL_SCALE;  округление к нулю, затем кламп)
M2 = M1 + offset        (COMMON_OFFSET)
M3 = точечно по sector/window (SECTOR_WINDOW + scale)
```

* «Округление к нулю» = C-семантика целочисленного деления (согласовано и документировано,
  чтобы два хоста получали одинаковые байты).
* Кламп задаётся явно (`--clamp MIN MAX`, по умолчанию `±10000` — значение
  `CURRENT_RECON_MAX_COEFF` из `src/current_reconstruct.c`); кламп вне этого диапазона
  отвергается (`ERR_TRANSFORM`), количество «упёршихся» коэффициентов пишется в статистику
  и в манифест.
* Выход за `int32` при вычислении → `ERR_OVERFLOW` (fail-closed).
* Если ни один коэффициент не изменился → `ERR_TRANSFORM` (бессмысленный вариант).

## Использование

```bash
# unit-тесты + e2e одной командой
sh tools/run_map_variant_writer_test.sh

# фикстура (НЕ измеренная карта — только для тестов/демо)
./tools/map_variant_cli.exe --emit-fixture variant_out/M0_fixture.bin

# M1 = M0 × 11/10
./tools/map_variant_cli.exe M0.bin M1.bin --variant M1 --scale 11/10 --manifest M1.json

# M2 = M1 + 50
./tools/map_variant_cli.exe M1.bin M2.bin --variant M2 --offset 50 --manifest M2.json

# точечно: sector=3, window=1 — удвоить
./tools/map_variant_cli.exe M0.bin M3.bin --variant M3 --sector 3 --window 1 --scale 2/1

# перед загрузкой: проверить, что identity совпадает с базовой картой
./tools/map_variant_cli.exe M1.bin M1_check.bin --variant M1 --scale 11/10 --expect-base M0.bin

# доставка на стенд (firmware НЕ пересобирается)
python tools/map_upload.py --port COM4 --bin M1.bin
```

Коды выхода CLI: `0` — OK, `1` — REJECT (печатается статус и причина отказа), `2` — ошибка
использования/IO. Отвергнутый вариант **не записывается** на диск.

## Манифест варианта

`--manifest out.json` пишет: имя варианта, параметры преобразования, кламп, флаги сохранения
identity/provenance/регионов/startup, `entries_total/changed`, число клампов, диапазоны
коэффициентов до/после и CRC до/после. JSON — аудиторский, прошивкой не потребляется.

## Acceptance-набор (для приёмки пакета)

```text
M0 round-trip             PASS   decode → encode байт-в-байт
identity preservation     PASS   проверка полей, не «предполагается»
canonical encoding        PASS   encode дважды → идентично
CRC compatibility         PASS   вариант декодируется MapArtifact_DecodeBinary
negative admission tests  PASS   CRC/магия/длина/ARR/board_revision/диапазон/overflow/det=0
M1/M2 deterministic       PASS   два прогона → идентичные байты
firmware SHA unchanged    PASS   контроль: сборка образа из ветки даёт тот же SHA
```
