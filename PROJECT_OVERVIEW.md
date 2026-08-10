# OEW Motor — Project Overview for Web AI

## Hardware Platform

**MCU:** STM32G474RE (Cortex-M4F, 170 MHz, FPU, CORDIC)
**Board:** Nucleo-G474RE (ST-Link V3, SWD)
**Inverter:** 2× STEVAL-IPM20B (IGBT 3-phase, **общий DC-link**, 2 фазных шунта I1/I2 0.03Ω + Ires трансформатор DC-звена; диапазон измерения ±26.2 A, модуль 10 A max)
**Logic Analyzer:** Saleae Logic 16ch (via sigrok-cli, driver fx2lafw, practical rate 8 MHz max)

## ⚡ OEW-коммутация (КРИТИЧНО, финальное решение 7e9f7b0)

**Двигатель — АСИНХРОННЫЙ** (нет магнитов, Ke не нужен — ЭДС через Lm/Rr/Tr).

Два инвертора питают обмотки с двух концов (Open-End Winding), общее DC-звено.
**TIM8 в PWM mode 2** (OCxM=111, активен при CNT>CCR) + **одинаковый CCR** с TIM1:

```c
// foc.c: d1u = d2u = 50 + vu·49/32768  (ОБА инвертора одинаково!)
TIM1 mode 1 (CNT<CCR): HIN_U1=1 → узел U1 = +Vbus
TIM8 mode 2 (CNT>CCR): LIN_U2=1 → узел U2 = GND
→ HIN_U1=1 ⇔ LIN_U2=1 → ток по обмотке
→ V_U = (2·CCR − ARR)·Vbus/ARR = (2d/100 − 1)·Vbus — линейно по duty
```

**Следствия (проверены железом):**
- mode 1 на обоих = HIN синфазны → LIN_U2=0 при HIN_U1=1 → **нет тока**
- mode 2 + разные CCR (half±ccr) = оба плеча в одну сторону → V_U≈0 (баг был в autotune OEW)
- «не инверсные» LIN = сквозной ток = КЗ (UM2705: входы IPM active-high, инверторов на плате нет)

### Pinout (final working)

| Pin | Function | Timer | AF |
|-----|----------|-------|----|
| PC0 | HIN_U1 (Inv1 high side U) | TIM1_CH1 | AF2 |
| PA7 | LIN_U1 (Inv1 low side U) | TIM1_CH1N | AF6 |
| PC1 | HIN_V1 | TIM1_CH2 | AF2 |
| PB0 | LIN_V1 | TIM1_CH2N | AF6 |
| PC2 | HIN_W1 | TIM1_CH3 | AF2 |
| PB1 | LIN_W1 | TIM1_CH3N | AF6 |
| PC6 | HIN_U2 (Inv2) | TIM8_CH1 | AF4 |
| PC10 | LIN_U2 (Inv2 low side U) | TIM8_CH1N | AF4 |
| PC7 | HIN_V2 | TIM8_CH2 | AF4 |
| PC11 | LIN_V2 | TIM8_CH2N | AF4 |
| PC8 | HIN_W2 | TIM8_CH3 | AF4 |
| PC12 | LIN_W2 | TIM8_CH3N | AF4 |
| PA0 | I1 (phase current sensor) | ADC2_IN1 | — |
| PA1 | I2 (phase current sensor) | ADC2_IN2 | — |
| PA6 | IN (DC-link shunt) | ADC2_IN3 | — |
| PC4 | VBUS (bus voltage divider 1:125) | ADC2_IN5 | — |
| PA2 | USART2_TX | — | — |
| PA3 | USART2_RX | — | — |
| PB4 | EN1 (Inv1 enable) | GPIO | — |
| PB5 | EN2 (Inv2 enable) | GPIO | — |

### Подключение обмоток двигателя (OEW, проверено осциллографом 06.08)

Каждая катушка одним концом висит на **инверторе 1** (узел U1/V1/W1), другим — на **инверторе 2** (узел U2/V2/W2). Узел = точка соединения верхнего (HIN) и нижнего (LIN) ключей инвертора.

