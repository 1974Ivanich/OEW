# ТЗ: пакет тестов Б+В+Г — покрытие autotune/encoder/observer/pll/fw/uart/vf_start + GUI-парсеры + CLI-интеграция

**Проект:** OEW Motor FOC — STM32G474RE (Nucleo-G474RE), 2× STEVAL-IPM20B, CMSIS-only (без HAL), C99, `-Wall -Wextra -Werror`, Makefile, hosted+QEMU тесты, Python GUI (`nucleo_debug_tool.py`, `vf_panel.py`, Python 3.11).
**Цель:** закрыть пробелы покрытия (см. карту модулей ниже). **НЕ менять поведение production** — только эквивалентный вынос + тесты.

## Карта пробелов (что закрываем)

| Фаза | Модуль | Текущее покрытие |
|---|---|---|
| Б | `src/autotune.c` (математика) | **0%** |
| Б | `src/encoder.c` (AS5048A) | **0%** |
| Б | `src/observer.c`, `src/pll.c`, `src/flux_weakening.c` | **0%** |
| Б | `src/uart.c` | **0%** |
| Б | `src/vf_start.c` | **0%** |
| В | Парсеры телеметрии GUI (`nucleo_debug_tool.py`, `vf_panel.py`) | только `py_compile` |
| Г | CLI-парсер команд `main.c` | только через GUI/руками |

---

## 1. Фаза Б — hosted-тесты firmware (F1–F5)

### F1. autotune_math — вынос чистых функций + тест

**Файлы:** `src/autotune.c` → новый `src/autotune_math.c/h` + `tests/autotune_math_test.c`

Вынести из `autotune.c` в `autotune_math` **без изменения логики** (static → extern, autotune.c вызывает новый модуль):
- `at_sin_q15(int32_t angle_x1000)` (строка ~1492) и косинус (`at_sin_q15(angle + AT_PI_2_MRAD)` — обёртка)
- `at_abs32`, `median_small`, `curve_sort_by_current`, `curve_filter_outliers`, `AT_CalcIsat`, `AT_SaneLs`, `AT_SaneRs`

Константы (`AT_PI_2_MRAD`, `TWO_PI_X1000`, пороги sanity) перенести в заголовок модуля.

**Тест `tests/autotune_math_test.c`** (hosted, gcc, без железа):
- sin: 0→0, π/2→+32767, π→0, 3π/2→−32767, 2π→0; отрицательные углы; wrap `> 2π`; косинус сдвиг
- `median_small`: нечёт/чёт, перемешанный массив, n=1, n=2
- `AT_SaneLs`/`AT_SaneRs`: границы 0.5..500 мГн, Rs>0, отказ на 0/отрицательных
- `curve_filter_outliers`: нормальная кривая сохраняется; 1-2 выброса удаляются; все точки-выбросы → безопасный результат
- `AT_CalcIsat`: монотонная кривая → корректная Isat; насыщение

### F2. encoder — мок TIM2 + тест

**Файлы:** новый мок `tests/enc_mock/stm32g474xx.h` (TIM2-регистры: CCR1/CCR2, SR, CNT, PSC, ARR, CCMR1, CCER, SMCR, DIER; как `tests/hs1_mock` по образцу) + `tests/encoder_test.c`

По `src/encoder.c`: CH1 (rising) → CCR1 = период, CH2 (косвенный TI1, falling) → CCR2 = длительность импульса. Тест симулирует захваты через регистры и вызывает обработчик захвата + `ENC_Update()`:

- Известные стендовые значения: период ≈1087 мкс → частота ≈920 Гц; проверить `ENC_GetPeriod_us()` и угол 14-bit из duty
- Угол: 0..16383; `ENC_GetAngle14()` = 0xFFFF при ошибке
- `ENC_GetSpeed_rpm()`: знак (направление), half-turn скачок → **clamp до механического максимума, без int32-переполнения** (регрессия известного бага)
- `ENC_Update()` при отсутствии новых захватов → `ENC_ERR_TIMEOUT`
- Захват периода вне разумного диапазона → `ENC_ERR_BAD_PERIOD`
- `ENC_GetAngle_deg()`: 0..360, −1 при ошибке

### F3. observer + pll + flux_weakening — тест

**Файлы:** `tests/observer_pll_fw_test.c` (мок CORDIC — существующий `tests/mocks/mock_cordic.c`)

