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

#endif
