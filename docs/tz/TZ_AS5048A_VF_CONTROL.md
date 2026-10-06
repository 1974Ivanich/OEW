# ТЗ v3: AS5048A + V/f управление с обратной связью по скорости

> **Версия 2** — исправлены все технические противоречия, выявленные при анализе
> исходного кода проекта. Архитектура изменена на **phase accumulator + slip**,
> что правильно для асинхронного двигателя. Все формулы и регистры сверены с
> актуальными исходниками и RM0440.
>
> **Версия 3** — исправлена safety-критичная ошибка: `PROTECT_Check()` в V/f-режиме
> проверял бы протухшие (не обновляемые) данные АЦП, т.к. injected-группа в этом
> режиме не запускается, а обновление `adc_data` через regular-группу нигде не было
> прописано. Также добавлен явный `CLAMP` на выход `PI_Update()` для `f_slip_hz`
> (сам `PI_Update` клэмпит только интегратор, а не итоговый P+I выход — см. `foc.c`).

## 1. Цель

Установить магнитный энкодер AS5048A на ротор асинхронного двигателя для получения
обратной связи по скорости. Реализовать **замкнутый по скорости V/f алгоритм** с
phase accumulator и slip-частотой для асинхронного двигателя. Управление — через
PWM-вкладку GUI `nucleo_debug_tool.py` по UART.

## 2. Аппаратная часть

### 2.1. Датчик AS5048A

- 14-битный магнитный энкодер (0.022°/bit)
- Интерфейс: **SPI** (режим 1, CPOL=1, CPHA=1)
- Питание: 3.3V (от Nucleo)
- Максимальная тактовая частота SPI: 10 МГц

### 2.2. Подключение к STM32G474RE (Nucleo-64)

Используется **SPI2** (аппаратный SPI, не занят в текущем проекте).

| Сигнал AS5048A | Пин Nucleo | Порт MCU | AF | Назначение |
|---|---|---|---|---|
| VDD | 3V3 | — | — | Питание 3.3V |
| GND | GND | — | — | Земля |
| CSn | D10 | PB6 | GPIO (Output) | Chip Select (активный low) |
| CLK | D6 | PB10 | AF5 (SPI2_SCK) | SPI тактовый сигнал |
| MISO | D12 | PB14 | AF5 (SPI2_MISO) | Данные от энкодера |
| MOSI | D11 | PB15 | AF5 (SPI2_MOSI) | Команды к энкодеру |

> **Важно:** Пины PB6, PB10, PB14, PB15 **не заняты** в текущей прошивке. Все остальные пины (PA0-PA7, PB0-PB1, PB4-PB5, PC0-PC2, PC4, PC6-PC8, PC10-PC12) уже используются для PWM, ADC, UART и EN-сигналов.

### 2.3. Магнит

- Диаметр магнита: 6 мм (стандартный для AS5048A)
- Расстояние от поверхности чипа до магнита: 1-2 мм
- Магнит крепится на оси ротора соосно

### 2.4. Схема подключения

```
AS5048A          STM32G474RE (Nucleo)
──────           ──────────────────────
VDD  ─────────── 3V3
GND  ─────────── GND
CSn  ─────────── PB6  (GPIO Output, Pull-up)
CLK  ─────────── PB10 (SPI2_SCK, AF5)
MISO ─────────── PB14 (SPI2_MISO, AF5)
MOSI ─────────── PB15 (SPI2_MOSI, AF5)
```

## 3. Программная часть — Firmware (STM32G474)

### 3.1. Драйвер AS5048A (`src/encoder.c`, `src/encoder.h`)

**Ограничения:**
- Только CMSIS, **без HAL** (правило проекта)
- Использовать регистры SPI2 напрямую через CMSIS
- Проверять регистры по RM0440 (раздел 28 — SPI)
- **(v3) Таймауты на всех busy-wait циклах** (ожидание `TXE`/`RXNE`/`BSY`) —
  по тому же принципу, что уже используется в `adc.c`/`pwm.c` проекта
  (`while(!(...)) { if(--t==0) break; }`). `ENC_Update()` вызывается из
  1 кГц ISR — залипший без таймаута SPI-обмен (обрыв проводов, шум)
  подвесит ISR намертво и остановит весь проект, а не только энкодер.

**Функции:**

```c
// encoder.h
void     ENC_Init(void);              // Инициализация SPI2 + CS пин (PB6)
uint16_t ENC_ReadRaw(void);           // Сырой 16-битный кадр от AS5048A
uint16_t ENC_GetAngle14(void);        // Угол 0..16383 (14 бит, после parity check)
int32_t  ENC_GetSpeed_rpm(void);     // Механическая скорость, об/мин (signed)
int32_t  ENC_GetAngle_deg(void);     // Угол в градусах 0..360
void     ENC_Update(void);           // Вызов из TIM6 ISR (1 кГц): чтение SPI + расчёт скорости
uint8_t  ENC_GetError(void);         // Флаг ошибки (parity, EF)
```

