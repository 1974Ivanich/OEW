# ТЗ: телеметрия — firmware P1 (budget и truncation accounting)

**Источник:** read-only аудит телеметрии/логирования (Manus, на `0dd7f4a`),
бюджет канала подтверждён приёмщиком независимым расчётом (`@VFLOG` 50 Гц = ~81–89% канала 115200).

## Контекст

- `@VFLOG` (main.c TIM6 ISR, default period 20 мс = 50 Гц) занимает ~180–206 B/пакет
  → 9 100–10 300 B/s из 11 520 B/s полезной ёмкости 115200 8N1.
- Остаток (~1,2–2,1 КБ/с) делят `@VF` 10 Гц, CLI, ошибки, debug — при смешанном трафике
  ISR закономерно отбрасывает целые строки (`drp`). Это НЕ safety-дефект, но бюджет надо вернуть.
- Абсолютные int32-extrema могут дать пакет до 285 B > 256 B буфера `UART_TrySendTelemetry`
  (сейчас недостижимо, но формат — budget-constrained контракт).

## Требования

1. **Telemetry profiles (P1)**: явно документированные профили — compact default для online
   logging; extended/raw только по отдельной commissioning/debug-команде. Либо снизить default
   `vflog_period_ms` до 25–40 Гц (контрольный цикл V/f остаётся 1 кГц).
   **Запрещено** менять существующий формат `@VFLOG` под тем же включателем без версии/schema-negotiation.
   Фактический default задаёт `CLI_VFLOG_DEFAULT_PERIOD_MS` в `src/cli.h:8` (используется в `src/cli.c:214`
   при старте V/f) — снижение default 20 мс → 40 мс (25 Гц) выполняется там же, в одной константе.
2. **Truncation accounting (P1)**: в `UART_TrySendTelemetry` проверять результат `vsnprintf`;
   при truncation — инкремент отдельного счётчика и **никогда не ставить неполную строку**
   (предотвращает склейку со следующей строкой). Не менять неблокирующую семантику ISR и PRIMASK-защиту.
3. **Регрессии (hosted)**: пакетный тест с реальными bounded-полями — `snprintf` length < буфера,
   терминатор на месте, бюджет 25 Гц задокументирован; snapshot совместимости legacy `@VFLOG`.
   `uart_test` 10/10 остаётся PASS.

## Запреты

- Никаких блокирующих `UART_Send*` из ADC/TIM6 ISR; не ослаблять PRIMASK/MPSC reserve.
- Не менять смысл полей `@VFLOG`; fault/status-строки не подавлять.
- `pwm.c`/`protect.c`/`.ioc`/fail-closed — 0 строк.
- P2/P3 этого аудита (delta drp, uartstats, cadence ownership, SWO diagnostics) — вне рамок.

## Приёмка

`make` PASS; `make test` ALL PASS; commissioning (`OEW_MAP_CAPTURE=1 OEW_MAP_L3=1 REV=7`) PASS;
новые hosted-регрессии PASS; `git diff --check`; CI ветки success; scope — `main.c`/`src/uart.*`/`src/cli.h` (одна константа `CLI_VFLOG_DEFAULT_PERIOD_MS`)/тесты.
