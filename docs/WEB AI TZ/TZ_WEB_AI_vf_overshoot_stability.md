# ТЗ: WEB AI — V/f overshoot стабилизация + @VFLOG стабильность эмиссии

## Цель
Добавить PI-регулирование `vmag` в V/f-контроллер для ограничения overshoot до ≤10%,
обеспечить стабильную эмиссию `@VFLOG` (~200 кадров/8с, ±20) без обрывов.

## Требования
1. Добавить поле `PIController speed_pi` в `VFCtrl` (vf_control.h) — если ещё нет.
2. В `VFC_Init()` вызвать `PI_Init(&vfc.speed_pi, Kp, Ki, out_max, out_min)` для регулирования `vmag`.
3. В `VFC_Update()` после вычисления базового `vmag` из таблицы V/f:
   - вычислить `speed_error = vfc.ramp_current_rpm - vfc.measured_rpm`;
   - `vmag_adjustment = PI_Update(&vfc.speed_pi, speed_error)`;
   - `vmag = CLAMP(vmag + vmag_adjustment, 0, VFC_MAX_VOLTAGE_PCT)`;
   - применить к `vfc.voltage_mag`.
4. В `VFC_Stop()` сбрасывать `PI_Reset(&vfc.speed_pi)` — запрет windup при следующем старте.
5. UART: увеличить `UART_TX_BUF_SIZE` с 1024 до 2048 байт в `uart.c`.
6. `@VFLOG` (main.c TIM6 ISR) — использовать `UART_TrySendTelemetry` (уже используется),
   добавить проверку: если возвращает -1 → инкремент `vflog_drop_count` (отдельный счётчик
   для V/f, не путать с кумулятивным `UART_GetDroppedCount`).
7. Добавить локальный счётчик drops в пакет `@VFLOG` (согласовано с `TZ_fix_audit_logical.md` F6).
8. Обновить `tests/vf_control_test.c` — добавить тест PI-регулирования `vmag` при известном `speed_error`.
9. Собрать: `make` → `make test` (hosted + QEMU) — ALL PASS.
10. Запушить ветку `ai2/vf-overshoot-stability`.

## Что НЕ трогать (fail-closed, из TZ_fix_audit_logical.md §2)
- `.ioc`, `src/pwm.c`, `src/pwm.h`, `src/protect.c`, BKIN/MOE/PRIMASK.
- `VFC_START_CONTEXT_UNVERIFIED` гейт.
- Формат существующих полей `@VFLOG`.
- HAL — только CMSIS-регистры.

## Контекстные файлы
- docs/TEST_PLAN_VF_OVERSHOOT_STABILITY.md
- docs/TZ_VF_DATA_LOGGING.md
- docs/TZ_fix_audit_logical.md
- src/vf_control.c, src/vf_control.h
- src/uart.c, src/uart.h
- src/main.c
- src/cli.c
- tests/vf_control_test.c
