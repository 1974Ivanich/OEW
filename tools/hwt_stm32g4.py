"""Адреса и биты STM32G474RE для сценариев HWT.

Значения НЕ берутся по памяти: они сверены с заголовками CMSIS в репозитории
(`Drivers/CMSIS/Device/ST/STM32G4xx/Include/stm32g474xx.h`,
`Drivers/CMSIS/Include/core_cm4.h`), которые соответствуют RM0440. В каждом
блоке указано, откуда именно взято значение.

Сценарии читают регистры по абсолютному адресу (`t.read_u32(TIM1_BDTR)`):
макросы вида `TIM1->BDTR` живут в препроцессоре и в отладочной информации
образа отсутствуют, поэтому на цели они не вычисляются.
"""

from __future__ import annotations

# ── память (linker.ld: FLASH 512K @0x08000000, RAM 128K @0x20000000) ──────
FLASH_BASE = 0x08000000
FLASH_SIZE = 512 * 1024
RAM_BASE = 0x20000000
RAM_SIZE = 128 * 1024

# ── периферия (stm32g474xx.h: PERIPH_BASE=0x40000000, APB2PERIPH_BASE=+0x10000)
TIM1_BASE = 0x40012C00          # TIM1_BASE = APB2PERIPH_BASE + 0x2C00
TIM8_BASE = 0x40013400          # TIM8_BASE = APB2PERIPH_BASE + 0x3400

# Смещения TIM_TypeDef (stm32g474xx.h, структура TIM_TypeDef)
TIM_CR1 = 0x00                  # __IO uint32_t CR1;  Address offset: 0x00
TIM_SR = 0x10                   # __IO uint32_t SR;   Address offset: 0x10
TIM_CNT = 0x24                  # __IO uint32_t CNT;  Address offset: 0x24
TIM_PSC = 0x28                  # __IO uint32_t PSC;  Address offset: 0x28
TIM_ARR = 0x2C                  # __IO uint32_t ARR;  Address offset: 0x2C
TIM_CCR1 = 0x34                 # __IO uint32_t CCR1; Address offset: 0x34
TIM_BDTR = 0x44                 # __IO uint32_t BDTR; Address offset: 0x44

TIM1_CR1, TIM1_SR, TIM1_CNT = TIM1_BASE + TIM_CR1, TIM1_BASE + TIM_SR, \
    TIM1_BASE + TIM_CNT
TIM1_PSC, TIM1_ARR, TIM1_CCR1 = TIM1_BASE + TIM_PSC, TIM1_BASE + TIM_ARR, \
    TIM1_BASE + TIM_CCR1
TIM1_BDTR = TIM1_BASE + TIM_BDTR
TIM8_CR1, TIM8_SR, TIM8_CNT = TIM8_BASE + TIM_CR1, TIM8_BASE + TIM_SR, \
    TIM8_BASE + TIM_CNT
TIM8_BDTR = TIM8_BASE + TIM_BDTR

# Биты (stm32g474xx.h: TIM_CR1_CEN_Msk = 0x1; TIM_SR_BIF_Pos = 7;
# TIM_SR_B2IF_Pos = 8; TIM_BDTR_OSSI_Pos = 10, OSSR = 11, BKE = 12,
# BKP = 13, AOE = 14, MOE = 15, BK2E = 24, BK2P = 25)
TIM_CR1_CEN = 0x00000001
TIM_SR_BIF = 0x00000080
TIM_SR_B2IF = 0x00000100
TIM_BDTR_OSSI = 0x00000400
TIM_BDTR_OSSR = 0x00000800
TIM_BDTR_BKE = 0x00001000
TIM_BDTR_BKP = 0x00002000
TIM_BDTR_AOE = 0x00004000
TIM_BDTR_MOE = 0x00008000
TIM_BDTR_BK2E = 0x01000000
TIM_BDTR_BK2P = 0x02000000

# Инвариант BDTR, который ставит PWM_Init (src/pwm.c: PWM_BDTR_REQUIRED /
# PWM_BDTR_FORBIDDEN). Сценарий только читает: сам модуль не меняется.
PWM_BDTR_REQUIRED = TIM_BDTR_BKE | TIM_BDTR_OSSR | TIM_BDTR_OSSI
PWM_BDTR_FORBIDDEN = TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE
PWM_BREAK_STATUS_MASK = TIM_SR_BIF | TIM_SR_B2IF

# ── идентификация кристалла (stm32g474xx.h: DBGMCU_BASE=0xE0042000,
# FLASHSIZE_BASE=0x1FFF75E0) ───────────────────────────────────────────────
DBGMCU_IDCODE = 0xE0042000
FLASH_SIZE_REG = 0x1FFF75E0

# ── ядро (core_cm4.h: SCS_BASE=0xE000E000, SCB_BASE=SCS_BASE+0x0D00;
# CFSR offset 0x028, HFSR offset 0x02C) ───────────────────────────────────
SCB_CFSR = 0xE000ED28
SCB_HFSR = 0xE000ED2C
