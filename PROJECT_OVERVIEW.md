# OEW Motor — Project Overview for Web AI

## Hardware Platform

**MCU:** STM32G474RE (Cortex-M4F, 170 MHz, FPU, CORDIC)
**Board:** Nucleo-G474RE (ST-Link V3, SWD)
**Inverter:** 2× STEVAL-IPM20B (IGBT 3-phase, single DC-link shunt, 10A max)
**Logic Analyzer:** Saleae Logic (via sigrok-cli, driver fx2lafw, 8 ch, 8 MHz max)

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
| PC10 | LIN_U2 | TIM8_CH1N | AF4 |
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

### Current Sensing Topology

STEVAL-IPM20B current sensing:
- **I1 (PA0) / I2 (PA1)** — phase current amplifiers, used by **FOC** for Clarke transform (2-sensor reconstruction: iu=i1, iv=i2, iw=-iu-iv)
- **IN (PA6)** — DC-link shunt, used by **Auto-Tune** (single shunt path: R=0.03Ω, Gain=2.1)

Both paths are calibrated. FOC reads I1/I2; autotune reads IN.

**Shunt parameters:** Rshunt=0.03Ω, Gain=2.1 → 0.063 V/A, ADC Vref=3.3V, 12-bit → 1 code ≈ 12.8 mA

**Voltage sensing:** VBUS pin via resistor divider 1:125. Raw ADC → Vbus_mV = raw * 3300 * 125 / 4095

### Saleae / Sigrok Connection

Probes connected to Saleae Logic ch0-ch5 (D0-D5). For Inv1 test: D0=PC0, D1=PA7, D2=PC1, D3=PB0, D4=PC2, D5=PB1. For Inv2 test: physically reconnect to PC6/PC10/PC7/PC11/PC8/PC12.

Sigrok-cli 0.8.0 at `C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe`, driver fx2lafw, max 8 channels, practical rate 8 MHz.

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
| `src/foc.c` / `foc.h` | FOC control: Clarke/Park, PI regulators, SVPWM |
| `src/observer.c` / `.h` | BEMF observer for sensorless speed/position |
| `src/pll.c` / `.h` | PLL for speed/angle tracking |
| `src/flux_weakening.c` / `.h` | Field weakening at high speed |
| `src/vf_start.c` / `.h` | V/f open-loop startup sequence |
| `src/protect.c` / `.h` | Overcurrent/overvoltage protection |
| `src/autotune.c` / `autotune.h` | Auto-tuning: Rs, Ls, Isat, curve, channel detect, all pairs |

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

#### Key ADC Functions

```c
void ADC_Init(void);
void ADC_CalibrateOffsets(void);       // 8-sample zero calibration
void ADC_CalibrateI1_256(void);        // 256-sample zero cal (debug)
void ADC_StartConversion(void);        // Software-triggered regular conversion
int32_t ADC_GetI1_mA(void);            // Phase A current (FOC Clarke)
int32_t ADC_GetI2_mA(void);            // Phase B current (FOC Clarke)
int32_t ADC_GetIN_mA(void);            // DC-link shunt current (Auto-Tune)
int32_t ADC_GetVbus_mV(void);          // Bus voltage
```

**CRITICAL:** For Auto-Tune use `ADC_GetIN_mA()` (DC-link shunt). For FOC Clarke use `ADC_GetI1_mA()`/`ADC_GetI2_mA()` (phase sensors).

#### UART Protocol

- 115200 baud, 8N1
- Line-based: each command/reply ends with `\r\n`
- Prompt: `> `
- Telemetry format: `@PREFIX:KEY1=VALUE1:KEY2=VALUE2\r\n`
- CLI parser in main.c `while(1)` loop, `UART_ReadLine()` returns line buffer
- Command examples:
  - `p=99,15,1500,63` — PWM config (arr, duty%, dt_ns, mask)
  - `idle` — static autotune
  - `ch` — detect current channel
  - `iv` — multi-point Rs
  - `pairs` — measure AB/BC/CA
  - `abort` — stop running test
  - `params` — show motor parameters
  - `curve` — show saturation curve
  - `stats` — show statistics
  - `c` — ADC calibration
  - `sysinfo` — system info
  - `dump` / `dump8` — TIM1/TIM8 register dump

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
- Max 8 channels D0-D7, practical rate 8 MHz
- CSV format: no timestamps, time = sample_index / samplerate

---

## Makefile Structure

```
C_SOURCES = main.c system_stm32g4xx.c \
  src/pwm.c src/adc.c src/uart.c src/cordic_math.c \
  src/foc.c src/observer.c src/pll.c src/flux_weakening.c \
  src/vf_start.c src/protect.c src/autotune.c

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
**Latest commit:** 42ab114 (Auto-Tune v2)
