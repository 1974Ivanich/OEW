#ifndef STM32G474XX_H
#define STM32G474XX_H
#include <stdint.h>
typedef uint32_t __IO;
/* Заглушки IRQ-примитивов (используются в не тестируемых функциях FOC). */
static inline void __disable_irq(void) { }
static inline void __enable_irq(void)  { }
#endif
