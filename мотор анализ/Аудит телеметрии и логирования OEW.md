# Аудит телеметрии и логирования OEW

**База аудита:** `main` `0dd7f4a2fd70d5a9600d977b6e164fb58560fbb9`  
**Режим:** read-only; исходное дерево репозитория не изменялось.  
**Охват:** firmware UART/SWO, periodic telemetry, V/f CSV logging, основной Nucleo GUI, отдельные FOC/Measurement GUI и pure Python telemetry parsers.

> Главный результат: транспорт UART и safety-контракт ISR спроектированы корректно, но текущий `@VFLOG` на 50 Гц почти полностью занимает 115200-baud канал. Это делает `drp` ожидаемым штатным индикатором перегрузки при одновременной обычной телеметрии. На стороне GUI наибольший practical hot path — синхронный `flush()` каждого V/f CSV sample и неограниченный рост Tk `Text` logs.

## Проверенные контракты

| Область | Подтверждённое свойство | Результат |
|---|---|---|
| UART TX MPSC | `src/uart.c` защищает reserve/copy/advance producer ring через сохранение и восстановление PRIMASK. ISR priority 0 не смешивает `tx_head` с main/TIM6 producer. | Корректно; не оптимизировать за счёт возврата BASEPRI. |
| ISR telemetry | `UART_TrySendTelemetry()` формирует packet вне critical section, затем atomically резервирует строку. При заполнении ring packet отбрасывается целиком; priority-inversion deadlock отсутствует. | Корректно и safety-preserving. |
| V/f observability | `@VFLOG` включает `drp=UART_GetDroppedCount()`, поэтому удалённый consumer видит накопленную потерю ISR telemetry. | Хорошая диагностика, но счётчик только cumulative. |
| SWO | `SWO_TrySend*()` не блокирует: при неготовом ITM/TER/stimulus line обрывается, incrementing drop counter. | Корректно для debug-only канала. |
| Parser contract | `telem_parser.py` изолирован от GUI/serial, `@VFLOG` стабилизирован 19 полями с `None` для missing keys. | Хорошая тестируемость и CSV schema stability. |
| Входные протоколы | `measurement_gui` и `foc_control_gui` посылают CRLF framing; FOC GUI принимает production `Ires` telemetry. | Недавние P0-fixes сохранены. |

Проверки во время аудита: `uart_test` — 10/10 PASS; `tests/test_telem_parser.py` — 6 passed; `py_compile` всех telemetry GUI/parser modules — PASS; рабочее дерево чистое.

## Подтверждённый bottleneck: budget `@VFLOG`

`main.c:177–191` формирует `@VFLOG` в TIM6 ISR c default period 20 ms, то есть 50 Гц. Временный C budget test с достижимыми консервативными bounds V/f/encoder дал **206 bytes per packet**. UART 115200 8N1 передаёт максимум **11 520 payload B/s**:

| Расчёт | Значение |
|---|---:|
| `@VFLOG` worst reachable packet | 206 B |
| `@VFLOG` at 50 Hz | 10 300 B/s |
| UART payload capacity, 115200 8N1 | 11 520 B/s |
| Остаток до любого другого трафика | **1 220 B/s** |
| Загрузка канала только `@VFLOG` | **89.4%** |

Независимый foreground loop каждые 100 ms посылает `@VF` во время V/f (`main.c:612–617`). Он один уже потребляет часть 1 220 B/s headroom; debug, CLI responses, `@TRIG`, error/status lines и any burst further consume budget. Следовательно, при high-value raw ADC input/extended decimal values transport закономерно входит в drop mode. Это не safety failure: ISR не блокируется, но V/f telemetry CSV может иметь пропуски.

Проверка абсолютных `int32` extrema также показала, что общий `@VFLOG` format может достигать 285 B, то есть превысить 256-byte local `UART_TrySendTelemetry` buffer. Нынешние V/f field bounds делают такой набор недостижимым в normal operation, поэтому это **не текущий production defect**. Однако новый field или расширение domain без packet-length regression может silently truncate packet и убрать final CRLF; формат следует считать budget-constrained контрактом.

## Рекомендации firmware-side

| Приоритет | Узел | Безопасная оптимизация | Ожидаемый эффект и guardrails |
|---|---|---|---|
| P1 | `main.c` `@VFLOG` | Ввести явно документированные telemetry profiles: compact default для online logging, extended/raw только по отдельной commissioning/debug command. Либо уменьшить default V/f period до 25–40 Hz, не меняя V/f control rate. | Возвращает устойчивый headroom; control loop остаётся 1 kHz. Нельзя менять существующий format под тем же включателем без версии/schema negotiation. |
| P1 | `UART_TrySendTelemetry` | Добавить explicit formatted-length/truncation accounting: проверять `vsnprintf` result; increment отдельный format-truncation counter и never enqueue incomplete line. | Предотвращает склейку следующей line после hypothetically truncated packet. Не изменять current ISR nonblocking semantics. |
| P2 | UART diagnostics | В `@VFLOG` передавать per-session или delta dropped count alongside cumulative `drp`, а CLI дать `uartstats` с TX/RX error/overflow/drop. | Делает loss rate измеримой без клиентского diff cumulative counter; никаких blocking reads. |
| P2 | cadence ownership | При включённом `vflog`, suppress/reduce redundant foreground `@VF` 10 Hz, поскольку `@VFLOG` уже несёт target/meas/fe/fslip/vmag. | Экономит bytes без потери V/f observability. Сначала зафиксировать GUI compatibility test: некоторые consumers могут отображать `@VF` отдельно. |
| P3 | SWO diagnostics | `SWO_GetDropped()` может получить line-level/byte-level distinction и explicit reset/emit command. | Улучшает debug observability; не относится к production control. |

