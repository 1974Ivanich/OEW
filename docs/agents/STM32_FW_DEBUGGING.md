# STM32 Firmware Debugging — общая процедурная память агентов

> Дамп Hermes-скилла `stm32-firmware-debugging@1.2.0` (ПК-1, ветка
> `ai1/agents-skills-share-doc`, база `origin/main@f3ebb36`) — единая
> версия для ai1/ПК-1, ai2/ПК-2, ai3/ПК-3.
>
> Это **не** код проекта: прошивка, `Makefile`, `.ioc`,
> safety-модули (`foc`/`pwm`/`protect`/`vf`/`adc`/`adc_dispatch`) и
> стендовые артефакты не трогаются. Файл — общая процедурная память
> по отладке на плате (телеметрия, sigrok, SWO/ITM, UART, QEMU),
> плюс свод правил работы с длинными логами и HAL-антипаттернов.
>
> Соответствие Hermes-скиллу: `C:\Users\190\AppData\Local\hermes\skills\software-development\stm32-firmware-debugging\SKILL.md`.
> Скилл — локальный; этот файл — канонический проектный аналог.
> При расхождении правьте оба сразу.

# STM32 Firmware Debugging (telemetry + sigrok + UART)

Debugging STM32 firmware on real hardware (Nucleo boards, inverter boards,
motor drives) when "signals are missing", "nothing works", or a scope/logic
analyzer shows no output. The core discipline: **read the UART telemetry and
FAULT state BEFORE touching the scope/wiring**.

## When to use

- Hardware test shows "no signal" / "no transitions" on scope or sigrok
- GUI/auto-test reports FAIL on channels that worked before
- New commands return `unknown` after a git pull (firmware on board is stale)
- Any STM32 session with UART telemetry + logic analyzer involved

## Golden rule: telemetry first, scope second

1. **Read the newest UART log** first: `ls -t logs/test_log_*.log | head -1`, grep
   for `[sent]`/`[received]`, `FAULT=`, `@PWM:OK`.
2. **Check FAULT before blaming wiring.** `@FOC:...FAULT=1:FAULT_R=N` means the
   firmware disabled PWM — there will be NO signals anywhere, by design.
   - Decode `FAULT_R` from `src/protect.h`: 0=none, 1=OVERCURRENT,
     2=VBUS_HIGH, 3=VBUS_LOW.
   - `VBUS_LOW` (FAULT_R=3): bus voltage below `PROTECT_VBUS_MIN_MV` (e.g. 8 V)
     — bench PSU set too low. Raise supply (12-48 V per test spec), clear with `f`.
3. **Confirm PWM actually runs** before capturing: send `p=<arr>,<duty>,<dt_ns>,<mask>`
   (mask 0x3F = all 6 phases), expect `@PWM:OK` reply.
4. Only then capture with sigrok and interpret channels.

## Firmware-on-board staleness

After `git pull` with new commands, the board still runs old firmware →
new commands reply `unknown`. This is NOT a code bug. Per project rules:
`make && make flash` (or `flash.bat`) after every change, then reconnect UART.
Verify version banner in the log.

## sigrok / Saleae pitfalls (fx2lafw clones)

- **Clone detection:** `sigrok-cli --scan` shows only `demo` device when the
  clone (Cypress FX2, VID 04B4:8613) has no WinUSB driver. Fix: install
  WinUSB via **Zadig** (Options → List All Devices → select Cypress FX2 →
  Replace Driver). Then scan shows `fx2lafw - Cypress FX2 with 16 channels`.
- **Throughput limit:** clone FX2 sends only ~1 286 144 samples per capture
  regardless of requested duration — 13ch @ 8 MHz ≈ 0.16 s, then
  "Device only sent N samples" or sigrok-cli exits non-zero on longer requests.
  For long captures: fewer channels or lower sample rate. Check
  `logs/sigrok_errors.log` for the exact failing command line.
- **Capture window vs channel map mismatch:** auto-test "no signal" on
  channels that are outside the captured `digital_chs` range is a GUI bug,
  not hardware. E.g. Inv2 channels on D6-D11 but capture used `range(8)`
  (D0-D7) → D8-D11 always "no signal". Fix the capture range, not the wiring.
- **Manual CSV analysis:** count transitions per channel from the CSV
  (skip `;` comment rows) to see which physical probes actually have signal.

