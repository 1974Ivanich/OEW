# TZ2_M_TUNING_SESSION — операторский протокол сессии M-подбора (первая energize)

Статус: **подготовка пакета. Сессия НЕ запускалась.** Firmware не изменялся (правки только в
`docs/` и `tools/`; `src/`, `main.c`, `Makefile`, `.ioc` — без изменений).

Шаг (c) плана: после offline-слоёв (writer → M0-rebased → comparator) стенд закрывает причинную
цепочку **конкретный M-артефакт → конкретный CRC → конкретный burst → конкретный raw-log →
конкретное изменение наблюдаемой метрики**.

## 0. Роли и предпосылки

| Роль | Кто | Что делает |
|---|---|---|
| Оператор 1 | у стенда | подача DC-link, запуск burst, физический STOP |
| Оператор 2 | второй человек | независимая запись параметров, контроль stop-gate, LOTO |
| Safety-owner | — | утверждает конверт (поле `safety_owner_approval`); без него сессия не стартует |
| ПК-2 (ai2) | offline | identity → M0-rebased, валидация манифеста, сравнение вариантов |

Обязательны ДО первого burst:

1. **Конверт утверждён:** `PROPOSED EXPERIMENTAL ENVELOPE` — VBUS 24…36 В, ток ≤ 1 А,
   burst ≤ 1 с, пауза ≥ 5 с. Без записи утверждения в манифесте валидатор даёт `BLOCKED [S4]`.
2. **Живая identity снята с платы** (`mapcap identity`) и сохранена файлом: из неё собирается
   авторитетный `M0-rebased` (identity = живая конфигурация, коэффициенты — измеренные).
3. **Firmware заморожен:** один и тот же `firmware_sha256` во **всех** burst'ах сессии.
   Между вариантами пересборки не бывает — меняется только рантайм-артефакт карты (`mapload`).
4. **Оба оператора** записаны; двойной контроль обязателен.

## 1. Структура манифеста (одна запись на burst)

| Поле | Смысл |
|---|---|
| `run_id` | уникальный идентификатор burst'а (уникальность проверяется, `B2`) |
| `variant_id` | имя варианта карты; baseline — ровно один и строго `M0-rebased` (`B1`, `B6`) |
| `base_map_id` / `base_map_crc32` | база сравнения; **одинакова у всех burst'ов** (`B5`) |
| `variant_map_id` / `variant_map_crc32` | загруженный вариант; уникален и ≠ базе (`B7`) |
| `artifact_sha256` | sha256 файла артефакта (сверяется с диском, `C2`) |
| `firmware_sha256` | обязан совпадать с сессионным и у всех burst'ов (`S3`, `B4`) |
| `timestamp_start` / `timestamp_end` | ISO-8601, начало/конец burst'а (`B15`) |
| `vbus_target_mv` | целевое звено; внутри утверждённого конверта (`B10`) |
| `current_limit_ma`, `burst_duration_ms`, `pause_before_ms` | ограничения; пауза ≥ минимума (`B9`, `B10`) |
| `operator1` / `operator2` | оба оператора (`S2`) |
| `telemetry.raw_log` | непрерывный raw UART-лог варианта |
| `telemetry.preflight` / `postflight` | обязательные команды до/после burst'а |
| `telemetry.identity_file`, `telemetry.artifact_file` | файлы identity и артефакта |
| `map_load_ok`, `run_id_ack` | подтверждения `@MAP:LOAD:OK` и `@RUN:ID` (`B11`) |
| `stop_gate` | если сработал — burst недействителен (`B12`) |
| `break_snapshot_archived` | при BREAK: файл + sha256 + **≥ 2 чтений** (`B13`) |
| `previous_variant_comparison` | для не-baseline: анализ предыдущего варианта (`B14`) |

Шаблон: `tools/session_manifest_template.json`. Схема: `tools/session_manifest_schema.json`.

## 2. Телеметрия: один непрерывный лог на вариант

Внутри лога (без разрывов и ручной правки):

```text
@MAP:LOAD:OK
@RUN:ID=...
@ADC:...
@FOC:...
```

Перед burst'ом (в том же логе):

```text
sysinfo
p?
pdump
enc
mapcap identity
```

После burst'а — тот же safety snapshot (`sysinfo`, `p?`, `pdump`, `breakdiag`).
Список preflight/postflight обязан совпадать у всех burst'ов: различие даёт `BLOCKED [B8]`.

## 3. Последовательность

```text
SAFE / PWM OFF
       │
       ▼
mapcap identity ──────────────► live.txt ──► M0-rebased (коэффициенты измеренные, identity живая)
       │
       ▼
mapload M0-rebased
       │
       ▼
подтвердить @MAP:LOAD:OK + @RUN:ID
       │
       ▼
safe snapshot (sysinfo, p?, pdump, enc)
       │
       ▼
M0 burst ──► SAFE
       │
       ├──► comparator: baseline зафиксирован (--baseline-crc32 = CRC(M0-rebased))
       │
       ▼
mapload M1 → M1 burst → SAFE → offline comparison → решение о следующем варианте
```

