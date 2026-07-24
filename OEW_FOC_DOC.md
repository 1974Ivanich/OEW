# OEW Motor FOC — Описание проекта

## Аппаратная часть

### Платформа
- **MCU:** STM32G474RE (Cortex-M4F, 170 МГц, FPU, CORDIC)
- **Плата:** NUCLEO-G474RE
- **Инвертор 1:** STEVAL-IPM20B (IGBT STGIB20M60TS-L, 400V/12A)
- **Инвертор 2:** STEVAL-IPM20B (тот же)
- **Топология:** Open-End Winding (OEW) — двигатель подключён между двумя инверторами
- **Токовые датчики:** One-shunt на каждый инвертор (общий шунт на 3 фазы)
- **Нулевой ток:** трансформаторный датчик тока (TBD)
- **Vbus:** делитель напряжения 1:125 (встроен в STEVAL-IPM20B)

### Подключение Nucleo-G474RE

1. Подключите Nucleo-G474RE к ПК через **USB-C** (порт CN1 — ST-LINK)
2. Драйверы ST-LINK устанавливаются автоматически
3. После подключения:
   - **COM-порт:** STMicroelectronics STLink Virtual COM Port (обычно COM15)
   - **ST-LINK:** SWD-отладчик для прошивки
4. Для прошивки: `make flash`

### Подключение STEVAL-IPM20B (разъём J2, 34-pin)

| Pin J2 | Сигнал | Описание | Пин Nucleo | Периферия |
|--------|--------|----------|------------|-----------|
| 1 | EM_STOP | Аварийный стоп | (NC) | |
| 2,4,6,8,10,12,16,18,20,22,24,30,32 | GND | Земля | GND | |
| 3 | PWM-1H | HIN_U | **PC0** | TIM1_CH1, AF2 |
| 5 | PWM-1L | LIN_U | **PA7** | TIM1_CH1N, **AF6** |
| 7 | PWM-2H | HIN_V | **PC1** | TIM1_CH2, AF2 |
| 9 | PWM-2L | LIN_V | **PB0** | TIM1_CH2N, **AF6** |
| 11 | PWM-3H | HIN_W | **PC2** | TIM1_CH3, AF2 |
| 13 | PWM-3L | LIN_W | **PB1** | TIM1_CH3N, **AF6** |
| 14 | HV bus voltage | Напряжение шины (делитель 1:125) | **PC4** | ADC2_IN5 |
| 15 | Current phase A | Ток инвертора 1 (шунт + ОУ 2.1х) | **PA0** | ADC2_IN1 |
| 17 | Current phase B | Ток инвертора 2 (шунт + ОУ 2.1х) | **PA1** | ADC2_IN2 |
| 19 | Current phase C | Нулевой ток (трансформатор) | **PA6** | ADC2_IN3 |
| 25 | +V power | Питание 3.3В | 3.3V | |
| 28 | VDD_m | Питание 3.3-5В | 3.3V | |

### Перемычки STEVAL-IPM20B

| Перемычка | Положение | Назначение |
|-----------|-----------|------------|
| **SW5** | **ЗАМКНУТ** | One-shunt конфигурация |
| **SW6** | **ЗАМКНУТ** | One-shunt конфигурация |
| SW7 | РАЗОМКНУТ | One-shunt конфигурация |
| SW8 | РАЗОМКНУТ | One-shunt конфигурация |
| SW1 | 1-2 | Ток фазы A через onboard ОУ |
| SW2 | 1-2 | Ток фазы B через onboard ОУ |
| SW4 | 1-2 | Ток фазы C через onboard ОУ |
| SW3 | 1-2 | TSO (встроенный датчик IPM) |
| SW9, SW16 | 2-3 | Hall/Encoder питание 3.3В |

### Параметры датчика тока (STEVAL-IPM20B)