## Python GUI и файловое логирование

### P1: per-row `flush()` в V/f CSV

`vf_panel.py:319–328` выполняет `csv_writer.writerow()` и `self.csv_fp.flush()` на **каждом** `@VFLOG` sample. При 50 Hz это 50 user-space flush calls/s плюс Tk GUI callback work. `flush()` обычно не равен physical `fsync`, поэтому заявленная гарантия «защита от потери данных при аварийном стопе» частична: ОС page cache всё равно может не попасть на носитель при process/OS/power failure.

Рекомендуемый контракт: batched flush every N samples or bounded time, например 10–25 samples/200–500 ms, plus mandatory flush+close on `_close_session()`/firmware stop. Для режима, который действительно требует crash/power durability, предоставить optional explicit `fsync` mode с documented throughput cost. Не следует silently remove flush без replacement lifecycle test.

### P1: unbounded Tk `Text` growth

`nucleo_debug_tool.py:_log`, `foc_control_gui.py:_log` и `measurement_gui.py:_log` добавляют каждую telemetry line в Tk `Text`, вызывают `see(tk.END)` и не ограничивают buffer. При long V/f/autotune session GUI rendering and memory use растут с длительностью, даже если filesystem log уже содержит полный history.

Нужен display retention limit, например 2 000–10 000 visual lines/maximum characters, с bulk trim и autoscroll only если view уже находился в конце. Полный history должен остаться в on-disk log или export, поэтому ограничение UI не является потерей forensic data.

### P1: main GUI synchronous file flush

`NucleoDebugTool._log_to_file()` выполняет write+flush для каждой received/sent/telemetry line в GUI thread. При текущей V/f cadence это добавляет disk I/O к parse/routing/Tk update. При медленном носителе `_process_queue()` отстаёт, bounded RX queue начнёт вытеснять старые lines.

Нужен buffered writer: `queue.Queue` to a dedicated file worker или GUI-thread flush timer. Worker должен иметь bounded queue and explicit policy: telemetry may coalesce/drop with counter, while errors/commands/state transitions are lossless/high priority. Close must drain+flush before destroy.

### P2: `measurement_gui` list queue

`measurement_gui.py:281–291` использует shared ordinary list между reader и GUI thread и `pop(0)`, что имеет O(n) cost и relies on CPython implementation details. FOC GUI уже демонстрирует правильный pattern `queue.Queue` + `get_nowait()`. Заменить Measurement queue на that pattern before increasing message rate. Это также уменьшает latency tail under burst.

### P2: V/f session directory collision and write-error state

`vf_panel.py:145–148` names directories only to seconds and uses `exist_ok=True`; two sessions started within one second can overwrite `telemetry.csv`/`meta.json`. `session_id` has needed uniqueness but not filename. Use timestamp with microseconds or suffix session ID and fail closed if existing directory is unexpectedly nonempty.

In `vf_panel.py:319–328`, repeated `OSError` is swallowed without closing CSV or setting an error state. The UI may remain green while every following row fails. On first write failure, close/disable writer, preserve error reason in session metadata/status and avoid repeated exceptions.

### P3: queue and parser efficiency

`NucleoDebugTool.rx_queue` is bounded (4096) and correctly drops oldest telemetry under GUI backlog. Improvements are operational rather than correctness-critical: count queue drops, batch up to a time/line budget per `_process_queue()` tick, and coalesce only high-rate telemetry display updates while preserving each raw line to file writer. Pure parser `_payload()` currently uses multiple string allocations (`strip`, split CR, split LF); at 50 Hz this is negligible. Optimize it only after collecting a profile.

## Recommended test extensions before implementation

| Change class | Regression contract |
|---|---|
| Firmware packet/cadence | A host test that uses real bounded fields and asserts `snprintf` length < buffer, terminator present, 50 Hz byte budget documented; a compatibility snapshot of legacy `@VFLOG`. |
| UART counters | Ring saturation test verifies atomic whole-line drop, delta/session counters and no ISR blocking. |
| CSV batching | `tmp_path` test verifies every expected row reaches CSV after stop; injected write failure changes GUI/session state exactly once. |
| Log retention | Test inserts more than limit and verifies bounded Text content while on-disk log retains all records. |
| Session naming | Two starts within one mocked second produce distinct dirs and independent metadata/CSV. |
| Measurement queue | Burst reader/consumer test checks FIFO order, no lost callback and no O(n) list path. |

## Safety boundaries

None of these optimizations should call blocking `UART_Send*` from ADC/TIM6 ISR, reduce PRIMASK protection of UART MPSC reservation, change `@VFLOG` field meaning without protocol versioning, or suppress/overwrite fault/status lines. Any throughput work must preserve terminal fault telemetry and the fail-closed control path.

## Suggested package decomposition

A safe implementation should be split by trust boundary: first firmware telemetry budget/truncation/cadence with hosted snapshot tests; second GUI log batching/retention/session-path robustness; third diagnostic counters and Measurement queue. This avoids conflating wire-protocol evolution, safety-adjacent ISR work and GUI I/O changes in one acceptance package.