**Запрещено** идти `M1 → M2 → M3` без промежуточного сравнения: причинная связь теряется.
Валидатор требует для не-baseline burst'а заполненный анализ **непосредственно предыдущего**
варианта (`B14`).

## 3.1 Gate G0…G9 перед первым M0 burst (исполняемая проверка)

Порядок зафиксирован; проверяется одной командой, каждая строка имеет статус PASS/FAIL:

```bash
python tools/preflight_m0_burst.py --manifest session_manifest.json --bundle <каталог сессии> \
        [--firmware firmware.bin] [--rebase-manifest M0_rebased.json] [--dump-cmd "... --dump"] \
        --json gate.json
```

| Гейт | Что проверяется |
|---|---|
| `G0` | правильный firmware SHA: образ на диске == `session.firmware_sha256` |
| `G1` | живая identity с платы: файл есть, извлечены все 11 полей, совпадают с манифестом |
| `G2` | authoritative `M0-rebased`: rebase-манифест с `identity_rebased=true`, `coefficients_changed=false`, `crc32.after == CRC burst'а` |
| `G3` | artifact SHA (файл == `burst.artifact_sha256`) и CRC (при `--dump-cmd` — из `--dump`) |
| `G4` | `safety_owner_approval` заполнен |
| `G5` | оба оператора указаны и различаются |
| `G6` | session_manifest валиден (правила `S1..S5`, `B1..B15`) |
| `G7` | `--bundle`: файлы и sha256 на месте (`C1`, `C2`) |
| `G8` | preflight по логу: `@SYS` с `uart_drp=0/uart_trunc=0`, `@PWM:CR1` с `CCER=0`, `@PWM:DUMP` с `ARR` == живому, `@ENC` `err=0`, `@MAP:IDENTITY`, `@MAP:LOAD:OK`, нет `@BRK:valid=1` и признаков `@FAULT` |
| `G9` | вердикт `M0 burst разрешён` — печатается **только** при полном PASS |

При любом FAIL вердикт — `G9: M0 burst НЕ разрешён` со списком непройденных гейтов, rc=1.

**Граница результата (печатается при PASS):** M0 burst создаёт *experimental baseline point* для
последующего `M1 vs M0` и **не является** квалификацией карты или реконструкции токов.
Физическая пригодность остаётся отдельным этапом (§7/§7.1 P0/P1).

Проверка форматов ведётся по фактическим ответам прошивки (`src/cli.c`):
`@SYS:CLK=…:uart_drp=…:uart_trunc=…`, `@PWM:CR1=…:CCER=…:BDTR=…:CNT=…`,
`@PWM:DUMP:PSC=…:ARR=…`, `@ENC:…:err=…`, `@MAP:IDENTITY:…`, `@MAP:LOAD:OK`.

## 3.2 После первого M0 burst: заморозка baseline (обязательна до проектирования M1)

Порядок зафиксирован приёмкой:

```text
raw UART → manifest completion → offline verify → map_variant_compare.py
         → independent audit → M0 baseline frozen → только затем проектирование M1
```

Исполняемый шаг — `tools/freeze_m0_baseline.py`: он проверяет всю цепочку и выпускает
`M0_BASELINE_FROZEN.json` (пины firmware/identity/артефакта/лога/gate/comparator + запись аудита).

```bash
python tools/freeze_m0_baseline.py --manifest session_manifest.json --bundle <каталог сессии> \
        --gate gate.json --comparator compare.json \
        --audit-by "<кто провёл независимый аудит>" [--audit-file AUDIT.md] \
        --patch-manifest session_manifest.json \
        --return-manifest <каталог сессии>/RETURN_SHA256.txt
```

| Проверка | Что означает |
|---|---|
| `F1` | манифест валиден, baseline burst определён |
| `F2` | gate G0…G9 дал вердикт **PASS** |
| `F3` | raw-лог: есть `@RUN:ID` ACK, нет `@BRK:valid=1`, нет строк `FAULT≠0`, `uart_drp/uart_trunc=0` (в **любой** строке `@SYS`), `t` монотонен, нет `@MAP:LOAD:FAIL`, покрытие safety-полей ≥ 0.5 |
| `F4` | sha256 артефакта на диске == манифесту |
| `F5` | живая identity == значениям манифеста (11 полей) |
| `F6` | stop-gate не сработал, `map_load_ok`, `run_id_ack` |
| `F7` | независимый аудит зафиксирован (`--audit-by`) |
| `F8` | comparator (если передан): sha256 и baseline CRC == базовому CRC burst'а |

Пока запись не выпущена — **M1 не проектируется**: валидатор манифеста даёт `BLOCKED [B16]`
(не-baseline burst без `baseline_frozen`), а gate для варианта — `FAIL G2b`.

В выпущенной записи отдельно стоит ограничение: M0 baseline — экспериментальная точка отсчёта
для `M1 vs M0`; он **не** доказывает корректность карты, реконструкции фазных токов, линейности ADC
и физическую пригодность OEW map.

`--patch-manifest` проставляет `baseline_frozen` (file + sha256 + baseline CRC) в не-baseline
burst'ы — чтобы значения не переносились руками. `--return-manifest` пишет `RETURN_SHA256.txt`
**последним** (запись о заморозке — последней строкой).

