# TZ_MAP_UPLOAD_AND_ADMISSION — загрузка oew_map_v2.bin через UART и контрольный admission

> Цель: дать firmware возможность принять готовый 497-байтный артефакт
> `oew_map_v2.bin` через UART (без перепрошивки), десериализовать его в
> `OewCurrentMap`, пройти `MapCommissioning_LoadMeasured` и открыть путь к
> `FOC_Start` (`rc=0` вместо `rc=-2 map_unverified`). Никакого ослабления
> default-deny: артефакт проходит все штатные гейты (CRC, identity, provenance,
> region overlap, startup containment, reconstruction determinant).

---

## 1. Контекст (что уже есть)

### 1.1. Артефакт
60V BOAR grid-кампания завершена (48/48 точек). Offline pipeline произвёл:
- `oew_map_v2.bin` — 497 байт, canonical little-endian wire format
  (`OEW_CURRENT_MAP_WIRE_SIZE = 497`, `tools/map_artifact_writer.h:18`)
- `oew_map_v2.json` — audit metadata (CRC = `0x0A8A9FAB`, cid = `0x424F4152`)
- 384 samples, dataset_crc32 = `0xC3FA2C1B`

### 1.2. Firmware loading path (уже реализован)
- `CurrentMap_LoadMeasured(const OewCurrentMap *map, const OewMapIdentity *active_identity)`
  (`src/current_map_selector.c:138`) — принимает **in-RAM** `OewCurrentMap` struct,
  проверяет magic/revision/identity/provenance/CRC/regions/startup/recon, копирует
  в `g_map`, ставит `g_ready = 1`.
- `MapCommissioning_LoadMeasured` (`src/map_commissioning.c:8`) — обёртка: проверяет,
  что PWM off, FOC/V-f/autotune/capture не активны, нет fault, identity совпадает;
  вызывает `MapCandidate_IsCanonical` → `CurrentMap_LoadMeasured` → `CurrentMap_IsReady`.
- `FOC_Start` (`src/foc.c:514`) — проверяет `CurrentRecon_IsReady()` →
  `CurrentMap_SelectInitialStartupContext` → `ADC_SetControlAdmission(true)` →
  `ADC_InjectedStart` → `PWM_Enable`. Без loaded map → `rc = -2`.

### 1.3. Чего НЕТ
1. **Decoder wire→struct.** `MapArtifactWriter_EncodeBinary` (`tools/map_artifact_writer.c:81`)
   сериализует struct→wire (497 байт), но **обратной** `DecodeBinary` (wire→struct) нет.
   Firmware не может восстановить `OewCurrentMap` из принятых 497 байт.
2. **CLI-команда загрузки.** `src/cli.c` не имеет команды `mapload`. Нет XMODEM,
   base64, или бинарного upload-протокола.
3. **Flash-хранение.** `linker.ld` — единый `FLASH` 512 KB без выделенного раздела
   для карты. Карта живёт только в RAM (`g_map`). При сбросе теряется.

### 1.4. Ограничения (не нарушать)
- `OEW_MAP_CAPTURE=1`, `OEW_MAP_L3=1`, `PWM_OEW_BOARD_REVISION=7` — commissioning build.
- Hardware BKIN остаётся активным.
- Все штатные гейты `CurrentMap_LoadMeasured` / `MapCommissioning_LoadMeasured`
  должны проверять артефакт — запрещено обходить CRC, identity, provenance,
  region, startup, reconstruction.
- Не трогать: `foc.c`, `protect.c`, `adc.c`, `adc_dispatch.c`, `pwm.c`, `.ioc` —
  кроме добавления CLI-хука в `main.c`/`cli.c`.
- Код декодера (`DecodeBinary`) должен быть в `src/` (firmware), не в `tools/`
  (host-only).

## 2. Задача WEB AI

### 2.1. `MapArtifactWriter_DecodeBinary` — wire → struct decoder (firmware-side)

Создать функцию — **зеркало** `MapArtifactWriter_EncodeBinary`:

```c
/* src/map_artifact_decoder.h / src/map_artifact_decoder.c */
bool MapArtifact_DecodeBinary(const uint8_t *src, size_t length,
                              OewCurrentMap *out);
```

Требования:
- Входные данные: `src` — 497 байт little-endian wire, `length == OEW_CURRENT_MAP_WIRE_SIZE`.
- Выход: заполненная `OewCurrentMap` с вычисленным `crc32`.
- Fail-closed: возвращает `false` при `length != 497`, null-указателях,
  `magic != OEW_CURRENT_MAP_MAGIC`, `revision != OEW_CURRENT_MAP_REVISION`.
- CRC: прочитать CRC из wire (последние 4 байта), заполнить struct с `crc32 = 0`,
  вычислить `CurrentMap_CalculateCrc32(&out)`, сравнить с wire CRC. Не совпадает → `false`.
- Не использовать `memcpy` всей payload → явный побайтовый разбор (как в encode).
- Wire layout (порядок полей, размеры) — точная копия
  `MapArtifactWriter_EncodeBinary` (`tools/map_artifact_writer.c:100–156`):
  - `[0..3]` magic U32
  - `[4..5]` revision U16
  - `[6..7]` board_revision U16
  - `[8..11]` pwm_frequency_hz U32
  - `[12..15]` timer_arr U32
  - `[16..19]` adc_trigger_id U32
  - `[20..21]` trigger_offset_ticks U16
  - `[22..23]` deadtime_ticks U16
  - `[24..27]` adc_clock_hz U32
  - `[28..29]` adc_sample_cycles_x2 U16
  - `[30]` adc_resolution U8
  - `[31..34]` adc_config_signature U32
  - `[35..38]` current_calibration_signature U32
  - `[39..62]` provenance (6× U32)
  - `[63]` startup_sector U8
  - `[64]` startup_window U8
  - `[65..66]` startup_hold_cycles U16
  - `[67..68]` startup_mu I16
  - `[69..70]` startup_mv I16
  - `[71..72]` startup_mw I16
  - `[73..300]` recon[6][2] — 12 entries × 19 байт каждый:
    U8(valid), U8(phase_a), U8(phase_b), U32(m00), U32(m01), U32(m10), U32(m11)
  - `[301..492]` region[6][2] — 12 entries × 16 байт каждый:
    I16(mu_min), I16(mu_max), I16(mv_min), I16(mv_max), I16(mw_min), I16(mw_max),
    U16(min_margin_ticks), U8(valid), U8(reserved)
  - `[493..496]` crc32 U32

### 2.2. CLI-команда `mapload` — base16 upload через UART

Добавить CLI-команду для загрузки артефакта через UART в текстовом формате:

```text
>>> mapload <994 hex chars>
```

994 hex символа = 497 байт × 2 hex-цифры на байт.

Логика:
1. Парсить hex-строку → 497-байтный буфер (`uint8_t wire[497]`).
2. `MapArtifact_DecodeBinary(wire, 497, &candidate)` → `OewCurrentMap candidate`.
3. Прочитать `MapReferenceManifest` из profile (или собрать из identity).
4. Вызвать `MapCommissioning_LoadMeasured(&candidate, &manifest, &ops)` —
   использовать тот же `ops` что в `mapcap_build_and_load` (`main.c:~380`).
5. Ответить телеметрией:
   - Успех: `@MAP:LOAD:OK:crc=0x%08X:cid=0x%08X\r\n`
   - Ошибка decode: `@MAP:LOAD:FAIL:DECODE\r\n`
   - Ошибка commissioning: `@MAP:LOAD:FAIL:COMMISSION\r\n`

Ограничения:
- Команда доступна только в commissioning build (`#if OEW_MAP_CAPTURE && OEW_MAP_L3`).
- Без `OEW_MAP_CAPTURE` команда отсутствует (default-deny).
- Буфер `wire[497]` — локальный (стек) или static; `OewCurrentMap candidate` —
  локальный (стек ~512 байт + 497 wire ≈ 1 KB, стек = 4 KB, допустимо).