> **Внимание:** `ENC_GetElectricalAngle()` **НЕ нужен** — электрический угол
> вычисляется внутри `vf_control.c` через phase accumulator, а не напрямую
> из энкодера. Энкодер используется только для измерения **механической скорости**.

**SPI2 конфигурация (по RM0440, раздел 28):**

- `RCC->APB1ENR1 |= RCC_APB1ENR1_SPI2EN`
- GPIO: PB10/PB14/PB15 → AF5, push-pull, high speed; PB6 → output push-pull, pull-up
- `SPI2->CR1`: Master, CPOL=1, CPHA=1 (SPI mode 1), BR=PCLK/32, SSM=1, SSI=1, 16-bit data frame (DFF=1), LSBFIRST=0
  - **Важно:** APB1 = 170 МГц (PPRE1=/1, не делится — проверено в `main.c` RCC config)
  - SPI2 max clock = PCLK1/2 = 85 МГц; BR=/32 → 5.3 МГц (с запасом от лимита AS5048A 10 МГц)
- `SPI2->CR2`: SSOE=0 (software CS), DS=0xF (16-bit), FRXTH=0
- `SPI2->CR1 |= SPI_CR1_SPE` — включение

**Протокол AS5048A (строго по datasheet):**

AS5048A использует 16-битный SPI с parity bit. Формат кадра:
- Бит 15: parity (even)
- Бит 14: RW (0=read, 1=write)
- Биты 13..0: address (для чтения ANGLE = 0x16)

**Чтение угла — правильный протокол (2 transaction):**

```
Transaction 1:
  CSn → LOW
  TX: 0x4000 | (0x16 << 2) = 0x4058  // read command, addr=0x16 (ANGLE)
  RX: (мусор — предыдущий кадр в буфере)
  CSn → HIGH

Transaction 2 (минимум 350ns позже):
  CSn → LOW
  TX: 0x0000 (NOP)
  RX: 16-bit data: [par(1)|EF(1)|angle(14)]
  CSn → HIGH
```

**Альтернатива (continuous read):** После первого чтения ANGLE,
AS5048A автоматически возвращает угол при каждом следующем SPI exchange.
Можно отправлять `0x0000` и читать угол — он обновляется каждый SPI цикл.

**Обработка ответа:**
```c
uint16_t raw = ENC_ReadRaw();
uint8_t parity = (raw >> 15) & 1;
uint8_t ef = (raw >> 14) & 1;  // error flag
uint16_t angle = raw & 0x3FFF;  // 14-bit angle
// Проверка parity (even): считаем биты raw[14:0], parity должен сделать total even
```

**Расчёт скорости (исправленная формула):**

- Вызов `ENC_Update()` из TIM6 ISR (1 кГц, dt = 1 мс)
- `delta_angle` — изменение 14-битного значения за 1 мс, с учётом wrap-around (0<->16383)
- Wrap-around: если `delta > 8192` → `delta -= 16384`; если `delta < -8192` → `delta += 16384`
- **Механические RPM = delta_angle * 60 / 16384 / 0.001 = delta_angle * 3.662109**
- Фильтр: скользящее среднее по 8 выборкам (IIR 1/8)
- `ENC_GetSpeed_rpm()` возвращает **signed** int32_t (отрицательная = реверс)

### 3.2. V/f алгоритм с обратной связью (`src/vf_control.c`, `src/vf_control.h`)

**Контекст:**
- Существующий `vf_start.c` — open-loop стартер (генерирует угол рампой, без обратной связи). **Сохранить без изменений** — он используется FOC.
- Новый `vf_control.c` — **замкнутый по скорости V/f** с phase accumulator и slip.

> **Критично для асинхронного двигателя:** Нельзя использовать угол ротора
> напрямую как угол статора (это синхронное управление). Для АД нужен slip:
> `omega_e = omega_r,elec + omega_slip`, где `omega_slip` задаётся ПИ-регулятором скорости.
> Угол статора интегрируется (phase accumulator), а не берётся из энкодера.

**Архитектура (правильная для АД):**

