# ТЗ: WEB AI — V/f software-защита fail-closed

## Цель
Пока `VFC_IsRunning()`, программные лимиты OC (12 А) и VBUS_HIGH должны
срабатывать. Сейчас `AdcDispatch` зовёт `PROTECT_CheckFrame` только при FOC,
а `PROTECT_Check` в TIM6 пропускает `protect_check_values`, если
`ADC_ReadVbusRegularMv()` вернул −1 (JADSTART). Это fail-open.

## Требования
1. `AdcDispatchOps`: поля `vf_running`, `vf_stop`.
2. `AdcDispatch_Handle`: при V/f + TIM1 CEN вызывать protect; при fault —
   `vf_stop`, **не** `FOC_RunFrame`. Copy-failure latch и для V/f.
3. `PROTECT_CheckVfFrame`: статус как у `CheckFrame`; токи — OC; Vbus —
   только HIGH (injected VBUS_LOW запрещён: ложные срабатывания на PWM-edge).
4. `PROTECT_Check`: токи всегда; regular Vbus если ≥0 (включая LOW);
   если regular −1 — HIGH с injected, без LOW.
5. Host-тесты: JADSTART (regular=−1) → OC latch; injected 5 В без LOW;
   V/f protect без FOC run.
6. Не трогать `pwm.c`, `.ioc`, HAL.

## Контекст
- Аудит: P0 fail-open во время V/f.
- Файлы: `src/adc_dispatch.*`, `src/protect.*`, `main.c`,
  `tests/adc_dispatch_test.c`, `tests/protect_frame_host_test.c`.