- **Не** хранить в flash. Карта живёт в RAM до следующего сброса.

### 2.3. Python-скрипт отправки: `tools/map_upload.py`

```bash
python tools/map_upload.py --port COM5 --bin oew_map_v2.bin
```

Логика:
1. Прочитать `oew_map_v2.bin` (497 байт), проверить размер.
2. Преобразовать в hex-строку (994 символа).
3. Отправить по UART: `mapload <hex>\n`.
4. Ждать ответ `@MAP:LOAD:OK` или `@MAP:LOAD:FAIL` (таймаут 5 с).
5. Вывести результат; exit 0 при OK, exit 1 при FAIL.
6. Опциональный `--verify`: после загрузки отправить `1` (FOC start) и проверить,
   что `rc=0` (не `rc=-2`), затем `0` (FOC stop).

### 2.4. Roundtrip-тест (hosted)

`tests/map_artifact_decode_test.c`:
- Взять `OewCurrentMap` из `tests/map_artifact_pipeline_test.c` (happy-path artifact).
- `MapArtifactWriter_EncodeBinary` → wire[497].
- `MapArtifact_DecodeBinary(wire, 497, &decoded)`.
- `memcmp(&original, &decoded, sizeof(OewCurrentMap)) == 0`.
- `CurrentMap_CalculateCrc32(&decoded) == decoded.crc32`.
- Мутировать один байт wire → `DecodeBinary` → `false`.
- Передать `length != 497` → `false`.
- Обнулить magic → `false`.

Добавить в `Makefile` target `tests/map_artifact_decode_test.exe` и в `test-hosted`.

## 3. Входные материалы (прочитать)

| Файл | Что |
|---|---|
| `tools/map_artifact_writer.c:81–164` | `EncodeBinary` — канонический encoder, зеркало для decoder |
| `tools/map_artifact_writer.h:18` | `OEW_CURRENT_MAP_WIRE_SIZE = 497` |
| `src/current_map_selector.h:63–93` | `OewCurrentMap` struct layout |
| `src/current_map_selector.c:138–176` | `CurrentMap_LoadMeasured` — все гейты |
| `src/map_commissioning.c:8–30` | `MapCommissioning_LoadMeasured` — ops и preconditions |
| `src/map_candidate.c:153–168` | `MapCandidate_IsCanonical` — validation |
| `main.c:313–438` | `mapcap_build_and_load` — пример commissioning wiring |
| `main.c:522–539` | `cli_mapcap_command` — куда добавить `mapload` |
| `src/current_reconstruct.h:9–10` | `CURRENT_RECON_MAX_SECTORS=6`, `CURRENT_RECON_MAX_WINDOWS=2` |
| `src/map_capture_port.c:276–304` | `MapCapturePort_GetMapIdentity` — runtime identity builder |

## 4. Формат ответа (что вернуть)

1. **`src/map_artifact_decoder.h`** + **`src/map_artifact_decoder.c`** — decoder wire → struct.
2. **Изменения `main.c`** — CLI-команда `mapload` (в `cli_mapcap_command` или новый хук).
3. **`tools/map_upload.py`** — Python-скрипт отправки.
4. **`tests/map_artifact_decode_test.c`** — roundtrip hosted-тест.
5. **Изменения `Makefile`** — новый test target.
6. Всё компилируемое, тестируемое: `make && make test-hosted`.

## 5. Что НЕ делать

- Не добавлять flash-хранение (отдельный этап, отдельное ТЗ).
- Не менять `foc.c`, `protect.c`, `adc.c`, `adc_dispatch.c`, `pwm.c`, `.ioc`.
- Не ослаблять и не обходить ни один гейт `CurrentMap_LoadMeasured`.
- Не вводить FOC/V-f/autotune admission — это следствие загрузки карты,
  а не часть этого ТЗ. После `mapload OK` оператор сам вводит `1` (FOC start).
- Не использовать бинарный UART-протокол (XMODEM и т.п.) — hex-текст проще,
  отлаживается в терминале, 994 символа влезают в один UART-пакет.
