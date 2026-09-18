# `mopt0_capture.py` — M-OPT-0 no-HV baseline (5 прогонов) для стенда ПК-3

Скрипт собирает **физический baseline существующей карты M0** при физически
отключённом DC-link: пять независимых прогонов `M0-R1…M0-R5`, для каждого —
отдельная папка, отдельный raw UART-лог и отдельный verdict по no-HV гейту и
идентичности.

> **Контракт честности.** Скрипт записывает только то, что реально пришло по
> UART, и объявляет `PASS` только при наличии настоящего evidence. Он **не**
> фабрикует строки `@FOC`/`@RUN`, **не** подставляет ADC/VBUS-значения, **не**
> прошивает MCU, **не** подаёт DC-link, **не** выполняет FOC/V/f/autotune и **не**
> запускает `mapcap build`. Хостовый `tests/m_opt_0_nohv_test.c` проверяет
> fail-closed логику допуска/терминала и **не является** этим baseline.

## 0. Identity-телеметрия в прошивке (предпосылка)

Скрипт отправляет в начале каждого прогона команду установки идентификатора и
требует **ответ прошивки**, иначе прогон блокируется:

| Шаг/поле | Контракт | Назначение |
|---|---|---|
| команда | `run=<id>` (1..23 симв., `[A-Za-z0-9_.-]`), настраивается `--run-id-command` | установить идентификатор прогона |
| ответ | `@RUN:ID=<id>` в **прямом** ответе на команду (`run_id_ack`) | доказать, что прошивка приняла id |
| `@FOC` | `run_id=<id>`, `map_id=<M0>`, `map_crc32=<hex>` | привязка сэмплов к карте и прогону |

Плюс SHA-256 `build/firmware.bin` и git SHA источника (скрипт берёт их сам из
`--firmware-bin` и `git rev-parse HEAD`).

Контракт реализован в ветке **`ai5/m-opt-0-telemetry`** (`@RUN:ID=`, `run_id`,
`map_id`, `map_crc32`, `Id/Iq/Id_ref/Iq_ref` в `@FOC`). Пока эта ветка не влита
в образ, который прошит на стенде, `run_id_ack`/`identity_*` не проходят →
прогон `BLOCKED_MISSING_IDENTITY`, кампания `INCOMPLETE_IDENTITY`. Это ожидаемое
fail-closed поведение, а не сбой скрипта: **порядок работ — сначала identity
telemetry в образе, затем физический M-OPT-0**.

`--identity-source folder` — только документирование: `run_id` берётся из имени
папки артефакта, в summary появляется `identity_note`, и кампания всё равно не
может стать `PASS`.

## 1. Последовательность одного прогона

```
run=<id> → sysinfo → p? → pdump → a × N (по умолчанию 20) → c → enc → mapcap status
```

| Шаг | Что доказывает | Гейт |
|---|---|---|
| `run=<id>` | прошивка приняла идентификатор прогона | `@RUN:ID=<id>` в прямом ответе |
| `sysinfo` | живой MCU, board identity | лог непустой |
| `sysinfo` (`uart_drp`/`uart_trunc`) | потери на транспорте | если поле есть: `uart_trunc=0` (иначе FAIL — пакет `@FOC` не поместился и был отброшен целиком) |
| `p?`, `pdump` | default-deny удержан, мост выключен | `MOE = 0` (бит 15 `BDTR`, RM0440) **и** `CCER = 0` по прямому дампу; текстовых полей `MOE=`/`default_deny=` production-образ не печатает |
| `a` × N | статистический no-HV гейт шины | `median(raw_vbus) ≤ 9` **и** `max(raw_vbus) ≤ 200`, I1/I2 не на рельсах (1…4094) |
| `c` | offsets живы | `@ADC:CAL:…` и отсутствие `@ADC:CAL:FAIL` |
| `enc` | датчик положения живой | `err=0` |
| `mapcap status` | MapCapture не запущен | `state=0 term=0 frames=0 dropped=0 avail=0`; на production-образе команды нет (`unknown`) → `mapcap_scope.applicability = N/A` с причиной |
| поток `@FOC` (каждая прочитанная строка) | safety/state удержаны | `FAULT = 0` **и** `FAULT_R = 0`, `RUN = 0`, `FAIL = 0`, `STATE = --expected-safe-state` (по умолчанию 0) |

