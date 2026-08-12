# ТЗ: полный аудит цепочки FOC + observer + encoder + Voltage Manager + VF starter

**Проект:** OEW Motor Drive — STM32G474RE, CMSIS-only (никакого HAL), 2× STEVAL-IPM20B (dual-inverter open-end winding), асинхронный двигатель.
**Исходники:** файл `src_audit_20260812.zip` (актуальные версии из рабочей ветки, включая свежие исправления).

## Контекст проекта (проверенные факты, чтобы не гадать)

- **PWM:** TIM1 — master, center-aligned mode 3, 5 кГц, RCR=1 (1 TRGO за период). TIM8 — slave Reset mode по ITR0 (TIM1_TRGO). Оба в PWM: TIM1 mode 1, TIM8 mode 2 (сигнальная противофаза OEW), dead-time ~1.5 мкс (BDTR).
- **ADC:** TIM1_TRGO → ADC2 injected group (JL=3): JDR1=I1(PA0), JDR2=I2(PA1), JDR3=Ires(PA6, трансформаторный датчик суммы), JDR4=Vbus(PC4). JEOS → ADC1_2_IRQHandler (prio 0) → FOC_Run().
- **Масштабы:** напряжения — Q15 (32768 = Vbus, Vbus обновляется ежециклово: foc.c:778), токи — мА (не mA/100), модуляция PWM — Q15 signed (PWM_SetMod1/2: CCR = ARR/2 + mod·ARR/2).
- **Угол:** STARTUP — open-loop I-f стартер (VFStart, фазовый аккумулятор, вызывается из FOC_Run на 5 кГц); переход V/f→FOC при VF_IsComplete + |EMF|>порог + encoder-условия (foc.c:571-596, угол сохраняется бесшовно); RUN — encoder + slip-модель → phase accumulator. FOC и closed-loop V/f (VFC, TIM6 1 кГц) взаимоисключаются (VFC_Start проверяет FOC_IsRunning).
- **Encoder:** AS5048A PWM-output (один провод), TIM2 PWM-input capture (PA15). Период ≈920 Гц. SPI-режим удалён намеренно.
- **CORDIC:** аппаратный, используется в FOC_Run (Park/InvPark, VM_Update, PLL), VFC_Update (TIM6) и autotune. Функции: SinCos, Modulus, Atan2, Sqrt — нормализация в Q1.31 с восстановлением масштаба.
- **Voltage Manager:** ограничение Vd/Vq по кругу Q15 (Vmax = 90%·32768), приоритет FLUX или TORQUE; limit_scale_q15 = Vmax/|V| — ИНДИКАТОР насыщения для FW (не коэффициент масштабирования).
- **PI:** встроен в foc.c (PIController, PI_Update, PI_BackCalculation — anti-windup: integral += Kw·(out_limited − out_cmd)).
- **Protect:** FAULT-флаги, блокировка PWM при VBUS<8V/>80V и перетоке; PWM_Disable → CCR=midpoint, ADC_InjectedStop.

## Что проверить — полный data-flow, не отдельные функции

Пройди по цепочкам (все 4 параллельных):

```
ADC → JDR1-4 → Ia/Ib/Ires/Vbus → Clarke → Id/Iq → PI d/q → VM → InvPark → Vα/Vβ → PWM_SetMod → CCR → TIM1/TIM8
ADC/Vbus + Iαβ → BEMF observer → Eα/Eβ → PLL (диагностический)
AS5048A → TIM2 → angle14 → speed_rpm → slip → f_e → phase accumulator → θe
VFStart (STARTUP) → θe open-loop → переход VF→FOC
```

## Контрольный список (20 пунктов)

1. Правильность физических единиц во всей цепочке (ADC code → мА → Q15 → В).
2. Clarke/Park и обратные преобразования — знаки, масштаб (амплитудно-инвариантные?).
3. Соответствие Vα/Vβ и Iα/Iβ (одна конвенция Clarke? foc.c:26-36 и шаг 12).
4. Знак и масштаб R·I в BEMF (Q15 относительно Vbus).
5. L·dI/dt — масштаб, шум, необходимость фильтрации.
6. Масштабирование относительно Vbus (Vbus меняется — observer.Vdc_mV обновляется в foc.c:778).
7. Observer + PLL: формат valpha/vbeta на входе BEMF_Update (Q15? относительно Vbus?).
8. Реальный смысл Vmax (90% Vbus? что означает для OEW — фаза может дать ±Vbus).
9. Соответствие Voltage Manager фактической PWM-модуляции (Q15 mod → CCR).
10. Anti-windup PI: знак saturation_error, Kw, clamps.
11. Взаимодействие encoder speed с V/f (slip, f_e = p·n/60 + f_slip).
12. TIM2/TIM6/FOC timing и приоритеты IRQ (ADC=0, TIM6=1, USART2=2).
13. Race conditions между ISR и main (uart, encoder capture, autotune).
14. Переход VF → FOC (условия, бесшовность угла).
15. Запуск, остановка, реверс.
16. Поведение при потере encoder (BAD_PERIOD/OF → speed=0).
17. Поведение при изменении Vbus (фильтр, observer, FAULT).
18. Ограничения при насыщении (VM priority, FW).
19. Dead-time и фактическая потеря напряжения (компенсация vcomp_u/v/w в foc.c).
20. Соответствие всей реализации ТЗ: OEW FOC + AS5048A + V/f с slip-компенсацией.

## Формат ответа

- По каждому пункту: **OK / ОШИБКА / ЗАМЕЧАНИЕ** + доказательство (файл:строка) + краткое обоснование.
- Найденные ошибки — с конкретным исправлением (код-паттерн, не «исправьте как-нибудь»).
- Отдельно: список критичных (P0/P1) проблем с приоритетами.
- В конце: вердикт — можно ли прошивать на стенд в текущем виде.

## Правила

- Только CMSIS, никакого HAL — это проверка кода, не предложения по архитектуре.
- Не предлагай переход на другие алгоритмы (Sliding Mode, Kalman и т.п.) — проект осознанно на BEMF-наблюдателе + encoder.
- Ссылайся ТОЛЬКО на файлы из архива.