| Параметр | Значение |
|----------|----------|
| Шунт (Rshunt) | 0.03 Ом (30 мОм), 7 Вт |
| Усиление ОУ | 2.1 (дифференциальное) |
| Трансрезистанс (rm) | 0.063 Ом (0.03 × 2.1) |
| Смещение (Voffset) | 1.65 В (Vcc/2) |
| Макс. ток (IOCP) | ±26.2 А |
| Чувствительность | 63 мВ/А |
| Диапазон АЦП | 0-3.3 В → 1.65В ± 1.65В = ±26.2А |

### Расчёт тока

```c
// raw — код АЦП (0-4095 при VREF=3.3В)
// offset — код при нулевом токе (~2048 при 1.65В)
int32_t diff = (int32_t)raw - (int32_t)offset;
// Ток в мА: diff * 3300 / 4095 / 0.063 * 1000
// Упрощённо в коде: diff * 256 / 5  (погрешность ~2%)
int32_t current_ma = (diff * 256) / 5;
```

### Делитель Vbus

- Коэффициент: 1:125 (R1=470k, R2=3.9k)
- При 60В: на пине АЦП = 60/125 = 0.48 В
- Расчёт: `Vbus_mV = raw * 3300 / 4095 * 125`

---

## Программная часть

### Архитектура

```
main.c              — инициализация, главный цикл, обработка UART
src/
  pwm.c/h           — TIM1 + TIM8: 5 кГц center-aligned ШИМ
  adc.c/h           — ADC2: I1 (PA0), I2 (PA1), IN (PA6), Vbus (PC4)
  uart.c/h          — USART2: 115200, команды, телеметрия
  cordic_math.c/h   — аппаратный CORDIC: sin, cos, atan2, модуль
  foc.c/h           — Clarke/Park, PI-регулятор, FOC_ControlLoop
  observer.c/h      — Back-EMF Observer в αβ-координатах
  pll.c/h           — PLL для оценки угла и скорости
  flux_weakening.c/h — ослабление поля (Flux Weakening)
  vf_start.c/h      — V/f open-loop старт
  protect.c/h       — защита (overcurrent, watchdog)
```

### Параметры ШИМ
- **Частота:** 5 кГц (center-aligned mode 1)
- **TIMx_PSC:** 15 (16 MHz / 16 = 1 MHz)
- **TIMx_ARR:** 99 (1 MHz / 100 = 10 kHz edge / 2 = 5 kHz center)
- **Dead-time:** ~1 мкс (BDTR.DTG = 16 при 16 МГц)
- **Duty:** 0-99 (0-100%)
- **TIM1:** Инвертор 1 (PC0/PC1/PC2 + PA7/PB0/PB1)
- **TIM8:** Инвертор 2 (PC6/PC7/PC8 + PC10/PC11/PC12)

### OEW топология (Open-End Winding)

Двигатель подключён между двумя инверторами:
```
V_motor = V_inv1 - V_inv2

V_inv1 = Vdc/2 + V/2   (смещение 50% + половина задания)
V_inv2 = Vdc/2 - V/2   (смещение 50% - половина задания)

Макс. напряжение: 2 × Vdc (при 60В = 120Вп-п)
```

### FOC цикл (5 кГц)

```
1. ADC_StartConversion() — читаем I1, I2, IN, Vbus
2. Iu = I1, Iv = I2, Iw = -(I1 + I2 + IN) — закон Кирхгофа
3. Clarke: Iu,Iv,Iw -> Iα,Iβ
4. BEMF Observer: Eα = Vα - R·Iα - L·dIα/dt
5. PLL: err = -Eα·sin(θ) + Eβ·cos(θ); ω += Ki·err; θ += ω·Ts
6. Park: Iα,Iβ,θ -> Id,Iq
7. Flux Weakening: если V_out > 95% от макс → уменьшаем Id_ref
8. PI: Vd = PI(Id_ref - Id); Vq = PI(Iq_ref - Iq)
9. InvPark: Vd,Vq,θ -> Vα,Vβ
10. InvClarke: Vα,Vβ -> Vu,Vv,Vw
11. OEW: V1 = dc_bias + V/2; V2 = dc_bias - V/2
12. CLAMP каждой duty в [1..98]
13. PWM_SetDuty1(V1u,V1v,V1w); PWM_SetDuty2(V2u,V2v,V2w)
```