`@MC:REC:` в baseline-логе не ожидается вовсе: M-OPT-0 фиксирует поведение
существующей карты, а не characterization. Маркеры потери evidence
(`line overflow`, `@UART:TRUNC`, `@UART:DROP`), пустой лог и чужой `run_id`
внутри лога — FAIL прогона.

## 1.1 Safety-гейты потока `@FOC` (fault/state) и скоуп identity

Защёлкнутый `PROTECT_FAULT_HARDWARE_BREAK` (код 18) не печатает в UART ничего: ISR
`TIM1_BRK/TIM8_BRK` латчит fault и вызывает `PWM_Disable()`. Единственный след в
логе — поля `FAULT`/`FAULT_R` строк `@FOC` (плюс `CCR1..3 = mid` от `PWM_Disable()`).
Поэтому приёмка проверяет их **в каждой прочитанной строке**, а не в начале/конце:

| Поле `@FOC` | Источник в прошивке | Гейт |
|---|---|---|
| `FAULT` | `PROTECT_IsFault()` | `= 0` |
| `FAULT_R` | `PROTECT_GetFaultReason()` | `= 0` (иначе FAIL, имя причины пишется в отчёт) |
| `RUN` | `FOC_IsRunning()` | `= 0` |
| `FAIL` | `FOC_GetStartupFailReason()` | `= 0` |
| `STATE` | `FOC_GetState()` | `= --expected-safe-state` (read-only baseline: `0`) |
| `MOE`, `CCER` | `p?`/`pdump` (прямой дамп) | `= 0` |

Правила разбора потока (в отчёте — блок `foc_stream`):

* **поле за полем, не строкой целиком.** Граница RX-окна транспорта режет строку,
  и хвост может прийти в следующем чанке (иногда уже после маркеров следующей
  команды). Прочитанные поля сохраняются, непрочитанные остаются `null`;
  `rows_without_safety_fields` и `safety_field_coverage` показывают, какая доля
  строк дала доказательство (на реальных логах ≈ 0.95);
* **доказательство нечем — FAIL.** Если `FAULT`/`FAULT_R` не прочитаны ни в одной
  строке (`fields_missing_in_all_rows`), гейт падает: «доказать нечем» ≠ «чисто»;
* **скоуп identity по прямому ACK.** Строки до `@RUN:ID=<id>` принадлежат
  предыдущей сессии (id в RAM переживает прогоны): они не приписываются текущему
  прогону, не участвуют в его safety-гейтах и не дают ему `map_id`/`map_crc32`.
  Чужой `run_id` в строке ПОСЛЕ ACK → `BLOCKED_MISSING_IDENTITY`. Идентификатор,
  разрезанный границей окна (префикс ожидаемого, напр. `M0-`), считается своей
  строкой и перечисляется в `truncated_run_ids_after_ack`;
* **вне скоупа — только к сведению.** `fault_rows_before_ack` показывает latches,
  доставшиеся от предыдущей сессии (они попадут в её лог), но вердикт текущего
  прогона решают строки его скоупа.

## 2. Вердикты

| Вердикт | Значение |
|---|---|
| `PASS` | прогон: гейты + identity + no-HV — всё доказано |
| `BLOCKED_MISSING_IDENTITY` | нет подтверждения `run_id`, нет `map_id`/`map_crc32` после ACK или чужой `run_id` в строке @FOC после ACK |
| `FAIL` | нарушен no-HV гейт, калибровка/энкодер, потеря evidence, ожидаемые `@MC:REC`, **активный `FAULT`/`FAULT_R` (в т.ч. latched `HARDWARE_BREAK`), `RUN`/`FAIL` ≠ 0, `STATE` ≠ ожидаемого** |
| `INCOMPLETE_IDENTITY` / `INCOMPLETE_PROVENANCE` | кампания: identity или SHA отсутствуют |
| `SIMULATED` | офлайн-прогон оркестрации; никогда не физический baseline |
| `MISMATCH` | офлайн-верификация: сохранённый verdict ≠ пересчитанный |