| Катушка | Конец | Узел | Инвертор | Верхний ключ (HIN) | Нижний ключ (LIN) |
|---------|-------|------|----------|--------------------|--------------------|
| **A (фаза U)** | Начало | **U1** | Inv1 (TIM1_CH1) | **PC0** | **PA7** |
| | Конец | **U2** | Inv2 (TIM8_CH1) | **PC6** | **PC10** |
| **B (фаза V)** | Начало | **V1** | Inv1 (TIM1_CH2) | **PC1** | **PB0** |
| | Конец | **V2** | Inv2 (TIM8_CH2) | **PC7** | **PC11** |
| **C (фаза W)** | Начало | **W1** | Inv1 (TIM1_CH3) | **PC2** | **PB1** |
| | Конец | **W2** | Inv2 (TIM8_CH3) | **PC8** | **PC12** |

Направление тока: HIN_U1=1 ⇔ LIN_U2=1 всегда (mode 2) → ток по катушке A от U1 к U2.
Напряжение обмотки: V_A = V_U1 − V_U2 = (2·d/100 − 1)·Vbus.

**Проверено (лог 152128):** PC0 HIN_U1=50.0%, PC6 HIN_U2=34.8% (пара A); PC1/PC7 (пара B); dead-time ≈1500 нс на всех парах.

⚠️ «Начало»/«конец» — условно: для АД полярность важна только для согласования фаз при пуске; инверсия конца = сдвиг 180° (все три катушки должны быть согласованы одинаково).

### Current Sensing Topology

STEVAL-IPM20B current sensing (one-shunt в DC-звене, сигнал дублируется на 3 пина):
- **I1 (PA0) / I2 (PA1)** — фазные токи через ОУ (Gain=2.1, bias 1.65В), используются **FOC** для Clarke (2-датчиковая: iu=i1, iv=i2, iw=−iu−iv)
- **Ires (PA6, ADC2_IN3)** — ток DC-звена (one-shunt), **диагностический**: в OEW сумма фаз НЕ обязана быть 0, Ires показывает zero-sequence ток iz (общий DC → контур iz замкнут). 3-датчиковый Clarke (iw=Ires−iu−iv) — задел на будущее.

**ВНИМАНИЕ (OEW):** 2-датчиковая формула Clarke подразумевает iu+iv+iw=0 (звезда). В OEW это приближение — iz может быть ненулевым (3-я гармоника ЭДС, dead-time).

**Shunt parameters:** Rshunt=0.03Ω, Gain=2.1 → 0.063 В/А, ADC Vref=3.3V, 12-bit → 1 code ≈ 12.8 mA (I_mA = diff·3300·1000/(4095·63000) ≈ diff·12.79)

**Voltage sensing:** VBUS pin via resistor divider 1:125. Raw ADC → Vbus_mV = raw * 3300 * 125 / 4095

### Saleae / Sigrok Connection

**16-канальный анализатор.** Подключение подтверждено пользователем: Ch1..Ch12 → D0..D11 последовательно, по порядку списка. Обновлено после перехода на PWM-энкодер (коммит 9fe2cf3) — подключены ВСЕ 12 ШИМ-сигналов обоих инверторов (включая W-фазы, которые раньше не были подключены), D13 — аппаратный sync-триггер.

