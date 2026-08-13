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
#endif
