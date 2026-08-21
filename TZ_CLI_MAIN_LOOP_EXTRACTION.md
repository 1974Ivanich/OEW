# ТЗ: Полный перенос command-loop из main.c в CLI_ProcessLine (эквивалентное извлечение)

**Проект:** OEW Motor Drive — STM32G474RE, CMSIS-only, OEW dual-inverter FOC (2× STEVAL-IPM20B), асинхронный двигатель.
**База:** HEAD c524d41 (main, после BVG-пакета: cli.c/h + cli_test.c уже в репо, но dispatcher неполный).
**Цель:** заменить большой command-loop в `main.c` (строки 467-901: 37 команд, UART-протокол для GUI) вызовом `CLI_ProcessLine()` с **100% эквивалентным поведением**: та же последовательность распознавания, те же guard'ы, те же форматы вывода (строки `@...` — это протокол с `nucleo_debug_tool.py`, их менять НЕЛЬЗЯ), те же return-коды. Устранить дублирование: существующий `src/cli.c` (101 строка, 9 команд) заменяется полным dispatcher'ом.

**Жёсткое ограничение:** поведение каждой команды — 1-в-1 с `main.c:467-901` на базе c524d41. Не «улучшать», не переформатировать, не менять порядок. Отклонение от протокола = регрессия GUI.

---

## 1. Контекст: что есть сейчас

- `main.c:464-922` — `while(1)`: `UART_ReadLine` → цепочка `if/else if` (37 команд) → периодика (ADC stream, 100 мс телеметрия)
- `src/cli.c` (существующий) — неполный: `p?`, `p=`, `1`, `0`, `f`, `mp=` (3 арг), `mpapply`, `vf=`, `enc` — через `CLI_Ops` (колбэки). Остальные 28 команд — только в main.c
- `tests/cli_test.c` — hosted-тест на колбэках (линкуется только `cli.c + cli.h`, без модулей)
- Модули доступны из `src/*.h`; глобалы — extern: `g_motor_params` (autotune.h:94), `g_autotune_abort` (autotune.h:95, volatile), `vfc` (vf_control.h:46)
- `DBG_STR/DBG_FMT` — макросы `main.c:36-40`: **дублируют вывод в SWO** (UART + SWO). Это поведение должно сохраниться (см. 4.1)
- `OEW_MAP_CAPTURE`/`OEW_MAP_L3` — `#ifndef`-дефолты `0` в `main.c:16-20`; команды `mcarm=`/`mapcap *` компилируются только при включении (commissioning)

---

## 2. Полный реестр команд (порядок распознавания — КАК В main.c, менять нельзя)

