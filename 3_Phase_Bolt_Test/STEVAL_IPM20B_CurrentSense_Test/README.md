# STEVAL-IPM20B Current Sense Test Project

Упрощенный проект для изучения и отладки датчиков тока на плате **STEVAL-IPM20B**.
Цель — научиться правильно читать raw ADC, калибровать нулевые оффсеты и определять коэффициенты масштабирования тока для обоих инверторов (ADC1 и ADC2).

## Что делает проект

- Инициализирует ADC1/ADC2 в режиме **injected conversion**, запускаемом от TIM1 TRGO.
- TIM1 генерирует center-aligned PWM 10 кГц на шести выходах (два инвертора OEW).
- Главный цикл принимает простые команды по UART2 (115200 8N1).
- Позволяет:
  - читать сырые ADC значения (`adc`);
  - калибровать ноль (`calib`) — усредняет 1000 выборок при отключенном ШИМ;
  - подавать DC на одну фазу (`dc A 850`, `dc B 850`) и измерять ток;
  - вычислять ток по заданным scale-факторам (`i`);
  - останавливать ШИМ (`stop`).

## Как собрать

1. Создайте новый STM32CubeIDE / STM32CubeMX проект для **STM32G431KBTx** (или тот МК, что на вашей плате).
2. Настройте периферию по таблице ниже (пин-аут из основного проекта `3_Phase_Bolt`):

| Периферия | Настройка |
|-----------|-----------|
| TIM1 | Center-aligned mode 1, PWM Period = 8500, Dead-time = 255, CH1/CH2/CH3 + complementary |
| TIM8 | Center-aligned mode 1, PWM Period = 8500, Dead-time = 255, CH1/CH2/CH3 + complementary |
| ADC1 | Injected: 3 channels, External Trigger = TIM1 TRGO, Rising edge, 12-bit |
| ADC2 | Injected: 3 channels, External Trigger = TIM1 TRGO, Rising edge, 12-bit |
| UART2 | 115200 8N1 (или тот UART, что используется на плате для ST-LINK VCP) |

3. Скопируйте `Core/Src/main.c` из этого проекта вместо сгенерированного CubeMX `main.c`.
4. Скомпилируйте и прошейте.

## Подключение

Откройте COM-порт в любом терминале (PuTTY, RealTerm, Arduino Serial Monitor) со скоростью **115200**.
Или используйте `pc_control/sense_test.py`.

## Команды

```
adc            — напечатать raw ADC1/ADC2 один раз
adccont        — печатать raw ADC каждые 100 мс
adcstop        — остановить непрерывный вывод

calib          — собрать 1000 выборок при выключенном ШИМ и записать оффсеты

i              — вычислить и напечатать токи по текущим scale-факторам
scaleA <val>   — задать scale для фазы A (A/LSB), например scaleA 0.00035
scaleB <val>   — задать scale для фазы B
scaleC <val>   — задать scale для фазы C

dc A <duty>    — подать DC: phase A upper ON, phase B lower ON, duty offset от HALF_PERIOD
                 duty > 0: положительный ток в фазе A
                 duty < 0: отрицательный ток
                 Пример: dc A 850 → duty = HALF_PERIOD + 850
stop           — все фазы к 50%, выключить DC-тест
help           — список команд
```

## Как определить scale-фактор

1. Подайте `calib` при разомкнутой цепи питания двигателя (ток = 0).
2. Подайте `dc A 850`.
3. Измерьте ток фазы A мультиметром (например, 0.70 A).
4. Выполните `i` — получите измеренный ток `ia_meas`.
5. Новый scale:

```
scaleA_new = scaleA_old * (ia_meas / 0.70)
```

Или используйте raw-значения:

```
scaleA = I_multimeter / (raw_a - off_a)
```

## Следующий шаг

После того как scale-факторы для ADC1 и ADC2 будут определены и совпадут с мультиметром,
перенесите их в основной проект `3_Phase_Bolt`:
- `g_curr.scale_a`, `g_curr.scale_b`, `g_curr.scale_c` для инвертера 1 (ADC1);
- `g_curr2.scale_a`, `g_curr2.scale_b`, `g_curr2.scale_c` для инвертера 2 (ADC2).