```
                 AS5048A
                    |
                  SPI2
                    |
              ENC_Update()
                  1 kHz (TIM6)
                    |
                    v
             measured RPM (signed)
                    |
                    v
        +---------------------+
        | Speed PI controller |  error = target_rpm - measured_rpm
        +----------+----------+
                   |
              f_slip (Hz, signed)
                   |
                   v
       f_e = p*n_mech/60 + f_slip     <- электрическая частота статора
                   |                     (p = pole_pairs, n_mech в об/мин)
                   v
          phase accumulator              <- theta_e += omega_e*dt (НЕ из энкодера!)
                   |
                   v
             theta_elec (q31)
                   |
                   v
             sin/cos (CORDIC)
                   |
                   v
          V/f voltage magnitude          <- V = K_vf*|f_e| + V_boost
                   |
                   v
             OEW modulation
              +----+----+
              v         v
         PWM_SetDuty1  PWM_SetDuty2  (одинаковый duty, OEW mode 2)
              |         |
              +----+----+
                   v
                Motor
```

**Структура:**

```c
// vf_control.h
#include "foc.h"  // PIController, PI_Init, PI_Update

typedef struct {
    int32_t  target_rpm;        // целевая механическая скорость, об/мин (signed)
    int32_t  measured_rpm;      // измеренная скорость с энкодера (signed)
    int32_t  f_e_hz;            // электрическая частота статора, Гц (signed)
    int32_t  f_slip_hz;         // slip-частота, Гц (signed, выход ПИ)
    int32_t  voltage_mag;       // амплитуда напряжения, % от Vbus (0..95)
    uint32_t theta_elec;        // phase accumulator: q31, 0..2^32 = 0..2*pi
    PIController speed_pi;      // ПИ-регулятор: error(rpm) -> f_slip(Hz)
    int      running;
    // V/f параметры (настраиваемые)
    int32_t  v_boost_pct;       // voltage boost, % (0..30)
    int32_t  rated_freq_hz;     // номинальная частота, Гц (50)
    int32_t  ramp_target_rpm;   // цель рампы (для плавного разгона)
    int32_t  ramp_current_rpm;  // текущее значение на рампе
    int32_t  ramp_time_ms;      // время разгона, мс (2000)
    uint32_t ramp_tick;         // счётчик тиков рампа (1 tick = 1 ms)
} VFCtrl;

void     VFC_Init(void);
void     VFC_Start(int32_t target_rpm);
void     VFC_Stop(void);
void     VFC_SetTarget(int32_t target_rpm);  // обновление цели на лету
void     VFC_Update(void);       // Вызов из TIM6 ISR (1 кГц)
int      VFC_IsRunning(void);
int32_t  VFC_GetSpeed(void);     // measured_rpm
int32_t  VFC_GetTarget(void);   // target_rpm
void     VFC_SetVfParams(int32_t boost_pct, int32_t rated_hz);
```

**Алгоритм V/f (исправленный, для АД):**

1. **Измерение скорости:** `measured_rpm = ENC_GetSpeed_rpm()` (signed)

2. **Рампа скорости:** плавный разгон `ramp_current_rpm -> ramp_target_rpm`
   - `ramp_current_rpm += (ramp_target_rpm - ramp_current_rpm) * dt / ramp_time_ms`
   - Ограничение: +-5000 об/мин

3. **ПИ-регулятор скорости -> slip-частота:**
   - `error = ramp_current_rpm - measured_rpm` (об/мин)
   - `f_slip_hz = PI_Update(&speed_pi, error)` (выход в Гц, signed)
   - **Ограничение (v3, обязательно явным `CLAMP` после вызова):**
     `f_slip_hz = CLAMP(PI_Update(&speed_pi, error), -5, 5);`
     `PI_Update()` (см. `foc.c`) клэмпит **только внутренний интегратор**
     (`pi->integral = CLAMP(...)`), а возвращает `p_term + pi->integral`
     БЕЗ клэмпа итоговой суммы — при большой мгновенной ошибке P-член может
     вытолкнуть выход за пределы `out_max/out_min`, заданные в `PI_Init`.
     `foc.c` сам всегда клэмпит выход `PI_Update` снаружи (см. `iq_ref =
     CLAMP(PI_Update(&pi_spd, spd_err), -FOC_IQ_MAX, FOC_IQ_MAX);`) —
     здесь нужно делать то же самое, `PI_Init`'а с `out_max=5/out_min=-5`
     самого по себе недостаточно.
   - ПИ использует существующий `PIController` из `foc.h/foc.c`
   - Начальные коэффициенты: `kp=50, ki=5` (тюнить на реальном моторе)

4. **Электрическая частота статора:**
   - `f_e_hz = pole_pairs * measured_rpm / 60 + f_slip_hz` (signed)
   - `pole_pairs` берётся из `g_motor_params.pole_pairs` (или `FOC_GetPolePairs()`)
   - Ограничение: `|f_e_hz| <= 200 Гц`

