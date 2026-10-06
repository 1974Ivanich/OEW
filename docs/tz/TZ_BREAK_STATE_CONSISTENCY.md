# ТЗ: state-consistency после hardware break (P2 из safety-аудита)

**Источник:** read-only аудит safety-модулей/ISR (Manus, на `f58316c`),
подтверждён приёмщиком (все пункты верифицированы в коде).
**Характер:** state-consistency после аппаратного отключения. НЕ rearm,
НЕ энергия, НЕ fail-closed политика. Default-deny не меняется.
**Затрагиваемые файлы:** `main.c` (break ISR), `src/map_capture_port.c/h`
(вызов существующего хука). Остальное — ЗАПРЕЩЕНО.

## P2-1. Break ISR → FOC_Stop()

`TIM1_BRK_TIM15_IRQHandler()` и `TIM8_BRK_IRQHandler()` (`main.c:75–90`)
делают `PROTECT_LatchFault()` + `PWM_Disable()`, но не `FOC_Stop()`:
после hardware break `foc_running` остаётся 1 (ложная телеметрия
`RUN=1`, состояние рассинхронизировано до ручной команды `0`).

Требование: добавить `FOC_Stop()` в оба break ISR **после**
`PROTECT_LatchFault()`/`PWM_Disable()` (порядок не менять). Проверено:
`FOC_Stop()` (`src/foc.c:610`) не содержит UART/блокировок, только
флаги + идемпотентный `PWM_Disable()` — безопасна для ISR приоритета 0.
Ожидаемое поведение: после break `foc_running=0`, `RUN=0` в телеметрии,
повторный `FOC_Start()` после clear работает штатно.

## P2-2. Break path → MapCapturePort_OnProtectionLatched()

Хук `MapCapturePort_OnProtectionLatched()` (`map_capture_port.c:140`)
существует, но **не вызывается** из central break/protection path: при
внешнем break во время активного capture `g_state` остаётся
`MAP_CAPTURE_RUNNING` (периодический callback не придёт — CEN снят),
новая capture-сессия блокирована до ручного abort/reinit.

Требование: вызвать `MapCapturePort_OnProtectionLatched()` из break path
(оба break ISR, после `PROTECT_LatchFault()`) — модуль `map_capture_port`
входит в C_SOURCES при любой конфигурации, вызов безопасен в generic-образе.
Ожидаемое поведение: capture переходит в терминальное состояние;
новая сессия возможна без abort. Путь актуален для commissioning-сборок.

## Критерии приёмки

- [ ] `make` (generic production) — PASS;
- [ ] `make test` — ALL PASS (hosted+QEMU+pytest), существующие safety-тесты
      (`sd_interlock`, `sd_latch`, `sd_no_self_rearm`, `protect_frame_host`,
      `adc_frame_host`) не изменили результат;
- [ ] commissioning: `make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1
      -DPWM_OEW_BOARD_REVISION=7"` — PASS;
- [ ] `git diff origin/main...HEAD --check` — чисто;
- [ ] diff ограничен `main.c` + `src/map_capture_port.c/h` (или только вызовы);
- [ ] fail-closed/`pwm.c`/`protect.c`/`.ioc` — 0 строк;
- [ ] CI ветки — зелёный.

## Запреты

- Не менять `PROTECT_LatchFault`/`PROTECT_RequestClear`, `PWM_Disable`,
  порядок в ISR (latch → disable → только ДОБАВИТЬ stop/hook-вызовы).
- Не трогать `pwm.c`, `protect.c`, `foc.c` (кроме чтения), `.ioc`, GUI,
  `vf_*`, autotune.

## Процесс

Ветка `ai<N>/break-state-consistency` от свежего `origin/main`, запись в
`docs/AGENTS_STATUS.md`, публикация + `git ls-remote` подтверждение SHA,
ожидание приёмки (main не трогать).
