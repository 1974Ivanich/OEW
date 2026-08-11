#ifndef SWO_H
#define SWO_H

#include <stdint.h>

/* SWO/ITM отладочный вывод (TRACESWO на PB3, AF0).
 * Альтернатива USART2 для отладочных сообщений — не занимает UART.
 * Чтение: OpenOCD `tpiu config` + ST-Link SWO, или STM32CubeMonitor.
 *
 * Протокол: ITM stimulus port 0, формат ASCII. Инициализация — SWO_Init().
 * Функции блокирующие (ожидают свободного стимула), НЕ вызывать из ISR
 * с приоритетом выше ITM/DWT (обычно так и есть — ITM быстрее UART). */

void SWO_Init(void);
void SWO_SendChar(char c);
void SWO_SendStr(const char *str);
void SWO_Printf(const char *fmt, ...);

#endif /* SWO_H */