5. **Phase accumulator (интегрирование угла):**
   - `delta_theta(q31) за 1 мс = f_e_hz * 2^32 / 1000 = f_e_hz * 4294967`
   - `theta_elec += (uint32_t)(f_e_hz * 4294967)` — uint32 wrap-around = модуль 2*pi
   - **При отрицательной f_e** (реверс): арифметика дополнения до 2 + uint32 wrap-around
     даёт корректное вычитание угла. **НЕ "исправлять" на int32!** (как в `vf_start.c`)

6. **V/f характеристика:**
   - `voltage_mag = K_vf * |f_e_hz| + V_boost`
   - `K_vf = 100 / rated_freq_hz` (%/Hz, т.е. 100%/50Hz = 2%/Hz)
   - `V_boost = v_boost_pct` (настраивается, 10..20%)
   - Ограничение: `voltage_mag <= 95%` (запас для ШИМ)
   - При `|f_e_hz| < 1`: `voltage_mag = V_boost` (стартовый момент)

7. **Генерация 3-фазного напряжения (через CORDIC):**
   - `sin_u = CORDIC_Sin((int32_t)theta_elec)` -> Q15 (-32768..32767)
   - `sin_v = CORDIC_Sin((int32_t)(theta_elec + 2^32/3))` -> Q15 (120° сдвиг)
   - `sin_w = CORDIC_Sin((int32_t)(theta_elec + 2*2^32/3))` -> Q15 (240° сдвиг)
   - Существующая функция: `CORDIC_SinCos(angle_q31, &sin, &cos)` из `cordic_math.h`

8. **OEW коммутация (через существующий PWM API):**
   - `d_u = 50 + (voltage_mag * sin_u) / (2 * 32768)` (центр 50%, +-voltage_mag/2)
   - `d_v = 50 + (voltage_mag * sin_v) / (2 * 32768)`
   - `d_w = 50 + (voltage_mag * sin_w) / (2 * 32768)`
   - CLAMP: `d in [2, 98]` (запас 2% как в FOC: `FOC_OEW_DUTY_MAX=49`)
   - `PWM_SetDuty1(d_u, d_v, d_w)` — TIM1 (Inv1)
   - `PWM_SetDuty2(d_u, d_v, d_w)` — TIM8 (Inv2), **одинаковый** duty
   - OEW: V_обмотки = (2*d/100 - 1) * Vbus (TIM8 mode 2, проверено)

**Интеграция с ISR:**

- `VFC_Update()` вызывается из **TIM6 ISR (1 кГц)** — не трогать FOC ISR (5 кГц)
- `ENC_Update()` также вызывается из TIM6 ISR (перед `VFC_Update()`)
- TIM6 не используется в текущем проекте — вектор `TIM6_DAC_IRQHandler` есть
  в `startup_stm32g474xx.s` (строка 204, weak alias на `Default_Handler`)
- **Не нужно менять startup файл** — достаточно определить `TIM6_DAC_IRQHandler` в `main.c`

### 3.3. TIM6 конфигурация (1 кГц ISR)

TIM6 — basic timer, нет PWM выходов, только update interrupt.

**Конфигурация (проверено по RM0440 раздел 22 — TIM6):**

```c
static void TIM6_Init_1kHz(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_TIM6EN;
    // APB1 = 170 МГц, PPRE1=/1 -> TIM6 clock = 170 МГц
    TIM6->PSC = 169;   // 170 МГц / 170 = 1 МГц
    TIM6->ARR = 999;   // 1 МГц / 1000 = 1 кГц
    TIM6->DIER |= TIM_DIER_UIE;   // Update interrupt enable
    TIM6->CR1 |= TIM_CR1_CEN;     // Start
    NVIC_SetPriority(TIM6_DAC_IRQn, 1);  // Ниже ADC (priority 0), выше UART (priority 2)
    NVIC_EnableIRQ(TIM6_DAC_IRQn);
}

void TIM6_DAC_IRQHandler(void) {
    if(TIM6->SR & TIM_SR_UIF) {
        TIM6->SR = ~TIM_SR_UIF;  // Clear flag
        ENC_Update();            // Чтение SPI2 + расчёт скорости
        if(VFC_IsRunning()) {
            /* КРИТИЧНО: injected-группа АЦП в V/f-режиме не запущена
             * (см. §3.5 — FOC не активен, ADC ISR не работает), значит
             * adc_data НЕ обновляется сама по себе, как в FOC-цикле
             * (там это делает ADC_ReadInjected() в ADC1_2_IRQHandler
             * прямо перед PROTECT_Check(), см. main.c). Без явного
             * ADC_StartConversion() здесь PROTECT_Check() проверял бы
             * протухшие (последние закэшированные до FOC_Stop()) значения
             * тока/Vbus и НИКОГДА не увидел бы реальный бросок тока —
             * защита в V/f-режиме была бы фиктивной. */
            ADC_StartConversion();  // regular-группа, ~58 мкс, обновляет adc_data
            VFC_Update();           // V/f control loop
            // Защита: проверка тока в V/f режиме (теперь на свежих данных)
            PROTECT_Check();
            if(PROTECT_IsFault()) VFC_Stop();
        }
    }
}
```

