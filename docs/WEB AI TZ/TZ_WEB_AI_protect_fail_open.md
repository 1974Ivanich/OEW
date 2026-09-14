# ТЗ: WEB AI — Защита P0: fail-open в V/f режиме

## Проблема
В режиме V/f (FOC выключен) защита не проверяет ток и Vbus:
- `AdcDispatch_Handle` вызывает `PROTECT_CheckFrame` только если `FOC_IsRunning()` (строки 24–26 в `adc_dispatch.c`)
- `PROTECT_Check` в `TIM6` пытается читать regular Vbus, но `ADC_ReadVbusRegularMv` возвращает `-1` (JADSTART=1 после `ADC_InjectedStart`)
- При `-1` — молча выходит без `protect_check_values` (строки 180–185 в `protect.c`)
- Инжектед-токи тоже не проверяются

**Результат:** программные лимиты 12 А / 8–350 В не работают. Только железо (BKIN/SD).

## Задача
Исправить `adc_dispatch.c` и `protect.c` для fail-closed в V/f.

## Файлы для изменения
- `src/adc_dispatch.c` — добавить вызов `PROTECT_CheckFrame` при `VFC_IsRunning()`
- `src/protect.c` — в `PROTECT_Check`: если regular Vbus недоступен (-1), проверять injected frame (`frame.vbus_mv`, `frame.idc_a/b/c`) или fail-closed (latch timeout/busy)

## Не трогать
- `pwm.c/h`, `.ioc`, BKIN/MOE/PRIMASK, `VFC_START_CONTEXT_UNVERIFIED`

## Критерии приёмки (hosted test)
1. `tests/protect_frame_host_test.c` — добавить кейс: `JADSTART=1` → regular `-1`; `V/f running` + `injected VALID` → OC/Vbus latch
2. Существующие тесты проходят
3. `make test-hosted` — PASS
4. `python scripts/cubemx_check.py` — PASS

## Контекст
- Аудит ошибок: P0
- Ветка: `ai2/vf-overshoot-stability` (или новая `ai2/protect-vf-fail-open`)
- Стенд: v6 (60 В), не требует HV для hosted тестов