# OEW Motor — Phase Resistance Measurement
## Nucleo-G474RE Распиновка

### 1. Инвертор 1 (TIM1) — Начала обмоток U1, V1, W1
| Сигнал | Пин | TIM | AF |
|--------|-----|-----|----|
| HIN_U1 | **PC0** | TIM1_CH1 | AF2 |
| LIN_U1 | **PB13** | TIM1_CH1N | AF2 |
| HIN_V1 | **PC1** | TIM1_CH2 | AF2 |
| LIN_V1 | **PB14** | TIM1_CH2N | AF2 |
| HIN_W1 | **PC2** | TIM1_CH3 | AF2 |
| LIN_W1 | **PB15** | TIM1_CH3N | AF2 |

### 2. Инвертор 2 (TIM8) — Концы обмоток U2, V2, W2
| Сигнал | Пин | TIM | AF |
|--------|-----|-----|----|
| HIN_U2 | **PC6** | TIM8_CH1 | AF4 |
| LIN_U2 | **PC10** | TIM8_CH1N | AF4 |
| HIN_V2 | **PC7** | TIM8_CH2 | AF4 |
| LIN_V2 | **PB0** | TIM8_CH2N | AF3 |
| HIN_W2 | **PC8** | TIM8_CH3 | AF4 |
| LIN_W2 | **PB1** | TIM8_CH3N | AF3 |

### 3. АЦП (ADC2)
| Сигнал | Пин | ADC2 канал |
|--------|-----|-----------|
| I_U1 | **PA0** | IN1 |
| I_V1 | **PA1** | IN2 |
| I_U2 | **PA6** | IN3 |
| I_V2 | **PA7** | IN4 |
| V_BUS_SENSE | **PC5** | IN11 |

### 4. Управление
| Сигнал | Пин | Описание |
|--------|-----|----------|
| EN_1 | **PB4** | Enable модуль 1 |
| EN_2 | **PB5** | Enable модуль 2 |
| UART TX | **PA2** | USART2 на ST-LINK |
| UART RX | **PA3** | USART2 на ST-LINK |

### Примечания
- TIM1 на PC0/PC1/PC2 (AF2), а НЕ на PA8/PA9/PA10 (AF1) — на данной плате PA8/PA9/PA10 не выводят ШИМ
- ADC2 используется вместо ADC1 (все каналы I_U1..V_BUS подключены к ADC2)
- Делитель V_BUS: R_high=110 кОм, R_low=4.7 кОм + 100 нФ
- Для измерений подаётся тестовый импульс 10 мс, 5 кГц center-aligned, 15% duty