> **Важно:** `TIM6_DAC_IRQHandler` уже есть в `startup_stm32g474xx.s` (строка 204)
> как weak alias на `Default_Handler`. Достаточно определить функцию в `main.c` —
> линкер подхватит её вместо weak. **Startup файл не менять.**
>
> **Важно (v3):** `ADC_StartConversion()` — это та же regular-группа, что
> использует `autotune.c`. Конфликта с injected-группой не будет: в V/f-режиме
> она остановлена (`FOC_Stop()` → `ADC_InjectedStop()` перед `VFC_Start()`),
> а `adc2_read()` внутри `ADC_StartConversion()` и так умеет корректно
> приостановить/восстановить `JADSTART`, если он всё же оказался взведён.

### 3.4. Интеграция в `main.c`

**Новые UART команды:**

| Команда | Описание |
|---|---|
| `enc` | Чтение угла и скорости: `@ENC:angle=XXXX:speed=XXXX:raw=XXXX:err=X` |
| `vf=N` | Запуск V/f с целевой скоростью N об/мин (signed: `-5000..+5000`) |
| `vf=0` | Остановка V/f |
| `vf?` | Статус: `@VF:target=XXXX:meas=XXXX:fe=XXXX:fslip=XXXX:vmag=XXXX` |
| `vfk=V,F` | Установка V/f: V=boost(%), F=rated_freq(Hz) |

**Порядок инициализации в `main()`:**

```c
// После PWM_Init() и ADC_Init():
ENC_Init();     UART_SendStr("Encoder OK\r\n");
VFC_Init();     UART_SendStr("V/f Ctrl OK\r\n");
// TIM6 для 1 кГц вызова ENC_Update + VFC_Update:
TIM6_Init_1kHz();
```

**Main loop — телеметрия:**

```c
// В существующем блоке телеметрии (после @FOC:...):
if(VFC_IsRunning() && (sys_tick_ms - last_telem_ms) >= 100) {
    UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n",
        (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
        (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
}
```

**Обработка команд `vf=N`:**
```c
else if(sscanf(linebuf, "vf=%d", &a1) == 1) {
    if(a1 == 0) {
        VFC_Stop();
        UART_SendStr("V/f stopped\r\n> ");
    } else if(a1 >= -5000 && a1 <= 5000) {
        if(PROTECT_IsFault()) {
            UART_SendStr("FAULT! send 'f' to clear\r\n> ");
        } else {
            FOC_Stop();      // mutual exclusion
            VFC_Start(a1);
            UART_SendTelemetry("V/f started: %d rpm\r\n> ", a1);
        }
    } else {
        UART_SendStr("err: rpm range -5000..+5000\r\n> ");
    }
}
else if(strcmp(linebuf, "vf?") == 0) {
    UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n> ",
        (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
        (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
}
else if(strcmp(linebuf, "enc") == 0) {
    UART_SendTelemetry("@ENC:angle=%u:speed=%ld:raw=%04X:err=%u\r\n> ",
        (unsigned)ENC_GetAngle14(), (long)ENC_GetSpeed_rpm(),
        (unsigned)ENC_ReadRaw(), (unsigned)ENC_GetError());
}
```

### 3.5. Конфликт с FOC и защита

**Mutual exclusion:**
- V/f и FOC — **взаимоисключающие** режимы
- При запуске V/f: `FOC_Stop()` -> `VFC_Start()` (FOC_Stop останавливает PWM + ADC injected)
- При запуске FOC: `VFC_Stop()` -> `FOC_Start()` (VFC_Stop останавливает PWM + TIM6 VFC loop)
- Оба используют `PWM_SetDuty1/2` + `PWM_Enable/Disable`

**VFC_Start / VFC_Stop процедура:**
```c
void VFC_Start(int32_t target_rpm) {
    if(vfc.running) return;
    if(PROTECT_IsFault()) return;  // не запускать при fault
    ADC_CalibrateOffsets();        // калибровка нуля токов (инвертор выключен)
    vfc.target_rpm = target_rpm;
    vfc.ramp_target_rpm = target_rpm;
    vfc.ramp_current_rpm = 0;     // плавный разгон с нуля
    vfc.ramp_tick = 0;
    vfc.theta_elec = 0;           // phase accumulator с нуля
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
    PI_Init(&vfc.speed_pi, 50, 5, 5, -5);  // kp=50, ki=5, out=+-5 Hz (slip)
    vfc.running = 1;
    PWM_Enable();                  // Включение TIM1+TIM8 (как в FOC_Start)
    // ADC injected НЕ запускается — V/f не использует ADC ISR (FOC не активен)
}

void VFC_Stop(void) {
    vfc.running = 0;
    PWM_Disable();                 // CEN=0, MOE=0, EN1/EN2=LOW
    vfc.ramp_current_rpm = 0;
    vfc.f_e_hz = 0;
    vfc.f_slip_hz = 0;
}
```

