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
    __IO uint32_t AHB2ENR;
} RCC_TypeDef;
#define RCC_AHB1ENR_CORDICEN (1U << 19)
#define RCC_AHB2ENR_GPIOAEN  (1U << 0)
#define RCC_AHB2ENR_DAC1EN   (1U << 16)

/* Мок DAC/GPIO для hosted-теста dac_debug_test.c (регистры — как в RM0440). */
typedef struct {
    __IO uint32_t CR;
    __IO uint32_t DHR12R1;
    __IO uint32_t DHR12R2;
    __IO uint32_t DHR12RD;
} DAC_TypeDef;

typedef struct {
    __IO uint32_t MODER;
} GPIO_TypeDef;

#define DAC_CR_EN1 (1U << 0)
#define DAC_CR_EN2 (1U << 16)

extern DAC_TypeDef mock_dac1;
extern GPIO_TypeDef mock_gpioa;
#define DAC1  (&mock_dac1)
#define GPIOA (&mock_gpioa)
/* Заглушки IRQ-примитивов (используются в не тестируемых функциях FOC). */
static inline void __disable_irq(void) { }
static inline void __enable_irq(void)  { }
#endif