`PASS` кампании означает **только** «пять независимых no-HV прогонов с
identity-evidence записаны». Это не разрешение на Stage A, DC-link, FOC,
V/f или characterization.

## 2.1 Офлайн-приёмка перед железом

Кампания не считается пригодной, пока не выполнены все пункты (проверяются
инструментом автоматически, кроме отмеченных как «оператором»):

```text
run_id = M0-R1 … M0-R5                  (в каждом прогоне свой, в логе свой)
map_id = M0                             (из firmware, НЕ из скрипта)
map_crc32 = одинаковый во всех 5 прогонах        → map_crc32_identical
map_id = одинаковый во всех 5 прогонах           → map_id_identical
firmware SHA = одинаковый во всех 5 прогонах     (один SHA на кампанию)
@RUN:ID ACK = точный (@RUN:ID=<id> в прямом ответе) → run_id_ack
@FOC = без усечения                              → uart_truncation_zero
CRLF = присутствует                              → log_integrity + парсинг
no-HV VBUS gate = PASS                           → no_hv_gate
```

Гейты `map_*_identical` и `uart_truncation_zero` добавлены в
`campaign_verdict`/`evaluate_run`: пять прогонов с разными `map_crc32` —
это пять разных измерений, а не baseline; ненулевой `uart_trunc` означает дыру
в потоке evidence.

## 3. Команды (PowerShell, ПК-3)

```powershell
# 1. Найти порт UART MCU (не угадывать из старого лога!)
py -3 tools\mopt0_capture.py run --list-ports

# 2. Предпросмотр без железа
py -3 tools\mopt0_capture.py run --dry-run

# 3. Кампания (папка должна быть новой; evidence неизменяем)
py -3 tools\mopt0_capture.py run `
  --port COM15 `
  --campaign D:\campaign_raw\mopt0_nohv_20260918Z `
  --firmware-bin build\firmware.bin `
  --confirm-dc-link-disconnected --confirm-pc4-zero --confirm-sd-high

# 4. Офлайн-перепроверка сохранённой кампании
py -3 tools\mopt0_capture.py verify --campaign D:\campaign_raw\mopt0_nohv_20260918Z
```

Физический `run` заблокирован (exit 2, папка не создаётся) без `--port` и без
всех трёх `--confirm-*`. Существующая папка кампании не перезаписывается.

## 4. Раскладка evidence

```text
<campaign>/
 ├─ metadata.json         # mode, SHAs, run_prefix, перечень гейтов, аргументы
 ├─ summary.json          # кампания: verdict, runs_total, runs_pass
 ├─ verify_report.json    # появляется после `verify`
 ├─ M0-R1/ uart.log + M0-R1.json
 ├─ M0-R2/ uart.log + M0-R2.json
 ├─ M0-R3/ uart.log + M0-R3.json
 ├─ M0-R4/ uart.log + M0-R4.json
 └─ M0-R5/ uart.log + M0-R5.json
```

`uart.log` — сырые байты UART с маркерами `[utc] TX/RX`; offline-верификатор
восстанавливает пары команда→ответ прямо из лога, без побочных файлов.

## 5. Стоп-условия (немедленно остановить кампанию)

- `default_deny=1` или `MOE=0` не подтверждён;
- `median(raw_vbus) > 9` или `max > 200` (шина не в no-HV);
- I1/I2 на рельсах (0 или 4095), `@ADC:CAL:FAIL`, `enc err ≠ 0`;
- неожиданный PWM/`@MC:REC`/fault/reset, потеря связи, запах/нагрев/шум;
- любое сомнение оператора. Fault не очищается автоматически.

## 6. Тесты

- `tests/test_mopt0_capture.py` — 33 pytest: no-HV гейт (включая fail-closed на
  неразобранном сэмпле), разбор identity, контракт run id (команда первой,
  подстановка `{run_id}`, обязательный ack, отказ прошивки), реконструкция лога
  из TX/RX-маркеров, вердикты кампании, simulated-прогоны, verify (в т.ч.
  `MISMATCH` по подделанному логу).
- `make test-hosted` (включая `tests/m_opt_0_nohv_test.exe`, 165 проверок) и
  `make test-py` должны проходить целиком; `make test-qemu` требует
  `qemu-system-arm`.