Порядок if/else в main.c (строки): `a` (474), `a=` (478), `a?` (483), `c` (484), `p?` (501), `p=` (505, sscanf >=3), `1` (520), `0` (529), `mcarm=` (537, #if OEW_MAP_CAPTURE), `mapcap run` (551), `mapcap drain` (555), `mapcap build=` (576, #if OEW_MAP_L3), `mapcap abort` (585), `mapcap status` (588), `m` (598), `s` (599), `f` (603), `s=` (621, %ld+trail, диапазон ±50000), `dump` (629), `dumpa` (635), `dump8` (644), `pdump` (650), `sysinfo` (661), `pp=` (669, 1..24, guard running), `vdc=` (680, 10..400), `fwbase=` (688, 100..5000), `dt=` (694, ≤12700, guard PWM), `curve` (700), `params` (703), `irot` (706), `inertia` (710), `ch` (714), `chu/chv/chw` (720), `iv` (729), `pairs` (735), `abort` (741), `oew` (744), `rr` (748), `noload` (752), `scope` (756), `pi=` (760), `lspos` (764), `mp=` (768, sscanf >=2, **8 параметров**, маски AT_VALID_*), `mpapply` (797), `piapply` (815), `stats` (824), `idle` (827), `i=` (837), `vf=` (843, диапазон ±5000, TRIG_High/Low, авто-старт vflog, **break при VFC_START_FAIL**), `vflog=` (877, 0 или 10..1000), `vf?` (883), `enc` (887), `vfk=` (892), иначе `unknown` (898).

Ключевые нюансы парсинга (сохранить ТОЧНО):
- `p=` — `sscanf(..., "%u,%u,%u,%u") >= 3` (4-й опционален; mask 0 → все 6 каналов — комментарий GUI-01)
- `mp=` — `sscanf(..., "%d,%d,%d,%d,%d,%d,%d,%d") >= 2`; a3..a8 применяются только если > 0; `measured_mask |= AT_VALID_*` по условиям; после `FOC_SetMotorParams` — при a7 в 1..24 `FOC_SetPolePairs(a7)`; вывод `@MP:OK:Rs=...:Ls=...:Rr=...:Lm=...:Tr=...:Ke=...:p=...:J=...:Kp=...:Ki=...:Lsig=...:AP=1`
- `s=` — `%ld%c` с проверкой `trail` (п.19 ревью: без `(long*)&int32_t`), диапазон ±50000
- `vf=` — `a1 == 0` → стоп (+ `vflog_period_ms = 0` + `TRIG_Low()`); `a1` вне ±5000 → err; fault → "FAULT! send 'f' to clear"; иначе `FOC_Stop(); TRIG_High(); trig_tick = sys_tick_ms; VFC_Start(a1);` при fail — вывод + `vflog_period_ms=0; TRIG_Low(); break;` (см. 4.4)
- `c` — guard `PWM_IsEnabled()` → "err: PWM running — stop FOC/Vf first"; иначе `NVIC_DisableIRQ(ADC1_2_IRQn); ADC_CalibrateOffsets_256(); NVIC_EnableIRQ(ADC1_2_IRQn);`
- autotune-команды (`ch`, `chu/chv/chw`, `iv`, `pairs`, `oew`, `rr`, `noload`, `scope`, `lspos`, `idle`) — паттерн `g_autotune_abort = 0; NVIC_DisableIRQ(ADC1_2_IRQn); <вызов>; NVIC_EnableIRQ(ADC1_2_IRQn);` с разбором rc (0/-5/-6 → OK/ABORTED/FAIL)
- `mapcap drain` — полный формат `@MC:REC:cap=...:seq=...:raw_i1=...:...:fault=%d` (main.c:559-570) — НЕ сокращать

---

## 3. Архитектура

### 3.1 `src/cli.c` — полный перенос тел обработчиков
- Тела команд переносятся из main.c 1-в-1 (парсинг, guard'ы, вызовы, форматирование)
- **Доступ к модулям — через расширенный `CLI_Ops`** (колбэки, см. 4.2) — сохраняет hosted-тестируемость без моков 10 модулей (текущий стиль cli.c)
- **Чтение данных-глобалов — напрямую** (только данные, не функции): `vfc.f_e_hz/f_slip_hz/voltage_mag/v_boost_pct/rated_freq_hz` (vf?, vfk=), `g_motor_params.*` (mp=, mpapply), `g_autotune_abort` (запись 0/1) — через `#include "vf_control.h"`, `#include "autotune.h"` (типы и extern; функции — через ops)
- `mapcap_next_id` — static в cli.c (внутри `#if OEW_MAP_CAPTURE`, дефолт 0 как main.c:16-17)
- `#ifndef OEW_MAP_CAPTURE / #define OEW_MAP_CAPTURE 0` и `#ifndef OEW_MAP_L3 / #define OEW_MAP_L3 0` — продублировать в cli.c (или вынести в общий `oew_config.h` — на выбор автора, но обе сборки commissioning/mainstream должны работать)

### 3.2 `src/cli.h` — расширение
- `CLI_Ops`: добавить колбэки (см. 4.2); существующие поля — сохранить/адаптировать
- `CLI_State`: добавить `uint32_t adc_stream_period_ms; uint32_t vflog_period_ms; uint32_t vflog_last_ms;` (main-периодика читает их из state) — существующие `motor_rs/motor_ls/motor_vbus/params_valid` сохранить
- Return-коды: `0` = unknown, `1` = handled, `-1` = bad args (сохранить); **новый `CLI_EXIT_LOOP (-2)`** — для break-семантики `vf=` (см. 4.4)

### 3.3 `main.c` — остаётся
- Инициализация, `print_help` (static, вызывается из cli через `ops->print_help`), стартовое "> "
- Цикл: `rc = UART_ReadLine(...)`; `if (rc > 0) { int r = CLI_ProcessLine(linebuf, &ops, &state); if (r == CLI_EXIT_LOOP) break; }`; `else if (rc < 0) UART_SendStr("line overflow\r\n> ");`
- Периодика: ADC stream (`state.adc_stream_period_ms`) и 100 мс телеметрия (`@VF:`/`@FOC:`) — 1-в-1 (main.c:903-920)
- `ops` — статическая структура с адаптерами: `send` → `UART_SendStr`, `send_telem` → `UART_SendTelemetry`, `send_dbg`/`send_dbg_fmt` → DBG-семантика (UART+SWO, main.c:36-40), остальные → прямые вызовы модулей (FOC_*, VFC_*, ADC_*, PWM_*, ENC_*, Autotune_*, MapCapture_*, PROTECT_*, TRIG_*, vflog)

---

## 4. Требования

### 4.1 Вывод: 4 канала (различие ОБЯЗАТЕЛЬНО)
| Колбэк | Реализация в main | Используют команды |
|---|---|---|
| `send(text)` | `UART_SendStr` | a= (err), c (err), p= (err), m→print_help, s= (err), pp=/vdc=/fwbase=/dt= (err), curve, vf= , vflog=, и т.д. |
| `send_telem(fmt,...)` | `UART_SendTelemetry` | a, a?, c(cal), p?, p=(OK), 1(fail), mapcap*, dump*, sysinfo, mp=, mpapply, piapply, i=, vf=, vf?, enc, vfk=, ... |
| `send_dbg(text)` | `UART_SendStr + SWO_SendStr` (DBG_STR) | a=(stop/start/err), 1(fault), 0, f, s, s= (OK), irot/inertia/ch/... (OK/FAIL), abort |
| `send_dbg_fmt(fmt,...)` | `UART_SendTelemetry + SWO_SendTelemetry` (DBG_FMT) | f (STATUS/err), s= (ok msg) |

**Проверка эквивалентности:** для каждой команды определить, каким макросом она выводила в main.c (DBG_* или UART_*) и сохранить канал. В тесте — снапшот-проверка точных строк.

### 4.2 Полный набор колбэков CLI_Ops (минимум; имена на усмотрение автора)
`print_help`, `adc_read_single` (a: StartConversion+raw getters), `adc_set_stream(period)` + `adc_stream_status()`, `adc_calibrate(full)` (c: 256-версия), `pwm_is_enabled`, `pwm_debug_set(arr,duty,dt,mask)` (p=), `pwm_status(cr1,ccer,bdtr,cnt)`, `pwm_dump1/dump8/full/` (`PWM_DumpRegs`/`PWM_DumpRegs8`/pdump-состав), `sys_info(psc,tclk)`, `adc_diag(...)` (dumpa), `foc_start`, `foc_stop`, `foc_is_running`, `foc_set_speed/get_speed`, `foc_set_pole_pairs`, `foc_set_vdc`, `foc_set_base_speed`, `foc_set_pi`, `foc_get_pi`, `foc_get_sigma_l`, `foc_params_apply(rs,ls,vbus)`, `foc_get_vbus_mv`, `fault_is_active`, `fault_request_clear` (f: возврат ProtectClearStatus), `vf_start(rpm)`, `vf_stop`, `vf_set_params(boost,rated)`, `encoder_status(...)`, `autotune_run(kind)` (одна команда-диспетчер для irot/inertia/ch/chu/chv/chw/iv/pairs/oew/rr/noload/scope/lspos/idle: kind→функция; возврат rc; NVIC-маскирование — в cli.c вокруг вызова), `autotune_print_curve/params/stats`, `autotune_calc_pi(hz)`, `autotune_get_last_pi(kp,ki,bw)`, `autotune_abort_set(v)` (или прямой extern — на выбор, но предпочтительнее колбэк для теста), `mapcap_arm(profile_id, next_id, &req)`, `mapcap_run`, `mapcap_drain` (колбэк, возвращающий готовую строку записи ИЛИ доступ к MapCapture_ConsumeRecord — на выбор автора; формат @MC:REC НЕ менять), `mapcap_build(profile_id)` (#if OEW_MAP_L3), `mapcap_abort`, `mapcap_stats(&st)`, `trig_high/trig_low`, `sys_tick_ms_get()`, `vflog_set_period(ms)` + `vflog_get_period()` (или через CLI_State — см. 3.2).

Допускаются прямые вызовы в cli.c для `ADC_GetOvrCount/JeosCount/TimeoutCount/JqovfCount` (sysinfo) и `RCC->PLLCFGR`/`TIM1->BDTR`/`TIM8->*` (pdump, dt=) **только если** это упрощает ops (иначе — колбэки). Решение — за автором, но hosted-тест обязан покрывать эти ветки (моки регистров: hs1_mock/stm32g474xx.h уже имеет gpio-модель — расширить регистровую модель TIM/RCC/ADC2 при необходимости).

### 4.3 Парные NVIC-маскирования
Все `NVIC_DisableIRQ(ADC1_2_IRQn)`/`NVIC_EnableIRQ(ADC1_2_IRQn)` — в cli.c (вокруг колбэка autotune_run и adc_calibrate), с сохранением порядка. В hosted-тесте (hs1_mock/stm32g474xx.h) — стабы NVIC счётчиками: тест проверяет, что Disable/Enable вызваны парно и вокруг нужного колбэка.

### 4.4 Break-семантика `vf=`
В main.c:866 `break;` прерывает главный цикл (main() возвращается). Эквивалент: cli.c при VFC_START_FAIL после вывода и сброса vflog/TRIG возвращает `CLI_EXIT_LOOP`; main выполняет `break`. Тест: команда `vf=5000` со стабом vf_start = fail → возврат CLI_EXIT_LOOP.

### 4.5 Регрессия GUI-протокола
Формат-строки в ТЗ-приложении не приводятся целиком — **переносить буквально из main.c:467-901** (включая `\r\n> `-суффиксы и `@...` префиксы). В тесте: для каждой команды — сверка вывода со снапшотом, сгенерированным из ТЕКУЩЕГО main.c (золотой файл). Генерацию снапшотов выполняет автор патча на базе c524d41.

---

## 5. Тесты (`tests/cli_test.c`, hosted)

Линковка: `cli.c + cli.h + cli_test.c` + стабы (внутри cli_test.c или отдельный `tests/cli_stubs.c`) + `-Itests/hs1_mock` (stm32g474xx.h с NVIC-стабами). Все колбэки — реальные стабы в тесте (как сейчас).

Обязательные сценарии (минимум):
1. **Каждая команда — снапшот вывода** (золотые строки из main.c): a, a=0/50/49/1001/abc, a?, c (с PWM running и без), p?, p= с масками, 1 (fault/ok/rc), 0, f (CONTROL_ACTIVE / OK / not cleared), s= (норм/trailing/диапазон), dump/dumpa/dump8/pdump/sysinfo (стабы регистров), pp=/vdc=/fwbase=/dt= (валид/невалид/guard), curve/params/irot/.../idle (все autotune-команды, rc 0/-5/-6), mp= (2..8 параметров, маски), mpapply, piapply, i=, vf= (0/норм/fault/диапазон/fail→EXIT_LOOP), vflog=, vf?, enc, vfk=, unknown, m (print_help)
2. **Парные NVIC**: для каждой autotune-команды и c — Disable до вызова, Enable после
3. **ADC stream/vflog state**: a=200 → `state.adc_stream_period_ms == 200`; vflog=50 → `state.vflog_period_ms == 50`; vf= (start) при vflog==0 → vflog_period_ms == VFLOG_DEFAULT_PERIOD_MS
4. **SWO-канал**: команды с DBG_* выводом — проверка, что вызван send_dbg (не send)
5. `#if OEW_MAP_CAPTURE=1` сборка теста (отдельный таргет или define) — команды mcarm=/mapcap run/drain/abort/status — снапшоты; при =0 — эти строки → unknown

Проверка сборки теста: `make tests/cli_test.exe && ./tests/cli_test.exe` — PASS.

---

## 6. Проверки (обязательные)

```bash
make                    # production build — PASS (main.c с новым циклом)
make test               # hosted — все PASS, включая расширенный cli_test
make test-qemu          # QEMU — PASS
python3 -m pytest tests/test_telem_parser.py -q   # 6 passed (если доступен; иначе py -3 -m pytest)
git diff --check        # без whitespace-ошибок
```

Дополнительно: `git diff c524d41 -- main.c` — main.c должен уменьшиться (уходят строки 467-901); в diff main.c НЕ должно быть изменений инициализации/периодики, кроме замены цикла на вызов CLI_ProcessLine + обработку CLI_EXIT_LOOP.

---

## 7. Запрещено

- Менять форматы `@...` строк, суффиксы `\r\n> `, порядок команд, guard'ы, диапазоны валидации
- Менять поведение периодики (ADC stream/100 мс телеметрия) и инициализации
- Менять `nucleo_debug_tool.py`/протокол
- Менять другие модули (foc, vf_control, adc, pwm, encoder, protect, autotune-функции, map_capture) — только добавление колбэк-адаптеров в main.c
- Оставлять дублирование: старый неполный dispatcher в cli.c должен быть ПОЛНОСТЬЮ заменён

## 8. Формат сдачи

Unified-diff к базе c524d41 (файлы: `src/cli.c`, `src/cli.h`, `main.c`, `tests/cli_test.c`, при необходимости `tests/hs1_mock/stm32g474xx.h`, `Makefile`) + таблица «команда → main.c:строка → поведение сохранено (да/нет)» + результаты прогонов 6. Интеграцию и полную проверку выполняет агент (git apply; при смещениях — ручная интеграция по diff).