| Канал sigrok | Пин MCU | Сигнал | Инвертор | Маска прошивки |
|---|---|---|---|---|
| D0  | PC0  | HIN_U1  | Inv1 (TIM1_CH1)   | 0x01 CC1E  |
| D1  | PC1  | HIN_V1  | Inv1 (TIM1_CH2)   | 0x04 CC2E  |
| D2  | PA7  | LIN_U1  | Inv1 (TIM1_CH1N)  | 0x02 CC1NE |
| D3  | PB0  | LIN_V1  | Inv1 (TIM1_CH2N)  | 0x08 CC2NE |
| D4  | PC2  | HIN_W1  | Inv1 (TIM1_CH3)   | 0x10 CC3E  |
| D5  | PB1  | LIN_W1  | Inv1 (TIM1_CH3N)  | 0x20 CC3NE |
| D6  | PC11 | LIN_V2  | Inv2 (TIM8_CH2N)  | 0x08 CC2NE |
| D7  | PC10 | LIN_U2  | Inv2 (TIM8_CH1N)  | 0x02 CC1NE |
| D8  | PC7  | HIN_V2  | Inv2 (TIM8_CH2)   | 0x04 CC2E  |
| D9  | PC6  | HIN_U2  | Inv2 (TIM8_CH1)   | 0x01 CC1E  |
| D10 | PC8  | HIN_W2  | Inv2 (TIM8_CH3)   | 0x10 CC3E  |
| D11 | PC12 | LIN_W2  | Inv2 (TIM8_CH3N)  | 0x20 CC3NE |
| D13 | PB6  | sync-триггер (аппаратный) | — | vf_panel.py `TRIGGER_SIGROK_CHANNEL` |

Маска бита = схема прошивки `PWM_SetMask` в `src/pwm.c` (GUI сверяет ожидаемые сигналы). Таблица каналов — в `nucleo_debug_tool.py`, класс `PWMTab.CHANNELS` / `CHANNELS_INV2`.

Sigrok-cli 0.8.0 at `C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe`, driver fx2lafw, практическая частота 8 MHz max (в GUI `SaleaeHelper.capture_sync`, digital: `min(sample_rate, 8_000_000)`).

---

## Software Architecture

### Firmware (CMSIS-only, no HAL)

**Build:** arm-none-eabi-gcc (GNU Arm Embedded 13.2.rel1), Makefile, linker.ld
**Flash:** STM32_Programmer_CLI via `make flash`

#### Source Files

| File | Purpose |
|------|---------|
| `main.c` | Init, CLI loop, command parsing |
| `system_stm32g4xx.c` | SystemCoreClockUpdate |
| `startup_stm32g474xx.s` | Vector table |
| `src/pwm.c` / `pwm.h` | TIM1+TIM8 config, PWM control, dead-time, test functions |
| `src/adc.c` / `adc.h` | ADC2 init, regular/injected conversion, current/voltage read |
| `src/uart.c` / `uart.h` | USART2 115200, line-buffered read, SendStr, SendTelemetry |
| `src/cordic_math.c` / `.h` | CORDIC-accelerated sin/cos/sqrt for FOC |
| `src/foc.c` / `foc.h` | FOC: Clarke/Park, PI (модульный оптимум Kp/Ki), компенсация перекрёстных связей dq, dead-time компенсация, OEW d2=d1, Voltage Manager integration |
| `src/voltage_manager.c` / `.h` | Voltage vector limiter (Q15, CORDIC-based, flux/torque priority) |
| `src/observer.c` / `.h` | BEMF observer (использует **Lσ**, не Ls — насыщение-безопасно, стр.171 Антиучебника) |
| `src/pll.c` / `.h` | PLL for speed/angle tracking |
| `src/flux_weakening.c` / `.h` | Field weakening at high speed |
| `src/vf_start.c` / `.h` | V/f open-loop startup sequence |
| `src/protect.c` / `.h` | Overcurrent/overvoltage protection |
| `src/autotune.c` / `autotune.h` | Автотюнинг АД: RS_IV, PAIRS, IDLE (кривая L(I)+Isat), LSPOS, OEW, RR (lock-in), NOLOAD, IROT, INERTIA, SCOPE |

#### Firmware Configuration Constants

- **System clock:** PLL from HSI16 (16 MHz) → PLLM=4, PLLN=85, PLLR=2 → 170 MHz
- **APB2 timer clock:** 170 MHz (PPRE2=1)
- **t_CK_INT:** 170 MHz (timer clock BEFORE PSC, used for dead-time t_DTS)
- **PWM frequency:** 5000 Hz (center-aligned, TIM1+TIM8)
- **PSC:** 16 → timer_clk = 170/17 = 10 MHz
- **ARR:** 999 (PWM_GetARR() returns this)
- **Dead-time:** 1500 ns → DTG=0xC0 (256 ticks × 5.88ns)
- **ADC:** 12-bit, SMPR=7 (601.5 cycles), injected group from TIM1_TRGO, 4 conversions (I1, I2, IN, VBUS)

