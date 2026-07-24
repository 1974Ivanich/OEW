#include "uart.h"
#include "stm32g474xx.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

/*
 * Простой line-based парсер UART. Работает в polling-режиме (вызов
 * UART_ReadLine из main loop). Размер буфера 32 символа — этого хватит
 * для команд типа "s=500\r" и диагностики.
 *
 * Символы собираются до '\n' (или '\r'). Возврат — длина строки без
 * терминатора. Таймаут 0 — non-blocking; на каждом вызове возвращает
 * текущее накопленное состояние.
 */
#define UART_RX_LINE_MAX  32

void UART_Init(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    /* PA2 = TX, PA3 = RX, AF7 = USART2 */
    GPIOA->MODER &= ~(3U<<4); GPIOA->MODER |= (2U<<4);
    GPIOA->OSPEEDR |= (3U<<4);
    GPIOA->AFR[0] &= ~(0xF<<8); GPIOA->AFR[0] |= (7U<<8);
    GPIOA->MODER &= ~(3U<<6); GPIOA->MODER |= (2U<<6);
    GPIOA->OSPEEDR |= (3U<<6);
    GPIOA->AFR[0] &= ~(0xF<<12); GPIOA->AFR[0] |= (7U<<12);
    USART2->BRR = 16000000UL / 115200;     /* 16 МГц HSI — на текущем этапе */
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

void UART_SendStr(const char *str) {
    while(*str) {
        while(!(USART2->ISR & USART_ISR_TXE_TXFNF)) {}
        USART2->TDR = (uint8_t)(*str++);
    }
}

void UART_SendChar(char c) {
    while(!(USART2->ISR & USART_ISR_TXE_TXFNF)) {}
    USART2->TDR = (uint8_t)c;
}

int UART_GetChar(void) {
    if(!(USART2->ISR & USART_ISR_RXNE_RXFNE)) return -1;
    return (int)(USART2->RDR & 0xFF);
}

int UART_DataAvailable(void) {
    return (USART2->ISR & USART_ISR_RXNE_RXFNE) ? 1 : 0;
}

/*
 * Чтение строки (до '\n' или '\r'). Без блокировки: возвращает 0 если
 * строка ещё не собрана, >0 — длину готовой строки (без терминатора).
 * При переполнении буфера — отбрасывает и возвращает -1.
 */
int UART_ReadLine(char *buf, int maxlen) {
    static char rxbuf[UART_RX_LINE_MAX];
    static int  idx = 0;
    int c = UART_GetChar();
    if(c < 0) return 0;
    char ch = (char)c;
    if(ch == '\n' || ch == '\r') {
        rxbuf[idx] = '\0';
        int len = idx;
        idx = 0;
        if(len == 0) return 0;
        strncpy(buf, rxbuf, (size_t)maxlen - 1);
        buf[maxlen - 1] = '\0';
        return len;
    }
    if(ch == 8 || ch == 127) {   /* backspace */
        if(idx > 0) idx--;
        return 0;
    }
    if(idx >= UART_RX_LINE_MAX - 1) {
        idx = 0;   /* overflow — сброс */
        return -1;
    }
    if(ch >= 32 && ch < 127) {   /* printable */
        rxbuf[idx++] = ch;
    }
    return 0;
}

void UART_SendInt(int32_t val) {
    char buf[16];
    sprintf(buf, "%ld", (long)val);
    UART_SendStr(buf);
}

void UART_SendTelemetry(const char *fmt, ...) {
    char buf[128];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    UART_SendStr(buf);
}