**Защита (критично):**
- `PROTECT_Check()` вызывается из TIM6 ISR при `VFC_IsRunning()` (см. 3.3)
- При fault: `PROTECT_Check()` вызывает `PWM_Disable()` (внутри `protect.c`)
- `VFC_Stop()` также вызывается — VFC не должен повторно включать PWM
- `VFC_Start()` проверяет `PROTECT_IsFault()` — не запускать при активном fault
- Сброс fault — только командой `f` (существующая логика в `main.c`)
- **VFC не должен вызывать `PWM_Enable()` после fault** — только после `PROTECT_Clear()` + новый `VFC_Start()`

## 4. Программная часть — GUI (`nucleo_debug_tool.py`)

### 4.1. Добавления в PWM-вкладку

В существующий класс `PWMTab` (строка 299) добавить **новую секцию "V/f Control"**.

PWMTab.__init__ (строка 327) вызывает:
```python
self._build_channels_panel()
self._build_params_panel()
self._build_status_panel()
self._build_saleae_panel()
```

Добавить: `self._build_vf_panel()` — вызов после `_build_status_panel()`.

**Размещение:** `row=2, column=0, columnspan=3` (под Sigrok panel, row=1)

**Элементы управления:**

| Элемент | Тип | Описание |
|---|---|---|
| Target RPM | Spinbox | Целевая скорость, об/мин (-5000..+5000) |
| V/f Boost (%) | Spinbox | Voltage boost на нулевой частоте (0..30) |
| Rated Freq (Hz) | Spinbox | Номинальная частота (50/60 Гц) |
| Start V/f | Button | Отправляет `vf=<RPM>` |
| Stop V/f | Button | Отправляет `vf=0` |
| Encoder | Button | Отправляет `enc` |
| V/f Status | Label | Отображает `@VF:target=...:meas=...:fe=...:fslip=...:vmag=...` |
| Encoder Angle | Label | Отображает угол в градусах |
| Encoder Speed | Label | Отображает скорость в об/мин |

**Логика:**
- При нажатии "Start V/f": отправить `vf=<target_rpm>` по UART через `self.send()`
- При нажатии "Stop V/f": отправить `vf=0`
- Парсить входящие строки `@VF:...` и `@ENC:...` в существующем обработчике `on_line()`
- Кнопка "Encoder" — single-shot запрос `enc`
- V/f Boost и Rated Freq отправляются командой `vfk=V,F` при изменении

### 4.2. Телеметрия — парсинг

Парсить строки (в `on_line` методе или в `_handle_telemetry`):
```python
# @VF:target=1500:meas=1480:fe=50:fslip=2:vmag=45
if line.startswith('@VF:'):
    kv = dict(re.findall(r'(\w+)=(-?\d+)', line))
    self.vf_status_label.config(text=f"Target: {kv.get('target','?')} rpm | Meas: {kv.get('meas','?')} | fe: {kv.get('fe','?')} Hz | Vmag: {kv.get('vmag','?')}%")

# @ENC:angle=12345:speed=1495:raw=3FFF:err=0
if line.startswith('@ENC:'):
    kv = dict(re.findall(r'(\w+)=(-?\d+)', line))
    angle_deg = int(kv.get('angle',0)) * 360 / 16384
    self.enc_angle_label.config(text=f"Angle: {angle_deg:.1f} deg")
    self.enc_speed_label.config(text=f"Speed: {kv.get('speed','?')} rpm")
```

## 5. Файлы для создания/изменения

### Новые файлы:
- `src/encoder.c` — драйвер AS5048A (SPI2)
- `src/encoder.h` — заголовок
- `src/vf_control.c` — V/f алгоритм с phase accumulator + slip
- `src/vf_control.h` — заголовок

### Изменяемые файлы:
- `main.c` — инициализация ENC/VFC, TIM6 ISR, UART-команды (`vf=`, `enc`, `vf?`, `vfk=`)
- `nucleo_debug_tool.py` — V/f Control секция в PWMTab (метод `_build_vf_panel`)
- `Makefile` — добавить `src/encoder.c` и `src/vf_control.c` в `C_SOURCES`