- `BEMF_Init/Update`: контракт `BEMF_Update(V[k−1], I[k])`; на валидной ЭДС `BEMF_IsValid()==1`, магнитуда растёт; на нулевом входе — invalid
- `PLL_Init/Update`: слежение за постоянной частотой (угол сходится), `PLL_LOST_CYCLES` подряд без EMF → UNLOCKED, интегратор клампится на `PLL_INTEGRATOR_MAX`, `PLL_MAX_ERPM` (120000 eRPM)
- `flux_weakening`: активация по скорости, пределы Id_ref/V, возврат в нормальный режим ниже порога

### F4. uart — мок USART2 + тест

**Файлы:** мок USART2-регистров (в `tests/hs1_mock/stm32g474xx.h` или отдельный `tests/uart_mock/`) + `tests/uart_test.c`

По `src/uart.c`:
- `UART_ReadLine`: накопление байтов, строка только после `\r\n`; возврат: 0 = не готово, >0 = длина, −1 = overflow (буфер переполнен)
- `UART_GetChar`/`UART_DataAvailable`: RX-путь
- `UART_SendStr`/`UART_SendChar`/`UART_SendTelemetry` (printf-формат): TX-буфер, корректное содержимое
- Прерывание/буферизация — по фактической реализации uart.c

### F5. vf_start — тест последовательности

**Файлы:** `tests/vf_start_test.c` (чистая логика, моки не нужны)

По `src/vf_start.h`: `VF_Init/VF_SetTarget/VF_Update/VF_IsComplete/VF_GetTheta/VF_GetSpeed`:
- Ramp: экспоненциальный подход к цели, `VF_IsComplete()==1` при достижении
- **Регрессия VFS-01: `complete` НЕ замораживает фазовый генератор** — после complete `theta` продолжает интегрироваться на целевой скорости
- `VF_SetTarget` на лету (цель меняется, ramp пересчитывается)
- `VF_GetTheta` wrap 2π, `VF_GetSpeed` на цели

**Makefile:** новые hosted-цели + строки в `test-hosted`; имена exe **без подстроки «dispatch»** (локальное ограничение: security-софт блокирует запуск таких exe; пример корректного имени: `autotune_math_test.exe`, `encoder_test.exe`, `observer_pll_fw_test.exe`, `uart_test.exe`, `vf_start_test.exe`).

---

## 2. Фаза В — Python unit-тесты GUI (F6)

### F6. Парсеры телеметрии → `telem_parser.py` + pytest

**Файлы:** новый `telem_parser.py` + `tests/test_telem_parser.py` + `tests/__init__.py` (если нужно для pytest)

Вынести **чистые функции парсинга** (без Tkinter/сериала) из `nucleo_debug_tool.py` (`on_line`/`_parse_params`/`_parse_curve`, строки ~1193/1336/1350) и `vf_panel.py` в `telem_parser.py`:

```python
def parse_vflog(line: str) -> dict | None   # @VFLOG: t,target,meas,fe,fslip,vmag,theta,du,dv,dw,i1,i2,ires,vbus,eangle,espeed,eerr,fault,drp
def parse_foc(line: str) -> dict | None     # @FOC:...
def parse_enc(line: str) -> dict | None     # @ENC: angle,speed,period_us,pulse_us,err
def parse_params(line: str) -> dict | None  # @PARAMS:...
def parse_curve(line: str) -> dict | None   # @IDLE:CURVE:...
```

GUI-классы вызывают `telem_parser` вместо собственного кода (поведение не меняется; старые методы становятся тонкими обёртками или удаляются с заменой вызовов).

**Тест** (pytest, Python 3.11):
- Реальные строки из `logs/test_log_*.log` (репозиторий содержит исторические логи — использовать как фикстуры)
- `parse_vflog`: все 19 полей, включая `drp`; отсутствующие поля → None/default; мусорная строка → None; отрицательные значения
- `parse_foc`/`parse_enc`/`parse_params`/`parse_curve`: корректные строки, битые строки (обрезка, лишние разделители), пустая строка
- Кросс-проверка с CSV_FIELDS: набор ключей `parse_vflog` == `CSV_FIELDS` (защита от рассинхрона типа F6/L11)

**Makefile:** цель `py-test` (`python -m pytest tests/test_telem_parser.py -q`), добавить в `test` или отдельной целью `test-py`; pytest — в зависимостях (допустимо `pip install pytest` — отметить в отчёте).

---

## 3. Фаза Г — CLI-интеграция (F7)

### F7. CLI-парсер `main.c` → `src/cli.c/h` + тест

**Файлы:** `src/cli.c/h` + `tests/cli_test.c`; `main.c` — точечная замена цикла