#### Key PWM Functions

```c
void PWM_Init(void);           // One-time init of TIM1+TIM8
void PWM_SetDuty1(u,v,w);      // Set duty % (0-100) for TIM1 phases A,B,C
void PWM_SetDuty2(u,v,w);      // Same for TIM8
void PWM_Enable(void);         // MOE, CEN for both timers
void PWM_Disable(void);        // Stop both
void PWM_DebugConfig(arr,duty,dt_ns,mask);  // Direct test config
uint16_t PWM_GetARR(void);     // Returns current ARR value
void PWM_SetDeadTime_ns(ns);   // Set dead-time in nanoseconds
void PWM_GetStatus(&cr1,&ccer,&bdtr,&cnt);  // Read TIM1 status regs
```

#### FOC Control Loop Architecture

```text
Speed PI ──► Iq_ref
FW ──────► Id_ref (Id_add)
              │
              ▼
      Current PI_d ──► Vd ──┐
      Current PI_q ──► Vq ──┤
                            ▼
                  VoltageManager_Update()
                    • Vmax_Q15 = 29490 (90% of 32767)
                    • CORDIC_Modulus for |Vdq|
                    • Flux priority: Vd preserved, Vq = ±sqrt(Vmax²-Vd²)
                    • Anti-windup: vd_err/vq_err → PI integral correction
                            │
                            ▼
                  InvPark ──► Vα, Vβ ──► InvClarke ──► Vu, Vv, Vw
                            │
                            ▼
                  OEW: TIM1 = 50% + V/2, TIM8 = 50% - V/2
                  CLAMP duty to 1..98%
                            │
                            ▼
                  Real voltage feedback → observer
                  (duty1-duty2)*32768/100 → forward Clarke → prev_valpha/vbeta
```

**Voltage Manager** (`src/voltage_manager.c`):
- Thin mathematical module, works exclusively in Q15
- No dependencies on PI, FW, UART — pure function `(vd, vq, Vmax) → (vd_lim, vq_lim, saturated)`
- Uses hardware `CORDIC_Modulus()` for vector magnitude and sqrt
- Two priority strategies: `VM_PRIORITY_FLUX` (default for PMSM) and `VM_PRIORITY_TORQUE`
- Anti-windup via `vd_err`/`vq_err` outputs — PI controllers apply correction externally

**Observer voltage feedback**: observer receives **actual** voltage after PWM CLAMP, not command voltage. This automatically accounts for OEW coefficient (49/50 ≈ 2%) and PWM saturation.

#### Key ADC Functions

```c
void ADC_Init(void);
void ADC_CalibrateOffsets(void);       // 8-sample zero calibration
void ADC_CalibrateI1_256(void);        // 256-sample zero cal (debug)
void ADC_StartConversion(void);        // Software-triggered regular conversion
int32_t ADC_GetI1_mA(void);            // Фазный ток A (FOC Clarke)
int32_t ADC_GetI2_mA(void);            // Фазный ток B (FOC Clarke)
int32_t ADC_GetIres_mA(void);          // Ток DC-звена (диагностика iz)
int32_t ADC_GetVbus_mV(void);          // Напряжение шины
uint16_t ADC_GetRawI1/I2/Ires/Vbus();  // Сырые коды
```

**CRITICAL:** FOC Clarke — только `ADC_GetI1_mA()`/`ADC_GetI2_mA()` (фазные шунты). `ADC_GetIres_mA()` — ток DC-звена, диагностика zero-sequence (в OEW iw≠−(iu+iv)!). Все три канала (I1, I2, Ires) физически присутствуют — это архитектура 2 фазных датчика + DC-link, НЕ single-shunt. Автотюн выбирает канал автоматически (`Autotune_DetectChannel`).