### НЕ ИЗМЕНЯТЬ:
- `startup_stm32g474xx.s` — `TIM6_DAC_IRQHandler` уже в vector table (weak alias)
- `src/vf_start.c/.h` — существующий open-loop стартер для FOC, не трогать
- `src/foc.c/.h` — FOC не изменять, только вызывать `FOC_Stop()` при запуске V/f
- `src/pwm.c/.h` — PWM API не изменять, использовать существующие функции
- `src/protect.c/.h` — защиту не изменять, использовать `PROTECT_Check/Clear/IsFault`
- `linker.ld` — не изменять

## 6. Ограничения и правила

1. **Только CMSIS, без HAL** — все обращения к регистрам через CMSIS
2. **Проверка по RM0440** — раздел 28 (SPI), раздел 22 (TIM6)
3. **Сборка:** `make` + `make flash`
4. **Тестирование:** UART 115200, команды `enc`, `vf=N`, `vf=0`, `vf?`
5. **Совместимость:** не ломать существующие FOC и autotune функции
6. **Защита:** `PROTECT_Check()` вызывается из TIM6 ISR при `VFC_IsRunning()`;
   VFC не должен повторно включать PWM после fault
7. **Пины PB6/PB10/PB14/PB15** — свободны, проверено по `GPIO_Init()` в `main.c`
8. **Phase accumulator, не угол энкодера** — угол статора интегрируется из omega_e,
   энкодер используется только для измерения механической скорости
9. **Slip для АД** — `f_e = p*n/60 + f_slip`, где `f_slip` от ПИ-регулятора скорости
10. **Signed частоты** — `f_e_hz` и `f_slip_hz` — `int32_t` (отрицательная = реверс)
11. **CORDIC** — использовать `CORDIC_SinCos()` из `cordic_math.h` (уже инициализирован)
12. **PIController** — использовать существующий из `foc.h` (`PI_Init`, `PI_Update`)

## 7. Порядок реализации

1. **Драйвер энкодера** — `encoder.c/h`, инициализация SPI2, чтение угла с parity check
2. **Тест** — команда `enc` в UART, проверка вращения магнита (угол меняется)
3. **TIM6 1 кГц** — конфигурация TIM6 + ISR, вызов `ENC_Update()`
4. **Тест** — `enc` показывает скорость при вращении вала рукой
5. **V/f алгоритм** — `vf_control.c/h`, ПИ -> slip -> phase accumulator -> OEW PWM
6. **Тест** — команда `vf=500`, проверка вращения вала
7. **Тест** — команда `vf=-500`, проверка реверса
8. **GUI** — V/f Control секция в PWM-вкладке
9. **Тест** — управление через GUI, проверка скорости и направления
10. **Тест защиты** — превышение тока -> fault -> PWM off -> VFC_Stop

## 8. Параметры двигателя (для V/f)

- Номинальное напряжение: 24V DC (Vbus)
- Номинальная частота: 50 Гц (настраивается через `vfk=`)
- Пар полюсов: из `g_motor_params.pole_pairs` (устанавливается через `pp=N`)
- V/f коэффициент: `K_vf = 100% / rated_freq_hz` (2%/Hz при 50 Гц)
- Voltage boost: 15% (настраивается через `vfk=`)
- Максимальная частота: 200 Гц
- Максимальный slip: +-5 Гц (типовое значение для АД)
- Ramp time: 2000 мс (плавный разгон)
- ПИ скорости: kp=50, ki=5 (тюнить на реальном моторе)

## 9. Существующий код (контекст для разработчика)

### Занятые пины (НЕ ИСПОЛЬЗОВАТЬ — проверено по `main.c` `GPIO_Init()`):
```
PA0  — ADC2_IN1 (I1, фазный ток A)
PA1  — ADC2_IN2 (I2, фазный ток B)
PA2  — USART2_TX (UART консоль, AF7)
PA3  — USART2_RX (AF7)
PA6  — ADC2_IN3 (Ires, ток DC-звена)
PA7  — TIM1_CH1N (PWM Inv1 LIN U, AF6)
PB0  — TIM1_CH2N (PWM Inv1 LIN V, AF6)
PB1  — TIM1_CH3N (PWM Inv1 LIN W, AF6)
PB4  — EN1 (GPIO output, enable Inv1)
PB5  — EN2 (GPIO output, enable Inv2)
PC0  — TIM1_CH1  (PWM Inv1 HIN U, AF2)
PC1  — TIM1_CH2  (PWM Inv1 HIN V, AF2)
PC2  — TIM1_CH3  (PWM Inv1 HIN W, AF2)
PC4  — ADC2_IN5  (VBUS)
PC6  — TIM8_CH1  (PWM Inv2 HIN U, AF4)
PC7  — TIM8_CH2  (PWM Inv2 HIN V, AF4)
PC8  — TIM8_CH3  (PWM Inv2 HIN W, AF4)
PC10 — TIM8_CH1N (PWM Inv2 LIN U, AF4)
PC11 — TIM8_CH2N (PWM Inv2 LIN V, AF4)
PC12 — TIM8_CH3N (PWM Inv2 LIN W, AF4)
```