## 4. Stop-gate: burst прекращается немедленно

1. `FAULT != 0` или новый `FAULT_R`;
2. `@BRK:valid=1` или изменение BREAK-снимка;
3. неожиданные `MOE`/`CCER` вне ожидаемого состояния;
4. VBUS вышел за утверждённый конверт;
5. ADC rail / saturation;
6. `uart_trunc != 0` или `uart_drp != 0`;
7. потеря encoder/связи;
8. `t` перестал монотонно возрастать;
9. `@MAP:LOAD:FAIL`;
10. перегрев;
11. операторская команда STOP.

При срабатывании любого — burst помечается `stop_gate.triggered = true` и **недействителен**;
для сессии требуется перезапись (`B12`).

**При BREAK:** сначала **архивировать snapshot дважды** (два чтения подряд, без действий между
ними, побайтовое совпадение) и сохранить raw, и только потом `breakdiag reset`/clear — порядок
зафиксирован процедурой breakdiag, здесь он обязателен как условие валидности (`B13`).

## 5. Инвариант сравнения: меняется только M

Для каждого варианта обязательны:

```text
same firmware   (B4)
same identity   (артефакты собраны от одной живой identity; base_map одинаков, B5)
same envelope   (vbus_target, current_limit, burst_duration, B8/B10)
same telemetry  (preflight/postflight, одна процедура захвата, B8)
different M only (variant_map_crc32 уникален и ≠ базе, B7)
```

Иначе сравнение не даёт права утверждать «изменение токовой карты меняет поведение FOC
предсказуемым образом» — оно будет объясняться изменением чего-то ещё.

## 6. Команды (ПК-2 — offline, ПК-3 — стенд)

```bash
# 1) живая identity из лога сессии → live.txt
python tools/live_identity_from_log.py <uart.log> live.txt

# 2) авторитетный M0-rebased: коэффициенты измеренные, identity живая
./tools/map_variant_cli.exe --rebase-identity M0_measured.bin live.txt M0_rebased.bin \
        --variant M0-rebased --manifest M0_rebased.json
#    три проверки: coefficients == измеренным / identity == живой / decode + admission PASS

# 3) гейт манифеста до подачи звена (fail-closed)
python tools/validate_session_manifest.py session_manifest.json --bundle <каталог сессии>

# 4) стенд: загрузка варианта (firmware не пересобирается)
python tools/map_upload.py --port COM4 --bin M0_rebased.bin --verify

# 5) после burst'ов — сравнение (baseline = M0-rebased, без агрегированного рейтинга)
python tools/map_variant_compare.py --baseline M0 --baseline-crc32 <CRC(M0-rebased)> \
        --run M0=logs/M0.log --run M1=logs/M1.log --json report.json --markdown report.md
```

## 7. Состав возвращаемого пакета (на каждый burst)

```text
<run_id>/
  uart.log              непрерывный raw-лог (META/BOOT/TX/RX)
  <run_id>.json         запись burst'а (те же поля, что в манифесте)
  identity.txt          живая identity, снятая в этой сессии
  M0_rebased.bin        артефакт, загруженный в этом burst'е
  breakdiag_*.txt       только если был BREAK (два чтения + sha256)
session_manifest.json   манифест сессии (единый, заполняется по ходу)
RETURN_SHA256.txt       манифест файлов — записывается ПОСЛЕДНИМ
```

Проверка каталога: `--bundle <каталог>` (наличие файлов, `C1`; sha256 артефакта, `C2`).
Анализ ведётся **вне** возвращаемой папки; оригиналы не редактируются.

## 8. Что докажет эта сессия и что нет

**Докажет (при полном манифесте и сравнении):**

* что при неизменных firmware/identity/envelope/процедуре смена **только** токовой карты
  предсказуемо и воспроизводимо меняет наблюдаемые метрики (`Id/Iq` error и ripple, repeatability
  `idc`, ZSV, `sector/window` ↔ CCR, protection/integrity);
* что ветка «улучшили dq, но вырос ZSV» детектируется машинно, а не глазами;
* что BREAK-событие сопровождается архивированным снимком, пригодным для разбора.

**Не докажет:**

* физическую истинность коэффициентов `M` (для этого — отдельная квалификация, §7/§7.1 P0/P1);
* абсолютную точность измерений ADC/датчиков;
* отсутствие BREAK при других режимах вне конверта;
* что «лучшая карта» переносима на другой экземпляр стенда.

## 9. Правила-исполнители

`tools/validate_session_manifest.py` реализует ID-правила `S1..S5`, `B1..B15`, `C1..C2`
(см. докстринг). Тесты: `tests/test_session_manifest.py` — 21 проверка, включая негативные:
пустой шаблон → `BLOCKED` (28 нарушений), отсутствие утверждения safety-owner, разные
firmware между burst'ами, разные базы, дубли CRC/`run_id`, превышение конверта, короткая пауза,
различия процедуры, сработавший stop-gate, BREAK с одним чтением, отсутствие промежуточного анализа.