### CORDIC (аппаратный ускоритель)

STM32G474 имеет аппаратный CORDIC:
- **Sin/Cos:** вход Q31 (2π = 0x7FFFFFFF)
- **Atan2:** возвращает угол Q31
- **Modulus:** sqrt(x²+y²)
- Тактирование: `RCC->AHB1ENR |= RCC_AHB1ENR_CORDICEN`
- Регистры: CSR (функция, точность, размер), WDATA, RDATA

### Команды UART (115200)

| Команда | Описание |
|---------|----------|
| `1` | Запуск FOC |
| `0` | Останов FOC |
| `m` | Меню |
| `s=500` | Задать скорость (об/мин) |

### Телеметрия

Формат: `@FOC:I1=...:I2=...:IN=...:VBUS=...`
Период: каждые 100 циклов ШИМ (20 мс)
Значения: I1/I2/IN в мА, VBUS в мВ

---

## Сборка и прошивка

```bash
# Сборка
cd C:\ST\boyler\Motor
make clean
make

# Прошивка (требует STM32CubeProgrammer)
make flash
```

**Требования:**
- ARM GNU Toolchain (arm-none-eabi-gcc) в PATH
- STM32CubeProgrammer (STM32_Programmer_CLI)
- CMSIS из STM32CubeG4 по пути `C:/Users/190/STM32CubeG4/`

## Структура файлов проекта

```
Motor/
├── main.c                  — Главный модуль
├── Makefile                — Сборка
├── startup_stm32g474xx.s   — Стартап (ассемблер)
├── system_stm32g4xx.c      — Системная инициализация
├── linker.ld               — Линкер-скрипт
├── flash.bat               — Быстрая прошивка
├── run_foc_gui.bat         — Запуск GUI управления
├── foc_control_gui.py      — PC GUI на Python/Tkinter
├── measurement_gui.py      — Старый GUI (измерение сопротивлений)
├── run_gui.bat             — Запуск старого GUI
├── src/
│   ├── pwm.c/h             — ШИМ модуль (TIM1 + TIM8)
│   ├── adc.c/h             — АЦП модуль (ADC2)
│   ├── uart.c/h            — UART модуль (USART2)
│   ├── cordic_math.c/h     — CORDIC (sin/cos/atan2)
│   ├── foc.c/h             — FOC ядро (Clarke/Park/PI)
│   ├── observer.c/h        — BEMF наблюдатель
│   ├── pll.c/h             — PLL оценка скорости
│   ├── flux_weakening.c/h  — Ослабление поля
│   ├── vf_start.c/h        — V/f старт
│   └── protect.c/h         — Защиты
├── AGENTS.md               — Правила для Hermes Agent
├── pinout.md               — Распиновка Nucleo
├── STEVAL_IPM20B_SETUP.md  — Настройка инвертора
├── OEW_FOC_DOC.md          — Этот файл
└── *.pdf                   — Даташиты
```

## Важные замечания по AF (Alternate Function)

На STM32G474 таблица AF отличается от F4/L4:
- **TIM1_CH1/CH2/CH3 на PC0/PC1/PC2:** AF2 ✅
- **TIM1_CH1N на PA7:** **AF6** (не AF2!)
- **TIM1_CH2N/CH3N на PB0/PB1:** **AF6** (не AF2!)
- **TIM8 на PC6/PC7/PC8/PC10/PC11/PC12:** AF4 ✅
- **PA8/PA9/PA10** не работают с TIM1 на данной плате (аппаратная особенность)