### Свободные пины для AS5048A (подтверждено — не используются нигде):
```
PB6  — CS  (GPIO Output, Pull-up) — НЕ настроен в GPIO_Init()
PB10 — SPI2_SCK (AF5) — НЕ настроен
PB14 — SPI2_MISO (AF5) — НЕ настроен
PB15 — SPI2_MOSI (AF5) — НЕ настроен
```

### OEW коммутация (критично, из `pwm.c` и `foc.c`):
- TIM1: PWM mode 1 (OCxM=110), активен при CNT < CCR
- TIM8: PWM mode 2 (OCxM=111), активен при CNT > CCR
- Одинаковый CCR на обоих -> V_обмотки = (2*duty/100 - 1) * Vbus
- `PWM_SetDuty1(u,v,w)` — TIM1, `PWM_SetDuty2(u,v,w)` — TIM8
- Duty в процентах 0..100, **50% = нулевое напряжение** на обмотке
- CLAMP duty: 1..98% (FOC использует `FOC_OEW_DUTY_MAX=49`, запас 1%)
- `PWM_Enable()` — включает EN1/EN2 + CCER + BDTR + CEN (как в `FOC_Start`)
- `PWM_Disable()` — CEN=0, MOE=0, EN1/EN2=LOW

### Существующие утилиты (использовать, не дублировать):
- `CORDIC_SinCos(angle_q31, &sin_q15, &cos_q15)` — синус/косинус (`cordic_math.h`)
- `PIController` + `PI_Init(pi, kp, ki, max, min)` + `PI_Update(pi, error)` — ПИ (`foc.h`)
- `PWM_SetDuty1/2(u,v,w)` — установка duty 0..100% (`pwm.h`)
- `PWM_Enable/Disable()` — включение/выключение PWM (`pwm.h`)
- `PWM_GetARR()` — текущий ARR (`pwm.h`, возвращает 999 при 170 МГц)
- `PROTECT_Check()` — проверка перегрузки (`protect.h`) — вызывает `PWM_Disable()` при fault
- `PROTECT_IsFault()` / `PROTECT_Clear()` — состояние/сброс защиты
- `ADC_GetVbus_mV()` — напряжение шины в мВ (`adc.h`)
- `ADC_CalibrateOffsets()` — калибровка нуля токов (`adc.h`)
- `FOC_Stop()` — остановка FOC (`foc.h`) — для mutual exclusion
- `FOC_GetPolePairs()` — текущее число пар полюсов (`foc.h`)
- `g_motor_params.pole_pairs` — из `autotune.h` (устанавливается `pp=N`)
- `UART_SendStr/SendTelemetry` — вывод в UART (`uart.h`)

### Clock tree (проверено по `main.c` RCC config):
```
HSI 16 МГц -> PLL: M=3, N=85, R=0 -> PLLR = 170 МГц
SystemCoreClock = 170 МГц
APB1 = 170 МГц (PPRE1 = /1, не делится)
APB2 = 170 МГц (PPRE2 = /1, не делится)
TIM1/TIM8 clock = 170 МГц (APB2, prescaler /1 -> timer = HCLK)
TIM6 clock = 170 МГц (APB1, prescaler /1 -> timer = HCLK)
SPI2 clock = 170 МГц (APB1)
```

### TIM6 (1 кГц ISR):
```
PSC = 169  -> 170 МГц / 170 = 1 МГц
ARR = 999  -> 1 МГц / 1000 = 1 кГц
```

### SPI2 (для AS5048A):
```
BR = /32 -> 170 МГц / 32 = 5.3 МГц (запас от лимита 10 МГц)
CPOL=1, CPHA=1 (SPI mode 1, AS5048A)
16-bit frame (DFF=1)
Software CS (PB6)
```

### Vector table (startup_stm32g474xx.s):
- `TIM6_DAC_IRQHandler` — строка 204, weak alias на `Default_Handler` (строка 452)
- `SPI2_IRQHandler` — строка 186, weak alias (для прерываний SPI, если нужны)
- **Startup файл НЕ изменять** — достаточно определить функцию в C коде

### Makefile — добавить в C_SOURCES (после `src/autotune.c`):
```makefile
$(SRC_DIR)/encoder.c \
$(SRC_DIR)/vf_control.c
```