## Nucleo-64 pin conflicts (solder bridges)

On Nucleo boards, **one physical connector pin can route to TWO MCU pins via
solder bridges (SB)**. Example (MB1367): CN7 pin 38 = ARD_A5 is shared by
PC0 (SB36) and PA15 (SB37), both default OFF; PA15 also on CN7 pin 17 (SB9).
Enable only the SB for the signal you need — enabling both shorts two MCU
outputs. When two pins seem to "collide" on one header pin, check the board
manual (UMxxxx) Table "Solder bridge control" before reworking wiring.
Extract text: `pdftotext -layout umXXXX.pdf out.txt`.

## UART from scripts (no pyserial)

When the GUI is disconnected and pyserial is unavailable, drive the COM port
via PowerShell. **CRITICAL: PowerShell does NOT interpret `\r\n` inside
single quotes** — `$port.Write('cmd\r\n')` sends the literal backslash
characters and the board answers `unknown` or nothing. Use double quotes with
backtick escapes, or concatenate `[char]13`/`[char]10`:

```powershell
$port = New-Object System.IO.Ports.SerialPort('COM15',115200,[System.IO.Ports.Parity]::None,8,[System.IO.Ports.StopBits]::One)
$port.ReadTimeout = 800; $port.Open(); Start-Sleep -Milliseconds 200
$port.Write('p=99,15,1500,63' + [char]13 + [char]10); Start-Sleep -Milliseconds 800
$buf=''; while ($port.BytesToRead -gt 0) { $buf += $port.ReadExisting() }
$port.Close(); $buf
```

Remember: a GUI holding the COM port will conflict — close it first.

## Stale ADC cache vs fresh read (frozen telemetry)

`@FOC:` telemetry reads cached `adc_data[]`; the command `a` forces a fresh
ADC read that answers `@ADC:I1=..:I2=..:Ires=..:VBUS=..` (raw codes). If the
two disagree (e.g. `@ADC:VBUS=165` but `@FOC:VBUS=6043`), the cache is
**frozen** — see the JADSTART pitfall below. Reading the log after the fact
can also mix stale pre-command lines with the fresh `@ADC` reply; filter for
`@ADC` specifically and take the last lines.

## PWM_Disable must disarm the ADC injected group (JADSTART)

If `JADSTART` stays set after PWM stops, `ADC_StartConversion()` early-returns
(`if(ADC2->CR & ADC_CR_JADSTART) return;` in `adc.c`), so `adc_data[]`
freezes **forever**: VBUS and currents never refresh, and a real
`FAULT_R=3 VBUS_LOW` can never clear even after the supply is fixed — the
board "doesn't see" the corrected voltage. Fix: in `PWM_Disable()` mirror
`PWM_Enable()` — `JADSTP` + wait-loop + clear `JEOS|OVR`, guarded by
`if(CR & JADSTART)`. After flashing, verify live: `a` → `@ADC:VBUS=<raw>`,
then `f` → `@FOC:...VBUS=<mV>:FAULT=0`. Full case study:
`references/adc-jadstart-cache-freeze.md`.

## Simulating supply voltage via a sense pin — mind the divider

The sense pin sits AFTER the divider. OEW board:
`Vbus_mV = raw·3300·125/4095` (divider 1:125 on PC4). Injecting **1.65 V** on
PC4 reads as **206 V** → above the 80 V max → `FAULT_R=2 VBUS_HIGH` (a NEW
fault, not the cure). To simulate 12–24 V inject **96–192 mV**. Always compute
with the firmware's own formula before injecting, and state the mapping
table (V_target → mV on pin) when instructing the user.

## Web-AI code review: verify every claim against the code (2026-08 lesson)

The project's review loop: an external web AI (GPT/Claude/Gemini) reviews one
file at a time, the user pastes findings, Hermes verifies. **Do NOT apply or
refute a finding on its word — verify against the real code/CMSIS first.**
In one pass (adc.c/pwm.c/main.c/autotune.c reviews) roughly HALF the "critical"
claims were wrong, and several real bugs were confirmed:

- **Claim "HSE is not enabled, PLL broken" → FALSE.** On STM32G474
  `PLLSRC=2 (10b)` = **HSI16** (CMSIS: `RCC_PLLCFGR_PLLSRC_HSI = 0x2`,
  `PLLSRC_HSE = 0x3` — note the inverted numbering vs other STM32 families).
  HSEON is not needed; 16/4×85/2 = 170 MHz. Confirm by reading the actual
  CMSIS defines, not the reviewer's family-generic knowledge.
- **Claim "2 TRGO events per PWM period → FOC at 10 kHz, not 5" → FALSE.**
  `RCR=1` in center-aligned mode divides 2 UEVs/period → exactly 1 TRGO =
  5 kHz. The RCR comment in pwm.c already documents this.
- **Claim "PWR Range 1 not configured → 170 MHz unstable" → FALSE.**
  On G4 the reset value of VOS = 0x00 = `PWR_REGULATOR_VOLTAGE_SCALE1_BOOST`
  (HAL constant is 0x00000000) — 170 MHz works out of reset; ST example code
  sets it "explicitly for clarity".
- **Claim "adc_ovr/jeos counters never incremented" → FALSE.** They live in
  `ADC1_2_IRQHandler` in **main.c**, not adc.c — the reviewer only saw adc.c.
- **Claim "ADC_StartConversion returns before data ready" → FALSE.** It is
  synchronous: `adc2_read()` waits for EOC; `PROTECT_Check()` right after gets
  fresh data.
- **Real bugs found & fixed this pass:** comment `12791 mA/count` was ×1000
  wrong (true: 12.79); `AT_CalcIsat` interpolated between two points BELOW the
  threshold instead of last-above/first-below; dead macro
  `AT_IDLE_OPEN_PHASE_PCT` (literal `/5` vs intended `/10`); `ADC_InjectedStart`
  didn't clear OVR/JQOVF; `p=` debug command re-configured running PWM without
  stopping injected ADC; GUI parsers (nucleo_debug_tool.py) expected the OLD
  telemetry format while firmware sends verbose `@AT:PARAMS:Rs_mOhm=...`
  (sync GUI regexes to the firmware, not the doc).

Pattern: (1) grep the real code/CMSIS header for the contested fact,
(2) check the project's own constants/comments (they often already document
the answer), (3) only then fix. When fixing a GUI parser, verify regexes
against REAL firmware lines, not the doc examples — docs drift.

## SWO/ITM printf debugging (free UART, second channel)

Add printf-style debug output on PB3 (TRACESWO, AF0) via ITM stimulus port 0
so USART2 stays free for GUI telemetry. Three rules make it work:

1. **SWO_SendChar MUST have a timeout** — `while(!(ITM->PORT[0].u32 & 1UL)){ if(--t==0) return; }`.
   Without it, an unconfigured TPI hangs main() forever.
2. **No SWO output during firmware init** — ITM FIFO fills before the debugger
   attaches → `ITM_TCR_BUSY_BIT` timeout on every later attach. Output on
   demand (e.g. UART command `s`).
3. **TPI is configured by the DEBUGGER, not the firmware** — firmware touches
   only DEMCR + ITM (LAR unlock, TCR, TER). Writing `TPI->SPPR/ACPR/FFCR` from
   firmware also causes ITM_TCR_BUSY.

**Capture: OpenOCD + ST-Link V2 does NOT work** (`SWO frequency is not
suitable` / `enable deferred` / ITM_TCR_BUSY). Use STM32CubeProgrammer CLI:

```bash
STM32_Programmer_CLI.exe -c port=SWD -swv freq=170 portnumber=0 -RA
```