#### UART Protocol

- 115200 baud, 8N1
- Line-based: each command/reply ends with `\r\n`
- Prompt: `> `
- Telemetry format: `@PREFIX:KEY1=VALUE1:KEY2=VALUE2\r\n`
- CLI parser in main.c `while(1)` loop, `UART_ReadLine()` returns line buffer
- Command examples:
  - `p=99,15,1500,63` — PWM config (arr, duty%, dt_ns, mask)
  - `mp=Rs,Ls,Rr,Lm,Tr,Ke,p,J` — ручной ввод параметров (mp=5000,50000,0,0,0,0,4,0); применяет модульный оптимум Kp/Ki, считает Lσ
  - `dt=1500` — dead-time в нс
  - `iv` — multi-point Rs (RS_IV)
  - `ch` — detect current channel
  - `pairs` — measure AB/BC/CA
  - `idle` — кривая L(I) + Isat (5 повторов)
  - `lspos` — Ls от положения ротора (6 замеров, сохраняет медиану)
  - `oew` — OEW кривая L(I) (диф-драйв mode 2)
  - `rr` — Rr lock-in на 5 Гц (заблокировать ротор!)
  - `noload` — Lm/Lr/Tr (свободный ротор, V/f до 50 Гц)
  - `irot` / `inertia` — (заглушки/stub)
  - `scope` — осциллограмма 100 точек тока
  - `abort` — stop running test
  - `params` — show motor parameters (@PARAMS)
  - `curve` — show saturation curve
  - `stats` — show statistics
  - `c` — ADC calibration
  - `sysinfo` — system info
  - `dump` / `dump8` — TIM1/TIM8 register dump

#### Порядок автотюнинга АД (рекомендуемый)

```
iv → ch → lspos → oew → rr → noload
Rs     канал  Ls     Ls     Rr     Lm/Lr/Tr
```
- Ls: LSPOS/OEW должны сойтись ±30% (AT_SaneLs: 0.5..500 мГн, иначе REJECT)
- RR: Rtotal = Rs + Rr' ≥ Rs (иначе @ERR, Rr не пишется)
- NOLOAD: Lm = Ltotal − Ls > 0 (иначе @ERR:LTOTAL_LTE_LS — признак мусорной Ls)

#### Auto-Tune Protocol

```
> ch
@AT:CH_DETECT:OK:CH=3:I=412:SIGN=1
@AT:CH:OK

> idle
@IDLE:START
@IDLE:PROG=5/50:D=5:I=244:L=629:REP=1/5
...
@IDLE:PROG=50/50:D=50:I=2500:L=320:REP=5/5
@AT:STAT:Rs=850:820:890:8%:Ls=620:610:635:4%:Isat=2600:2500:2700:7%
@PARAMS:Rs=850:Ls=620:Isat=2600:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:CH=3
@IDLE:DONE
@IDLE:OK

> iv
@AT:RS_IV:START
@AT:RS_IV:POINT:D=2:U=480:I=85
@AT:RS_IV:POINT:D=4:U=960:I=162
...
@AT:RS_IV:OK:Rs=852
@AT:IV:OK

> pairs
@AT:PAIRS:START
@AT:PAIR:AB:Rs=850:Ls=620:Isat=0:V=1
@AT:PAIR:BC:Rs=845:Ls=615:Isat=0:V=1
@AT:PAIR:CA:Rs=860:Ls=630:Isat=0:V=1
@AT:PAIRS:OK:Rs=852:Ls=622:ASYM=1%
@AT:PAIRS:RESULT_OK
```

---

### GUI (Python/Tkinter + sigrok)

**File:** `nucleo_debug_tool.py`
**Runtime:** Python 3.11, dependencies: pyserial, (no more saleae package)

#### Window Structure

- 4-tab notebook: PWM Test, ADC Test, FOC Full, Auto-Tune
- Connection panel: COM port selection, Connect/Disconnect
- Log widget (bottom): colored log with Save/Copy/Clear
- Status bar (bottom)

#### Tab: PWM Test (PWMTab)