Вынести обработку командной строки из `while(1)`-цикла `main.c` в тестируемый модуль **без изменения поведения** (паттерн `adc_dispatch` — ops-колбэки):

```c
typedef struct {
    /* вывод: UART_SendStr/UART_SendTelemetry-эквиваленты */
    void (*send)(const char *text);
    void (*send_telem)(const char *fmt, ...);
    /* доступ к подсистемам (по одной функции на команду) */
    int  (*foc_start)(void);
    void (*foc_stop)(void);
    int  (*foc_set_params)(int32_t rs, int32_t ls, int32_t vbus);
    ... /* ADC, PROTECT, AUTOTUNE, ENCODER, VFC, PWM, MAP_CAPTURE — по фактическим командам */
} CLI_Ops;

typedef struct { /* зеркало g_motor_params и флагов команд, если требуется */ } CLI_State;

int CLI_ProcessLine(const char *line, const CLI_Ops *ops, CLI_State *state);
```

`main.c` вызывает `CLI_ProcessLine(linebuf, &ops, &state)` из цикла; тексты ответов (`@PWM:OK`, `@MP:OK`, `V/f blocked: rc=...`, `FAULT! send 'f'...`, `@AT:...`) и порядок — **идентичны текущим** (сверка по `git diff`).

**Тест `tests/cli_test.c`** (hosted, стабы подсистем):
- `p=99,15,1500,63` → `@PWM:OK:...`; `p?` → `@PWM:CR1=...`; `a` → `@ADC:...`; `f` (clear) — сценарии fault/no-fault; `1`/`0` (FOC start/stop) — коды `@FOC:START:FAIL:rc=...`; `mp=...` → `@MP:OK`/`@MP:ERROR`; `mpapply`; `vf=N`/`vf=0` — `V/f started/stopped/blocked`; `ch` → `@AT:CH_DETECT:...`; `sysinfo`; `dump`/`dump8`; неизвестная команда → `unknown`
- Проверка ответов строка-в-строку против ожидаемых (эталон — текущее поведение main.c)

**Ограничение:** только hosted; fail-closed гейты (`PWM_Enable` interlock, `both_enable`, профили, `VFC_START_CONTEXT_UNVERIFIED`) **не меняются** — тест фиксирует их текущее поведение (например, `1` → rc отказ при default-deny).

---

## 4. Запреты и требования

1. **Поведение production не меняется.** Любой вынос (autotune_math, telem_parser, cli) — эквивалентный рефакторинг; `git diff` по затрагиваемым production-файлам не должен показывать изменений логики (только вызовы/переносы).
2. **Fail-closed не трогать:** `OEW_MAP_CAPTURE`/`OEW_MAP_L3`, `OEW_HS1_COMMISSIONING_RELEASE`, `both_enable()`, `MapCaptureProfile_*`, `VFC_START_CONTEXT_UNVERIFIED`, `pwm.c`/`protect.c` safety-путь, `.ioc`, распиновка, HAL — запрещено.
3. **CRLF:** `main.c`, `src/*.c` — CRLF; для `main.c`, `vf_panel.py`, `nucleo_debug_tool.py` присылать **точечные диффы** (не полные replacement); для новых модулей — полные файлы с CRLF для `.c/.h` (как в проекте), LF для Python.
4. **Стиль:** C99, `-Wall -Wextra -Werror`, комментарии на русском, формат как в соседних функциях; Python — PEP8, Python 3.11, без новых зависимостей кроме pytest (отметить в отчёте).
5. **Имена hosted-exe без подстроки «dispatch»** (примеры: `autotune_math_test.exe`, `encoder_test.exe`, `observer_pll_fw_test.exe`, `uart_test.exe`, `vf_start_test.exe`, `cli_test.exe`).
6. **Обязательные проверки:** `make` (production) PASS; `make test` (hosted+QEMU) **ALL PASS**; `make test-py` (если добавлена) PASS; `py_compile` всех Python-файлов; `git diff --check` PASS.
7. Никакого попутного рефакторинга вне пунктов ТЗ.

## 5. Порядок и формат ответа

Порядок: F1→F5 (фаза Б), F6 (фаза В), F7 (фаза Г). Допустима поставка по фазам (3 пакета или один — на усмотрение исполнителя, но отчёт обязателен по каждой фазе).

Формат ответа: по каждому пункту — файлы, номера строк, суть, результат тестов; явно перечислить, что из §4 не менялось. Ветка/патч — как обычно (отдельная ветка от актуального `origin/main`).