- Parameter is **`portnumber=`**, NOT `port=` (that fails with "missing some
  arguments"). `freq` = system clock in MHz.
- `log=<path>` is ignored — output goes to
  `C:\Users\<user>\STMicroelectronics\STM32CubeProgrammer\SWV_Log\swv.log`.
- Runs until killed (background). Trigger firmware print via UART, then tail swv.log.
- CMSIS: `ITM_TCR_TRACEENA_Msk` doesn't exist → `(1UL<<24)`; TPI block is
  named `TPI`, not `TPIU`.
- For real use (not just the `s` test): duplicate debug output to UART+SWO via
  `DBG_STR`/`DBG_FMT` macros (menu/errors/status), keep `@`-telemetry and the
  `"> "` prompt UART-only. Pattern in the reference below.

Full firmware pattern + session detail:
`references/swo-itm-printf.md`.

## QEMU bare-metal тест математики (без железа)

Эмуляция Cortex-M4 (`-M olimex-stm32-h405` или `netduino2`) для юнит-тестов
чистой математики (Clarke/Park/PI и т.п.) без платы. Пример в проекте:
`tests/foc_math_test.c` + `tests/mocks/` (мок CORDIC) + `tests/qemu_startup.s`
+ `tests/qemu_test.ld` — 19 проверок, hosted (x86) и QEMU.

Подводные камни bare-metal QEMU (все проверены на практике):

1. **Никакого libm!** `sinf/cosf` из arm-none-eabi libm при `-nostdlib`
   вызывает semihosting-заглушки → `Data Abort at 0x7FFFFF8` в QEMU.
   Пишите собственный полином (ряд Тейлора до x^15 даёт <1 LSB Q15).
2. **Никакого rdimon.specs с собственным startup** — rdimon crt0 не
   вызывается, `__sfvwrite_r` падает в Default_Handler. Нужен свой
   минимальный `qemu_startup.s` (векторная таблица + Reset_Handler → main).
3. **SP: `ldr sp, =0x20020000`, НЕ `= _estack`** — метка в .isr_vector
   резолвится в адрес FLASH (0x08000000) → запись в FLASH → Data Abort.
4. **`.isr_vector` должен лежать ПЕРВЫМ** по 0x08000000 — нужен linker
   script (KEEP(.isr_vector) в начале), иначе QEMU читает .text как векторы.
5. **Semihosting вывод: bkpt 0xAB, r0 = номер операции (0x04 WRITE0),
   r1 = указатель** (не наоборот!). SYS_EXIT: r0=0x18, r1=0x20026 —
   завершает QEMU сам (иначе висит, убивайте timeout-ом).
6. **Semihosting-вывод идёт в stderr** QEMU — при проверке из скрипта
   читайте stdout+stderr вместе.

**Мок-паттерны для модульных тестов (новые, 2026-08):**

- **Реальный layout структуры:** НЕ делайте мини-мок структуры
  (`struct MotorParams { Lm_uH, Rr_mOhm, Tr_rotor_us; }`) — смещения полей
  не совпадут с реальной `src/autotune.h`, и `pole_pairs` прочитается как
  мусор (16 вместо 2). В stub включайте реальный заголовок:
  `#include "../src/autotune.h"` и `MotorParams g_motor_params;` (typedef,
  без `struct` — иначе `conflicting types`).
- **Контролируемый «сенсор»:** если модуль сам читает датчик внутри
  (`VFC_Update()` вызывает `ENC_GetSpeed_rpm()`), запись `vfc.measured_rpm`
  напрямую перезатирается. Stub возвращает глобальную переменную:
  `int32_t test_enc_rpm; int32_t ENC_GetSpeed_rpm(void){return test_enc_rpm;}`
- **Per-test stub split:** если тест A линкует реальный модуль, а тест B —
  заглушку того же символа (`FOC_IsRunning`), выносите заглушку в отдельный
  файл (`vfc_stub.c`), линкуемый только тестом B — иначе `multiple definition`.
- **Mock-заголовок shadowing:** `tests/mocks/vf_control.h` (пустая заглушка)
  маскирует реальный `src/vf_control.h` через `-I tests/mocks` — удалите мок
  и добавьте `-I src`, чтобы реальный заголовок побеждал.
- **Ожидания = реальный алгоритм, не учебник:** V/f ramp экспоненциальный
  (`cur += (tgt-cur)*dt/ramp_time`) — цель 500 rpm за 2000 мс даёт ≈316
  (= 500·(1−1/e)), НЕ 500. При падении проверки сначала сверьте ожидание с кодом.
- V/f-тест: `tests/vf_control_test.c` (16 проверок: ramp, U/f, boost, f_e,
  theta, 3ф синус, duty, клиппинги); общий запуск — `make test`
  (hosted+QEMU, 35 проверок суммарно). Подробности bare-metal QEMU —
  в отдельном скилле `stm32-hosted-qemu-testing` (шаблоны startup.s/ld).

Запуск: `qemu-system-arm -M olimex-stm32-h405 -nographic
-semihosting-config enable=on,target=native -kernel test.elf`

## ObservationPack: подавай длинные логи в модель сводкой, а не as-is

При работе со стендовыми прогонами и прошивкой почти всё «жирное» —
`logs/test_log_*.log`, вывод `make test` / `make hwt-run`,
`build/hwt/*/result.json` + junit, дампы Flash после `make backup`,
таблицы регистров из RM0440 — попадает в контекст повторно с каждым
следующим запросом. Это раздувает входные токены и сбрасывает
prompt-cache провайдера. Подход из SoL-Pi (NVIDIA/MIT/NTU, arXiv:2609.20519,
сентябрь 2026): для вывода >10 KiB в контекст уходит head + tail + размер
+ sha256 + короткая сводка, а полный текст — по ссылке на
`$LOCALAPPDATA/Temp/...`. У нас это уже частично делает `terminal` (режет
>50KB и сохраняет полный текст в файл) — не дублируй файл в чат, дай
только сводку.

### Правила применения в OEW-сессии

1. **Длинный `terminal` / `read_file` (>10 KiB) — дай пользователю полный
   вывод один раз, а в следующий — только суть + sha256 + путь к файлу.**
   Пользователь сам прочитал; не заставляй модель повторно читать то же.
2. **`logs/test_log_*.log` целиком в модель НЕ клади.** grep нужного
   (`[sent]`, `[received]`, `FAULT=`, `@PWM:OK`, `@ADC:`, `@FOC:`, `ERROR`)
   и дай только релевантные строки + счётчик совпадений. Полный лог —
   по запросу по пути.
3. **`build/hwt/*/result.json` + junit** — это **исключение**: цитаты и
   точные значения нужны для приёмки (`ERROR ≠ PASS`). В контекст идё
   либо весь файл, либо ничего; ремейку НЕ подлежит.
4. **Таблицы регистров (RM0440, .ioc, карта TIM1/TIM8/ADC2)** — давай
   модельной стороне модели только выжимку (PSC, ARR, dead-time, каналы
   ADC), полную таблицу — по запросу. Это связано с
   `cubemx_check.py`: точные значения не теряем.
5. **Action Fusion: батчивай в один блок всё, что не зависит от
   предыдущего вывода** — `read_file` нескольких файлов,
   `search_files` по разным regex, начальный `git status` + `git
   log --oneline -5`. Уже стандартная практика — продолжай.
6. **Никогда не сжимай:** активный план задачи, последние 2 шага
   диалога, активный планшет на стенде, `.ioc`-значения, формулы
   из `src/protect.h` / `src/protune.c` (пороги, коэффициенты).
7. **Evidence-Preserving Reducer (упрощённо):** перед тем как дать
   основной модели длинный вывод терминала, спроси себя — есть ли
   локальная/дешёвая модель, которая может сделать выжимку (Ollama
   llama3.1:8b для механики, Z.ai для тяжёлого мышления). Если да —
   сначала выжимка, потом основная модель работает с кратким.

### Что это даёт в OEW

- Длинные стендовые сессии (HWT-прогоны, шунт-калибровка,
  bench_inject_check) перестают упираться в лимит контекста посреди
  приёмки.
- Меньше обрывов mid-turn на бесплатных провайдерах (Vireonix /
  OpenRouter `:free`) из-за rate-limit'ов.
- Скорость ответа модели не падает (наблюдаемые накладные расходы —
  один лишний grep перед отправкой).

### Чего НЕ делать

- Не редуцируй `result.json` / junit / `.ioc`-таблицы — это источник
  истины для `make hwt-run` и `cubemx_check.py`.
- Не сжимай активный план задачи: при fail-closed до карты
  (`campaign_raw — неизменяемый исходник`) потерянный шаг = провал
  приёмки.
- Не выбрасывай точные цитаты из RM0440, когда они нужны для
  исправления (PLLSRC=10b=HSI16 на G4 — это уже было поймано
  web-AI-ревью и закреплено в Pitfalls).
- **Не переноси «готовые примеры» из обзорных туториалов (Habr,
  RUVDS и т.п.) в наш код.** Типичный пример: статья «Hello World
  для STM32» (Habr/ruvds, статья 1083826) показывает `HAL_UART_Transmit`,
  `HAL_Delay`, `initialise_monitor_handles`, semihosting с
  `--specs=rdimon.specs`. Всё это **HAL**, и под наш CMSIS-only
  контракт не подходит даже в качестве шаблона: инициализация USART,
  тактирование и GPIO пишутся через CMSIS-регистры, `_write`/printf
  идёт через USART2 напрямую, `Delay()` — через `DWT->CYCCNT` или
  свой таймер, а не через HAL_Delay. Если в ревью видишь HAL_* в
  нашем коде — это ошибка применения правила №1 AGENTS.md, а не
  «нормальный код».
- **Semihosting + rdimon — только для QEMU-тестов**, не для
  прошивки на железе. На реальной плате semihosting зависит от
  версии J-Link/ST-Link/OpenOCD и IDE (автор того же туториала
  сам застрял на «не работает с J-Link под Linux»); на стенде
  роль semihosting'а играет ITM+SWO — быстрее, не останавливает
  CPU. В QEMU-сессии (`make test` через
  `qemu-system-arm -semihosting-config enable=on,target=native`)
  semihosting допустим и обязателен (см. QEMU-секцию выше).

## Project context

- OEW motor project: `C:\ST\boyler\Motor` — docs `PROJECT_OVERVIEW.md`,
  `pinout.md`, `AGENTS.md` (CMSIS-only, `make`/`make flash`), UART CLI in
  `main.c`, thresholds in `src/protect.c`.
- References: `references/nucleo-mb1367-solder-bridges.md` — the UM2505 SB
  table + CN7/CN10 pinout extracted for the G474RE board.
  `references/adc-jadstart-cache-freeze.md` — full case study of the frozen
  ADC cache caused by JADSTART not being disarmed in `PWM_Disable()`.
  `references/swo-itm-printf.md` — SWO/ITM firmware pattern + capture recipe
  (STM32_Programmer_CLI) + patch-tool \r\n gotcha.

## Pitfalls recap

1. FAULT (esp. VBUS_LOW) blocks PWM → "no signal" everywhere. Telemetry first.
2. Stale firmware → `unknown` on new commands. Flash after pull.
3. FX2 clone: 1.28M-sample cap; WinUSB driver via Zadig; check sigrok_errors.log.
4. GUI capture window must cover the channel map's D-indices.
5. Nucleo SB bridges can merge two MCU pins onto one header pin — read the
   manual table, don't rewire blind.
6. Frozen ADC cache: `@FOC` stale while `@ADC` fresh → JADSTART not disarmed
   in `PWM_Disable()`. Fix there, not in the GUI.
7. PowerShell UART: `'\r\n'` in single quotes is literal backslash-r — use
   `[char]13+[char]10` or double quotes.
8. Sense-pin injection: divide by the divider first (1.65 V on a 1:125 pin =
   206 V → VBUS_HIGH). Simulate 12–24 V with 96–192 mV.
9. SWO/ITM: SendChar needs a timeout; no init-time output (ITM FIFO busy);
   TPI configured by debugger; OpenOCD+ST-Link V2 can't capture — use
   `STM32_Programmer_CLI -swv freq=<MHz> portnumber=<N> -RA`.
10. **AI-модели для отладки и анализа**: При работе с прошивкой часто требуется анализ логов и данных. Используйте:
    - **DeepSeek-V4-Flash** для анализа логов и поиска ошибок (5M бесплатных токенов)
    - **Qwen3.8-Flash** для неограниченного анализа кода и данных
    - Обе модели имеют полную совместимость с OpenAI API
    - Для сложных математических расчетов (FOC алгоритмы) используйте DeepSeek-R1
    - Включайте контекст логов и телеметрии для более точного анализа

---

## Процедура обновления этого файла между агентами

- Источник правды — Hermes-скилл на ПК-1
  (`C:\Users\190\AppData\Local\hermes\skills\software-development\stm32-firmware-debugging\SKILL.md`).
- Правки делаются на ПК-1, коммит в ветку `ai<N>/agents-skills-share-doc`,
  строка в `docs/AGENTS_STATUS.md`, push + `git ls-remote` для подтверждения
  SHA — только для крупного пакета (см. `AGENTS.md`).
- Принимает ПК-1 (merge в `main`); ПК-2/ПК-3 получают свежий main через
  свой `git fetch upstream && git rebase upstream/main`.
- Расхождение между скиллом и этим файлом = баг. Сначала правь скилл,
  потом синхронизируй файл в том же коммите.