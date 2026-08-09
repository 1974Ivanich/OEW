#ifndef UART_H
#define UART_H

#include <stdint.h>

void UART_Init(void);
void UART_SendStr(const char *str);
void UART_SendChar(char c);
int  UART_GetChar(void);
int  UART_DataAvailable(void);
int  UART_ReadLine(char *buf, int maxlen);   /* 0 = не готово, >0 = длина, -1 = overflow */
void UART_SendInt(int32_t val);
void UART_SendTelemetry(const char *fmt, ...);

/* Неблокирующие варианты — ОБЯЗАТЕЛЬНЫ для вызова из ISR с приоритетом
 * NVIC <= 2 (ADC1_2_IRQHandler=0, TIM6_DAC_IRQHandler=1, TIM2_IRQHandler=1),
 * т.к. обычные UART_SendStr/UART_SendTelemetry делают busy-wait при полном
 * буфере и рискуют priority-inversion deadlock'ом с USART2_IRQn(=2).
 * Возвращают 0 при успехе, -1 если пакет не влез (отбрасывается целиком,
 * без блокировки) — см. UART_GetDroppedCount(). */
int      UART_TrySendStr(const char *str);
int      UART_TrySendTelemetry(const char *fmt, ...);
uint32_t UART_GetDroppedCount(void);

#endif