- Channel checkboxes for Inv1 (PC0/PA7/PC1/PB0/PC2/PB1) and Inv2 (PC6/PC10/PC7/PC11/PC8/PC12)
- Parameters: ARR, Duty%, Dead-time ns
- Start/Stop PWM, Refresh status
- Sigrok Auto Test: capture 6 digital channels, measure frequency/duty/dead-time
- Inv1 and Inv2 buttons with 6/6 PASS/FAIL results

#### Tab: Auto-Tune (AutoTuneTab)

- Static ID: Rs/Ls/Isat (5x repeats), Multi-point Rs (I-V), All pairs AB/BC/CA, Detect channel
- Rotational ID: Rotate + measure (stub), Measure J (stub)
- Abort, Params, Stats, Export CSV buttons
- Progress bar + `@IDLE:PROG=` parsing
- Measured parameters display (Rs, Ls, Isat, ... + CH)
- Expected params input + Validate button
- Validation results text area
- Saturation curve table + tk.Canvas plot
- `on_line()` method: parses @IDLE:PROG=, @AT:STAT:, @AT:PAIR:, @AT:CH_DETECT:, @PARAMS:, @IDLE:CURVE:

#### Sigrok Helper (SaleaeHelper)

- Wraps sigrok-cli (fx2lafw driver) via subprocess
- `capture_sync(digital_chs, duration_s)` → returns SigrokCapture (csv_path, samplerate)
- `get_transitions(capture, channel)` → parses CSV, returns [(t_ns, value), ...]
- `measure_freq`, `measure_duty`, `measure_deadtime`
- 16 digital channels D0-D15, practical rate 8 MHz (fx2lafw limit)
- CSV format: no timestamps, time = sample_index / samplerate

---

## Makefile Structure

```
C_SOURCES = main.c system_stm32g4xx.c \
  src/pwm.c src/adc.c src/uart.c src/cordic_math.c \
  src/foc.c src/voltage_manager.c src/observer.c src/pll.c \
  src/flux_weakening.c src/vf_start.c src/protect.c src/autotune.c

ASM_SOURCES = startup_stm32g474xx.s

INCLUDES = -I. -Isrc -I$(CMSIS_DEVICE_DIR)/Include -I$(CMSIS_CORE_DIR)

CPU_FLAGS = -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
OPT = -Os
CFLAGS = $(CPU_FLAGS) $(OPT) $(INCLUDES) -Wall -Wextra -Wno-unused-parameter
CFLAGS += -DSTM32G474xx -ffunction-sections -fdata-sections -std=c99
LDFLAGS += -specs=nano.specs -specs=nosys.specs -u _printf_float
```

## Repository

**URL:** https://github.com/1974Ivanich/OEW
**Branch:** main
**Latest commit:** c0745b7 (chore: косметика autotune)

## FOC-особенности (АД, добавлены 05-06.08)

1. **Компенсация перекрёстных связей dq** (Антиучебник 7.7.1, стр.186-187):
   `vd += ω·Lσ·Iq; vq −= ω·Lσ·Id` (шаг 8b, перед VM; int64, Lσ из autotune)
2. **Модульный оптимум Kp/Ki** (стр.42): `Kp = L/(2·a·Tμ·Ks)`, a=2 (4.3%), Tμ=Ts=200мкс,
   Ki = Kp·Ts·R/L — авто в FOC_SetMotorParams (mp=)
3. **Lσ в BEMF observer** (стр.171): observer использует Lσ = Ls − Lm²/Lr, не Ls —
   насыщение-безопасно (ЭДС = dψr/dt корректна при любом Lm(I))
4. **Dead-time компенсация** (шаг 11b): `Δd = ±100·t_dt/Tsw = 0.75%` по знаку фазного тока
5. **OEW: d1 = d2** (mode 2 даёт противофазу сигнально) — V_U = (2d/100−1)·Vbus
6. **PLL ω** — Δθ q31 за цикл; **FOC 5 кГц** (ARR=999), ADC injected 1×/период на вершине
