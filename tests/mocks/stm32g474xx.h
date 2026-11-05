#ifndef STM32G474XX_H
#define STM32G474XX_H
#include <stdint.h>
#define __IO volatile
/* Минимальные структуры для cordic_mod_test.c (реальные поля — первые три
 * в CORDIC_TypeDef из ST CMSIS; RCC нужен, т.к. cordic_math.c компилирует
 * CORDIC_Init — тест его не вызывает, но код должен собраться). */
typedef struct {
    __IO uint32_t CSR;
    __IO uint32_t WDATA;
    __IO uint32_t RDATA;
} CORDIC_TypeDef;

typedef struct {
    __IO uint32_t AHB1ENR;
} RCC_TypeDef;
#define RCC_AHB1ENR_CORDICEN (1U << 19)
/* Заглушки IRQ-примитивов (используются в не тестируемых функциях FOC). */
static inline void __disable_irq(void) { }
static inline void __enable_irq(void)  { }

/* Номера прерываний и NVIC-примитивы для hosted-сборки src/foc.c и
 * src/vf_control.c (EnableIRQ/DisableIRQ/SetPriority/GetEnableIRQ).
 * Значения взяты из Drivers/CMSIS/Device/ST/STM32G4xx/Include/stm32g474xx.h. */
typedef enum {
    ADC1_2_IRQn   = 18,
    TIM2_IRQn     = 28,
    USART2_IRQn   = 38,
    TIM6_DAC_IRQn = 54
} IRQn_Type;

/* Мок ничего не включает: GetEnableIRQ всегда 0 — hosted-тесты не должны
 * полагаться на состояние NVIC реального МК. */
static inline void NVIC_EnableIRQ(IRQn_Type irq)     { (void)irq; }
static inline void NVIC_DisableIRQ(IRQn_Type irq)    { (void)irq; }
static inline void NVIC_SetPriority(IRQn_Type irq, uint32_t prio) { (void)irq; (void)prio; }
static inline uint32_t NVIC_GetEnableIRQ(IRQn_Type irq) { (void)irq; return 0u; }
#endif
