#ifndef SWO_H
#define SWO_H

#include <stdint.h>

/* SWO/ITM отладочный вывод (TRACESWO на PB3, AF0).
 * Альтернатива USART2 для отладочных сообщений — не занимает UART.
 * Чтение: OpenOCD `tpiu config` + ST-Link SWO, или STM32CubeMonitor.
 *
 * Протокол: ITM stimulus port 0, формат ASCII. Инициализация — SWO_Init().
 * Ревью SWO-01/04: транспорт НЕБЛОКИРУЮЩИЙ (TrySend*), дропы считаются
 * (SWO_GetDropped). SWO_Printf использует vsnprintf — ТОЛЬКО main-loop,
 * не из ISR. */

void SWO_Init(void);
void SWO_SendChar(char c);
void SWO_SendStr(const char *str);
void SWO_Printf(const char *fmt, ...);
int  SWO_TrySendChar(char c);       /* 1 = отправлено, 0 = дроп */
int  SWO_TrySendStr(const char *str); /* кол-во отправленных байт */
int  SWO_IsReady(void);             /* 1 = ITM/port0 активны */
uint32_t SWO_GetDropped(void);      /* счётчик дропнутых байт */

#endif /* SWO_H */
