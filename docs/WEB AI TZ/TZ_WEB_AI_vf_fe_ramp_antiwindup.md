# ТЗ: WEB AI — V/f overshoot: f_e от рампы + anti-windup slip

## Цель
Ограничить overshoot скорости ≤10% относительно рампы. Bounded vmag PI
не регулирует частоту: при отставании растут и slip, и напряжение.
`f_e = p*measured/60 + f_slip` даёт положительную обратную связь
(ротор ушёл вверх → статор ещё быстрее).

## Требования
1. В `VFC_Update`: `f_e = p * ramp_current_rpm / 60 + f_slip`, затем
   clamp `VFC_MAX_FE_HZ`.
2. Slip PI: conditional integration — не копить I, когда выход уже на
   `±VFC_MAX_SLIP_HZ` в ту же сторону, что ошибка.
3. Vmag PI (±10 п.п.) оставить как IR-boost, не как регулятор скорости.
4. Тесты: f_e не растёт от overspeed ротора; после длинного лага reverse
   error снижает slip ниже clamp.
5. Не трогать `pwm.c`, `.ioc`, HAL, `VFC_START_CONTEXT_UNVERIFIED` код.

## Контекст
- Стенд v7: ~1195 rpm при цели ~1000.
- Файлы: `src/vf_control.c`, `tests/vf_control_test.c`.
