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
 * NVIC <= 2 (ADC1_2_IRQHandler=0, TIM2_IRQHandler=1, TIM6_DAC_IRQHandler=2),
 * т.к. обычные UART_SendStr/UART_SendTelemetry делают busy-wait при полном
 * буфере и рискуют priority-inversion deadlock'ом с USART2_IRQn(=2).
 * Возвращают 0 при успехе, -1 если пакет не влез (отбрасывается целиком,
 * без блокировки) — см. UART_GetDroppedCount().
 *
 * Критические секции — на PRIMASK (__disable_irq): маскируют ВСЕ приоритеты,
 * включая ADC=0 (BASEPRI не мог — см. uart.c). Длина секции ограничена
 * размером пакета (≤256 байт ≈ 2 мкс при 170 МГц) — задержка FOC-ISR
 * пренебрежимо мала, выборка тока аппаратная (TIM1_TRGO). */
int      UART_TrySendStr(const char *str);
int      UART_TrySendTelemetry(const char *fmt, ...);
uint32_t UART_GetDroppedCount(void);
/* Formatted packet rejected before enqueue because it exceeded the 256-byte
 * telemetry buffer or formatting failed. It is also included in dropped count. */
uint32_t UART_GetTruncatedCount(void);

/* ── Телеметрия с проверкой длины (минимальный контракт пакета) ──────────
 *
 * UART_SendTelemetry() форматирует в 256-байтовый буфер: строка, которая не
 * помещается, обрезается МОЛЧА (нет CRLF, счётчик не двигается, следующий
 * пакет склеивается с обрезком). Для длинных contract-строк (@FOC baseline с
 * identity-полями) использовать ТОЛЬКО эту функцию.
 *
 * Гарантии:
 *   - либо строка уходит целиком вместе с CRLF, либо не уходит вообще;
 *   - отброшенная строка учитывается в UART_GetTruncatedCount() (и в drops);
 *   - вызывать только из main loop/потока (busy-wait при полном TX-буфере),
 *     НЕ из ISR — для ISR есть UART_TrySendTelemetry(). */
#define UART_TELEMETRY_BUF_SIZE 512
int UART_SendTelemetryChecked(const char *fmt, ...);

uint32_t UART_GetRxErrorCount(void);     /* ORE/FE/NE/PE (UART-04) */
uint32_t UART_GetRxOverflowCount(void);  /* переполнение RX ring (UART-02) */

#endif
